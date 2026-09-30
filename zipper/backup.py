"""zipper.backup

The vault's second copy: a git remote named `origin`, pushed after every commit.

**Git in the vault's own directory is history, not a backup** -- lose the directory
and the history goes with it. A backup is a remote somewhere else: a bare repo on
another disk, another machine, or at minimum outside the vault's directory (for a
container, outside its volume). `zipper init --backup <path>` creates one.

**Pushing is part of `commit`, and a failure is loud but not fatal.** The commit
already happened and is the thing a pass needs; the push is retried by the next
one. That is also why a flag watches it: nothing pushed for twelve days while every
commit succeeded, and the only way to notice was to go and look. Now the brief
says so -- when there is no remote at all, and when the remote falls a day behind.
"""
import datetime, os, subprocess

from .core import VAULT

STALE_HOURS = 24


def _git(*args):
    return subprocess.run(['git', '-C', VAULT] + list(args), capture_output=True, text=True)


def remote():
    r = _git('remote', 'get-url', 'origin')
    return r.stdout.strip() if r.returncode == 0 else ''


def attach(path, vault=VAULT):
    """Create a bare repo at `path` (if absent), make it `origin`, and push."""
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.exists(os.path.join(path, 'HEAD')):
        os.makedirs(path, exist_ok=True)
        subprocess.run(['git', 'init', '-q', '--bare', '-b', 'main', path], check=True)
    g = ['git', '-C', vault]
    have = subprocess.run(g + ['remote'], capture_output=True, text=True).stdout.split()
    subprocess.run(g + ['remote', 'set-url' if 'origin' in have else 'add', 'origin', path],
                   check=True)
    r = subprocess.run(g + ['push', '-q', '-u', 'origin', 'HEAD'], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError('push to %s failed: %s' % (path, r.stderr.strip()))
    return path


def push():
    """Push the current branch to origin. '' on success or when there is no remote
    (the flag covers that), else the error, for `commit` to print."""
    if not remote():
        return ''
    r = _git('push', '-q', 'origin', 'HEAD')
    return '' if r.returncode == 0 else (r.stderr or r.stdout).strip().split('\n')[-1]


def behind():
    """(unpushed commit count, hours since the oldest of them), or None without a remote.
    Compares with the local tracking ref, updated by every push -- no network."""
    if not remote():
        return None
    branch = _git('rev-parse', '--abbrev-ref', 'HEAD').stdout.strip()
    r = _git('log', '--format=%ct', 'origin/%s..HEAD' % branch)
    if r.returncode:
        # Never pushed at all: there is no tracking ref yet.
        r = _git('log', '--format=%ct')
    stamps = [int(x) for x in r.stdout.split()]
    if not stamps:
        return 0, 0.0
    age = (datetime.datetime.now().timestamp() - min(stamps)) / 3600
    return len(stamps), age


def flags():
    if not os.path.isdir(os.path.join(VAULT, '.git')):
        return ['the vault is not a git repository -- `zipper init` makes one']
    b = behind()
    if b is None:
        return ['the vault has no backup remote -- `zipper init --backup <path>` on a new '
                'vault, or `git remote add origin <bare repo>`; commits push to it']
    n, hours = b
    if n and hours >= STALE_HOURS:
        return ['the vault backup is %d commit(s) behind, the oldest %d day(s) old -- '
                '`zipper commit` pushes; check `git -C %s push origin HEAD`'
                % (n, hours // 24 or 1, VAULT)]
    return []
