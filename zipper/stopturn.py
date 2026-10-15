"""`stop` from Discord: end the running turn, drop what queued behind it, ask what next.

A message sent mid-turn still queues (`convhead._turn_lock`); only the bare word
`stop` interrupts. It signals the turn's `claude` and every process under it -- the
tools it is running go with it -- writes a stop marker that releases every message waiting on
the lock without running it, and asks in the thread what to do instead. The next
message finds the lock free and runs at once, prefixed with a line telling Claude
that its last turn was cut off, so it does not resume it unasked.

The marker is a file holding the stop time, not a flag that is cleared: a waiter
compares it with when it started waiting, so a message sent after the stop is never
caught by it, and nothing has to race to reset it.
"""
import glob, json, os, signal, time

from .core import INBOX
from . import turnstatus

ASK = '[Stopped] What would you like Zipper to do instead?'
NOTE = ('[The operator stopped your previous turn before it finished; anything it '
        'was doing may be half-done. Do not resume it unless this asks you to. '
        'Their message:]\n\n')


def is_stop(text):
    return text.strip().strip('.!').lower() == 'stop'


def _path(thread_id):
    return os.path.join(INBOX, 'head-%s.stop' % thread_id)


def _read(thread_id):
    try:
        return json.load(open(_path(thread_id), encoding='utf-8'))
    except Exception:
        return {}


def _write(thread_id, rec):
    os.makedirs(INBOX, exist_ok=True)
    tmp = _path(thread_id) + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(rec, fh)
    os.replace(tmp, _path(thread_id))     # a killed writer leaves the old marker whole


def stopped_since(thread_id, t0):
    """Was this thread stopped after `t0`? A queued message asks while it waits."""
    return _read(thread_id).get('at', 0) > t0


def take_note(thread_id):
    """The cut-off note, once, for the first message after a stop."""
    rec = _read(thread_id)
    if not rec or rec.get('noted'):
        return ''
    rec['noted'] = True
    _write(thread_id, rec)
    return NOTE


def pids(thread_id):
    """Every headless `claude` answering this thread. Normally one."""
    out = []
    for d in glob.glob('/proc/[0-9]*'):
        pid = d.rsplit('/', 1)[-1]
        if turnstatus._is_turn(pid, thread_id):
            out.append(int(pid))
    return out


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _children():
    """{ppid: [pid]} for the whole box, read once."""
    kids = {}
    for d in glob.glob('/proc/[0-9]*/stat'):
        try:
            raw = open(d, 'rb').read().decode('utf-8', 'replace')
            # The name in parentheses can hold spaces; the ppid follows the last ')'.
            ppid = int(raw.rsplit(')', 1)[1].split()[1])
            kids.setdefault(ppid, []).append(int(d.split('/')[2]))
        except (OSError, ValueError, IndexError):
            pass
    return kids


def tree(pid):
    """The pid and everything under it. Claude Code starts each tool in a process
    group of its own, so signalling its group misses the tools: a `sleep` outlived
    its turn that way. The tree is walked instead."""
    kids, out, todo = _children(), [], [pid]
    while todo:
        p = todo.pop()
        out.append(p)
        todo.extend(kids.get(p, []))
    return out


def stop(thread_id, grace=5.0):
    """Interrupt the thread's turn and its tools. Returns the turn pids signalled.

    The marker is written first, so a message queued behind the turn cannot slip
    into the lock in the instant between the kill and the marker. The tree is read
    before anything is signalled: once Claude exits, its tools are orphaned and
    nothing links them to the turn any more. SIGINT is Claude Code's own
    interrupt; whatever is still alive after the grace is terminated, then killed.
    """
    _write(thread_id, {'at': time.time(), 'noted': False})
    ps = pids(thread_id)
    procs = [q for p in ps for q in tree(p)]
    for sig, wait in ((signal.SIGINT, grace), (signal.SIGTERM, grace), (signal.SIGKILL, 0)):
        live = [p for p in (ps if sig == signal.SIGINT else procs) if _alive(p)]
        if not live:
            continue
        for p in live:
            try:
                os.kill(p, sig)
            except OSError:
                pass
        end = time.time() + wait
        while time.time() < end and any(_alive(p) for p in live):
            time.sleep(0.1)
    return ps
