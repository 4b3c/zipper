"""The bot answers only in its own server: a message from any other server the same
bot sits in, or a DM, never reaches this zipper."""
import os, sys, types, unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('DISCORD_TOKEN', 'test')
os.environ.setdefault('DISCORD_CHANNEL_ID', '100')

import bot.client as bc                 # noqa: E402

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


if __name__ == '__main__':
    unittest.main()
