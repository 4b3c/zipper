"""Secrets through a Discord form (bot/secretask.py) and the relay's scoping of the
replies (gateway/relay.py). No Discord: interactions are fakes with the same shape."""
import asyncio, json, os, sys, tempfile, types, unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
TMP = tempfile.mkdtemp(prefix='zipper-secretask-')
_ENV = dict(os.environ)
os.environ.update(DISCORD_TOKEN='x', DISCORD_CHANNEL_ID='100',
                  ZIPPER_SETTINGS=os.path.join(TMP, 'settings.json'),
                  ZIPPER_ENV_FILE=os.path.join(TMP, 'env'),
                  ZIPPER_VAULT=os.path.join(TMP, 'vault'))
import discord                                      # noqa: E402
import bot.client as bc                             # noqa: E402
from bot import secretask                           # noqa: E402
from gateway import registry, relay                 # noqa: E402
os.environ.clear()
os.environ.update(_ENV)

HOME, OTHER, OWNER = 10, 20, 7


class Response:
    def __init__(self):
        self.modal, self.sent = None, []

    async def send_modal(self, m):
        self.modal = m

    async def send_message(self, text, ephemeral=False):
        self.sent.append((text, ephemeral))


def interaction(kind, custom_id, user=OWNER, guild=HOME, value=None):
    data = {'custom_id': custom_id}
    if value is not None:
        data['components'] = [{'type': 1, 'components': [{'type': 4, 'custom_id': 'v',
                                                           'value': value}]}]
    edits = []

    async def edit(**kw):
        edits.append(kw)
    return types.SimpleNamespace(
        type=kind, data=data, user=types.SimpleNamespace(id=user),
        guild=types.SimpleNamespace(id=guild, owner_id=OWNER), response=Response(),
        message=types.SimpleNamespace(edit=edit), edits=edits)


class SecretFormTest(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.env = os.path.join(self.dir, 'env')
        for target, value in ((secretask, 'PATH'), ):
            p = mock.patch.object(target, value, os.path.join(self.dir, 'asks.json'))
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(secretask.core, 'ENV_FILE', self.env)
        p.start()
        self.addCleanup(p.stop)
        home = types.SimpleNamespace(guild=types.SimpleNamespace(id=HOME))
        p = mock.patch.object(bc.client, 'get_channel', return_value=home)
        p.start()
        self.addCleanup(p.stop)
        self.told = []

        async def tell(prompt, thread_id, **kw):
            self.told.append((prompt, thread_id))
            return True, ''
        p = mock.patch.object(bc, 'post_to_zipper', tell)
        p.start()
        self.addCleanup(p.stop)

    async def tap_and_submit(self, nonce, value, **kw):
        btn = interaction(discord.InteractionType.component, secretask.BUTTON + nonce, **kw)
        await secretask.on_interaction(btn)
        form = interaction(discord.InteractionType.modal_submit, secretask.FORM + nonce,
                           value=value, **kw)
        await secretask.on_interaction(form)
        return btn, form

    async def test_the_value_reaches_env_and_never_the_conversation(self):
        nonce = secretask.new_ask(555, 'OPENAI_API_KEY', 'OpenAI key', 'env')
        btn, form = await self.tap_and_submit(nonce, '  sk-secret-value  ')
        self.assertIsNotNone(btn.response.modal)
        self.assertEqual(btn.response.modal.custom_id, secretask.FORM + nonce)
        with open(self.env) as fh:
            self.assertIn('OPENAI_API_KEY=sk-secret-value', fh.read())
        self.assertEqual(len(self.told), 1)
        self.assertEqual(self.told[0][1], 555)
        self.assertNotIn('sk-secret-value', self.told[0][0])
        self.assertTrue(all(e for _, e in form.response.sent))       # ephemeral only
        self.assertIsNone(form.edits[0]['view'])                     # button gone
        # Once: the same button and form do nothing more.
        btn2, form2 = await self.tap_and_submit(nonce, 'other')
        self.assertIsNone(btn2.response.modal)
        with open(self.env) as fh:
            self.assertNotIn('other', fh.read())

    async def test_only_the_owner_and_only_at_home(self):
        nonce = secretask.new_ask(555, 'KEY', 'k', 'env')
        btn, _ = await self.tap_and_submit(nonce, 'v', user=99)
        self.assertIsNone(btn.response.modal)
        self.assertIn('owner', btn.response.sent[0][0])
        # Another zipper's server: not even an answer -- its own bot answers there.
        btn, form = await self.tap_and_submit(nonce, 'v', guild=OTHER)
        self.assertEqual((btn.response.sent, btn.response.modal, form.response.sent), ([], None, []))
        self.assertFalse(os.path.exists(self.env))
        self.assertIsNotNone(secretask.peek(nonce))                 # still unspent

    async def test_line_breaks_are_refused_and_the_button_still_works(self):
        nonce = secretask.new_ask(555, 'KEY', 'k', 'env')
        _, form = await self.tap_and_submit(nonce, 'abc\nEVIL=1')
        self.assertIn('line break', form.response.sent[0][0])
        self.assertFalse(os.path.exists(self.env))
        await self.tap_and_submit(nonce, 'abc')
        with open(self.env) as fh:
            self.assertEqual(fh.read().count('KEY='), 1)

    async def test_no_character_that_splits_a_line_gets_through(self):
        # Everything str.splitlines() breaks on, plus other invisibles. Each would be a
        # second .env variable the next time the file is rewritten.
        for ch in '\n\r\x00\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029\x7f\t':
            nonce = secretask.new_ask(555, 'KEY', 'k', 'env')
            _, form = await self.tap_and_submit(nonce, 'abc%sINJECTED=1' % ch)
            self.assertIn('line break', form.response.sent[0][0], repr(ch))
        self.assertFalse(os.path.exists(self.env))
        # And the writer itself refuses, whoever calls it.
        from zipper import setup
        with self.assertRaises(ValueError):
            setup.env_set('KEY', 'abc\u2028INJECTED=1', self.env)

    async def test_webcal_and_capitals_become_fetchable_links(self):
        for given, want in (('webcal://cal.example/x.ics', 'https://cal.example/x.ics'),
                            ('WEBCAL://cal.example/x.ics', 'https://cal.example/x.ics'),
                            ('HTTPS://cal.example/x.ics', 'https://cal.example/x.ics')):
            self.assertEqual(secretask.clean('calendar', given), (want, None))

    async def test_a_failed_fetch_never_quotes_the_link(self):
        def ingest(a):
            print('fetch failed: <urlopen error bad path /feed/SECRETTOKEN123>')
            return 1
        with mock.patch('zipper.ics.cmd_ingest_ics', ingest), \
                mock.patch('zipper.scoops.is_enabled', return_value=True):
            said = secretask.store({'kind': 'calendar', 'name': 'canvas', 'label': 'Canvas'},
                                   'https://canvas.example/feed/SECRETTOKEN123')
        self.assertNotIn('SECRETTOKEN', said)
        self.assertIn('could not be fetched', said)

    async def test_a_calendar_link_is_ingested_under_its_label(self):
        nonce = secretask.new_ask(555, 'canvas', 'Canvas feed', 'calendar')
        seen = {}

        def ingest(a):
            seen.update(source=a.source, label=a.label)
            print('  remembered in Inbox/calendars.json')
            print('canvas: 3 event(s), 2 upcoming -> Inbox/calendar-canvas.json')
            return 0
        with mock.patch('zipper.ics.cmd_ingest_ics', ingest), \
                mock.patch('zipper.scoops.is_enabled', return_value=True):
            _, form = await self.tap_and_submit(nonce, 'https://canvas.example/feed/abc.ics')
        self.assertEqual(seen, {'source': 'https://canvas.example/feed/abc.ics', 'label': 'canvas'})
        self.assertIn('2 upcoming', self.told[0][0])
        self.assertNotIn('canvas.example', self.told[0][0])
        nonce = secretask.new_ask(555, 'google', 'Google', 'calendar')
        _, form = await self.tap_and_submit(nonce, 'not a link')
        self.assertIn("isn't a calendar link", form.response.sent[0][0])

    def test_asks_are_checked(self):
        self.assertIsNone(secretask.check('API_KEY', 'env'))
        self.assertIsNotNone(secretask.check('bad name', 'env'))
        self.assertIsNone(secretask.check('canvas', 'calendar'))
        self.assertIsNotNone(secretask.check('../x', 'calendar'))
        self.assertIsNotNone(secretask.check('X', 'shell'))


class RelayInteractionTest(unittest.IsolatedAsyncioTestCase):

    async def test_replies_only_to_clicks_from_the_zippers_own_server(self):
        reg = registry.Registry(os.path.join(tempfile.mkdtemp(), 'r.json'))
        hub = relay.Hub(reg, 'real')
        hub.feed(json.dumps({'op': 0, 't': 'READY', 'd': {
            'user': {'id': '900'}, 'application': {'id': '900', 'flags': 0}, 'guilds': []}}))
        hub.feed(json.dumps({'op': 0, 't': 'INTERACTION_CREATE', 'd': {
            'id': '4000000001', 'token': 'tok.en_A-1', 'guild_id': '111111'}}))
        hub.feed(json.dumps({'op': 0, 't': 'INTERACTION_CREATE', 'd': {
            'id': '4000000002', 'token': 'tok_B', 'guild_id': '222222'}}))
        ok = lambda g, m, path: hub.allowed(g, m, relay.split_path('/api/v10/' + path)[1])  # noqa
        self.assertTrue(await ok('111111', 'POST', 'interactions/4000000001/tok.en_A-1/callback'))
        self.assertFalse(await ok('222222', 'POST', 'interactions/4000000001/tok.en_A-1/callback'))
        self.assertFalse(await ok('111111', 'POST', 'interactions/4000000001/tok_B/callback'))
        self.assertFalse(await ok('111111', 'POST', 'interactions/4000000002/tok_B/callback'))
        self.assertTrue(await ok('111111', 'PATCH', 'webhooks/900/tok.en_A-1/messages/@original'))
        self.assertFalse(await ok('111111', 'PATCH', 'webhooks/900/tok_B/messages/@original'))
        self.assertFalse(await ok('111111', 'GET', 'webhooks/12345/tok.en_A-1'))
        # Dots are allowed inside a token, never as a whole segment.
        self.assertIsNone(relay.split_path('/api/v10/channels/1/../x')[0])
        self.assertIsNone(relay.split_path('/api/v10/interactions/./x/callback')[0])
        # A click with no server (a DM) is never recorded.
        hub.feed(json.dumps({'op': 0, 't': 'INTERACTION_CREATE', 'd': {
            'id': '4000000003', 'token': 'tok_C'}}))
        self.assertNotIn('4000000003', hub.interactions)


if __name__ == '__main__':
    unittest.main()
