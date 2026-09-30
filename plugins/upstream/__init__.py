"""Upstream: changes merged into the code every zipper shares.

Each fetch looks at the checkout's origin. A commit on the upstream branch that
this checkout does not have yet is a queue row -- "merged: <subject>" -- in
every zipper, which is how a feature one person asked for reaches the others.
Applying it is `zipper update`, run by the scheduler when `plugins.upstream.auto_update` is
on; the row says what arrived, not whether it is running yet.
"""
import os, subprocess

from zipper import code, settings

name = 'upstream'


def jobs(conf):
    """Take merged changes on the fetch clock, if asked to. `zipper update` waits
    while a conversation is live, so an hourly try is what "when idle" means."""
    return [('update', 'every')] if conf.get('auto_update') else []


def _git(*args):
    return subprocess.run(['git'] + list(args), cwd=code.ROOT, capture_output=True,
                          text=True).stdout.strip()


def pull():
    code._fetch()


def fetched():
    p = os.path.join(code.ROOT, '.git', 'FETCH_HEAD')
    try:
        import datetime
        return datetime.datetime.fromtimestamp(os.path.getmtime(p)).isoformat(timespec='seconds')
    except OSError:
        return None


def snapshot():
    """Upstream commits this checkout lacks: {sha: [when, author, subject]}."""
    out = {}
    raw = _git('log', '--format=%H%x1f%cI%x1f%an%x1f%s',
               'HEAD..origin/%s' % code._branch())
    for ln in raw.splitlines():
        sha, when, who, subject = (ln.split('\x1f') + ['', '', ''])[:4]
        out[sha] = [when[:16], who, subject]
    return out


def events(before, after):
    return [{'system': 'zipper', 'action': 'push', 'when': w, 'who': who,
             'text': 'merged      %s  (%s)' % (subject[:70], sha[:8])}
            for sha, (w, who, subject) in sorted(after.items(), key=lambda kv: kv[1][0])
            if sha not in before]
