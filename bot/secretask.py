"""Secrets through a Discord form: a button in the thread, a pop-up form, straight to disk.

    zipper secret NAME --discord "label"                 -> config/.env
    zipper secret canvas --calendar --discord "label"    -> a calendar feed (Inbox/)

The conversation asks (POST /secretask); this posts a message with a button. The
server's owner taps it and gets a modal -- a pop-up form whose contents go only to the
bot, never into the channel, its history or search. The value is written here, in the
bot process, and the conversation is told only that it was saved. So it never passes
through Claude or its transcript, the same promise as the dashboard's one-time page
(zipper/secret.py), for a zipper that has no dashboard.

It does pass through Discord's servers, which a private page of your own would not.
For calendar links and API keys that is fine; for a bank password it is not.

**Who may answer.** Only the server's owner, only in this zipper's own server (several
zippers share one bot through the gateway, and every one of them receives every
click), and each button once, within an hour. Asks are kept in
`Inbox/secret-asks.json`, so a button still works after the bot restarts.

**Values are one printable line.** `.env` is one `NAME=value` per line and is re-read
with `splitlines()`, which breaks on a dozen characters besides `\n` (U+2028, \x85,
\x0b...); any of them in a pasted value would write a second variable of the sender's
choosing. So anything `str.isprintable()` rejects is refused, not just newlines.

**The conversation never hears the link, even in part.** What it is told is built
from the ingest's counts, never from its output, which can quote the URL.
"""
import asyncio, contextlib, io, json, os, re, secrets, threading, time, types

import discord

from zipper import core

PATH = os.path.join(core.INBOX, 'secret-asks.json')
TTL = 3600
BUTTON, FORM = 'zsecret:', 'zsecretm:'
CAL_LABEL = re.compile(r'^[a-z0-9][a-z0-9-]{0,23}$')
CAL_URL = re.compile(r'^(https?|webcal)://(\S+)$', re.I)
COUNTS = re.compile(r'^\S+: (\d+) event\(s\), (\d+) upcoming')

_lock = threading.Lock()


def _load():
    try:
        with open(PATH, encoding='utf-8') as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _save(asks):
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    tmp = PATH + '.tmp'
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as fh:
        json.dump(asks, fh, indent=1)
    os.replace(tmp, PATH)


def check(name, kind):
    """None if (name, kind) is a valid ask, else what is wrong."""
    from zipper import secret
    if kind == 'env':
        return None if secret.valid_name(name) else 'NAME is letters, digits and _'
    if kind == 'calendar':
        return None if CAL_LABEL.match(name) else 'a calendar label is short: canvas, google, work'
    return 'kind is env or calendar'


def new_ask(thread_id, name, label, kind):
    nonce = secrets.token_urlsafe(12)
    now = time.time()
    with _lock:
        asks = {k: v for k, v in _load().items() if now - v['at'] < TTL and not v.get('used')}
        asks[nonce] = {'thread': str(thread_id), 'name': name, 'label': label[:80],
                       'kind': kind, 'at': now}
        _save(asks)
    return nonce


def take(nonce):
    """The ask for this nonce, marked used -- or None if it is gone, used or stale.
    Checked and spent under one lock, so a double tap saves once."""
    with _lock:
        asks = _load()
        a = asks.get(nonce)
        if not a or a.get('used') or time.time() - a['at'] > TTL:
            return None
        a['used'] = True
        _save(asks)
        return dict(a, nonce=nonce)


def peek(nonce):
    a = _load().get(nonce)
    if not a or a.get('used') or time.time() - a['at'] > TTL:
        return None
    return a


def clean(kind, value):
    """(value, None) or (None, why it was refused)."""
    value = (value or '').strip()
    if not value:
        return None, 'it was empty'
    if not value.isprintable() or len(value.splitlines()) != 1:
        return None, 'it had a line break or an invisible character in it'
    if kind == 'calendar':
        m = CAL_URL.match(value)
        if not m:
            return None, "that isn't a calendar link (it should start with https:// or webcal://)"
        # webcal:// is https:// by another name, and the fetcher only speaks http(s).
        scheme = m.group(1).lower()
        value = '%s://%s' % ('https' if scheme == 'webcal' else scheme, m.group(2))
    return value, None


def store(ask, value):
    """Write it. Returns the line the conversation is told -- never the value."""
    if ask['kind'] == 'env':
        from zipper import setup
        setup.env_set(ask['name'], value, core.ENV_FILE)
        return '%s was saved to the zipper\'s secrets as %s.' % (ask['label'], ask['name'])
    from zipper import ics, scoops
    if not scoops.is_enabled('calendar'):
        scoops.settings.put('scoops.calendar.enabled', True)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = ics.cmd_ingest_ics(types.SimpleNamespace(source=value, label=ask['name'], match=None))
    counts = next((COUNTS.match(ln.strip()) for ln in out.getvalue().splitlines()
                   if COUNTS.match(ln.strip())), None)
    if rc or counts is None:
        return ('The %s link could not be fetched or read. Ask them to copy it again from '
                'the place the setup guide names, and post a new button.' % ask['label'])
    return ('The %s link was saved and read as the "%s" calendar: %s events, %s upcoming.'
            % (ask['label'], ask['name'], counts.group(1), counts.group(2)))


def is_home(guild):
    from bot.client import home_guild_id
    home = home_guild_id()
    return guild is not None and home is not None and guild.id == home


async def post(thread, nonce, label):
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label='Enter it privately', style=discord.ButtonStyle.primary,
                                    custom_id=BUTTON + nonce, emoji='🔒'))
    return await thread.send('🔒 **%s**: tap the button and paste it into the form. It goes '
                             'straight to your zipper\'s private files, not into this chat.' % label,
                             view=view)


async def on_interaction(interaction):
    """Every click and form in the server. Only ours, only home, only the owner."""
    data = interaction.data or {}
    cid = str(data.get('custom_id') or '')
    if not (cid.startswith(BUTTON) or cid.startswith(FORM)):
        return
    if not is_home(interaction.guild):
        return                      # another zipper's server: its own bot answers
    if interaction.user.id != interaction.guild.owner_id:
        await interaction.response.send_message('Only the owner of this server can do that.',
                                                ephemeral=True)
        return
    if interaction.type == discord.InteractionType.component and cid.startswith(BUTTON):
        nonce = cid[len(BUTTON):]
        ask = peek(nonce)
        if ask is None:
            await interaction.response.send_message('That button has expired or was already used. '
                                                    'Ask your zipper for a new one.', ephemeral=True)
            return
        form = discord.ui.Modal(title=ask['label'][:45], custom_id=FORM + nonce, timeout=900)
        form.add_item(discord.ui.TextInput(label='Paste it here', style=discord.TextStyle.long,
                                           max_length=4000, required=True))
        await interaction.response.send_modal(form)
        return
    if interaction.type == discord.InteractionType.modal_submit and cid.startswith(FORM):
        nonce = cid[len(FORM):]
        value = ''
        for row in data.get('components') or []:
            for c in row.get('components') or [row.get('component') or {}]:
                value = value or c.get('value') or ''
        ask = peek(nonce)
        if ask is None:
            await interaction.response.send_message('That form has expired. Ask your zipper for a '
                                                    'new button.', ephemeral=True)
            return
        value, why = clean(ask['kind'], value)
        if why:
            # Not spent: they can tap the button again and paste it properly.
            await interaction.response.send_message("Not saved: %s. Tap the button to try again."
                                                    % why, ephemeral=True)
            return
        ask = take(nonce)
        if ask is None:
            await interaction.response.send_message('That was already saved.', ephemeral=True)
            return
        await interaction.response.send_message('Saved. Your zipper is checking it now.',
                                                ephemeral=True)
        try:
            await interaction.message.edit(content='🔒 **%s**: saved.' % ask['label'], view=None)
        except Exception:
            pass
        try:
            said = await asyncio.to_thread(store, ask, value)
        except Exception as e:
            said = 'Saving %s failed: %s' % (ask['label'], type(e).__name__)
        from bot.client import post_to_zipper
        await post_to_zipper('[secret] %s Carry on from here.' % said, int(ask['thread']))
