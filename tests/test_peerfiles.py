"""Peers: files ride along with a message, and a message reaches on_peer_message.

    python3 -m unittest tests.test_peerfiles -v
"""
import base64, os, shutil, sys, tempfile, unittest

TMP = tempfile.mkdtemp(prefix='zipper-peerfiles-')
for k in [k for k in os.environ if k.startswith(('ZIPPER_', 'DISCORD_', 'GITHUB_', 'BOT_'))]:
    del os.environ[k]
os.environ.update(ZIPPER_SETTINGS=os.path.join(TMP, 'settings.json'),
                  ZIPPER_ENV_FILE=os.path.join(TMP, 'env'),
                  ZIPPER_VAULT=os.path.join(TMP, 'vault'),
                  ZIPPER_COMMS_TOKEN='tok')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from zipper import settings                                         # noqa: E402
from plugins import peers                                           # noqa: E402


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


def b64(data):
    return base64.b64encode(data).decode()


class PeerFiles(unittest.TestCase):
    def setUp(self):
        os.makedirs(os.path.join(TMP, 'vault', 'Inbox'), exist_ok=True)
        settings.put('plugins.peers.zippers', {'studio': 'http://studio:8898'})
        settings.put('plugins.peers.notify', False)
        shutil.rmtree(peers.FILES, ignore_errors=True)

    def post(self, **kw):
        body = {'from': 'studio', 'token': 'tok', 'text': 'frames'}
        body.update(kw)
        return peers.receive(body)

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


if __name__ == '__main__':
    unittest.main()
