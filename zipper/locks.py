"""Who is editing which vault file, and the commit that ends it.

Several conversations share one vault and nothing used to stop two of them
editing the same note, or one `zipper commit` sweeping up another's
half-finished edit. Now a file belongs to the conversation that first touched
it, until the end of that conversation's turn:

- **Claiming.** The edit tools claim a file before they write it
  (`hooks/vault_guard.py`, PreToolUse). A shell command can't be read for which
  files it will touch, so it is checked afterwards: the files it changed are
  claimed, and one that another conversation holds is reported to the session as
  a collision.
- **Refusing.** Another conversation's edit tools are refused with who holds the
  file and that it will be free when that turn ends. Reading is allowed, with a
  warning that the file is mid-edit.
- **Releasing.** At the end of every turn the Stop hook (`hooks/turn_commit.py`)
  commits the files the turn holds -- only those, never `-a` -- and releases
  them. Claude doesn't have to remember to commit; `zipper commit` still works
  for a pass, which also ticks its rows, and releases the same way.

A lock whose owner is no longer live (no turn running, no pane open) is stale
and ignored, so a turn that died before its commit can't hold a file forever.

State lives in `Inbox/locks.json` under an flock. Hooks run on every tool call,
so the hot path imports nothing heavier than `core`.
"""
import os, json, time, fcntl, subprocess, contextlib

from . import core

LOCKS = os.path.join(core.INBOX, 'locks.json')
SNAPS = os.path.join(core.INBOX, 'locks-snap')
COMMIT_LOCK = os.path.join(core.INBOX, 'commit.lock')
# Paths no conversation owns: machine state and git itself.
SKIP = ('Inbox/', '.git/')


def owner(session_id=''):
    """This process's conversation; a bare Claude session as a fallback, so a
    session started outside zipper (a test, a shell) still holds its own files."""
    from . import chat
    return chat.current_conversation() or ('session-%s' % session_id if session_id else None)


def rel(path):
    """A vault-relative path, or None for anything outside the vault or skipped."""
    if not path:
        return None
    p = os.path.realpath(path if os.path.isabs(path) else os.path.join(core.VAULT, path))
    root = os.path.realpath(core.VAULT)
    if p != root and not p.startswith(root + os.sep):
        return None
    r = os.path.relpath(p, root)
    if r == '.' or any(r.startswith(s) for s in SKIP):
        return None
    return r


@contextlib.contextmanager
def _locked():
    os.makedirs(core.INBOX, exist_ok=True)
    with open(LOCKS + '.lock', 'a+') as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _load():
    try:
        with open(LOCKS, encoding='utf-8') as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _save(d):
    tmp = '%s.%d.tmp' % (LOCKS, os.getpid())
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(d, fh, indent=1)
    os.replace(tmp, LOCKS)


def live(who):
    """Is this owner mid-turn or an open pane? Called only when someone else
    holds a file, so the cost lands on collisions, not on every tool call."""
    if not who:
        return False
    if who.startswith('session-'):
        return True     # no registry to ask; released by its own Stop hook
    try:
        from . import convhead
        if convhead.turn_running(who):
            return True
        from . import conversations
        return any(c.get('thread_id') == who and c.get('alive')
                   for c in conversations.listing())
    except Exception:
        return True     # unsure: keep the lock rather than let two writers in


def holder(path, me):
    """Who else holds this vault path, live: (owner, since) or None."""
    r = rel(path)
    if not r:
        return None
    e = _load().get(r)
    if not e or e['owner'] == me or not live(e['owner']):
        return None
    return e['owner'], e['at']


def claim(paths, me):
    """Claim each path for `me`. Returns {path: (owner, since)} for the ones
    another live conversation holds; those are not taken."""
    refused = {}
    now = time.time()
    with _locked():
        d = _load()
        for p in paths:
            r = rel(p)
            if not r:
                continue
            e = d.get(r)
            if e and e['owner'] != me and live(e['owner']):
                refused[r] = (e['owner'], e['at'])
                continue
            d[r] = {'owner': me, 'at': e['at'] if e and e['owner'] == me else now,
                    'touched': now}
        _save(d)
    return refused


def touch(paths, me):
    """Mark that `me` is about to write these (an edit tool call), so a shell
    command running at the same moment in another conversation doesn't take
    the write for its own."""
    return claim(paths, me)


def owned(me):
    return sorted(r for r, e in _load().items() if e['owner'] == me)


def release(me, paths=None):
    with _locked():
        d = _load()
        for r in [r for r, e in d.items() if e['owner'] == me
                  and (paths is None or r in paths)]:
            del d[r]
        _save(d)


def held_by_others(me):
    """Every path a live conversation other than `me` holds."""
    return {r for r, e in _load().items() if e['owner'] != me and live(e['owner'])}


def listing():
    return [(r, e['owner'], e['at'], live(e['owner'])) for r, e in sorted(_load().items())]


# ---------------------------------------------------------------- shell commands

def dirty():
    """{path: (mtime, size)} for every changed or new file git sees."""
    out = subprocess.run(['git', '-C', core.VAULT, '-c', 'core.quotePath=false',
                          '--no-optional-locks', 'status', '--porcelain', '-z',
                          '--untracked-files=all'], capture_output=True, text=True).stdout
    res = {}
    parts = out.split('\0')
    i = 0
    while i < len(parts):
        ent = parts[i]
        i += 1
        if len(ent) < 4:
            continue
        code, path = ent[:2], ent[3:]
        if code[0] in 'RC':
            i += 1      # the rename's source follows as its own field
        try:
            st = os.stat(os.path.join(core.VAULT, path))
            res[path] = (st.st_mtime, st.st_size)
        except OSError:
            res[path] = (0, -1)     # deleted
    return res


def _snap_file(key):
    return os.path.join(SNAPS, '%s.json' % ''.join(c for c in key if c.isalnum() or c in '-_'))


def snapshot(key):
    os.makedirs(SNAPS, exist_ok=True)
    with open(_snap_file(key), 'w', encoding='utf-8') as fh:
        json.dump({'at': time.time(), 'dirty': dirty()}, fh)


def changed_since(key):
    """(paths a shell command changed, when it started). Files only some other
    conversation's edit tools touched during the command are left out: they
    wrote those, not this command."""
    try:
        with open(_snap_file(key), encoding='utf-8') as fh:
            snap = json.load(fh)
        os.remove(_snap_file(key))
    except (OSError, ValueError):
        return [], 0
    before = {p: tuple(v) for p, v in snap['dirty'].items()}
    now = dirty()
    out = [p for p, v in now.items() if before.get(p) != v and rel(p)]
    return out, snap['at']


def touched_after(path, since, me):
    e = _load().get(path)
    return bool(e and e['owner'] != me and e.get('touched', 0) >= since)


# ---------------------------------------------------------------- committing

def commit(paths, message, env=None):
    """Commit exactly these paths -- never the rest of the tree -- under one
    mutex, so two conversations committing at once queue instead of colliding
    on git's index.lock. Returns (ok, output)."""
    if not paths:
        return True, ''
    os.makedirs(core.INBOX, exist_ok=True)
    with open(COMMIT_LOCK, 'a+') as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            live_paths = [p for p in paths if p in dirty()]
            if not live_paths:
                return True, ''
            a = subprocess.run(['git', '-C', core.VAULT, 'add', '-A', '--'] + live_paths,
                               capture_output=True, text=True, env=env)
            if a.returncode:
                return False, a.stderr
            c = subprocess.run(['git', '-C', core.VAULT, 'commit', '-q', '-m', message,
                                '--only', '--'] + live_paths,
                               capture_output=True, text=True, env=env)
            if c.returncode:
                # Leave nothing half-staged for the next commit to sweep up.
                subprocess.run(['git', '-C', core.VAULT, 'reset', '-q', '--'] + live_paths,
                               capture_output=True)
            return c.returncode == 0, (c.stdout or '') + (c.stderr or '')
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def cmd_locks(a=None):
    rows = listing()
    if not rows:
        print('no vault file is being edited')
        return 0
    for r, who, at, alive in rows:
        print('%-50s %s since %s%s' % (r, who, time.strftime('%H:%M', time.localtime(at)),
                                        '' if alive else '  (stale: not live)'))
    return 0
