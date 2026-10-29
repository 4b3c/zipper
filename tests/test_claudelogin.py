"""`/login` from Discord: the link goes to the thread, the code goes to the process.

    python3 -m unittest tests.test_claudelogin -v

A fake `claude auth login` stands in for the real one, so nothing here can touch
this box's credentials.
"""
import os, shutil, stat, sys, tempfile, time, unittest
from unittest import mock

TMP = tempfile.mkdtemp(prefix='zipper-claudelogin-')
for k in [k for k in os.environ if k.startswith(('ZIPPER_', 'DISCORD_', 'GITHUB_', 'BOT_'))]:
    del os.environ[k]
os.environ.update(ZIPPER_SETTINGS=os.path.join(TMP, 'settings.json'),
                  ZIPPER_ENV_FILE=os.path.join(TMP, 'env'),
                  ZIPPER_VAULT=os.path.join(TMP, 'vault'))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zipper import claudelogin   # noqa: E402

URL = 'https://claude.com/cai/oauth/authorize?code=true&state=abc'
GOOD = 'goodcode1234567890abcdef#state'
# Prints the link the way Claude Code does (an OSC 8 hyperlink), then reads a code.
FAKE = r'''#!/usr/bin/env python3
import sys
u = %r
sys.stdout.write("Opening browser to sign in\r\nIf the browser didn't open, visit: "
                 "\x1b]8;;" + u + "\x07" + u + "\x1b]8;;\x07\r\nPaste code here if prompted > ")
sys.stdout.flush()
code = sys.stdin.readline().strip()
if code == %r:
    print("Login successful.")
    sys.exit(0)
print("OAuth error: invalid code")
sys.exit(1)
''' % (URL, GOOD)
STATUS = ['python3', '-c', 'print(\'{"loggedIn": true, "email": "a@b.c"}\')']


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


class Login(unittest.TestCase):
    def setUp(self):
        self.fake = os.path.join(TMP, 'claude')
        with open(self.fake, 'w') as f:
            f.write(FAKE)
        os.chmod(self.fake, os.stat(self.fake).st_mode | stat.S_IEXEC)
        self.sent = []
        p = mock.patch.object(claudelogin, '_send',
                              lambda tid, text: self.sent.append((tid, text)))
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self._reset)

    def _reset(self):
        if claudelogin._current:
            claudelogin._current.kill()
        claudelogin._current = None

    def test_words(self):
        for t in ('/login', ' /LOGIN '):
            self.assertTrue(claudelogin.is_login(t), t)
        for t in ('login', '/login please', 'how do I /login', ''):
            self.assertFalse(claudelogin.is_login(t), t)

    def test_link_then_code_signs_in(self):
        self.assertEqual(claudelogin.start('7', [self.fake]), URL)
        self.assertIn(URL, self.sent[-1][1])
        self.assertTrue(claudelogin.takes('7', GOOD))
        self.assertTrue(claudelogin.submit('7', GOOD, status_cmd=STATUS))
        self.assertIn('Signed in as a@b.c', self.sent[-1][1])
        self.assertFalse(claudelogin.takes('7', GOOD))      # spent

    def test_bad_code_says_so(self):
        claudelogin.start('7', [self.fake])
        self.assertFalse(claudelogin.submit('7', 'wrongcode1234567890abcdef'))
        self.assertIn('Sign-in failed', self.sent[-1][1])
        self.assertIn('invalid code', self.sent[-1][1])

    def test_only_a_code_in_that_thread_is_taken(self):
        claudelogin.start('7', [self.fake])
        self.assertFalse(claudelogin.takes('8', GOOD))        # another thread
        self.assertFalse(claudelogin.takes('7', 'what is due this week?'))
        self.assertFalse(claudelogin.takes('7', 'short'))

    def test_second_login_replaces_the_first(self):
        claudelogin.start('7', [self.fake])
        first = claudelogin._current
        claudelogin.start('8', [self.fake])
        self.assertFalse(first.alive())
        self.assertIn('Replaced', [t for tid, t in self.sent if tid == '7'][-1])
        self.assertFalse(claudelogin.takes('7', GOOD))
        self.assertTrue(claudelogin.takes('8', GOOD))

    def test_expiry_kills_and_says_so(self):
        claudelogin.start('7', [self.fake])
        login = claudelogin._current
        claudelogin._expire(login)
        self.assertFalse(login.alive())
        self.assertIn('expired', self.sent[-1][1])
        self.assertFalse(claudelogin.takes('7', GOOD))

    def test_no_link_is_reported(self):
        self.assertIsNone(claudelogin.start('7', ['python3', '-c', 'print("boom")']))
        self.assertIn('printed no link', self.sent[-1][1])
        self.assertIsNone(claudelogin._current)


if __name__ == '__main__':
    unittest.main()
