"""GitHub: pushes and commits, from the operator's repos and any orgs in ZIPPER_GH_ORGS.

The fetching lives in `zipper.gh`; this is its face as an input.
"""
import argparse, json, re

from .. import core, gh
from . import OWNER, blob_fetched, as_list

name = 'github'
SETUP_TITLE = 'GitHub'
SETUP_ABOUT = 'pushes and commit counts from your repos, as evidence a project is alive'


def setup(w):
    w.setting('inputs.github.user', 'Your GitHub login')
    w.setting('inputs.github.orgs', 'Orgs to include, comma-separated (blank for none)', kind=list)
    w.secret('GITHUB_TOKEN', 'A GitHub token -- read access is enough; blank sees public repos only')


def pull():
    gh.cmd_github(argparse.Namespace(since_days=30, full=False))


def cmd(a):
    """`zipper github`: fetch, then write the facts. The CLI's face of pull()."""
    rc = gh.cmd_github(a)
    from .. import writer
    writer.apply(facts(), source=name)
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
    return {r['name']: r.get('pushed_at', '') for r in _repos()}


def _who(name):
    """The owner when the newest commits are theirs, 'team' when not.

    On a personal repo that is always the owner and says nothing. On an org repo it
    is the point: `commits_recent` is the team's, and only the owner's own work
    should move `last_touched`, so a push that isn't theirs is a different fact.
    """
    for r in _repos():
        if r['name'] == name and r.get('commits'):
            return OWNER if any(c.get('mine') for c in r['commits'][:5]) else 'team'
    return None


def events(before, after):
    out = []
    for repo, ts in sorted(after.items()):
        # A repo absent from `before` is newly visible, not newly pushed.
        if before.get(repo) is not None and before[repo] != ts:
            out.append({'system': 'github', 'action': 'push', 'when': ts[:16],
                        'who': _who(repo),
                        'text': 'pushed      %s  %s' % (repo, ts[:16])})
    return out


def target(row, notes):
    """The note whose hand-maintained `repos:` names this repo. Never guessed."""
    m = re.match(r'pushed\s+(\S+)', row.get('text', ''))
    if not m:
        return None
    for title, d in notes:
        if m.group(1) in (str(r).split('/')[-1].strip() for r in as_list(d.get('repos'))):
            return title
    return None
