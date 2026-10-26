"""The dashboard's sign-in: a real form, so a password manager can fill it.

nginx guarded the dashboard with HTTP basic auth alone, and a browser's basic-auth
dialog is not a page: password managers can't see it, so every sign-in meant
opening the vault and copying the password by hand. Now nginx accepts either a
session cookie (checked here, through `auth_request`) or basic credentials (for
scripts and `tests/shot.py`), and sends anything else to `/login`.

The cookie is `user:expiry:hmac`, keyed from the credential itself: no session
store to lose, and changing ZIPPER_TERM_CRED signs every browser out.
"""
import hashlib, hmac, html, time, urllib.parse

from .. import core

COOKIE = 'zipper_session'
MAX_AGE = 30 * 86400


def _cred():
    user, _, pw = (core.cfg('ZIPPER_TERM_CRED') or '').partition(':')
    return (user, pw) if user and pw else (None, None)


def _sig(user, exp, pw):
    key = hashlib.sha256(('zipper-login:%s:%s' % (user, pw)).encode('utf-8')).digest()
    return hmac.new(key, ('%s:%d' % (user, exp)).encode('utf-8'), hashlib.sha256).hexdigest()


def make(now=None):
    user, pw = _cred()
    exp = int((now or time.time()) + MAX_AGE)
    return '%s:%d:%s' % (user, exp, _sig(user, exp, pw))


def valid(cookie_header, now=None):
    """Does this Cookie header carry a live session for the current credential?"""
    user, pw = _cred()
    if not user:
        return False
    for part in (cookie_header or '').split(';'):
        name, _, value = part.strip().partition('=')
        if name != COOKIE:
            continue
        try:
            u, exp, sig = value.split(':', 2)
            exp = int(exp)
        except ValueError:
            continue
        if u == user and exp > (now or time.time()) and \
                hmac.compare_digest(sig, _sig(u, exp, pw)):
            return True
    return False


def check(user, pw):
    want_user, want_pw = _cred()
    if not want_user:
        return False
    # Both compared, always, in constant time: no early exit to time.
    ok_user = hmac.compare_digest(user.encode('utf-8'), want_user.encode('utf-8'))
    ok_pw = hmac.compare_digest(pw.encode('utf-8'), want_pw.encode('utf-8'))
    return ok_user and ok_pw


def safe_next(target):
    """Only a path on this site, safe to put in a Location header.

    Checked on what a browser would act on, not the raw string: browsers drop tabs
    and newlines from URLs, so `/\t/evil.com` becomes `//evil.com`, and a CR or LF
    would end the header and start a forged one. Any control character, a
    backslash, or anything that parses with a scheme or host is refused outright;
    what passes is percent-encoded, so nothing but ASCII reaches the header."""
    target = urllib.parse.unquote(target or '/')
    if any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in target) or '\\' in target:
        return '/'
    parts = urllib.parse.urlsplit(target)
    if parts.scheme or parts.netloc or not target.startswith('/') or target.startswith('//'):
        return '/'
    return urllib.parse.quote(target, safe="/?&=#%:@!$'()*+,;~-._")


def over_https(headers):
    """Did this request reach the browser's side over HTTPS? tailscale serve terminates
    TLS and says so in X-Forwarded-Proto, which nginx passes through. `zipper init`
    hands a new zipper a plain-http address, so HTTPS can't be assumed. A forged
    header only makes the forger's own browser drop its cookie."""
    return (headers.get('X-Forwarded-Proto') or '').strip().lower() == 'https'


def set_cookie(value, max_age=MAX_AGE, secure=False):
    """`secure` (from over_https) keeps the cookie off plain http from then on. It
    can't be unconditional: a browser drops a Secure cookie set over plain http, so
    a zipper reached that way would bounce back to the form after every sign-in."""
    return '%s=%s; Path=/; Max-Age=%d; HttpOnly; %sSameSite=Lax' % (
        COOKIE, value, max_age, 'Secure; ' if secure else '')


def page(nxt='/', error=''):
    """The form. Plain inputs with the standard names and autocomplete hints are
    what password managers look for; nothing here is clever on purpose."""
    err = '<p class="err">%s</p>' % html.escape(error) if error else ''
    return '''<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sign in · Zipper</title>
<style>
  :root { color-scheme: dark; }
  body { margin: 0; min-height: 100vh; display: grid; place-items: center;
         background: #0f1115; color: #e6e6e6;
         font: 15px/1.4 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
  form { width: 300px; padding: 28px; border-radius: 12px; background: #171a21;
         border: 1px solid #262b36; display: flex; flex-direction: column; gap: 12px; }
  h1 { margin: 0 0 6px; font-size: 20px; font-weight: 600; }
  label { display: flex; flex-direction: column; gap: 5px; font-size: 13px; color: #9aa3b2; }
  input { padding: 9px 11px; border-radius: 8px; border: 1px solid #2c3240;
          background: #0f1115; color: #e6e6e6; font-size: 15px; }
  input:focus { outline: none; border-color: #5b8def; }
  button { margin-top: 6px; padding: 10px; border: 0; border-radius: 8px;
           background: #5b8def; color: white; font-size: 15px; font-weight: 600; cursor: pointer; }
  .err { margin: 0; color: #f27a7a; font-size: 13px; }
</style></head>
<body>
<form method="post" action="/login">
  <h1>Zipper</h1>
  %s
  <input type="hidden" name="next" value="%s">
  <label>Username
    <input type="text" name="username" id="username" autocomplete="username" autofocus required></label>
  <label>Password
    <input type="password" name="password" id="password" autocomplete="current-password" required></label>
  <button type="submit">Sign in</button>
</form>
</body></html>''' % (err, html.escape(safe_next(nxt), quote=True))
