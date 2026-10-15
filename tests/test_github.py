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

from zipper import gh, settings              # noqa: E402
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


class PushRows(unittest.TestCase):
    def rows(self, repos, before, after):
        with mock.patch.object(ghp, '_repos', return_value=repos):
            return ghp.events(before, after)

    def test_row_names_pusher_and_message(self):
        repos = [{'name': 'zipper', 'key': 'zipper', 'commits': [
            {'time': '2026-10-14T10:05', 'message': 'Newest', 'author': 'zipper-4b3c[bot]', 'mine': False},
            {'time': '2026-10-14T10:01', 'message': 'Older', 'author': 'abe', 'mine': True},
            {'time': '2026-10-13T09:00', 'message': 'Last push', 'author': 'abe', 'mine': True}]}]
        [row] = self.rows(repos, {'zipper': '2026-10-13T09:00:00'}, {'zipper': '2026-10-14T10:06:00'})
        self.assertIn('Newest', row['text'])
        self.assertIn('(+1 more)', row['text'])
        self.assertIn('2026-10-14T10:06', row['text'])
        self.assertEqual(row['who'], 'zipper-4b3c[bot], ' + ghp.OWNER)
        self.assertEqual(ghp.target(row, [('Zipper', {'repos': ['zipper']})]), 'Zipper')

    def test_nda_repo_row_has_no_message(self):
        repos = [{'name': 'secret', 'key': 'LAB/secret', 'commits': [
            {'time': '2026-10-14T10:05', 'author': 'mate', 'mine': False}]}]
        [row] = self.rows(repos, {'secret': '2026-10-13T09:00:00'}, {'secret': '2026-10-14T10:06:00'})
        self.assertEqual(row['text'], 'pushed      secret  2026-10-14T10:06')
        self.assertEqual(row['who'], 'mate')

    def test_unknown_pusher_is_absent(self):
        [row] = self.rows([{'name': 'r', 'commits': []}], {'r': '1'}, {'r': '2'})
        self.assertNotIn('who', row)

    def test_newly_visible_repo_is_not_a_push(self):
        self.assertEqual(self.rows([], {}, {'r': '2'}), [])


if __name__ == '__main__':
    unittest.main()
