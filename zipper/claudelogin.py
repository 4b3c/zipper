"""`/login` from Discord: sign Claude Code back in without a terminal.

When the login lapses every turn is refused, so this cannot go through Claude.
`zipper-web` runs `claude auth login` itself under a pty, posts the sign-in link to
the thread, and the next one-word message in that thread is the code the sign-in
page shows, typed into the waiting process. Claude Code writes the credentials as it
would at a keyboard; nothing here reads or stores them.

One login at a time: the process holds the PKCE verifier its link was made with, so
a second `/login` replaces the first rather than racing it for the credentials file.
State is in this process only. A restart kills the pty and forgets the login, and the
code sent afterwards goes to Claude like any message -- `/login` again gets a new link.
"""
import json, os, re, select, subprocess, threading, time

WAIT = 600              # how long a link waits for its code
URL = re.compile(rb'https://[^\s\x07\x1b]+')
ANSI = re.compile(r'\x1b\][^\x07]*\x07|\x1b\[[0-9;?]*[A-Za-z]')
CODE = re.compile(r'^[A-Za-z0-9_\-#.]{20,}$')

_lock = threading.Lock()
_current = None         # the waiting _Login, if any


def is_login(text):
    return text.strip().lower() == '/login'


def takes(thread_id, text):
    """Is `text` the code a login in this thread is waiting for? Only a single
    code-shaped word is: anything else is a message and goes to Claude as usual."""
    cur = _current
    return bool(cur and cur.thread_id == str(thread_id) and cur.alive()
                and CODE.match(text.strip()))


def _send(thread_id, text):
    from . import chat
    chat.discord_send(text, thread_id=thread_id)


def _env():
    env = dict(os.environ)
    env.setdefault('HOME', '/root')
    env['PATH'] = '%s/.local/bin:/usr/local/bin:%s' % (env['HOME'], env.get('PATH', ''))
    env['BROWSER'] = '/bin/false'   # nobody is at this box's screen; print the link
    for k in ('ZIPPER_DISCORD_THREAD', 'ZIPPER_CONVERSATION'):
        env.pop(k, None)
    return env


class _Login:
    def __init__(self, thread_id, cmd):
        self.thread_id = str(thread_id)
        self.buf = b''
        master, slave = os.openpty()
        try:
            self.proc = subprocess.Popen(cmd, stdin=slave, stdout=slave, stderr=slave,
                                         env=_env(), start_new_session=True)
        finally:
            os.close(slave)
        self.fd = master

    def alive(self):
        return self.proc.poll() is None

    def read(self, until=None, timeout=30):
        """Read output until `until` matches, the process exits, or `timeout`."""
        end = time.time() + timeout
        while time.time() < end:
            if until is not None and until.search(self.buf):
                return True
            r, _, _ = select.select([self.fd], [], [], 0.5)
            if r:
                try:
                    chunk = os.read(self.fd, 4096)
                except OSError:          # EIO: the child closed its end
                    chunk = b''
                if not chunk:
                    self.proc.wait(timeout=5)
                    break
                self.buf += chunk
            elif not self.alive():
                break
        return until is not None and bool(until.search(self.buf))

    def text(self):
        return ANSI.sub('', self.buf.decode('utf-8', 'replace')).strip()

    def kill(self):
        try:
            if self.alive():
                os.killpg(self.proc.pid, 9)
            self.proc.wait(timeout=5)
        except Exception:
            pass
        try:
            os.close(self.fd)
        except OSError:
            pass


def _claude():
    from .convhead import _claude
    return _claude()


def start(thread_id, cmd=None):
    """Begin a login and post its link to `thread_id`. Returns the link, or None."""
    global _current
    thread_id = str(thread_id)
    try:
        login = _Login(thread_id, cmd or [_claude(), 'auth', 'login'])
    except Exception as e:
        _send(thread_id, '[Login] Could not start `claude auth login`: %s' % e)
        return None
    with _lock:
        old, _current = _current, login
    if old:
        old.kill()
        if old.thread_id != thread_id:
            _send(old.thread_id, '[Login] Replaced by a /login in another thread; '
                                 'the link above no longer works.')
    m = login.read(URL, timeout=30) and URL.search(login.buf)
    if not m:
        login.kill()
        _clear(login)
        _send(thread_id, '[Login] `claude auth login` printed no link:\n%s'
              % login.text()[-800:])
        return None
    url = m.group(0).decode()
    _send(thread_id, '[Login] Open this, sign in, then paste the code it shows here '
                     '(good for %d minutes):\n%s' % (WAIT // 60, url))
    t = threading.Timer(WAIT, _expire, (login,))
    t.daemon = True
    t.start()
    return url


def _clear(login):
    global _current
    with _lock:
        if _current is login:
            _current = None


def _expire(login):
    with _lock:
        if _current is not login:
            return
    login.kill()
    _clear(login)
    _send(login.thread_id, '[Login] The link expired. Send /login for a new one.')


def submit(thread_id, code, status_cmd=None):
    """Type `code` into the waiting login and report how it went. True if signed in."""
    login = _current
    if not login or login.thread_id != str(thread_id):
        return False
    _clear(login)
    login.buf = b''
    try:
        os.write(login.fd, code.strip().encode() + b'\r')
    except OSError:
        pass
    login.read(timeout=60)
    ok = login.proc.poll() == 0
    out = login.text()
    login.kill()
    if not ok:
        _send(thread_id, '[Login] Sign-in failed. Send /login to try again.\n%s'
              % out[-800:])
        return False
    _send(thread_id, '[Login] Signed in%s. Conversations can answer again.'
          % _who(status_cmd))
    return True


def _who(cmd=None):
    """` as <email>` from `claude auth status`, or '' if it can't say."""
    try:
        r = subprocess.run(cmd or [_claude(), 'auth', 'status'], env=_env(),
                           capture_output=True, text=True, timeout=30)
        email = json.loads(r.stdout).get('email')
        return ' as %s' % email if email else ''
    except Exception:
        return ''
