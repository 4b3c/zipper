"""zipper.plugins

Everything beyond the core is a plugin. The core is a Discord bot handing messages
to Claude conversations that edit a vault, plus the queue, lint and commit. The
rest -- GitHub, calendars, the dashboard, backups, scheduled passes -- lives in
`plugins/<name>/` at the top of the repository:

    plugins/<name>/plugin.json    the manifest: title, about, default settings,
                                  secrets it needs, env variables its settings stand for
    plugins/<name>/__init__.py    the code, imported only when the plugin is enabled

**Enabled means `plugins.<name>.enabled` in the settings file**, nothing else -- not
whether its credentials happen to be present. A plugin that switches itself on or off
quietly is how a fetch once ran on public repos only and nobody noticed.
`zipper plugin list|info|enable|disable` is the interface; the setup conversation
runs `enable` only after the owner says yes.

A plugin's code provides whichever of these it has; callers check with hasattr.

    pull()                fetch from outside and store under Inbox/
    receive(body) -> dict take a POST to /api/inputs/<name>
    fetched() -> iso      when its data was last read (freshness chips)
    snapshot() -> obj     cheap comparable state; two of them make a diff
    events(before, after) queue rows for what changed between two snapshots
    target(row, notes)    the note a queue row lands on, or None
    timeline(first, last) event rows between two dates (the agenda, the Today grid)
    work()                work items: things that are due
    facts()               {note title: {field: value}} for zipper.writer to apply
    toggle(key)           cross a work item off by hand; keys are `<name>:...`
    CALENDARS             ICS labels it owns; the calendar plugin skips them
    flags() -> [str]      standing disagreements for the brief
    on_commit() -> str    run after `zipper commit`; a string is a warning to print
    jobs(settings) -> [(job, 'HH:MM')]  scheduled `zipper <job>` runs (zipper run)

Two row shapes, because the dashboard asks two different questions.

    event row   "what is on these days?" -- date, time, label, summary, loc, done,
                start, end, uid, url, all_day
    work item   "what is due?" -- source, title, due, at, tag, url, points, next,
                elsewhere, desc, kind, links, course, submitted, done,
                done_by_hand, key
"""
import datetime, importlib, json, os, sys

from . import core, settings

DIR = os.path.join(settings.ROOT, 'plugins')
# `python3 -m zipper` from inside the vault has the vault, not the checkout, on
# sys.path; the top-level `plugins` package has to be findable from anywhere.
if settings.ROOT not in sys.path:
    sys.path.insert(0, settings.ROOT)

# Who "you" are in a queue row's `who`. Configuration: a real name compiled into a
# public repository is a bug.
OWNER = core.cfg('ZIPPER_OWNER') or 'you'


# ---------------------------------------------------------------- manifests

def manifests():
    """{name: manifest} for every plugin in the repository, enabled or not. Reading a
    manifest never imports the plugin's code."""
    out = {}
    try:
        names = sorted(os.listdir(DIR))
    except FileNotFoundError:
        return out
    for n in names:
        p = os.path.join(DIR, n, 'plugin.json')
        try:
            with open(p, encoding='utf-8') as fh:
                out[n] = json.load(fh)
        except (OSError, ValueError):     # not a plugin folder (__init__.py, caches)
            continue
    return out


def is_enabled(name, s=None):
    m = manifests().get(name)
    if not m:
        return False
    s = s if s is not None else settings.load()
    v = (s.get('plugins') or {}).get(name, {}).get('enabled')
    return bool(m.get('default_on')) if v is None else bool(v)


def names():
    s = settings.load()
    return [n for n in manifests() if is_enabled(n, s)]


def load(name):
    return importlib.import_module('plugins.' + name)


def enabled():
    """The enabled plugins' modules, in name order."""
    out = []
    for n in names():
        try:
            out.append(load(n))
        except Exception as e:
            # One broken plugin must not take the core down with it.
            print('plugin %s failed to load: %s' % (n, e), file=sys.stderr)
    return out


def get(name):
    """One enabled plugin's module, or None -- a disabled plugin is treated as absent."""
    return load(name) if is_enabled(name) else None


def require(name):
    """For a command that belongs to a plugin: None if enabled, else a sentence."""
    if name not in manifests():
        return 'no plugin named %r' % name
    if not is_enabled(name):
        return ('the %s plugin is off -- `zipper plugin enable %s` to turn it on'
                % (name, name))
    return None


# ---------------------------------------------------------------- the pipeline

def pull_all(log=print):
    """Pull every plugin that pulls. One failing never stops the rest: a GitHub
    outage or a revoked token degrades the brief, it does not prevent it."""
    errors = []
    for i in enabled():
        if not hasattr(i, 'pull'):
            continue
        log('== %s ==' % i.name)
        try:
            i.pull()
        except Exception as e:
            log('%s step skipped: %s' % (i.name, e))
            errors.append('%s: %s' % (i.name, e))
        log('')
    from . import writer
    log('== facts ==')
    writer.apply_all()
    log('')
    return errors


def pulls():
    """Does any enabled plugin fetch? If not, there is nothing to schedule."""
    return any(hasattr(i, 'pull') for i in enabled())


def freshness():
    return {i.name: i.fetched() for i in enabled() if hasattr(i, 'fetched')}


def snapshot():
    out = {}
    for i in enabled():
        if hasattr(i, 'snapshot'):
            try:
                out[i.name] = i.snapshot()
            except Exception as e:
                print('%s snapshot failed: %s' % (i.name, e))
    return out


def events(before, after):
    """Queue rows for everything that changed between two snapshots. A plugin missing
    from `before` has just been enabled: its first snapshot is a baseline, not news."""
    out = []
    for i in enabled():
        if not hasattr(i, 'events') or i.name not in before or i.name not in after:
            continue
        try:
            out += i.events(before[i.name], after[i.name])
        except Exception as e:
            out.append({'system': 'error', 'action': 'edit',
                        'text': 'error       %s events: %s' % (i.name, e)})
    return out


def target_resolver():
    """A function row -> note title, with the vault read once rather than per row."""
    notes = [(core.title_of(p), core.fm_dict(core.read_note(p)[0])) for p in core.iter_notes()]
    mods = {i.name: i for i in enabled() if hasattr(i, 'target')}

    def resolve(row):
        i = mods.get(row.get('system'))
        return i.target(row, notes) if i else None
    return resolve


def claimed_calendars():
    out = set()
    for i in enabled():
        out.update(getattr(i, 'CALENDARS', ()))
    return out


def timeline(first, last):
    rows = []
    for i in enabled():
        if hasattr(i, 'timeline'):
            rows += i.timeline(first, last)
    rows.sort(key=lambda r: r['label'])
    return rows


def work():
    out = []
    for i in enabled():
        if hasattr(i, 'work'):
            out += i.work()
    return out


def toggle(key):
    i = get(key.split(':', 1)[0])
    return i.toggle(key) if i and hasattr(i, 'toggle') else None


def flags():
    out = []
    for i in enabled():
        if hasattr(i, 'flags'):
            try:
                out += i.flags()
            except Exception as e:
                out.append('%s flags failed: %s' % (i.name, e))
    return out


def on_commit():
    """Warnings from plugins that act after a commit (the backup push)."""
    out = []
    for i in enabled():
        if hasattr(i, 'on_commit'):
            try:
                w = i.on_commit()
            except Exception as e:
                w = str(e)
            if w:
                out.append('%s: %s' % (i.name, w))
    return out


def jobs():
    """[(job, 'HH:MM')] from every enabled plugin that schedules something."""
    out = []
    s = settings.load()
    for i in enabled():
        if hasattr(i, 'jobs'):
            out += i.jobs((s.get('plugins') or {}).get(i.name, {}))
    return out


# ---------------------------------------------------------------- shared helpers

def event_row(label, e, done=None):
    """One ingested ICS event as an event row."""
    return {'date': e['start'][:10], 'time': e['start'][11:], 'label': label,
            'summary': e['summary'], 'loc': e.get('location', ''), 'done': done,
            'start': e['start'], 'end': e.get('end', ''), 'uid': e.get('uid', ''),
            'url': e.get('url', ''),
            'all_day': bool(e.get('all_day')) or len(e['start']) <= 10}


def read_calendar(path):
    try:
        blob = json.load(open(path, encoding='utf-8'))
        return blob['label'], blob['events']
    except Exception:
        return None, []


def mtime_iso(path):
    try:
        return datetime.datetime.fromtimestamp(os.path.getmtime(path)).isoformat(timespec='seconds')
    except OSError:
        return None


def blob_fetched(path):
    """A JSON file's own `fetched` stamp, falling back to its mtime (calendars store a
    bare date, too coarse to age)."""
    try:
        blob = json.load(open(path, encoding='utf-8'))
    except Exception:
        return None
    v = blob.get('fetched')
    if v and len(v) == 10:
        return mtime_iso(path)
    return v or mtime_iso(path)


def as_list(v):
    if v is None:
        return []
    return v if isinstance(v, list) else core.as_list(v)


# ---------------------------------------------------------------- the command

def cmd_plugin(a):
    action = getattr(a, 'action', None) or 'list'
    ms = manifests()
    if action == 'list':
        s = settings.load()
        for n, m in ms.items():
            print('  [%s] %-10s %s' % ('x' if is_enabled(n, s) else ' ', n, m.get('title', '')))
        return 0
    name = a.name
    if name not in ms:
        print('plugin: no plugin named %r (have: %s)' % (name, ', '.join(ms)))
        return 1
    m = ms[name]
    if action == 'info':
        print('%s -- %s' % (m.get('title', name), 'on' if is_enabled(name) else 'off'))
        print('  %s' % m.get('about', ''))
        for k, v in (m.get('defaults') or {}).items():
            print('  setting  plugins.%s.%s = %s' % (name, k,
                  json.dumps(settings.get('plugins.%s.%s' % (name, k), v))))
        for sec in m.get('secrets') or []:
            print('  secret   %s  (%s)' % (sec, 'set' if core.cfg(sec) else 'not set'))
        return 0
    settings.put('plugins.%s.enabled' % name, action == 'enable')
    print('%s %s. Restart for running services to pick it up (`zipper restart --when-idle`).'
          % (m.get('title', name), 'enabled' if action == 'enable' else 'disabled'))
    if action == 'enable':
        missing = [s for s in m.get('secrets') or [] if not core.cfg(s)]
        if missing:
            print('  secrets not set yet: %s -- `zipper secret <NAME>`' % ', '.join(missing))
    return 0
