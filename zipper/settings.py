"""zipper.settings

`zipper.settings.json`: what this zipper is and does -- its id, whose it is, which
inputs run, when it wakes, its ports, its peers. Structure only. **Secrets stay in
`.env`**, so an agent can read and edit this file freely without a token ever
entering a transcript.

Where it is: `$ZIPPER_SETTINGS`, else `zipper.settings.json` beside `.env` in the
checkout. Both are gitignored; `zipper.settings.example.json` is the documented
shape.

**How the code sees it.** Most of the code reads environment variables, and has
for a long time. Rather than rewrite every reader, `apply()` runs once at import
and fills in any variable the environment has not already set, from the file. So
the order is: the real environment, then `.env` (through `core.cfg`), then this
file. A value set in more than one place is resolved the same way by every
component -- which matters, because two components answering one question
differently is the classic bug here.
"""
import json, os, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.environ.get('ZIPPER_SETTINGS') or os.path.join(ROOT, 'zipper.settings.json')

# The shape, with defaults. Anything absent from the file falls back to these; a
# key in the file that is absent here is reported by `zipper settings --check`.
DEFAULTS = {
    'id': 'zipper-0',
    'owner': '',
    'vault': '',
    'timezone': '',
    'inputs': {
        'github':   {'enabled': True, 'user': '', 'orgs': []},
        'calendar': {'enabled': True},
        'canvas':   {'enabled': False, 'host': ''},
        'hours':    {'enabled': False, 'sheet': '', 'metric': 'hours_worked'},
        'upstream': {'enabled': True},
        'peers':    {'enabled': True},
    },
    'discord': {'channel': '', 'notify_channel': ''},
    'schedule': {'fetch_minutes': 60, 'pass': ['09:00', '21:00'], 'digest': '19:00'},
    'ports': {'web': 8800, 'bot': 4200, 'ttyd_base': 8810},
    'terminal': {'host': ''},
    'claude': {'permission_mode': 'auto'},
    'code': {'repo': '', 'branch': 'main', 'auto_update': True},
    'github_app': {'app_id': '', 'install_id': '', 'slug': '', 'uid': '', 'key': ''},
    'google': {'account': '', 'client_id': ''},
    'extension': {'base': ''},
    'peers': {},
    'host': {'socket': ''},
}


def _merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = _merge(base[k], v) if isinstance(base.get(k), dict) and isinstance(v, dict) else v
    return out


def raw():
    """The file as written, or {} if there is none."""
    try:
        with open(PATH, encoding='utf-8') as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {}


def load():
    """The file merged over the defaults. Re-read on every call: it is small, and a
    long-running server should see an edit without a restart where it can."""
    return _merge(DEFAULTS, raw())


def get(path, default=None):
    """A dotted path: `get('inputs.hours.sheet')`."""
    cur = load()
    for part in path.split('.'):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def _parse(value):
    """A CLI value: JSON if it parses (numbers, true, lists), else the string."""
    try:
        return json.loads(value)
    except ValueError:
        return value


def put(path, value):
    """Write one dotted key and save atomically -- a half-written settings file
    would take every component down at its next start."""
    data = raw()
    cur = data
    parts = path.split('.')
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
        if not isinstance(cur, dict):
            raise ValueError('%s is not an object' % part)
    cur[parts[-1]] = value
    save(data)


def save(data):
    d = os.path.dirname(os.path.abspath(PATH))
    fd, tmp = tempfile.mkstemp(dir=d, prefix='.settings-')
    with os.fdopen(fd, 'w', encoding='utf-8') as fh:
        json.dump(data, fh, indent=2)
        fh.write('\n')
    os.replace(tmp, PATH)


def check():
    """Problems with the file: unknown keys, wrong types. [] when clean."""
    out = []

    def walk(base, over, where):
        for k, v in over.items():
            here = '%s.%s' % (where, k) if where else k
            if where in ('peers', 'inputs') and k not in base:
                if where == 'inputs':
                    out.append('%s: unknown input' % here)
                continue
            if k not in base:
                out.append('%s: unknown key' % here)
            elif isinstance(base[k], dict):
                if not isinstance(v, dict):
                    out.append('%s: should be an object' % here)
                else:
                    walk(base[k], v, here)
            elif base[k] is not None and not isinstance(v, type(base[k])) \
                    and not (isinstance(base[k], int) and isinstance(v, int)):
                out.append('%s: should be %s' % (here, type(base[k]).__name__))
    try:
        walk(DEFAULTS, raw(), '')
    except ValueError as e:
        out.append('unreadable: %s' % e)
    return out


def env():
    """The environment variables this file stands for."""
    s = load()
    ins = s['inputs']
    out = {
        'ZIPPER_ID': s['id'],
        'ZIPPER_OWNER': s['owner'],
        'ZIPPER_VAULT': s['vault'],
        'TZ': s['timezone'],
        'ZIPPER_INPUTS': ','.join(n for n, v in ins.items() if v.get('enabled')),
        'ZIPPER_GH_USER': ins.get('github', {}).get('user', ''),
        'ZIPPER_GH_ORGS': ','.join(ins.get('github', {}).get('orgs', [])),
        'CANVAS_HOST': ins.get('canvas', {}).get('host', ''),
        'ZIPPER_SHEET_ID': ins.get('hours', {}).get('sheet', ''),
        'ZIPPER_HOURS_METRIC': ins.get('hours', {}).get('metric', ''),
        'DISCORD_CHANNEL_ID': str(s['discord']['channel'] or ''),
        'ZIPPER_NOTIFY_CHANNEL': str(s['discord']['notify_channel'] or ''),
        'ZIPPER_PORT': str(s['ports']['web']),
        'ZIPPER_URL': 'http://127.0.0.1:%s' % s['ports']['web'],
        'BOT_PORT': str(s['ports']['bot']),
        'BOT_URL': 'http://127.0.0.1:%s' % s['ports']['bot'],
        'ZIPPER_TTYD_BASE': str(s['ports']['ttyd_base']),
        'ZIPPER_TERM_HOST': s['terminal']['host'],
        'ZIPPER_PERMISSION_MODE': s['claude']['permission_mode'],
        'ZIPPER_CODE_REPO': s['code']['repo'],
        'ZIPPER_CODE_BRANCH': s['code']['branch'],
        'ZIPPER_GH_APP_ID': str(s['github_app']['app_id']),
        'ZIPPER_GH_APP_INSTALL_ID': str(s['github_app']['install_id']),
        'ZIPPER_GH_APP_SLUG': s['github_app']['slug'],
        'ZIPPER_GH_APP_UID': str(s['github_app']['uid']),
        'ZIPPER_GH_APP_KEY': (os.path.join(ROOT, s['github_app']['key'])
                              if s['github_app']['key'] else ''),
        'ZIPPER_GOOGLE_ACCOUNT': s['google']['account'],
        'ZIPPER_GOOGLE_CLIENT_ID': s['google']['client_id'],
        'ZIPPER_EXT_BASE': s['extension']['base'],
        'ZIPPER_HOST_SOCKET': s['host']['socket'],
    }
    return {k: v for k, v in out.items() if v not in ('', None)}


def apply():
    """Fill unset environment variables from the file. Never overrides: the real
    environment and `.env` both outrank it. An unreadable file is reported and
    ignored rather than raised, because this runs at import and a typo must not
    stop `zipper settings` itself from starting to fix it."""
    try:
        values = env()
    except ValueError as e:
        import sys
        print('zipper.settings.json is unreadable (%s) -- ignored' % e, file=sys.stderr)
        return
    for k, v in values.items():
        os.environ.setdefault(k, v)


# Where each non-secret variable lives in the file, for `migrate`. Derived
# variables (ZIPPER_URL, BOT_URL, ZIPPER_INPUTS) are handled by hand below.
FROM_ENV = {
    'ZIPPER_ID': 'id', 'ZIPPER_OWNER': 'owner', 'ZIPPER_VAULT': 'vault', 'TZ': 'timezone',
    'ZIPPER_GH_USER': 'inputs.github.user', 'CANVAS_HOST': 'inputs.canvas.host',
    'ZIPPER_SHEET_ID': 'inputs.hours.sheet', 'ZIPPER_HOURS_METRIC': 'inputs.hours.metric',
    'DISCORD_CHANNEL_ID': 'discord.channel', 'ZIPPER_NOTIFY_CHANNEL': 'discord.notify_channel',
    'ZIPPER_TTYD_BASE': 'ports.ttyd_base', 'ZIPPER_TERM_HOST': 'terminal.host',
    'ZIPPER_PERMISSION_MODE': 'claude.permission_mode',
    'ZIPPER_GH_APP_ID': 'github_app.app_id', 'ZIPPER_GH_APP_INSTALL_ID': 'github_app.install_id',
    'ZIPPER_GH_APP_SLUG': 'github_app.slug', 'ZIPPER_GH_APP_UID': 'github_app.uid',
    'ZIPPER_GOOGLE_ACCOUNT': 'google.account', 'ZIPPER_GOOGLE_CLIENT_ID': 'google.client_id',
    'ZIPPER_EXT_BASE': 'extension.base',
}


def migrate(envfile):
    """Copy the non-secret keys of an existing `.env` into the settings file.
    Returns the keys moved. `.env` is left alone: the operator deletes the moved
    lines once they have looked, because an edit to a file of secrets is theirs."""
    from .core import _env_file
    e = _env_file() if envfile is None else _read_env(envfile)
    data = raw()
    moved = []

    def put_in(path, value):
        cur = data
        parts = path.split('.')
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
        cur[parts[-1]] = value
        moved.append(path)
    for k, path in FROM_ENV.items():
        if e.get(k):
            v = e[k]
            put_in(path, int(v) if path.startswith('ports.') and v.isdigit() else v)
    if e.get('ZIPPER_GH_ORGS'):
        put_in('inputs.github.orgs', [o.strip() for o in e['ZIPPER_GH_ORGS'].split(',') if o.strip()])
    if e.get('ZIPPER_GH_APP_KEY'):
        k = e['ZIPPER_GH_APP_KEY']
        put_in('github_app.key', os.path.relpath(k, ROOT) if k.startswith(ROOT) else k)
    if e.get('ZIPPER_INPUTS'):
        on = [n.strip() for n in e['ZIPPER_INPUTS'].split(',') if n.strip()]
        for n in DEFAULTS['inputs']:
            put_in('inputs.%s.enabled' % n, n in on)
    for key, name in (('ZIPPER_URL', 'web'), ('BOT_URL', 'bot')):
        port = e.get(key, '').rsplit(':', 1)[-1].strip('/')
        if port.isdigit():
            put_in('ports.' + name, int(port))
    save(data)
    return moved


def _read_env(path):
    out = {}
    with open(path) as fh:
        for ln in fh:
            ln = ln.strip()
            if ln and not ln.startswith('#') and '=' in ln:
                k, v = ln.split('=', 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def zipper_id():
    return os.environ.get('ZIPPER_ID') or get('id') or 'zipper-0'


# ---------------------------------------------------------------- the command

def cmd_settings(a):
    action = getattr(a, 'action', None) or 'show'
    if action == 'path':
        print(PATH); return 0
    if action == 'get':
        v = get(a.key)
        if v is None:
            print('%s: unset' % a.key); return 1
        print(json.dumps(v, indent=2) if isinstance(v, (dict, list)) else v)
        return 0
    if action == 'set':
        put(a.key, _parse(a.value))
        print('%s = %s' % (a.key, json.dumps(get(a.key))))
        probs = check()
        for p in probs:
            print('  warning: %s' % p)
        print('restart the services for long-running components to see it')
        return 0
    if action == 'migrate':
        moved = migrate(getattr(a, 'env', None))
        print('moved %d value(s) into %s:' % (len(moved), PATH))
        for m in moved:
            print('  %s' % m)
        print('Now delete those lines from .env; it should hold only secrets.')
        return 0
    if action == 'check':
        probs = check()
        for p in probs:
            print(p)
        print('settings: %s' % ('clean' if not probs else '%d problem(s)' % len(probs)))
        return 1 if probs else 0
    print('# %s%s' % (PATH, '' if os.path.exists(PATH) else '  (absent -- defaults)'))
    print(json.dumps(load(), indent=2))
    return 0
