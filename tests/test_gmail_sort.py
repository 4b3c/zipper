"""plugins/gmail/sort.py: folder rules over a fake Gmail, no network.

    python3 -m unittest tests.test_gmail_sort -v
"""
import json, os, shutil, sys, tempfile, unittest

TMP = tempfile.mkdtemp(prefix='zipper-gsort-')
for k in [k for k in os.environ if k.startswith(('ZIPPER_', 'DISCORD_', 'GITHUB_', 'BOT_'))]:
    del os.environ[k]
os.environ.update(ZIPPER_SETTINGS=os.path.join(TMP, 'settings.json'),
                  ZIPPER_ENV_FILE=os.path.join(TMP, 'env'),
                  ZIPPER_VAULT=os.path.join(TMP, 'vault'))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zipper import google, settings     # noqa: E402
from plugins.gmail import sort          # noqa: E402

RULES = {'plugins': {'gmail': {
    'folders': [{'label': 'Login codes', 'query': 'CODE', 'always': True},
                {'label': 'Promos', 'query': 'PROMO'},
                {'label': 'Newsletters', 'query': 'NEWS'}],
    'hold': 'HOLD', 'fallback': 'Important'}}}


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


class FakeGmail:
    """Answers `search` from a table of query -> ids, and records moves."""

    def __init__(self, inbox, matches, held=(), unread=()):
        self.inbox, self.matches, self.held, self.unread = list(inbox), matches, set(held), set(unread)
        self.moves, self.labels = [], {'INBOX': 'INBOX'}

    def search(self, q):
        ids = list(self.inbox)
        for key, hits in self.matches.items():
            if '(%s)' % key in q:
                ids = [i for i in ids if i in hits]
        if '-is:unread' in q:
            ids = [i for i in ids if i not in self.unread]
        if '-(HOLD)' in q:
            ids = [i for i in ids if i not in self.held]
        return ids

    def label_ids(self, names, create=True):
        for n in names:
            self.labels.setdefault(n, 'L_' + n)
        return self.labels

    def move(self, ids, label_id):
        self.moves.append((label_id, list(ids)))


class Sort(unittest.TestCase):
    def setUp(self):
        with open(os.environ['ZIPPER_SETTINGS'], 'w') as fh:
            json.dump(RULES, fh)
        self.fake = FakeGmail(['c1', 'p1', 'p2', 'n1', 'h1', 'x1'],
                              {'CODE': {'c1', 'h1'}, 'PROMO': {'p1', 'p2'}, 'NEWS': {'n1', 'p2', 'h1'}},
                              held={'h1'})
        self.saved = sort.search, sort.label_ids, sort.move
        sort.search, sort.label_ids, sort.move = self.fake.search, self.fake.label_ids, self.fake.move

    def tearDown(self):
        sort.search, sort.label_ids, sort.move = self.saved

    def test_first_rule_wins(self):
        steps = dict(sort.plan(only=None))
        self.assertEqual(steps['Promos'], ['p1', 'p2'])
        self.assertEqual(steps['Newsletters'], ['n1'])        # p2 already taken

    def test_live_respects_hold_except_always(self):
        steps = dict(sort.plan())
        self.assertIn('h1', steps['Login codes'])              # always: hold ignored
        self.fake.matches['CODE'] = {'c1'}
        self.assertNotIn('h1', dict(sort.plan())['Newsletters'])   # held

    def test_unread_never_moves(self):
        self.fake.unread = {'c1', 'p1', 'x1'}
        for backlog in (False, True):
            moved = [i for _, ids in sort.plan(backlog=backlog) for i in ids]
            self.assertNotIn('c1', moved)                      # not even an `always` rule
            self.assertNotIn('p1', moved)
            self.assertNotIn('x1', moved)                      # nor the fallback
            self.assertIn('p2', moved)

    def test_live_only_touches_seen(self):
        steps = dict(sort.plan(only={'p1'}))
        self.assertEqual(steps['Promos'], ['p1'])
        self.assertEqual(sum(len(v) for v in steps.values()), 1)

    def test_backlog_fallback_takes_the_rest(self):
        steps = dict(sort.plan(backlog=True))
        self.assertEqual(steps['Important'], ['x1'])
        self.assertNotIn('Important', dict(sort.plan()))      # never live

    def test_dry_run_moves_nothing(self):
        sort.run(backlog=True, dry=True)
        self.assertEqual(self.fake.moves, [])
        sort.run(backlog=True)
        self.assertTrue(self.fake.moves)

    def test_live_never_raises(self):
        def boom(q):
            raise RuntimeError('no network')
        sort.search = boom
        self.assertIn('sort failed', sort.live({'a': {}}))

    def test_off_without_rules(self):
        with open(os.environ['ZIPPER_SETTINGS'], 'w') as fh:
            json.dump({}, fh)
        self.assertFalse(sort.enabled())
        self.assertIsNone(sort.live({'a': {}}))
        self.assertNotIn(google.MODIFY, google.scopes())     # read-only by default

    def test_modify_scope_only_with_folders(self):
        self.assertIn(google.MODIFY, google.scopes())
        self.assertNotIn('https://www.googleapis.com/auth/gmail.readonly', google.scopes())


if __name__ == '__main__':
    unittest.main()
