"""Unit tests: the pure parts, fast, with no network, no Claude and no Discord.

    python3 -m unittest tests.test_units -v

Run in CI on every pull request (.github/workflows/check.yml). The environment is
emptied of anything zipper reads before the package is imported, so a run on the
box cannot touch the live vault, settings or .env -- a sandbox that inherited the
live environment once logged a second bot in with the real token.
"""
import base64, datetime, json, os, shutil, subprocess, sys, tempfile, time, unittest

TMP = tempfile.mkdtemp(prefix='zipper-units-')
for k in [k for k in os.environ if k.startswith(('ZIPPER_', 'DISCORD_', 'GITHUB_', 'BOT_'))]:
    del os.environ[k]
os.environ.update(ZIPPER_SETTINGS=os.path.join(TMP, 'settings.json'),
                  ZIPPER_ENV_FILE=os.path.join(TMP, 'env'),
                  ZIPPER_VAULT=os.path.join(TMP, 'vault'))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from zipper import backup, hostd, settings, supervise, setup, runqueue   # noqa: E402
from zipper.inputs import peers, upstream                         # noqa: E402


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


class TOTP(unittest.TestCase):
    def test_rfc6238_vector(self):
        # RFC 6238 appendix B, SHA-1, T=59: 94287082.
        self.assertEqual(hostd._hotp(b'12345678901234567890', 59 // 30, 8), '94287082')

    def test_code_works_once(self):
        secret = base64.b32encode(b'A' * 20).decode()
        code, counter = hostd.totp(secret)
        used = hostd.check_code(secret, code, last_used=0)
        self.assertEqual(used, counter)
        self.assertIsNone(hostd.check_code(secret, code, last_used=used))

    def test_wrong_code(self):
        secret = base64.b32encode(b'A' * 20).decode()
        self.assertIsNone(hostd.check_code(secret, '000000', last_used=0, t=10 ** 9))


class HostTiers(unittest.TestCase):
    cfg = dict(hostd.DEFAULT_CONFIG, services=['zipper-web', 'pantry'],
               restart_auto=['zipper-web'])

    def tier(self, verb, *args):
        return hostd.classify(self.cfg, 'zipper-1', verb, list(args))[0]

    def test_tiers(self):
        self.assertEqual(self.tier('status'), 'read')
        self.assertEqual(self.tier('service.logs', 'pantry'), 'read')
        self.assertEqual(self.tier('service.restart', 'zipper-web'), 'routine')
        self.assertEqual(self.tier('service.restart', 'pantry'), 'advanced')
        self.assertEqual(self.tier('run', 'ls'), 'advanced')

    def test_own_container_only(self):
        _, argv, _ = hostd.classify(self.cfg, 'zipper-1', 'container.restart', ['zipper-0'])
        self.assertEqual(argv[-1], 'zipper-1')

    def test_refusals(self):
        for verb, args in (('service.status', ['nginx']), ('rm', ['-rf', '/']), ('run', [])):
            with self.assertRaises(ValueError):
                hostd.classify(self.cfg, 'zipper-1', verb, args)

    def test_approval_is_the_requesters(self):
        secret = base64.b32encode(b'C' * 20).decode()
        st = hostd.State(dict(self.cfg, totp_secret=secret, zippers={
            'zipper-0': {'tiers': ['advanced']}, 'zipper-1': {'tiers': ['advanced']}}))
        st.pending[1] = {'zipper': 'zipper-0', 'what': 'true', 'desc': 'true', 'at': time.time()}
        code = hostd.totp(secret)[0]
        self.assertIn('not yours', hostd._approve(st, 'zipper-1', ['1', code])['error'])
        self.assertIn(1, st.pending)


class Schedule(unittest.TestCase):
    S = {'fetch_minutes': 60, 'pass': ['09:00', '21:00'], 'digest': '19:00'}

    def test_catches_up_once(self):
        now = datetime.datetime(2026, 9, 29, 9, 1)
        self.assertEqual(supervise.due(now, {}, self.S),
                         [('fetch', 'fetch'), ('pass', 'pass@09:00')])
        ran = {'fetch': '2026-09-29T08:30:00', 'pass@09:00': '2026-09-29T09:00:30'}
        self.assertEqual(supervise.due(now, ran, self.S), [])

    def test_missed_slot_is_skipped(self):
        now = datetime.datetime(2026, 9, 29, 12, 0)
        self.assertEqual(supervise.due(now, {'fetch': '2026-09-29T11:30:00'}, self.S), [])

    def test_update_rides_the_fetch_clock(self):
        now = datetime.datetime(2026, 9, 29, 12, 0)
        jobs = supervise.due(now, {'fetch': '2026-09-29T11:30:00'}, self.S, auto_update=True)
        self.assertIn(('update', 'update'), jobs)


class Settings(unittest.TestCase):
    def setUp(self):
        if os.path.exists(settings.PATH):
            os.remove(settings.PATH)

    def test_defaults_and_env(self):
        settings.put('ports.web', 9100)
        settings.put('inputs.hours.enabled', True)
        env = settings.env()
        self.assertEqual(env['ZIPPER_URL'], 'http://127.0.0.1:9100')
        self.assertIn('hours', env['ZIPPER_INPUTS'].split(','))
        self.assertEqual(settings.get('ports.bot'), 4200)

    def test_check_flags_mistakes(self):
        settings.save({'ports': {'web': 'eighty'}, 'colour': 'blue', 'inputs': {'slak': {}}})
        probs = ' '.join(settings.check())
        for word in ('ports.web', 'colour', 'inputs.slak'):
            self.assertIn(word, probs)

    def test_environment_outranks_settings(self):
        settings.put('owner', 'Settings')
        os.environ['ZIPPER_OWNER'] = 'Env'
        try:
            settings.apply()
            self.assertEqual(os.environ['ZIPPER_OWNER'], 'Env')
        finally:
            del os.environ['ZIPPER_OWNER']


class InitVault(unittest.TestCase):
    def test_init_substitutes_and_refuses_twice(self):
        path = os.path.join(TMP, 'v1')
        setup.init_vault(path, owner='Sam', zid='zipper-9')
        with open(os.path.join(path, 'CLAUDE.md'), encoding='utf-8') as fh:
            text = fh.read()
        self.assertIn("Sam's vault", text)
        self.assertIn('zipper-9', text)
        self.assertNotIn('{{', text)
        with self.assertRaises(RuntimeError):
            setup.init_vault(path)

    def test_engine_output_is_not_notes(self):
        path = os.path.join(TMP, 'v2')
        os.makedirs(os.path.join(path, 'Meta'))
        os.makedirs(os.path.join(path, 'Inbox'))
        open(os.path.join(path, 'Meta', 'Status.md'), 'w').close()
        self.assertFalse(setup._has_notes(path))
        open(os.path.join(path, 'Meta', 'Mine.md'), 'w').close()
        self.assertTrue(setup._has_notes(path))


class Backup(unittest.TestCase):
    def git(self, path, *args, **env):
        return subprocess.run(['git', '-C', path] + list(args), capture_output=True, text=True,
                              env=dict(os.environ, **env)).stdout.strip()

    def use(self, vault):
        old = backup.VAULT
        backup.VAULT = vault
        self.addCleanup(setattr, backup, 'VAULT', old)

    def test_init_with_backup_pushes(self):
        vault, bare = os.path.join(TMP, 'b1'), os.path.join(TMP, 'b1.git')
        setup.init_vault(vault, owner='Sam', backup=bare)
        self.assertEqual(self.git(vault, 'remote', 'get-url', 'origin'), bare)
        self.assertEqual(self.git(bare, 'log', '-1', '--format=%s', 'main'), 'A new vault')
        self.use(vault)
        self.assertEqual(backup.flags(), [])

    def test_no_remote_is_flagged(self):
        vault = os.path.join(TMP, 'b2')
        setup.init_vault(vault)
        self.use(vault)
        self.assertIn('no backup remote', ' '.join(backup.flags()))

    def test_push_catches_up_and_stale_is_flagged(self):
        vault, bare = os.path.join(TMP, 'b3'), os.path.join(TMP, 'b3.git')
        setup.init_vault(vault, backup=bare)
        self.use(vault)
        with open(os.path.join(vault, 'Tasks', 'Main.md'), 'a') as fh:
            fh.write('\n- [ ] something\n')
        old = '2026-01-01T00:00:00'
        self.git(vault, 'commit', '-qam', 'old work', GIT_COMMITTER_DATE=old, GIT_AUTHOR_DATE=old)
        self.assertIn('1 commit(s) behind', ' '.join(backup.flags()))
        self.assertEqual(backup.push(), '')
        self.assertEqual(backup.flags(), [])
        self.assertEqual(self.git(bare, 'log', '-1', '--format=%s', 'main'), 'old work')


class QueueRows(unittest.TestCase):
    def test_every_system_has_a_label(self):
        for s in runqueue.SYSTEM_ORDER:
            self.assertIn(s, runqueue.SYSTEM_LABEL)

    def test_peer_message_row(self):
        rows = peers.events({}, {'m1': ['2026-09-29T20:29', 'zipper-a', 'hello\nthere']})
        self.assertEqual(rows[0]['system'], 'zipper')
        self.assertEqual(rows[0]['who'], 'zipper-a')
        self.assertNotIn('\n', rows[0]['text'])

    def test_upstream_rows_only_for_new_commits(self):
        before = {'a' * 40: ['2026-09-29T20:00', 'dev', 'old']}
        after = dict(before, **{'b' * 40: ['2026-09-29T21:00', 'dev', 'new']})
        rows = upstream.events(before, after)
        self.assertEqual(len(rows), 1)
        self.assertIn('new', rows[0]['text'])


class CommandLine(unittest.TestCase):
    """The CLI end to end on a fresh vault, in a child with the same empty env."""

    def run_zipper(self, *args):
        return subprocess.run([sys.executable, '-m', 'zipper'] + list(args), cwd=ROOT,
                              capture_output=True, text=True, env=dict(os.environ))

    def test_init_lint_status(self):
        vault = os.environ['ZIPPER_VAULT']
        if not os.path.exists(os.path.join(vault, 'CLAUDE.md')):
            r = self.run_zipper('init', vault)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for cmd in (['lint'], ['status'], ['agenda'], ['views'], ['settings', 'check']):
            r = self.run_zipper(*cmd)
            self.assertEqual(r.returncode, 0, '%s: %s' % (cmd, r.stdout + r.stderr))


if __name__ == '__main__':
    unittest.main()
