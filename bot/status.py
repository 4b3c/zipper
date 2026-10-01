"""The status message: one per thread and slot, edited every minute, gone at the answer.

The typing indicator says "busy" for ten seconds at a time and nothing else; on a
phone it is easy to miss and it cannot say how long, or whether anything is still
happening. So a message that Zipper received lands in the thread at once --

    ⏳ Got it, working on it (12:41)

-- is edited every minute while the work goes on --

    ⏳ Still working · 3 min · 12:44 · running zipper lint

-- and is **deleted** when the reply arrives. Deleted, not edited into the reply:
Discord notifies on a new message and never on an edit, and a reply can be several
messages long. The thread ends up reading question, answer.

**Slots.** `turn` is a conversation answering. `review` is `zipper code review`
testing a change the thread asked for; it outlives the turn that submitted it, so it
is its own message rather than a relabel of the turn's.

**A status that only counts is a clock.** Each minute asks `zipper.turnstatus`
whether the work is actually running. When it is not and nothing was posted after
the status, the status says so and stops: "⚠️ Stopped without answering". When
something was posted, the reply landed and only the cleanup was missed (the bot
was down when it was asked to delete), so it is deleted.

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


def _mins(started):
    m = int((time.time() - started) // 60)
    return '%d min' % m if m < 60 else '%dh %02dm' % (m // 60, m % 60)


def opening(slot, label):
    if slot == 'review':
        return '🧪 %s (%s)' % (label or 'Testing the change', _clock())
    return '⏳ Got it, working on it (%s)' % _clock()


def progress(row, doing):
    head = '🧪 ' + (row.get('label') or 'Testing the change') if row['slot'] == 'review' \
        else '⏳ Still working'
    parts = [head, _mins(row['started']), _clock()]
    if row.get('queued'):
        parts.append('your follow-up is next')
    if doing:
        parts.append(doing)
    return ' · '.join(parts)


def stopped(row):
    what = 'The review stopped' if row['slot'] == 'review' else 'Stopped'
    return '⚠️ %s without answering (%s, after %s). Send it again, or ask what happened.' % (
        what, _clock(), _mins(row['started']))


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


async def stop(thread_id, slot='turn'):
    """The work is answered: the status goes.

    A delete that fails keeps the row, marked `done`, and the next tick tries
    again. Dropping the row first would leave "Still working" in the thread with
    nothing left that knows about it.
    """
    k = _key(thread_id, slot)
    async with _lock:
        row = _load().get(k)
    if not row:
        return {'ok': True, 'had': False}
    gone = await _delete(row)
    async with _lock:
        state = _load()
        if state.get(k, {}).get('message_id') == row['message_id']:
            if gone:
                state.pop(k)
            else:
                state[k]['done'] = True
            _save(state)
    return {'ok': True, 'had': True, 'deleted': gone}


async def _delete(row):
    """Delete a status message. True once it is gone, however it went."""
    try:
        _, msg = await _message(row)
        if msg is None:
            return False
        await msg.delete()
    except discord.NotFound:
        pass
    except Exception as e:
        print(f"[discord] status delete failed: {e}")
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
            if await _delete(row):
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
            if running:
                await msg.edit(content=progress(row, doing))
                continue
            answered = await _answered_since(thread, row['message_id'])
            async with _lock:
                state = _load()
                if state.get(k, {}).get('message_id') != row['message_id']:
                    continue                # replaced or stopped meanwhile
                state.pop(k, None)
                _save(state)
            if answered:
                await msg.delete()
            else:
                await msg.edit(content=stopped(row))
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
