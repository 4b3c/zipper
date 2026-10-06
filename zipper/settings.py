"""zipper.settings

`settings.json`, in the vault: what this zipper is and does -- its id, whose it is,
its ports, which plugins are on, how often each pulls, and (under
`plugins.dashboard.rows`) the dashboard's cards. Structure only. **Secrets stay in
`.env`**, outside the vault, so an agent can read and edit this file freely without
a token entering a transcript.

Where it is: `$ZIPPER_SETTINGS`, else `$ZIPPER_VAULT/settings.json` (the vault is found
through the environment or `.env`, never through this file). An old checkout-level
`zipper.settings.json` is still read until `zipper settings migrate` moves it.

    {
      "id": "zipper-0", "owner": "Sam", "vault": "/zipper/vault",
      "discord": {"channel": "...", "notify_channel": ""},
      "plugins": {
        "github":    {"enabled": true, "user": "sam", "orgs": []},
        "dashboard": {"enabled": false}
      }
    }

**Plugin settings live under `plugins.<name>`**, and their defaults come from each
plugin's own `plugins/<name>/plugin.json` -- so a new plugin needs no edit here.

**How the code sees it.** Most readers use environment variables. `apply()` runs once
at import and fills in any variable not already set, from the core keys below and from
each manifest's `env` map. Order everywhere: the real environment, then `.env`
(through `core.cfg`), then this file.

**An older file** -- plugin settings under `inputs`, `peers`, `host`, `google`,
`extension`, `schedule.pass` -- is translated on read, and `zipper settings migrate`
rewrites it in the current shape.
"""
import copy, json, os, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN_DIR = os.path.join(ROOT, 'plugins')
LEGACY_PATH = os.path.join(ROOT, 'zipper.settings.json')


def _vault_from_env():
    """The vault is what locates the settings, so it cannot come from them: the
    environment, or failing that the `.env` file (which a shell does not load)."""
    v = os.environ.get('ZIPPER_VAULT', '')
    if v:
        return v
    envf = os.environ.get('ZIPPER_ENV_FILE') or os.path.join(ROOT, '.env')
    try:
        with open(envf) as fh:
            for ln in fh:
                if ln.strip().startswith('ZIPPER_VAULT='):
                    return ln.split('=', 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ''


def _path():
    """`settings.json` in the vault. `$ZIPPER_SETTINGS` overrides; a checkout that
    still has the old `zipper.settings.json` and no vault file keeps reading it
    until `zipper settings migrate` moves it."""
    if os.environ.get('ZIPPER_SETTINGS'):
        return os.environ['ZIPPER_SETTINGS']
    vault = _vault_from_env()
    if vault:
        p = os.path.join(vault, 'settings.json')
        if os.path.exists(p) or not os.path.exists(LEGACY_PATH):
            return p
    return LEGACY_PATH


PATH = _path()

# The core's shape, with defaults. Plugins add theirs from their manifests.
DEFAULTS = {
    'id': 'zipper-0',
    'owner': '',
    'timezone': '',
    'discord': {'channel': '', 'notify_channel': ''},
    'ports': {'web': 8800, 'bot': 4200, 'ttyd_base': 8810},
    'terminal': {'host': ''},
    'claude': {'permission_mode': 'auto'},
    'code': {'repo': '', 'branch': 'main'},
    'github_app': {'app_id': '', 'install_id': '', 'slug': '', 'uid': '', 'key': ''},
}

CORE_ENV = {
    'ZIPPER_ID': 'id', 'ZIPPER_OWNER': 'owner', 'TZ': 'timezone',
    'DISCORD_CHANNEL_ID': 'discord.channel', 'ZIPPER_NOTIFY_CHANNEL': 'discord.notify_channel',
    'ZIPPER_TTYD_BASE': 'ports.ttyd_base', 'ZIPPER_TERM_HOST': 'terminal.host',
    'ZIPPER_PERMISSION_MODE': 'claude.permission_mode',
    'ZIPPER_CODE_REPO': 'code.repo', 'ZIPPER_CODE_BRANCH': 'code.branch',
    'ZIPPER_GH_APP_ID': 'github_app.app_id', 'ZIPPER_GH_APP_INSTALL_ID': 'github_app.install_id',
    'ZIPPER_GH_APP_SLUG': 'github_app.slug', 'ZIPPER_GH_APP_UID': 'github_app.uid',
}


# ---------------------------------------------------------------- manifests

def manifests():
    """Read here rather than through zipper.plugins: this module runs at import,
    before anything else in the package, and must not import it."""
    out = {}
    try:
        names = sorted(os.listdir(PLUGIN_DIR))
    except FileNotFoundError:
        return out
    for n in names:
        try:
            with open(os.path.join(PLUGIN_DIR, n, 'plugin.json'), encoding='utf-8') as fh:
                out[n] = json.load(fh)
        except (OSError, ValueError):     # not a plugin folder (__init__.py, caches)
            continue
    return out


def defaults():
    d = copy.deepcopy(DEFAULTS)
    d['plugins'] = {n: dict({'enabled': bool(m.get('default_on'))}, **(m.get('defaults') or {}))
                    for n, m in manifests().items()}
    return d


# ---------------------------------------------------------------- reading

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


LEGACY_KEYS = ('inputs', 'peers', 'host', 'google', 'extension', 'vault')


def _legacy(data):
    """Translate the pre-plugin shape. Returns a new dict; never writes."""
    data = copy.deepcopy(data)
    data.pop('vault', None)             # located by ZIPPER_VAULT, never by this file
    p = data.setdefault('plugins', {})

    def put_p(name, key, value):
        p.setdefault(name, {})
        p[name].setdefault(key, value)
    for name, conf in (data.pop('inputs', None) or {}).items():
        for k, v in (conf or {}).items():
            put_p(name, k, v)
    if 'peers' in data:
        put_p('peers', 'zippers', data.pop('peers') or {})
    if 'host' in data:
        put_p('host', 'socket', (data.pop('host') or {}).get('socket', ''))
    g = data.pop('google', None) or {}
    if g.get('account'):
        put_p('hours', 'google_account', g['account'])
    if g.get('client_id'):
        put_p('hours', 'google_client_id', g['client_id'])
    ext = data.pop('extension', None) or {}
    if ext.get('base'):
        put_p('canvas', 'extension_base', ext['base'])
    code = data.get('code') or {}
    if 'auto_update' in code:
        put_p('upstream', 'auto_update', code.pop('auto_update'))
    sched = data.pop('schedule', None) or {}
    if sched.get('fetch_minutes') and int(sched['fetch_minutes']) != 60:
        for n in ('github', 'calendar', 'canvas', 'hours', 'upstream'):
            put_p(n, 'poll_minutes', int(sched['fetch_minutes']))
    if 'pass' in sched:
        put_p('passes', 'times', sched.pop('pass'))
    if 'digest' in sched:
        t = sched.pop('digest')
        put_p('digest', 'time', t)
    if not p:
        data.pop('plugins')
    return data


def is_legacy(data=None):
    data = raw() if data is None else data
    return any(k in data for k in LEGACY_KEYS) or \
        'auto_update' in (data.get('code') or {}) or \
        'schedule' in data


def load():
    """The file (translated if old) merged over the defaults. Re-read on every call."""
    return _merge(defaults(), _legacy(raw()))


def get(path, default=None):
    """A dotted path: `get('plugins.hours.sheet')`."""
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


# ---------------------------------------------------------------- writing

def put(path, value):
    """Write one dotted key and save atomically. An old-shape file is migrated in the
    same write, so a key is never written into a layout that is then translated
    around it."""
    data = _legacy(raw()) if is_legacy() else raw()
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
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix='.settings-')
    with os.fdopen(fd, 'w', encoding='utf-8') as fh:
        json.dump(data, fh, indent=2)
        fh.write('\n')
    os.replace(tmp, PATH)


def check():
    """Problems with the file: unknown keys, wrong types, unknown plugins. [] when clean."""
    out = []
    try:
        data = raw()
    except ValueError as e:
        return ['unreadable: %s' % e]
    if is_legacy(data):
        out.append('old layout (plugin settings outside "plugins") -- `zipper settings migrate`')
    base = defaults()

    def walk(ref, over, where):
        for k, v in over.items():
            here = '%s.%s' % (where, k) if where else k
            if where == 'plugins' and k not in ref:
                out.append('%s: no such plugin' % here)
                continue
            if where.startswith('plugins.') and where.count('.') == 1 and k in ('zippers', 'topics'):
                continue                    # peers' and topics' maps: any names
            if k not in ref:
                out.append('%s: unknown key' % here)
            elif isinstance(ref[k], dict):
                if not isinstance(v, dict):
                    out.append('%s: should be an object' % here)
                else:
                    walk(ref[k], v, here)
            elif ref[k] is not None and not isinstance(v, type(ref[k])) \
                    and not (isinstance(ref[k], int) and isinstance(v, int)):
                out.append('%s: should be %s' % (here, type(ref[k]).__name__))
    walk(base, _legacy(data), '')
    return out


# ---------------------------------------------------------------- the environment

def _flat(v):
    return ','.join(str(x) for x in v) if isinstance(v, list) else ('' if v is None else str(v))


def env():
    """The environment variables this file stands for."""
    s = load()
    out = {}
    for var, path in CORE_ENV.items():
        cur = s
        for part in path.split('.'):
            cur = cur.get(part, '') if isinstance(cur, dict) else ''
        out[var] = _flat(cur)
    out['ZIPPER_PORT'] = str(s['ports']['web'])
    out['ZIPPER_URL'] = 'http://127.0.0.1:%s' % s['ports']['web']
    out['BOT_PORT'] = str(s['ports']['bot'])
    out['BOT_URL'] = 'http://127.0.0.1:%s' % s['ports']['bot']
    key = s['github_app']['key']
    out['ZIPPER_GH_APP_KEY'] = os.path.join(ROOT, key) if key else ''
    for name, m in manifests().items():
        conf = s['plugins'].get(name, {})
        for var, k in (m.get('env') or {}).items():
            out[var] = _flat(conf.get(k))
    return {k: v for k, v in out.items() if v not in ('', None)}


def apply():
    """Fill unset environment variables from the file. Never overrides. An unreadable
    file is reported and ignored: this runs at import, and a typo must not stop
    `zipper settings` itself from starting to fix it."""
    try:
        values = env()
    except ValueError as e:
        import sys
        print('zipper.settings.json is unreadable (%s) -- ignored' % e, file=sys.stderr)
        return
    for k, v in values.items():
        os.environ.setdefault(k, v)
    # An older settings file -- outside the vault, from before settings.json moved
    # in -- may be the only place the vault is named. Honour it until migrated:
    # without this, a zipper whose environment never set ZIPPER_VAULT fell back to
    # its own code checkout and treated that as the vault.
    if not os.environ.get('ZIPPER_VAULT'):
        try:
            old = raw().get('vault')
        except ValueError:
            old = None
        if old:
            os.environ['ZIPPER_VAULT'] = old


# ---------------------------------------------------------------- migrating

def _env_to_path():
    """Every non-secret variable -> where it lives in the file."""
    out = dict(CORE_ENV)
    for name, m in manifests().items():
        for var, k in (m.get('env') or {}).items():
            out[var] = 'plugins.%s.%s' % (name, k)
    return out


def migrate(envfile):
    """Rewrite an old-shape file in the current shape, and copy the non-secret keys
    of a `.env` in. `.env` itself is left alone: the operator deletes the moved
    lines, because an edit to a file of secrets is theirs. Returns what moved."""
    from .core import _env_file
    e = _env_file() if envfile is None else _read_env(envfile)
    data = _legacy(raw())
    moved = ['(rewrote the old layout)'] if is_legacy() else []
    lists = {'plugins.github.orgs'}

    def put_in(path, value):
        cur = data
        parts = path.split('.')
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
        cur[parts[-1]] = value
        moved.append(path)
    for var, path in _env_to_path().items():
        if var == 'ZIPPER_GH_APP_KEY' or not e.get(var):
            continue
        v = e[var]
        if path in lists:
            v = [x.strip() for x in v.split(',') if x.strip()]
        elif path.startswith('ports.') and v.isdigit():
            v = int(v)
        put_in(path, v)
    if e.get('ZIPPER_GH_APP_KEY'):
        k = e['ZIPPER_GH_APP_KEY']
        put_in('github_app.key', os.path.relpath(k, ROOT) if k.startswith(ROOT) else k)
    if e.get('ZIPPER_INPUTS'):
        for n in (x.strip() for x in e['ZIPPER_INPUTS'].split(',') if x.strip()):
            put_in('plugins.%s.enabled' % n, True)
    for key, name in (('ZIPPER_URL', 'web'), ('BOT_URL', 'bot')):
        port = e.get(key, '').rsplit(':', 1)[-1].strip('/')
        if port.isdigit():
            put_in('ports.' + name, int(port))
    data.pop('vault', None)             # the environment locates the vault now
    global PATH
    vault = _vault_from_env()
    if PATH == LEGACY_PATH and vault and os.path.isdir(vault):
        # One file, in the vault: move it there. The old one is renamed rather
        # than deleted, so a rollback to older code still finds something.
        PATH = os.path.join(vault, 'settings.json')
        moved.append('(moved to %s)' % PATH)
        save(data)
        os.replace(LEGACY_PATH, LEGACY_PATH + '.moved')
    else:
        save(data)
    return moved


def _read_env(path):
    out = {}
    if not os.path.exists(path):
        return out
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
        for p in check():
            print('  warning: %s' % p)
        print('restart the services for long-running components to see it')
        return 0
    if action == 'migrate':
        moved = migrate(getattr(a, 'env', None))
        print('%d change(s) to %s:' % (len(moved), PATH))
        for m in moved:
            print('  %s' % m)
        print('Now delete moved lines from .env; it should hold only secrets.')
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
