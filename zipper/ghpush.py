"""What a push was: which branch moved, whether it was a merge, and what it brought in.

GitHub's `pushed_at` moves on a push to any branch, and its events API lags by
minutes to hours and no longer carries commits. So instead each fetch keeps the
repo's branch heads, and a moved head is compared with where it was: the compare
lists the commits on that branch alone, with their parents.

An NDA org repo's branch names are content like its commit messages: they are kept
only hashed, and its rows say "main" or "a branch" and no more.
"""
import hashlib


def _label(name, open_):
    return name if open_ else 'h:' + hashlib.sha1(name.encode('utf-8')).hexdigest()[:10]


def heads(r, api, open_):
    """{branch: sha} for every branch, names hashed unless the repo is open."""
    bs = api('/repos/%s/%s/branches' % (r['owner'], r['name']), '?per_page=100')
    return {_label(b['name'], open_): b['commit']['sha'] for b in bs}


def pushes(r, old, now, api, commit, open_):
    """One record per branch whose head moved since `old`, newest commit first.

    `old` is None before a baseline exists: then nothing can be said about which
    branch moved, and the row stays bare rather than guessing. A deleted branch is
    not a push and is skipped.
    """
    if old is None:
        return []
    default = _label(r.get('default_branch') or 'main', open_)
    out = []
    for b, sha in sorted(now.items()):
        was = old.get(b)
        if was == sha:
            continue
        # A new branch is compared with the default branch it was cut from.
        base = was or now.get(default) or old.get(default)
        rec = {'branch': b if open_ or b == default else None,
               'default': b == default, 'new': was is None,
               'head': sha[:7], 'kind': 'commit', 'forced': False, 'commits': []}
        if b == default and not open_:
            rec['branch'] = r.get('default_branch') or 'main'
        if base and base != sha:
            try:
                c = api('/repos/%s/%s/compare/%s...%s' % (r['owner'], r['name'], base, sha), '')
            except Exception as e:
                rec['error'] = str(e)
                out.append(rec)
                continue
            # A rewritten branch: some of what was there is gone.
            rec['forced'] = was is not None and c.get('status') in ('diverged', 'behind')
            raw = list(reversed(c.get('commits') or []))[:20]
            rec['commits'] = [commit(x, r) for x in raw]
            if raw and len(raw[0].get('parents') or []) > 1:
                rec['kind'] = 'merge'
        if rec['default'] and not rec['forced']:
            _pull_request(rec, r, sha, api, open_)
        out.append(rec)
    return out


def _pull_request(rec, r, sha, api, open_):
    """A default-branch head that a merged PR produced is a merge, however it was
    merged -- a squash or rebase merge has one parent and looks like a commit."""
    try:
        prs = api('/repos/%s/%s/commits/%s/pulls' % (r['owner'], r['name'], sha), '')
    except Exception:
        return
    for p in prs:
        if p.get('merged_at') and (p.get('merge_commit_sha') or '').startswith(sha[:7]):
            rec['kind'] = 'merge'
            rec['pr'] = p['number']
            if open_:
                rec['pr_title'] = (p.get('title') or '')[:100]
            return


def describe(rec, repo, when, owner):
    """The queue row text and who, for one branch push."""
    verb = 'merged' if rec['kind'] == 'merge' else \
        'force-pushed' if rec['forced'] else 'pushed'
    where = rec['branch'] or 'a branch'
    if rec['new']:
        where += ' (new)'
    cs = rec['commits']
    if rec.get('pr'):
        what = '#%d %s' % (rec['pr'], rec.get('pr_title', ''))
    else:
        what = next((c['message'] for c in cs if c.get('message')), '')
    if rec['kind'] == 'merge' and len(cs) > 1:
        what += '  (%d commits)' % len(cs)
    elif what and len(cs) > 1:
        what += '  (+%d more)' % (len(cs) - 1)
    # The time stays in the text: the queue keys a row by its text.
    text = ('%-11s %s → %s  %s  %s' % (verb, repo, where, when, what.strip())).rstrip()
    # A merge is the merger's act; anything else is its commits' authors'.
    names = []
    for c in (cs[:1] if rec['kind'] == 'merge' else cs):
        n = owner if c.get('mine') else c.get('author')
        if n and n not in names:
            names.append(n)
    return text, ', '.join(names) or None
