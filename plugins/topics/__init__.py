"""Topics: standing jobs for Claude that outlive any one conversation.

A topic is a folder and a timer:

    <dir>/<name>/purpose.md    the standing brief. The owner writes it; a run never edits it
    <dir>/<name>/context.md    what the last run left for the next one. Every run rewrites it
    <dir>/<name>/runs.jsonl    one line per run: when, session, outcome

On its timer (`plugins.topics.topics.<name>.every`, minutes) the schedule runs
`zipper topic run <name>`. If the topic has a `gate` -- a shell command, run in the
topic's folder -- and it exits non-zero, nothing happens: a reviewer with nothing
to review costs nothing. Otherwise a **fresh** headless Claude is started with the
purpose and the context in its prompt, does the work, and rewrites context.md
before it ends.

**Fresh every time, on purpose.** One conversation that wakes up forever grows
until it is mostly stale. Here the session is thrown away and only the condensed
context carries over, so the run is told to keep state, decisions and lessons and
to drop what is finished. If a run leaves context.md untouched, runs.jsonl says so.

The run is detached from the schedule, which runs jobs one at a time: a
thirty-minute topic must not hold up the hourly pull. A lock in the folder keeps
one run per topic. Like a pass, the run's last message starts `NOTIFY: yes|no`,
and yes opens a Discord thread attached to the session.
"""
import datetime, json, os, shutil, subprocess, sys, uuid

from zipper import core, settings

name = 'topics'
WORDS = 800


def jobs(conf):
    return [('topic run %s' % n, 'every:%d' % int(t['every']))
            for n, t in sorted((conf.get('topics') or {}).items()) if t.get('every')]


# ---------------------------------------------------------------- places

def _root():
    return settings.get('plugins.topics.dir') or os.path.join(settings.ROOT, 'data', 'topics')


def _conf(topic):
    return (settings.get('plugins.topics.topics') or {}).get(topic)


def _dir(topic):
    return os.path.join(_root(), topic)


def _read(path, default=''):
    try:
        return open(path, encoding='utf-8').read()
    except FileNotFoundError:
        return default


def _lock(topic):
    return os.path.join(_dir(topic), 'run.lock')


def _running(topic):
    """The pid of a live run, or None. A lock left by a killed run is stale."""
    try:
        pid = int(_read(_lock(topic)).split()[0])
        os.kill(pid, 0)
        return pid
    except (ValueError, IndexError, ProcessLookupError, PermissionError):
        return None


def _runs(topic, n=None):
    lines = [l for l in _read(os.path.join(_dir(topic), 'runs.jsonl')).splitlines() if l.strip()]
    out = []
    for l in lines[-n:] if n else lines:
        try:
            out.append(json.loads(l))
        except ValueError:
            pass
    return out


def _record(topic, rec):
    with open(os.path.join(_dir(topic), 'runs.jsonl'), 'a', encoding='utf-8') as fh:
        fh.write(json.dumps(rec) + '\n')


# ---------------------------------------------------------------- one run

PROMPT = """This is a scheduled run of the topic "%(name)s", started by a timer, with \
nobody watching. You are a fresh session. You remember nothing of earlier runs: what \
they knew is in the context below, and nowhere else.

## Purpose
From %(purpose_path)s, the standing brief. Do not edit that file.

%(purpose)s

## Context from the last run
From %(context_path)s, %(context_age)s.

%(context)s

## This run
Do this run's work toward the purpose. Your working directory is the topic's folder; \
keep working files there or wherever the purpose says.

Before you finish, **rewrite %(context_path)s** for the next run. It is all the next \
run will know. Keep: the current state, decisions and why they were made, what is in \
flight, what to do next, and lessons that change how the work is done. Drop: anything \
finished and no longer needed, raw output, and anything the next run can re-read from a \
file (name the file instead). Condense, never append: stay under about %(words)d words.

Your final message's first line must be exactly `NOTIFY: yes` if the operator needs to \
see something, or `NOTIFY: no`. After that line, the message itself, short. Do not call \
`discord send`.
"""


def prompt(topic, now=None):
    d = _dir(topic)
    ctx_path = os.path.join(d, 'context.md')
    ctx = _read(ctx_path).strip()
    if ctx:
        age = 'written %s' % datetime.datetime.fromtimestamp(
            os.path.getmtime(ctx_path)).isoformat(timespec='minutes')
    else:
        age, ctx = 'empty', '(No context yet: this is the first run.)'
    return PROMPT % {'name': topic, 'purpose_path': os.path.join(d, 'purpose.md'),
                     'purpose': _read(os.path.join(d, 'purpose.md')).strip() or '(empty)',
                     'context_path': ctx_path, 'context_age': age, 'context': ctx,
                     'words': int((_conf(topic) or {}).get('words') or WORDS)}


def _gate(topic, conf):
    """True when the topic should run. No gate means always."""
    cmd = conf.get('gate')
    if not cmd:
        return True
    try:
        return subprocess.run(['sh', '-c', cmd], cwd=_dir(topic), capture_output=True,
                              timeout=60).returncode == 0
    except subprocess.TimeoutExpired:
        return False


def _exec(topic):
    """The run itself, in the detached child: lock, Claude, record, unlock."""
    from zipper import scheduled
    conf = _conf(topic) or {}
    d = _dir(topic)
    sid = str(uuid.uuid4())
    start = datetime.datetime.now()
    ctx_path = os.path.join(d, 'context.md')
    before = os.path.getmtime(ctx_path) if os.path.exists(ctx_path) else None
    with open(_lock(topic), 'w') as fh:
        fh.write('%d %s\n' % (os.getpid(), sid))
    rec = {'start': start.isoformat(timespec='seconds'), 'session': sid}
    try:
        claude = shutil.which('claude') or os.path.expanduser('~/.local/bin/claude')
        mode = os.environ.get('ZIPPER_PERMISSION_MODE', 'auto')
        argv = [claude, '-p', '--session-id', sid, '--permission-mode', mode,
                '--output-format', 'json', prompt(topic)]
        if conf.get('model'):
            argv[2:2] = ['--model', conf['model']]
        r = subprocess.run(argv, cwd=d, env=scheduled._env(), capture_output=True, text=True,
                           timeout=int(conf.get('timeout') or 30) * 60)
        try:
            result = json.loads(r.stdout).get('result', '') or ''
        except ValueError:
            raise RuntimeError('claude gave no result (exit %d): %s'
                               % (r.returncode, (r.stderr or r.stdout)[-400:]))
        notify, message = scheduled.parse(result)
        rec.update(ok=True, notify=notify, message=message[:2000])
    except subprocess.TimeoutExpired:
        notify, message = True, 'Topic %s ran out of time and was stopped.' % topic
        rec.update(ok=False, notify=True, message=message)
    except Exception as e:
        notify, message = True, 'Topic %s failed: %s' % (topic, e)
        rec.update(ok=False, notify=True, message=message[:2000])
    finally:
        after = os.path.getmtime(ctx_path) if os.path.exists(ctx_path) else None
        rec.update(end=datetime.datetime.now().isoformat(timespec='seconds'),
                   context_updated=after is not None and after != before)
        _record(topic, rec)
        try:
            os.remove(_lock(topic))
        except FileNotFoundError:
            pass
    if notify and not conf.get('quiet'):
        _tell(topic, sid, message)
    return 0 if rec.get('ok') else 1


def _tell(topic, sid, message):
    from zipper import plugins
    if not plugins.is_enabled('discord'):
        print(message)
        return
    from zipper import scheduled, conversations
    title = 'Topic %s · %s' % (topic, datetime.datetime.now().strftime('%a %-I%p').lower().capitalize())
    try:
        tid = scheduled._open_thread(message, title)
        conversations.touch(tid, title=title, session_id=sid, session_fixed=True)
    except Exception as e:
        print('topic %s: could not post to Discord: %s' % (topic, e))


# ---------------------------------------------------------------- commands

def cmd_topic(a):
    return {'run': _cmd_run, 'list': _cmd_list, 'show': _cmd_show, 'add': _cmd_add,
            'rm': _cmd_rm, '_exec': lambda a: _exec(a.name)}[a.action](a)


def _known(topic):
    if _conf(topic) is None:
        print('topic: no topic %r (topics: %s)'
              % (topic, ', '.join(settings.get('plugins.topics.topics') or {}) or 'none'))
        return False
    return True


def _cmd_run(a):
    if not _known(a.name):
        return 1
    pid = _running(a.name)
    if pid:
        print('topic %s: already running (pid %d)' % (a.name, pid))
        return 0
    if not a.force and not _gate(a.name, _conf(a.name)):
        print('topic %s: gate closed, nothing to do' % a.name)
        return 0
    if a.wait:
        return _exec(a.name)
    p = subprocess.Popen([sys.executable, '-m', 'zipper', 'topic', '_exec', a.name],
                         cwd=settings.ROOT, start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print('topic %s: started (pid %d)' % (a.name, p.pid))
    return 0


def _cmd_list(a):
    topics = settings.get('plugins.topics.topics') or {}
    if not topics:
        print('no topics. `zipper topic add <name> --every <minutes>`')
    for n, t in sorted(topics.items()):
        last = (_runs(n, 1) or [{}])[0]
        state = 'running' if _running(n) else ('last %s %s' % (
            last.get('start', '')[:16], 'ok' if last.get('ok') else 'FAILED') if last else 'never run')
        print('%-16s every %-5s %s%s' % (n, '%sm' % t.get('every', '-'), state,
                                         '  gate: %s' % t['gate'] if t.get('gate') else ''))
    return 0


def _cmd_show(a):
    if not _known(a.name):
        return 1
    d = _dir(a.name)
    print('folder   %s' % d)
    print('settings %s' % json.dumps(_conf(a.name)))
    print('\n--- context.md\n%s' % (_read(os.path.join(d, 'context.md')).strip() or '(empty)'))
    print('\n--- last runs')
    for r in _runs(a.name, a.limit):
        print('%s  %s  ctx %s  %s' % (r.get('start', '')[:16], 'ok    ' if r.get('ok') else 'FAILED',
                                      'updated' if r.get('context_updated') else 'UNCHANGED',
                                      (r.get('message') or '').split('\n')[0][:100]))
    return 0


def _cmd_add(a):
    if not a.name.replace('-', '').replace('_', '').isalnum():
        print('topic: a name is letters, digits, - and _')
        return 1
    d = _dir(a.name)
    os.makedirs(d, exist_ok=True)
    purpose = os.path.join(d, 'purpose.md')
    if not os.path.exists(purpose):
        with open(purpose, 'w', encoding='utf-8') as fh:
            fh.write('# %s\n\nWhat this topic is for, what a run should do, and when to notify.\n' % a.name)
    conf = dict(_conf(a.name) or {})
    conf['every'] = a.every
    for k in ('gate', 'timeout', 'model'):
        if getattr(a, k) is not None:
            conf[k] = getattr(a, k)
    settings.put('plugins.topics.topics.%s' % a.name, conf)
    print('topic %s: every %d min, folder %s\nwrite its purpose in %s' % (a.name, a.every, d, purpose))
    return 0


def _cmd_rm(a):
    if not _known(a.name):
        return 1
    topics = dict(settings.get('plugins.topics.topics') or {})
    topics.pop(a.name, None)
    settings.put('plugins.topics.topics', topics)
    print('topic %s: removed from the schedule; its folder %s is kept' % (a.name, _dir(a.name)))
    return 0
