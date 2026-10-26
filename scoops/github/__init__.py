"""GitHub: pushes and commits, from the operator's repos and any orgs in ZIPPER_GH_ORGS,
and the issues assigned to them in `scoops.github.issues`, as `Tasks/GitHub.md`.

The fetching lives in `zipper.gh` and `zipper.ghissues`; this is their face as an input.
"""
import argparse, json, re, sys

from zipper import core, gh, ghissues, settings
from zipper.scoops import OWNER, blob_fetched, as_list

name = 'github'


def pull():
    gh.cmd_github(argparse.Namespace(since_days=30, full=False))
    pull_issues()


def pull_issues():
    """Rewrite `Tasks/GitHub.md` from GitHub. Off while `scoops.github.issues` is
    empty. A refused repo or a failed read raises, so the pull records the error;
    the file is only ever written from a complete read."""
    ok, refused = ghissues.repos()
    if refused:
        raise RuntimeError('issues: not reading %s -- an org repo must also be in '
                           'scoops.github.open' % ', '.join(refused))
    if not ok:
        return
    token = gh._token()
    if not token:
        raise RuntimeError('issues: no GitHub token')
    items = ghissues.fetch(token, gh.GH_USER,
                           settings.get('scoops.github.due_field') or 'Target date')
    notes = [(core.title_of(p), core.fm_dict(core.read_note(p)[0]))
             for p in core.iter_notes()]
    _, why = ghissues.write(items, notes)
    if why:
        raise RuntimeError('issues: ' + why)


def cmd(a):
    """`zipper github`: fetch, then write the facts. The CLI's face of pull()."""
    rc = gh.cmd_github(a)
    from zipper import writer
    writer.apply(facts(), source=name)
    # After the facts, and caught: a bad issues setting must not cost the push
    # evidence, the same as in a scheduled pull. Said, and the exit code says so.
    try:
        pull_issues()
    except Exception as e:
        print('issues: %s' % e, file=sys.stderr)
        return rc or 1
    return rc


def facts():
    """Per mapped note: last push, commits in the window, and last_touched.

    On an org repo a push is the team's, not necessarily the owner's, and
    `last_touched` feeds the drift flags, which are about *their* attention -- so
    only their own commits may move it, and `commits_mine` keeps the split visible.
    """
    by_note = {}
    for r in _repos():
        if r.get('note'):
            by_note.setdefault(r['note'], []).append(r)
    out = {}
    for note, rs in by_note.items():
        newest = max(r['pushed_at'] for r in rs)
        mine = [c for r in rs for c in r.get('commits', []) if c.get('mine')]
        f = {'last_push': newest[:10],
             'commits_recent': sum(len(r.get('commits', [])) for r in rs)}
        if any(r.get('is_org') for r in rs):
            f['commits_mine'] = len(mine)
            when = max((c['date'] for c in mine), default='')
            if when:
                f['last_touched'] = when[:7]
        else:
            f['last_touched'] = newest[:7]
        out[note] = f
    return out


def fetched():
    return blob_fetched(core.GH_JSON)


def _repos():
    try:
        return json.load(open(core.GH_JSON, encoding='utf-8'))['repos']
    except Exception:
        return []


def snapshot():
    return {'repos': {r['name']: r.get('pushed_at', '') for r in _repos()},
            'issues': ghissues.snapshot()}


def _record(name):
    for r in _repos():
        if r['name'] == name or r.get('key') == name:
            return r
    return {}


def _split(snap):
    """(repo heads, issues) from a snapshot. A bare {repo: pushed_at} is the shape
    from before issues were added, and still means repos alone."""
    snap = snap or {}
    if isinstance(snap.get('repos'), dict):
        return snap['repos'], snap.get('issues') or {}
    return snap, {}


def events(before, after):
    """One row per branch that moved, saying which, and whether it was a merge.

    With no baseline of branch heads, or if the heads couldn't be read, the row is
    bare: the repo and the time, with no branch, message or pusher guessed."""
    from zipper import ghpush
    (before, was), (after, now) = _split(before), _split(after)
    out = ghissues.events(was, now)
    for repo, ts in sorted(after.items()):
        # A repo absent from `before` is newly visible, not newly pushed.
        if before.get(repo) is None or before[repo] == ts:
            continue
        recs = _record(repo).get('pushes') or []
        for rec in recs:
            text, who = ghpush.describe(rec, repo, ts[:16], OWNER)
            row = {'system': 'github', 'action': 'push', 'when': ts[:16], 'text': text}
            if who:
                row['who'] = who
            out.append(row)
        if not recs:
            out.append({'system': 'github', 'action': 'push', 'when': ts[:16],
                        'text': 'pushed      %s  %s' % (repo, ts[:16])})
    return out


def target(row, notes):
    """The note whose hand-maintained `repos:` names this repo. Never guessed."""
    m = re.match(r'(?:\+ issue|- issue|closed|reopened|redated)\s+(\S+)#\d+',
                 row.get('text', '')) or \
        re.match(r'(?:pushed|merged|force-pushed)\s+(\S+)', row.get('text', ''))
    if not m:
        return None
    # Issue rows name `owner/repo`, push rows the bare repo; `repos:` may hold either.
    name = m.group(1).split('/')[-1]
    for title, d in notes:
        if name in (str(r).split('/')[-1].strip() for r in as_list(d.get('repos'))):
            return title
    return None
