"""zipper.setup

Standing up a zipper from nothing: `zipper init` makes a vault, and `zipper setup`
points Claude Code's Stop hook at this checkout. Everything else is configured by
talking to Claude in the new vault.

    zipper init <path> [--owner NAME] [--id ID] [--backup PATH] [--starter]

Secrets go to `.env`; everything else to zipper.settings.json.
"""
import json, os, re, subprocess

from . import settings

ROOT = settings.ROOT
TEMPLATE = os.path.join(ROOT, 'template', 'vault')      # always: CLAUDE.md, .gitignore
STARTER = os.path.join(ROOT, 'template', 'starter')     # optional: a suggested layout
from .core import ENV_FILE
HOOK = os.path.join(ROOT, 'hooks', 'forward_reply.py')

# The starter layout's folders. None is required: the engine makes Inbox/, Meta/,
# Log/ and Metrics/ when it first writes to them, and a vault is otherwise
# whatever folders its owner wants.
STARTER_DIRS = ('Projects', 'Areas', 'Topics', 'People', 'Tasks', 'Decisions', 'Events')


# ---------------------------------------------------------------- the vault

def init_vault(path, owner='', zid='', git_name='', git_email='', backup='', starter=False):
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
    subprocess.run(['git', '-C', path, 'add', '-A'], check=True)
    subprocess.run(['git', '-C', path, 'commit', '-q', '-m', 'A new vault'], check=False)
    if backup:
        from . import plugins as _plugins           # puts `plugins` on sys.path
        from plugins import backup as _backup
        _backup.attach(backup, vault=path)
        _plugins.settings.put('plugins.backup.enabled', True)
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


def _backup_default():
    """In a container, /zipper/backup is a volume of its own; elsewhere, ask."""
    d = os.environ.get('ZIPPER_BACKUP_DEFAULT', '')
    return os.path.join(d, 'vault.git') if d and os.path.isdir(d) else ''


# ---------------------------------------------------------------- commands

def cmd_init(a):
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
    if not settings.get('vault'):
        settings.put('vault', os.path.abspath(a.path))
        print('settings: vault = %s' % os.path.abspath(a.path))
    return 0


def cmd_setup(a):
    print('Stop hook -> %s' % install_hook())
    return 0
