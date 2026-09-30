"""Upstream: changes merged into the code every zipper shares.

Each fetch looks at the checkout's origin. A commit on the upstream branch that
this checkout does not have yet is a queue row -- "merged: <subject>" -- in
every zipper, which is how a feature one person asked for reaches the others.
Applying it is `zipper update`, run by the scheduler when `code.auto_update` is
on; the row says what arrived, not whether it is running yet.
"""
import os, subprocess

from .. import code, settings

name = 'upstream'
SETUP_TITLE = 'Code updates'
SETUP_ABOUT = 'a queue row when a change to the shared code is merged'


def setup(w):
    w.setting('code.repo', 'Upstream repository (owner/name)', default='4b3c/Zipper')
    w.setting('code.auto_update', 'Apply merged changes automatically when idle? (true/false)',
              default='true')
    v = settings.get('code.auto_update')
    settings.put('code.auto_update', str(v).lower() in ('true', '1', 'yes', 'y'))


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
