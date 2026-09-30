"""zipper.code

How a zipper changes the code every zipper runs, and how it takes changes in.

    zipper code start <slug>          a worktree on branch <id>/<slug>, off upstream
    zipper code propose "<title>"     push that branch as the App, open a pull request
    zipper code prs                   what is open, and what was merged lately
    zipper update [--check] [--force] take merged changes: pull, check, restart, or roll back

**Nobody edits the running checkout.** It would drift from every other zipper's,
and the next pull would conflict. A change is a branch in its own worktree, a pull
request, and a human's merge -- branch protection makes that last step a rule
GitHub enforces rather than one we promise to keep, since the App cannot approve.

**`update` is careful because nobody is watching.** In order:

  1. fetch; stop if already current
  2. refuse if the checkout has local edits (someone broke the first rule)
  3. wait if a conversation is live -- restarting under a turn in progress is
     how its reply gets lost. The scheduler simply tries again next hour
  4. fast-forward only; anything else is a history this zipper does not share
  5. check the new code: it compiles, the CLI imports, `lint` passes
  6. restart, then check the dashboard answers
  7. at 5 or 6: reset to the old commit and restart again, and say so

So a bad merge leaves a zipper one version behind and reporting it, never broken.
"""
import os, subprocess, sys, time, urllib.request

from . import core, ghapp, settings

ROOT = settings.ROOT
WORK = os.path.join(ROOT, 'data', 'work')


def _git(*args, cwd=ROOT, check=False):
    r = subprocess.run(['git'] + list(args), cwd=cwd, capture_output=True, text=True)
    if check and r.returncode:
        raise RuntimeError('git %s: %s' % (' '.join(args), (r.stderr or r.stdout).strip()))
    return r


def _branch():
    return os.environ.get('ZIPPER_CODE_BRANCH') or 'main'


def _fetch():
    """Fetch upstream. Reading a public repo needs no credential; a private one
    gets the App's token for the one command."""
    r = _git('fetch', '-q', 'origin', _branch())
    if r.returncode:
        try:
            url = ghapp.push_url(_git('remote', 'get-url', 'origin').stdout.strip(), ghapp.token())
            r = _git('fetch', '-q', url, '+%s:refs/remotes/origin/%s' % (_branch(), _branch()))
        except Exception:
            pass
    if r.returncode:
        raise RuntimeError('cannot fetch origin: %s' % r.stderr.strip())


# ---------------------------------------------------------------- proposing

def start(slug):
    slug = slug.strip().strip('/').replace(' ', '-')
    branch = '%s/%s' % (settings.zipper_id(), slug)
    path = os.path.join(WORK, slug)
    if os.path.exists(path):
        raise RuntimeError('%s already exists -- work there, or remove it with '
                           '`git worktree remove %s`' % (path, path))
    _fetch()
    os.makedirs(WORK, exist_ok=True)
    _git('worktree', 'add', '-q', '-b', branch, path, 'origin/' + _branch(), check=True)
    try:
        name, email = ghapp.identity()
        _git('config', 'user.name', name, cwd=path)
        _git('config', 'user.email', email, cwd=path)
    except RuntimeError:
        pass            # no App: commits fall back to the checkout's identity
    return branch, path


def propose(path, title, body='', draft=False):
    """Push the worktree at `path` and open (or find) its pull request."""
    branch = _git('rev-parse', '--abbrev-ref', 'HEAD', cwd=path).stdout.strip()
    if branch in ('HEAD', _branch()):
        raise RuntimeError('not on a proposal branch (%s) -- start one with `zipper code start`'
                           % branch)
    if _git('status', '--porcelain', '--untracked-files=no', cwd=path).stdout.strip():
        raise RuntimeError('uncommitted changes in %s -- commit them first' % path)
    ahead = _git('rev-list', '--count', 'origin/%s..HEAD' % _branch(), cwd=path).stdout.strip()
    if ahead in ('', '0'):
        raise RuntimeError('nothing to propose: %s has no commits past origin/%s'
                           % (branch, _branch()))
    if ghapp._push(path):
        raise RuntimeError('push failed')
    slug = ghapp.repo_slug(path)
    tok = ghapp.token()
    owner = slug.split('/')[0]
    found = ghapp.api('/repos/%s/pulls?head=%s:%s&state=open' % (slug, owner, branch), tok)
    if found:
        return found[0]['html_url'], False
    foot = '\n\n---\nProposed by **%s**.' % settings.zipper_id()
    pr = ghapp.api_json('/repos/%s/pulls' % slug, tok, body={
        'title': title, 'head': branch, 'base': _branch(), 'body': (body or '') + foot,
        'draft': bool(draft)})
    if pr.get('error'):
        raise RuntimeError('GitHub refused the pull request (%s): %s'
                           % (pr['error'], pr.get('message')))
    # Ask the repository's owner to review. They are the one who merges.
    ghapp.api_json('/repos/%s/pulls/%d/requested_reviewers' % (slug, pr['number']), tok,
                   body={'reviewers': [owner]})
    return pr['html_url'], True


def prs():
    slug = ghapp.repo_slug()
    tok = ghapp.token()
    open_ = ghapp.api('/repos/%s/pulls?state=open&per_page=30' % slug, tok)
    closed = ghapp.api('/repos/%s/pulls?state=closed&per_page=15&sort=updated&direction=desc'
                       % slug, tok)
    return open_, [p for p in closed if p.get('merged_at')]


# ---------------------------------------------------------------- updating

def behind():
    """(head, upstream, [subject lines]) after a fetch."""
    _fetch()
    head = _git('rev-parse', 'HEAD').stdout.strip()
    up = _git('rev-parse', 'origin/' + _branch()).stdout.strip()
    log = _git('log', '--format=%h %s', '%s..%s' % (head, up)).stdout.strip()
    return head, up, [ln for ln in log.splitlines() if ln]


def check(cwd=ROOT):
    """'' when the checkout is fit to run, else what is wrong with it."""
    py = sys.executable
    for argv, what in (([py, '-m', 'compileall', '-q', 'zipper', 'bot', 'hooks'], 'compile'),
                       ([py, '-c', 'import zipper.cli, zipper.serve, zipper.supervise'], 'import'),
                       ([py, '-m', 'zipper', 'lint'], 'lint')):
        r = subprocess.run(argv, cwd=cwd, capture_output=True, text=True)
        if r.returncode:
            return '%s failed: %s' % (what, (r.stderr or r.stdout).strip()[-400:])
    return ''


def healthy(timeout=60):
    url = (os.environ.get('ZIPPER_URL') or 'http://127.0.0.1:8800').rstrip('/') + '/api/conversations'
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(url, timeout=5) as fh:
                if fh.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(2)
    return False


def update(force=False, check_only=False, log=print):
    from . import runqueue, supervise
    head, up, subjects = behind()
    if head == up or not subjects:
        log('update: current (%s)' % head[:8])
        return 0
    log('update: %d new commit(s) on origin/%s:' % (len(subjects), _branch()))
    for s in subjects:
        log('  ' + s)
    if check_only:
        return 0
    if _git('status', '--porcelain', '--untracked-files=no').stdout.strip():
        log('update: REFUSED -- the running checkout has local edits. Changes go through '
            '`zipper code start`; move these to a branch, then update.')
        return 1
    live = runqueue.live_others()
    if live and not force:
        log('update: waiting -- %d conversation(s) live; a restart now could lose a reply. '
            'The scheduler will try again.' % len(live))
        return 2
    r = _git('merge', '--ff-only', '-q', 'origin/' + _branch())
    if r.returncode:
        log('update: REFUSED -- not a fast-forward: %s' % r.stderr.strip())
        return 1
    problem = check()
    if problem:
        _git('reset', '--hard', '-q', head)
        log('update: ROLLED BACK to %s -- the new code did not pass: %s' % (head[:8], problem))
        _notify('Update to %s was rolled back: %s' % (up[:8], problem))
        return 1
    if supervise.turns_running():
        # This update was started from inside a turn (the scheduler waits for
        # quiet). Restarting now would cut that turn off, so the restart, the
        # health check and any rollback run once it has finished.
        supervise.when_idle(['update', '--finish', head, up])
        log('update: at %s on disk; restart and health check will run when the current '
            'turn finishes' % up[:8])
        return 0
    return finish(head, up, log)


def finish(head, up, log=print):
    """Restart onto `up`, check it, and roll back to `head` if it does not come up."""
    from . import supervise
    supervise.restart()
    time.sleep(3)
    if not healthy():
        _git('reset', '--hard', '-q', head)
        supervise.restart()
        time.sleep(3)
        back = healthy()
        log('update: ROLLED BACK to %s -- the dashboard did not come back after restarting; '
            'after the rollback it is %s' % (head[:8], 'up' if back else 'STILL DOWN'))
        _notify('Update to %s was rolled back: the dashboard did not come back. After the '
                'rollback it is %s.' % (up[:8], 'up' if back else 'STILL DOWN -- look at the box'))
        return 1
    log('update: now at %s' % up[:8])
    return 0


def _notify(text):
    try:
        from . import chat
        chat.discord_send('%s: %s' % (settings.zipper_id(), text),
                          thread_id=core.cfg('ZIPPER_NOTIFY_CHANNEL') or None)
    except Exception:
        pass


# ---------------------------------------------------------------- commands

def cmd_code(a):
    try:
        if a.action == 'start':
            branch, path = start(a.slug)
            print('branch %s\nworktree %s\nEdit and commit there, then `zipper code propose "<title>"` '
                  'from inside it.' % (branch, path))
        elif a.action == 'propose':
            url, new = propose(a.path or os.getcwd(), a.title, a.body or '', a.draft)
            print('%s %s' % ('opened' if new else 'updated', url))
        else:
            open_, merged = prs()
            print('open:')
            for p in open_:
                print('  #%d  %-50s  %s' % (p['number'], p['title'][:50], p['head']['ref']))
            print('merged lately:')
            for p in merged[:10]:
                print('  #%d  %-50s  %s' % (p['number'], p['title'][:50], p['merged_at'][:10]))
        return 0
    except RuntimeError as e:
        print('code: %s' % e)
        return 1


def cmd_update(a):
    try:
        if a.finish:
            return finish(*a.finish)
        return update(force=a.force, check_only=a.check)
    except RuntimeError as e:
        print('update: %s' % e)
        return 1
