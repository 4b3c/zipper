"""zipper/hours.py: how a session is written into the timesheet, no network.

    python3 -m unittest tests.test_hours -v
"""
import os, sys, tempfile, unittest

TMP = tempfile.mkdtemp(prefix='zipper-hours-')
for k in [k for k in os.environ if k.startswith(('ZIPPER_', 'DISCORD_', 'GITHUB_', 'BOT_'))]:
    del os.environ[k]
os.environ.update(ZIPPER_SETTINGS=os.path.join(TMP, 'settings.json'),
                  ZIPPER_ENV_FILE=os.path.join(TMP, 'env'),
                  ZIPPER_VAULT=os.path.join(TMP, 'vault'))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zipper import hours     # noqa: E402


def total(pair):
    """The sheet's Total column: end minus start, read as written."""
    a, b = (hours._hm(x) for x in pair)
    return (b - a) / 60.0


class SheetTimes(unittest.TestCase):
    def test_spans_reaching_1pm_are_24_hour(self):
        # 10-27 to 10-29: these went in as 12:00-8:00 and never matched the sheet.
        self.assertEqual(hours.sheet_times('12:00', '20:00'), ('12:00', '20:00'))
        self.assertEqual(hours.sheet_times('12:30', '13:30'), ('12:30', '13:30'))
        self.assertEqual(hours.sheet_times('11:00', '17:00'), ('11:00', '17:00'))

    def test_other_spans_are_12_hour(self):
        self.assertEqual(hours.sheet_times('13:30', '16:30'), ('1:30', '4:30'))
        self.assertEqual(hours.sheet_times('18:30', '23:00'), ('6:30', '11:00'))
        self.assertEqual(hours.sheet_times('09:00', '12:00'), ('9:00', '12:00'))
        self.assertEqual(hours.sheet_times('11:00', '12:30'), ('11:00', '12:30'))

    def test_total_is_never_negative(self):
        for a in range(6 * 60, 23 * 60, 30):
            for b in range(a + 30, 24 * 60, 30):
                st = f'{a // 60:02d}:{a % 60:02d}'
                en = f'{b // 60:02d}:{b % 60:02d}'
                self.assertEqual(total(hours.sheet_times(st, en)), (b - a) / 60.0,
                                 f'{st}-{en}')


if __name__ == '__main__':
    unittest.main()
