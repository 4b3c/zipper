"""zipper.ghissues

Issues assigned to the operator, written as `Tasks/GitHub.md` -- a todo list GitHub owns.

The repos are named in `plugins.github.issues`. An org repo is read only if it is also
in `plugins.github.open`: an issue title is content, and owning an org is not consent
to copy its contents into a vault. The due date is the issue's date field on whatever
project board it sits on (`plugins.github.due_field`, "Target date" by default), and the
board's Status goes on the description line.

**The file is read-only.** Every pull rewrites it from GitHub, so a tick, a re-date or
a deleted line made in the vault is undone, and the dashboard draws its tick boxes as
ghosts. An edit is reported as a queue row rather than silently reverted: an undo
nobody hears about reads exactly like the edit having worked. A `Tasks/GitHub.md` the
plugin did not write (no `generated: github`) is never overwritten.
"""
import datetime, fcntl, json, os, re, urllib.request

from . import core, settings
from .plugins import as_list

FILE = 'GitHub.md'
STORE = os.path.join(core.INBOX, 'github-issues.json')
LOCK = STORE + '.lock'
MARK = 'generated: github'
# Closed issues stay ticked this long, so a close reads as done, not as vanished.
KEEP_CLOSED_DAYS = 14

QUERY = '''query($owner:String!,$name:String!,$who:String!,$states:[IssueState!],$since:DateTime,$after:String){
 repository(owner:$owner,name:$name){issues(first:50,after:$after,states:$states,
  filterBy:{assignee:$who,since:$since}){pageInfo{hasNextPage endCursor}
  nodes{number title state closedAt url parent{number}
   projectItems(first:10){nodes{fieldValues(first:30){nodes{
    ... on ProjectV2ItemFieldSingleSelectValue{name field{... on ProjectV2FieldCommon{name}}}
    ... on ProjectV2ItemFieldDateValue{date field{... on ProjectV2FieldCommon{name}}}}}}}}}}}'''


def path():
    return os.path.join(core.VAULT, 'Tasks', FILE)


def repos():
    """(repos to read, repos refused). Org repos must be listed open."""
    user = (settings.get('plugins.github.user') or '').lower()
    opened = set(settings.get('plugins.github.open') or [])
    ok, refused = [], []
    for r in settings.get('plugins.github.issues') or []:
        r = str(r).strip()
        if '/' not in r:
            refused.append(r)
        elif r.split('/')[0].lower() == user or r in opened:
            ok.append(r)
        else:
            refused.append(r)
    return ok, refused


def _gql(token, variables):
    req = urllib.request.Request(
        'https://api.github.com/graphql',
        json.dumps({'query': QUERY, 'variables': variables}).encode(),
        {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    d = json.load(urllib.request.urlopen(req, timeout=30))
    if d.get('errors'):
        raise RuntimeError(d['errors'][0].get('message', 'GraphQL error'))
    return d['data']['repository']['issues']


def _fields(node):
    """{field name: value} across every board the issue is on; the first board wins."""
    out = {}
    for item in (node.get('projectItems') or {}).get('nodes') or []:
        for v in (item.get('fieldValues') or {}).get('nodes') or []:
            name = ((v or {}).get('field') or {}).get('name')
            val = (v or {}).get('name') or (v or {}).get('date')
            if name and val and name not in out:
                out[name] = val
    return out


def item(repo, node, due_field):
    f = _fields(node)
    return {'key': '%s#%d' % (repo, node['number']), 'repo': repo,
            'number': node['number'], 'title': node['title'],
            'closed': (core._utc_local(node['closedAt'])[:10]
                       if node['state'] == 'CLOSED' and node.get('closedAt') else ''),
            'url': node['url'], 'parent': (node.get('parent') or {}).get('number'),
            'status': f.get('Status', ''), 'due': (f.get(due_field) or '')[:10]}


def fetch(token, user, due_field='Target date', today=None):
    """Every open issue assigned to `user` in the configured repos, plus those closed
    in the last KEEP_CLOSED_DAYS. Raises on any failure: a half-read list written
    out would untick, or drop, issues that are still open."""
    today = today or core.TODAY
    since = (today - datetime.timedelta(days=KEEP_CLOSED_DAYS)).isoformat() + 'T00:00:00Z'
    out = []
    for repo in repos()[0]:
        owner, name = repo.split('/', 1)
        for states, when in ((['OPEN'], None), (['CLOSED'], since)):
            after = None
            while True:
                page = _gql(token, {'owner': owner, 'name': name, 'who': user,
                                    'states': states, 'since': when, 'after': after})
                for n in page['nodes']:
                    it = item(repo, n, due_field)
                    if states == ['CLOSED'] and (not it['closed'] or it['closed'] < since[:10]):
                        continue
                    out.append(it)
                if not page['pageInfo']['hasNextPage']:
                    break
                after = page['pageInfo']['endCursor']
    return out


def _clean(title):
    """A title as a task line can carry it. `#71` would be read as a tag and
    stripped from the task's key, and brackets as links or fields."""
    t = re.sub(r'\s+', ' ', title).strip()
    t = re.sub(r'#(\d+)', r'issue \1', t)
    t = t.replace('#', '').replace('[', '(').replace(']', ')')
    return t


def _note_for(repo, notes):
    """The note whose hand-maintained `repos:` names this repo. Never guessed --
    the same rule the push rows use."""
    short = repo.split('/')[-1]
    for title, d in notes:
        for r in as_list(d.get('repos')):
            if str(r).strip() == repo or str(r).split('/')[-1].strip() == short:
                return title
    return ''


def render(items, notes):
    lines = ['---', 'tags: [tasks]', 'type: tasks', 'status: living', 'scope: github',
             MARK, '---', '# GitHub', '',
             '*Written by the GitHub plugin on every pull: the issues assigned to you in '
             + ', '.join('`%s`' % r for r in repos()[0])
             + '. **Read-only.** Close, reassign or re-date them on GitHub; an edit made '
             'here is undone on the next pull.*', '']
    order = sorted(items, key=lambda i: (bool(i['closed']), i['due'] or '9999',
                                         i['repo'], i['number']))
    for i in order:
        note = _note_for(i['repo'], notes)
        line = '- [%s] %s' % ('x' if i['closed'] else ' ', _clean(i['title']))
        if note:
            line += ' [project:: [[%s]]]' % note
        if i['due']:
            line += ' [due:: %s]' % i['due']
        line += ' [issue:: %s]' % i['key']
        if i['closed']:
            line += ' ✅ %s' % i['closed']
        lines.append(line)
        desc = []
        if i['status'] and not i['closed']:
            desc.append('%s on the board.' % i['status'])
        if i['parent']:
            desc.append('Part of issue %d.' % i['parent'])
        desc.append(i['url'])
        lines.append('  ' + ' '.join(desc))
    if not items:
        lines.append('Nothing assigned.')
    return '\n'.join(lines) + '\n'


def _boxes(text):
    """{task text: ticked} for the file's task lines."""
    out = {}
    for line in text.splitlines():
        m = core.TASK_RE.match(line)
        if m:
            out[re.sub(r'\[[a-z_]+::[^\]]*\]+|✅.*', '', m.group(2)).strip()] = \
                m.group(1).lower() == 'x'
    return out


def describe_edit(was, now):
    """What changed by hand, in a few words, for the queue row."""
    if now is None:
        return 'the file was deleted'
    a, b = _boxes(was), _boxes(now)
    bits = ['ticked "%s"' % t for t in b if b[t] and not a.get(t, False)]
    bits += ['unticked "%s"' % t for t in b if not b[t] and a.get(t)]
    bits += ['removed "%s"' % t for t in a if t not in b]
    return '; '.join(bits[:3]) or 'edited by hand'


def load():
    try:
        return json.load(open(STORE, encoding='utf-8'))
    except Exception:
        return {}


def write(items, notes):
    """Write the file from `items`. Returns (what was undone or '', refused reason or '').

    Locked, so two pulls at once can't each read the other's write as a hand edit."""
    os.makedirs(core.INBOX, exist_ok=True)
    with open(LOCK, 'w') as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        store = load()
        p = path()
        try:
            cur = open(p, encoding='utf-8').read()
        except FileNotFoundError:
            cur = None
        if cur is not None and MARK not in cur.split('\n---', 1)[0]:
            return '', '%s exists and was not written by this plugin; left alone' % p
        undone = ''
        if store.get('text') and cur != store['text']:
            undone = describe_edit(store['text'], cur)
            # Counted, so the snapshot diff turns it into exactly one queue row.
            store['undone'] = {'n': (store.get('undone') or {}).get('n', 0) + 1,
                               'what': undone}
        text = render(items, notes)
        if text != cur:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            tmp = p + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as fh:
                fh.write(text)
            os.replace(tmp, p)
        store.update(items=items, text=text,
                     fetched=datetime.datetime.now().isoformat(timespec='seconds'))
        tmp = STORE + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as fh:
            json.dump(store, fh, indent=1)
        os.replace(tmp, STORE)
        return undone, ''


def snapshot():
    s = load()
    return {'items': {i['key']: (i['title'], i['closed'], i['due'], i['status'])
                      for i in s.get('items') or []},
            'undone': s.get('undone') or {}}


def events(before, after):
    """Assigned, closed, reopened, unassigned, and a due date that moved."""
    out = []

    def row(action, text):
        out.append({'system': 'github', 'action': action, 'text': text})
    u = after.get('undone') or {}
    if u.get('n', 0) > (before.get('undone') or {}).get('n', 0):
        row('edit', 'undone      Tasks/%s  %s -- GitHub owns these; change it there'
            % (FILE, u.get('what', '')))
    before, after = before.get('items') or {}, after.get('items') or {}
    for k, (title, closed, due, _) in sorted(after.items()):
        was = before.get(k)
        if was is None:
            row('add', '+ issue     %s  %s%s' % (k, title, '  due %s' % due if due else ''))
        elif closed and not was[1]:
            row('close', 'closed      %s  %s' % (k, title))
        elif not closed and was[1]:
            row('add', 'reopened    %s  %s' % (k, title))
        elif due != was[2]:
            row('edit', 'redated     %s  %s  %s -> %s' % (k, title, was[2] or 'none',
                                                         due or 'none'))
    for k, (title, closed, _, _) in sorted(before.items()):
        if k not in after and not closed:
            row('edit', '- issue     %s  %s  (unassigned or gone)' % (k, title))
    return out
