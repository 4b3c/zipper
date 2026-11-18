#!/usr/bin/env python3
"""Put the queue in front of every new conversation.

Wired to Claude Code's **SessionStart** hook. On a new session (`source` is
`startup` or `clear`) it prints `Meta/Queue.md` as additional context, so the
first reply is written already knowing what arrived since the last pass.

Why this exists
---------------
The queue holds what the operator assumes Zipper already knows: a payroll
deadline posted in Slack that morning, a teammate's push, an email owed a
reply. Reading it was an instruction in CLAUDE.md, and an instruction is
skipped exactly when the question looks unrelated -- a timesheet question was
answered for six turns while the queue said timesheets were due at 5:00 that
day. A hook doesn't depend on the session remembering.

Rules that matter
-----------------
- **New sessions only.** `resume` is every Discord turn after the first (each
  is a `claude -p --resume`), and `compact` keeps the summary; re-sending the
  queue there would repeat it every message.
- **Always exit 0, never print an error.** A missing vault or queue is a fresh
  zipper, not a failure, and a session must still start.
- **Read the file; don't regenerate it.** `zipper pull --due` rewrites it every
  few minutes, and a pull here would put a network fetch in front of the first
  reply and race the one the schedule is running.
"""
import json, os, sys

LIMIT = 40_000      # characters; a queue past this is a backlog, not a brief
NEW = ('startup', 'clear')


def queue_path(cwd=''):
    for base in (os.environ.get('ZIPPER_VAULT'), cwd):
        if base:
            p = os.path.join(base, 'Meta', 'Queue.md')
            if os.path.isfile(p):
                return p
    return ''


def context(event):
    if event.get('source', 'startup') not in NEW:
        return ''
    p = queue_path(event.get('cwd', ''))
    if not p:
        return ''
    with open(p, encoding='utf-8') as fh:
        text = fh.read()
    if len(text) > LIMIT:
        text = text[:LIMIT] + '\n\n[... cut at %d characters; read %s for the rest]' % (LIMIT, p)
    return ('The queue (%s), read before anything else. It is what has arrived since '
            'the last pass -- mail, Slack, pushes, uncommitted edits, flags -- and '
            'the operator assumes you know it. Check it against their first message.'
            '\n\n%s' % (p, text))


def main():
    try:
        event = json.load(sys.stdin)
    except Exception:
        event = {}
    try:
        ctx = context(event)
    except Exception:
        ctx = ''
    if ctx:
        try:
            json.dump({'hookSpecificOutput': {'hookEventName': 'SessionStart',
                                              'additionalContext': ctx}}, sys.stdout)
        except Exception:
            pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
