"""Is the work behind a Discord status message still happening, and what is it?

The bot keeps one status message per thread and slot ("⏳ Still working · 3 min")
and edits it every minute -- see `bot/status.py`. Before each edit it asks this
module, because a status that only counts minutes is a clock, not a witness: it
would keep saying "working" over a turn that had died. Read, never tracked.

Two slots:

    turn    a headless Discord turn (`convhead`) answering in that thread
    review  a `zipper code review` running for a change that thread asked for

`activity(thread_id, slot)` returns `(running, doing)`; `doing` is a short line
for the status message, or ''.
"""
import glob, json, os

from . import convhead
from .convcore import load

DOING_MAX = 70


def _proc_env(pid):
    try:
        with open('/proc/%s/environ' % pid, 'rb') as fh:
            raw = fh.read()
    except OSError:
        return {}
    out = {}
    for part in raw.split(b'\0'):
        k, _, v = part.partition(b'=')
        if k:
            out[k.decode('utf-8', 'replace')] = v.decode('utf-8', 'replace')
    return out


def _cmdline(pid):
    try:
        with open('/proc/%s/cmdline' % pid, 'rb') as fh:
            return fh.read().split(b'\0')
    except OSError:
        return []


def _turn_process(thread_id):
    """A headless turn's `claude` for this thread, found by its environment.

    **Not only the lock.** `convhead.turn_running` reads a lock held by the web
    server's thread, and the `claude` it started survives a restart of that
    server (its own session, by design). After a restart the lock is free while
    the turn is still working, so the lock alone would report it dead. The
    process says so for as long as it exists. `--input-format` marks the
    headless path; the tester runs `claude -p` too, without it and without the
    thread in its environment.
    """
    for d in glob.glob('/proc/[0-9]*'):
        pid = d.rsplit('/', 1)[-1]
        argv = _cmdline(pid)
        if not argv or b'claude' not in os.path.basename(argv[0]):
            continue
        if b'--input-format' not in argv:
            continue
        if _proc_env(pid).get('ZIPPER_DISCORD_THREAD') == str(thread_id):
            return True
    return False


def _short(text):
    text = ' '.join(str(text or '').split())
    return text if len(text) <= DOING_MAX else text[:DOING_MAX - 1].rstrip() + '…'


def describe(block):
    """One tool call as a status line: "running zipper lint", "editing review.py"."""
    name = block.get('name') or ''
    inp = block.get('input') or {}
    if name == 'Bash':
        return 'running ' + _short(inp.get('description') or inp.get('command'))
    if name in ('Edit', 'Write', 'NotebookEdit'):
        return 'editing ' + os.path.basename(inp.get('file_path') or inp.get('notebook_path') or '')
    if name == 'Read':
        return 'reading ' + os.path.basename(inp.get('file_path') or '')
    if name == 'Agent':
        return 'running an agent: ' + _short(inp.get('description'))
    return 'using ' + _short(name)


def doing(thread_id, limit=400_000):
    """What the turn is doing now, from the tail of its transcript.

    The last tool call while it is still waiting on its result; "thinking" once
    the result is in and nothing new has started. '' when the transcript cannot
    be read -- the status then just says it is working, which is still true.
    """
    sid = (load().get(str(thread_id)) or {}).get('session_id') or ''
    path = convhead.transcript(sid) if sid else ''
    if not path:
        return ''
    try:
        with open(path, 'rb') as fh:
            if os.fstat(fh.fileno()).st_size > limit:
                fh.seek(-limit, os.SEEK_END)
                fh.readline()
            rows = fh.read().splitlines()
    except OSError:
        return ''
    for raw in reversed(rows):
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        content = (row.get('message') or {}).get('content')
        if not isinstance(content, list):
            if row.get('type') == 'user':
                return 'thinking'
            continue
        kinds = [b.get('type') for b in content if isinstance(b, dict)]
        if row.get('type') == 'assistant':
            calls = [b for b in content if isinstance(b, dict) and b.get('type') == 'tool_use']
            if calls:
                return describe(calls[-1])
            if 'text' in kinds:
                return 'writing'
            if 'thinking' in kinds:
                return 'thinking'
        elif row.get('type') == 'user':
            return 'thinking'
    return ''


def _review_job(thread_id):
    """The newest review job asked for from this thread, or None."""
    from . import review
    best = None
    for p in glob.glob(os.path.join(review.DIR, '*.json')):
        try:
            with open(p, encoding='utf-8') as fh:
                job = json.load(fh)
        except (OSError, ValueError):
            continue
        if str(job.get('thread') or '') != str(thread_id):
            continue
        if best is None or job.get('submitted', 0) > best.get('submitted', 0):
            best = job
    return best


def _review_process(slug):
    want = ('/%s.json' % slug).encode()
    for d in glob.glob('/proc/[0-9]*'):
        argv = _cmdline(d.rsplit('/', 1)[-1])
        if b'_review' in argv and any(a.endswith(want) for a in argv):
            return True
    return False


REVIEW_DOING = {'queued': 'starting', 'reviewing': 'checks, then the tester',
                'merging': 'merging and restarting'}


def activity(thread_id, slot='turn'):
    """(running, doing) for one status message."""
    if slot == 'review':
        job = _review_job(thread_id)
        if not job or job.get('state') not in REVIEW_DOING:
            return False, ''
        return _review_process(job['slug']), REVIEW_DOING[job['state']]
    running = convhead.turn_running(thread_id) or _turn_process(thread_id)
    return running, (doing(thread_id) if running else '')
