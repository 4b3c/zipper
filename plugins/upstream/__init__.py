"""Upstream: changes merged into the code every zipper shares.

Each fetch looks at the checkout's origin. A merge on the upstream branch that has
not been reported yet is a queue row -- "merged: <title> ... run zipper update" --
in every zipper, which is how a feature one person asked for reaches the others.
Applying it is `zipper update`: whoever works the queue runs it, and the scheduler
also runs it when `plugins.upstream.auto_update` is on.
"""
import json, os, re, subprocess

from zipper import code, core, settings

name = 'upstream'

# The last origin commit already turned into rows. Counting from here rather than
# from HEAD is what keeps a row from being lost: `zipper update` fetches and
# fast-forwards on its own clock, so by the time this plugin looks, HEAD may
# already hold the change and origin may already have moved.
SEEN = os.path.join(core.INBOX, 'upstream.json')
MERGE = re.compile(r'^Merge pull request #(\d+) from [^/\s]+/(\S+)')


def jobs(conf):
    """Take merged changes on the fetch clock, if asked to. `zipper update` waits
    while a conversation is live, so an hourly try is what "when idle" means."""
    return [('update', 'every:%d' % (conf.get('poll_minutes') or 60))] if conf.get('auto_update') else []


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


def _seen():
    try:
        with open(SEEN, encoding='utf-8') as fh:
            return json.load(fh).get('origin') or ''
    except (OSError, ValueError, AttributeError):
        return ''


def _mark(sha):
    os.makedirs(core.INBOX, exist_ok=True)
    tmp = '%s.%d.tmp' % (SEEN, os.getpid())
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump({'origin': sha}, fh)
    os.replace(tmp, SEEN)


def parse(raw, me):
    """`git log --first-parent` records (fields \\x1f, records \\x1e) ->
    {sha: [when, who, title, pr]}, without this zipper's own merges: it is
    already running those. A PR's title is its merge commit's first body line."""
    out = {}
    for rec in raw.split('\x1e'):
        sha, when, who, subject, body = (rec.strip('\n').split('\x1f') + [''] * 4)[:5]
        if not sha:
            continue
        pr, title = '', subject
        m = MERGE.match(subject)
        if m:
            pr, branch = m.groups()
            if '/' in branch:
                who = branch.split('/')[0]
                if who == me:
                    continue
            title = body.strip().split('\n')[0] or branch
        out[sha] = [when[:16], who, title, pr]
    return out


def snapshot():
    """{'origin': sha, 'new': {sha: [when, who, title, pr]}}: origin's head, and the
    merges on it not yet reported. With nothing reported yet, `new` is empty --
    the first look is a baseline, not news."""
    up = _git('rev-parse', '--verify', '-q', 'origin/%s' % code._branch())
    if not up:
        return {}
    seen = _seen()
    if not seen or seen == up:
        return {'origin': up, 'new': {}}
    raw = _git('log', '--first-parent', '--format=%H%x1f%cI%x1f%an%x1f%s%x1f%b%x1e',
               '%s..%s' % (seen, up))
    return {'origin': up, 'new': parse(raw, settings.zipper_id())}


def events(before, after):
    """Everything unreported in `after`, not only what this pull fetched: `zipper
    update` may have fetched it first. A row reported twice is one row (the queue
    keys on its text); a row never reported is the failure to avoid."""
    if not after or not after.get('origin'):
        return []
    rows = [{'system': 'zipper', 'action': 'push', 'when': w, 'who': who,
             'text': 'merged      %s  (%s)  -- run zipper update'
                     % (title[:70], '#' + pr if pr else sha[:8])}
            for sha, (w, who, title, pr) in sorted((after.get('new') or {}).items(),
                                                   key=lambda kv: kv[1][0])]
    _mark(after['origin'])
    return rows
