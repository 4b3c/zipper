#!/usr/bin/env python3
"""Commit what this turn edited, and release it. See `zipper/locks.py`.

Wired to Claude Code's **Stop** hook, beside `forward_reply.py`. Every turn ends
with the files it claimed committed -- only those, never the rest of the tree --
and released for the next conversation that wants them. Nobody has to remember
to commit, and a lock never outlives the turn that took it.

Rules that matter
-----------------
- **A refused commit hands the turn back once.** The vault's pre-commit check
  (`zipper fanout`) refuses when a project's status or money changed and other
  notes still say the old thing. That needs reading, so the session is sent back
  to do it (`decision: block`). If it stops again without fixing it
  (`stop_hook_active`), it is not sent back twice: the files stay uncommitted and
  the next pass shows them.
- **Always exit 0.** Exit 2 would trap the session; a block is said in JSON.
"""
import json, os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def message(paths, me):
    names = [os.path.splitext(os.path.basename(p))[0] for p in paths]
    shown = ', '.join(names[:6]) + (' and %d more' % (len(names) - 6) if len(names) > 6 else '')
    return 'End of turn: %s\n\nCommitted automatically for conversation %s.' % (shown, me)


def handle(ev):
    # No vault named, no locking: core would fall back to the code checkout and
    # this would start claiming -- and committing -- source files.
    if not os.environ.get('ZIPPER_VAULT'):
        return None
    from zipper import locks
    me = locks.owner(ev.get('session_id', ''))
    if not me:
        return None
    mine = locks.owned(me)
    if not mine:
        return None
    paths = [p for p in mine if p in locks.dirty()]
    ok, out = locks.commit(paths, message(paths, me))
    if ok:
        locks.release(me)
        return None
    if 'fanout' in out and not ev.get('stop_hook_active'):
        return {'decision': 'block', 'reason': (
            'Your edits weren\'t committed: the vault\'s pre-commit check refused them.\n\n'
            '%s\n\nFix every line it lists, then run '
            '`ZIPPER_FANOUT_CHECKED=1 zipper commit "<what changed>"` and finish your reply.'
            % out.strip()[:3000])}
    locks.release(me)
    sys.stderr.write('turn_commit: not committed: %s\n' % out.strip()[:500])
    return None


def main():
    try:
        out = handle(json.load(sys.stdin))
        if out:
            json.dump(out, sys.stdout)
    except Exception as e:
        sys.stderr.write('turn_commit: %s\n' % e)
    return 0


if __name__ == '__main__':
    sys.exit(main())
