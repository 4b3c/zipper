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
starts an ordinary turn, so its reply is forwarded by the Stop hook like any
other, and a turn already in flight is waited for by the turn lock.

A job is one file, rewritten whole at each step, so a runner killed at any line
leaves a job that still says what it was doing; `--list` reads the pid to tell
a waiting job from one whose runner died (a container restart).
"""
import datetime, json, os, re, subprocess, sys, time, uuid

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


def _alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


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
    proc = subprocess.Popen([sys.executable, '-m', 'zipper', '_later', job['id']],
                            cwd=settings.ROOT, start_new_session=True,
                            stdin=subprocess.DEVNULL, stdout=open(LOG, 'a'),
                            stderr=subprocess.STDOUT)
    job['pid'] = proc.pid
    _save(job)
    return job


def message(job, result=None):
    """What the conversation is handed. It reads as a user message, so it says
    plainly that nobody typed it -- the operator did not just speak."""
    head = ('[zipper later] Not a new message from the operator: a wakeup this '
            'conversation set at %s.' % job['set_at'])
    lines = [head, 'Note: %s' % (job['note'] or '(none)')]
    if result is not None:
        lines.append('Command `%s` finished with exit code %d after %ds. Output (tail):'
                     % (job['after'], result['code'], result['secs']))
        lines.append('```\n%s\n```' % (result['out'] or '(no output)'))
    lines.append('Do what the note says and reply; the reply goes to the thread.')
    return '\n'.join(lines)


def run(jid):
    """The detached runner: wait, then deliver."""
    from . import conversations
    job = _load(jid)
    job['pid'] = os.getpid()
    _save(job)
    result = None
    if job.get('after'):
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
    r = conversations.deliver(job['thread'], message(job, result))
    job['state'] = 'delivered' if r.get('ok') else 'failed'
    job['done_at'] = _now()
    if not r.get('ok'):
        job['error'] = str(r.get('error') or 'delivery failed')
    _save(job)
    print('[later] %s  %s %s %s' % (_now(), jid, job['state'], job.get('error', '')), flush=True)
    return 0 if r.get('ok') else 1


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
            state = j['state'] if _alive(j.get('pid')) else 'lost (runner died)'
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
