#!/usr/bin/env python3
"""Put the queue in front of every new conversation.

Wired to Claude Code's **SessionStart** hook. On a new session (`source` is
`startup` or `clear`) it prints `Meta/Queue.md` as additional context with the
instruction to work it -- claim its rows, file them, commit -- before answering,
so the first reply is written with everything that arrived already filed.

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


def known():
    """Only conversations the registry knows -- Discord threads and panes --
    are told to do a pass. A review tester or a scheduled job must not start
    working the live queue because it happened to start a session."""
    who = os.environ.get('ZIPPER_DISCORD_THREAD') or os.environ.get('ZIPPER_CONVERSATION')
    vault = os.environ.get('ZIPPER_VAULT')
    if not who or not vault:
        return False
    try:
        with open(os.path.join(vault, 'Inbox', 'conversations.json'), encoding='utf-8') as fh:
            return str(who) in json.load(fh)
    except (OSError, ValueError, TypeError):
        return False


def context(event):
    if event.get('source', 'startup') not in NEW or not known():
        return ''
    p = queue_path(event.get('cwd', ''))
    if not p:
        return ''
    with open(p, encoding='utf-8') as fh:
        text = fh.read()
    if len(text) > LIMIT:
        text = text[:LIMIT] + '\n\n[... cut at %d characters; read %s for the rest]' % (LIMIT, p)
    return ('This is a new conversation. **Before answering the first message, do a '
            'pass:** run `zipper pull --due` (it refreshes the queue and claims its open '
            'rows for you; rows another conversation already claimed are marked, leave '
            'those), work each of your rows into the notes it affects as the vault\'s '
            'CLAUDE.md describes, `zipper commit "<what changed>"`, then answer. If '
            'nothing is open, answer straight away. If the first message bears on a row '
            '(a deadline, a reply someone is owed), say so in the answer.\n\n'
            'The queue as of this session\'s start (%s):\n\n%s' % (p, text))


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
