"""Writing to the operator's Google Calendar: one calendar Zipper made, and only that.

The scope is `calendar.app.created`: Zipper can create calendars and manage the
events on calendars it created, and cannot read or change anything else in the
account. So the operator's own events are out of reach by construction, not by
care. The calendar shows in their Google Calendar beside the rest, under its own
name and colour.

What goes here is what happens at a time: "take the team photo at all-hands" is
an event with the details in its description, not a task that sits on the todo
list for a week with nothing to do until the moment comes.

Every event has a **key**, and its Google id is derived from it, so writing the
same key twice updates one event instead of making two. Without one, a retry
after a timeout -- Google applied it, the reply was lost -- would leave a
duplicate on someone's calendar.

The agenda reads `Inbox/calendar-zipper.json`, written in the same shape as an
ICS feed's file after every change and on the calendar scoop's pull, so moves
the operator makes in Google Calendar come back within the hour.
"""
import base64, datetime, fcntl, hashlib, json, os, urllib.error, urllib.parse, urllib.request

from . import core, google

SCOPE = 'https://www.googleapis.com/auth/calendar.app.created'
API = 'https://www.googleapis.com/calendar/v3'
NAME = 'Zipper'
LABEL = 'zipper'


def _out():
    return os.path.join(core.INBOX, 'calendar-%s.json' % LABEL)


def _call(path, method='GET', payload=None, **params):
    url = API + path
    if params:
        url += '?' + urllib.parse.urlencode(params)
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header('Authorization', 'Bearer ' + google.access_token())
    if data:
        req.add_header('Content-Type', 'application/json')
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read().decode()
        return json.loads(body) if body else {}


def _status(e):
    return getattr(e, 'code', None)


def calendar_id(create=True):
    """The Zipper calendar's id, made on first use.

    Under a lock: two conversations adding their first event at once would
    otherwise each create a calendar, and the operator would have two called
    Zipper with half the events in each.
    """
    cid = core.cfg('ZIPPER_GCAL_ID')
    if cid or not create:
        return cid
    os.makedirs(core.INBOX, exist_ok=True)
    with open(os.path.join(core.INBOX, '.gcal.lock'), 'w') as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        cid = core.cfg('ZIPPER_GCAL_ID')
        if cid:
            return cid
        tz = _tzname()
        cal = _call('/calendars', 'POST', {'summary': NAME,
                                          **({'timeZone': tz} if tz else {})})
        cid = cal['id']
        google.set_env('ZIPPER_GCAL_ID', cid)
        os.environ['ZIPPER_GCAL_ID'] = cid
        return cid


def _tzname():
    """An IANA name for the calendar, from the box's own zone. Unknown is fine:
    Google then uses the account's, and event times carry offsets anyway."""
    if os.environ.get('TZ', '').count('/'):     # set in the container, where
        return os.environ['TZ'].lstrip(':')     # /etc/localtime stays UTC
    try:
        link = os.path.realpath('/etc/localtime')
        if '/zoneinfo/' in link:
            return link.split('/zoneinfo/', 1)[1]
    except OSError:
        pass
    return ''


def event_id(key):
    """Google event ids are 5-1024 characters of a-v and 0-9: base32hex, lowercased."""
    digest = hashlib.sha1(key.strip().lower().encode()).digest()
    return base64.b32hexencode(digest).decode().rstrip('=').lower()


def _when(value, all_day):
    """'2026-11-17T18:00' (local) or '2026-11-17' -> Google's start/end object."""
    if all_day:
        return {'date': value[:10]}
    dt = datetime.datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.astimezone()            # the box's local zone, the vault's time
    return {'dateTime': dt.isoformat()}


def body(title, start, end=None, note='', location=''):
    """The event resource. No end means an hour for a timed event, a day for a date."""
    all_day = 'T' not in start
    if not end:
        if all_day:
            end = (datetime.date.fromisoformat(start[:10])
                   + datetime.timedelta(days=1)).isoformat()
        else:
            end = (datetime.datetime.fromisoformat(start)
                   + datetime.timedelta(hours=1)).isoformat(timespec='minutes')
    ev = {'summary': title, 'start': _when(start, all_day), 'end': _when(end, all_day)}
    if note:
        ev['description'] = note
    if location:
        ev['location'] = location
    return ev


def put(key, title, start, end=None, note='', location=''):
    """Create the event for `key`, or update it if it exists. Returns 'added' or 'updated'."""
    cid = urllib.parse.quote(calendar_id(), safe='')
    eid = event_id(key)
    ev = dict(body(title, start, end, note, location), id=eid)
    try:
        _call('/calendars/%s/events' % cid, 'POST', ev)
        return 'added'
    except urllib.error.HTTPError as e:
        if _status(e) != 409:           # 409: the id exists (possibly deleted)
            raise
    ev['status'] = 'confirmed'          # brings a deleted one back
    _call('/calendars/%s/events/%s' % (cid, eid), 'PUT', ev)
    return 'updated'


def remove(key):
    cid = calendar_id(create=False)
    if not cid:
        return False
    try:
        _call('/calendars/%s/events/%s' % (urllib.parse.quote(cid, safe=''), event_id(key)),
              'DELETE')
        return True
    except urllib.error.HTTPError as e:
        if _status(e) in (404, 410):
            return False
        raise


def _local(when):
    """Google's start/end -> the ICS file's 'YYYY-MM-DD HH:MM' or 'YYYY-MM-DD'."""
    if 'date' in when:
        return when['date'], True
    dt = datetime.datetime.fromisoformat(when['dateTime'].replace('Z', '+00:00'))
    return dt.astimezone().strftime('%Y-%m-%d %H:%M'), False


def events(back=180, ahead=400):
    cid = calendar_id(create=False)
    if not cid:
        return []
    lo = (core.TODAY - datetime.timedelta(days=back)).isoformat() + 'T00:00:00Z'
    hi = (core.TODAY + datetime.timedelta(days=ahead)).isoformat() + 'T00:00:00Z'
    out, token = [], None
    while True:
        params = {'timeMin': lo, 'timeMax': hi, 'singleEvents': 'true',
                  'orderBy': 'startTime', 'maxResults': 250}
        if token:
            params['pageToken'] = token
        res = _call('/calendars/%s/events' % urllib.parse.quote(cid, safe=''), **params)
        for e in res.get('items', []):
            if e.get('status') == 'cancelled':
                continue
            start, all_day = _local(e['start'])
            end, _ = _local(e['end'])
            out.append({'start': start, 'all_day': all_day, 'end': end,
                        'uid': e['id'], 'location': e.get('location', ''),
                        'summary': e.get('summary', ''),
                        'description': e.get('description', '')})
        token = res.get('nextPageToken')
        if not token:
            return out


def sync():
    """Write Inbox/calendar-zipper.json from Google. Written through a temp file:
    the agenda reads it while this runs, and a half-written file would read as
    a calendar with no events."""
    evs = events()
    os.makedirs(core.INBOX, exist_ok=True)
    tmp = '%s.%d.tmp' % (_out(), os.getpid())      # two syncs at once each write their own
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump({'label': LABEL, 'fetched': core.TODAY.isoformat(), 'match': None,
                   'kept': len(evs), 'seen': len(evs), 'events': evs}, fh, indent=1)
    os.replace(tmp, _out())
    return evs


def ready():
    """Google configured and authorized. Whether the token holds the calendar
    scope only shows when Google answers, so `_explain` reads the refusal."""
    return google.configured() and google.authorized()


def _explain(e):
    """Google's own reason, which says which fix applies. A 403 is either a token
    without the calendar scope or the Calendar API switched off in the client's
    Cloud project, and guessing sent the operator to sign in again for nothing."""
    reason = ''
    try:
        reason = json.loads(e.read().decode() or '{}').get('error', {}).get('message', '')
    except Exception:
        pass
    if _status(e) in (401, 403) and 'insufficient' in reason.lower():
        return ('Google refused: the token lacks calendar access. Run '
                '`zipper google --auth` and sign in again to grant it.')
    return 'Google error %s: %s' % (_status(e) or '?', reason or e)


def cmd_cal(a):
    if not ready():
        print('Google is not set up: `zipper google --auth`')
        return 1
    try:
        if a.action == 'add':
            if not (a.title and a.start):
                print('add needs --title and --start'); return 1
            key = a.key or '%s %s' % (a.start[:10], a.title)
            how = put(key, a.title, a.start, a.end, a.note or '', a.location or '')
            sync()
            print('%s: %s, %s (key: %s)' % (how, a.title, a.start, key))
        elif a.action == 'rm':
            if not a.key:
                print('rm needs --key'); return 1
            print('removed' if remove(a.key) else 'no such event')
            sync()
        else:
            evs = sync()
            today = core.TODAY.isoformat()
            for e in evs:
                if e['start'][:10] >= today:
                    print('%s  %s' % (e['start'], e['summary']))
            if not any(e['start'][:10] >= today for e in evs):
                print('(nothing upcoming on the Zipper calendar)')
    except urllib.error.HTTPError as e:
        print(_explain(e))
        return 1
    except (urllib.error.URLError, OSError, RuntimeError, KeyError) as e:
        print('could not reach Google: %s' % e)     # network, or the token refresh
        return 1
    return 0
