"""`stop` in Discord: the marker, the queue it drops, and the process tree it ends.

    python3 -m unittest tests.test_stop -v
"""
import os, shutil, subprocess, sys, tempfile, threading, time, unittest
from unittest import mock

TMP = tempfile.mkdtemp(prefix='zipper-stop-')
for k in [k for k in os.environ if k.startswith(('ZIPPER_', 'DISCORD_', 'GITHUB_', 'BOT_'))]:
    del os.environ[k]
os.environ.update(ZIPPER_SETTINGS=os.path.join(TMP, 'settings.json'),
                  ZIPPER_ENV_FILE=os.path.join(TMP, 'env'),
                  ZIPPER_VAULT=os.path.join(TMP, 'vault'))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zipper import convhead, stopturn   # noqa: E402


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


class Words(unittest.TestCase):
    def test_only_the_bare_word(self):
        for t in ('stop', 'Stop', ' STOP. ', 'stop!'):
            self.assertTrue(stopturn.is_stop(t), t)
        for t in ('stop the build', "don't stop", 'stopping', ''):
            self.assertFalse(stopturn.is_stop(t), t)


class Marker(unittest.TestCase):
    def test_note_once_and_only_after_a_stop(self):
        self.assertEqual(stopturn.take_note('t1'), '')
        with mock.patch.object(stopturn, 'pids', return_value=[]):
            stopturn.stop('t1')
        self.assertEqual(stopturn.take_note('t1'), stopturn.NOTE)
        self.assertEqual(stopturn.take_note('t1'), '')

    def test_stop_catches_only_earlier_waiters(self):
        before = time.time() - 1
        with mock.patch.object(stopturn, 'pids', return_value=[]):
            stopturn.stop('t2')
        self.assertTrue(stopturn.stopped_since('t2', before))
        self.assertFalse(stopturn.stopped_since('t2', time.time() + 1))
        self.assertFalse(stopturn.stopped_since('other', before))


class Queue(unittest.TestCase):
    def test_queued_message_is_dropped_and_the_next_runs(self):
        tid, got = 'q1', {}
        with convhead._turn_lock(tid):                 # the running turn
            def waiter():
                try:
                    with convhead._turn_lock(tid):
                        got['ran'] = True
                except RuntimeError as e:
                    got['error'] = str(e)
            t = threading.Thread(target=waiter)
            t.start()
            time.sleep(0.5)
            with mock.patch.object(stopturn, 'pids', return_value=[]):
                stopturn.stop(tid)
            t.join(3)
        self.assertNotIn('ran', got)
        self.assertIn('stopped', got['error'])
        with convhead._turn_lock(tid, timeout=1):      # sent after the stop: runs
            pass


    def test_lock_freed_right_after_the_stop_still_drops_the_waiter(self):
        # The turn lets go within one 0.25s poll of the stop -- the waiter gets the
        # lock without ever failing to take it after the marker was written.
        tid, got = 'q2', {}
        held = threading.Event()
        release = threading.Event()

        def turn():
            with convhead._turn_lock(tid):
                held.set()
                release.wait(5)
        tt = threading.Thread(target=turn)
        tt.start()
        held.wait(2)

        def waiter():
            try:
                with convhead._turn_lock(tid):
                    got['ran'] = True
            except RuntimeError as e:
                got['error'] = str(e)
        tw = threading.Thread(target=waiter)
        tw.start()
        time.sleep(0.4)
        with mock.patch.object(stopturn, 'pids', return_value=[]):
            stopturn.stop(tid)
        release.set()                                  # freed 0ms after the marker
        tt.join(3)
        tw.join(3)
        self.assertNotIn('ran', got)


class Tree(unittest.TestCase):
    def test_tools_in_their_own_session_die_too(self):
        # A stand-in turn whose child lives in its own session, the way Claude
        # Code's tools do -- signalling the turn's group would miss it.
        turn = subprocess.Popen([sys.executable, '-c',
                                 'import subprocess,time;'
                                 'subprocess.Popen(["sleep","300"],start_new_session=True);'
                                 'time.sleep(300)'], start_new_session=True)
        time.sleep(0.5)
        kids = [p for p in stopturn.tree(turn.pid) if p != turn.pid]
        self.assertEqual(len(kids), 1)
        with mock.patch.object(stopturn, 'pids', return_value=[turn.pid]):
            stopturn.stop('tree', grace=1)
        turn.wait(5)
        time.sleep(0.2)
        self.assertFalse(stopturn._alive(kids[0]) and
                         open('/proc/%d/stat' % kids[0]).read().split()[2] != 'Z')


class Refused(unittest.TestCase):
    def run_refused(self, tid, reason):
        from zipper import chat, turnstatus
        sent = []
        with mock.patch.object(turnstatus, 'api_error', return_value=reason), \
                mock.patch.object(chat, 'discord_status', side_effect=lambda *a, **k: sent.append(('status', k.get('label')))), \
                mock.patch.object(chat, 'discord_send', side_effect=lambda m, **k: sent.append(('send', m))):
            out = {}
            convhead._refused(tid, out)
        return out, sent

    def test_usage_limit_posts_a_new_message(self):
        out, sent = self.run_refused('123', "You've hit your session limit · resets 12am (America/Phoenix)")
        self.assertEqual(sent[0], ('status', 'undelivered'))
        self.assertIn('Out of Claude usage', sent[1][1])
        self.assertIn('resets 12am', sent[1][1])
        self.assertIn('refused', out)

    def test_other_refusal_says_so(self):
        _, sent = self.run_refused('123', 'API Error: overloaded')
        self.assertIn('refused this turn', sent[1][1])

    def test_a_normal_ending_posts_nothing(self):
        out, sent = self.run_refused('123', '')
        self.assertEqual((out, sent), ({}, []))

    def test_only_discord_threads(self):
        _, sent = self.run_refused('local-1-abc', 'limit')
        self.assertEqual(sent, [])


class Status(unittest.TestCase):
    def test_stopped_closing_line(self):
        # bot/__init__ reads these at import. Placeholders: nothing here logs in.
        os.environ.setdefault('DISCORD_TOKEN', 'placeholder')
        os.environ.setdefault('DISCORD_CHANNEL_ID', '1')
        from bot import status
        row = {'slot': 'turn', 'started': time.time() - 120}
        self.assertTrue(status.finished(row, status.STOPPED).startswith('⏹️ Stopped after 2m'))
        self.assertTrue(status.finished(row).startswith('✅ Took'))


if __name__ == '__main__':
    unittest.main()
