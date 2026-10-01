"""zipper.review

A change to the code is reviewed by testing it, not by a human reading it.

    zipper code review "<title>" "<purpose>" [--test "<how to test it>"]

From inside a worktree (`zipper code start`). It pushes the branch, opens the
pull request, and starts a detached reviewer; the conversation that asked ends
its turn and gets the verdict back as its next message. The reviewer, in order:

  1. **stale**     the branch must contain origin/main -- what is tested is
                   what gets merged. A change to `.github/` goes to the operator
  2. **checks**    compile, import, lint, and every unit test file, in the
                   worktree. Programmatic: a failure here is a trace, and no
                   agent is spent on it
  3. **tester**    a headless Claude in the worktree, told the purpose and how
                   to test it, exercises the change and ends with
                   `VERDICT: APPROVE` or `VERDICT: REJECT`
  4. **merge**     the PR's required checks pass, then an approving review and
                   an ordinary merge, both with the operator's GITHUB_TOKEN. The
                   App can do neither, so another person's zipper cannot change
                   main; GitHub still enforces review, checks and up-to-date
  5. **restart**   `zipper update`: fast-forward, check, restart, and roll the
                   checkout back if it does not come up. Then main is broken for
                   every zipper, so it is reverted at once -- a PR merged without
                   a tester, since it restores the tree that was running

A rejection at any step goes back to the conversation with the trace -- the
failing output, verbatim -- and the tester's reasoning. After MAX_ROUNDS
rejections of one branch the message says to stop and ask the operator.

**The tester must never touch the live system** -- its prompt says so, and it
runs in the worktree with no Discord thread, so its reply is never forwarded.
"""
import fcntl, glob, json, os, re, subprocess, sys, threading, time, urllib.request

from . import code, core, settings



def _live_root():
    """The running checkout. **Not settings.ROOT**: `zipper` is `python3 -m
    zipper` from the current directory, so typed inside a worktree it runs the
    worktree's code, and ROOT is the worktree. The reviewer, its job files and
    the update all belong to the checkout the zipper is actually running."""
    common = code._git('rev-parse', '--path-format=absolute', '--git-common-dir',
                       cwd=settings.ROOT).stdout.strip()
    return os.path.dirname(common) if common.endswith('/.git') else settings.ROOT


ROOT = _live_root()
DIR = os.path.join(ROOT, 'data', 'review')
MAX_ROUNDS = 3
TESTER_TIMEOUT = 45 * 60
CI_TIMEOUT = 25 * 60
CI_REGISTER_WAIT = 5 * 60
STALE_AFTER = 3 * 3600
VERDICT_RE = re.compile(r'^\W*VERDICT:\s*(APPROVE|REJECT)\b', re.M)


def _job_path(slug):
    return os.path.join(DIR, slug + '.json')


def _save(job):
    os.makedirs(DIR, exist_ok=True)
    tmp = _job_path(job['slug']) + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(job, fh, indent=1)
    os.replace(tmp, _job_path(job['slug']))


def _run(argv, cwd, timeout, env=None):
    """(returncode, output tail)."""
    try:
        r = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                           env=env)
        return r.returncode, (r.stdout + r.stderr)[-4000:]
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b'') + (e.stderr or b'')
        if isinstance(out, bytes):
            out = out.decode('utf-8', 'replace')
        return 124, out[-4000:] + '\n(timed out after %ds)' % timeout


# ---------------------------------------------------------------- submitting

def submit(path, title, purpose, how=''):
    """Push, open the PR, start the reviewer. Returns the job."""
    branch = code._git('rev-parse', '--abbrev-ref', 'HEAD', cwd=path).stdout.strip()
    slug = branch.split('/', 1)[-1]
    prev = {}
    if os.path.exists(_job_path(slug)):
        with open(_job_path(slug), encoding='utf-8') as fh:
            prev = json.load(fh)
        # A reviewer is a detached process; a container restart kills it and
        # leaves its job claiming to run. Past STALE_AFTER, believe the restart.
        if (prev.get('state') in ('queued', 'reviewing', 'merging')
                and time.time() - prev.get('submitted', 0) < STALE_AFTER):
            raise RuntimeError('a review of %s is already running (%s)' % (branch, prev['state']))
    url, _new = code.propose(path, title, purpose, request_review=False)
    from . import chat
    job = {'slug': slug, 'path': os.path.abspath(path), 'branch': branch, 'url': url,
           'pr': int(url.rstrip('/').rsplit('/', 1)[-1]), 'repo': code.ghapp.repo_slug(path),
           'title': title, 'purpose': purpose, 'test': how,
           'thread': chat.current_conversation() or '',
           'round': (prev.get('round', 0) + 1) if prev.get('state') == 'rejected' else 1,
           'state': 'queued', 'submitted': time.time()}
    _save(job)
    log = open(os.path.join(DIR, slug + '.log'), 'a')
    subprocess.Popen([sys.executable, '-m', 'zipper', 'code', '_review', _job_path(slug)],
                     cwd=ROOT, start_new_session=True, stdin=subprocess.DEVNULL,
                     stdout=log, stderr=subprocess.STDOUT)
    return job


# ---------------------------------------------------------------- reviewing

def checks(path):
    """'' when the worktree passes everything CI runs, else the trace."""
    problem = code.check(cwd=path)
    if problem:
        return problem
    for f in sorted(glob.glob(os.path.join(path, 'tests', 'test_*.py'))):
        mod = 'tests.' + os.path.basename(f)[:-3]
        rc, out = _run([sys.executable, '-m', 'unittest', mod], path, 600)
        if rc:
            return 'unit tests failed (%s):\n%s' % (mod, out)
    return ''


def touched(path, prefix):
    """Files under `prefix` the branch changes."""
    try:
        out = code._git('diff', '--name-only', 'origin/%s...HEAD' % code._branch(),
                        cwd=path).stdout
    except OSError:
        return []
    return [f for f in out.split() if f.startswith(prefix)]


def tester_prompt(job):
    tests = touched(job['path'], 'tests/')
    return '\n'.join([
        'You are the tester for a change to Zipper\'s own code. The working directory is a git '
        'worktree on branch %s (pull request #%d). Another Zipper conversation wrote it and is '
        'waiting on your verdict; if you approve, it is merged to main and the live zipper '
        'restarts onto it.' % (job['branch'], job['pr']),
        '',
        'Title: %s' % job['title'],
        'Purpose, in the author\'s words:',
        job['purpose'],
        '',
        'How the author says to test it:',
        job.get('test') or '(not given -- work it out from the purpose and the diff)',
        '',
        'Compile, import, lint and the unit tests already passed. Read CLAUDE.md here, then '
        '`git diff origin/main...HEAD`. Then exercise the changed behaviour for real: call the '
        'functions, run the CLI from this worktree, write throwaway scripts, render pages with '
        'tests/shot.py for UI. Approve only if it does what the purpose says and breaks nothing '
        'you can find.',
        '',
        ('This change edits its own tests: %s. Read those diffs first. Approve only if they '
         'add or correct tests, never if one is weakened or removed to make the change pass.'
         % ', '.join(tests)) if tests else '',
        '',
        'Rules:',
        '- Never touch the live system. Do not edit /zipper/code (the running checkout) or '
        '/zipper/vault, restart anything, push, merge, comment on GitHub or send Discord '
        'messages.',
        '- Run anything that imports zipper with an empty environment (CLAUDE.md section 7): '
        '`env -i PATH="$PATH" HOME=$T ZIPPER_SETTINGS=$T/settings.json ZIPPER_ENV_FILE=$T/env '
        'ZIPPER_VAULT=$T/vault python3 ...` with T=$(mktemp -d). The live environment holds the '
        'real Discord token.',
        '- Do not fix the code. If it is wrong, say exactly what is wrong and how you found it.',
        '',
        'End with what you tested and what happened, then, as the very last line, exactly '
        '`VERDICT: APPROVE` or `VERDICT: REJECT`.',
    ])


def verdict(text):
    """'APPROVE', 'REJECT' or None -- the last verdict line wins."""
    found = VERDICT_RE.findall(text or '')
    return found[-1] if found else None


def tester(job):
    """(verdict or None, the tester's reply)."""
    from . import convhead
    env = dict(os.environ)
    env.pop('ZIPPER_DISCORD_THREAD', None)
    # A pane marker: the Stop hook never forwards from one, so the tester's
    # reply cannot land in any Discord thread.
    env['ZIPPER_CONVERSATION'] = 'review-' + job['slug']
    argv = [convhead._claude(), '-p', '--output-format', 'json', '--permission-mode',
            os.environ.get('ZIPPER_PERMISSION_MODE', 'auto'), tester_prompt(job)]
    # Stdout alone and whole: `_run` keeps a tail of stdout+stderr, which cuts
    # the JSON open (a reply over a few KB) or prefixes it with a warning.
    try:
        r = subprocess.run(argv, cwd=job['path'], env=env, capture_output=True, text=True,
                           timeout=TESTER_TIMEOUT)
    except subprocess.TimeoutExpired:
        return None, 'the tester ran past %d minutes' % (TESTER_TIMEOUT // 60)
    return parse_tester(r.stdout, r.stderr)


def parse_tester(stdout, stderr=''):
    """(verdict, reply) from `claude -p --output-format json` output."""
    try:
        reply = json.loads(stdout).get('result') or ''
    except ValueError:
        reply = (stdout + '\n' + stderr)[-4000:]
    return verdict(reply), reply


def _wait_quiet(limit=3600):
    from . import supervise
    end = time.time() + limit
    while supervise.turns_running() and time.time() < end:
        time.sleep(10)


def merge_and_restart(job):
    """(ok, stage, detail). Serialised: one merge-and-restart at a time."""
    os.makedirs(DIR, exist_ok=True)
    with open(os.path.join(DIR, 'merge.lock'), 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        code._fetch()
        if code._git('merge-base', '--is-ancestor', 'origin/' + code._branch(), 'HEAD',
                     cwd=job['path']).returncode:
            return False, 'stale', ('main moved while this was being tested. Rebase '
                                    '(`git -C %s rebase origin/main`) and review again.'
                                    % job['path'])
        pr, repo = str(job['pr']), job['repo']
        body = 'Approved by %s\'s tester after testing it.\n\n%s' % (
            settings.zipper_id(), (job.get('tester') or '')[-6000:])
        problem = approve_and_merge(pr, repo, body)
        if problem:
            return False, problem[0], problem[1]
        _wait_quiet()
        lines = []
        rc = code.update(force=True, log=lines.append)
        if rc and any('ROLLED BACK' in ln for ln in lines):
            # The new code did not run here, so main is broken for every
            # zipper. Put main back to the tree that was running.
            try:
                job['revert'] = revert(job)
            except Exception as e:      # the rollback lines must still reach the author
                job['revert'] = 'The revert crashed -- main is still broken: %r' % e
            return False, 'restart', ('merged, but it did not come up, so this zipper rolled '
                                      'back to the previous version.\n%s\n\n%s'
                                      % ('\n'.join(lines), job['revert']))
        if rc:
            return False, 'restart', ('merged, but `zipper update` did not take it:\n'
                                      + '\n'.join(lines))
    return True, 'merged', '\n'.join(lines)


def approve_and_merge(pr, repo, body):
    """None, or (stage, detail). Waits for the required checks, then an approving
    review and an ordinary merge, both with the operator's GITHUB_TOKEN. The App
    authored the PR, so the operator's account may approve it, and GitHub still
    enforces the review, the checks and up-to-date itself."""
    # A PR opened a moment ago has no checks yet, and `--watch` does not wait
    # for them to appear: it fails at once with "no checks reported". Not yet
    # is not failed, so ask again until they register.
    end = time.time() + CI_REGISTER_WAIT
    while True:
        rc, out = _run(['gh', 'pr', 'checks', pr, '-R', repo, '--required', '--watch',
                        '--fail-fast', '--interval', '20'], ROOT, CI_TIMEOUT)
        if not (rc and 'checks reported' in out.lower() and time.time() < end):
            break
        time.sleep(10)
    if rc:
        return 'ci', 'the required checks did not pass:\n' + out
    rc, out = _run(['gh', 'pr', 'review', pr, '-R', repo, '--approve', '--body', body], ROOT, 120)
    if rc:
        return 'merge', 'could not approve (nothing is wrong with the code):\n' + out
    rc, out = _run(['gh', 'pr', 'merge', pr, '-R', repo, '--merge'], ROOT, 120)
    if rc:
        return 'merge', 'gh pr merge failed (nothing is wrong with the code):\n' + out
    return None


def revert(job):
    """Revert a merge that did not come up, as its own PR, merged without a
    tester: it restores the tree this zipper was running a minute ago. Returns
    a line for the report."""
    pr, repo = str(job['pr']), job['repo']
    rc, sha = _run(['gh', 'pr', 'view', pr, '-R', repo, '--json', 'mergeCommit',
                    '--jq', '.mergeCommit.oid'], ROOT, 60)
    sha = sha.strip()
    if rc or not sha:
        return 'Could not find the merge commit to revert -- main is still broken:\n' + sha
    try:
        slug = 'revert-%s-%d' % (job['slug'], int(time.time()))
        branch, path = code.start(slug)
        r = code._git('revert', '--no-edit', '-m', '1', sha, cwd=path)
        if r.returncode:
            return 'git revert failed -- main is still broken:\n' + (r.stderr or r.stdout)
        url, _new = code.propose(path, 'Revert #%s: it did not come up' % pr,
                                 'Reverts #%s (%s). It passed its tester but %s did not come '
                                 'up on it and rolled back. This restores the tree that was '
                                 'running.' % (pr, sha[:8], settings.zipper_id()),
                                 request_review=False)
    except Exception as e:          # git, GitHub or the network: report, never raise
        return 'Could not open the revert -- main is still broken: %r' % e
    num = url.rstrip('/').rsplit('/', 1)[-1]
    problem = approve_and_merge(num, repo, 'Reverts #%s, which did not come up on %s.'
                                % (pr, settings.zipper_id()))
    if problem:
        return 'Opened %s but could not merge it (%s) -- main is still broken:\n%s' % (
            url, problem[0], problem[1])
    try:
        code._git('worktree', 'remove', '--force', path)
        code._git('branch', '-D', branch)
        # Main now has the tree this zipper is already running: move onto it
        # without a restart, so `update` has nothing to do.
        code._fetch()
        base = 'origin/' + code._branch()
        if not code._git('diff', '--quiet', 'HEAD', base).returncode:
            code._git('merge', '--ff-only', '-q', base)
    except Exception as e:
        return 'Reverted on main: %s (tidying up after failed: %r).' % (url, e)
    return 'Reverted on main: %s.' % url


# Set by `report` so an announcement still waiting does not post after the verdict.
_reported = threading.Event()
_announcer = None


def _announce(job):
    """Post the review's status message once the asking turn has replied.

    The asking turn ends as soon as it submits, so the thread goes quiet while
    this runs, and a status message says it has not (bot/status.py). It waits
    for that turn's reply first: posted at once, it lands between the turn's
    "✅ Took" line and its reply, and the thread no longer reads working / answer /
    testing / verdict. "Not running" means the reply is out, because the Stop
    hook sends it from inside the turn's process.
    """
    global _announcer
    tid = job.get('thread') or ''
    if not tid:
        return

    def run():
        from . import chat, turnstatus
        end = time.time() + 600
        while time.time() < end and not _reported.is_set():
            try:
                if not turnstatus.activity(tid)[0]:
                    break
            except Exception:
                break
            time.sleep(2)
        if not _reported.is_set():
            chat.discord_status(True, tid, 'review', 'Tester on PR #%d, round %d of %d'
                                % (job['pr'], job['round'], MAX_ROUNDS))

    _announcer = threading.Thread(target=run, name='review-announce', daemon=True)
    _announcer.start()


def review(path):
    with open(path, encoding='utf-8') as fh:
        job = json.load(fh)
    job.update(state='reviewing', sha=code._git('rev-parse', 'HEAD', cwd=job['path']).stdout.strip())
    _save(job)
    _announce(job)
    ok, stage, detail, said = False, '', '', ''
    try:
        code._fetch()
        if code._git('merge-base', '--is-ancestor', 'origin/' + code._branch(), 'HEAD',
                     cwd=job['path']).returncode:
            stage, detail = 'stale', ('the branch does not contain origin/main. Rebase '
                                      '(`git -C %s rebase origin/main`) and review again.'
                                      % job['path'])
        elif touched(job['path'], '.github/'):
            stage, detail = 'workflows', ('this changes %s. CI is the check the tester relies on, '
                                          'so a change to it goes to the operator: '
                                          '`zipper code propose`.' % ', '.join(
                                              touched(job['path'], '.github/')))
        else:
            detail = checks(job['path'])
            stage = 'checks' if detail else 'tester'
            if not detail:
                v, said = tester(job)
                if v == 'APPROVE':
                    job['state'] = 'merging'
                    _save(job)
                    ok, stage, detail = merge_and_restart(job)
                elif v is None:
                    detail = 'the tester ended without a verdict line'
    except Exception as e:
        ok, stage, detail = False, 'reviewer', repr(e)
    job.update(state='merged' if ok else 'rejected', stage=stage, detail=detail,
               tester=said, finished=time.time())
    _save(job)
    report(job)
    return 0 if ok else 1


# ---------------------------------------------------------------- reporting

def message(job):
    head = '[review] %s: %s (PR #%d, round %d of %d)' % (
        'APPROVED, merged and live' if job['state'] == 'merged' else 'REJECTED at ' + job['stage'],
        job['title'], job['pr'], job['round'], MAX_ROUNDS)
    parts = [head]
    if job.get('detail'):
        parts += ['', '```', job['detail'][-3000:].strip(), '```']
    if job.get('tester'):
        parts += ['', 'Tester:', job['tester'][-12000:].strip()]
    if job['state'] == 'merged':
        parts += ['', 'Check it where the operator would see it, and tell them. Then '
                  '`git -C %s worktree remove %s`.' % (ROOT, job['path'])]
    elif job['round'] >= MAX_ROUNDS:
        parts += ['', 'That is %d rejections of this branch. Stop and ask the operator before trying '
                  'again.' % job['round']]
    else:
        parts += ['', 'Fix it in %s, commit, and `zipper code review` again.' % job['path']]
    return '\n'.join(parts)


def report(job):
    """Hand the verdict to the conversation that asked; Discord's notify
    channel if there is none, or it cannot be reached."""
    from . import chat
    text, tid = message(job), job.get('thread') or ''
    _reported.set()
    if _announcer is not None:
        _announcer.join(30)         # one mid-post finishes, so the close finds it
    if tid:
        chat.discord_status(False, tid, 'review', 'approved, merged' if job['state'] == 'merged'
                            else 'rejected at ' + job.get('stage', '?'))
    try:
        if tid and not tid.startswith('local-'):
            code.healthy(120)
            url = (os.environ.get('ZIPPER_URL') or 'http://127.0.0.1:8800').rstrip('/') + '/discord'
            req = urllib.request.Request(url, data=json.dumps(
                {'discord_thread_id': tid, 'content': text, 'source': 'review'}).encode(),
                headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=300) as fh:
                if json.loads(fh.read() or b'{}').get('ok'):
                    return
        elif tid:
            from . import convcore
            if convcore.alive(tid) and convcore.paste(tid, text).get('ok'):
                return
    except Exception as e:
        print('review: could not reach %s: %r' % (tid, e), flush=True)
    chat.discord_send('%s: %s' % (settings.zipper_id(), text),
                      thread_id=core.cfg('ZIPPER_NOTIFY_CHANNEL') or None)


# ---------------------------------------------------------------- commands

def cmd_review(a):
    try:
        job = submit(a.path or os.getcwd(), a.title, a.purpose, a.test or '')
    except RuntimeError as e:
        print('review: %s' % e)
        return 1
    print('review: %s is being tested (round %d of %d) -- %s\nEnd your turn; the verdict arrives '
          'as the next message in this conversation.' % (job['branch'], job['round'], MAX_ROUNDS, job['url']))
    return 0


def cmd_run_review(a):
    return review(a.job)
