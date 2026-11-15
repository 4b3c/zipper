"""zipper.later

`zipper later 5m "check the build"` -- wake this Discord conversation up later.

**A background shell cannot do this in a Discord conversation.** Each Discord
turn is its own `claude -p` process (`convhead`), and it exits when the turn
ends, taking its background shells with it: a `sleep 300` started for a
reminder is killed the moment the reply goes out, and the wakeup never comes. A
pane is one long-lived process, so there a background shell works and this is
not needed.

So the wait happens outside Claude. `later` records a job under
`Inbox/later/`, starts a detached runner with its own session (the idiom of
`supervise.when_idle`), and returns. The runner waits -- for a duration, or for
a shell command to finish -- then hands the conversation a message through
`conversations.deliver`, the same door a Discord message uses. That message
starts an ordinary turn, and the runner stays until that turn has finished, so
its reply is forwarded by the Stop hook like any
other, and a turn already in flight is waited for by the turn lock.

A job is one file, rewritten whole at each step, so a runner killed at any line
leaves a job that still says what it was doing.

**A job outlives its runner.** A container or host restart kills every runner
(the box restarts itself monthly). Each runner holds a lock on its job for as
long as it lives, so a free lock on a waiting job means the runner is gone, and
`revive()` -- called by the supervisor's scheduler every tick -- starts a new
one. A timed job sleeps to its recorded due time, so it fires on time, or at
once if the restart outlasted it. An `--after` job is not re-run: the command
may not be safe twice, so the conversation is woken and told the wait was cut
off and to check for itself. The lock is taken *before* a runner starts and
handed to it (`_spawn`), so a job is never unclaimed: not in the seconds a new
runner takes to boot, and not between two revivers racing for the same job.
"""
import datetime, fcntl, json, os, re, subprocess, sys, time, uuid

from . import core, settings

DIR = os.path.join(core.INBOX, 'later')
LOG = os.path.join(core.INBOX, 'later.log')
TAIL = 1500                 # characters of a command's output handed back


def parse_delay(s):
    """'90s', '5m', '1h30m', '2h' -> seconds. A bare number is minutes."""
    s = (s or '').strip().lower()
    if re.fullmatch(r'\d+', s):
        return int(s) * 60
    parts = re.findall(r'(\d+)\s*([hms])', s)
    if not parts or re.sub(r'\d+\s*[hms]', '', s).strip():
        raise ValueError('not a duration: %r (try 90s, 5m, 1h30m)' % s)
    return sum(int(n) * {'h': 3600, 'm': 60, 's': 1}[u] for n, u in parts)


def _path(jid):
    return os.path.join(DIR, jid + '.json')


def _save(job):
    os.makedirs(DIR, exist_ok=True)
    tmp = _path(job['id']) + '.tmp'
    with open(tmp, 'w') as fh:
        json.dump(job, fh, indent=1)
    os.replace(tmp, _path(job['id']))       # never a half-written job


def _load(jid):
    with open(_path(jid)) as fh:
        return json.load(fh)


def _lock_path(jid):
    return os.path.join(DIR, jid + '.lock')


def _take(jid):
    """The job's lock, held open for the runner's life, or None if a live runner
    already has it. The kernel drops it when the process dies, however it dies."""
    os.makedirs(DIR, exist_ok=True)
    fh = open(_lock_path(jid), 'a')
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fh
    except OSError:
        fh.close()
        return None


def _alive(jid, pid=None):
    """Is a runner holding this job? The lock says, for every runner since locks
    came in. A runner started before that holds none, so its pid counts too, but
    only if that process is still `_later <jid>`: after a restart the number may
    belong to anything."""
    fh = _take(jid)
    if fh is None:
        return True
    fh.close()
    return _legacy_alive(jid, pid)


def _legacy_alive(jid, pid):
    try:
        with open('/proc/%d/cmdline' % int(pid), 'rb') as c:
            argv = c.read().split(b'\0')
        return b'_later' in argv and jid.encode() in argv
    except (OSError, TypeError, ValueError):
        return False


LOCK_FD = 'ZIPPER_LATER_LOCK_FD'


def _spawn(jid, lock):
    """Start a runner that inherits `lock`, already held. The job is never
    unclaimed: taken here before the runner starts, so the seconds Python takes
    to boot can't look like a dead runner to `revive()`, and a probe can't
    steal the lock from a runner on its way up."""
    env = dict(os.environ, **{LOCK_FD: str(lock.fileno())})
    try:
        return subprocess.Popen([sys.executable, '-m', 'zipper', '_later', jid],
                                cwd=settings.ROOT, start_new_session=True,
                                stdin=subprocess.DEVNULL, stdout=open(LOG, 'a'),
                                stderr=subprocess.STDOUT, pass_fds=(lock.fileno(),),
                                env=env)
    finally:
        lock.close()            # the runner's copy keeps the lock; this one goes


def _now():
    return datetime.datetime.now().isoformat(timespec='seconds')


def schedule(thread_id, note, delay=None, after=None):
    """Record a job and start its runner. Returns the job."""
    job = {'id': uuid.uuid4().hex[:8], 'thread': str(thread_id), 'note': note,
           'delay': delay, 'after': after, 'cwd': os.getcwd(), 'set_at': _now(),
           'state': 'waiting', 'pid': None}
    if delay is not None:
        job['due'] = (datetime.datetime.now()
                      + datetime.timedelta(seconds=delay)).isoformat(timespec='seconds')
    _save(job)
    _spawn(job['id'], _take(job['id']))
    return job


def revive():
    """Start a runner for every waiting or delivering job that has none. Returns
    the ids started. A job mid-delivery when its runner died is delivered again
    and says so: the turn it started may or may not have finished, and a repeat
    is visible where a lost wakeup is not."""
    started = []
    for job in jobs():
        if job.get('state') not in ('waiting', 'delivering'):
            continue
        # Taken and kept, not probed: whoever gets it starts the runner, so two
        # revivers can't both start one, and it goes straight to the runner.
        lock = _take(job['id'])
        if lock is None:
            continue
        if _legacy_alive(job['id'], job.get('pid')):
            lock.close()
            continue
        job['revived'] = job.get('revived', 0) + 1
        job['revived_at'] = _now()
        job['cut_off'] = job['state']
        job['state'] = 'waiting'
        _save(job)
        _spawn(job['id'], lock)
        started.append(job['id'])
        print('[later] %s  %s revived (runner gone while %s)' % (_now(), job['id'], job['cut_off']),
              flush=True)
    return started


def message(job, result=None):
    """What the conversation is handed. It reads as a user message, so it says
    plainly that nobody typed it -- the operator did not just speak."""
    head = ('[zipper later] Not a new message from the operator: a wakeup this '
            'conversation set at %s.' % job['set_at'])
    lines = [head, 'Note: %s' % (job['note'] or '(none)')]
    if job.get('revived_at') and job.get('cut_off') == 'delivering':
        lines.append('A restart cut this wakeup off while it was being delivered '
                     '(revived %s), so it may already have run once: check before '
                     'repeating anything.' % job['revived_at'])
    if job.get('after') and result is None:
        lines.append('The wait on `%s` was cut off by a restart (revived %s) and was '
                     'not run again, since it may not be safe twice. Check the state '
                     'yourself, and set the wakeup again if it is still needed.'
                     % (job['after'], job.get('revived_at')))
    if result is not None:
        lines.append('Command `%s` finished with exit code %d after %ds. Output (tail):'
                     % (job['after'], result['code'], result['secs']))
        lines.append('```\n%s\n```' % (result['out'] or '(no output)'))
    lines.append('Do what the note says and reply; the reply goes to the thread.')
    return '\n'.join(lines)


def run(jid):
    """The detached runner: wait, then deliver."""
    from . import conversations
    fd = os.environ.pop(LOCK_FD, None)
    # Handed down by whoever started us (it already holds the job), or taken
    # here when the runner was started by hand.
    lock = os.fdopen(int(fd), 'a') if fd else _take(jid)
    if lock is None:
        return 0                # another runner has it
    job = _load(jid)
    if job.get('state') not in ('waiting', 'delivering'):
        return 0
    job['pid'] = os.getpid()
    _save(job)
    result = None
    if job.get('after') and job.get('revived_at'):
        pass                    # cut off by a restart: say so, don't re-run (message)
    elif job.get('after'):
        t0 = time.time()
        p = subprocess.run(job['after'], shell=True, cwd=job.get('cwd') or core.VAULT,
                           stdin=subprocess.DEVNULL, capture_output=True, text=True,
                           errors='replace')
        out = (p.stdout or '') + (p.stderr or '')
        result = {'code': p.returncode, 'secs': int(time.time() - t0),
                  'out': out.strip()[-TAIL:]}
    else:
        # Sleep to the recorded due time, not for `delay` from now, so a runner
        # restarted by hand does not push the reminder back.
        due = datetime.datetime.fromisoformat(job['due'])
        while True:
            left = (due - datetime.datetime.now()).total_seconds()
            if left <= 0:
                break
            time.sleep(min(left, 30))
    job['state'] = 'delivering'
    _save(job)
    # **Wait for the whole turn, not the echo.** `conversations.deliver` returns
    # on the echo and leaves the turn -- reading claude's output, the wait behind
    # a turn already running -- to a daemon thread, which is right in the web
    # server and fatal here: the runner exits, the thread dies, and claude
    # breaks its pipe before the Stop hook ever forwards the reply. So this
    # takes the same steps itself, with `wait='turn'`.
    from . import convhead
    text = message(job, result)
    conversations.note_delivery(job['thread'], text)
    r = convhead.deliver(job['thread'], text, wait='turn')
    if not r.get('echoed'):
        conversations.clear_delivery(job['thread'], text)
    job['state'] = 'delivered' if r.get('ok') else 'failed'
    job['done_at'] = _now()
    if not r.get('ok'):
        job['error'] = str(r.get('error') or 'delivery failed')
        _say_failed(job, r)
    _save(job)
    print('[later] %s  %s %s %s' % (_now(), jid, job['state'], job.get('error', '')), flush=True)
    return 0 if r.get('ok') else 1


def _say_failed(job, r):
    """**A failed wakeup must not vanish.** No Stop hook fires on a turn that
    never finished, so nothing else will post. A refusal -- the usage limit, most
    often -- is echoed first and is named by `_refused`, as on the Discord door;
    anything else gets one line saying the wakeup did not run."""
    from . import chat, convhead
    try:
        convhead._refused(job['thread'], r)
        if not r.get('refused'):
            chat.discord_send('[zipper later] The wakeup set at %s did not run (%s). '
                              'Note: %s' % (job['set_at'][11:16], job['error'], job['note']),
                              thread_id=job['thread'])
    except Exception as e:
        print('[later] %s  %s could not tell the thread: %r' % (_now(), job['id'], e), flush=True)


def jobs():
    out = []
    for f in sorted(os.listdir(DIR)) if os.path.isdir(DIR) else []:
        if f.endswith('.json'):
            try:
                with open(os.path.join(DIR, f)) as fh:
                    out.append(json.load(fh))
            except (OSError, ValueError):
                continue
    return out


def cmd_later(a):
    if a.list:
        rows = [j for j in jobs() if j.get('state') in ('waiting', 'delivering')]
        for j in rows:
            state = j['state'] if _alive(j['id'], j.get('pid')) else 'runner gone (revived within a minute)'
            when = ('at %s' % j['due']) if j.get('due') else ('after `%s`' % j['after'])
            print('%s  %s  %s  thread %s  %s' % (j['id'], state, when, j['thread'], j['note']))
        if not rows:
            print('later: nothing pending')
        return 0
    thread = os.environ.get('ZIPPER_DISCORD_THREAD') or ''
    if not thread or thread.startswith('local-') or os.environ.get('ZIPPER_CONVERSATION'):
        print('later: only for Discord conversations. In a pane, a background shell '
              'outlives the turn and wakes it itself.')
        return 1
    if a.after and a.delay and not a.note:
        a.note, a.delay = a.delay, None     # `--after CMD "note"`: the note is the one positional
    if bool(a.after) == bool(a.delay):
        print('later: give a delay (5m) or --after "<command>", not both')
        return 1
    if not (a.note or '').strip():
        print('later: say what to do when it wakes: zipper later 5m "check the build"')
        return 1
    try:
        delay = parse_delay(a.delay) if a.delay else None
    except ValueError as e:
        print('later: %s' % e)
        return 1
    job = schedule(thread, a.note or '', delay=delay, after=a.after)
    when = ('at %s' % job['due'][11:16]) if delay is not None else 'when the command finishes'
    print('later: job %s will wake this conversation %s' % (job['id'], when))
    return 0


def cmd_run(a):
    return run(a.job)
