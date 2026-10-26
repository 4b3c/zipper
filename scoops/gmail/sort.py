"""Folders: move mail out of the inbox into labels, by Gmail search rules.

Off until `scoops.gmail.folders` lists rules in settings.json -- the rules name
their senders, so they are the operator's, never a default here:

    "folders": [{"label": "Login codes", "query": "subject:(\"verification code\")"},
                {"label": "Promos",      "query": "category:promotions"}],
    "hold":     "category:personal OR subject:(\"action required\" OR deadline)",
    "fallback": "Important"

A rule with `"always": true` ignores `hold` (a login code is never a task).

Only mail he has opened moves: every query carries `-is:unread`, so the
organizer never files something he hasn't seen. `--file` is explicit and moves
whatever it is given.

Rules run in order and the first match wins: filing removes INBOX, so a later
rule's `in:inbox` no longer sees it. "Moving" is a label plus leaving the inbox;
nothing is deleted.

Two modes, because new mail and old mail want different caution.

- **Live**, after every pull: only messages the pull has already recorded, so the
  queue always sees a message before it leaves the inbox, and never anything
  matching `hold`. Held mail -- a person writing, an office asking for something --
  waits in the inbox for a pass to make its tasks and then `--file` it.
- **Backlog** (`zipper gmail --sort --backlog`): everything older than
  `BACKLOG_DAYS`, holds ignored, and whatever no rule matches goes to `fallback`.

Needs `gmail.modify`, which `zipper.google.scopes()` asks for only while folders
are configured. That scope can send mail too; see the note there.
"""
import json, urllib.error, urllib.request

from zipper import google, settings

API = 'https://gmail.googleapis.com/gmail/v1/users/me'
BACKLOG_DAYS = 14
BATCH = 1000                 # batchModify's limit
LIST_CAP = 20000             # a runaway query stops here
OPENED = '-is:unread'        # unread mail stays in the inbox


def rules():
    return [r for r in (settings.get('scoops.gmail.folders') or [])
            if isinstance(r, dict) and r.get('label') and r.get('query')]


def enabled():
    return bool(rules())


def _call(path, method='GET', payload=None, **params):
    import urllib.parse
    url = '%s/%s' % (API, path)
    if params:
        url += '?' + urllib.parse.urlencode(params, doseq=True)
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={'Authorization': 'Bearer ' + google.access_token()})
    if data is not None:
        req.add_header('Content-Type', 'application/json')
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode() or '{}')


def search(q):
    ids, token = [], None
    while True:
        res = _call('messages', q=q, maxResults=500, **({'pageToken': token} if token else {}))
        ids += [m['id'] for m in res.get('messages') or []]
        token = res.get('nextPageToken')
        if not token or len(ids) >= LIST_CAP:
            return ids


def label_ids(names, create=True):
    """Label name -> id, creating any that are missing."""
    have = {l['name']: l['id'] for l in _call('labels').get('labels') or []}
    for n in names:
        if n not in have and create:
            have[n] = _call('labels', 'POST', {'name': n, 'labelListVisibility': 'labelShow',
                                               'messageListVisibility': 'show'})['id']
    return have


def move(ids, label_id):
    for i in range(0, len(ids), BATCH):
        _call('messages/batchModify', 'POST', {'ids': ids[i:i + BATCH],
                                               'addLabelIds': [label_id],
                                               'removeLabelIds': ['INBOX']})


def plan(backlog=False, only=None):
    """[(label, [ids])] in rule order, each id once. `only` limits live sorting to
    ids the pull has recorded."""
    hold = (settings.get('scoops.gmail.hold') or '').strip()
    scope = ('in:inbox older_than:%dd' % BACKLOG_DAYS if backlog else 'in:inbox') + ' ' + OPENED
    taken, out = set(), []
    for r in rules():
        q = '%s (%s)' % (scope, r['query'])
        if hold and not backlog and not r.get('always'):
            q += ' -(%s)' % hold
        ids = [i for i in search(q)
               if i not in taken and (only is None or i in only)]
        taken.update(ids)
        out.append((r['label'], ids))
    fallback = settings.get('scoops.gmail.fallback')
    if backlog and fallback:
        out.append((fallback, [i for i in search(scope) if i not in taken]))
    return out


def run(backlog=False, only=None, dry=False):
    steps = plan(backlog, only)
    if dry:
        return steps
    ids = label_ids([l for l, _ in steps])
    for label, msgs in steps:
        if msgs:
            move(msgs, ids[label])
    return steps


def file(mid, label):
    """One message into one folder, out of the inbox: what a pass does after it
    has made the message's tasks."""
    move([mid], label_ids([label])[label])


def live(seen):
    """After a pull. Never raises: a missing scope must not stop the queue."""
    if not enabled():
        return None
    try:
        return sum(len(m) for _, m in run(only=set(seen)))
    except urllib.error.HTTPError as e:
        return 'sort failed: HTTP %d%s' % (e.code, ' -- the token lacks gmail.modify; '
                                           'run `zipper google --auth`' if e.code == 403 else '')
    except Exception as e:
        return 'sort failed: %s' % e
