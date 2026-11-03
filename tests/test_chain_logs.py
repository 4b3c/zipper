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

from zipper import sync      # noqa: E402

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


if __name__ == '__main__':
    unittest.main()
