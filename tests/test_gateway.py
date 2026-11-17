"""The shared gateway, end to end: real discord.py clients as two zippers, a fake
Discord behind the relay, and the scoping between them.

Nothing here touches Discord. The upstream is an aiohttp app standing in for
discord.com's REST API, and its gateway events are fed to the hub by hand -- the
same raw JSON the upstream client would pass on.
"""
import asyncio, json, os, subprocess, sys, tempfile, unittest

import aiohttp
from aiohttp import web

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
# Sandbox before importing anything from this repo: the live environment points at
# the real vault, and zipper/testguard.py refuses it during a test run.
_TMP = tempfile.mkdtemp()
os.environ.update(ZIPPER_VAULT=os.path.join(_TMP, 'vault'),
                  ZIPPER_ENV_FILE=os.path.join(_TMP, 'env'),
                  ZIPPER_HOSTD_LOG=os.path.join(_TMP, 'hostd.log'),
                  ZIPPER_HOSTD_CONFIG=os.path.join(_TMP, 'hostd.json'))
os.environ.pop('ZIPPER_SETTINGS', None)
os.environ.pop('ZIPPER_HOST_SOCKET', None)

import discord                                       # noqa: E402
import discord.gateway                               # noqa: E402
import discord.http                                  # noqa: E402
import yarl                                          # noqa: E402

from gateway import registry, relay                  # noqa: E402

REAL = 'real-bot-token'
BOT = {'id': '900000000000000001', 'username': 'Zipper', 'discriminator': '0',
       'avatar': None, 'bot': True, 'global_name': None}
G1, C1 = '111111111111111111', '111111111111111112'      # Josh's server and channel
G2, C2 = '222222222222222222', '222222222222222223'      # someone else's


def guild(gid, cid, name):
    return {'id': gid, 'name': name, 'owner_id': '1', 'features': [], 'emojis': [],
            'stickers': [], 'member_count': 2, 'large': False, 'unavailable': False,
            'roles': [{'id': gid, 'name': '@everyone', 'permissions': '2048', 'position': 0,
                       'color': 0, 'hoist': False, 'managed': False, 'mentionable': False,
                       'flags': 0}],
            'channels': [{'id': cid, 'type': 0, 'name': 'zipper', 'position': 0,
                          'guild_id': gid, 'permission_overwrites': []}],
            'threads': [], 'members': [], 'voice_states': [], 'presences': [],
            'stage_instances': [], 'guild_scheduled_events': [],
            'premium_tier': 0, 'verification_level': 0, 'default_message_notifications': 0,
            'explicit_content_filter': 0, 'mfa_level': 0, 'nsfw_level': 0,
            'system_channel_flags': 0, 'preferred_locale': 'en-US'}


def message(mid, gid, cid, text):
    return {'id': mid, 'channel_id': cid, 'guild_id': gid, 'content': text, 'type': 0,
            'author': {'id': '5', 'username': 'josh', 'discriminator': '0', 'avatar': None},
            'timestamp': '2026-11-15T20:00:00+00:00', 'edited_timestamp': None,
            'tts': False, 'mention_everyone': False, 'mentions': [], 'mention_roles': [],
            'attachments': [], 'embeds': [], 'pinned': False, 'flags': 0}


def raw(t, d):
    return json.dumps({'op': 0, 't': t, 's': 1, 'd': d})


def jr(body, status=200, headers=None):
    """A reply as Discord sends it: `application/json`, no charset."""
    return web.Response(status=status, body=json.dumps(body).encode(),
                        headers=dict(headers or {}, **{'Content-Type': 'application/json'}))


class FakeDiscord:
    """discord.com's REST API, as much of it as the bot uses here."""

    def __init__(self):
        self.calls = []
        a = web.Application()
        a.router.add_route('*', '/api/v10/{path:.*}', self.handle)
        self.app = a

    async def handle(self, request):
        path = request.match_info['path']
        self.calls.append((request.method, path, request.headers.get('Authorization')))
        if request.headers.get('Authorization') != 'Bot ' + REAL:
            return jr({'message': '401: Unauthorized', 'code': 0}, status=401)
        if path == 'users/@me':
            return jr(BOT)
        if path == 'oauth2/applications/@me':
            return jr({'id': BOT['id'], 'name': 'Zipper', 'description': '', 'icon': None,
                       'bot_public': True, 'bot_require_code_grant': False,
                       'verify_key': 'k', 'owner': dict(BOT, bot=False, id='1'),
                       'flags': 0, 'summary': ''})
        for gid, cid in ((G1, C1), (G2, C2)):
            if path == 'channels/%s' % cid:
                return jr({'id': cid, 'type': 0, 'guild_id': gid})
            if path == 'channels/%s/messages' % cid and request.method == 'POST':
                body = await request.json()
                m = message('700000000000000001', gid, cid, body.get('content', ''))
                m['author'] = BOT
                return jr(m, headers={'X-RateLimit-Limit': '5',
                                                     'X-RateLimit-Remaining': '4',
                                                     'X-RateLimit-Bucket': 'abc',
                                                     'X-RateLimit-Reset': '1900000000',
                                                     'X-RateLimit-Reset-After': '1.0'})
        return jr({'message': 'Unknown', 'code': 10003}, status=404)


class GatewayTest(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.tmp = tempfile.mkdtemp()
        self.reg = registry.Registry(os.path.join(self.tmp, 'registry.json'))
        self.tok1 = self.reg.add('zipper-2', G1, C1)
        self.tok2 = self.reg.add('zipper-3', G2, C2)

        self.fake = FakeDiscord()
        self.fake_runner = web.AppRunner(self.fake.app)
        await self.fake_runner.setup()
        site = web.TCPSite(self.fake_runner, '127.0.0.1', 0)
        await site.start()
        fake_port = site._server.sockets[0].getsockname()[1]
        self._upstream = relay.UPSTREAM_API
        relay.UPSTREAM_API = 'http://127.0.0.1:%d/api' % fake_port

        self.hub = relay.Hub(self.reg, REAL)
        self.relay_runner = web.AppRunner(relay.app(self.hub))
        await self.relay_runner.setup()
        site = web.TCPSite(self.relay_runner, '127.0.0.1', 0)
        await site.start()
        self.url = 'http://127.0.0.1:%d' % site._server.sockets[0].getsockname()[1]

        self._base = discord.http.Route.BASE
        self._gw = discord.gateway.DiscordWebSocket.DEFAULT_GATEWAY
        discord.http.Route.BASE = self.url + '/api/v10'
        discord.gateway.DiscordWebSocket.DEFAULT_GATEWAY = yarl.URL(
            self.url.replace('http', 'ws', 1) + '/ws')

        self.hub.feed(raw('READY', {'user': BOT, 'guilds': [], 'application':
                                    {'id': BOT['id'], 'flags': 0}}))
        self.hub.feed(raw('GUILD_CREATE', guild(G1, C1, 'josh')))
        self.hub.feed(raw('GUILD_CREATE', guild(G2, C2, 'other')))
        self.clients = []

    async def asyncTearDown(self):
        for c in self.clients:
            await c.close()
        await self.relay_runner.cleanup()
        await self.fake_runner.cleanup()
        if self.hub.session:
            await self.hub.session.close()
        discord.http.Route.BASE = self._base
        discord.gateway.DiscordWebSocket.DEFAULT_GATEWAY = self._gw
        relay.UPSTREAM_API = self._upstream

    async def zipper(self, token):
        """A discord.py client, as a zipper's bot runs it, connected through the relay."""
        intents = discord.Intents.default()
        intents.message_content = True
        c = discord.Client(intents=intents)
        c.seen = []
        ready = asyncio.Event()

        @c.event
        async def on_ready():
            ready.set()

        @c.event
        async def on_message(m):
            c.seen.append(m.content)

        self.clients.append(c)
        asyncio.ensure_future(c.start(token))
        await asyncio.wait_for(ready.wait(), 15)
        return c

    async def settle(self):
        for _ in range(20):
            await asyncio.sleep(0.05)

    async def test_a_zipper_sees_only_its_own_server(self):
        c = await self.zipper(self.tok1)
        self.assertEqual([g.id for g in c.guilds], [int(G1)])
        self.assertEqual(c.user.id, int(BOT['id']))
        self.hub.feed(raw('MESSAGE_CREATE', message('800000000000000001', G1, C1, 'mine')))
        self.hub.feed(raw('MESSAGE_CREATE', message('800000000000000002', G2, C2, 'theirs')))
        dm = message('800000000000000003', G1, C1, 'a dm')
        del dm['guild_id']
        self.hub.feed(raw('MESSAGE_CREATE', dm))
        await self.settle()
        self.assertEqual(c.seen, ['mine'])

    async def test_sends_pass_with_the_real_token_and_nowhere_else(self):
        c = await self.zipper(self.tok1)
        sent = await c.get_channel(int(C1)).send('hello')
        self.assertEqual(sent.content, 'hello')
        self.assertIn(('POST', 'channels/%s/messages' % C1, 'Bot ' + REAL), self.fake.calls)
        # The other server's channel, by raw REST, as a compromised container might.
        with self.assertRaises(discord.Forbidden):
            await c.http.send_message(int(C2), params=discord.http.MultipartParameters(
                payload={'content': 'leak'}, multipart=None, files=None))
        self.assertNotIn(('POST', 'channels/%s/messages' % C2, 'Bot ' + REAL), self.fake.calls)
        # No zipper token ever reaches Discord.
        self.assertTrue(all(a == 'Bot ' + REAL for _, _, a in self.fake.calls))

    async def test_rest_refuses_bad_tokens_and_odd_paths(self):
        async with aiohttp.ClientSession() as s:
            async with s.get(self.url + '/api/v10/users/@me',
                             headers={'Authorization': 'Bot ' + REAL}) as r:
                self.assertEqual(r.status, 401)      # the real token is not a zipper's
            h = {'Authorization': 'Bot ' + self.tok1}
            for path in ('channels/%s/..%%2F..%%2Fguilds/%s' % (C1, G2),
                         'channels/%s/%%2e%%2e/x' % C1, 'webhooks/1/abc',
                         'guilds/%s/channels' % G2, 'users/@me/guilds'):
                async with s.get(self.url + '/api/v10/' + path, headers=h) as r:
                    self.assertIn(r.status, (400, 403), path)
            async with s.get(self.url + '/api/v10/gateway/bot', headers=h) as r:
                self.assertTrue((await r.json())['url'].endswith('/ws'))

    async def test_identify_with_a_bad_token_is_closed(self):
        async with aiohttp.ClientSession() as s:
            async with s.ws_connect(self.url.replace('http', 'ws', 1) + '/ws') as ws:
                hello = await ws.receive_json()
                self.assertEqual(hello['op'], 10)
                await ws.send_json({'op': 2, 'd': {'token': REAL}})
                await ws.receive()
                self.assertEqual(ws.close_code, 4004)

    async def test_unlinking_closes_a_live_connection(self):
        c = await self.zipper(self.tok1)
        self.assertEqual(len(self.hub.conns), 1)
        self.reg.remove('zipper-2')
        self.hub.prune()
        await self.settle()
        self.assertEqual(len(self.hub.conns), 0)
        self.hub.feed(raw('MESSAGE_CREATE', message('800000000000000004', G1, C1, 'after')))
        await self.settle()
        self.assertNotIn('after', c.seen)

    async def test_rotating_a_token_closes_the_socket_on_the_old_one(self):
        # Re-linking is how a leaked token is rotated, so the stream must stop at
        # once -- at the very next event, with no sweep in between.
        c = await self.zipper(self.tok1)
        self.reg.add('zipper-2', G1, C1)
        self.hub.feed(raw('MESSAGE_CREATE', message('800000000000000005', G1, C1, 'rotated')))
        await self.settle()
        self.assertNotIn('rotated', c.seen)
        self.assertEqual(len(self.hub.conns), 0)

    async def test_remove_then_add_closes_the_old_socket(self):
        c = await self.zipper(self.tok1)
        self.reg.remove('zipper-2')
        new = self.reg.add('zipper-2', G1, C1)
        self.hub.feed(raw('MESSAGE_CREATE', message('800000000000000006', G1, C1, 're-added')))
        await self.settle()
        self.assertNotIn('re-added', c.seen)
        # The new token connects and sees the server.
        c2 = await self.zipper(new)
        self.hub.feed(raw('MESSAGE_CREATE', message('800000000000000007', G1, C1, 'fresh')))
        await self.settle()
        self.assertEqual(c2.seen, ['fresh'])
        self.assertEqual([x.zid for x in self.hub.conns], ['zipper-2'])

    async def test_new_threads_are_scoped_by_the_event_that_made_them(self):
        thread = {'id': '333333333333333333', 'type': 11, 'name': 't', 'guild_id': G1,
                  'parent_id': C1, 'owner_id': BOT['id'], 'thread_metadata':
                  {'archived': False, 'auto_archive_duration': 1440,
                   'archive_timestamp': '2026-11-15T20:00:00+00:00', 'locked': False}}
        self.hub.feed(raw('THREAD_CREATE', thread))
        self.assertEqual(await self.hub.guild_of(thread['id']), G1)
        self.assertTrue(await self.hub.allowed(G1, 'POST', ['channels', thread['id'], 'messages']))
        self.assertFalse(await self.hub.allowed(G2, 'POST', ['channels', thread['id'], 'messages']))
        # A reconnecting zipper's snapshot carries the thread.
        self.assertIn(thread['id'], [t['id'] for t in self.hub.guilds[G1]['threads']])

    async def test_a_zipper_learns_its_home_once_its_code_is_claimed(self):
        code, tok = self.reg.pair('zipper-5')

        async def home(token):
            async with aiohttp.ClientSession() as s:
                async with s.get(self.url + '/zipper/home',
                                 headers={'Authorization': 'Bot ' + token}) as r:
                    return r.status, await r.json(content_type=None)
        self.assertEqual(await home(tok), (200, {'state': 'pending'}))
        self.assertEqual((await home('zt_nope'))[0], 401)
        g3, c3 = '333333333333333333', '333333333333333334'
        self.assertEqual(self.reg.claim(code, g3, c3), 'zipper-5')
        self.assertEqual(await home(tok), (200, {'state': 'linked', 'guild': g3, 'channel': c3}))
        # A pending token is good for nothing else.
        _, tok6 = self.reg.pair('zipper-6')
        async with aiohttp.ClientSession() as s:
            async with s.get(self.url + '/api/v10/channels/%s/messages' % C1,
                             headers={'Authorization': 'Bot ' + tok6}) as r:
                self.assertEqual(r.status, 401)


class RegistryTest(unittest.TestCase):

    def test_two_processes_adding_at_once_both_land(self):
        path = os.path.join(tempfile.mkdtemp(), 'r.json')
        code = ('import sys; sys.path.insert(0, %r)\n'
                'from gateway import registry\n'
                'r = registry.Registry(%r)\n'
                'for i in range(15):\n'
                '    r.add("z%%s-%%d" %% (sys.argv[1], i), sys.argv[1] + "%%05d" %% i)\n') % (ROOT, path)
        procs = [subprocess.Popen([sys.executable, '-c', code, n]) for n in ('11111', '22222', '33333')]
        for pr in procs:
            self.assertEqual(pr.wait(timeout=60), 0)
        self.assertEqual(len(registry.Registry(path).zippers()), 45)

    def test_tokens_are_stored_hashed_and_servers_have_one_owner(self):
        path = os.path.join(tempfile.mkdtemp(), 'r.json')
        reg = registry.Registry(path)
        tok = reg.add('zipper-2', G1)
        with open(path) as fh:
            self.assertNotIn(tok, fh.read())
        self.assertEqual(reg.lookup('Bot ' + tok)[0], 'zipper-2')
        self.assertEqual(reg.lookup('Bot zt_nope'), (None, None))
        with self.assertRaises(ValueError):
            reg.add('zipper-3', G1)
        with self.assertRaises(ValueError):
            reg.add('Zipper 3', G2)
        # Re-linking replaces the token: the old one stops working.
        tok2 = reg.add('zipper-2', G1)
        self.assertIsNone(reg.lookup(tok)[0])
        self.assertEqual(registry.Registry(path).lookup(tok2)[0], 'zipper-2')


if __name__ == '__main__':
    unittest.main()
