"""The gateway's own connection to Discord: the one place the real token is used.

A plain discord.py client. Every raw event it receives goes to the hub, which hands
it on to whichever zipper owns that guild. It acts on its own only in a server no
zipper owns yet, where a pasted code links one. A *pairing* code was printed by
`zipper discord connect` for a zipper already on this machine, and the gateway
links it itself. Any other code is an *invite*, which builds a new zipper:

    the bot joins           "Hi, I'm Zipper. Paste your invite code here..."
    they paste ZIP-...      the host trades the code for a new zipper (hostd
                            `invite.redeem`), its token minted here and written into
                            its config by the host, and that channel becomes its home
    it connects             "Your zipper is ready. Send /login ..."

Making the zipper is the host's job, not this process's: the gateway can only spend
a code the operator made, never choose what gets built (scoops/host/hostd.py).
"""
import asyncio, json, os, re, socket, time

import discord

from gateway import registry

INTENTS = discord.Intents.default()
INTENTS.message_content = True      # the same intents a zipper's bot asks for

HOST_SOCKET = os.environ.get('GATEWAY_HOST_SOCKET', '/run/zipper-host/hostd.sock')
CODE = re.compile(r'\bZIP-?[A-Z2-9]{5}-?[A-Z2-9]{5}\b', re.I)
NUDGE_EVERY = 10 * 60       # at most one "paste your code" per unlinked server this often
READY_WAIT = 15 * 60        # a first boot clones the code; give it time to connect

WELCOME = ("Hi, I'm Zipper. To set up your own zipper here, paste the invite code you "
           "were sent into this channel.")
WORKING = "Got it. Setting up your zipper -- this takes a few minutes."
READY = ("Your zipper is ready, and this channel is its home. Send `/login` here to "
         "connect your Claude account: it sends a link, and you paste back the code that "
         "link gives you.")
UNKNOWN = ("That code isn't one this gateway knows. A pairing code from `zipper discord "
           "connect` lasts a week; run it again for a new one.")
SLOW = ("Your zipper was made, but it hasn't connected yet. It may still be starting; "
        "if it stays quiet, ask whoever invited you.")


def redeem(code, guild, channel, token):
    """Ask the host for a zipper. Blocking; run it off the event loop."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(600)
    buf = b''
    try:
        s.connect(HOST_SOCKET)
        s.sendall((json.dumps({'verb': 'invite.redeem', 'args': [code, str(guild), str(channel)],
                               'secret': token}) + '\n').encode())
        while not buf.endswith(b'\n'):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    except OSError as e:
        return {'ok': False, 'error': 'the host is unreachable (%s)' % e}
    finally:
        s.close()
    try:
        return json.loads(buf.decode() or '{}')
    except ValueError:
        return {'ok': False, 'error': 'the host gave no answer'}


def writable(guild):
    """Where the bot can speak in a server: its system channel, else the first it may."""
    ch = guild.system_channel
    if ch is not None and ch.permissions_for(guild.me).send_messages:
        return ch
    return next((x for x in guild.text_channels
                 if x.permissions_for(guild.me).send_messages), None)


class Signup:
    """Codes pasted in unlinked servers. Kept apart from the client so it can be
    tested without Discord: `handle` takes anything with .guild, .channel, .author
    and .content."""

    def __init__(self, hub, redeem_fn=redeem, ready_wait=READY_WAIT, poll=5, me=None,
                 can_build=None):
        self.hub, self.redeem_fn = hub, redeem_fn
        # Invites need the host daemon. A machine without one (one person's own
        # gateway) only ever pairs, and says so rather than "the host is unreachable".
        self.can_build = can_build or (lambda: os.path.exists(HOST_SOCKET))
        self.me = me                # the bot's user id, for "was I mentioned"
        self.ready_wait, self.poll = ready_wait, poll
        self.nudged = {}        # guild id -> when it was last told to paste a code
        self.busy = set()       # guilds with a redeem in flight

    async def joined(self, guild):
        if self.hub.registry.owner(guild.id):
            return
        ch = writable(guild)
        if ch is not None:
            await ch.send(WELCOME)
            self.nudged[guild.id] = time.time()
        print('[gateway] joined unlinked server %s (%s)' % (guild.name, guild.id))

    async def handle(self, message):
        # Only servers nobody owns yet. In a linked one the message is its zipper's,
        # delivered by the relay; this client never answers there.
        if message.author.bot or message.guild is None or isinstance(message.channel, discord.Thread):
            return
        gid = message.guild.id
        if self.hub.registry.owner(gid) or gid in self.busy:
            return
        m = CODE.search(message.content or '')
        if not m:
            # Said once on joining; after that only when someone speaks to the bot,
            # so a server it merely sits in is never nagged.
            mentioned = self.me is not None and any(u.id == self.me for u in message.mentions)
            if mentioned and time.time() - self.nudged.get(gid, 0) > NUDGE_EVERY:
                self.nudged[gid] = time.time()
                await message.channel.send(WELCOME)
            return
        self.busy.add(gid)
        try:
            try:
                zid = self.hub.registry.claim(m.group(0), gid, message.channel.id)
            except ValueError as e:
                await message.channel.send("That didn't work: %s." % e)
                return
            if zid:
                print('[gateway] %s paired with server %s' % (zid, gid))
                await self.wait_for(zid, message.channel)
                return
            if not self.can_build():
                await message.channel.send(UNKNOWN)
                return
            await message.channel.send(WORKING)
            token = registry.new_token()
            res = await asyncio.to_thread(self.redeem_fn, m.group(0), gid, message.channel.id, token)
            if not res.get('ok'):
                await message.channel.send("That didn't work: %s." % res.get('error', 'unknown error'))
                return
            zid = res['id']
            self.hub.registry.add(zid, gid, message.channel.id, token=token)
            print('[gateway] %s created for server %s' % (zid, gid))
            await self.wait_for(zid, message.channel)
        finally:
            self.busy.discard(gid)

    async def wait_for(self, zid, channel):
        """Say the zipper is ready once it connects, or that it hasn't."""
        deadline = time.time() + self.ready_wait
        while time.time() < deadline:
            if any(x.zid == zid for x in self.hub.conns):
                await channel.send(READY)
                return
            await asyncio.sleep(self.poll)
        await channel.send(SLOW)


def client(hub):
    # Always online, so offline means one thing: the gateway is down. Status is one per
    # bot, shared by every server, so it can't follow one zipper's activity; each
    # thread's typing indicator says a zipper is working.
    c = discord.Client(intents=INTENTS, enable_debug_events=True,
                       status=discord.Status.online)
    signup = Signup(hub)

    @c.event
    async def on_socket_raw_receive(raw):
        hub.feed(raw)

    @c.event
    async def on_ready():
        signup.me = c.user.id
        print('[gateway] upstream ready as %s in %d server(s)' % (c.user, len(c.guilds)))

    @c.event
    async def on_guild_join(guild):
        await signup.joined(guild)

    @c.event
    async def on_message(message):
        await signup.handle(message)

    return c
