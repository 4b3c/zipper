# Zipper Discord Bot

A thin relay between Discord and the Zipper server. It holds the system's only gateway
connection and no conversation state.

```
bot/
├── discord_bot.py   # entry point
├── __init__.py      # main(): aiohttp server + Discord client
├── client.py        # the gateway: on_message, post_to_zipper, resolve_thread
└── server.py        # HTTP: /send /history /edit /react /inject /typing /thread /threadinfo /threadrename
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

## Service

```bash
systemctl status zipper-discord
journalctl -u zipper-discord -f
```

Unit: `deploy/zipper-discord.service` (runs `python3 -u`, or nothing reaches the journal).
Environment from `/opt/zipper/.env`. Listens on `127.0.0.1:4200`.

```
DISCORD_TOKEN=                     # the bot token
DISCORD_CHANNEL_ID=                # the channel it listens in
BOT_URL=http://127.0.0.1:4200      # where zipper reaches this bot
ZIPPER_URL=http://127.0.0.1:8800   # where this bot forwards messages
```

The only part of the system with pip dependencies (`discord.py`, `aiohttp`). The engine stays
stdlib-only, so a cron job can post without owning a socket.
