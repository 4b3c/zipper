"""Vault file locks: claim on edit, refuse others, commit only your own files."""
import importlib.util, io, json, os, shutil, subprocess, sys, tempfile, time, unittest
from unittest import mock

TMP = tempfile.mkdtemp(prefix='zipper-locks-')
for k in [k for k in os.environ if k.startswith(('ZIPPER_', 'DISCORD_', 'GITHUB_', 'BOT_'))]:
    del os.environ[k]
VAULT = os.path.join(TMP, 'vault')
os.environ.update(ZIPPER_SETTINGS=os.path.join(TMP, 'settings.json'),
                  ZIPPER_ENV_FILE=os.path.join(TMP, 'env'), ZIPPER_VAULT=VAULT)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from zipper import core, locks, setup, runqueue   # noqa: E402

# Another test module in this process may have imported core first; its vault wins.
VAULT = core.VAULT


def _hook(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, 'hooks', name + '.py'))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


guard, turn = _hook('vault_guard'), _hook('turn_commit')


def git(*a):
    return subprocess.run(['git', '-C', VAULT] + list(a), capture_output=True, text=True).stdout


def write(rel, text):
    p = os.path.join(VAULT, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'w') as fh:
        fh.write(text)
    return p


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


class Base(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(VAULT, ignore_errors=True)
        os.makedirs(os.path.join(VAULT, 'Inbox'))
        git('init', '-q')
        git('config', 'user.email', 't@t'); git('config', 'user.name', 't')
        write('.gitignore', 'Inbox/*\n')
        write('A.md', 'a\n'); write('B.md', 'b\n')
        git('add', '-A'); git('commit', '-qm', 'init')
        # Everyone named in a test is live unless a test says otherwise.
        p = mock.patch.object(locks, 'live', lambda who: bool(who) and who != 'dead')
        p.start(); self.addCleanup(p.stop)
        # The registry: every name a test uses is a known conversation but review-*.
        k = mock.patch.object(locks, 'known', lambda who: bool(who) and not who.startswith('review-'))
        k.start(); self.addCleanup(k.stop)

    def as_(self, who):
        p = mock.patch.dict(os.environ, {'ZIPPER_CONVERSATION': who, 'ZIPPER_VAULT': VAULT})
        p.start(); self.addCleanup(p.stop)


class Claims(Base):
    def test_first_editor_holds_the_file(self):
        self.assertEqual(locks.claim(['A.md'], 'one'), {})
        self.assertIn('A.md', locks.claim(['A.md'], 'two'))
        self.assertEqual(locks.claim(['A.md'], 'one'), {})      # still mine

    def test_a_dead_owner_holds_nothing(self):
        locks.claim(['A.md'], 'dead')
        self.assertEqual(locks.claim(['A.md'], 'two'), {})

    def test_paths_outside_the_vault_and_inbox_are_ignored(self):
        self.assertIsNone(locks.rel('/etc/passwd'))
        self.assertIsNone(locks.rel(os.path.join(VAULT, 'Inbox', 'x.json')))
        self.assertEqual(locks.rel(os.path.join(VAULT, 'A.md')), 'A.md')

    def test_release_frees_only_mine(self):
        locks.claim(['A.md'], 'one'); locks.claim(['B.md'], 'two')
        locks.release('one')
        self.assertEqual(locks.owned('one'), [])
        self.assertEqual(locks.owned('two'), ['B.md'])


class Commit(Base):
    def test_commits_only_the_paths_named(self):
        write('A.md', 'a2\n'); write('B.md', 'b2\n'); write('New.md', 'n\n')
        ok, out = locks.commit(['A.md', 'New.md'], 'mine')
        self.assertTrue(ok, out)
        self.assertEqual(git('show', '--name-only', '--format=', 'HEAD').split(), ['A.md', 'New.md'])
        self.assertIn('B.md', git('status', '--porcelain'))

    def test_something_else_staged_is_not_swept_in(self):
        write('B.md', 'b2\n'); git('add', 'B.md')
        write('A.md', 'a2\n')
        self.assertTrue(locks.commit(['A.md'], 'mine')[0])
        self.assertEqual(git('show', '--name-only', '--format=', 'HEAD').split(), ['A.md'])


class Guard(Base):
    def ev(self, name, tool, **inp):
        return {'hook_event_name': name, 'tool_name': tool, 'tool_input': inp,
                'session_id': 's', 'tool_use_id': 'tu1'}

    def test_edit_claims_then_refuses_another(self):
        self.as_('one')
        self.assertIsNone(guard.handle(self.ev('PreToolUse', 'Edit',
                                               file_path=os.path.join(VAULT, 'A.md'))))
        self.assertEqual(locks.owned('one'), ['A.md'])
        with mock.patch.dict(os.environ, {'ZIPPER_CONVERSATION': 'two'}):
            out = guard.handle(self.ev('PreToolUse', 'Write', file_path=os.path.join(VAULT, 'A.md')))
            self.assertEqual(out['hookSpecificOutput']['permissionDecision'], 'deny')
            self.assertIn('one', out['hookSpecificOutput']['permissionDecisionReason'])
            read = guard.handle(self.ev('PostToolUse', 'Read', file_path=os.path.join(VAULT, 'A.md')))
            self.assertIn('editing this file', read['hookSpecificOutput']['additionalContext'])

    def test_code_edits_pass_through(self):
        self.as_('one')
        self.assertIsNone(guard.handle(self.ev('PreToolUse', 'Edit', file_path='/tmp/x.py')))
        self.assertEqual(locks.owned('one'), [])

    def test_shell_writes_are_claimed_after(self):
        self.as_('one')
        cmd = 'echo shell > B.md; echo x > Log/new.md'
        guard.handle(self.ev('PreToolUse', 'Bash', command=cmd))
        write('B.md', 'shell\n'); write('Log/new.md', 'x\n')
        self.assertIsNone(guard.handle(self.ev('PostToolUse', 'Bash', command=cmd)))
        self.assertEqual(locks.owned('one'), ['B.md', 'Log/new.md'])

    def test_a_file_the_command_does_not_name_is_not_claimed(self):
        # The tester's S3: the operator (or the timer's pull) writes C.md while
        # a long command that never mentions it is running.
        self.as_('one')
        cmd = 'python3 -m unittest tests.test_units'
        guard.handle(self.ev('PreToolUse', 'Bash', command=cmd))
        write('C.md', 'operator\n')
        out = guard.handle(self.ev('PostToolUse', 'Bash', command=cmd))
        self.assertEqual(locks.owned('one'), [])
        self.assertIn('C.md', out['hookSpecificOutput']['additionalContext'])
        self.assertTrue(turn.handle({'session_id': 's'}) is None)
        self.assertIn('C.md', git('status', '--porcelain'))     # left for the pass

    def test_engine_commands_claim_what_they_write(self):
        self.assertTrue(locks.attributable('Log/2026-11-18.md', 'zipper hours add --date x'))
        self.assertTrue(locks.attributable('X.md', 'cd /zipper/code && python3 -m zipper event y'))
        self.assertFalse(locks.attributable('X.md', 'cd /zipper/vault && git status'))

    def test_review_sessions_are_left_alone(self):
        self.as_('review-some-slug')
        self.assertIsNone(locks.owner())
        self.assertIsNone(guard.handle(self.ev('PreToolUse', 'Edit',
                                               file_path=os.path.join(VAULT, 'A.md'))))
        self.assertEqual(locks.listing(), [])

    def test_shell_write_over_someone_elses_file_is_reported(self):
        locks.claim(['B.md'], 'two')
        time.sleep(0.01)
        self.as_('one')
        guard.handle(self.ev('PreToolUse', 'Bash', command='sed -i s/b/x/ B.md'))
        write('B.md', 'shell\n')
        out = guard.handle(self.ev('PostToolUse', 'Bash', command='sed -i s/b/x/ B.md'))
        self.assertIn('B.md', out['hookSpecificOutput']['additionalContext'])
        self.assertEqual(locks.owned('one'), [])

    def test_no_vault_named_does_nothing(self):
        with mock.patch.dict(os.environ, {'ZIPPER_VAULT': ''}):
            self.assertIsNone(guard.handle(self.ev('PreToolUse', 'Edit',
                                                   file_path=os.path.join(VAULT, 'A.md'))))


class TurnEnd(Base):
    def test_commits_this_turns_files_and_releases(self):
        self.as_('one')
        write('A.md', 'mine\n'); write('B.md', 'theirs\n')
        locks.claim(['A.md'], 'one'); locks.claim(['B.md'], 'two')
        self.assertIsNone(turn.handle({'session_id': 's'}))
        self.assertEqual(git('show', '--name-only', '--format=', 'HEAD').split(), ['A.md'])
        self.assertEqual(locks.owned('one'), [])
        self.assertEqual(locks.owned('two'), ['B.md'])

    def test_a_refused_commit_hands_the_turn_back_once(self):
        self.as_('one')
        write('A.md', 'x\n'); locks.claim(['A.md'], 'one')
        with mock.patch.object(locks, 'commit', return_value=(False, 'fanout: Pantry changed')):
            out = turn.handle({'session_id': 's'})
            self.assertEqual(out['decision'], 'block')
            self.assertEqual(locks.owned('one'), ['A.md'])
            self.assertIsNone(turn.handle({'session_id': 's', 'stop_hook_active': True}))


class PassClaims(Base):
    def test_a_second_pass_leaves_the_first_ones_rows(self):
        rows = [{'key': 'k1'}, {'key': 'k2'}]
        with mock.patch.object(runqueue, 'pass_id', return_value='one'):
            self.assertEqual(runqueue.record_pass(rows), {})
        with mock.patch.object(runqueue, 'pass_id', return_value='two'):
            self.assertEqual(runqueue.record_pass(rows + [{'key': 'k3'}]),
                             {'k1': 'one', 'k2': 'one'})
            self.assertEqual(runqueue.take_pass(), ['k3'])


class Install(unittest.TestCase):
    def test_all_hooks_once(self):
        p = os.path.join(TMP, 'claude.json')
        setup.install_hook(p); setup.install_hook(p)
        with open(p) as fh:
            h = json.load(fh)['hooks']
        cmds = lambda ev: [x['command'] for g in h[ev] for x in g['hooks']]
        self.assertEqual(len(cmds('Stop')), 2)
        self.assertEqual(len(cmds('PreToolUse')), 1)
        self.assertEqual(h['PreToolUse'][0]['matcher'], 'Edit|Write|MultiEdit|NotebookEdit|Bash')
        self.assertEqual(h['PostToolUse'][0]['matcher'], 'Bash|Read')


if __name__ == '__main__':
    unittest.main()
