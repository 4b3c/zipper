"""zipper.google

OAuth against Google, and the two Sheets calls the timesheet needs.

The token lives on this box rather than in the browser, which is the whole
point of doing it here: the extension could only ever act while a tab was open
on the sheet, and hours arrive over Discord at times when nothing is open at
all. With a refresh token in `.env`, `zipper hours add` can put a row in the
spreadsheet the moment they say it.

No Google client library: this is three HTTP calls, and a dependency would be a
worse trade than the forty lines below.

Which kind of OAuth client decides how quiet this is. An Internal client (one
Workspace org) needs no verification and its refresh token does not expire. An
External client left in Testing works for its listed test users, but Google
expires their refresh tokens after 7 days -- publish it (unverified is fine for
a handful of users) or `zipper google --auth` becomes a weekly chore.

**Two ways back from the consent screen.** A zipper with a dashboard has a public
https address (`ZIPPER_EXT_BASE`), so Google returns to its `/oauth/google/callback`
and the token is saved with no help. A zipper reached only through Discord has no
address at all, so it signs in with a *Desktop* client, which Google lets return to
`http://localhost`. Nothing listens there: their browser shows a page that won't load,
with the code in its address bar, and they paste that address into the Discord secret
form (`zipper google --discord`, `bot/secretask.py`). One Desktop client serves every
such zipper: Google treats an installed app's client secret as non-confidential, and
hostd writes it into each new zipper's `.env` (`provision.env`).
"""
import re
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from . import core

AUTH = 'https://accounts.google.com/o/oauth2/v2/auth'
TOKEN = 'https://oauth2.googleapis.com/token'
SHEETS = 'https://sheets.googleapis.com/v4/spreadsheets'

# Read/write to the account's spreadsheets. Google has no per-document scope;
# `drive.file` would be narrower but only reaches files opened through its own
# picker, which a headless box cannot show. So the narrowing that is actually
# available is where the token lives, not what it can reach.
#
# Mail is read-only by default. Email text reaches the unattended passes through
# the queue, and a token that could send would let one message instruct the box
# to forward others. Sending, when wanted, happens in a conversation.
#
# Folders (scoops/gmail/sort.py) need gmail.modify, and Google has no scope that
# moves mail without also allowing send. So it is asked for only while folders are
# configured -- the operator's choice, made knowing that -- and nothing here sends.
SCOPES = ['https://www.googleapis.com/auth/spreadsheets',
          'https://www.googleapis.com/auth/gmail.readonly']
MODIFY = 'https://www.googleapis.com/auth/gmail.modify'

# Calendar: only calendars Zipper itself creates (`gcal.py`). It can't read or
# change any other event in the account.
CALENDAR = 'https://www.googleapis.com/auth/calendar.app.created'


def scopes():
    """What the consent screen asks for. Spreadsheets only for a zipper with a
    timesheet: it reaches every sheet in the account, a scary line on a friend's
    consent screen for a feature they don't use."""
    from . import settings
    sheets = SCOPES[:1] if _cfg('ZIPPER_SHEET_ID') else []
    if settings.get('scoops.gmail.folders'):
        return sheets + [MODIFY, CALENDAR]
    return sheets + SCOPES[1:] + [CALENDAR]

# The file core reads, so the token lands where the next process looks. This
# once built its own path beside the code, which stopped being where `.env`
# lives once ZIPPER_ENV_FILE moved it into config/: the callback wrote a fresh
# token that nothing read.
ENV = core.ENV_FILE


def _env_file():
    return core._env_file()


def _cfg(key, default=''):
    # `core.cfg`: the file wins, because it is where `exchange` writes the
    # refresh token and where they paste. This lived here first and moved to
    # core when the digest needed it too.
    return core.cfg(key, default)


LOOPBACK = 'http://localhost'


def redirect_uri():
    """The dashboard's callback where there is one, else the loopback a Desktop
    client may use. The same answer for the link and the exchange, or Google
    refuses the code."""
    base = _cfg('ZIPPER_EXT_BASE')      # the same tailnet https address
    if not base:
        return LOOPBACK
    return base.rstrip('/') + '/oauth/google/callback'


def loopback():
    return redirect_uri() == LOOPBACK


def code_from(pasted):
    """The one-time code out of what they pasted: the whole address their browser
    landed on, or the code alone. Raises ValueError saying what is wrong -- never
    quoting the value, which is a credential for a few minutes."""
    v = (pasted or '').strip()
    if '://' in v or v.startswith(('localhost', '?')):
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(v if '://' in v else 'http://' + v).query)
        if q.get('error'):
            raise ValueError('Google said %s: the sign-in was turned down, or the account '
                             'blocks this app' % re.sub(r'[^a-z_]', '', q['error'][0])[:40])
        v = (q.get('code') or [''])[0]
        if not v:
            raise ValueError('that address has no code in it. It should be the one the '
                             'browser landed on after Allow')
    if not re.match(r'^[0-9]/[A-Za-z0-9_\-]{20,}$', v):
        raise ValueError("that isn't the address the browser landed on after Allow "
                         "(it starts http://localhost/?...code=)")
    return v


def configured():
    return bool(_cfg('ZIPPER_GOOGLE_CLIENT_ID')
                and _cfg('ZIPPER_GOOGLE_CLIENT_SECRET'))


def authorized():
    return bool(_cfg('ZIPPER_GOOGLE_REFRESH_TOKEN'))


def auth_url():
    """The link they open once. `prompt=consent` is what yields a refresh token.

    Google only returns a refresh token on the *first* grant unless consent is
    forced, so a second run of this would otherwise hand back an access token
    and nothing durable -- which looks like success and expires in an hour.
    """
    q = {'client_id': _cfg('ZIPPER_GOOGLE_CLIENT_ID'),
         'redirect_uri': redirect_uri(),
         'response_type': 'code',
         'scope': ' '.join(scopes()),
         'access_type': 'offline',
         'prompt': 'consent',
         'include_granted_scopes': 'true'}
    return AUTH + '?' + urllib.parse.urlencode(q)


def _post(url, data):
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, method='POST')
    req.add_header('Content-Type', 'application/x-www-form-urlencoded')
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def exchange(code):
    """Turn the one-time code from the callback into a lasting refresh token."""
    _ACCESS.update(token=None, expires=0)   # a new grant may carry new scopes
    res = _post(TOKEN, {'code': code,
                        'client_id': _cfg('ZIPPER_GOOGLE_CLIENT_ID'),
                        'client_secret': _cfg('ZIPPER_GOOGLE_CLIENT_SECRET'),
                        'redirect_uri': redirect_uri(),
                        'grant_type': 'authorization_code'})
    rt = res.get('refresh_token')
    if not rt:
        raise RuntimeError('Google returned no refresh_token. This happens when '
                           'the account has granted before -- revoke at '
                           'myaccount.google.com/permissions and retry.')
    set_env('ZIPPER_GOOGLE_REFRESH_TOKEN', rt)
    os.environ['ZIPPER_GOOGLE_REFRESH_TOKEN'] = rt
    return res


_ACCESS = {'token': None, 'expires': 0}


def access_token():
    """A cached access token, refreshed a minute before it actually lapses."""
    if _ACCESS['token'] and time.time() < _ACCESS['expires'] - 60:
        return _ACCESS['token']
    rt = _cfg('ZIPPER_GOOGLE_REFRESH_TOKEN')
    if not rt:
        raise RuntimeError('not authorized yet - run `zipper google --auth`')
    res = _post(TOKEN, {'refresh_token': rt,
                        'client_id': _cfg('ZIPPER_GOOGLE_CLIENT_ID'),
                        'client_secret': _cfg('ZIPPER_GOOGLE_CLIENT_SECRET'),
                        'grant_type': 'refresh_token'})
    _ACCESS['token'] = res['access_token']
    _ACCESS['expires'] = time.time() + int(res.get('expires_in', 3600))
    return _ACCESS['token']


def _call(path, method='GET', payload=None, **params):
    url = f'{SHEETS}/{path}'
    if params:
        url += '?' + urllib.parse.urlencode(params)
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header('Authorization', 'Bearer ' + access_token())
    if data:
        req.add_header('Content-Type', 'application/json')
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode() or '{}')


def read(sheet_id, rng):
    """Raw cell text. UNFORMATTED would turn their times into serial fractions."""
    res = _call(f'{sheet_id}/values/{urllib.parse.quote(rng)}',
                valueRenderOption='FORMATTED_VALUE',
                dateTimeRenderOption='FORMATTED_STRING')
    return res.get('values', [])


def read_formula(sheet_id, rng):
    """The same cells as formulas, for reading a sheet's shape rather than its
    contents -- which week a SUM covers, and how much room is left in it."""
    res = _call(f'{sheet_id}/values/{urllib.parse.quote(rng)}',
                valueRenderOption='FORMULA')
    return res.get('values', [])


def write(sheet_id, updates):
    """Several ranges at once. USER_ENTERED so "1:30" becomes a time and
    "=C31-B31" becomes a formula, exactly as if they had typed them."""
    return _call(f'{sheet_id}/values:batchUpdate', 'POST', {
        'valueInputOption': 'USER_ENTERED',
        'data': [{'range': r, 'values': v} for r, v in updates]})


def batch(sheet_id, requests):
    """spreadsheets.batchUpdate -- structure, as opposed to values."""
    return _call(f'{sheet_id}:batchUpdate', 'POST', {'requests': requests})


def tabs(sheet_id):
    res = _call(sheet_id, fields='sheets.properties')
    return [s['properties'] for s in res.get('sheets', [])]


# ------------------------------------------------------------------- .env I/O

def set_env(key, value):
    """Rewrite one key in .env, in place, without disturbing the rest.

    The refresh token has to survive a restart and belongs with the other
    secrets, and this file is already the one place credentials live. Written
    through a temp file so an interrupted write cannot truncate it -- losing
    .env would take the Discord token and the GitHub App with it.
    """
    try:
        with open(ENV) as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        lines = []
    out, done = [], False
    for ln in lines:
        if ln.startswith(key + '='):
            out.append(f'{key}={value}')
            done = True
        else:
            out.append(ln)
    if not done:
        out.append(f'{key}={value}')
    tmp = ENV + '.tmp'
    with open(tmp, 'w') as f:
        f.write('\n'.join(out) + '\n')
    os.chmod(tmp, 0o600)
    os.replace(tmp, ENV)


DISCORD_STEPS = """Send them this link, with these steps, and end your turn:

%s

1. Open the link and sign in with the Google account whose mail this is.
2. Google will warn that it hasn't verified the app. Tap Advanced, then "Go to ..."
   (it is the operator's own Google app; it reads mail and never sends).
3. Allow it. The browser then lands on a page that won't load, at an address starting
   http://localhost. That is expected: copy that whole address from the address bar.
4. Tap the button posted below and paste it there. It goes to the zipper's private
   files, never into the chat.

You will get a "[secret] ..." message saying whether Google is signed in."""


def cmd_google_discord(a):
    """The sign-in for a zipper reached only through Discord: the link for the reply,
    and a secret button for the address their browser lands on."""
    if not os.environ.get('ZIPPER_DISCORD_THREAD'):
        print('`zipper google --discord` works in a Discord conversation, where it can post '
              'the button. Here, use `zipper google --auth`.')
        return 1
    if not loopback():
        print('This zipper has a dashboard address, so Google returns to it: use '
              '`zipper google --auth` and the link alone.')
        return 1
    from . import secret
    print(DISCORD_STEPS % auth_url())
    print()
    return secret.ask_discord('google', 'Google sign-in: the localhost address',
                              kind='google')


def save_pasted(pasted):
    """What the Discord form does with the pasted address. Returns the line the
    conversation is told: whether it worked, never the value."""
    try:
        code = code_from(pasted)
    except ValueError as e:
        return 'Google was not signed in: %s. Run `zipper google --discord` again.' % e
    try:
        exchange(code)
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read().decode()).get('error', '')
        except Exception:
            err = ''
        why = {'invalid_grant': 'the code was already used or has expired (it lasts a few '
                                'minutes), so it needs a fresh sign-in',
               'redirect_uri_mismatch': 'the Google app is not a Desktop app',
               'invalid_client': 'ZIPPER_GOOGLE_CLIENT_ID / _SECRET are wrong'}.get(
            err, 'Google refused it (%s)' % (re.sub(r'[^a-z_]', '', err)[:40] or e.code))
        return 'Google was not signed in: %s. Run `zipper google --discord` again.' % why
    except RuntimeError as e:
        return 'Google was not signed in: %s' % e
    return ('Google is signed in (%s). Run `zipper scoop enable gmail` if it is not on '
            'yet; the next pull reads mail.' % ', '.join(
                s.rsplit('/', 1)[-1] for s in scopes()))


def cmd_google(a):
    if not configured():
        print('ZIPPER_GOOGLE_CLIENT_ID / _SECRET are unset in .env. A zipper on the shared '
              'gateway gets them from hostd (`provision.env`); ask its operator.')
        return 1
    if getattr(a, 'discord', False):
        return cmd_google_discord(a)
    if getattr(a, 'auth', False) or not authorized():
        # Naming the account is worth a line -- the client is Internal to one
        # Workspace org, so the wrong login fails at the consent screen and it
        # is better to be told which one up front. The address is config, not a
        # default: unset, this says what it needs rather than who they are.
        who = _cfg('ZIPPER_GOOGLE_ACCOUNT')
        print('Open this, signed in as %s:\n'
              % (who or 'the account that owns the sheet'))
        print(auth_url())
        print('\nThe callback writes the refresh token into .env itself.')
        if not getattr(a, 'auth', False):
            print('\n(not authorized yet — that is why you are seeing this)')
        return 0
    print('client     : %s' % _cfg('ZIPPER_GOOGLE_CLIENT_ID')[:28] + '...')
    print('account    : %s' % (_cfg('ZIPPER_GOOGLE_ACCOUNT') or '(unset)'))
    print('redirect   : %s' % redirect_uri())
    print('authorized : yes')
    sid = _cfg('ZIPPER_SHEET_ID')
    if sid:
        try:
            names = [t['title'] for t in tabs(sid)]
            print('sheet      : %s' % ', '.join(names))
        except Exception as e:
            print('sheet      : could not read it — %s' % e)
    return 0
