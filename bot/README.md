# Zipper Discord Bot

Thin relay between Discord and the Zipper core service.

## Architecture

```
bot/
├── discord_bot.py   # Entry point — asyncio.run(main())
├── __init__.py      # main() — aiohttp server + Discord client startup
├── client.py        # Discord gateway: on_message, on_ready, resolve_thread
├── server.py        # HTTP handlers: /send, /history, /edit, /react, /inject
└── zipper-discord.service
```

**Flow:**
1. Discord message arrives → `client.py` POSTs to zipper's `/discord` endpoint
2. Zipper runs the LLM loop in the background
3. Zipper POSTs the result back to this bot's `/send` endpoint
4. Bot sends the reply in the Discord thread

All conversation state lives in zipper, never here.

## Service

Managed by systemd:

```bash
systemctl status zipper-discord
systemctl restart zipper-discord
journalctl -u zipper-discord -f
```

To install or update the service file:

```bash
cp bot/zipper-discord.service /etc/systemd/system/zipper-discord.service
systemctl daemon-reload
```

Environment file: `/opt/zipper/app/.env`
Port: `127.0.0.1:4200`

## Environment Variables

```
DISCORD_TOKEN=
DISCORD_CHANNEL_ID=
```
