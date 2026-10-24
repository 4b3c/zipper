"""The drift rule: an active project is raised after a month with no evidence of work."""
import datetime, os, sys, tempfile, unittest

TMP = tempfile.mkdtemp(prefix='zipper-drift-')
for k in [k for k in os.environ if k.startswith(('ZIPPER_', 'DISCORD_', 'GITHUB_', 'BOT_'))]:
    del os.environ[k]
os.environ.update(ZIPPER_SETTINGS=os.path.join(TMP, 'settings.json'),
                  ZIPPER_ENV_FILE=os.path.join(TMP, 'env'),
                  ZIPPER_VAULT=os.path.join(TMP, 'vault'))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zipper import sync   # noqa: E402


class LastWork(unittest.TestCase):
    def test_coarse_dates_read_at_their_last_day(self):
        self.assertEqual(sync.last_work({'last_touched': '2025'}), '2025-12-31')
        self.assertEqual(sync.last_work({'last_touched': '2026-02'}), '2026-02-28')
        self.assertEqual(sync.last_work({'last_touched': '2026-12'}), '2026-12-31')
        self.assertEqual(sync.last_work({'last_touched': '2026-10-05'}), '2026-10-05')

    def test_newest_evidence_wins(self):
        d = {'last_touched': '2026-08', 'last_push': '2026-09-03T10:00:00Z'}
        self.assertEqual(sync.last_work(d, '2026-09-20'), '2026-09-20')
        self.assertEqual(sync.last_work(d), '2026-09-03')

    def test_no_evidence(self):
        self.assertEqual(sync.last_work({}), '')
        self.assertIsNone(sync.ignored_for({}))
        self.assertIsNone(sync.ignored_for({'last_touched': 'soon'}))

    def test_days(self):
        old = (datetime.date.today() - datetime.timedelta(days=40)).isoformat()
        self.assertEqual(sync.ignored_for({}, old), 40)


if __name__ == '__main__':
    unittest.main()
