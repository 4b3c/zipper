"""Wakeups survive a restart (zipper/later.py: the job lock and revive()).

    python3 -m unittest tests.test_later_revive -v
"""
import os, shutil, sys, tempfile, unittest
from unittest import mock

TMP = tempfile.mkdtemp(prefix='zipper-later-')
for k in [k for k in os.environ if k.startswith(('ZIPPER_', 'DISCORD_', 'GITHUB_', 'BOT_'))]:
    del os.environ[k]
os.environ.update(ZIPPER_SETTINGS=os.path.join(TMP, 'settings.json'),
                  ZIPPER_ENV_FILE=os.path.join(TMP, 'env'),
                  ZIPPER_VAULT=os.path.join(TMP, 'vault'))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zipper import later      # noqa: E402


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


def job(jid, **kw):
    j = {'id': jid, 'thread': '1', 'note': 'n', 'after': None, 'delay': 60,
         'due': '2026-01-01T00:00:00', 'cwd': TMP, 'set_at': '2026-11-15T15:00:00',
         'state': 'waiting', 'pid': 999999}
    j.update(kw)
    later._save(j)
    return j


class Revive(unittest.TestCase):
    def setUp(self):
        os.makedirs(later.DIR, exist_ok=True)
        for f in os.listdir(later.DIR):
            os.remove(os.path.join(later.DIR, f))
        self.spawned = []
        self.p = mock.patch.object(later, '_spawn',
                                   side_effect=lambda jid, lock: (self.spawned.append(jid),
                                                                  lock.close()))
        self.p.start()

    def tearDown(self):
        self.p.stop()

    def test_orphaned_jobs_get_a_runner_and_others_dont(self):
        job('dead1')                                   # runner gone: no lock, dead pid
        job('dead2', state='delivering')
        job('held')
        held = later._take('held')                     # a live runner holds this one
        job('done', state='delivered')
        try:
            started = later.revive()
        finally:
            held.close()
        self.assertEqual(sorted(started), ['dead1', 'dead2'])
        self.assertEqual(sorted(self.spawned), ['dead1', 'dead2'])
        d2 = later._load('dead2')
        self.assertEqual((d2['state'], d2['cut_off'], d2['revived']), ('waiting', 'delivering', 1))

    def test_a_pre_lock_runner_still_counts_while_its_process_lives(self):
        job('legacy', pid=os.getpid())                 # this process isn't `_later legacy`
        self.assertFalse(later._alive('legacy', os.getpid()))
        import subprocess
        p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(20)',
                              '_later', 'legacy'])
        import time
        time.sleep(0.5)                                # let it exec, so /proc shows its argv
        try:
            self.assertTrue(later._alive('legacy', p.pid))
        finally:
            p.kill(); p.wait()
        self.assertFalse(later._alive('legacy', p.pid))

    def test_a_new_job_is_claimed_before_its_runner_starts(self):
        # The tester's race: revive() right after schedule(), while the runner is
        # still booting. The lock goes to the child before Popen returns, so the
        # job must not look orphaned.
        self.p.stop()
        kept = []

        class Child:
            def __init__(self, argv, **kw):
                kept.extend(os.dup(fd) for fd in kw['pass_fds'])   # the child's copy

        try:
            with mock.patch('subprocess.Popen', Child):
                j = later.schedule('1', 'race', after='true')
                self.assertEqual(later.revive(), [])
                self.assertTrue(later._alive(j['id']))
                self.assertNotIn('revived_at', later._load(j['id']))
        finally:
            for fd in kept:
                os.close(fd)
            self.p.start()
        self.assertFalse(later._alive(j['id']))            # child gone, lock free

    def test_runner_uses_the_lock_it_was_handed(self):
        job('handed')
        lock = later._take('handed')
        fd = os.dup(lock.fileno())
        lock.close()
        with mock.patch.dict(os.environ, {later.LOCK_FD: str(fd)}), \
             mock.patch.object(later, '_load', side_effect=lambda jid: {'state': 'done'}):
            self.assertEqual(later.run('handed'), 0)        # didn't exit as "taken"
            self.assertNotIn(later.LOCK_FD, os.environ)      # not passed to the command

    def test_second_runner_exits_when_the_lock_is_taken(self):
        job('busy')
        held = later._take('busy')
        try:
            self.assertEqual(later.run('busy'), 0)
            self.assertEqual(later._load('busy')['state'], 'waiting')   # untouched
        finally:
            held.close()

    def test_revived_after_job_is_not_rerun_and_says_so(self):
        from zipper import conversations, convhead
        job('wait', after='touch %s' % os.path.join(TMP, 'ran'), delay=None, due=None,
            revived_at='2026-11-15T15:50:00', cut_off='waiting')
        done = {'ok': True, 'echoed': True}
        with mock.patch.object(conversations, 'note_delivery'), \
             mock.patch.object(convhead, 'deliver', return_value=done) as d:
            self.assertEqual(later.run('wait'), 0)
        self.assertFalse(os.path.exists(os.path.join(TMP, 'ran')))
        self.assertIn('cut off by a restart', d.call_args[0][1])

    def test_cut_off_delivery_warns_it_may_repeat(self):
        m = later.message({'set_at': 's', 'note': 'n', 'after': None,
                           'revived_at': 'r', 'cut_off': 'delivering'})
        self.assertIn('may already have run once', m)


if __name__ == '__main__':
    unittest.main()
