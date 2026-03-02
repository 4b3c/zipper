"""Shared utility functions."""

import json
import urllib.request
import asyncio

BOT_URL = "http://127.0.0.1:4200"


def notify_discord(message: str, thread_id: int = None):
    """Send a message to Discord via the bot endpoint (sync)."""
    body = {"message": message}
    if thread_id:
        body["thread_id"] = thread_id
    payload = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{BOT_URL}/send",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            r.read()
    except Exception as e:
        print(f"[utils] failed to notify discord: {e}")


async def notify_discord_async(message: str):
    """Send a message to Discord via the bot endpoint (async)."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, notify_discord, message)
