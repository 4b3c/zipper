"""zipper.scheduled

`zipper pass`: a whole bookkeeping pass on a timer, and a Discord thread only when
something needs the operator.

    fetch  ->  brief empty? stop, silently
           ->  a headless Claude does the middle step and commits
           ->  it answers NOTIFY: yes/no. Yes opens a thread with its message

The thread is attached to the session that did the pass, so a reply in it
continues that conversation instead of starting one that never saw the work.
`zipper-pass.timer` runs this at 09:00 and 21:00; the hourly fetch is separate.
"""
import argparse, datetime, json, os, shutil, subprocess, urllib.request, uuid

from . import core

PROMPT = """This is a scheduled bookkeeping pass, started by a timer, with nobody watching.

`zipper fetch` has just run. The brief has %(events)d open event(s), %(notes)d \
uncommitted note(s) and %(flags)d flag(s).

Do step 2 of a pass as CLAUDE.md §2 describes: read Meta/Queue.md against the vault, work \
each row into the notes it affects, then `python3 -m zipper lint`, `status`, and \
`python3 -m zipper commit "<what changed>"` from /opt/zipper. Do not pass --force: if commit \
refuses because another conversation is live, leave the tree uncommitted and say so.

Some things are his to decide, not yours (CLAUDE.md §8): a status that is a judgment about \
his life, a flag you can't resolve from the data, an event debrief, anything you would ask \
him about. Don't guess at those; list them.

Your final message is what he may receive on Discord. Its first line must be exactly \
`NOTIFY: yes` if anything needs him, or `NOTIFY: no` if the pass was routine. After that \
line, write the message: what needs him first, then one or two lines on what the pass did. \
Keep it short. Do not call `discord send`.
"""


def _brief():
    try:
        return json.load(open(os.path.join(core.INBOX, 'queue.json'), encoding='utf-8'))
    except Exception:
        return {}


def _env():
    """A pass belongs to no thread and no pane. HOME and PATH as convhead sets them."""
    env = dict(os.environ)
    env.pop('ZIPPER_CONVERSATION', None)
    env.pop('ZIPPER_DISCORD_THREAD', None)
    env['ZIPPER_VAULT'] = core.VAULT
    env.setdefault('HOME', '/root')
    env['PATH'] = '%s/.local/bin:/usr/local/bin:%s' % (env['HOME'], env.get('PATH', ''))
    return env


def _run_claude(prompt, sid, timeout):
    claude = shutil.which('claude') or os.path.expanduser('~/.local/bin/claude')
    mode = os.environ.get('ZIPPER_PERMISSION_MODE', 'auto')
    r = subprocess.run([claude, '-p', '--session-id', sid, '--permission-mode', mode,
                        '--output-format', 'json', prompt],
                       cwd=core.VAULT, env=_env(), capture_output=True, text=True,
                       timeout=timeout)
    try:
        return json.loads(r.stdout).get('result', '') or ''
    except ValueError:
        raise RuntimeError('claude gave no result (exit %d): %s'
                           % (r.returncode, (r.stderr or r.stdout)[-400:]))


def parse(result):
    """(notify, message) from the pass's final message. Anything unparseable notifies:
    a pass that went wrong is exactly what he should hear about."""
    lines = result.strip().split('\n')
    head = lines[0].strip().lower() if lines else ''
    body = '\n'.join(lines[1:]).strip()
    if head == 'notify: no':
        return False, body
    if head == 'notify: yes':
        return True, body
    return True, result.strip() or 'The scheduled pass produced no message.'


def _open_thread(message, name):
    base = core.cfg('BOT_URL') or 'http://127.0.0.1:4200'
    req = urllib.request.Request(base.rstrip('/') + '/thread', method='POST',
                                 data=json.dumps({'message': message, 'name': name}).encode(),
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=30) as resp:
        out = json.loads(resp.read().decode())
    if not out.get('ok'):
        raise RuntimeError(out.get('error') or 'bot refused to open a thread')
    return out['thread_id']


def cmd_pass(a):
    from . import runqueue, conversations
    runqueue.cmd_fetch(argparse.Namespace(days=14))
    q = _brief()
    counts = {'events': len(q.get('events', [])), 'notes': len(q.get('notes_uncommitted', [])),
              'flags': len(q.get('flags', []))}
    if not any(counts.values()) and not getattr(a, 'force', False):
        print('pass: the brief is empty -- nothing to do')
        return 0

    sid = str(uuid.uuid4())
    print('pass: %(events)d event(s), %(notes)d note(s), %(flags)d flag(s)' % counts
          + ' -- running Claude, session %s' % sid)
    notify, message = parse(_run_claude(PROMPT % counts, sid, timeout=getattr(a, 'timeout', 1800)))
    print(message)
    if not notify or getattr(a, 'dry_run', False):
        print('pass: %s' % ('dry run, nothing sent' if notify else 'routine, nothing sent'))
        return 0

    name = 'Bookkeeping · %s' % datetime.datetime.now().strftime('%a %-I%p').lower().capitalize()
    tid = _open_thread(message, name)
    # Attach the thread to the session that did the pass. `session_fixed` keeps
    # `touch` from replacing it with the id derived from the thread.
    conversations.touch(tid, title=name, session_id=sid, session_fixed=True)
    print('pass: posted to thread %s' % tid)
    return 0
