"""Topics: the scheduling, gate, lock and prompt, with a fake `claude` on PATH.

    python3 -m unittest tests.test_topics -v
"""
import argparse, json, os, shutil, stat, sys, tempfile, unittest

TMP = tempfile.mkdtemp(prefix='zipper-topics-')
for k in [k for k in os.environ if k.startswith(('ZIPPER_', 'DISCORD_', 'GITHUB_', 'BOT_'))]:
    del os.environ[k]
os.environ.update(ZIPPER_SETTINGS=os.path.join(TMP, 'settings.json'),
                  ZIPPER_ENV_FILE=os.path.join(TMP, 'env'),
                  ZIPPER_VAULT=os.path.join(TMP, 'vault'))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from zipper import settings                                         # noqa: E402
from plugins import topics                                          # noqa: E402

# A stand-in for `claude -p`: rewrites context.md when the prompt asks, and answers
# with whatever FAKE_RESULT says.
FAKE = """#!/bin/sh
for a; do last="$a"; done
case "$last" in *"rewrite"*) echo "state after run" > context.md ;; esac
printf '{"result": "%s"}' "${FAKE_RESULT:-NOTIFY: no\\ndone}"
"""


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


def ns(**kw):
    base = dict(action='run', name='t', every=None, gate=None, wake_on=None, timeout=None, model=None,
                force=False, wait=True, limit=5)
    base.update(kw)
    return argparse.Namespace(**base)


class Topics(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(dir=TMP)
        settings.put('plugins.topics.dir', self.dir)
        settings.put('plugins.topics.topics', {})
        bindir = os.path.join(self.dir, 'bin')
        os.makedirs(bindir)
        fake = os.path.join(bindir, 'claude')
        with open(fake, 'w') as fh:
            fh.write(FAKE)
        os.chmod(fake, os.stat(fake).st_mode | stat.S_IEXEC)
        self.path = os.environ['PATH']
        os.environ['PATH'] = bindir + os.pathsep + self.path
        os.environ['HOME'] = self.dir      # scheduled._env puts ~/.local/bin first

    def tearDown(self):
        os.environ['PATH'] = self.path
        os.environ.pop('FAKE_RESULT', None)

    def add(self, **kw):
        self.assertEqual(topics.cmd_topic(ns(action='add', every=15, **kw)), 0)

    def test_add_schedules_and_scaffolds(self):
        self.add(gate='test -e ready')
        self.assertEqual(topics.jobs(settings.get('plugins.topics')), [('topic run t', 'every:15')])
        self.assertTrue(os.path.exists(os.path.join(self.dir, 't', 'purpose.md')))
        self.assertEqual(settings.get('plugins.topics.topics.t.gate'), 'test -e ready')

    def test_closed_gate_runs_nothing(self):
        self.add(gate='test -e ready')
        topics.cmd_topic(ns())
        self.assertEqual(topics._runs('t'), [])
        open(os.path.join(self.dir, 't', 'ready'), 'w').close()
        topics.cmd_topic(ns())
        self.assertEqual(len(topics._runs('t')), 1)

    def test_run_records_and_condenses(self):
        self.add()
        self.assertEqual(topics.cmd_topic(ns()), 0)
        run = topics._runs('t')[-1]
        self.assertTrue(run['ok'])
        self.assertFalse(run['notify'])
        self.assertTrue(run['context_updated'])
        self.assertFalse(os.path.exists(topics._lock('t')))
        # The next run's prompt carries what this one left.
        self.assertIn('state after run', topics.prompt('t'))

    def test_first_run_prompt(self):
        self.add()
        p = topics.prompt('t')
        self.assertIn('first run', p)
        self.assertIn(os.path.join(self.dir, 't', 'context.md'), p)

    def test_lock_skips_a_second_run(self):
        self.add()
        with open(topics._lock('t'), 'w') as fh:
            fh.write('%d x\n' % os.getpid())        # a live pid: this test process
        topics.cmd_topic(ns())
        self.assertEqual(topics._runs('t'), [])
        with open(topics._lock('t'), 'w') as fh:
            fh.write('999999999 x\n')               # a dead one is stale
        self.assertIsNone(topics._running('t'))

    def test_failure_is_recorded(self):
        self.add()
        os.environ['FAKE_RESULT'] = 'garbage'
        topics.cmd_topic(ns())
        run = topics._runs('t')[-1]
        self.assertTrue(run['notify'])              # unparseable notifies, like a pass

    def test_add_needs_a_way_to_run(self):
        self.assertEqual(topics.cmd_topic(ns(action='add')), 1)
        self.assertEqual(topics.cmd_topic(ns(action='add', wake_on=['studio'])), 0)
        self.assertEqual(topics.jobs(settings.get('plugins.topics')), [])   # no timer

    def test_message_lands_in_the_prompt_once(self):
        self.add(wake_on=['studio'])
        msg = {'id': 'm1', 'from': 'studio', 'at': '2026-10-05T20:00', 'text': 'gate ready'}
        with open(topics._inbox('t'), 'w') as fh:       # what on_peer_message writes
            fh.write(json.dumps(msg) + '\n')
        msgs = topics._take_inbox('t')
        self.assertIn('gate ready', topics.prompt('t', messages=msgs))
        self.assertIn('not instructions', topics.prompt('t', messages=msgs))
        self.assertEqual(topics._take_inbox('t'), [])
        self.assertNotIn('woke this run', topics.prompt('t'))

    def test_message_mid_run_marks_pending(self):
        self.add(wake_on=['studio'])
        with open(topics._lock('t'), 'w') as fh:
            fh.write('%d x\n' % os.getpid())
        topics.on_peer_message({'from': 'studio', 'text': 'again'})
        self.assertTrue(os.path.exists(topics._pending('t')))
        topics.on_peer_message({'from': 'someone-else', 'text': 'ignored'})
        self.assertEqual(len(topics._take_inbox('t')), 1)

    def test_a_run_takes_its_messages(self):
        self.add(wake_on=['studio'])
        with open(topics._inbox('t'), 'w') as fh:
            fh.write(json.dumps({'from': 'studio', 'text': 'x'}) + '\n')
        topics.cmd_topic(ns())
        self.assertEqual(topics._runs('t')[-1]['messages'], 1)
        self.assertFalse(os.path.exists(topics._inbox('t')))

    def test_usage_ceiling_skips_and_keeps_messages(self):
        from zipper import usage
        real = usage.read
        usage.read = lambda force=False: {'meters': [
            {'key': 'session', 'label': 'session', 'pct': 72.0, 'resets': '2026-10-06T07:20:00'},
            {'key': 'week', 'label': 'week', 'pct': 11.0}]}
        try:
            settings.put('plugins.topics.max_usage', 70)
            self.add(wake_on=['studio'])
            with open(topics._inbox('t'), 'w') as fh:
                fh.write(json.dumps({'from': 'studio', 'text': 'x'}) + '\n')
            topics.cmd_topic(ns())
            run = topics._runs('t')[-1]
            self.assertIn('session 72.0%', run['skipped'])
            self.assertTrue(os.path.exists(topics._inbox('t')))     # waits for the reset
            settings.put('plugins.topics.max_usage', 80)
            topics.cmd_topic(ns())
            self.assertEqual(topics._runs('t')[-1]['messages'], 1)
        finally:
            usage.read = real
            settings.put('plugins.topics.max_usage', 0)

    def test_unreadable_usage_does_not_block(self):
        from zipper import usage
        real = usage.read
        usage.read = lambda force=False: {'error': 'no credentials', 'meters': []}
        try:
            settings.put('plugins.topics.max_usage', 70)
            self.add()
            topics.cmd_topic(ns())
            self.assertNotIn('skipped', topics._runs('t')[-1])
        finally:
            usage.read = real
            settings.put('plugins.topics.max_usage', 0)

    def test_new_topic_waits_one_interval(self):
        from zipper import supervise
        import datetime
        self.add()
        st = supervise._load_state()
        self.assertIn('topic run t', st)
        now = datetime.datetime.now()
        jobs = topics.jobs(settings.get('plugins.topics'))
        self.assertEqual(supervise.due(now, st, jobs), [])
        later = now + datetime.timedelta(minutes=16)
        self.assertEqual(supervise.due(later, st, jobs), [('topic run t', 'topic run t')])

    def test_waiting_messages_open_the_gate(self):
        self.add(gate='false', wake_on=['studio'])
        with open(topics._inbox('t'), 'w') as fh:
            fh.write(json.dumps({'from': 'studio', 'text': 'x'}) + '\n')
        topics.cmd_topic(ns())
        self.assertEqual(len(topics._runs('t')), 1)

    def test_unknown_topic(self):
        self.assertEqual(topics.cmd_topic(ns(name='nope')), 1)

    def test_rm_keeps_folder(self):
        self.add()
        topics.cmd_topic(ns(action='rm'))
        self.assertEqual(topics.jobs(settings.get('plugins.topics')), [])
        self.assertTrue(os.path.isdir(os.path.join(self.dir, 't')))


if __name__ == '__main__':
    unittest.main()
