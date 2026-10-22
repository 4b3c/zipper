"""The scheduled pass's evening hours question.

    python3 -m unittest tests.test_pass -v

Claude, Discord and the fetch are stubbed; the environment is emptied first, as in
test_units, so nothing here reaches the live vault.
"""
import argparse, os, shutil, sys, tempfile, unittest

TMP = tempfile.mkdtemp(prefix='zipper-pass-')
for k in [k for k in os.environ if k.startswith(('ZIPPER_', 'DISCORD_', 'GITHUB_', 'BOT_'))]:
    del os.environ[k]
os.environ.update(ZIPPER_SETTINGS=os.path.join(TMP, 'settings.json'),
                  ZIPPER_ENV_FILE=os.path.join(TMP, 'env'),
                  ZIPPER_VAULT=os.path.join(TMP, 'vault'))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zipper import scheduled, runqueue, conversations, settings   # noqa: E402


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


class EveningHours(unittest.TestCase):
    def setUp(self):
        self.prompts, self.threads = [], []
        self.saved = (runqueue.cmd_pull, scheduled._brief, scheduled._run_claude,
                      scheduled._open_thread, conversations.touch, scheduled._evening,
                      scheduled._hours_today, scheduled._hours_on)
        scheduled._hours_on = lambda: True
        runqueue.cmd_pull = lambda a: 0
        scheduled._brief = lambda: {}
        scheduled._run_claude = lambda p, sid, timeout: self.prompts.append(p) or 'NOTIFY: yes\nhi'
        scheduled._open_thread = lambda m, n, c=None: self.threads.append(m) or '1'
        conversations.touch = lambda *a, **k: None

    def tearDown(self):
        (runqueue.cmd_pull, scheduled._brief, scheduled._run_claude, scheduled._open_thread,
         conversations.touch, scheduled._evening, scheduled._hours_today,
         scheduled._hours_on) = self.saved
        settings.put('plugins.hours.employer', '')

    def run_pass(self, evening, logged):
        scheduled._evening = lambda now: evening
        scheduled._hours_today = lambda today: logged
        scheduled.cmd_pass(argparse.Namespace(force=False, dry_run=False, timeout=1))

    def test_evening_without_hours_asks_even_with_an_empty_brief(self):
        self.run_pass(evening=True, logged=[])
        self.assertEqual(len(self.prompts), 1)
        self.assertIn('at their hourly job', self.prompts[0])
        self.assertEqual(self.threads, ['hi'])

    def test_the_employer_comes_from_settings(self):
        settings.put('plugins.hours.employer', 'Acme Lab')
        self.run_pass(evening=True, logged=[])
        self.assertIn('did they work at Acme Lab today', self.prompts[0])

    def test_no_hours_plugin_no_question(self):
        scheduled._hours_on = lambda: False
        self.run_pass(evening=True, logged=[])
        self.assertEqual(self.prompts, [])

    def test_logged_hours_or_morning_stay_silent(self):
        self.run_pass(evening=True, logged=[{'date': 'x'}])
        self.run_pass(evening=False, logged=[])
        self.assertEqual(self.prompts, [])

    def test_morning_pass_does_not_ask(self):
        scheduled._brief = lambda: {'flags': [1]}
        self.run_pass(evening=False, logged=[])
        self.assertNotIn('hours ledger', self.prompts[0])


if __name__ == '__main__':
    unittest.main()
