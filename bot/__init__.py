"""Discord bot package — main() entry point, sets up HTTP server and Discord client."""

import asyncio
import os

from aiohttp import web
from dotenv import load_dotenv

load_dotenv()

# Ports, channel and peers from the vault's settings.json, under anything the
# environment or .env already set -- the same order the engine uses.
from zipper import settings as _settings
_settings.apply()

# Always through the gateway (gateway/), never Discord itself: discord.py's REST and
# websocket point at the relay, and DISCORD_TOKEN is the token the gateway minted for
# this zipper, which Discord would refuse. One zipper or several on a machine, the
# real bot token lives only in the gateway. `zipper discord connect` sets both.
# Checked in main(), not here: tests import this package for its modules.
DISCORD_RELAY = os.environ.get("DISCORD_RELAY", "").rstrip("/")
if DISCORD_RELAY:
    import discord.gateway
    import discord.http
    import yarl
    discord.http.Route.BASE = DISCORD_RELAY + "/api/v10"
    discord.gateway.DiscordWebSocket.DEFAULT_GATEWAY = yarl.URL(
        DISCORD_RELAY.replace("http", "ws", 1) + "/ws")

import bot.client as _client_mod
from bot.server import setup_routes, SEND_MAX_BYTES

BOT_PORT = int(os.environ.get("BOT_PORT", 4200))
# Loopback only. This API is for this zipper's own processes (the Stop hook, `zipper
# discord`, the web), and `/inject` hands a prompt straight to a Claude conversation:
# on any other address, every container on the Docker network -- other people's
# zippers, the shared gateway -- could talk to this zipper's Claude.
BOT_HOST = os.environ.get("BOT_HOST", "127.0.0.1")
DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]

PAIR_POLL = 15      # seconds between "has my code been pasted yet" while pairing


async def home_channel():
    """The channel this zipper answers in. From settings once it is known; until then
    (a zipper just paired, whose code nobody has pasted yet) the relay is asked every
    PAIR_POLL seconds, and the channel the code was pasted in is saved to settings."""
    if os.environ.get("DISCORD_CHANNEL_ID"):
        return int(os.environ["DISCORD_CHANNEL_ID"])
    import aiohttp
    said = None
    async with aiohttp.ClientSession() as s:
        while True:
            try:
                async with s.get(DISCORD_RELAY + "/zipper/home",
                                 headers={"Authorization": "Bot " + DISCORD_TOKEN}) as r:
                    body = await r.json(content_type=None) if r.status == 200 else {}
                    state = body.get("state") or ("unknown token" if r.status == 401
                                                  else "HTTP %d" % r.status)
            except (aiohttp.ClientError, ValueError) as e:
                body, state = {}, "gateway unreachable (%s)" % e
            if state == "linked" and body.get("channel"):
                _settings.put("discord.channel", str(body["channel"]))
                print(f"[discord] paired: home channel {body['channel']}", flush=True)
                return int(body["channel"])
            if state != said:
                hint = {"pending": "waiting for the pairing code to be pasted in a server",
                        "unknown token": "the gateway doesn't know this zipper; run "
                                         "`./zipper discord connect` on the machine"}.get(state, state)
                print(f"[discord] no home channel yet: {hint}", flush=True)
                said = state
            await asyncio.sleep(PAIR_POLL)


async def main():
    if not DISCORD_RELAY:
        raise SystemExit("[discord] DISCORD_RELAY is not set. Discord goes through the gateway: "
                         "run `./zipper discord connect` on the machine, from this zipper's folder.")
    # Start HTTP server first, before Discord connects
    # aiohttp refuses bodies over 1 MiB by default; leave room for a full-size file
    # plus the multipart framing and message text around it.
    http_app = web.Application(client_max_size=SEND_MAX_BYTES + 1024 * 1024)
    setup_routes(http_app)
    runner = web.AppRunner(http_app)
    await runner.setup()
    site = web.TCPSite(runner, BOT_HOST, BOT_PORT)
    await site.start()
    print(f"[discord] internal server listening on {BOT_HOST}:{BOT_PORT}")

    _client_mod.DISCORD_CHANNEL_ID = await home_channel()
    await _client_mod.client.start(DISCORD_TOKEN)
