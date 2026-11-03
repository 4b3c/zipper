"""zipper/sync.py chain_logs: each log's Tomorrow links the next log, no network.

    python3 -m unittest tests.test_chain_logs -v
"""
import os, sys, tempfile, unittest

TMP = tempfile.mkdtemp(prefix='zipper-chain-')
for k in [k for k in os.environ if k.startswith(('ZIPPER_', 'DISCORD_', 'GITHUB_', 'BOT_'))]:
    del os.environ[k]
os.environ.update(ZIPPER_SETTINGS=os.path.join(TMP, 'settings.json'),
                  ZIPPER_ENV_FILE=os.path.join(TMP, 'env'),
                  ZIPPER_VAULT=os.path.join(TMP, 'vault'))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zipper import sync, status, core      # noqa: E402
import datetime                           # noqa: E402

LOG = os.path.join(TMP, 'vault', 'Log')


def write(day, text):
    with open(os.path.join(LOG, day + '.md'), 'w') as fh:
        fh.write(text)


def read(day):
    with open(os.path.join(LOG, day + '.md')) as fh:
        return fh.read()


class Chain(unittest.TestCase):
    def setUp(self):
        os.makedirs(LOG, exist_ok=True)
        for f in os.listdir(LOG):
            os.remove(os.path.join(LOG, f))

    def test_links_the_next_log_across_a_gap(self):
        write('2026-09-03', '# a\n\n## Tomorrow\n- \n')
        write('2026-09-05', '# b\n- did a thing\n')
        self.assertEqual(sync.chain_logs(), 2)
        self.assertIn('## Tomorrow\n- [[2026-09-05]]\n- \n', read('2026-09-03'))
        self.assertTrue(read('2026-09-05').endswith('## Tomorrow\n- [[2026-09-06]]\n'))
        self.assertEqual(sync.chain_logs(), 0)     # idempotent

    def test_retargets_when_a_missing_day_turns_up(self):
        write('2026-09-03', '# a\n\n## Tomorrow\n- [[2026-09-04]]\n- [[Pantry]] maybe\n')
        write('2026-09-05', '# b\n')
        sync.chain_logs()
        self.assertIn('## Tomorrow\n- [[2026-09-05]]\n- [[Pantry]] maybe\n', read('2026-09-03'))

    def test_chain_links_are_not_evidence(self):
        write('2026-09-03', '# a\n')
        write('2026-09-04', '# b\n')
        sync.chain_logs()
        self.assertEqual(sync.log_facts().keys() & {'2026-09-04', '2026-09-05'},
                         {'2026-09-04', '2026-09-05'})   # read as links...
        # ...but the writer only touches notes iter_notes() yields, and Log/ is skipped
        self.assertIn('Log', sync.core.SKIP_DIRS)

    def test_heading_with_trailing_space_is_reused(self):
        write('2026-09-03', '# a\n\n## Tomorrow \n- \n')
        sync.chain_logs()
        self.assertEqual(read('2026-09-03').count('## Tomorrow'), 1)
        self.assertIn('## Tomorrow\n- [[2026-09-04]]\n', read('2026-09-03'))


class Attention(unittest.TestCase):
    """The chain must not crowd notes out of Status's attention tally."""

    def setUp(self):
        os.makedirs(LOG, exist_ok=True)
        for f in os.listdir(LOG):
            os.remove(os.path.join(LOG, f))

    def attention(self):
        status.cmd_status(None)
        with open(os.path.join(core.METADIR, 'Status.md')) as fh:
            text = fh.read()
        return text.split('## Attention, last 14 days')[1].split('\n## ')[0]

    def test_chain_links_are_not_attention(self):
        for i in range(13):
            day = (core.TODAY - datetime.timedelta(days=i)).isoformat()
            write(day, '# d\n- [[Note%d]]\n' % i)
        sync.chain_logs()
        sec = self.attention()
        self.assertNotRegex(sec, r'\[\[\d{4}-\d\d-\d\d\]\]')
        for i in range(13):
            self.assertIn('[[Note%d]]' % i, sec)

    def test_chain_alone_still_reads_as_nothing_logged(self):
        for i in range(3):
            write((core.TODAY - datetime.timedelta(days=i)).isoformat(), '# d\n')
        sync.chain_logs()
        self.assertIn('Nothing logged', self.attention())


if __name__ == '__main__':
    unittest.main()
