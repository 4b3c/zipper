"""The bot answers only in its own server: a message from any other server the same
bot sits in, or a DM, never reaches this zipper."""
import os, sys, tempfile, types, unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('DISCORD_TOKEN', 'test')
# Sandbox before importing anything from this repo: the live environment points at
# the real vault, and zipper/testguard.py refuses it during a test run.
_TMP = tempfile.mkdtemp()
os.environ.update(ZIPPER_VAULT=os.path.join(_TMP, 'vault'),
                  ZIPPER_ENV_FILE=os.path.join(_TMP, 'env'),
                  ZIPPER_HOSTD_LOG=os.path.join(_TMP, 'hostd.log'),
                  ZIPPER_HOSTD_CONFIG=os.path.join(_TMP, 'hostd.json'))
os.environ.pop('ZIPPER_SETTINGS', None)
os.environ.pop('ZIPPER_HOST_SOCKET', None)
os.environ.setdefault('DISCORD_CHANNEL_ID', '100')

# Importing `bot` loads .env and settings into os.environ (bot/__init__.py); put the
# environment back so tests run after this one in the same process see their own.
_ENV = dict(os.environ)
import bot.client as bc                 # noqa: E402
os.environ.clear()
os.environ.update(_ENV)

HOME, OTHER = 10, 20


def msg(guild_id):
    guild = types.SimpleNamespace(id=guild_id) if guild_id else None
    return types.SimpleNamespace(guild=guild)


class HomeServerTest(unittest.TestCase):

    def test_only_messages_in_the_home_server_count(self):
        channel = types.SimpleNamespace(guild=types.SimpleNamespace(id=HOME))
        with mock.patch.object(bc.client, 'get_channel', return_value=channel):
            self.assertTrue(bc.is_home(msg(HOME)))
            self.assertFalse(bc.is_home(msg(OTHER)))
            self.assertFalse(bc.is_home(msg(None)))           # a DM

    def test_nothing_counts_before_the_home_channel_is_visible(self):
        with mock.patch.object(bc.client, 'get_channel', return_value=None):
            self.assertFalse(bc.is_home(msg(HOME)))


class LoopbackTest(unittest.TestCase):

    def test_the_bot_api_listens_on_loopback_by_default(self):
        # Checked in a clean process: importing `bot` reads the environment.
        import subprocess
        env = {'PATH': os.environ.get('PATH', ''), 'HOME': '/tmp',
               'DISCORD_TOKEN': 'x', 'DISCORD_CHANNEL_ID': '1',
               'ZIPPER_SETTINGS': '/nonexistent/settings.json',
               'ZIPPER_ENV_FILE': '/nonexistent/.env'}
        out = subprocess.run([sys.executable, '-c', 'import bot; print(bot.BOT_HOST)'],
                             cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(out.stdout.strip().splitlines()[-1], '127.0.0.1', out.stderr)


if __name__ == '__main__':
    unittest.main()
