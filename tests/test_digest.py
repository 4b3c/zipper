"""The digest's daily-habit lines: pure, against a temporary metrics file.

    python3 -m unittest tests.test_digest -v
"""
import datetime, json, os, shutil, sys, tempfile, unittest

TMP = tempfile.mkdtemp(prefix='zipper-digest-')
for k in [k for k in os.environ if k.startswith(('ZIPPER_', 'DISCORD_', 'GITHUB_', 'BOT_'))]:
    del os.environ[k]
os.environ.update(ZIPPER_SETTINGS=os.path.join(TMP, 'settings.json'),
                  ZIPPER_ENV_FILE=os.path.join(TMP, 'env'),
                  ZIPPER_VAULT=os.path.join(TMP, 'vault'))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zipper import core, digest   # noqa: E402

TODAY = datetime.date(2026, 10, 2)


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


class Habits(unittest.TestCase):
    def setUp(self):
        self.csv = os.path.join(TMP, 'metrics.csv')
        self._orig = core.METCSV
        core.METCSV = self.csv
        json.dump({'plugins': {'digest': {'daily': ['leetcode']}}},
                  open(os.environ['ZIPPER_SETTINGS'], 'w'))

    def tearDown(self):
        core.METCSV = self._orig

    def write(self, *rows):
        with open(self.csv, 'w') as fh:
            fh.write('date,key,value,note,source\n')
            for d, k, v in rows:
                fh.write('%s,%s,%s,,manual\n' % (d, k, v))

    def test_off_without_settings(self):
        json.dump({}, open(os.environ['ZIPPER_SETTINGS'], 'w'))
        self.write()
        self.assertEqual(digest._habits(TODAY), [])

    def test_done_today_counts_the_streak(self):
        self.write(('2026-09-30', 'leetcode', 1), ('2026-10-01', 'leetcode', 2),
                   ('2026-10-02', 'leetcode', 1), ('2026-10-02', 'gpa', 4))
        lines = digest._habits(TODAY)
        self.assertIn('leetcode: done today · 3-day streak', lines[1])
        self.assertFalse(any('log it' in l for l in lines))

    def test_streak_at_stake_tonight(self):
        self.write(('2026-09-30', 'leetcode', 1), ('2026-10-01', 'leetcode', 1))
        self.assertIn('NOT done today · 2-day streak ends at midnight', digest._habits(TODAY)[1])

    def test_broken_streak_shows_the_week(self):
        self.write(('2026-09-28', 'leetcode', 1), ('2026-09-30', 'leetcode', 0))
        lines = digest._habits(TODAY)
        self.assertIn('NOT done today · 1 of the last 7 days', lines[1])
        self.assertIn('zipper metric', lines[2])

    def test_missing_file(self):
        if os.path.exists(self.csv):
            os.remove(self.csv)
        self.assertIn('0 of the last 7 days', digest._habits(TODAY)[1])


if __name__ == '__main__':
    unittest.main()
