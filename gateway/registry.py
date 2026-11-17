"""Which zipper owns which server, the token each one presents, and the zippers
waiting to be paired with a server.

**Pairing** is how a zipper made on this machine finds its server: `pair` mints its
token and a code (`ZIP-XXXXX-XXXXX`, the same shape as an invite), and whoever pastes
that code in a channel of a server the bot is in links that server, with that channel
as its home (`claim`). A pending zipper's token is good for one thing only, asking
the relay whether it has been paired yet (`status`); it identifies to nothing.

One JSON file, `GATEWAY_STATE`. Tokens are stored as SHA-256 only: the file says
who a token belongs to, never what it is, so reading it gives nobody a way in.
Re-read whenever it changes on disk, so `python3 -m gateway add` takes effect on
the running relay without a restart. Written whole to a temporary file and renamed,
so a process killed mid-write leaves the old registry, never half of one. Every
change holds an flock on a lock file beside it, so two processes adding zippers at
once can't each write over the other's entry.
"""
import contextlib, datetime, fcntl, hashlib, json, os, re, secrets, threading, time

PATH = os.environ.get('GATEWAY_STATE', '/zipper/gateway/registry.json')
ZID = re.compile(r'^[a-z0-9][a-z0-9-]{0,31}$')
SNOWFLAKE = re.compile(r'^[0-9]{5,25}$')     # ASCII only: \d also takes '１'


def new_token():
    return 'zt_' + secrets.token_urlsafe(32)


# The invite alphabet: no 0/O, 1/I/L, so a code read off a screen types back right.
CODE_ALPHABET = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'
PAIR_TTL = 7 * 24 * 3600        # an unclaimed code stops working after a week


def new_code():
    pick = lambda: ''.join(secrets.choice(CODE_ALPHABET) for _ in range(5))
    return 'ZIP-%s-%s' % (pick(), pick())


def _code_key(code):
    """A pasted code, however it was typed (case, dashes), as it is stored: hashed."""
    return _hash(re.sub(r'[^A-Z0-9]', '', (code or '').upper()))


def _hash(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


class Registry:
    def __init__(self, path=PATH):
        self.path = path
        self._lock = threading.Lock()
        self._mtime = None
        self._data = {'zippers': {}, 'pending': {}}

    def _load(self):
        mtime = self.version()
        if mtime is None:
            self._mtime, self._data = None, {'zippers': {}, 'pending': {}}
            return
        if mtime != self._mtime:
            with open(self.path, encoding='utf-8') as fh:
                self._data = json.load(fh)
            self._data.setdefault('zippers', {})
            self._data.setdefault('pending', {})
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

    def add(self, zid, guild_id, channel_id='', token=None):
        """Link a guild to a zipper and return the zipper's new token (shown once).
        Adding an id that exists re-links it and replaces its token. `token`: one
        minted earlier with `new_token`, already written into that zipper's config."""
        guild_id, channel_id = str(guild_id), str(channel_id or '')
        if not ZID.match(zid):
            raise ValueError('zipper id must be lowercase letters, digits and dashes')
        if not SNOWFLAKE.match(guild_id) or (channel_id and not SNOWFLAKE.match(channel_id)):
            raise ValueError('guild and channel are Discord ids (digits)')
        token = token or new_token()
        with self._changing():
            for other, entry in self._data['zippers'].items():
                if other != zid and entry.get('guild') == guild_id:
                    raise ValueError('that server already belongs to %s' % other)
            self._data['pending'] = {k: p for k, p in self._data['pending'].items()
                                     if p['zipper'] != zid}
            self._data['zippers'][zid] = {
                'guild': guild_id, 'channel': channel_id, 'token_sha256': _hash(token),
                'added': datetime.datetime.now().isoformat(timespec='seconds')}
            self._save()
        return token

    def remove(self, zid):
        with self._changing():
            gone = self._data['zippers'].pop(zid, None)
            waiting = {k for k, p in self._data['pending'].items() if p['zipper'] == zid}
            for k in waiting:
                del self._data['pending'][k]
            if gone is not None or waiting:
                self._save()
            return gone is not None or bool(waiting)

    # ------------------------------------------------------------ pairing

    def pending(self):
        """Zipper id -> when its code was made, for the codes still good."""
        with self._lock:
            self._load()
            now = time.time()
            return {p['zipper']: p['made'] for p in self._data['pending'].values()
                    if now - p['made'] < PAIR_TTL}

    def pair(self, zid, again=False):
        """A token and a code for `zid`, which is linked once the code is pasted in a
        server (`claim`). Returns (code, token), both shown once. A zipper already
        linked to a server is refused unless `again`: pairing it again moves it, and
        its old token stops working when the new code is claimed. A second `pair`
        for the same zipper replaces its earlier code."""
        if not ZID.match(zid):
            raise ValueError('zipper id must be lowercase letters, digits and dashes')
        code, token = new_code(), new_token()
        with self._changing():
            if zid in self._data['zippers'] and not again:
                raise ValueError('%s is already linked to server %s'
                                 % (zid, self._data['zippers'][zid].get('guild')))
            self._data['pending'] = {k: p for k, p in self._data['pending'].items()
                                     if p['zipper'] != zid}
            self._data['pending'][_code_key(code)] = {
                'zipper': zid, 'token_sha256': _hash(token), 'made': time.time()}
            self._save()
        return code, token

    def claim(self, code, guild_id, channel_id):
        """Spend a pairing code pasted in `channel_id` of `guild_id`: link that server
        to the code's zipper, the channel its home. Returns the zipper id, or None for
        a code that is not a pairing code here (an invite, a typo, an old one). Raises
        ValueError if the server already belongs to another zipper."""
        guild_id, channel_id = str(guild_id), str(channel_id)
        if not SNOWFLAKE.match(guild_id) or not SNOWFLAKE.match(channel_id):
            raise ValueError('guild and channel are Discord ids (digits)')
        key = _code_key(code)
        with self._changing():
            p = self._data['pending'].get(key)
            if p is None or time.time() - p['made'] >= PAIR_TTL:
                return None
            zid = p['zipper']
            for other, entry in self._data['zippers'].items():
                if other != zid and entry.get('guild') == guild_id:
                    raise ValueError('that server already belongs to %s' % other)
            del self._data['pending'][key]
            self._data['zippers'][zid] = {
                'guild': guild_id, 'channel': channel_id, 'token_sha256': p['token_sha256'],
                'added': datetime.datetime.now().isoformat(timespec='seconds')}
            self._save()
        return zid

    def status(self, token):
        """('linked', entry), ('pending', None) or (None, None) for a presented token.
        The one question a pending zipper may ask: has my code been claimed yet."""
        zid, entry = self.lookup(token)
        if zid:
            return 'linked', entry
        token = (token or '').strip()
        if token.startswith('Bot '):
            token = token[4:]
        if token:
            h, now = _hash(token), time.time()
            with self._lock:
                self._load()
                for p in self._data['pending'].values():
                    if now - p['made'] < PAIR_TTL and secrets.compare_digest(p['token_sha256'], h):
                        return 'pending', None
        return None, None
