"""Push rows: who pushed and what, and the NDA line on commit messages.

    python3 -m unittest tests.test_github -v
"""
import json, os, shutil, sys, tempfile, unittest
from unittest import mock

TMP = tempfile.mkdtemp(prefix='zipper-github-')
for k in [k for k in os.environ if k.startswith(('ZIPPER_', 'DISCORD_', 'GITHUB_', 'BOT_'))]:
    del os.environ[k]
os.environ.update(ZIPPER_SETTINGS=os.path.join(TMP, 'settings.json'),
                  ZIPPER_ENV_FILE=os.path.join(TMP, 'env'),
                  ZIPPER_VAULT=os.path.join(TMP, 'vault'))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zipper import gh, ghpush, settings      # noqa: E402
import plugins.github as ghp                 # noqa: E402


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


def api_commit(sha, login, when, message, name='Someone'):
    return {'sha': sha * 7, 'author': {'login': login} if login else None,
            'commit': {'message': message + '\n\nbody that never leaves',
                       'author': {'name': name, 'date': when},
                       'committer': {'name': name, 'date': when}}}


class CommitRecord(unittest.TestCase):
    def setUp(self):
        if os.path.exists(settings.PATH):
            os.remove(settings.PATH)

    def test_own_repo_keeps_message_and_author(self):
        with mock.patch.object(gh, 'GH_USER', 'abe'):
            c = gh._commit(api_commit('a', 'abe', '2026-10-14T03:30:00Z', 'Fix it'),
                           {'key': 'zipper', 'is_org': False})
        self.assertEqual(c['message'], 'Fix it')
        self.assertEqual(c['author'], 'abe')
        self.assertTrue(c['mine'])
        self.assertEqual(len(c['time']), 16)

    def test_org_repo_drops_message_unless_open(self):
        r = {'key': 'LAB/secret', 'is_org': True}
        c = gh._commit(api_commit('b', 'mate', '2026-10-14T03:30:00Z', 'NDA thing'), r)
        self.assertNotIn('message', c)
        self.assertEqual(c['author'], 'mate')
        settings.put('plugins.github.open', ['LAB/secret'])
        c = gh._commit(api_commit('b', 'mate', '2026-10-14T03:30:00Z', 'Open thing'), r)
        self.assertEqual(c['message'], 'Open thing')

    def test_no_login_is_not_mine(self):
        c = gh._commit(api_commit('c', None, '2026-10-14T03:30:00Z', 'x', name='Bot'),
                       {'key': 'r', 'is_org': False})
        self.assertFalse(c['mine'])
        self.assertEqual(c['author'], 'Bot')


def raw(sha, login, message, parents=1, when='2026-10-14T03:30:00Z'):
    c = api_commit(sha, login, when, message)
    c['sha'] = sha.ljust(40, '0')
    c['parents'] = [{}] * parents
    return c


class FakeAPI:
    """Answers the three endpoints ghpush reads from a fixed table."""
    def __init__(self, compare=None, pulls=None):
        self.compare, self.pulls, self.calls = compare or {}, pulls or {}, []

    def __call__(self, path, params):
        self.calls.append(path)
        if '/compare/' in path:
            return self.compare[path.rsplit('/', 1)[1]]
        if path.endswith('/pulls'):
            return self.pulls.get(path.split('/')[-2], [])
        raise AssertionError(path)


REPO = {'owner': 'abe', 'name': 'zipper', 'key': 'zipper', 'is_org': False,
        'default_branch': 'main'}


def push(old, now, api, repo=REPO, open_=True):
    with mock.patch.object(gh, 'GH_USER', 'abe'):
        return ghpush.pushes(repo, old, now, api, gh._commit, open_)


class BranchPushes(unittest.TestCase):
    def test_feature_branch_push(self):
        api = FakeAPI({'b1...b2': {'status': 'ahead', 'commits': [
            raw('c1', 'zipper-4b3c[bot]', 'First'), raw('b2', 'zipper-4b3c[bot]', 'Second')]}})
        [rec] = push({'main': 'm1', 'feat': 'b1'}, {'main': 'm1', 'feat': 'b2'}, api)
        text, who = ghpush.describe(rec, 'zipper', '2026-10-15T04:17', 'Abram')
        self.assertEqual(text, 'pushed      zipper → feat  2026-10-15T04:17  Second  (+1 more)')
        self.assertEqual(who, 'zipper-4b3c[bot]')
        self.assertFalse(any(c.endswith('/pulls') for c in api.calls))

    def test_new_branch_compares_with_default(self):
        api = FakeAPI({'m1...b2': {'status': 'ahead', 'commits': [raw('b2', 'abe', 'Start')]}})
        [rec] = push({'main': 'm1'}, {'main': 'm1', 'feat': 'b2'}, api)
        text, who = ghpush.describe(rec, 'zipper', 'T', 'Abram')
        self.assertEqual(text, 'pushed      zipper → feat (new)  T  Start')
        self.assertEqual(who, 'Abram')

    def test_merge_commit_names_the_pr_and_merger(self):
        api = FakeAPI({'m1...m2' + '0' * 38: {'status': 'ahead', 'commits': [
            raw('c1', 'zipper-4b3c[bot]', 'Work'), raw('m2', 'abe', 'Merge pull request #44', 2)]}},
            {'m2' + '0' * 38: [{'number': 44, 'merged_at': 'x', 'title': 'Push rows',
                                'merge_commit_sha': 'm2' + '0' * 38}]})
        [rec] = push({'main': 'm1'}, {'main': 'm2' + '0' * 38}, api)
        self.assertEqual(rec['kind'], 'merge')
        text, who = ghpush.describe(rec, 'zipper', 'T', 'Abram')
        self.assertEqual(text, 'merged      zipper → main  T  #44 Push rows  (2 commits)')
        self.assertEqual(who, 'Abram')

    def test_squash_merge_is_a_merge(self):
        sha = 's1' + '0' * 38
        api = FakeAPI({'m1...' + sha: {'status': 'ahead', 'commits': [raw('s1', 'mate', 'Squashed (#9)')]}},
                      {sha: [{'number': 9, 'merged_at': 'x', 'title': 'Thing', 'merge_commit_sha': sha}]})
        [rec] = push({'main': 'm1'}, {'main': sha}, api)
        self.assertEqual((rec['kind'], rec['pr']), ('merge', 9))

    def test_direct_commit_to_main_is_a_commit(self):
        api = FakeAPI({'m1...m2': {'status': 'ahead', 'commits': [raw('m2', 'abe', 'Fix')]}})
        [rec] = push({'main': 'm1'}, {'main': 'm2'}, api)
        self.assertEqual(ghpush.describe(rec, 'r', 'T', 'Abram')[0], 'pushed      r → main  T  Fix')

    def test_rewritten_branch_is_a_force_push(self):
        api = FakeAPI({'b1...b2': {'status': 'diverged', 'commits': [raw('b2', 'abe', 'Redo')]}})
        [rec] = push({'main': 'm', 'feat': 'b1'}, {'main': 'm', 'feat': 'b2'}, api)
        self.assertTrue(ghpush.describe(rec, 'r', 'T', 'A')[0].startswith('force-pushed r → feat'))

    def test_no_baseline_or_deleted_branch_says_nothing(self):
        self.assertEqual(push(None, {'main': 'm2'}, FakeAPI()), [])
        self.assertEqual(push({'main': 'm', 'gone': 'g'}, {'main': 'm'}, FakeAPI()), [])

    def test_nda_repo_hides_branch_names_and_messages(self):
        org = {'owner': 'LAB', 'name': 'secret', 'key': 'LAB/secret', 'is_org': True,
               'default_branch': 'main'}
        main, feat = ghpush._label('main', False), ghpush._label('lidar-calib', False)
        self.assertNotIn('lidar', feat)
        api = FakeAPI({'m...f2': {'status': 'ahead', 'commits': [raw('f2', 'mate', 'NDA work')]},
                       'm...m2': {'status': 'ahead', 'commits': [raw('m2', 'mate', 'NDA merge', 2)]}},
                      {'m2': [{'number': 3, 'merged_at': 'x', 'title': 'NDA title', 'merge_commit_sha': 'm2'}]})
        recs = push({main: 'm', feat: 'f1'.replace('f1', 'm')}, {main: 'm2', feat: 'f2'}, api,
                    repo=org, open_=False)
        texts = [ghpush.describe(r, 'secret', 'T', 'A')[0] for r in recs]
        self.assertIn('pushed      secret → a branch  T', texts)
        self.assertIn('merged      secret → main  T  #3', texts)
        self.assertFalse(any('NDA' in t for t in texts))


class PushRows(unittest.TestCase):
    def rows(self, repos, before, after):
        with mock.patch.object(ghp, '_repos', return_value=repos):
            return ghp.events(before, after)

    def test_one_row_per_branch(self):
        recs = [{'branch': 'main', 'default': True, 'new': False, 'kind': 'merge', 'forced': False,
                 'pr': 44, 'pr_title': 'Push rows', 'commits': [{'author': 'abe', 'mine': True}]},
                {'branch': 'feat', 'default': False, 'new': True, 'kind': 'commit', 'forced': False,
                 'commits': [{'author': 'mate', 'message': 'Hi'}]}]
        rows = self.rows([{'name': 'zipper', 'pushes': recs}], {'zipper': '1'}, {'zipper': '2026-10-15T04:20:00'})
        self.assertEqual([r['text'] for r in rows],
                         ['merged      zipper → main  2026-10-15T04:20  #44 Push rows',
                          'pushed      zipper → feat (new)  2026-10-15T04:20  Hi'])
        self.assertEqual([r['who'] for r in rows], [ghp.OWNER, 'mate'])
        self.assertEqual(ghp.target(rows[0], [('Zipper', {'repos': ['zipper']})]), 'Zipper')

    def test_no_pushes_known_is_a_bare_row(self):
        [row] = self.rows([{'name': 'r'}], {'r': '1'}, {'r': '2026-10-14T10:06:00'})
        self.assertEqual(row['text'], 'pushed      r  2026-10-14T10:06')
        self.assertNotIn('who', row)

    def test_newly_visible_repo_is_not_a_push(self):
        self.assertEqual(self.rows([], {}, {'r': '2'}), [])


class BranchBaseline(unittest.TestCase):
    def run_branches(self, r, cached):
        with mock.patch.object(gh, '_api', side_effect=lambda path, tok, params: [
                {'name': 'main', 'commit': {'sha': 'm2'}}] if path.endswith('/branches') else
                {'status': 'ahead', 'commits': []}):
            gh._branches(r, cached, None)
        return r

    def test_unchanged_repo_keeps_heads_and_makes_no_push(self):
        r = self.run_branches(dict(REPO, pushed_at='a'), {'pushed_at': 'a', 'branches': {'main': 'm1'}})
        self.assertEqual((r['branches'], r['pushes']), ({'main': 'm1'}, []))

    def test_first_sight_takes_a_baseline_only(self):
        r = self.run_branches(dict(REPO, pushed_at='b'), {'pushed_at': 'a'})
        self.assertEqual((r['branches'], r['pushes']), ({'main': 'm2'}, []))


# ---------------------------------------------------------------- assigned issues

from zipper import core, ghissues           # noqa: E402


def issues_settings(**plug):
    g = {'user': 'abe', 'open': ['Org/open'], 'issues': ['Org/open']}
    g.update(plug)
    os.makedirs(os.path.dirname(settings.PATH), exist_ok=True)
    json.dump({'plugins': {'github': g}}, open(settings.PATH, 'w'))


def node(n, title, state='OPEN', closed=None, status='In Progress', due='2026-10-30'):
    vals = [{'name': status, 'field': {'name': 'Status'}},
            {'date': due, 'field': {'name': 'Target date'}}]
    return {'number': n, 'title': title, 'state': state, 'closedAt': closed,
            'url': 'https://github.com/Org/open/issues/%d' % n, 'parent': None,
            'projectItems': {'nodes': [{'fieldValues': {'nodes': vals}}]}}


NOTES = [('Orbit', {'repos': '[Org/open, Org/other]'})]


class Issues(unittest.TestCase):
    def setUp(self):
        issues_settings()
        shutil.rmtree(core.VAULT, ignore_errors=True)
        os.makedirs(core.INBOX, exist_ok=True)
        if os.path.exists(ghissues.STORE):
            os.remove(ghissues.STORE)

    def items(self):
        return [ghissues.item('Org/open', node(9, 'Fix the editor (#71) [now]'), 'Target date'),
                ghissues.item('Org/open', node(4, 'Old one', 'CLOSED', '2026-10-20T18:00:00Z'),
                              'Target date')]

    def test_org_repo_must_be_listed_open(self):
        issues_settings(issues=['Org/open', 'Org/secret', 'abe/mine'])
        self.assertEqual(ghissues.repos(), (['Org/open', 'abe/mine'], ['Org/secret']))

    def test_render_reads_as_tasks(self):
        text = ghissues.render(self.items(), NOTES)
        self.assertIn('generated: github', text)
        self.assertIn('- [ ] Fix the editor (issue 71) (now) [project:: [[Orbit]]] '
                      '[due:: 2026-10-30] [issue:: Org/open#9]', text)
        self.assertIn('  In Progress on the board.', text)
        self.assertRegex(text, r'- \[x\] Old one .*\[issue:: Org/open#4\] ✅ 2026-10-2')

    def test_a_hand_tick_is_undone_and_reported(self):
        ghissues.write(self.items(), NOTES)
        before = ghissues.snapshot()
        p = ghissues.path()
        t = open(p).read().replace('- [ ] Fix', '- [x] Fix')
        open(p, 'w').write(t)
        undone, why = ghissues.write(self.items(), NOTES)
        self.assertEqual(why, '')
        self.assertIn('ticked "Fix the editor', undone)
        self.assertIn('- [ ] Fix', open(p).read())
        rows = ghissues.events(before, ghissues.snapshot())
        self.assertEqual([r['text'][:6] for r in rows], ['undone'])

    def test_rewriting_its_own_file_is_not_an_edit(self):
        ghissues.write(self.items(), NOTES)
        self.assertEqual(ghissues.write(self.items(), NOTES), ('', ''))

    def test_a_file_it_did_not_write_is_left_alone(self):
        os.makedirs(os.path.dirname(ghissues.path()), exist_ok=True)
        open(ghissues.path(), 'w').write('---\ntype: tasks\n---\n- [ ] mine\n')
        undone, why = ghissues.write(self.items(), NOTES)
        self.assertIn('left alone', why)
        self.assertEqual(open(ghissues.path()).read(), '---\ntype: tasks\n---\n- [ ] mine\n')

    def test_events(self):
        b = {'items': {'Org/open#1': ('A', '', '2026-10-23', 'Ready'),
                       'Org/open#2': ('B', '', '', 'Ready'),
                       'Org/open#3': ('C', '', '', 'Ready')}}
        a = {'items': {'Org/open#1': ('A', '', '2026-10-30', 'Ready'),
                       'Org/open#2': ('B', '2026-10-21', '', 'Done'),
                       'Org/open#5': ('E', '', '', 'Ready')}}
        texts = sorted(r['text'].split()[0] for r in ghissues.events(b, a))
        self.assertEqual(texts, ['+', '-', 'closed', 'redated'])

    def test_a_partial_read_raises_rather_than_writing(self):
        with mock.patch.object(ghissues, '_gql', side_effect=RuntimeError('rate limited')):
            with self.assertRaises(RuntimeError):
                ghissues.fetch('tok', 'abe')
        self.assertFalse(os.path.exists(ghissues.path()))

    def test_dashboard_refuses_to_tick_or_delete(self):
        from zipper.web import data
        ghissues.write(self.items(), NOTES)
        key = 'task:Orbit|Fix the editor (issue 71) (now)'
        self.assertEqual(data.generated(ghissues.path()), 'github')
        self.assertFalse(data.toggle_done(key)['ok'])
        self.assertFalse(data.delete_task(key)['ok'])
        t = [t for t in data.open_tasks() if t['text'].startswith('Fix')][0]
        self.assertTrue(t['readonly'])
        self.assertEqual(t['url'], 'https://github.com/Org/open/issues/9')


if __name__ == '__main__':
    unittest.main()
