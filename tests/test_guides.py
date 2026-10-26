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
        # A guide is shared by every zipper, so it can't lean on one vault's layout:
        # an owner's folders and notes belong in their own guide note.
        for name in plugins.manifests():
            p = os.path.join(plugins.DIR, name, 'GUIDE.md')
            if os.path.isfile(p):
                text = open(p, encoding='utf-8').read()
                self.assertTrue(text.startswith('# '), name)
                for word in ('Meta/Systems', 'People/', '[['):
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

    def test_a_pulled_row_keeps_its_plugin_into_the_brief(self):
        # The live path: a plugin's pull publishes its rows into Inbox/feed.json and
        # the brief reads them back. A `zipper` row only maps to its guide by the tag.
        from zipper import serve

        class Stub:
            name = 'upstream'
            state = 0
            def pull(self): Stub.state += 1
            def snapshot(self): return Stub.state
            def events(self, before, after):
                return [{'system': 'zipper', 'action': 'push', 'text': 'merged  a fix %d' % after}]

        real = plugins.get
        plugins.get = lambda n: Stub() if n == 'upstream' else real(n)
        try:
            self.assertEqual(plugins.pull('upstream')[0], 1)
        finally:
            plugins.get = real
        rows = [r for r in serve.feed_rows() if r['text'].startswith('merged  a fix')]
        self.assertEqual([r.get('plugin') for r in rows], ['upstream'])
        runqueue.cmd_brief(None)
        brief = open(os.path.join(TMP, 'vault', 'Meta', 'Queue.md'), encoding='utf-8').read()
        self.assertIn('`zipper plugin guide upstream`', brief)

    def test_publish_keeps_the_tag_from_the_batch_path(self):
        from zipper import serve
        serve.publish('diff', 'hello from zipper-9', system='zipper', action='add',
                      who='zipper-9', plugin='peers')
        row = [r for r in serve.feed_rows() if r['text'] == 'hello from zipper-9'][0]
        self.assertEqual(row['plugin'], 'peers')


if __name__ == '__main__':
    unittest.main()
