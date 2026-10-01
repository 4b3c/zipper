"""The bot's Discord status line: what Zipper is working on, if anything.

The newest busy conversation's title, with a count of the others --
"Pantry pricing (+2)" -- or "Waiting" when no turn is running. The bot polls
`text()` every 30 seconds and only talks to Discord when the answer changes.

Busy means the same thing it does everywhere else: a headless Discord turn
holding its lock (`convhead.turn_running`), or a pane whose status line says
Claude is working (`convstate.state`). Read, never tracked -- a state kept here
would drift the moment a turn ended without telling us.
"""
from . import convhead, convstate
from .convcore import load

IDLE = 'Waiting'

# Discord allows 128 characters; a status that long is truncated in the member
# list anyway, and the first message of a thread can be a paragraph.
TITLE_MAX = 40


def _name(tid, row):
    """The conversation's best name: Claude's own title, then the thread's
    rename, then the first words of the message that opened it."""
    name = (convstate.title(tid) or row.get('discord_name') or row.get('title') or '').strip()
    name = ' '.join(name.split())
    if len(name) > TITLE_MAX:
        name = name[:TITLE_MAX - 1].rstrip() + '…'
    return name or 'a conversation'


def _busy(tid, row):
    if convhead.turn_running(tid):
        return True
    # A pane. A closed row is skipped before `state` shells out to tmux for it.
    return not row.get('closed') and convstate.state(tid) == 'working'


def working():
    """Busy conversations as (thread_id, name), newest message first."""
    rows = [(tid, row) for tid, row in load().items() if _busy(tid, row)]
    rows.sort(key=lambda r: convstate.last_active(r[0]) or 0, reverse=True)
    return [(tid, _name(tid, row)) for tid, row in rows]


def text():
    busy = working()
    if not busy:
        return IDLE
    name = busy[0][1]
    return name if len(busy) == 1 else '%s (+%d)' % (name, len(busy) - 1)
