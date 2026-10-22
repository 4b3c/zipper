"""The dashboard's sign-in form: the cookie, the credential check, the redirect target.

    python3 -m unittest tests.test_login -v
"""
import os, shutil, subprocess, sys, tempfile, unittest

TMP = tempfile.mkdtemp(prefix='zipper-login-')
for k in [k for k in os.environ if k.startswith(('ZIPPER_', 'DISCORD_', 'GITHUB_', 'BOT_'))]:
    del os.environ[k]
os.environ.update(ZIPPER_SETTINGS=os.path.join(TMP, 'settings.json'),
                  ZIPPER_ENV_FILE=os.path.join(TMP, 'env'),
                  ZIPPER_VAULT=os.path.join(TMP, 'vault'))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from zipper.web import login   # noqa: E402


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


def cred(value):
    with open(os.environ['ZIPPER_ENV_FILE'], 'w') as fh:
        fh.write('ZIPPER_TERM_CRED=%s\n' % value if value else '')


class Login(unittest.TestCase):
    def setUp(self):
        cred('abe:hunter2')

    def test_check(self):
        self.assertTrue(login.check('abe', 'hunter2'))
        self.assertFalse(login.check('abe', 'hunter3'))
        self.assertFalse(login.check('bob', 'hunter2'))
        self.assertFalse(login.check('abe', 'ünïcode'))

    def test_cookie_round_trip_and_expiry(self):
        c = 'other=1; %s=%s' % (login.COOKIE, login.make(now=1000))
        self.assertTrue(login.valid(c, now=2000))
        self.assertFalse(login.valid(c, now=1000 + login.MAX_AGE + 1))
        self.assertFalse(login.valid('%s=abe:99999999999:forged' % login.COOKIE))
        self.assertFalse(login.valid(''))

    def test_changing_the_password_signs_everyone_out(self):
        c = '%s=%s' % (login.COOKIE, login.make())
        cred('abe:new-password')
        self.assertFalse(login.valid(c))

    def test_no_credential_means_no_session(self):
        cred('')
        self.assertFalse(login.check('', ''))
        self.assertFalse(login.valid('%s=:1:x' % login.COOKIE))

    def test_cookie_is_https_only(self):
        for c in (login.set_cookie(login.make()), login.set_cookie('', 0)):
            for flag in ('HttpOnly', 'Secure', 'SameSite=Lax', 'Path=/'):
                self.assertIn(flag, c)

    def test_next_stays_on_this_site(self):
        self.assertEqual(login.safe_next('/views?x=1&y=2'), '/views?x=1&y=2')
        self.assertEqual(login.safe_next('/p/Café'), '/p/Caf%C3%A9')     # ASCII only in the header
        for bad in ('//evil.com', 'https://evil.com', '/\\evil.com', '%2F%2Fevil.com', '',
                    '/\t/evil.com', '/%09/evil.com', '/%0a/evil.com', '/\n/evil.com',
                    '/x%0d%0aSet-Cookie:%20pwn=1', '/x\r\nSet-Cookie: pwn=1', '/%7f/x',
                    'javascript:alert(1)', '/%5C%5Cevil.com'):
            self.assertEqual(login.safe_next(bad), '/', bad)

    def test_form_is_one_a_password_manager_reads(self):
        page = login.page('/views', 'Wrong')
        for attr in ('<form method="post" action="/login">', 'name="username"',
                     'autocomplete="username"', 'type="password"',
                     'autocomplete="current-password"', 'value="/views"'):
            self.assertIn(attr, page)
        self.assertNotIn('<script', login.page('"><script>alert(1)</script>'))


class Nginx(unittest.TestCase):
    def conf(self, public=None):
        from zipper import settings
        settings.put('plugins.dashboard.enabled', True)
        settings.put('plugins.dashboard.public_url', public or '')
        env = dict(os.environ, ZIPPER_TERM_CRED='abe:pw', PYTHONPATH=ROOT)
        src = open(os.path.join(ROOT, 'docker', 'nginx.py')).read()
        src = src.replace("sys.path.insert(0, '/zipper/code')", '').replace(
            '/etc/nginx/zipper.htpasswd', os.path.join(TMP, 'htpasswd'))
        return subprocess.run([sys.executable, '-c', src], env=env, capture_output=True,
                              text=True, cwd=TMP).stdout

    def test_plain_http_redirect_is_opt_in(self):
        self.assertNotIn('$zipper_plain) {', self.conf())
        conf = self.conf('https://box.example.ts.net:8443/')
        self.assertIn('if ($zipper_plain) { return 302 https://box.example.ts.net:8443$request_uri; }', conf)
        # tailscale serve's forwarded HTTPS and the box's own loopback are never redirected
        m = conf.split('$zipper_plain {')[1].split('}')[0]
        self.assertIn('"~^https "', m)
        self.assertIn('127\\.0\\.0\\.1$', m)
        self.assertNotIn('$zipper_plain) {', self.conf('http://box:8900'))   # only an https target

    def test_config_redirects_and_keeps_extension_open(self):
        env = dict(os.environ, ZIPPER_TERM_CRED='abe:pw', PYTHONPATH=ROOT)
        src = open(os.path.join(ROOT, 'docker', 'nginx.py')).read()
        src = src.replace("sys.path.insert(0, '/zipper/code')", '').replace(
            '/etc/nginx/zipper.htpasswd', os.path.join(TMP, 'htpasswd'))
        conf = subprocess.run([sys.executable, '-c', src], env=env, capture_output=True,
                              text=True, cwd=TMP).stdout
        if 'listen' not in conf:
            self.skipTest('dashboard plugin off in this environment')
        self.assertIn('satisfy any;', conf)
        self.assertIn('absolute_redirect off;', conf)
        # ttyd checks the same credential; nginx answers it for a cookie session
        term = conf.split('location ~ ^/t/')[1].split('}')[0]
        self.assertIn('proxy_set_header Authorization "Basic YWJlOnB3";', term)   # abe:pw
        self.assertIn('auth_request /_zipper_auth;', conf)
        self.assertIn('error_page 401 = @zipper_login;', conf)
        ext = conf.split('api/inputs/')[1].split('}')[0]
        self.assertIn('auth_request off;', ext)
        self.assertIn('auth_basic off;', ext)


if __name__ == '__main__':
    unittest.main()
