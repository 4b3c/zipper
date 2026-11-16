"""Which zipper owns which server, and the token each one presents.

One JSON file, `GATEWAY_STATE`. Tokens are stored as SHA-256 only: the file says
who a token belongs to, never what it is, so reading it gives nobody a way in.
Re-read whenever it changes on disk, so `python3 -m gateway add` takes effect on
the running relay without a restart. Written whole to a temporary file and renamed,
so a process killed mid-write leaves the old registry, never half of one. Every
change holds an flock on a lock file beside it, so two processes adding zippers at
once can't each write over the other's entry.
"""
import contextlib, datetime, fcntl, hashlib, json, os, re, secrets, threading

PATH = os.environ.get('GATEWAY_STATE', '/zipper/gateway/registry.json')
ZID = re.compile(r'^[a-z0-9][a-z0-9-]{0,31}$')
SNOWFLAKE = re.compile(r'^\d{5,25}$')


def _hash(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


class Registry:
    def __init__(self, path=PATH):
        self.path = path
        self._lock = threading.Lock()
        self._mtime = None
        self._data = {'zippers': {}}

    def _load(self):
        mtime = self.version()
        if mtime is None:
            self._mtime, self._data = None, {'zippers': {}}
            return
        if mtime != self._mtime:
            with open(self.path, encoding='utf-8') as fh:
                self._data = json.load(fh)
            self._data.setdefault('zippers', {})
            self._mtime = mtime

    @contextlib.contextmanager
    def _changing(self):
        """Read-modify-write under both locks: the thread lock for this process, the
        flock for every other one (the CLI beside the running relay)."""
        os.makedirs(os.path.dirname(self.path) or '.', exist_ok=True)
        with self._lock, open(self.path + '.lock', 'a') as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            self._mtime = None          # always re-read under the lock
            self._load()
            yield

    def version(self):
        """Changes whenever the file does (None while there is none)."""
        try:
            st = os.stat(self.path)
            return (st.st_mtime_ns, st.st_ino, st.st_size)
        except FileNotFoundError:
            return None

    def _save(self):
        os.makedirs(os.path.dirname(self.path) or '.', exist_ok=True)
        tmp = '%s.%d.tmp' % (self.path, os.getpid())
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as fh:
            json.dump(self._data, fh, indent=2)
        os.replace(tmp, self.path)
        self._mtime = self.version()

    def zippers(self):
        with self._lock:
            self._load()
            return {z: dict(v) for z, v in self._data['zippers'].items()}

    def lookup(self, token):
        """(zipper id, entry) for a presented token, or (None, None)."""
        token = (token or '').strip()
        if token.startswith('Bot '):
            token = token[4:]
        if not token:
            return None, None
        h = _hash(token)
        for zid, entry in self.zippers().items():
            if secrets.compare_digest(entry.get('token_sha256', ''), h):
                return zid, entry
        return None, None

    def owner(self, guild_id):
        """The zipper id that owns a guild, or None."""
        for zid, entry in self.zippers().items():
            if entry.get('guild') == str(guild_id):
                return zid
        return None

    def add(self, zid, guild_id, channel_id=''):
        """Link a guild to a zipper and return the zipper's new token (shown once).
        Adding an id that exists re-links it and replaces its token."""
        guild_id, channel_id = str(guild_id), str(channel_id or '')
        if not ZID.match(zid):
            raise ValueError('zipper id must be lowercase letters, digits and dashes')
        if not SNOWFLAKE.match(guild_id) or (channel_id and not SNOWFLAKE.match(channel_id)):
            raise ValueError('guild and channel are Discord ids (digits)')
        token = 'zt_' + secrets.token_urlsafe(32)
        with self._changing():
            for other, entry in self._data['zippers'].items():
                if other != zid and entry.get('guild') == guild_id:
                    raise ValueError('that server already belongs to %s' % other)
            self._data['zippers'][zid] = {
                'guild': guild_id, 'channel': channel_id, 'token_sha256': _hash(token),
                'added': datetime.datetime.now().isoformat(timespec='seconds')}
            self._save()
        return token

    def remove(self, zid):
        with self._changing():
            gone = self._data['zippers'].pop(zid, None)
            if gone is not None:
                self._save()
            return gone is not None
