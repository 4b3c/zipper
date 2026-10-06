"""The bot's Discord status line: one word for what Zipper is doing.

    Working   a conversation is running a tool
    Thinking  conversations are busy, but only reasoning or writing
    Testing   no conversation is busy, and a `zipper code review` is running
    Waiting   nothing is

No conversation names: the status is visible to everyone in the server, and
which chat is busy is noise there. The bot polls `text()` every 30 seconds and
only talks to Discord when the word changes.

Busy means the same thing it does everywhere else: a headless Discord turn
holding its lock (`convhead.turn_running`), or a pane whose status line says
Claude is working (`convstate.state`). What a busy one is doing is the tail of
its transcript (`turnstatus.doing`). Read, never tracked -- a state kept here
would drift the moment a turn ended without telling us.
"""
import glob, json, os

from . import convhead, convstate, turnstatus
from .convcore import load

IDLE = 'Waiting'
# What `turnstatus.doing` says when no tool call is in flight.
NOT_A_TOOL = ('thinking', 'writing')


def _busy(tid, row):
    if convhead.turn_running(tid):
        return True
    # A pane. A closed row is skipped before `state` shells out to tmux for it.
    return not row.get('closed') and convstate.state(tid) == 'working'


def working():
    """Busy conversations' thread ids."""
    return [tid for tid, row in load().items() if _busy(tid, row)]


def testing():
    """Whether a code review is checking, testing or merging right now."""
    from . import review
    for p in glob.glob(os.path.join(review.DIR, '*.json')):
        try:
            with open(p, encoding='utf-8') as fh:
                job = json.load(fh)
        except (OSError, ValueError):
            continue
        if job.get('state') in turnstatus.REVIEW_DOING and turnstatus._review_process(job['slug']):
            return True
    return False


def text():
    busy = working()
    if busy:
        # Unreadable transcript ('') still means busy: say Working, which is true.
        if any(turnstatus.doing(tid) not in NOT_A_TOOL for tid in busy):
            return 'Working'
        return 'Thinking'
    return 'Testing' if testing() else IDLE
