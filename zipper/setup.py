"""zipper.setup

Standing up a zipper from nothing: `zipper init` makes a vault, and `zipper setup`
points Claude Code's Stop hook at this checkout. Everything else is configured by
talking to Claude in the new vault.

    zipper init <path> [--owner NAME] [--id ID] [--backup PATH] [--starter]

Secrets go to `.env`; everything else to the vault's settings.json.
"""
import json, os, re, subprocess

from . import settings

ROOT = settings.ROOT
TEMPLATE = os.path.join(ROOT, 'template', 'vault')      # always: CLAUDE.md, .gitignore
STARTER = os.path.join(ROOT, 'template', 'starter')     # optional: a suggested layout
from .core import ENV_FILE
HOOK = os.path.join(ROOT, 'hooks', 'forward_reply.py')
START_HOOK = os.path.join(ROOT, 'hooks', 'session_start.py')

# The starter layout's folders. None is required: the engine makes Inbox/, Meta/,
# Log/ and Metrics/ when it first writes to them, and a vault is otherwise
# whatever folders its owner wants.
STARTER_DIRS = ('Projects', 'Areas', 'Topics', 'People', 'Tasks', 'Decisions', 'Events')


# ---------------------------------------------------------------- the vault

def init_vault(path, owner='', zid='', git_name='', git_email='', backup='', starter=False,
               guide='', record_backup=True, settings_json=None):
    """Make `path` a vault: a git repository with a CLAUDE.md and a .gitignore.

    That is all a vault needs. `starter` adds a suggested layout (folders, a
    schema, Dataview pages for Obsidian) that is just that -- a suggestion.
    Refuses a non-empty directory that is not already a vault: overwriting
    someone's notes is the one mistake here that cannot be undone. Returns the
    files written."""
    path = os.path.abspath(os.path.expanduser(path))
    if os.path.exists(os.path.join(path, 'CLAUDE.md')):
        raise RuntimeError('%s already holds a vault -- leaving it alone' % path)
    if os.path.isdir(path) and _has_notes(path):
        raise RuntimeError('%s is not empty and is not a vault' % path)
    os.makedirs(path, exist_ok=True)
    subs = {'{{OWNER}}': owner or 'the operator', '{{ID}}': zid or settings.zipper_id()}
    written = []
    sources = [TEMPLATE] + ([STARTER] if starter else [])
    if starter:
        for d in STARTER_DIRS:
            os.makedirs(os.path.join(path, d), exist_ok=True)
    for src_root, base, _dirs, files in ((r,) + w for r in sources for w in os.walk(r)):
        for f in files:
            src = os.path.join(base, f)
            rel = os.path.relpath(src, src_root)
            dst = os.path.join(path, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(src, encoding='utf-8') as fh:
                text = fh.read()
            for k, v in subs.items():
                text = text.replace(k, v)
            with open(dst, 'w', encoding='utf-8') as fh:
                fh.write(text)
            written.append(rel)
    # A local identity, always: services run with no HOME, and a commit that
    # falls back to root@hostname is how a history stops meaning anything.
    if not os.path.isdir(os.path.join(path, '.git')):
        subprocess.run(['git', 'init', '-q', '-b', 'main', path], check=True)
    ident = (git_name or zid or 'zipper', git_email or '%s@zipper.local' % (zid or 'zipper'))
    subprocess.run(['git', '-C', path, 'config', 'user.name', ident[0]], check=True)
    subprocess.run(['git', '-C', path, 'config', 'user.email', ident[1]], check=True)
    from . import fanout
    fanout.install_hook(path)
    # The one settings file lives in the vault, beside the notes and the
    # dashboard's cards: everything personal except secrets.
    sj = os.path.join(path, 'settings.json')
    if not os.path.exists(sj):
        with open(sj, 'w', encoding='utf-8') as fh:
            json.dump(settings_json or {'id': zid or settings.zipper_id(), 'owner': owner}, fh,
                      indent=2)
            fh.write('\n')
        written.append('settings.json')
    if guide:
        # The setup guide goes on top of the rules, between markers, and removes
        # itself section by section (`zipper setup done`).
        claude = os.path.join(path, 'CLAUDE.md')
        with open(claude, encoding='utf-8') as fh:
            rules = fh.read()
        with open(claude, 'w', encoding='utf-8') as fh:
            fh.write(guide + rules)
    subprocess.run(['git', '-C', path, 'add', '-A'], check=True)
    subprocess.run(['git', '-C', path, 'commit', '-q', '-m', 'A new vault'], check=False)
    if backup:
        from . import scoops as _scoops           # puts `scoops` on sys.path
        from scoops import backup as _backup
        _backup.attach(backup, vault=path)
        if record_backup:
            _scoops.settings.put('scoops.backup.enabled', True)
    return written


def _has_notes(path):
    """Anything in `path` that someone wrote. The engine's own output does not
    count: a dashboard started before `init` has already made Inbox/, Metrics/
    and the generated Meta/ views, and refusing over those would make the order
    of two commands matter for no reason."""
    engine = {'Inbox', 'Log', 'Metrics', '.git'}
    generated = {'Queue.md', 'Status.md', 'Agenda.md', 'Repos.md'}
    for base, dirs, files in os.walk(path):
        rel = os.path.relpath(base, path)
        top = rel.split(os.sep)[0]
        if top in engine:
            dirs[:] = []
            continue
        for f in files:
            if not (top == 'Meta' and f in generated):
                return True
    return False


# ---------------------------------------------------------------- .env, secrets

def env_get(key):
    from .core import _env_file
    return _env_file().get(key, '')


def env_set(key, value, path=None):
    """Set one line of `.env`, keeping every other line (and comment) as it was.
    Written 600: it is the file of secrets. `path`: another zipper's, for init."""
    ENV_FILE = path or globals()['ENV_FILE']
    # One printable line, or it becomes more than one variable: this file is re-read
    # with splitlines(), which also breaks on U+2028, \x85, \x0b and others.
    value = str(value)
    if not value.isprintable() or len(value.splitlines()) > 1:
        raise ValueError('%s: a value must be one line of printable text' % key)
    lines = []
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE, encoding='utf-8') as fh:
            lines = fh.read().splitlines()
    pat = re.compile(r'^\s*%s\s*=' % re.escape(key))
    for i, ln in enumerate(lines):
        if pat.match(ln):
            lines[i] = '%s=%s' % (key, value)
            break
    else:
        lines.append('%s=%s' % (key, value))
    tmp = ENV_FILE + '.tmp'
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(lines) + '\n')
    os.replace(tmp, ENV_FILE)


# ---------------------------------------------------------------- the hooks

def _ensure(data, event, script, entry):
    """Point `event`'s hook for `script` at this checkout, adding it if absent.
    Repointed rather than duplicated: two Stop hooks would post every reply twice."""
    groups = data.setdefault('hooks', {}).setdefault(event, [])
    found = False
    for group in groups:
        for h in group.get('hooks', []):
            if script in h.get('command', ''):
                h['command'] = entry['command']
                found = True
    if not found:
        groups.append({'hooks': [entry]})


def install_hook(settings_path=None):
    """Point Claude Code's hooks at this checkout: `Stop` (forward_reply.py) and
    `SessionStart` (session_start.py).

    Without Stop a Discord conversation works and its replies silently never
    arrive -- the failure a fresh install hit first. Without SessionStart a new
    conversation answers without having seen the queue. Idempotent, and every
    other setting in the file is kept. The entrypoint runs this on every boot,
    which is how a zipper made before a hook existed gets it."""
    p = settings_path or os.path.expanduser('~/.claude/settings.json')
    try:
        with open(p, encoding='utf-8') as fh:
            data = json.load(fh)
    except FileNotFoundError:
        data = {}
    _ensure(data, 'Stop', 'forward_reply.py',
            {'type': 'command', 'command': 'python3 %s' % HOOK, 'timeout': 45,
             'statusMessage': 'Forwarding reply to Discord'})
    _ensure(data, 'SessionStart', 'session_start.py',
            {'type': 'command', 'command': 'python3 %s' % START_HOOK, 'timeout': 10})
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(data, fh, indent=2)
        fh.write('\n')
    os.replace(tmp, p)
    return p


def _backup_default():
    """In a container, /zipper/backup is a volume of its own; elsewhere, ask."""
    d = os.environ.get('ZIPPER_BACKUP_DEFAULT', '')
    return os.path.join(d, 'vault.git') if d and os.path.isdir(d) else ''


# ---------------------------------------------------------------- commands

# ---------------------------------------------------------------- a whole zipper

SETUP_DIR = os.path.join(ROOT, 'template', 'setup')
HOME_TEMPLATE = os.path.join(ROOT, 'template', 'home')
# The guide's order. Scoops not named here follow, alphabetically.
FIRST = ('about', 'notes')
SCOOP_ORDER = ('discord', 'dashboard', 'backup', 'github', 'calendar', 'canvas', 'hours', 'gmail',
                'slack', 'passes', 'digest', 'upstream', 'peers', 'host')
LAST = ('finish',)
# A Discord-only zipper's guide, each `template/setup/<name>-discord.md`.
DISCORD_STEPS = ('about', 'calendar', 'gmail', 'rhythm', 'flow', 'finish')
GUIDE_OPEN, GUIDE_CLOSE = '<!-- setup -->', '<!-- /setup -->'


def _block(name, text):
    return '<!-- setup:%s -->\n%s\n<!-- /setup:%s -->\n\n' % (name, text.strip(), name)


def guide(subs, discord_only=False):
    """The setup guide: the intro, then one removable block per step and per scoop.
    `discord_only`: a zipper reached only through the shared gateway, with no
    dashboard to open a secret page in -- its own intro, and no scoop sections."""
    def read(p):
        with open(p, encoding='utf-8') as fh:
            t = fh.read()
        for k, v in subs.items():
            t = t.replace(k, v)
        return t
    intro = 'intro-discord.md' if discord_only else 'intro.md'
    out = GUIDE_OPEN + '\n' + read(os.path.join(SETUP_DIR, intro)) + '\n'
    if discord_only:
        # Short on purpose: inputs, two times of day, how it works. Preferences are
        # learned by working together, not asked up front. The inputs are the scoops
        # that can be set up from Discord: calendars are a link pasted into their own
        # server, Gmail a Google sign-in finished through the secret form.
        for n in DISCORD_STEPS:
            out += _block(n, read(os.path.join(SETUP_DIR, n + '-discord.md')))
        return out + GUIDE_CLOSE + '\n\n'
    for n in FIRST:
        out += _block(n, read(os.path.join(SETUP_DIR, n + '.md')))
    pdir = os.path.join(ROOT, 'scoops')
    have = sorted(n for n in os.listdir(pdir) if os.path.exists(os.path.join(pdir, n, 'SETUP.md')))
    for n in [x for x in SCOOP_ORDER if x in have] + [x for x in have if x not in SCOOP_ORDER]:
        out += _block(n, read(os.path.join(pdir, n, 'SETUP.md')))
    for n in LAST:
        out += _block(n, read(os.path.join(SETUP_DIR, n + '.md')))
    return out + GUIDE_CLOSE + '\n\n'


def add_starter(vault):
    """The suggested layout, into an existing vault: its folders and files, skipping
    anything already there. Never overwrites a note. Returns what it added."""
    added = []
    for d in STARTER_DIRS:
        p = os.path.join(vault, d)
        if not os.path.exists(p):
            os.makedirs(p)
            added.append(d + '/')
    for base, _dirs, files in os.walk(STARTER):
        for f in files:
            rel = os.path.relpath(os.path.join(base, f), STARTER)
            dst = os.path.join(vault, rel)
            if os.path.exists(dst):
                continue
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(os.path.join(base, f), encoding='utf-8') as fh:
                text = fh.read()
            with open(dst, 'w', encoding='utf-8') as fh:
                fh.write(text.replace('{{OWNER}}', settings.get('owner') or 'the operator')
                             .replace('{{ID}}', settings.zipper_id()))
            added.append(rel)
    return added


def remaining(text):
    return re.findall(r'<!-- setup:([a-z0-9_-]+) -->', text)


def done(text, name=None):
    """Remove one section, or -- with none left -- the whole guide. Returns the new text."""
    if name:
        n = re.escape(name)
        pat = re.compile(r'<!-- setup:%s -->.*?<!-- /setup:%s -->\n*' % (n, n), re.S)
        if not pat.search(text):
            raise RuntimeError('no setup section %r (left: %s)'
                               % (name, ', '.join(remaining(text)) or 'none'))
        return pat.sub('', text, count=1)
    left = remaining(text)
    if left:
        raise RuntimeError('sections still to go: %s' % ', '.join(left))
    return re.sub(re.escape(GUIDE_OPEN) + r'.*?' + re.escape(GUIDE_CLOSE) + r'\n*', '', text,
                  count=1, flags=re.S)


def host_timezone():
    """This machine's zone, for the container's `TZ`. A zipper on UTC while its
    calendars are read in local time sees every event move and fills the queue
    with remove/add pairs (it happened on 2026-09-30). `$TZ`, else /etc/timezone,
    else where /etc/localtime points, else UTC."""
    if os.environ.get('TZ'):
        return os.environ['TZ'].lstrip(':')
    try:
        with open('/etc/timezone', encoding='utf-8') as fh:
            tz = fh.read().strip()
        if tz:
            return tz
    except OSError:
        pass
    link = os.path.realpath('/etc/localtime')
    if '/zoneinfo/' in link:
        return link.split('/zoneinfo/', 1)[1]
    return 'UTC'


def init_home(home, owner='', zid='zipper-0', starter=False, addr='127.0.0.1', port=8899,
              cred=''):
    """Stand up a whole zipper in `home`: the vault (with the setup guide as its
    CLAUDE.md), its config, a backup, a compose file, and a `zipper` command for this
    machine. The dashboard is published on `addr:port`, behind `cred` (user:password)
    when given. Returns what it made."""
    home = os.path.abspath(os.path.expanduser(home))
    if os.path.exists(os.path.join(home, 'vault', 'settings.json')):
        raise RuntimeError('%s already holds a zipper -- leaving it alone' % home)
    subs = {'{{HOME}}': home, '{{ID}}': zid, '{{CODE}}': ROOT,
            '{{OWNER}}': owner or 'the operator', '{{TZ}}': host_timezone(),
            '{{ADDR}}': addr, '{{PORT}}': str(port)}
    os.makedirs(os.path.join(home, 'config'), exist_ok=True)
    os.makedirs(os.path.join(home, 'backup'), exist_ok=True)
    envf = os.path.join(home, 'config', '.env')
    if not os.path.exists(envf):
        fd = os.open(envf, os.O_WRONLY | os.O_CREAT, 0o600)
        with os.fdopen(fd, 'w') as fh:
            fh.write('# Secrets only. Written by `zipper secret NAME`; never opened by Claude.\n')
    if cred:
        env_set('ZIPPER_TERM_CRED', cred, envf)
    env_set('ZIPPER_DASHBOARD_URL', 'http://%s:%d' % (addr, port), envf)
    for f in os.listdir(HOME_TEMPLATE):
        with open(os.path.join(HOME_TEMPLATE, f), encoding='utf-8') as fh:
            t = fh.read()
        for k, v in subs.items():
            t = t.replace(k, v)
        dst = os.path.join(home, f)
        with open(dst, 'w', encoding='utf-8') as fh:
            fh.write(t)
        if f == 'zipper':
            os.chmod(dst, 0o755)
    vault = os.path.join(home, 'vault')
    init_vault(vault, owner=owner, zid=zid, starter=starter,
               backup=os.path.join(home, 'backup', 'vault.git'), guide=guide(subs),
               record_backup=False, settings_json={
                   'id': zid, 'owner': owner,
                   'code': {'repo': '4b3c/Zipper', 'branch': 'main'},
                   'timezone': host_timezone(),
                   'scoops': {'backup': {'enabled': True}}})
    container_backup(vault)
    return home


def container_backup(vault):
    """Point the vault's backup remote where the container will see it. init made
    and pushed the bare repo at its path here, on the host; inside the container the
    same folder is mounted at /zipper/backup (template/home/compose.yml), and a remote
    left at the host path fails every push from there."""
    subprocess.run(['git', '-C', vault, 'remote', 'set-url', 'origin',
                    '/zipper/backup/vault.git'], check=True)


RELAY_COMPOSE = """\
# {{ID}}: one zipper, reached only through the shared Discord gateway. No dashboard and
# no published port: nothing outside this container can reach anything in it.
# From this folder: docker compose up -d
services:
  zipper:
    image: {{IMAGE}}
    container_name: {{ID}}
    hostname: {{ID}}
    restart: unless-stopped
    mem_limit: {{MEM}}
    environment:
      TZ: {{TZ}}
    env_file: [./config/.env]
    volumes:
      - ./config:/zipper/config
      - ./vault:/zipper/vault
      - ./backup:/zipper/backup
      - ./code:/zipper/code
      - ./home:/zipper/home
    networks: [{{NETWORK}}]

networks:
  {{NETWORK}}:
    external: true
"""


def init_relay_home(home, zid, relay, channel, token, network='zipper-gateway',
                    image='zipper:local', mem='1g', tz=''):
    """A zipper whose only door is the shared gateway: Discord already on, pointed at
    the relay with the token the gateway minted for it, the dashboard off, and the
    setup guide written for a conversation in Discord. `zipper secret` needs the
    dashboard, so this is also the one way its Discord token is ever set: written here,
    once, by whoever provisions it. Returns what it made."""
    home = os.path.abspath(os.path.expanduser(home))
    if os.path.exists(home) and os.listdir(home):
        raise RuntimeError('%s is not empty -- leaving it alone' % home)
    if not re.match(r'^[0-9]{5,25}$', str(channel)):
        raise RuntimeError('channel must be a Discord channel id')
    tz = tz or host_timezone()
    for d in ('config', 'backup', 'code', 'home'):
        os.makedirs(os.path.join(home, d), exist_ok=True)
    envf = os.path.join(home, 'config', '.env')
    fd = os.open(envf, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as fh:
        fh.write('# Secrets only. Never opened by Claude.\n')
    env_set('DISCORD_TOKEN', token, envf)
    env_set('DISCORD_RELAY', relay, envf)
    t = RELAY_COMPOSE
    for k, v in {'{{ID}}': zid, '{{IMAGE}}': image, '{{MEM}}': mem, '{{TZ}}': tz,
                 '{{NETWORK}}': network}.items():
        t = t.replace(k, v)
    with open(os.path.join(home, 'compose.yml'), 'w', encoding='utf-8') as fh:
        fh.write(t)
    subs = {'{{HOME}}': home, '{{ID}}': zid, '{{CODE}}': ROOT, '{{OWNER}}': 'the operator',
            '{{TZ}}': tz, '{{ADDR}}': '', '{{PORT}}': ''}
    init_vault(os.path.join(home, 'vault'), zid=zid,
               backup=os.path.join(home, 'backup', 'vault.git'),
               guide=guide(subs, discord_only=True), record_backup=False, settings_json={
                   'id': zid, 'owner': '',
                   'code': {'repo': '4b3c/Zipper', 'branch': 'main'},
                   'timezone': tz,
                   'discord': {'channel': str(channel)},
                   'scoops': {'backup': {'enabled': True}, 'discord': {'enabled': True},
                              'dashboard': {'enabled': False}}})
    container_backup(os.path.join(home, 'vault'))
    return home


def default_id(path):
    """A zipper is named after its folder unless told otherwise: `init /opt/zippers/quinn`
    makes `quinn`. Two zippers both defaulting to `zipper-0` is a container-name clash."""
    base = re.sub(r'[^a-z0-9-]+', '-', os.path.basename(os.path.abspath(path)).lower()).strip('-')
    return base or 'zipper-0'


def cmd_init(a):
    if a.vault_only:
        return cmd_init_vault(a)
    if getattr(a, 'relay', None):
        # The token comes from the environment, never the command line, where any
        # process on the machine could read it in `ps`.
        token = os.environ.get('ZIPPER_NEW_DISCORD_TOKEN', '')
        if not token or not a.id or not a.discord_channel:
            print('init --relay needs --id, --discord-channel and ZIPPER_NEW_DISCORD_TOKEN')
            return 1
        try:
            home = init_relay_home(a.path, a.id, a.relay, a.discord_channel, token,
                                   network=a.network or 'zipper-gateway', mem=a.mem or '1g')
        except RuntimeError as e:
            print('init: %s' % e); return 1
        print('Made %s in %s (Discord through %s). Start it: docker compose up -d'
              % (a.id, home, a.relay))
        return 0
    from . import install
    addr = a.address or '127.0.0.1'
    if not a.address and install.remote() and not a.local:
        addr = install.tailnet_address()
        if not addr:
            print(install.TAILSCALE_FIRST)
            return 1
    try:
        port = install.free_port(addr)
        user, pw = 'zipper', install.password()
        home = init_home(a.path, owner=a.owner or '', zid=a.id or default_id(a.path),
                         starter=a.starter, addr=addr, port=port, cred='%s:%s' % (user, pw))
    except RuntimeError as e:
        print('init: %s' % e); return 1
    print('Made a zipper in %s (vault, config, backup, compose.yml).' % home)
    if not a.no_start:
        ok, msg = install.start(home)
        if not ok:
            print(msg); return 1
    url = 'http://%s:%d' % (addr, port)
    print()
    print('Open its dashboard%s:' % (' from your laptop or phone (on Tailscale)'
                                    if addr.startswith('100.') else ''))
    print('    %s' % url)
    print('    user: %s   password: %s   (shown once; it is in config/.env)' % (user, pw))
    print()
    print('Then open a terminal on the dashboard, log in to Claude when it asks, and say')
    print('"set me up".')
    if not a.no_start:
        offer_discord(home)
    return 0


def offer_discord(home):
    """Discord at install time, since it is set up here on the machine: at once when
    this machine already has a gateway (no question to ask), else if they say so."""
    import sys
    from . import discordsetup
    if discordsetup.find_gateway()[0] is None:
        if not sys.stdin.isatty():
            return
        print()
        ans = input('Reach this zipper from Discord too? It takes a bot you make '
                    '(about five minutes). [y/N] ')
        if ans.strip().lower() not in ('y', 'yes'):
            print('Later, from %s: ./zipper discord connect' % home)
            return
    print()
    env = dict(os.environ, ZIPPER_ENV_FILE=os.path.join(home, 'config', '.env'),
               ZIPPER_VAULT=os.path.join(home, 'vault'))
    env.pop('ZIPPER_SETTINGS', None)
    subprocess.run([os.path.join(home, 'zipper'), 'discord', 'connect'], env=env)


def cmd_init_vault(a):
    try:
        files = init_vault(a.path, owner=a.owner or settings.get('owner'),
                           zid=a.id or settings.zipper_id(), backup=a.backup or _backup_default(),
                           starter=a.starter)
    except RuntimeError as e:
        print('init: %s' % e); return 1
    print('vault: %s (%d files)' % (os.path.abspath(a.path), len(files)))
    b = a.backup or _backup_default()
    print('backup: %s' % (b or 'NONE -- a lost directory takes its history with it; '
                                'add one with --backup <path>'))
    return 0


def cmd_setup(a):
    action = getattr(a, 'action', None) or 'hook'
    if action == 'hook':
        print('Stop and SessionStart hooks -> %s' % install_hook())
        return 0
    from . import core
    if action == 'starter':
        added = add_starter(core.VAULT)
        print('added %s' % (', '.join(added) if added else 'nothing -- it was all there'))
        return 0
    p = os.path.join(core.VAULT, 'CLAUDE.md')
    with open(p, encoding='utf-8') as fh:
        text = fh.read()
    if action == 'remaining':
        left = remaining(text)
        print('\n'.join(left) if left else ('nothing left -- `zipper setup done` removes the guide'
                                            if GUIDE_OPEN in text else 'setup is finished'))
        return 0
    try:
        new = done(text, getattr(a, 'section', None))
    except RuntimeError as e:
        print('setup: %s' % e); return 1
    with open(p, 'w', encoding='utf-8') as fh:
        fh.write(new)
    left = remaining(new)
    print('removed %s. %s' % (a.section or 'the setup guide',
                              ('left: ' + ', '.join(left)) if left else
                              ('nothing left -- run `zipper setup done` to remove the guide'
                               if GUIDE_OPEN in new else 'CLAUDE.md is now just the rules.')))
    return 0
