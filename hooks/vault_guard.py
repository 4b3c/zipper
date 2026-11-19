#!/usr/bin/env python3
"""Keep two conversations from editing the same vault file. See `zipper/locks.py`.

Wired to Claude Code's tool hooks:

- **PreToolUse, edit tools** (Edit, Write, MultiEdit, NotebookEdit): claim the
  file, or refuse the call if another live conversation holds it.
- **PreToolUse, Bash**: remember what the working tree looks like.
- **PostToolUse, Bash**: claim what the command changed; tell the session if
  another conversation held any of it.
- **PostToolUse, Read**: warn that the file is mid-edit elsewhere.

Rules that matter
-----------------
- **Always exit 0, and never refuse because of an error here.** A broken guard
  must not stop every edit; it fails open, and the turn-end commit still commits
  only this conversation's files.
- **Only vault paths.** Code, /tmp and everything else pass straight through.
"""
import json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

EDIT_TOOLS = ('Edit', 'Write', 'MultiEdit', 'NotebookEdit')


def _since(t):
    return time.strftime('%H:%M', time.localtime(t))


def _out(event, **kw):
    return {'hookSpecificOutput': dict(hookEventName=event, **kw)}


def handle(ev):
    # No vault named, no locking: core would fall back to the code checkout and
    # this would start claiming -- and committing -- source files.
    if not os.environ.get('ZIPPER_VAULT'):
        return None
    from zipper import locks
    name = ev.get('hook_event_name', '')
    tool = ev.get('tool_name', '')
    inp = ev.get('tool_input') or {}
    me = locks.owner(ev.get('session_id', ''))
    if not me:
        return None
    key = ev.get('tool_use_id') or '%s-%s' % (me, os.getpid())

    if name == 'PreToolUse' and tool in EDIT_TOOLS:
        path = inp.get('file_path') or inp.get('notebook_path')
        if not locks.rel(path):
            return None
        refused = locks.claim([path], me)
        if refused:
            r, (who, at) = next(iter(refused.items()))
            return _out('PreToolUse', permissionDecision='deny', permissionDecisionReason=(
                '%s is being edited by conversation %s (since %s) and isn\'t committed yet. '
                'You can read it, but not write it. Its edits commit and the file is '
                'released when that conversation\'s turn ends -- try again then, or work '
                'on something else first.' % (r, who, _since(at))))
        return None

    if name == 'PreToolUse' and tool == 'Bash':
        locks.snapshot(key)
        return None

    if name == 'PostToolUse' and tool == 'Bash':
        changed, since = locks.changed_since(key)
        cmd = inp.get('command', '')
        changed = [p for p in changed if not locks.touched_after(p, since, me)]
        mine = [p for p in changed if locks.attributable(p, cmd)]
        unsure = [p for p in changed if p not in mine]
        msgs = []
        clash = locks.claim(mine, me) if mine else {}
        if clash:
            lines = ['- %s (conversation %s, since %s)' % (r, who, _since(at))
                     for r, (who, at) in clash.items()]
            msgs.append(
                'That command changed vault files another conversation is editing:\n%s\n'
                'They were not claimed for you and will not be in your commit. Check '
                '`git -C %s diff -- <path>` for whether you overwrote its edit, and don\'t '
                'write these again until that conversation\'s turn ends.'
                % ('\n'.join(lines), os.environ.get('ZIPPER_VAULT', '.')))
        unsure = [p for p in unsure if not locks.holder(p, me)]
        if unsure:
            msgs.append(
                'These vault files changed while that command ran, but it doesn\'t name '
                'them, so they weren\'t claimed and won\'t be in your turn\'s commit: %s. '
                'If your command wrote them, run `zipper locks --claim <path>...`; if not, '
                'leave them -- the next pass reviews them.' % ', '.join(unsure[:8]))
        if msgs:
            return _out('PostToolUse', additionalContext='\n\n'.join(msgs))
        return None

    if name == 'PostToolUse' and tool == 'Read':
        h = locks.holder(inp.get('file_path'), me)
        if h:
            return _out('PostToolUse', additionalContext=(
                'Heads up: conversation %s is editing this file (since %s) and hasn\'t '
                'committed. What you read may change when its turn ends; you can\'t write '
                'it until then.' % (h[0], _since(h[1]))))
    return None


def main():
    try:
        ev = json.load(sys.stdin)
        out = handle(ev)
        if out:
            json.dump(out, sys.stdout)
    except Exception:
        pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
