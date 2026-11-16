"""The relay: Discord's REST API and gateway, as each zipper is allowed to see them.

A zipper's discord.py connects here as if this were Discord. It identifies with its
own token, gets a READY that lists only its own guild, then that guild's
GUILD_CREATE as the real gateway last described it, then every later event in that
guild. Its REST calls are checked against the same guild and forwarded with the
real token; the answer, rate-limit headers included, goes back unchanged, so
discord.py's own rate limiting keeps working.

**One queue per connection, filled synchronously.** `Hub.feed` runs in the event
loop and only ever does `put_nowait`; a connection is attached -- its READY and
GUILD_CREATE snapshot queued -- in the same synchronous step that starts it
receiving events. So an event can never land between the snapshot and the stream,
and a slow zipper can never hold up the others.

**Sessions are never resumed.** A RESUME gets INVALID_SESSION, and discord.py
identifies again and receives a fresh snapshot. The relay keeps no per-zipper
history, so after its own restart there is nothing to resume from anyway.
"""
import asyncio, copy, json, os, re, secrets, time

import aiohttp
import yarl
from aiohttp import web

UPSTREAM_API = os.environ.get('GATEWAY_UPSTREAM_API', 'https://discord.com/api').rstrip('/')
API_VERSION = 10
HEARTBEAT_MS = 41250
READY_WAIT = 30             # seconds an identify waits for the upstream to be ready
MAX_BODY = 30 * 1024 * 1024  # Discord's own upload ceiling, with room for the form
QUEUE_MAX = 5000            # a zipper this far behind is disconnected, not waited for
INTERACTION_TTL = 15 * 60   # how long Discord accepts replies to a click

# Request headers a zipper may pass on; everything else (above all Authorization)
# is the relay's to set.
PASS_UP = ('content-type', 'x-audit-log-reason', 'user-agent')
# Response headers worth handing back: the body's type and the rate-limit state.
PASS_DOWN = re.compile(r'^(content-type|retry-after|x-ratelimit-.*|via)$', re.I)

# Events whose guild is their own `id` rather than a `guild_id`.
GUILD_EVENTS = ('GUILD_CREATE', 'GUILD_UPDATE', 'GUILD_DELETE')


class Conn:
    """One zipper's websocket."""

    def __init__(self, ws, zid, entry):
        self.ws, self.zid, self.guild = ws, zid, entry['guild']
        self.token_sha256 = entry['token_sha256']   # the token it identified with
        self.queue = asyncio.Queue(maxsize=QUEUE_MAX)
        self.seq = 0
        self.writer = None

    def put(self, payload):
        try:
            self.queue.put_nowait(payload)
            return True
        except asyncio.QueueFull:
            return False

    def dispatch(self, t, d):
        self.seq += 1
        return self.put({'op': 0, 't': t, 's': self.seq, 'd': d})

    async def drain(self):
        while True:
            payload = await self.queue.get()
            if payload is None:
                return
            await self.ws.send_str(json.dumps(payload))


class Hub:
    """The upstream's view of Discord, cut per guild for each connection."""

    def __init__(self, registry, real_token):
        self.registry = registry
        self.real_token = real_token
        self.user = None
        self.application = None
        self.guilds = {}            # guild id -> its GUILD_CREATE payload, kept current
        self.channel_guild = {}     # channel or thread id -> guild id ('' for none)
        # Interactions (button clicks, form submits) by id: (guild, token, when). A
        # reply to one goes to /interactions/<id>/<token>/callback and its follow-ups
        # to /webhooks/<application>/<token>/..., paths that name no channel, so this
        # is the only way to tell whose click it was.
        self.interactions = {}
        self.conns = set()
        self.ready = asyncio.Event()
        self.session = None

    # ------------------------------------------------------------ upstream events

    def feed(self, raw):
        """One raw gateway message from the upstream client."""
        try:
            msg = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
        except ValueError:
            return
        if msg.get('op') != 0:
            return
        self.prune_if_changed()
        t, d = msg.get('t'), msg.get('d') or {}
        self._remember(t, d)
        gid = str(d.get('id') if t in GUILD_EVENTS else d.get('guild_id') or '')
        if not gid:
            return                  # DMs, the bot's own user: no zipper sees these
        for conn in list(self.conns):
            if conn.guild == gid and not conn.dispatch(t, d):
                self._drop(conn, 'fell %d events behind' % QUEUE_MAX)

    def _remember(self, t, d):
        if t == 'INTERACTION_CREATE' and d.get('guild_id') and d.get('token'):
            now = time.time()
            self.interactions = {k: v for k, v in self.interactions.items()
                                 if now - v[2] < INTERACTION_TTL}
            self.interactions[str(d['id'])] = (str(d['guild_id']), str(d['token']), now)
        elif t == 'READY':
            self.user, self.application = d.get('user'), d.get('application')
            self.ready.set()
        elif t == 'GUILD_CREATE':
            g = copy.deepcopy(d)
            self.guilds[str(g['id'])] = g
            for c in g.get('channels', []) + g.get('threads', []):
                self.channel_guild[str(c['id'])] = str(g['id'])
        elif t == 'GUILD_UPDATE':
            g = self.guilds.get(str(d.get('id')))
            if g is not None:
                g.update({k: v for k, v in d.items()
                          if k not in ('channels', 'threads', 'members')})
        elif t == 'GUILD_DELETE' and not d.get('unavailable'):
            self.guilds.pop(str(d.get('id')), None)
        elif t in ('CHANNEL_CREATE', 'CHANNEL_UPDATE', 'THREAD_CREATE', 'THREAD_UPDATE',
                   'CHANNEL_DELETE', 'THREAD_DELETE'):
            gid = str(d.get('guild_id') or '')
            self.channel_guild[str(d['id'])] = gid
            g = self.guilds.get(gid)
            if g is None:
                return
            key = 'threads' if t.startswith('THREAD') else 'channels'
            rest = [c for c in g.get(key, []) if str(c['id']) != str(d['id'])]
            archived = (d.get('thread_metadata') or {}).get('archived')
            if not t.endswith('DELETE') and not archived:
                rest.append(copy.deepcopy(d))
            g[key] = rest
        elif t in ('GUILD_ROLE_CREATE', 'GUILD_ROLE_UPDATE', 'GUILD_ROLE_DELETE'):
            g = self.guilds.get(str(d.get('guild_id')))
            if g is None:
                return
            rid = str((d.get('role') or {}).get('id') or d.get('role_id'))
            roles = [r for r in g.get('roles', []) if str(r['id']) != rid]
            if t != 'GUILD_ROLE_DELETE':
                roles.append(d['role'])
            g['roles'] = roles

    # ------------------------------------------------------------ connections

    def attach(self, conn, ws_url):
        """Queue READY and the guild snapshot, and start streaming -- one step."""
        g = self.guilds.get(conn.guild)
        conn.put({'op': 0, 't': 'READY', 's': 1, 'd': {
            'v': API_VERSION, 'user': self.user,
            'guilds': [{'id': conn.guild, 'unavailable': True}] if g else [],
            'session_id': secrets.token_hex(16), 'resume_gateway_url': ws_url,
            'application': self.application or {'id': self.user['id'], 'flags': 0},
            'private_channels': [], 'relationships': [], 'presences': [],
            'guild_join_requests': [], 'geo_ordered_rtc_regions': [], '_trace': ['zipper-gateway']}})
        conn.seq = 1
        if g:
            conn.dispatch('GUILD_CREATE', copy.deepcopy(g))
        self.conns.add(conn)

    def _drop(self, conn, why):
        self.conns.discard(conn)
        print('[gateway] %s disconnected: %s' % (conn.zid, why))
        conn.put(None)
        asyncio.ensure_future(conn.ws.close(code=4004, message=why.encode()[:120]))

    def prune(self):
        """Close every connection whose token is no longer its zipper's current one:
        unlinked, moved to another server, or re-linked with a new token. A token
        checked at identify must not outlive its entry -- re-linking is how a leaked
        token is rotated, and the event stream is what it would leak."""
        live = self.registry.zippers()
        for conn in list(self.conns):
            entry = live.get(conn.zid) or {}
            if entry.get('guild') != conn.guild or entry.get('token_sha256') != conn.token_sha256:
                self._drop(conn, 'unlinked')
        self._registry_seen = self.registry.version()

    def prune_if_changed(self):
        """Prune before delivering, whenever the registry file changed since the last
        look: one stat per event, so a rotation takes effect at the very next event
        rather than at the next timed sweep."""
        if self.registry.version() != getattr(self, '_registry_seen', None):
            self.prune()

    def detach(self, conn):
        self.conns.discard(conn)
        conn.put(None)

    # ------------------------------------------------------------ REST

    async def upstream(self, method, path, **kw):
        if self.session is None:
            self.session = aiohttp.ClientSession()
        headers = dict(kw.pop('headers', {}), Authorization='Bot %s' % self.real_token)
        url = yarl.URL('%s/v%d/%s' % (UPSTREAM_API, API_VERSION, path), encoded=True)
        return await self.session.request(method, url, headers=headers,
                                          allow_redirects=False, **kw)

    def interaction_guild(self, parts):
        """The guild of a click whose reply path this is, if its token matches."""
        if len(parts) >= 3 and parts[0] in ('interactions', 'webhooks'):
            tok = parts[2]
            for iid, (g, t, _) in self.interactions.items():
                if secrets.compare_digest(t, tok) and (parts[0] == 'webhooks' or iid == parts[1]):
                    return g
        return None

    async def guild_of(self, channel_id):
        """The guild a channel or thread is in ('' for a DM or one that doesn't exist)."""
        cid = str(channel_id)
        if cid not in self.channel_guild:
            async with await self.upstream('GET', 'channels/%s' % cid) as r:
                body = await r.json(content_type=None) if r.status == 200 else {}
            if r.status in (200, 403, 404):
                self.channel_guild[cid] = str(body.get('guild_id') or '')
            else:
                return ''           # a Discord hiccup is not an answer worth caching
        return self.channel_guild[cid]

    async def allowed(self, guild, method, parts):
        """Whether a zipper in `guild` may make this call. `parts` is the path."""
        # Who the bot is, and its application (discord.py reads both at login).
        # Read-only, and neither holds a secret.
        if parts in (['users', '@me'], ['oauth2', 'applications', '@me']):
            return method == 'GET'
        if len(parts) >= 2 and parts[0] == 'channels':
            return SNOW.match(parts[1]) is not None and await self.guild_of(parts[1]) == guild
        if len(parts) >= 2 and parts[0] == 'guilds':
            return parts[1] == guild
        # Answering a click: only one from this zipper's guild, with its own token.
        if len(parts) == 4 and parts[0] == 'interactions' and parts[3] == 'callback':
            rec = self.interactions.get(parts[1])
            return method == 'POST' and rec is not None and rec[0] == guild \
                and secrets.compare_digest(rec[1], parts[2])
        if len(parts) >= 3 and parts[0] == 'webhooks':
            app = str((self.application or {}).get('id') or (self.user or {}).get('id') or '')
            return parts[1] == app and any(
                g == guild and secrets.compare_digest(tok, parts[2])
                for g, tok, _ in self.interactions.values())
        return False


SNOW = re.compile(r'^[0-9]{5,25}$')
# A path segment as Discord's routes use them: ids, `@me`, words, interaction
# tokens, and a percent-encoded emoji. Never `.` or `..`, and no encoded slash or dot.
SEGMENT = re.compile(r'^(?!\.{1,2}$)(?!.*%(2f|2e|5c))[A-Za-z0-9@_%:.\-]+$', re.I)
PREFIX = '/api/v%d/' % API_VERSION


def split_path(raw_path):
    """The still-encoded path after the prefix, and its segments -- or (None, None)
    for anything that is not a plain Discord route. Routing uses the raw path, never
    the decoded one, so what is checked is byte for byte what is forwarded."""
    path = raw_path.split('?', 1)[0]
    if not path.startswith(PREFIX):
        return None, None
    path = path[len(PREFIX):].rstrip('/')
    parts = path.split('/')
    if not path or not all(SEGMENT.match(p) for p in parts):
        return None, None
    return path, parts


def _json(body, status=200):
    # Exactly `application/json`, as Discord sends it: discord.py parses a body as
    # JSON only on that exact header, and aiohttp's json_response adds a charset.
    return web.Response(status=status, body=json.dumps(body).encode(),
                        headers={'Content-Type': 'application/json'})


def _error(status, message, code=0):
    return _json({'message': message, 'code': code}, status)


def ws_url(request):
    scheme = 'wss' if request.scheme == 'https' else 'ws'
    return '%s://%s/ws' % (scheme, request.host)


def app(hub):
    async def rest(request):
        path, parts = split_path(request.raw_path)
        if path is None:
            return _error(400, '400: Bad Request')
        zid, entry = hub.registry.lookup(request.headers.get('Authorization'))
        if zid is None:
            # Replies to a click carry no Authorization: discord.py sends them as a
            # webhook, and the click's own token is the credential. The relay only
            # ever handed that token to the zipper whose server the click came from,
            # so holding it is the proof -- `allowed` checks it against that server.
            guild = hub.interaction_guild(parts)
            if guild is None:
                return _error(401, '401: Unauthorized')
            zid, entry = 'interaction', {'guild': guild}
        if parts[0] == 'gateway' and request.method == 'GET':
            return _json({'url': ws_url(request), 'shards': 1,
                                      'session_start_limit': {'total': 1000, 'remaining': 1000,
                                                              'reset_after': 0, 'max_concurrency': 1}})
        if not await hub.allowed(entry['guild'], request.method, parts):
            print('[gateway] %s refused: %s /%s' % (zid, request.method, path))
            return _error(403, 'Missing Access: outside this zipper\'s server', 50001)
        body = await request.read()
        up = {k: v for k, v in request.headers.items() if k.lower() in PASS_UP}
        query = request.raw_path.split('?', 1)[1] if '?' in request.raw_path else ''
        async with await hub.upstream(request.method, path + ('?' + query if query else ''),
                                      data=body or None, headers=up) as r:
            data = await r.read()
            down = {k: v for k, v in r.headers.items() if PASS_DOWN.match(k)}
            return web.Response(status=r.status, body=data, headers=down)

    async def ws(request):
        sock = web.WebSocketResponse(max_msg_size=0)
        await sock.prepare(request)
        await sock.send_str(json.dumps({'op': 10, 'd': {'heartbeat_interval': HEARTBEAT_MS}}))
        conn = None
        try:
            async for msg in sock:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                try:
                    p = json.loads(msg.data)
                except ValueError:
                    continue
                op = p.get('op')
                if op == 1:
                    reply = {'op': 11}
                    if conn:
                        conn.put(reply)
                    else:
                        await sock.send_str(json.dumps(reply))
                elif op == 2 and conn is None:
                    zid, entry = hub.registry.lookup((p.get('d') or {}).get('token'))
                    if zid is None:
                        await sock.close(code=4004, message=b'Authentication failed.')
                        break
                    try:
                        await asyncio.wait_for(hub.ready.wait(), READY_WAIT)
                    except asyncio.TimeoutError:
                        await sock.close(code=4000, message=b'gateway not connected to Discord')
                        break
                    # Again, after the wait: a token rotated while this identify waited
                    # must not attach with the entry it had before.
                    zid, entry = hub.registry.lookup((p.get('d') or {}).get('token'))
                    if zid is None:
                        await sock.close(code=4004, message=b'Authentication failed.')
                        break
                    conn = Conn(sock, zid, entry)
                    conn.writer = asyncio.ensure_future(conn.drain())
                    hub.attach(conn, ws_url(request))
                    print('[gateway] %s connected' % zid)
                elif op == 6:
                    await sock.send_str(json.dumps({'op': 9, 'd': False}))
                # Presence (3), voice (4) and member requests (8) are the shared
                # bot's, not one zipper's to set; they are dropped.
        finally:
            if conn:
                hub.detach(conn)
                print('[gateway] %s disconnected' % conn.zid)
        return sock

    a = web.Application(client_max_size=MAX_BODY)
    a.router.add_route('*', '/api/v%d/{path:.*}' % API_VERSION, rest)
    a.router.add_get('/ws', ws)
    return a
