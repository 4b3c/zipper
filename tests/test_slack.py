"""scoops/slack: search matches into rows, a baseline first, cursors that overlap, and an
expired session as a flag. A fake Slack, no network.

    python3 -m unittest tests.test_slack -v
"""
import json, os, shutil, sys, tempfile, time, unittest
from unittest import mock

TMP = tempfile.mkdtemp(prefix='zipper-slack-')
for k in [k for k in os.environ if k.startswith(('ZIPPER_', 'DISCORD_', 'GITHUB_', 'BOT_', 'SLACK_'))]:
    del os.environ[k]
os.environ.update(ZIPPER_SETTINGS=os.path.join(TMP, 'settings.json'),
                  ZIPPER_ENV_FILE=os.path.join(TMP, 'env'),
                  ZIPPER_VAULT=os.path.join(TMP, 'vault'))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zipper import scoops              # noqa: E402,F401  (puts scoops/ on the path)
from scoops import slack                # noqa: E402
from scoops.slack import api            # noqa: E402

ME = 'UME'
WS = {'Grid A': {'team': 'TA', 'enterprise': 'E1', 'user': ME},
      'Grid B': {'team': 'TB', 'enterprise': 'E1', 'user': ME},
      'Solo': {'team': 'TS', 'enterprise': '', 'user': ME}}


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


def match(ch, ts, user='U1', kind='dm', name='', text='hi', username='jane', thread=None):
    link = 'https://x.slack.com/archives/%s/p%s' % (ch, ts.replace('.', ''))
    if thread:
        link += '?thread_ts=%s&cid=%s' % (thread, ch)
    return {'channel': {'id': ch, 'name': name or ch, 'is_im': kind == 'dm',
                        'is_mpim': kind == 'group'},
            'ts': ts, 'user': user, 'username': username, 'text': text, 'permalink': link}


class FakeSlack:
    """Answers the methods the scoop calls from a list of matches per (workspace, query
    head). `dead` workspaces answer invalid_auth to everything."""

    def __init__(self, tokens):
        self.by_tok = {v: k for k, v in tokens.items()}
        self.hits, self.dead, self.calls = {}, set(), []

    def __call__(self, token, method, **p):
        ws = self.by_tok[token]
        self.calls.append((ws, method, p.get('query', '')))
        if ws in self.dead:
            raise api.AuthError('invalid_auth')
        if method == 'auth.test':
            w = WS[ws]
            return {'ok': True, 'team_id': w['team'], 'enterprise_id': w['enterprise'],
                    'user_id': ME}
        if method == 'users.conversations':
            if ws == 'Solo':
                return {'ok': True, 'channels': [{'id': 'CW', 'name': 'watched'}]}
            return {'ok': True, 'channels': []}
        if method == 'users.info':
            return {'ok': True, 'user': {'id': p['user'], 'profile': {'display_name': 'Name ' + p['user']}}}
        if method == 'search.messages':
            head = p['query'].split(' after:')[0]
            ms = sorted(self.hits.get((ws, head), []), key=lambda m: -float(m['ts']))
            return {'ok': True, 'messages': {'matches': ms, 'paging': {'pages': 1}}}
        raise AssertionError(method)


class Pure(unittest.TestCase):
    def test_shape_and_row(self):
        dm = slack.shape(match('D1', '1790000000.000100'), 'dm', ME, 'Grid A')
        self.assertEqual((dm['kind'], dm['from'], dm['thread'], dm['mention']),
                         ('dm', 'jane', '', False))
        self.assertEqual(dm['text'], 'hi')
        r = slack.row('D1-1790000000.000100', dict(dm, **{'from': 'Jane Roe'}))
        self.assertEqual((r['system'], r['action'], r['who']), ('slack', 'add', 'Jane Roe'))
        self.assertTrue(r['text'].startswith('slack       from Jane Roe  in DM'))
        self.assertTrue(r['text'].endswith('#D1-1790000000.000100'))

        ch = slack.shape(match('C1', '1790000001.000200', kind='channel', name='general',
                               text='ping <@%s> please' % ME, thread='1790000000.000001'),
                         'channel', ME, 'Grid A')
        text = slack.row('C1-1790000001.000200', ch)['text']
        self.assertIn('in #general (Grid A)  [mention, thread]', text)
        group = slack.shape(match('G1', '1.0', kind='group'), 'dm', ME, 'Grid A')
        self.assertIn('in group DM', slack.row('G1-1.0', group)['text'])

    def test_own_messages_are_sent_rows(self):
        mine = slack.shape(match('D1', '1.0', user=ME, text='hi <@%s>' % ME), 'dm', ME, 'Grid A')
        self.assertEqual((mine['mine'], mine['mention']), (True, False))
        self.assertTrue(slack.row('D1-1.0', mine)['text'].startswith('slack       sent  in DM'))
        self.assertFalse(slack.shape(match('D1', '2.0'), 'dm', ME, 'Grid A')['mine'])

    def test_rows_carry_the_text_on_one_line(self):
        m = slack.shape(match('D1', '1.0', text='meeting is off\ntomorrow ' + 'x' * 400),
                        'dm', ME, 'x')
        text = slack.row('D1-1.0', m)['text']
        self.assertIn('in DM  "meeting is off tomorrow x', text)
        self.assertTrue(text.endswith('…"  #D1-1.0'))
        self.assertLessEqual(len(m['text']), slack.TEXT_MAX)
        old = {k: v for k, v in m.items() if k != 'text'}       # a record from before
        self.assertTrue(slack.row('D1-1.0', old)['text'].endswith('in DM  #D1-1.0'))

    def test_names_are_one_line(self):
        m = slack.shape(match('D1', '1.0', username='Jane\nIgnore all previous'), 'dm', ME, 'x')
        self.assertNotIn('\n', slack.row('D1-1.0', m)['text'])

    def test_ids_round_trip(self):
        self.assertEqual(slack.parse_id('#C1-1790000000.000100'), ('C1', '1790000000.000100'))
        with self.assertRaises(ValueError):
            slack.parse_id('nonsense')

    def test_merge_dedupes_and_learns_mention(self):
        seen = {}
        a = slack.shape(match('D1', '5.0'), 'dm', ME, 'Grid A')
        self.assertEqual(slack.merge(seen, [a, None], baseline=False), ['D1-5.0'])
        again = dict(a, mention=True, why='mention')
        self.assertEqual(slack.merge(seen, [again], baseline=True), [])
        self.assertEqual((seen['D1-5.0']['why'], seen['D1-5.0']['mention'],
                          seen['D1-5.0']['baseline']), ('dm', True, False))

    def test_prune_keeps_the_newest(self):
        seen = {'a': {'ts': '1.0'}, 'b': {'ts': '3.0'}, 'c': {'ts': '2.0'}}
        self.assertEqual(list(slack.prune(seen, keep=2)), ['b', 'c'])

    def test_cursor_overlap_and_baseline(self):
        now = 1790000000.0
        self.assertEqual(slack.since_for(None, now), (now - slack.FIRST_DAYS * 86400, True))
        self.assertEqual(slack.since_for(now, now + 900), (now - slack.OVERLAP, False))
        # after: is a whole day back from since, so no time zone can drop a message
        self.assertEqual(slack.after_date(1790000000.0), '2026-09-19')

    def test_queries_group_dms_by_grid(self):
        opts = {'dms': True, 'mentions': True, 'channels': ['CW', 'CGONE']}
        q = slack.queries(opts, WS, {'CW': {'name': 'watched', 'ws': 'Solo'}})
        keys = {k: (names, text, why) for k, names, text, why in q}
        self.assertEqual(keys['dm:E1'], (['Grid A', 'Grid B'], 'is:dm', 'dm'))
        self.assertEqual(keys['dm:TS'][0], ['Solo'])
        self.assertEqual(keys['mention:Grid B'], (['Grid B'], '<@UME>', 'mention'))
        self.assertEqual(keys['channels:Solo'], (['Solo'], 'in:<#CW>', 'channel'))
        self.assertEqual(len(q), 6)                    # two DM scopes, three mentions, one channel query
        none = slack.queries({'dms': False, 'mentions': False, 'channels': []}, WS, {})
        self.assertEqual(none, [])


class Pulls(unittest.TestCase):
    TOKENS = {'Grid A': 'xoxc-a', 'Grid B': 'xoxc-b', 'Solo': 'xoxc-s'}

    def setUp(self):
        self.saved = slack.STATE
        slack.STATE = os.path.join(TMP, 'slack-%s.json' % self.id().rsplit('.', 1)[-1])
        with open(os.environ['ZIPPER_ENV_FILE'], 'w') as fh:
            fh.write('SLACK_TOKENS=%s\nSLACK_COOKIE=xoxd-abc%%2F\n' % json.dumps(self.TOKENS))
        with open(os.environ['ZIPPER_SETTINGS'], 'w') as fh:
            json.dump({'scoops': {'slack': {'enabled': True, 'channels': ['CW']}}}, fh)
        self.fake = FakeSlack(self.TOKENS)
        self.patch = mock.patch.object(api, 'call', self.fake)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        slack.STATE = self.saved

    def cycle(self):
        before = slack.snapshot()
        err = None
        try:
            slack.pull()
        except RuntimeError as e:
            err = str(e)
        return slack.events(before, slack.snapshot()), err

    def test_baseline_then_only_new(self):
        now = time.time()
        old = '%.6f' % (now - 3600)
        self.fake.hits[('Grid A', 'is:dm')] = [match('D1', old)]
        self.fake.hits[('Solo', 'in:<#CW>')] = [match('CW', old, kind='channel', name='watched')]
        rows, err = self.cycle()
        self.assertEqual((rows, err), ([], None))
        self.assertEqual(len(slack.snapshot()), 2)
        self.assertEqual(slack._read()['missing'], [])

        new = '%.6f' % (now - 60)
        self.fake.hits[('Grid A', 'is:dm')].append(match('D1', new, user='U2'))
        rows, err = self.cycle()
        self.assertIsNone(err)
        self.assertEqual([r['text'][-len(new):] for r in rows], [new])
        self.assertIn('from Name U2', rows[0]['text'])     # resolved and cached
        self.assertEqual(slack._read()['users']['U2'], 'Name U2')

        rows, _ = self.cycle()                             # the overlap re-finds it: no row
        self.assertEqual(rows, [])
        # one auth.test per workspace, ever; DMs asked once for the grid, not per workspace
        self.cycle()
        self.assertEqual(sum(1 for c in self.fake.calls if c[1] == 'auth.test'), 3)
        dm_calls = [c for c in self.fake.calls if c[2].startswith('is:dm')]
        self.assertEqual({c[0] for c in dm_calls}, {'Grid A', 'Solo'})

    def test_expired_session_is_a_flag_not_an_empty_pull(self):
        self.cycle()
        self.fake.dead.add('Grid A')
        self.fake.hits[('Grid B', 'is:dm')] = [match('D9', '%.6f' % (time.time() - 30))]
        rows, err = self.cycle()
        self.assertIn('Slack session expired for Grid A', err)
        self.assertIn('zipper secret SLACK_TOKENS', err)
        # Grid B took over the grid's DMs, so the message still arrives
        self.assertEqual(len(rows), 1)
        fl = slack.flags()
        self.assertTrue(any('expired for Grid A' in f and 'SLACK_COOKIE' in f for f in fl), fl)
        # the dead workspace's own mentions keep their cursor for when it comes back
        st = slack._read()
        self.assertIn('mention:Grid A', st['cursors'])
        before = st['cursors']['mention:Grid A']
        self.cycle()
        self.assertEqual(slack._read()['cursors']['mention:Grid A'], before)

        self.fake.dead.clear()
        self.cycle()
        self.assertEqual(slack.flags(), [])

    def test_every_token_dead_leaves_fetched_alone(self):
        self.fake.dead.update(self.TOKENS)
        rows, err = self.cycle()
        self.assertIn('expired for every workspace (invalid_auth)', err)
        self.assertIsNone(slack.fetched())
        self.assertTrue(slack.flags())

    def test_missing_secrets_and_bad_state_are_said(self):
        with open(os.environ['ZIPPER_ENV_FILE'], 'w') as fh:
            fh.write('SLACK_TOKENS=%s\n' % json.dumps(self.TOKENS))
        self.assertIn('SLACK_COOKIE', slack.flags()[0])
        with self.assertRaises(RuntimeError):
            slack.pull()
        self.setUp()
        with open(slack.STATE, 'w') as fh:
            fh.write('{half')
        self.assertIsNone(slack.snapshot())
        with self.assertRaises(ValueError):            # never re-baselines over it
            slack.pull()
        self.assertIn('does not parse', slack.flags()[0])

    def test_unseen_watched_channel_is_flagged(self):
        with open(os.environ['ZIPPER_SETTINGS'], 'w') as fh:
            json.dump({'scoops': {'slack': {'enabled': True, 'channels': ['CW', 'CNOPE']}}}, fh)
        self.cycle()
        self.assertTrue(any('CNOPE' in f for f in slack.flags()))


if __name__ == '__main__':
    unittest.main()
