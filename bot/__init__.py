"""Discord bot package — main() entry point, sets up HTTP server and Discord client."""

import asyncio
import os

from aiohttp import web
from dotenv import load_dotenv

load_dotenv()

import bot.client as _client_mod
from bot.server import setup_routes

BOT_PORT = int(os.environ.get("BOT_PORT", 4200))
BOT_HOST = os.environ.get("BOT_HOST", "0.0.0.0")
DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]

# Expose channel ID to client module
_client_mod.DISCORD_CHANNEL_ID = int(os.environ["DISCORD_CHANNEL_ID"])


async def main():
    # Start HTTP server first, before Discord connects
    http_app = web.Application()
    setup_routes(http_app)
    runner = web.AppRunner(http_app)
    await runner.setup()
    site = web.TCPSite(runner, BOT_HOST, BOT_PORT)
    await site.start()
    print(f"[discord] internal server listening on {BOT_HOST}:{BOT_PORT}")

    # Now start the Discord client
    await _client_mod.client.start(DISCORD_TOKEN)
