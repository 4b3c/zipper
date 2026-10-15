"""The status message: one per thread and slot, edited every minute, gone at the answer.

The typing indicator says "busy" for ten seconds at a time and nothing else; on a
phone it is easy to miss and it cannot say how long, or whether anything is still
happening. So a message that Zipper received lands in the thread at once --

    [Working] Started 12:41

-- is edited every minute while the work goes on --

    [Working] Ran for 3m 12s · Last edited: 12:44 · Session 44% · Context 404k · running zipper lint

-- and when the reply arrives it becomes its closing line and stays:

    [Done] Ran for 4m 03s (12:41 → 12:45) · Session 45% · Context 410k

It stays above the reply as a divider, so a thread reads working, answer, testing,
verdict. The reply is still its own new message, never edited into the status:
Discord notifies on a new message and never on an edit, and a reply can be several
messages long.

**Slots.** `turn` is a conversation answering. `review` is `zipper code review`
testing a change the thread asked for; it outlives the turn that submitted it, so it
is its own message rather than a relabel of the turn's.

**A status that only counts is a clock.** Each minute asks `zipper.turnstatus`
whether the work is actually running. When it is not and nothing was posted after
the status, the status says so and stops: "[Stopped without answering]". When
something was posted, the reply landed and only the closing edit was missed (the
bot was down when it was asked), so it gets its closing line now.

State is on disk (`Inbox/discord-status.json`), so a bot restart picks up where it
was rather than leaving "still working" behind forever.
"""
import asyncio
import json
import os
import time

import discord

from bot.client import client, resolve_thread
from zipper import core, turnstatus

EVERY = 60
# A status is posted a moment before its turn takes the lock, so a tick landing in
# that gap would call a turn that has not started yet dead. Young ones wait a round.
GRACE = 30
PATH = os.path.join(core.INBOX, 'discord-status.json')

_lock = asyncio.Lock()
_task = None


def _load():
    try:
        with open(PATH, encoding='utf-8') as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _save(state):
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    tmp = PATH + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(state, fh, indent=1)
    os.replace(tmp, PATH)


def _key(thread_id, slot):
    return '%s:%s' % (thread_id, slot)


def _clock(ts=None):
    return time.strftime('%H:%M', time.localtime(ts or time.time()))


def _dur(started, now=None):
    """Exact: `48s`, `1m 48s`, `1h 02m`."""
    sec = max(0, int((now or time.time()) - started))
    if sec < 60:
        return '%ds' % sec
    if sec < 3600:
        return '%dm %02ds' % (sec // 60, sec % 60)
    return '%dh %02dm' % (sec // 3600, sec % 3600 // 60)


def meters(thread_id, slot='turn'):
    """`Session 44% · Context 404k`: the plan's 5-hour window, and how much this
    conversation's next message would carry. Blocking (a cached HTTP call and a
    transcript read), so callers run it off the event loop. Either part is left
    out when it can't be read -- a blank beats a wrong number."""
    from zipper import convstate, usage
    parts = []
    try:
        s = next((m for m in usage.read().get('meters') or [] if m.get('key') == 'session'), None)
        if s and s.get('pct') is not None:
            parts.append('Session %d%%' % round(s['pct']))
    except Exception:
        pass
    if slot == 'turn':
        try:
            n = convstate.context_tokens(str(thread_id))
            if n:
                parts.append('Context %s' % convstate.fmt_tokens(n))
        except Exception:
            pass
    return ' · '.join(parts)


def _join(*parts):
    return ' · '.join(p for p in parts if p)


def _label(row, default):
    return row.get('label') or default


def opening(slot, label):
    if slot == 'review':
        return _join('[Testing] ' + (label or 'Testing the change'), 'Started %s' % _clock())
    return '[Working] Started %s' % _clock()


def progress(row, doing, meter=''):
    if row['slot'] == 'review':
        head = '[Testing] ' + _label(row, 'Testing the change')
    else:
        head = '[Working]'
    return head + ' ' + _join('Ran for %s' % _dur(row['started']), 'Last edited: %s' % _clock(),
                              meter, 'your follow-up is next' if row.get('queued') else '', doing)


def finished(row, outcome='', meter=''):
    """What a status becomes once it is answered. It stays in the thread, above
    the reply, so the thread reads as working / answer / testing / verdict."""
    span = 'Ran for %s (%s → %s)' % (_dur(row['started']), _clock(row['started']), _clock())
    if row['slot'] == 'review':
        return _join('[Tested] ' + _label(row, 'Tested the change'), outcome, span)
    if outcome == STOPPED:
        return '[Stopped] ' + _join(span, meter)
    return '[Done] ' + _join(span, meter)


# The outcome `/discord` passes when `stop` ended the turn.
STOPPED = 'stopped'

# The outcome `/discord` passes when a message never reached the conversation.
# Nothing ran, so the status is deleted rather than closed: a "[Done]" line over
# the bot's failure notice would claim work that never happened.
UNDELIVERED = 'undelivered'


def stopped(row, reason='', meter=''):
    """A dead turn's closing line. `reason` is the API's refusal when that is
    what ended it (`turnstatus.api_error`): asking what happened would only
    hit the same wall, so the line says it instead."""
    if reason:
        return '[Refused] ' + _join(_short(reason), 'Last edited: %s' % _clock(), meter)
    what = '[Review stopped]' if row['slot'] == 'review' else '[Stopped without answering]'
    return what + ' ' + _join('Ran for %s' % _dur(row['started']), 'Last edited: %s' % _clock(), meter) + \
        '. Send it again, or ask what happened.'


def _short(text, most=300):
    return text if len(text) <= most else text[:most - 1] + '…'


async def _message(row):
    thread = await resolve_thread(int(row['thread']))
    if thread is None:
        return None, None
    return thread, thread.get_partial_message(int(row['message_id']))


async def start(thread_id, slot='turn', label=''):
    """A status for this thread and slot. A second `start` while one is showing
    is a message that arrived mid-turn: it waits for the current turn, so say so."""
    async with _lock:
        state = _load()
        k = _key(thread_id, slot)
        row = state.get(k)
        if row:
            if slot == 'turn':
                row['queued'] = True
            if label:
                row['label'] = label
            state[k] = row
            _save(state)
            try:
                _, msg = await _message(row)
                if msg:
                    await msg.edit(content=progress(row, ''))
            except Exception as e:
                print(f"[discord] status edit failed: {e}")
            return {'ok': True, 'message_id': row['message_id']}
        thread = await resolve_thread(int(thread_id))
        if thread is None:
            return {'ok': False, 'error': 'thread not found'}
        sent = await thread.send(opening(slot, label))
        state[k] = {'thread': str(thread_id), 'slot': slot, 'label': label,
                    'message_id': str(sent.id), 'started': time.time()}
        _save(state)
        _ensure_loop()
        return {'ok': True, 'message_id': str(sent.id)}


async def stop(thread_id, slot='turn', outcome=''):
    """The work is answered: the status becomes its closing line ("[Done] Ran for 4m 03s
    (13:59 → 14:03)") and stays, as the divider above the reply. A message
    that was never delivered (`UNDELIVERED`) has nothing to close, so its
    status is deleted instead.

    An edit that fails keeps the row, marked `done` with the text it should
    end on, and the next tick tries again. Dropping the row first would leave
    "Still working" in the thread with nothing left that knows about it. The
    kept row moves to its own key, off the slot: the slot is free at once, so
    the next turn's `start` posts a status of its own instead of finding the
    old one and relabelling it.
    """
    k = _key(thread_id, slot)
    async with _lock:
        row = _load().get(k)
    if not row:
        return {'ok': True, 'had': False}
    if outcome == UNDELIVERED:
        final = None
    else:
        final = finished(row, outcome, await asyncio.to_thread(meters, row['thread'], row['slot']))
    settled = await _settle(row, final)
    async with _lock:
        state = _load()
        if state.get(k, {}).get('message_id') == row['message_id']:
            row = state.pop(k)
            if not settled:
                row.update(done=True, final=final)
                state['%s:done:%s' % (k, row['message_id'])] = row
            _save(state)
    return {'ok': True, 'had': True, 'settled': settled}


async def _settle(row, text):
    """Edit a status to its closing line, or delete it when `text` is None.
    True once nothing is left to do.

    A message already gone, or a thread that cannot be resolved, counts as
    settled, as it does in `tick`: retrying it every round would stall every
    other status behind `resolve_thread`'s backoff, indefinitely.
    """
    try:
        _, msg = await _message(row)
        if msg is None:
            return True
        if text is None:
            await msg.delete()
        else:
            await msg.edit(content=text)
    except discord.NotFound:
        pass
    except Exception as e:
        print(f"[discord] status close failed: {e}")
        return False
    return True


async def _answered_since(thread, message_id):
    """Did Zipper post anything after the status? Then the answer landed."""
    async for m in thread.history(after=discord.Object(id=int(message_id)), limit=50):
        if m.author == client.user:
            return True
    return False


async def tick():
    """One round over every status showing."""
    for k, row in list(_load().items()):
        if row.get('done'):
            if await _settle(row, row['final'] if 'final' in row else finished(row)):
                async with _lock:
                    state = _load()
                    if state.get(k, {}).get('message_id') == row['message_id']:
                        state.pop(k)
                        _save(state)
            continue
        if time.time() - row.get('started', 0) < GRACE:
            continue
        try:
            running, doing = await asyncio.to_thread(
                turnstatus.activity, row['thread'], row['slot'])
            thread, msg = await _message(row)
            if msg is None:
                async with _lock:
                    state = _load()
                    state.pop(k, None)
                    _save(state)
                continue
            meter = await asyncio.to_thread(meters, row['thread'], row['slot'])
            if running:
                await msg.edit(content=progress(row, doing, meter))
                continue
            answered = await _answered_since(thread, row['message_id'])
            async with _lock:
                state = _load()
                if state.get(k, {}).get('message_id') != row['message_id']:
                    continue                # replaced or stopped meanwhile
                state.pop(k, None)
                _save(state)
            if answered:
                # The reply landed and only the closing edit was missed.
                await msg.edit(content=finished(row, '', meter))
            else:
                reason = '' if row['slot'] == 'review' else await asyncio.to_thread(
                    turnstatus.api_error, row['thread'])
                await msg.edit(content=stopped(row, reason, meter))
        except discord.NotFound:
            async with _lock:
                state = _load()
                if state.get(k, {}).get('message_id') == row['message_id']:
                    state.pop(k, None)
                    _save(state)
        except Exception as e:
            print(f"[discord] status tick failed for {k}: {e}")


async def _loop():
    while True:
        await asyncio.sleep(EVERY)
        if not _load():
            continue
        await tick()


def _ensure_loop():
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop())
