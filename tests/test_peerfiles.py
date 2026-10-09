"""Peers: the sender check, files riding along, on_peer_message, and the reply turn's wake.

    python3 -m unittest tests.test_peerfiles -v
"""
import base64, os, shutil, sys, tempfile, unittest

TMP = tempfile.mkdtemp(prefix='zipper-peerfiles-')
for k in [k for k in os.environ if k.startswith(('ZIPPER_', 'DISCORD_', 'GITHUB_', 'BOT_'))]:
    del os.environ[k]
os.environ.update(ZIPPER_SETTINGS=os.path.join(TMP, 'settings.json'),
                  ZIPPER_ENV_FILE=os.path.join(TMP, 'env'),
                  ZIPPER_VAULT=os.path.join(TMP, 'vault'))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from zipper import settings                                         # noqa: E402
from plugins import peers                                           # noqa: E402
from plugins.peers import turn                                      # noqa: E402

SPAWNED = []
turn._spawn = SPAWNED.append                # no test starts a real Claude


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


def b64(data):
    return base64.b64encode(data).decode()


class PeerFiles(unittest.TestCase):
    def setUp(self):
        os.makedirs(os.path.join(TMP, 'vault', 'Inbox'), exist_ok=True)
        settings.put('plugins.peers.zippers', {'studio': 'http://127.0.0.1:8898'})
        settings.put('plugins.peers.notify', False)
        shutil.rmtree(peers.FILES, ignore_errors=True)
        shutil.rmtree(turn._dir(), ignore_errors=True)
        del SPAWNED[:]

    def post(self, source='127.0.0.1', **kw):
        body = {'from': 'studio', 'text': 'frames'}
        body.update(kw)
        return peers.receive(body, source=source)

    def test_file_is_saved_and_pointed_at(self):
        res = self.post(files=[{'name': 'sheet.png', 'data': b64(b'\x89PNG...')}])
        self.assertEqual(res['files'], 1)
        text = peers._load()['messages'][-1]['text']
        self.assertIn('attached file saved here: ', text)
        path = text.split('saved here: ')[1].strip()
        self.assertEqual(open(path, 'rb').read(), b'\x89PNG...')
        self.assertTrue(path.startswith(peers.FILES))

    def test_names_cannot_escape(self):
        res = self.post(files=[{'name': '../../evil.sh', 'data': b64(b'x')}])
        path = peers._load()['messages'][-1]['text'].split('saved here: ')[1].strip()
        self.assertEqual(os.path.basename(path), 'evil.sh')
        self.assertTrue(os.path.realpath(path).startswith(os.path.realpath(peers.FILES)))
        self.assertEqual(res['files'], 1)
        with self.assertRaises(ValueError):
            self.post(files=[{'name': '.hidden', 'data': b64(b'x')}])

    def test_same_names_do_not_overwrite(self):
        self.post(files=[{'name': 'a.png', 'data': b64(b'one')}, {'name': 'a.png', 'data': b64(b'two')}])
        text = peers._load()['messages'][-1]['text']
        paths = [l.split('saved here: ')[1] for l in text.splitlines() if 'saved here' in l]
        self.assertEqual([os.path.basename(p) for p in paths], ['a.png', 'a-2.png'])
        self.assertEqual(open(paths[1], 'rb').read(), b'two')

    def test_bad_base64_and_size_refused(self):
        with self.assertRaises(ValueError):
            self.post(files=[{'name': 'a.bin', 'data': 'not base64!!'}])
        old = peers.MAX_FILES_BYTES
        peers.MAX_FILES_BYTES = 10
        try:
            with self.assertRaises(ValueError):
                self.post(files=[{'name': 'a.bin', 'data': b64(b'x' * 11)}])
        finally:
            peers.MAX_FILES_BYTES = old

    def test_file_only_message_is_fine(self):
        self.post(text='', files=[{'name': 'a.txt', 'data': b64(b'hi')}])
        with self.assertRaises(ValueError):
            self.post(text='')

    def test_old_folders_are_pruned(self):
        old = peers.KEEP_FILES
        peers.KEEP_FILES = 2
        try:
            for i in range(4):
                self.post(files=[{'name': 'f%d.txt' % i, 'data': b64(b'x')}])
            self.assertEqual(len(os.listdir(peers.FILES)), 2)
        finally:
            peers.KEEP_FILES = old

    def test_hook_sees_the_message(self):
        seen = []

        class Fake:
            name = 'fake'
            on_peer_message = staticmethod(seen.append)
        from zipper import plugins
        real = plugins.enabled
        plugins.enabled = lambda: [Fake]
        try:
            self.post(text='wake up')
        finally:
            plugins.enabled = real
        self.assertEqual(seen[0]['text'], 'wake up')



class Sender(unittest.TestCase):
    """No password: a message must come from the address its sender's name resolves to."""
    def setUp(self):
        PeerFiles.setUp(self)

    post = PeerFiles.post

    def test_from_its_own_address(self):
        self.assertTrue(self.post()['ok'])

    def test_from_elsewhere_refused(self):
        with self.assertRaises(ValueError):
            self.post(source='10.9.9.9')

    def test_no_source_refused(self):
        with self.assertRaises(ValueError):
            self.post(source=None)

    def test_unknown_sender_refused(self):
        with self.assertRaises(ValueError):
            self.post(**{'from': 'stranger'})

    def test_unresolvable_peer_refused(self):
        settings.put('plugins.peers.zippers', {'studio': 'http://no-such-host.invalid:8898'})
        with self.assertRaises(ValueError):
            self.post()


class Wake(unittest.TestCase):
    """A message wakes one reply; a reply wakes nothing."""
    def setUp(self):
        PeerFiles.setUp(self)

    post = PeerFiles.post

    def pending(self):
        p = turn._path('studio', 'pending.jsonl')
        return open(p).read().splitlines() if os.path.exists(p) else []

    def test_message_wakes_a_turn(self):
        self.post(text='what is due this week?')
        self.assertEqual(SPAWNED, ['studio'])
        self.assertEqual(len(self.pending()), 1)

    def test_reply_wakes_nothing(self):
        self.post(text='here you go', reply=True)
        self.assertEqual(SPAWNED, [])
        self.assertEqual(self.pending(), [])
        self.assertTrue(peers._load()['messages'][-1]['reply'])

    def test_answer_off(self):
        settings.put('plugins.peers.answer', False)
        try:
            self.post()
        finally:
            settings.put('plugins.peers.answer', True)
        self.assertEqual(SPAWNED, [])

    def test_take_empties_the_file(self):
        self.post(text='one')
        self.post(text='two')
        self.assertEqual([m['text'] for m in turn._take('studio')], ['one', 'two'])
        self.assertEqual(turn._take('studio'), [])

    def test_turn_cannot_act(self):
        a = turn.argv('claude', 'sid', False)
        deny = a[a.index('--disallowedTools') + 1:a.index('--session-id')]
        for tool in ('Bash', 'Edit', 'Write', 'WebFetch'):
            self.assertIn(tool, deny)
        self.assertIn('--strict-mcp-config', a)
        allow = a[a.index('--allowedTools') + 1:a.index('--disallowedTools')]
        self.assertTrue(all(x.startswith(('Read(/', 'Grep(/', 'Glob(/')) for x in allow))


if __name__ == '__main__':
    unittest.main()
