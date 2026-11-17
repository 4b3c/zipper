"""Invite codes, end to end without Docker or Discord: hostd makes and redeems them,
the gateway's signup trades one for a zipper, and `zipper init --relay` builds it."""
import asyncio, base64, json, os, subprocess, sys, tempfile, time, types, unittest
from unittest import mock

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

from scoops.host import hostd                  # noqa: E402
from gateway import registry, upstream         # noqa: E402

GUILD, CHANNEL = '111111111111111111', '111111111111111112'
TOKEN = registry.new_token()


def state(tmp, **over):
    secret = base64.b32encode(b'D' * 20).decode()
    cfg = dict(hostd.DEFAULT_CONFIG, totp_secret=secret, window_minutes=0,
               invites=os.path.join(tmp, 'invites.json'),
               provision=dict(hostd.DEFAULT_CONFIG['provision'], dir=tmp),
               zippers={'zipper-0': {'tiers': ['read', 'routine', 'advanced']},
                        'zipper-1': {'tiers': ['read', 'routine']},
                        'zipper-gateway': {'tiers': ['provision']}})
    cfg.update(over)
    return hostd.State(cfg)


def make_code(st):
    out = hostd.invite_create(st, 'zipper-0')['output']
    return out.split('Invite code: ', 1)[1].split()[0]


class HostdInviteTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.st = state(self.tmp)
        self.built = []
        patcher = mock.patch.object(hostd, 'provision',
                                    side_effect=lambda cfg, zid, ch, tok:
                                    (self.built.append((zid, ch, tok)) or True, 'ok'))
        patcher.start()
        self.addCleanup(patcher.stop)
        mock.patch.object(hostd, 'announce', return_value=True).start()
        self.addCleanup(mock.patch.stopall)

    def redeem(self, code, who='zipper-gateway', guild=GUILD, secret=TOKEN):
        return hostd.handle(self.st, who, {'verb': 'invite.redeem',
                                           'args': [code, guild, CHANNEL], 'secret': secret})

    def test_making_one_needs_the_operators_code(self):
        res = hostd.handle(self.st, 'zipper-0', {'verb': 'invite.create'})
        self.assertIn('pending', res)                       # held for approval
        self.assertFalse(os.path.exists(self.st.cfg['invites']))
        code = hostd.totp(self.st.cfg['totp_secret'])[0]
        out = hostd._approve(self.st, 'zipper-0', [str(res['pending']), code])
        self.assertTrue(out['ok'])
        self.assertIn('Invite code: ZIP-', out['output'])
        # A zipper without advanced can't ask at all.
        self.assertFalse(hostd.handle(self.st, 'zipper-1', {'verb': 'invite.create'})['ok'])

    def test_a_code_makes_one_zipper_once_and_is_stored_hashed(self):
        code = make_code(self.st)
        with open(self.st.cfg['invites']) as fh:
            self.assertNotIn(code, fh.read())
        res = self.redeem(code.lower().replace('-', ' '))   # however it gets typed
        self.assertTrue(res['ok'], res)
        self.assertEqual(res['id'], 'zipper-2')
        self.assertEqual(self.built, [('zipper-2', CHANNEL, TOKEN)])
        again = self.redeem(code, guild='222222222222222222')
        self.assertFalse(again['ok'])
        self.assertEqual(len(self.built), 1)

    def test_only_the_gateway_redeems(self):
        code = make_code(self.st)
        for who in ('zipper-0', 'zipper-1'):
            self.assertFalse(self.redeem(code, who=who)['ok'])
        self.assertEqual(self.built, [])
        self.assertTrue(self.redeem(code)['ok'])

    def test_a_failed_build_gives_the_code_back(self):
        code = make_code(self.st)
        with mock.patch.object(hostd, 'provision', return_value=(False, 'boom')):
            self.assertFalse(self.redeem(code)['ok'])
        self.assertTrue(self.redeem(code)['ok'])

    def test_wrong_codes_lock_and_bad_requests_are_refused(self):
        for _ in range(hostd.INVITE_BAD[0]):
            self.assertFalse(self.redeem('ZIP-AAAAA-AAAAA')['ok'])
        code = make_code(self.st)
        self.assertIn('too many', self.redeem(code)['error'])
        self.st.bad_invites = []
        self.assertIn('token', self.redeem(code, secret='nope')['error'])
        self.assertIn('invite.redeem', hostd.handle(self.st, 'zipper-gateway', {
            'verb': 'invite.redeem', 'args': [code, '../etc', CHANNEL], 'secret': TOKEN})['error'])
        self.assertTrue(self.redeem(code)['ok'])

    def test_one_zipper_per_server_and_a_cap(self):
        self.assertTrue(self.redeem(make_code(self.st))['ok'])
        self.assertIn('already has', self.redeem(make_code(self.st))['error'])
        self.st.cfg['provision']['max'] = 1
        self.assertIn('no room', self.redeem(make_code(self.st), guild='333333333333333333')['error'])

    def test_ids_are_ascii_digits_only(self):
        code = make_code(self.st)
        wide = '\uff11' * 18                                  # fullwidth "1"s
        self.assertIn('invite.redeem', self.redeem(code, guild=wide)['error'])
        self.assertIsNone(registry.SNOWFLAKE.match(wide))
        self.assertTrue(self.redeem(code)['ok'])

    def test_expired_codes_do_nothing(self):
        code = make_code(self.st)
        with mock.patch.object(hostd.time, 'time', return_value=time.time() + 8 * 86400):
            self.assertFalse(self.redeem(code)['ok'])


class Conn:
    def __init__(self, zid):
        self.zid = zid


def msg(content, guild=int(GUILD), mentions=()):
    sent = []

    async def send(text):
        sent.append(text)
    channel = types.SimpleNamespace(id=int(CHANNEL), send=send)
    m = types.SimpleNamespace(content=content, channel=channel, mentions=list(mentions),
                              guild=types.SimpleNamespace(id=guild),
                              author=types.SimpleNamespace(bot=False, id=5))
    return m, sent


class SignupTest(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.reg = registry.Registry(os.path.join(tempfile.mkdtemp(), 'r.json'))
        self.hub = types.SimpleNamespace(registry=self.reg, conns=set())
        self.calls = []

    def signup(self, result=None, **kw):
        def fake(code, guild, channel, token):
            self.calls.append((code, guild, channel, token))
            if result is not None:
                return result
            # The zipper "connects" as soon as it is made.
            self.hub.conns.add(Conn('zipper-2'))
            return {'ok': True, 'id': 'zipper-2'}
        return upstream.Signup(self.hub, redeem_fn=fake, ready_wait=2, poll=0.05, me=900, **kw)

    async def test_a_pasted_code_makes_and_links_a_zipper(self):
        s = self.signup()
        m, sent = msg('here it is: zip-ABCDE-23456 thanks')
        await s.handle(m)
        code, guild, channel, token = self.calls[0]
        self.assertEqual((code.upper(), guild, channel), ('ZIP-ABCDE-23456', int(GUILD), int(CHANNEL)))
        # The token the host wrote into the zipper is the one the relay now accepts.
        self.assertEqual(self.reg.lookup(token)[0], 'zipper-2')
        self.assertEqual(self.reg.owner(GUILD), 'zipper-2')
        self.assertEqual(sent, [upstream.WORKING, upstream.READY])
        # Linked now: the next message is the zipper's, not the gateway's.
        m, sent = msg('zip-ABCDE-23456')
        await s.handle(m)
        self.assertEqual((len(self.calls), sent), (1, []))

    async def test_a_refused_code_links_nothing(self):
        s = self.signup(result={'ok': False, 'error': 'that code is not valid'})
        m, sent = msg('ZIP-ABCDE-23456')
        await s.handle(m)
        self.assertIsNone(self.reg.owner(GUILD))
        self.assertIn('not valid', sent[-1])

    async def test_quiet_unless_spoken_to_and_never_in_ignored_servers(self):
        s = self.signup(ignore={int(GUILD)})
        m, sent = msg('ZIP-ABCDE-23456', mentions=[types.SimpleNamespace(id=900)])
        await s.handle(m)
        self.assertEqual((self.calls, sent), ([], []))
        s = self.signup()
        m, sent = msg('just chatting')
        await s.handle(m)
        self.assertEqual(sent, [])
        m, sent = msg('hello bot', mentions=[types.SimpleNamespace(id=900)])
        await s.handle(m)
        self.assertEqual(sent, [upstream.WELCOME])
        m, sent = msg('hello again', mentions=[types.SimpleNamespace(id=900)])
        await s.handle(m)
        self.assertEqual(sent, [])                          # once per ten minutes


class RelayInitTest(unittest.TestCase):

    def test_init_relay_builds_a_closed_zipper_with_the_token_from_the_environment(self):
        home = os.path.join(tempfile.mkdtemp(), 'zipper-7')
        env = {'PATH': os.environ.get('PATH', ''), 'HOME': '/tmp', 'TZ': 'America/Phoenix',
               'ZIPPER_SETTINGS': '/nonexistent/settings.json',
               'ZIPPER_ENV_FILE': '/nonexistent/.env', 'ZIPPER_NEW_DISCORD_TOKEN': TOKEN}
        r = subprocess.run([sys.executable, '-m', 'zipper', 'init', home, '--id', 'zipper-7',
                            '--relay', 'http://zipper-gateway:4300', '--discord-channel', CHANNEL],
                           cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        envf = os.path.join(home, 'config', '.env')
        self.assertEqual(os.stat(envf).st_mode & 0o777, 0o600)
        with open(envf) as fh:
            text = fh.read()
        self.assertIn('DISCORD_TOKEN=' + TOKEN, text)
        self.assertIn('DISCORD_RELAY=http://zipper-gateway:4300', text)
        with open(os.path.join(home, 'vault', 'settings.json')) as fh:
            s = json.load(fh)
        self.assertEqual(s['discord']['channel'], CHANNEL)
        self.assertFalse(s['scoops']['dashboard']['enabled'])
        with open(os.path.join(home, 'compose.yml')) as fh:
            compose = fh.read()
        self.assertNotIn('ports', compose)
        self.assertIn('mem_limit: 1g', compose)
        self.assertIn('TZ: America/Phoenix', compose)
        with open(os.path.join(home, 'vault', 'CLAUDE.md')) as fh:
            guide = fh.read()
        self.assertIn('only through Discord', guide)
        # The backup remote is where the container sees it, not the host path.
        url = subprocess.run(['git', '-C', os.path.join(home, 'vault'), 'remote', 'get-url',
                              'origin'], capture_output=True, text=True).stdout.strip()
        self.assertEqual(url, '/zipper/backup/vault.git')
        self.assertTrue(os.path.exists(os.path.join(home, 'backup', 'vault.git', 'HEAD')))
        self.assertNotIn('setup:gmail', guide)
        # Calendars are the one scoop a Discord-only zipper can set up: a pasted link.
        self.assertIn('<!-- setup:calendar -->', guide)
        self.assertIn('Calendar Feed', guide)
        self.assertLess(guide.index('setup:notes'), guide.index('setup:calendar'))
        self.assertLess(guide.index('setup:calendar'), guide.index('setup:finish'))
        # Without the token in the environment it refuses rather than making a mute zipper.
        env.pop('ZIPPER_NEW_DISCORD_TOKEN')
        r = subprocess.run([sys.executable, '-m', 'zipper', 'init', home + '-b', '--id', 'zipper-8',
                            '--relay', 'http://x:1', '--discord-channel', CHANNEL],
                           cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(os.path.exists(home + '-b'))


if __name__ == '__main__':
    unittest.main()
