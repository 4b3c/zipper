"""Closing a task with a reason from the dashboard (zipper/web/closewhy.py, data.close_task).

    python3 -m unittest tests.test_closewhy -v
"""
import os, shutil, sys, tempfile, unittest

TMP = tempfile.mkdtemp(prefix='zipper-closewhy-')
for k in [k for k in os.environ if k.startswith(('ZIPPER_', 'DISCORD_', 'GITHUB_', 'BOT_'))]:
    del os.environ[k]
os.environ.update(ZIPPER_SETTINGS=os.path.join(TMP, 'settings.json'),
                  ZIPPER_ENV_FILE=os.path.join(TMP, 'env'),
                  ZIPPER_VAULT=os.path.join(TMP, 'vault'))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zipper import core                     # noqa: E402
from zipper.web import closewhy, data, feed  # noqa: E402


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


TASKS = """# Tasks

- [ ] Decide where the spring lab hours go [project:: [[Luminosity Lab]]] [due:: 2026-12-01]
  Mini Charlotte or Orbitscape.
  Second description line.
- [ ] Print the keychains [project:: [[TapTech Studio]]]
"""


class CloseWithReason(unittest.TestCase):
    def setUp(self):
        self.dir = os.path.join(core.VAULT, 'Tasks')
        os.makedirs(self.dir, exist_ok=True)
        self.path = os.path.join(self.dir, 'Main.md')
        open(self.path, 'w').write(TASKS)
        self.rows = []
        self._pub = feed.publish
        feed.publish = lambda kind, text='', **kw: self.rows.append((kind, text, kw))
        title = 'Decide where the spring lab hours go'
        self.key = 'task:Luminosity Lab|' + title     # the dashboard's key shape

    def tearDown(self):
        feed.publish = self._pub

    def test_ticks_writes_reason_under_description_and_queues(self):
        r = data.close_task(self.key, '  Mini Charlotte will take all my\nhours  ')
        self.assertTrue(r['ok'], r)
        lines = open(self.path).read().split('\n')
        self.assertTrue(lines[2].startswith('- [x] Decide where the spring lab hours go'))
        self.assertEqual(lines[4], '  Second description line.')
        self.assertEqual(lines[5], '  Closed %s: Mini Charlotte will take all my hours'
                         % core.TODAY.isoformat())
        self.assertTrue(lines[6].startswith('- [ ] Print the keychains'))
        self.assertEqual(len(self.rows), 1)
        kind, text, kw = self.rows[0]
        self.assertEqual((kind, kw['system']), ('diff', 'tasks'))
        self.assertIn('Decide where the spring lab hours go', text)
        self.assertIn('Mini Charlotte will take all my hours', text)

    def test_empty_reason_changes_nothing(self):
        r = data.close_task(self.key, '   ')
        self.assertFalse(r['ok'])
        self.assertEqual(open(self.path).read(), TASKS)
        self.assertEqual(self.rows, [])

    def test_already_ticked_is_refused(self):
        self.assertTrue(data.close_task(self.key, 'first')['ok'])
        self.assertFalse(data.close_task(self.key, 'second')['ok'])
        self.assertEqual(open(self.path).read().count('Closed '), 1)

    def test_form_only_on_open_writable_tasks(self):
        self.assertIn('closewhy', closewhy.close_form({'key': 'task:X|y'}))
        self.assertEqual(closewhy.close_form({'key': 'task:X|y', 'done': True}), '')
        self.assertEqual(closewhy.close_form({'key': 'task:X|y', 'readonly': True}), '')
        self.assertEqual(closewhy.close_form({'key': 'canvas:123'}), '')


if __name__ == '__main__':
    unittest.main()
