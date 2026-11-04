"""Slack's Web API, spoken as the operator's browser session.

A browser session is an `xoxc-` token per workspace plus the one `d` cookie they all
share. The token travels in the form body, never the URL, so no error or log line
built from a request can carry it, and neither value is ever printed. Read-only by
use: nothing here calls a method that posts, reacts or marks anything read.
"""
import json, time, urllib.error, urllib.parse, urllib.request

from zipper import core

BASE = 'https://slack.com/api/'
# What Slack says when the session is gone: logged out in that browser, the cookie
# rotated, or the token revoked. Only re-sending the pair fixes these.
AUTH_ERRORS = frozenset({'invalid_auth', 'not_authed', 'token_revoked', 'token_expired',
                         'account_inactive', 'invalid_cookie'})
RETRIES = 3
WAIT_MAX = 60                # seconds; a longer Retry-After is better as next pull's problem
CALLS = 0                    # requests made by this process, for the pull's own count


class Error(Exception):
    """Slack refused or could not be reached. Says the method and Slack's error code."""


class AuthError(Error):
    """The session is no longer valid. The fix is new secrets, not a retry."""


def tokens():
    """{workspace: token} from `SLACK_TOKENS`, read at call time so a re-sent secret
    applies to the next pull. {} when unset."""
    raw = core.cfg('SLACK_TOKENS')
    if not raw:
        return {}
    try:
        d = json.loads(raw)
    except ValueError:
        d = None
    if not isinstance(d, dict):
        raise Error('SLACK_TOKENS is not a JSON object of {"workspace": "xoxc-..."}')
    return {str(k): str(v).strip() for k, v in d.items() if v}


def cookie():
    """The `d` cookie, already URL-encoded as the browser stores it: sent verbatim."""
    return core.cfg('SLACK_COOKIE')


def call(token, method, **params):
    """POST one method; the decoded reply when `ok`, else Error or AuthError.
    `ratelimited` (HTTP 429 or in the body) waits Retry-After and tries again."""
    global CALLS
    body = urllib.parse.urlencode(dict(params, token=token)).encode()
    for attempt in range(RETRIES + 1):
        req = urllib.request.Request(BASE + method, data=body, headers={
            'Cookie': 'd=' + cookie(),
            'Content-Type': 'application/x-www-form-urlencoded'})
        CALLS += 1
        wait = None
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                res = json.loads(r.read().decode() or '{}')
                wait = r.headers.get('Retry-After')
        except urllib.error.HTTPError as e:
            if e.code != 429:
                raise Error('%s: HTTP %d' % (method, e.code)) from None
            res, wait = {'ok': False, 'error': 'ratelimited'}, e.headers.get('Retry-After')
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise Error('%s: %s' % (method, getattr(e, 'reason', e))) from None
        if res.get('ok'):
            return res
        err = res.get('error') or 'unknown error'
        if err == 'ratelimited' and attempt < RETRIES:
            try:
                secs = int(wait or 5)
            except ValueError:
                secs = 5
            time.sleep(min(max(secs, 1), WAIT_MAX))
            continue
        if err in AUTH_ERRORS:
            raise AuthError(err)
        raise Error('%s: %s' % (method, err))
    raise Error('%s: ratelimited' % method)
