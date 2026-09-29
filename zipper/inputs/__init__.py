"""zipper.inputs

The inputs: one module per thing in the world Zipper reads about.

An input is a module in this package that provides some of these. Only `name` and
`fetched` are required; everything else is optional, and a caller checks for it.

    name                  the input's name, as written in ZIPPER_INPUTS
    pull()                fetch from outside and store under Inbox/. Absent for an
                          input that is pushed to (the browser posts Canvas)
    receive(body) -> dict take a POSTed payload: /api/inputs/<name>
    fetched() -> iso      when the data was last read, for the freshness chips
    snapshot() -> obj     cheap, comparable state; two of them make a diff
    events(before, after) queue rows for what changed between two snapshots
    target(row, notes)    the note a queue row lands on, or None

**Which inputs run is the operator's choice**, not the code's: `ZIPPER_INPUTS` in
`.env`, comma-separated. Unset means every input this package knows. An input is never
switched on or off by whether its credentials happen to be present -- one that turns
itself off quietly is how a fetch once ran on public repos only and nobody noticed.
"""
import datetime, importlib, json, os

from .. import core

KNOWN = ('github', 'calendar', 'canvas', 'hours')

# Who "you" are in a queue row's `who`. Configuration, because this repository is
# public and a real name compiled in is a bug.
OWNER = core.cfg('ZIPPER_OWNER') or 'you'


def names():
    raw = core.cfg('ZIPPER_INPUTS')
    if not raw:
        return list(KNOWN)
    out = []
    for n in (x.strip() for x in raw.split(',')):
        if not n:
            continue
        if n not in KNOWN:
            print('ZIPPER_INPUTS: unknown input %r -- ignored (known: %s)'
                  % (n, ', '.join(KNOWN)))
            continue
        if n not in out:
            out.append(n)
    return out


def enabled():
    """The enabled input modules, in ZIPPER_INPUTS order."""
    return [importlib.import_module('.' + n, __name__) for n in names()]


def get(name):
    """One enabled input, or None -- a disabled input is treated as absent."""
    return importlib.import_module('.' + name, __name__) if name in names() else None


# ---------------------------------------------------------------- the pipeline

def pull_all(log=print):
    """Pull every input that pulls. One failing never stops the rest: a GitHub
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
    return errors


def freshness():
    return {i.name: i.fetched() for i in enabled()}


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
    """Queue rows for everything that changed between two snapshots.

    An input missing from `before` has just been enabled (or just started
    working). Its first snapshot is a baseline, not news, so it emits nothing --
    otherwise enabling the calendar would publish every event in it as added.
    """
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


# ---------------------------------------------------------------- shared helpers

def mtime_iso(path):
    try:
        return datetime.datetime.fromtimestamp(os.path.getmtime(path)).isoformat(timespec='seconds')
    except OSError:
        return None


def blob_fetched(path):
    """A JSON file's own `fetched` stamp, falling back to its mtime. Calendars store a
    bare date, which is too coarse to age, so those fall back too."""
    try:
        blob = json.load(open(path, encoding='utf-8'))
    except Exception:
        return None
    v = blob.get('fetched')
    if v and len(v) == 10:
        return mtime_iso(path)
    return v or mtime_iso(path)


def as_list(v):
    """`parse_fm` returns a real list for `[a, b]` and a bare string otherwise."""
    if v is None:
        return []
    return v if isinstance(v, list) else core.as_list(v)
