"""Conversation bookkeeping: the idle warning and the close that follows it.

    python3 -m unittest tests.test_conv -v

Same sandbox as test_units: the environment is emptied before import, so the
registry written here is a temp file, never the live one.
"""
import datetime, os, shutil, sys, tempfile, unittest
from unittest import mock

TMP = tempfile.mkdtemp(prefix='zipper-conv-')
for k in [k for k in os.environ if k.startswith(('ZIPPER_', 'DISCORD_', 'GITHUB_', 'BOT_'))]:
    del os.environ[k]
os.environ.update(ZIPPER_SETTINGS=os.path.join(TMP, 'settings.json'),
                  ZIPPER_ENV_FILE=os.path.join(TMP, 'env'),
                  ZIPPER_VAULT=os.path.join(TMP, 'vault'))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zipper import convcore, convstate                             # noqa: E402


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


class Reap(unittest.TestCase):
    TID = '900000000000000001'

    def setUp(self):
        if os.path.exists(convcore.CONV_JSON):
            os.remove(convcore.CONV_JSON)
        # No tmux in the sandbox, and a headless thread has no pane anyway.
        for name in ('alive', 'stop_ttyd'):
            p = mock.patch.object(convstate, name, return_value=False)
            p.start()
            self.addCleanup(p.stop)
        self.sent = []

    def idle(self, seconds):
        stamp = datetime.datetime.now() - datetime.timedelta(seconds=seconds)
        with convcore.mutate() as d:
            d[self.TID]['last_active'] = stamp.isoformat(timespec='seconds')

    def sweep(self):
        return convstate.reap(notify=lambda tid, text: self.sent.append(tid))

    def test_warned_again_after_a_message_reopens_a_closed_thread(self):
        # A headless Discord turn: touch(active=True), never start().
        convcore.touch(self.TID, active=True)
        self.idle(convcore.IDLE_NOTICE + 1)
        self.sweep()
        self.assertEqual(len(self.sent), 1)
        self.idle(convcore.IDLE_EXPIRY + 1)
        self.assertEqual(self.sweep(), [self.TID])
        self.assertTrue(convcore.load()[self.TID]['closed'])

        convcore.touch(self.TID, active=True)      # he answers after expiry
        self.assertFalse(convcore.load()[self.TID]['closed'])
        self.idle(convcore.IDLE_NOTICE + 1)
        self.sweep()
        self.assertEqual(len(self.sent), 2, 'second idle period went unwarned')
        self.idle(convcore.IDLE_EXPIRY + 1)
        self.assertEqual(self.sweep(), [self.TID])

    def test_incidental_touch_does_not_reopen(self):
        convcore.touch(self.TID, active=True)
        convstate.close(self.TID)
        convcore.touch(self.TID, title='cached title')
        self.assertTrue(convcore.load()[self.TID]['closed'])


class Append(unittest.TestCase):
    """The idle warning edits Zipper's last message rather than notifying."""

    def run_with(self, last):
        from zipper import chat
        calls = []
        with mock.patch.object(chat, 'discord_on', return_value=True), \
             mock.patch.object(chat, 'discord_history', return_value=[last] if last else []), \
             mock.patch.object(chat, '_bot', side_effect=lambda p, b: calls.append((p, b)) or {}), \
             mock.patch.object(chat, 'discord_send', side_effect=lambda m, thread_id: calls.append(('/send', m)) or {}):
            chat.discord_append('warn', thread_id='1')
        return calls

    def test_edits_own_message(self):
        calls = self.run_with({'id': '5', 'mine': True, 'content': 'the reply'})
        self.assertEqual(calls, [('/edit', {'message_id': '5', 'thread_id': '1',
                                            'content': 'the reply\n\nwarn'})])

    def test_sends_when_they_spoke_last(self):
        calls = self.run_with({'id': '5', 'mine': False, 'content': 'hi'})
        self.assertEqual(calls, [('/send', 'warn')])

    def test_sends_when_edit_would_overflow(self):
        calls = self.run_with({'id': '5', 'mine': True, 'content': 'x' * 1999})
        self.assertEqual(calls, [('/send', 'warn')])


if __name__ == '__main__':
    unittest.main()
