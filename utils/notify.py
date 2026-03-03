"""Shared notification utilities."""

import asyncio

from utils.constants import BOT_URL
from utils.http import post_json


def notify_discord(message: str, thread_id: int = None):
    """Send a message to Discord via the bot endpoint (sync)."""
    body = {"message": message}
    if thread_id:
        body["thread_id"] = thread_id
    result = post_json(f"{BOT_URL}/send", body)
    if "error" in result:
        print(f"[utils] failed to notify discord: {result['error']}")


async def notify_discord_async(message: str, thread_id: int = None):
    """Send a message to Discord via the bot endpoint (async)."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, notify_discord, message, thread_id)
