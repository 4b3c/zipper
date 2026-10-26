"""Plugin guides: GUIDE.md, the owner's note, and the brief pointing at both.

    python3 -m unittest tests.test_guides -v
"""
import os, shutil, sys, tempfile, unittest

TMP = tempfile.mkdtemp(prefix='zipper-guides-')
for k in [k for k in os.environ if k.startswith(('ZIPPER_', 'DISCORD_', 'GITHUB_', 'BOT_'))]:
    del os.environ[k]
os.environ.update(ZIPPER_SETTINGS=os.path.join(TMP, 'settings.json'),
                  ZIPPER_ENV_FILE=os.path.join(TMP, 'env'),
                  ZIPPER_VAULT=os.path.join(TMP, 'vault'))
os.makedirs(os.path.join(TMP, 'vault', 'Meta'), exist_ok=True)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from zipper import plugins, runqueue, settings   # noqa: E402


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


class Guides(unittest.TestCase):
    def setUp(self):
        if os.path.exists(settings.PATH):
            os.remove(settings.PATH)

    def test_every_guide_is_generic(self):
        # A guide is public and shared by every zipper: no owner's names in it.
        for name in plugins.manifests():
            p = os.path.join(plugins.DIR, name, 'GUIDE.md')
            if os.path.isfile(p):
                text = open(p, encoding='utf-8').read()
                self.assertTrue(text.startswith('# '), name)
                for word in ('Abram', 'ASU', 'Pantry'):
                    self.assertNotIn(word, text, '%s guide names %s' % (name, word))

    def test_guide_is_the_plugins_then_the_owners(self):
        self.assertIn('zipper gmail <id>', plugins.guide('gmail'))
        self.assertIsNone(plugins.guide('backup'))
        note = os.path.join(TMP, 'vault', 'Meta', 'Mail rules.md')
        with open(note, 'w', encoding='utf-8') as fh:
            fh.write('---\ntype: reference\nstatus: living\n---\n\nFile school mail under School.\n')
        settings.put('plugins.gmail.guide', 'Meta/Mail rules.md')
        text = plugins.guide('gmail')
        self.assertLess(text.index('zipper gmail <id>'), text.index('File school mail'))
        self.assertNotIn('type: reference', text)
        self.assertEqual(settings.check(), [])          # `guide` is a key every plugin takes

    def test_a_missing_owner_note_is_said_not_skipped(self):
        settings.put('plugins.backup.guide', 'Meta/Nope.md')
        self.assertIn('does not exist', plugins.guide('backup'))

    def test_the_command(self):
        class A:
            action, name = 'guide', 'canvas'
        self.assertEqual(plugins.cmd_plugin(A), 0)
        A.name = 'backup'
        self.assertEqual(plugins.cmd_plugin(A), 1)

    def test_row_plugin(self):
        self.assertEqual(plugins.row_plugin({'system': 'zipper', 'plugin': 'peers'}), 'peers')
        self.assertEqual(plugins.row_plugin({'system': 'gmail'}), 'gmail')   # older rows
        self.assertIsNone(plugins.row_plugin({'system': 'zipper'}))
        self.assertIsNone(plugins.row_plugin({'system': 'vault'}))

    def test_brief_names_only_the_guides_its_rows_need(self):
        q = {'generated': 'now', 'tasks_dropped': [], 'tasks_renamed': [], 'flags': [],
             'notes_uncommitted': [], 'guides': ['gmail', 'upstream'],
             'events': [{'system': 'gmail', 'plugin': 'gmail', 'text': 'mail'},
                        {'system': 'zipper', 'plugin': 'upstream', 'text': 'merged'},
                        {'system': 'zipper', 'plugin': 'peers', 'text': 'hi'}]}
        runqueue._write_brief(q)
        brief = open(os.path.join(TMP, 'vault', 'Meta', 'Queue.md'), encoding='utf-8').read()
        self.assertIn('`zipper plugin guide gmail`', brief)
        self.assertIn('`zipper plugin guide upstream`', brief)
        self.assertNotIn('guide peers', brief)          # not in q['guides']
        self.assertNotIn('guide canvas', brief)         # no rows
        gm = brief.index('### Gmail')
        self.assertLess(gm, brief.index('guide gmail'))
        self.assertLess(brief.index('guide gmail'), brief.index('- mail'))


if __name__ == '__main__':
    unittest.main()
