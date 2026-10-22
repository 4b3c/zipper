"""The scheduled pass's standing instructions from the vault.

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

from zipper import core, scheduled, runqueue, conversations   # noqa: E402

FILE = os.path.join(core.VAULT, scheduled.INSTRUCTIONS)


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


class StandingInstructions(unittest.TestCase):
    def setUp(self):
        self.prompts, self.threads = [], []
        self.saved = (runqueue.cmd_pull, scheduled._brief, scheduled._run_claude,
                      scheduled._open_thread, conversations.touch)
        runqueue.cmd_pull = lambda a: 0
        scheduled._brief = lambda: {}
        scheduled._run_claude = lambda p, sid, timeout: self.prompts.append(p) or 'NOTIFY: yes\nhi'
        scheduled._open_thread = lambda m, n, c=None: self.threads.append(m) or '1'
        conversations.touch = lambda *a, **k: None

    def tearDown(self):
        (runqueue.cmd_pull, scheduled._brief, scheduled._run_claude, scheduled._open_thread,
         conversations.touch) = self.saved
        if os.path.exists(FILE):
            os.remove(FILE)

    def run_pass(self):
        scheduled.cmd_pass(argparse.Namespace(force=False, dry_run=False, timeout=1))

    def test_a_file_runs_the_pass_even_with_an_empty_brief(self):
        os.makedirs(os.path.dirname(FILE), exist_ok=True)
        open(FILE, 'w').write('ask about hours')
        self.run_pass()
        self.assertEqual(len(self.prompts), 1)
        self.assertIn(scheduled.INSTRUCTIONS, self.prompts[0])
        self.assertEqual(self.threads, ['hi'])

    def test_no_file_and_an_empty_brief_stays_silent(self):
        self.run_pass()
        self.assertEqual(self.prompts, [])

    def test_no_file_leaves_the_prompt_alone(self):
        scheduled._brief = lambda: {'flags': [1]}
        self.run_pass()
        self.assertNotIn(scheduled.INSTRUCTIONS, self.prompts[0])

    def test_slot(self):
        import datetime
        self.assertEqual(scheduled._slot(datetime.datetime(2026, 1, 1, 21)), 'evening')
        self.assertEqual(scheduled._slot(datetime.datetime(2026, 1, 1, 9)), 'morning')


if __name__ == '__main__':
    unittest.main()
