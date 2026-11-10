"""The pre-commit check: when a project's numbers or status change, list every
note that links it, so the sentences restating the old value get re-read.

A fact recorded in its home note makes every copy of it elsewhere stale, and
nothing else notices: the copies were edited recently or are never edited, and
a freshness check by edit date passes both. A summary that still said "~$100"
under a new section recording $1,620 is the case this exists for.

The check blocks the commit until whoever is committing says they read the list
(`ZIPPER_FANOUT_CHECKED=1`). Staging a listed note doesn't count as reading it:
the stale summary above was in a note that commit had just edited.

    zipper fanout             what the working tree's changes would list
    zipper fanout --staged    the same for the index (what the hook runs)
"""
import os, re, subprocess, sys

from . import core

FIELDS = ('status', 'stage', 'revenue_to_date', 'paying_users', 'customers')
# Dated records say what was true then; a task names its project on every line.
DATED_TYPES = {'log', 'tasks', 'event', 'decision', 'view'}
SKIP_DIRS = {'.git', '.obsidian', 'Inbox', 'Log', 'Metrics'}
OK_ENV = 'ZIPPER_FANOUT_CHECKED'
BLOCKED = 3          # the hook's signal; any other failure lets the commit through

HOOK_MARK = '# zipper fanout hook'
HOOK = """#!/bin/sh
%s -- installed by zipper; edit freely, but remove this line or it is rewritten.
# Lists notes to re-read when a project's numbers or status change.
# Blocks only on its own verdict: if zipper is missing or broken, commits go through.
ZIPPER_VAULT="$(git rev-parse --show-toplevel)" PYTHONPATH=%s %s -m zipper fanout --staged
[ $? -eq %d ] && exit 1
exit 0
"""


def _git(*args):
    r = subprocess.run(['git', '-C', core.VAULT] + list(args),
                       capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def _fields(text):
    d = core.fm_dict(core.parse_fm(text or '')[0])
    return {k: d.get(k, '') for k in FIELDS}


def changed(staged=False, paths=None):
    """[(title, [(field, old, new)])] for notes whose FIELDS differ from HEAD.
    A new note has no old value for anything to restate, so it changes nothing."""
    if paths is None:
        out = _git('diff', '--name-only', '--diff-filter=M',
                   *(['--cached'] if staged else ['HEAD'])) or ''
        paths = [p for p in out.split('\n') if p.endswith('.md')]
    res = []
    for p in paths:
        old = _git('show', 'HEAD:%s' % p)
        if old is None:
            continue
        if staged:
            new = _git('show', ':%s' % p)
        else:
            try:
                new = open(os.path.join(core.VAULT, p), encoding='utf-8').read()
            except OSError:
                continue
        a, b = _fields(old), _fields(new)
        diff = [(k, a[k], b[k]) for k in FIELDS if a[k] != b[k]]
        if diff:
            res.append((core.title_of(p), diff))
    return res


def mentions(title):
    """[(rel path, line no, line)] for every line outside dated notes linking `title`."""
    link = re.compile(r'\[\[(?:[^\]|#]*/)?%s(?:[|#][^\]]*)?\]\]' % re.escape(title))
    out = []
    for dp, dn, fn in os.walk(core.VAULT):
        dn[:] = sorted(d for d in dn if d not in SKIP_DIRS)
        for f in sorted(fn):
            if not f.endswith('.md') or core.title_of(f) == title:
                continue
            path = os.path.join(dp, f)
            try:
                text = open(path, encoding='utf-8').read()
            except OSError:
                continue
            if core.fm_dict(core.parse_fm(text)[0]).get('type') in DATED_TYPES:
                continue
            for n, line in enumerate(text.split('\n'), 1):
                if link.search(line):
                    out.append((core.rel(path), n, line.strip()))
    return out


def check(staged=False, paths=None, out=sys.stdout):
    """Print what to re-read; return BLOCKED if there is any and it isn't acknowledged."""
    found = [(t, diff, mentions(t)) for t, diff in changed(staged, paths)]
    found = [f for f in found if f[2]]
    if not found:
        return 0
    if os.environ.get(OK_ENV) == '1':
        print('fanout: %d note(s) re-read, by %s=1' % (sum(len(f[2]) for f in found), OK_ENV),
              file=out)
        return 0
    for t, diff, lines in found:
        print('fanout: %s changed %s.' % (t, '; '.join(
            '%s %s -> %s' % (k, a or '(blank)', b or '(blank)') for k, a, b in diff)), file=out)
        for p, n, line in lines:
            print('  %s:%d  %s' % (p, n, core._snip(line, 110)), file=out)
    print('Re-read each line above and rewrite anything the change made false, then commit '
          'again with %s=1. Staging a note is not reading it.' % OK_ENV, file=out)
    return BLOCKED


def install_hook(vault=None):
    """Write the vault's pre-commit hook. Leaves a hook someone else wrote alone."""
    vault = vault or core.VAULT
    hooks = os.path.join(vault, '.git', 'hooks')
    if not os.path.isdir(hooks):
        return False
    path = os.path.join(hooks, 'pre-commit')
    root = os.path.dirname(core.HERE)
    want = HOOK % (HOOK_MARK, _sh(root), _sh(sys.executable), BLOCKED)
    try:
        have = open(path, encoding='utf-8').read()
    except OSError:
        have = None
    if have is not None and HOOK_MARK not in have:
        return False
    if have != want:
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as fh:
            fh.write(want)
        os.chmod(tmp, 0o755)
        os.replace(tmp, path)
    return True


def _sh(s):
    return "'%s'" % s.replace("'", "'\\''")


def cmd_fanout(a):
    return check(staged=a.staged, out=sys.stderr if a.staged else sys.stdout)
