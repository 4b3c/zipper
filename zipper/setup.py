"""zipper.setup

Standing up a zipper from nothing: `zipper init` makes a vault, `zipper setup` asks
everything else.

    zipper init <path> [--owner NAME] [--id ID]
    zipper setup [--section S]

**Every question writes one of two places.** Structure goes to zipper.settings.json;
a secret goes to `.env` and is never echoed back -- the wizard shows only whether it
is set. That is the same split the agent lives by: it may read settings, and it
never opens `.env`.

**Inputs describe their own setup.** The checklist is the registry: each module in
`zipper/inputs/` offers `SETUP_TITLE`, `SETUP_ABOUT` and `setup(w)`, so a new input
appears here by existing. Nothing below names one.

Plain `input()` rather than a curses UI: it works over `docker exec`, a pipe, and a
phone's SSH client alike, and a test can drive it by writing lines to stdin.
"""
import getpass, importlib, json, os, re, shutil, subprocess, sys

from . import settings

ROOT = settings.ROOT
TEMPLATE = os.path.join(ROOT, 'template', 'vault')
from .core import ENV_FILE
HOOK = os.path.join(ROOT, 'hooks', 'forward_reply.py')

# The vault's directories. `core` expects them; git does not keep empty ones.
DIRS = ('Projects', 'Areas', 'Topics', 'Life', 'People', 'Classes', 'Tasks',
        'Decisions', 'Events', 'Log', 'Metrics', 'Meta', 'Inbox')


# ---------------------------------------------------------------- the vault

def init_vault(path, owner='', zid='', git_name='', git_email='', backup=''):
    """Create a vault at `path` from the template. Refuses a non-empty directory
    that is not already a vault: overwriting someone's notes is the one mistake
    here that cannot be undone. Returns the list of files written."""
    path = os.path.abspath(os.path.expanduser(path))
    if os.path.exists(os.path.join(path, 'CLAUDE.md')):
        raise RuntimeError('%s already holds a vault -- leaving it alone' % path)
    if os.path.isdir(path) and _has_notes(path):
        raise RuntimeError('%s is not empty and is not a vault' % path)
    os.makedirs(path, exist_ok=True)
    for d in DIRS:
        os.makedirs(os.path.join(path, d), exist_ok=True)
    subs = {'{{OWNER}}': owner or 'the operator', '{{ID}}': zid or settings.zipper_id()}
    written = []
    for base, _dirs, files in os.walk(TEMPLATE):
        for f in files:
            src = os.path.join(base, f)
            rel = os.path.relpath(src, TEMPLATE)
            dst = os.path.join(path, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(src, encoding='utf-8') as fh:
                text = fh.read()
            for k, v in subs.items():
                text = text.replace(k, v)
            with open(dst, 'w', encoding='utf-8') as fh:
                fh.write(text)
            written.append(rel)
    csv = os.path.join(path, 'Metrics', 'metrics.csv')
    if not os.path.exists(csv):
        with open(csv, 'w', encoding='utf-8') as fh:
            fh.write('date,key,value,note,source\n')
    # A local identity, always: services run with no HOME, and a commit that
    # falls back to root@hostname is how a history stops meaning anything.
    if not os.path.isdir(os.path.join(path, '.git')):
        subprocess.run(['git', 'init', '-q', '-b', 'main', path], check=True)
    ident = (git_name or zid or 'zipper', git_email or '%s@zipper.local' % (zid or 'zipper'))
    subprocess.run(['git', '-C', path, 'config', 'user.name', ident[0]], check=True)
    subprocess.run(['git', '-C', path, 'config', 'user.email', ident[1]], check=True)
    subprocess.run(['git', '-C', path, 'add', '-A'], check=True)
    subprocess.run(['git', '-C', path, 'commit', '-q', '-m', 'A new vault'], check=False)
    if backup:
        from . import backup as _backup
        _backup.attach(backup, vault=path)
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


def env_set(key, value):
    """Set one line of `.env`, keeping every other line (and comment) as it was.
    Written 600: it is the file of secrets."""
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


# ---------------------------------------------------------------- the Stop hook

def install_hook(settings_path=None):
    """Point Claude Code's `Stop` hook at this checkout's forward_reply.py.

    Without it a Discord conversation works and its replies silently never
    arrive -- the failure a fresh install hit first. Idempotent: an existing
    forward_reply entry is repointed rather than duplicated (two would post every
    reply twice), and every other setting in the file is kept."""
    p = settings_path or os.path.expanduser('~/.claude/settings.json')
    try:
        with open(p, encoding='utf-8') as fh:
            data = json.load(fh)
    except FileNotFoundError:
        data = {}
    cmd = 'python3 %s' % HOOK
    stop = data.setdefault('hooks', {}).setdefault('Stop', [])
    found = False
    for group in stop:
        for h in group.get('hooks', []):
            if 'forward_reply.py' in h.get('command', ''):
                h['command'] = cmd
                found = True
    if not found:
        stop.append({'hooks': [{'type': 'command', 'command': cmd, 'timeout': 45,
                                'statusMessage': 'Forwarding reply to Discord'}]})
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(data, fh, indent=2)
        fh.write('\n')
    os.replace(tmp, p)
    return p


# ---------------------------------------------------------------- the wizard

class Wizard:
    """What an input's `setup(w)` is handed. Reads stdin, writes settings or .env."""

    def __init__(self, inp=input, out=print):
        self.inp, self.out = inp, out

    def say(self, text):
        self.out('  ' + text)

    def ask(self, prompt, default=''):
        d = ' [%s]' % default if default not in ('', None) else ''
        try:
            v = self.inp('  %s%s: ' % (prompt, d)).strip()
        except EOFError:
            v = ''
        return v or (default if default is not None else '')

    def yes(self, prompt, default=True):
        v = self.ask(prompt + (' (Y/n)' if default else ' (y/N)')).lower()
        return default if not v else v.startswith('y')

    def setting(self, key, prompt, default=None, kind=str):
        cur = settings.get(key)
        shown = ', '.join(cur) if isinstance(cur, list) else cur
        v = self.ask(prompt, default=shown if shown not in ('', None, []) else (default or ''))
        if kind is list:
            val = [x.strip() for x in str(v).split(',') if x.strip()]
        elif kind is int:
            val = int(v) if str(v).strip().isdigit() else (cur if isinstance(cur, int) else 0)
        else:
            val = v
        settings.put(key, val)
        return val

    def secret(self, env, prompt):
        have = bool(env_get(env))
        tag = ' (set -- Enter keeps it)' if have else ''
        try:
            if sys.stdin.isatty():
                v = getpass.getpass('  %s%s: ' % (prompt, tag)).strip()
            else:
                v = self.inp('  %s%s: ' % (prompt, tag)).strip()
        except EOFError:
            v = ''
        if v:
            env_set(env, v)
        return bool(v) or have

    def run(self, argv):
        r = subprocess.run([sys.executable, '-m', 'zipper'] + argv, cwd=ROOT)
        if r.returncode:
            self.say('(that step failed -- run `zipper %s` again later)' % ' '.join(argv[:1]))


def _inputs():
    from . import inputs
    return [importlib.import_module('zipper.inputs.' + n) for n in inputs.KNOWN]


def _checklist(w, mods):
    """A toggle list. Numbers flip boxes; Enter accepts."""
    on = {m.name: bool(settings.get('inputs.%s.enabled' % m.name)) for m in mods}
    while True:
        w.out('')
        for i, m in enumerate(mods, 1):
            w.out('  %d. [%s] %-28s %s' % (i, 'x' if on[m.name] else ' ',
                                          getattr(m, 'SETUP_TITLE', m.name),
                                          getattr(m, 'SETUP_ABOUT', '')))
        v = w.ask('Numbers to toggle, Enter to continue')
        if not v:
            return on
        for tok in re.split(r'[\s,]+', v):
            if tok.isdigit() and 1 <= int(tok) <= len(mods):
                n = mods[int(tok) - 1].name
                on[n] = not on[n]


SECTIONS = ('identity', 'vault', 'inputs', 'discord', 'schedule', 'claude')


def wizard(w, only=None):
    def want(s):
        return only is None or s == only

    if want('identity'):
        w.out('\n== Who this zipper is ==')
        w.setting('id', 'Id (unique among the zippers on this host)', default='zipper-0')
        w.setting('owner', 'Whose it is -- a first name')
        w.setting('timezone', 'Timezone (e.g. America/Denver)', default=_host_tz())

    if want('vault'):
        w.out('\n== The vault ==')
        path = w.setting('vault', 'Where the notes live (absolute path)',
                         default=os.environ.get('ZIPPER_VAULT_DEFAULT')
                         or os.path.expanduser('~/vault'))
        if path and not os.path.exists(os.path.join(path, 'CLAUDE.md')):
            if w.yes('No vault at %s. Create one from the template?' % path):
                bk = w.ask('Backup: a git repo to push every commit to, outside the vault '
                           '(blank for none)', default=_backup_default()
                           or os.path.join(os.path.dirname(path.rstrip('/')), 'vault-backup.git'))
                try:
                    init_vault(path, owner=settings.get('owner'), zid=settings.get('id'),
                               backup=bk)
                    w.say('created %s%s' % (path, ', backed up to %s' % bk if bk else ''))
                except RuntimeError as e:
                    w.say(str(e))

    if want('inputs'):
        w.out('\n== Inputs: what this zipper reads ==')
        mods = _inputs()
        on = _checklist(w, mods)
        for m in mods:
            settings.put('inputs.%s.enabled' % m.name, on[m.name])
        for m in mods:
            if on[m.name] and hasattr(m, 'setup'):
                w.out('\n-- %s --' % getattr(m, 'SETUP_TITLE', m.name))
                m.setup(w)

    if want('discord'):
        w.out('\n== Discord: the door on your phone ==')
        if w.yes('Connect a Discord bot?', default=bool(settings.get('discord.channel'))):
            w.say('discord.com/developers -> New Application -> Bot. Enable the Message')
            w.say('Content intent, invite it to your server, copy the token.')
            w.secret('DISCORD_TOKEN', 'Bot token')
            w.setting('discord.channel', 'Channel id it listens in (Developer Mode -> Copy ID)')
            w.setting('discord.notify_channel', 'Channel id for scheduled messages (blank: same)')
            p = install_hook()
            w.say('replies are forwarded by the Stop hook in %s' % p)

    if want('schedule'):
        w.out('\n== When it wakes ==')
        w.setting('schedule.fetch_minutes', 'Fetch every N minutes', kind=int)
        w.setting('schedule.pass', 'Bookkeeping passes at (comma-separated HH:MM)', kind=list)
        w.setting('schedule.digest', 'Evening digest at HH:MM (blank for none)')

    if want('claude'):
        w.out('\n== Claude ==')
        claude = shutil.which('claude') or os.path.expanduser('~/.local/bin/claude')
        if not os.path.exists(claude):
            w.say('`claude` is not installed: npm install -g @anthropic-ai/claude-code')
        logged = os.path.exists(os.path.expanduser('~/.claude/.credentials.json'))
        w.say('claude.ai login: %s' % ('found' if logged else 'none -- run `claude` once and /login'))
        if not logged:
            w.secret('ANTHROPIC_API_KEY', 'Or an API key (blank to log in instead)')
        w.setting('claude.permission_mode', 'Permission mode for unattended sessions', default='auto')

    probs = settings.check()
    w.out('\n== Done ==')
    w.out('  settings: %s' % settings.PATH)
    for p in probs:
        w.out('  warning: %s' % p)
    w.out('  next: `zipper fetch`, then `zipper run` (or `docker compose up -d`)')
    return 0


def _backup_default():
    """In a container, /zipper/backup is a volume of its own; elsewhere, ask."""
    d = os.environ.get('ZIPPER_BACKUP_DEFAULT', '')
    return os.path.join(d, 'vault.git') if d and os.path.isdir(d) else ''


def _host_tz():
    try:
        return os.path.realpath('/etc/localtime').split('zoneinfo/', 1)[1]
    except (IndexError, OSError):
        return ''


# ---------------------------------------------------------------- commands

def cmd_init(a):
    try:
        files = init_vault(a.path, owner=a.owner or settings.get('owner'),
                           zid=a.id or settings.zipper_id(), backup=a.backup or _backup_default())
    except RuntimeError as e:
        print('init: %s' % e); return 1
    print('vault: %s (%d files)' % (os.path.abspath(a.path), len(files)))
    b = a.backup or _backup_default()
    print('backup: %s' % (b or 'NONE -- a lost directory takes its history with it; '
                                'add one with --backup <path>'))
    if not settings.get('vault'):
        settings.put('vault', os.path.abspath(a.path))
        print('settings: vault = %s' % os.path.abspath(a.path))
    return 0


def cmd_setup(a):
    if getattr(a, 'hook', False):
        print('Stop hook -> %s' % install_hook()); return 0
    return wizard(Wizard(), only=getattr(a, 'section', None))
