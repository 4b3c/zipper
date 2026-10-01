# Zipper Discord Bot

A thin relay between Discord and the Zipper server. It holds the system's only gateway
connection and no conversation state.

```
bot/
├── discord_bot.py   # entry point
├── __init__.py      # main(): aiohttp server + Discord client
├── client.py        # the gateway: on_message, post_to_zipper, resolve_thread, status line
├── status.py        # the per-thread "still working" message
└── server.py        # HTTP: /send /history /edit /react /inject /typing /status /thread /threadinfo /threadrename
```

## Inbound

1. A message in the channel opens a thread; a message in a thread stays there.
2. `client.py` POSTs it, with the thread id, to the server's `/discord`.
3. The server hands it to that thread's headless Claude conversation, resuming or starting
   one as needed.
4. When the turn ends, the `Stop` hook (`hooks/forward_reply.py`) posts the reply to the
   thread through this bot's `/send`.

**Attachments** are downloaded to `/tmp/zipper-discord-files/<message id>/`, and each adds
an `attached file saved here: <path>` line to the message (so a caption-less photo is still
a message). A failed download adds a line saying so, so the session can ask for it again.
Only the last 20 messages' files are kept. **`PrivateTmp` must stay off** on this service
and `zipper-web`, or the path points at nothing.

## Status line

Every 30 seconds the bot sets its Discord status from `zipper.presence`: the title of the
newest conversation with a turn running, plus a count of the rest (*"Pantry pricing (+2)"*),
or *"Waiting"* when nothing is. Busy is read the same way the dashboard and `commit` read it
(turn locks and pane status lines), and Discord is only told when the text changes.

## Status messages

While a turn runs, its thread shows one message, edited every minute:
*"⏳ Still working · 3 min · 12:44 · running zipper lint"*. It is posted when `/discord`
takes the message, and **deleted** by the Stop hook right after the reply goes out. It is
deleted rather than edited into the reply because Discord notifies on new messages, never
on edits. A review (`zipper code review`) gets its own message in a second slot
(*"🧪 Tester on PR #26…"*), cleared just before the verdict is delivered.

Each minute `zipper.turnstatus` checks that the work is really running: the turn lock, or
a headless `claude` whose environment names the thread, which survives a `zipper-web`
restart that frees the lock. If it isn't running and nothing was posted after the status,
the status changes to *"⚠️ Stopped without answering"* and stays. State lives in
`Inbox/discord-status.json`, so a bot restart resumes the updates.

## Service

```bash
systemctl status zipper-discord
journalctl -u zipper-discord -f
```

Unit: `deploy/zipper-discord.service` (runs `python3 -u`, or nothing reaches the journal).
Environment from the checkout's `.env`. Listens on `127.0.0.1:4200`.

```
DISCORD_TOKEN=                     # the bot token
DISCORD_CHANNEL_ID=                # the channel it listens in
BOT_URL=http://127.0.0.1:4200      # where zipper reaches this bot
ZIPPER_URL=http://127.0.0.1:8800   # where this bot forwards messages
```

Dependencies: `discord.py`, `aiohttp`. Nothing outside `bot/` imports `discord`, so a cron
job can post through the bot without holding the gateway connection itself.
