"""Scoops were plugins until 2026-10-25: old settings files, old rows and the old command.

    python3 -m unittest tests.test_scoop_rename -v
"""
import json, os, shutil, sys, tempfile, unittest

TMP = tempfile.mkdtemp(prefix='zipper-rename-')
for k in [k for k in os.environ if k.startswith(('ZIPPER_', 'DISCORD_', 'GITHUB_', 'BOT_'))]:
    del os.environ[k]
os.environ.update(ZIPPER_SETTINGS=os.path.join(TMP, 'settings.json'),
                  ZIPPER_ENV_FILE=os.path.join(TMP, 'env'),
                  ZIPPER_VAULT=os.path.join(TMP, 'vault'))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from zipper import scoops, settings   # noqa: E402


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


def _file():
    with open(settings.PATH, encoding='utf-8') as fh:
        return json.load(fh)


class OldSettings(unittest.TestCase):
    def setUp(self):
        settings.save({'plugins': {'github': {'enabled': True, 'user': 'sam'}}})

    def test_an_old_file_is_read_as_scoops(self):
        self.assertTrue(scoops.is_enabled('github'))
        self.assertEqual(settings.get('scoops.github.user'), 'sam')

    def test_a_write_keeps_the_old_name(self):
        # Code from before the rename (a rollback, a process not yet restarted)
        # must still find its settings after a write from new code.
        settings.put('scoops.github.user', 'kim')
        f = _file()
        self.assertNotIn('scoops', f)
        self.assertEqual(f['plugins']['github'], {'enabled': True, 'user': 'kim'})
        self.assertEqual(settings.get('scoops.github.user'), 'kim')

    def test_check_names_the_rename_and_migrate_does_it(self):
        self.assertTrue(any('old name' in p for p in settings.check()))
        self.assertTrue(settings.rename_old())
        f = _file()
        self.assertNotIn('plugins', f)
        self.assertEqual(f['scoops']['github']['user'], 'sam')
        self.assertFalse(settings.rename_old())
        self.assertEqual(settings.check(), [])
        settings.put('scoops.github.user', 'kim')
        self.assertEqual(_file()['scoops']['github']['user'], 'kim')

    def test_enable_writes_where_the_file_already_is(self):
        class A:
            action, name = 'enable', 'canvas'
        scoops.cmd_scoop(A)
        self.assertTrue(_file()['plugins']['canvas']['enabled'])
        self.assertTrue(scoops.is_enabled('canvas'))


class OldRowsAndCommand(unittest.TestCase):
    def test_a_row_saved_with_the_old_field(self):
        self.assertEqual(scoops.row_scoop({'system': 'zipper', 'plugin': 'peers'}), 'peers')
        self.assertEqual(scoops.row_scoop({'system': 'zipper', 'scoop': 'upstream'}), 'upstream')

    def test_the_old_command_still_runs(self):
        import subprocess
        env = {k: v for k, v in os.environ.items()}
        for word in ('scoop', 'plugin'):
            r = subprocess.run([sys.executable, '-m', 'zipper', word, 'list'], cwd=ROOT,
                               env=env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('gmail', r.stdout)

if __name__ == '__main__':
    unittest.main()
