"""zipper.supervise

`zipper run`: one process that keeps a zipper alive where there is no systemd --
inside a container, or on a laptop.

    the dashboard   python3 -m zipper.serve --daemon       always
    the bot         python3 -u -m bot.discord_bot          required: DISCORD_TOKEN
    the schedule    fetch / pass / digest / update         from the enabled plugins

Children are restarted when they exit, with a backoff so a crash loop does not
spin. The schedule runs jobs as subprocesses, one at a time: a pass that takes
twenty minutes delays the next fetch rather than racing it, because both write
the queue.

**`zipper restart` reloads the code in place.** It sends SIGHUP; the supervisor
stops its two children and re-execs itself, so the next generation imports
whatever `git pull` just brought. The tmux server and its ttyd terminals are not
children of the web process's group -- tmux daemonizes -- so a restart does not
end a live conversation, the same guarantee `KillMode=process` gives under
systemd. Where no supervisor is running (the systemd layout), `restart` falls
back to restarting the units.

**The schedule remembers what it ran** in `Inbox/schedule.json`, so a restart at
09:01 does not run the 09:00 pass twice, and one at 09:30 still runs it once. A
slot missed by more than two hours is skipped: a pass that fires at 4pm because
the box was down all morning is news about the box, not the vault.
"""
import datetime, json, os, signal, subprocess, sys, threading, time

from . import core, settings

ROOT = settings.ROOT
PIDFILE = os.path.join(ROOT, 'data', 'supervisor.pid')
GRACE = datetime.timedelta(hours=2)


def _state_path():
    return os.path.join(core.INBOX, 'schedule.json')


def _load_state():
    try:
        with open(_state_path(), encoding='utf-8') as fh:
            return json.load(fh)
    except (FileNotFoundError, ValueError):
        return {}


def _save_state(st):
    os.makedirs(core.INBOX, exist_ok=True)
    tmp = _state_path() + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(st, fh, indent=1)
    os.replace(tmp, _state_path())


def due(now, st, jobs, pulls_due=False):
    """The jobs due at `now`, given what has run. Pure, so it can be tested.

    `pulls_due`: whether any plugin's own poll timer has run out (plugins.due());
    if so, one `zipper pull --due` pulls exactly those. `jobs`: [(job, spec)] from
    the plugins -- spec 'HH:MM' (a slot, once a day) or 'every:N' (every N
    minutes; `update` rides this, and waits by itself while a conversation is live).
    """
    out = [('pull --due', 'pull')] if pulls_due else []
    for job, spec in jobs:
        if spec.startswith('every'):
            mins = int(spec.split(':', 1)[1]) if ':' in spec else 60
            last = st.get(job)
            if not last or (now - datetime.datetime.fromisoformat(last)).total_seconds() >= mins * 60:
                out.append((job, job))
            continue
        try:
            h, m = (int(x) for x in spec.split(':'))
        except ValueError:
            continue
        at = now.replace(hour=h, minute=m, second=0, microsecond=0)
        key = '%s@%s' % (job, spec)
        ran = st.get(key)
        if at <= now < at + GRACE and (not ran or datetime.datetime.fromisoformat(ran) < at):
            out.append((job, key))
    return out


class Child:
    def __init__(self, name, argv):
        self.name, self.argv, self.p = name, argv, None
        self.backoff, self.started = 1, 0

    def ensure(self, wake):
        """Start the child if it is not running. The backoff waits on `wake`, not
        a sleep: a reload or stop must interrupt it. A plain sleep held a rollback
        for the whole backoff -- the good code sat on disk while the supervisor
        waited to restart the bad one."""
        if self.p and self.p.poll() is None:
            return
        if self.p is not None:
            code = self.p.returncode
            # A child that lived a minute was healthy; one that died at once is
            # a loop, and gets slower each time up to a minute.
            self.backoff = 1 if time.time() - self.started > 60 else min(self.backoff * 2, 60)
            print('[run] %s exited (%s); restarting in %ds' % (self.name, code, self.backoff),
                  flush=True)
            if wake.wait(self.backoff):
                return
        self.started = time.time()
        self.p = subprocess.Popen(self.argv, cwd=ROOT)
        print('[run] %s started, pid %d' % (self.name, self.p.pid), flush=True)

    def stop(self):
        if self.p and self.p.poll() is None:
            self.p.terminate()
            try:
                self.p.wait(15)
            except subprocess.TimeoutExpired:
                self.p.kill()


def _children():
    py = sys.executable
    host = os.environ.get('ZIPPER_WEB_HOST', '127.0.0.1')
    web = [py, '-m', 'zipper.serve', '--daemon', '--host', host,
           '--port', os.environ.get('ZIPPER_PORT', '8800')]
    if os.environ.get('ZIPPER_NO_TERMINAL'):
        web.append('--no-terminal')     # tests, and zippers that never want a shell
    # The web process is core even without the dashboard plugin: it is where the
    # bot hands each Discord message to a Claude conversation. Without the plugin
    # it serves only that relay (zipper/web/http.py).
    kids = [Child('web', web)]
    from . import plugins
    if os.environ.get('ZIPPER_NGINX') and (plugins.is_enabled('dashboard')
                                           or plugins.is_enabled('peers')):
        # In the container, nginx fronts the dashboard (8899) and the peer port
        # (8898) -- see docker/nginx.py. With neither, nothing listens outside.
        kids.append(Child('nginx', ['nginx', '-g', 'daemon off;']))
    if core.cfg('DISCORD_TOKEN'):
        kids.append(Child('bot', [py, '-u', '-m', 'bot.discord_bot']))
    else:
        # The one thing a zipper cannot do without. Say so on every start, and keep
        # running: the container has to stay up for setup to finish.
        print('[run] zipper needs a Discord bot: DISCORD_TOKEN is not set '
              '(`zipper secret DISCORD_TOKEN`). Nothing will answer until it is.', flush=True)
    return kids


def _scheduler(stop):
    busy = threading.Lock()
    while not stop.wait(30):
        if busy.locked():
            continue
        now = datetime.datetime.now()
        st = _load_state()
        from . import plugins
        jobs = due(now, st, plugins.jobs(), pulls_due=bool(plugins.due(now)))
        if not jobs:
            continue

        def work(jobs=jobs, st=st, now=now):
            with busy:
                for job, key in jobs:
                    # Recorded before running: a job that crashes the box every
                    # time must not be retried every thirty seconds.
                    st[key] = now.isoformat(timespec='seconds')
                    _save_state(st)
                    print('[run] %s' % job, flush=True)
                    subprocess.run([sys.executable, '-m', 'zipper'] + job.split(), cwd=ROOT)
        threading.Thread(target=work, daemon=True).start()


def cmd_run(a):
    os.makedirs(os.path.dirname(PIDFILE), exist_ok=True)
    with open(PIDFILE, 'w') as fh:
        fh.write(str(os.getpid()))
    kids = _children()
    stop = threading.Event()
    reload = threading.Event()
    wake = threading.Event()            # either of the two; interrupts a backoff

    def on(ev):
        def h(*_):
            ev.set()
            wake.set()
        return h
    signal.signal(signal.SIGHUP, on(reload))
    signal.signal(signal.SIGTERM, on(stop))
    signal.signal(signal.SIGINT, on(stop))
    if not getattr(a, 'no_schedule', False):
        threading.Thread(target=_scheduler, args=(stop,), daemon=True).start()
    print('[run] %s up: %s' % (settings.zipper_id(), ', '.join(k.name for k in kids)), flush=True)
    while not stop.is_set() and not reload.is_set():
        for k in kids:
            k.ensure(wake)
        wake.wait(2)
    for k in kids:
        k.stop()
    if reload.is_set() and not stop.is_set():
        print('[run] reloading', flush=True)
        os.execv(sys.executable, [sys.executable, '-m', 'zipper', 'run']
                 + (['--no-schedule'] if getattr(a, 'no_schedule', False) else []))
    try:
        os.remove(PIDFILE)
    except OSError:
        pass
    return 0


def supervisor_pid():
    try:
        pid = int(open(PIDFILE).read().strip())
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError):
        return None


def turns_running():
    """Is any headless turn in progress? Each holds its thread's lock for the
    whole turn (zipper.convhead), including the one that may be asking."""
    import glob
    from . import convhead
    for p in glob.glob(os.path.join(core.INBOX, 'head-*.lock')):
        if convhead.turn_running(os.path.basename(p)[len('head-'):-len('.lock')]):
            return True
    return False


def when_idle(argv):
    """Run `zipper <argv>` once no turn is running, from a detached process.

    **A restart under a running turn cuts it off.** The web process reads each
    headless turn's output; restart it and the turn loses its reader mid-reply.
    Under systemd that happened to the very turn that scheduled a restart with
    a timer. The waiter polls the turn locks, and a turn's lock is held until
    its process -- Stop hook and all -- has exited, so the reply is out first.
    Detached with its own session, so it outlives the turn that started it.
    """
    subprocess.Popen([sys.executable, '-m', 'zipper', '_when_idle'] + list(argv), cwd=ROOT,
                     start_new_session=True, stdin=subprocess.DEVNULL,
                     stdout=open(os.path.join(core.INBOX, 'when-idle.log'), 'a'),
                     stderr=subprocess.STDOUT)


def cmd_when_idle(a):
    while turns_running():
        time.sleep(5)
    print('[when-idle] %s  zipper %s' % (datetime.datetime.now().isoformat(timespec='seconds'),
                                         ' '.join(a.argv)), flush=True)
    return subprocess.run([sys.executable, '-m', 'zipper'] + a.argv, cwd=ROOT).returncode


def restart(idle=False):
    """Reload this zipper's code. True if something was (or will be) restarted.
    `idle`: wait until no turn is running -- use it from inside a conversation."""
    if idle and turns_running():
        when_idle(['restart'])
        print('restart: will happen when the running turn(s) finish')
        return True
    pid = supervisor_pid()
    if pid:
        os.kill(pid, signal.SIGHUP)
        print('restart: signalled the supervisor (pid %d)' % pid)
        return True
    units = ['zipper-web', 'zipper-discord']
    r = subprocess.run(['systemctl', 'restart'] + units, capture_output=True, text=True)
    if r.returncode == 0:
        print('restart: systemctl restart %s' % ' '.join(units))
        return True
    print('restart: no supervisor and no systemd units (%s)' % r.stderr.strip())
    return False


def cmd_restart(a):
    return 0 if restart(idle=getattr(a, 'when_idle', False)) else 1
