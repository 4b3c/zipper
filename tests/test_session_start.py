"""The SessionStart hook: a new conversation starts with the queue in context."""
import importlib.util, io, json, os, shutil, sys, tempfile, unittest
from unittest import mock

TMP = tempfile.mkdtemp(prefix='zipper-session-start-')
for k in [k for k in os.environ if k.startswith(('ZIPPER_', 'DISCORD_', 'GITHUB_', 'BOT_'))]:
    del os.environ[k]
os.environ.update(ZIPPER_SETTINGS=os.path.join(TMP, 'settings.json'),
                  ZIPPER_ENV_FILE=os.path.join(TMP, 'env'),
                  ZIPPER_VAULT=os.path.join(TMP, 'vault'))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from zipper import setup   # noqa: E402

spec = importlib.util.spec_from_file_location(
    'session_start', os.path.join(ROOT, 'hooks', 'session_start.py'))
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


class Context(unittest.TestCase):
    def setUp(self):
        self.vault = os.environ['ZIPPER_VAULT']
        os.makedirs(os.path.join(self.vault, 'Meta'), exist_ok=True)
        self.q = os.path.join(self.vault, 'Meta', 'Queue.md')
        with open(self.q, 'w') as fh:
            fh.write('# Queue\n\n- slack: timesheets due at 5:00 today\n')

    def tearDown(self):
        if os.path.exists(self.q):
            os.remove(self.q)

    def _run(self, event):
        out = io.StringIO()
        with mock.patch('sys.stdin', io.StringIO(json.dumps(event))), \
                mock.patch('sys.stdout', out):
            self.assertEqual(hook.main(), 0)
        return out.getvalue()

    def test_new_session_gets_the_queue(self):
        for source in ('startup', 'clear'):
            got = json.loads(self._run({'source': source}))['hookSpecificOutput']
            self.assertEqual(got['hookEventName'], 'SessionStart')
            self.assertIn('timesheets due at 5:00', got['additionalContext'])

    def test_resumed_turns_do_not_repeat_it(self):
        # Every Discord turn after the first is a --resume.
        for source in ('resume', 'compact'):
            self.assertEqual(self._run({'source': source}), '')

    def test_no_queue_is_silent(self):
        os.remove(self.q)
        self.assertEqual(self._run({'source': 'startup'}), '')

    def test_bad_input_still_exits_clean(self):
        out = io.StringIO()
        with mock.patch('sys.stdin', io.StringIO('not json')), mock.patch('sys.stdout', out):
            self.assertEqual(hook.main(), 0)

    def test_long_queue_is_cut(self):
        with open(self.q, 'w') as fh:
            fh.write('x' * (hook.LIMIT + 5000))
        ctx = hook.context({'source': 'startup'})
        self.assertLess(len(ctx), hook.LIMIT + 1000)
        self.assertIn('cut at', ctx)


class Install(unittest.TestCase):
    def test_installs_both_hooks_once_and_keeps_the_rest(self):
        p = os.path.join(TMP, 'claude-settings.json')
        with open(p, 'w') as fh:
            json.dump({'model': 'x', 'hooks': {'Stop': [{'hooks': [
                {'type': 'command', 'command': 'python3 /old/hooks/forward_reply.py'}]}]}}, fh)
        setup.install_hook(p)
        setup.install_hook(p)
        with open(p) as fh:
            data = json.load(fh)
        self.assertEqual(data['model'], 'x')
        stop = [h for g in data['hooks']['Stop'] for h in g['hooks']]
        start = [h for g in data['hooks']['SessionStart'] for h in g['hooks']]
        self.assertEqual(len(stop), 1)
        self.assertEqual(len(start), 1)
        self.assertEqual(stop[0]['command'], 'python3 %s' % setup.HOOK)
        self.assertEqual(start[0]['command'], 'python3 %s' % setup.START_HOOK)


if __name__ == '__main__':
    unittest.main()
