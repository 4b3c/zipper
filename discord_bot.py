import os
import json
import asyncio
from collections import deque
import aiohttp
from aiohttp import web
from pathlib import Path
from dotenv import load_dotenv
import discord

load_dotenv()

ZIPPER_URL = os.environ.get("ZIPPER_URL", "http://localhost:4199")
DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
DISCORD_CHANNEL_ID = int(os.environ["DISCORD_CHANNEL_ID"])
BOT_PORT = int(os.environ.get("BOT_PORT", 4200))

ROOT = Path(__file__).parent
THREADS_PATH = ROOT / "data" / "discord_threads.json"

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

_notify_queue: deque = deque()


# --- Thread → conversation mapping ---

def load_threads() -> dict:
    if not THREADS_PATH.exists():
        return {}
    return json.loads(THREADS_PATH.read_text())


def save_threads(threads: dict):
    THREADS_PATH.parent.mkdir(parents=True, exist_ok=True)
    THREADS_PATH.write_text(json.dumps(threads, indent=2))


def get_conversation_id(thread_id: int) -> str | None:
    return load_threads().get(str(thread_id))


def set_conversation_id(thread_id: int, conversation_id: str):
    threads = load_threads()
    threads[str(thread_id)] = conversation_id
    save_threads(threads)


# --- Zipper API ---

async def chat(prompt: str, conversation_id: str | None = None, discord_thread_id: int | None = None) -> dict:
    payload = {"prompt": prompt, "source": "discord"}
    if conversation_id:
        payload["conversation_id"] = conversation_id
    if discord_thread_id:
        payload["discord_thread_id"] = discord_thread_id

    async with aiohttp.ClientSession() as session:
        async with session.post(f"{ZIPPER_URL}/chat", json=payload) as resp:
            return await resp.json(content_type=None)


# --- Notify queue ---

async def _notify_worker():
    """Drain the in-memory notify queue, retrying on Discord API failures."""
    await client.wait_until_ready()
    while not client.is_closed():
        if _notify_queue:
            entry = _notify_queue[0]
            thread_id = entry.get("thread_id")
            target = client.get_channel(int(thread_id)) if thread_id else client.get_channel(DISCORD_CHANNEL_ID)
            if target is None:
                _notify_queue.popleft()  # invalid target, drop it
            else:
                try:
                    await target.send(entry["message"])
                    _notify_queue.popleft()
                except Exception as e:
                    print(f"[discord] notify send failed, retrying: {e}")
                    await asyncio.sleep(5)
        else:
            await asyncio.sleep(1)


# --- Discord events ---

@client.event
async def on_ready():
    print(f"[discord] logged in as {client.user}")
    print(f"[discord] listening in channel {DISCORD_CHANNEL_ID}")
    asyncio.create_task(_notify_worker())


@client.event
async def on_message(message: discord.Message):
    if message.author == client.user:
        return

    # message in a thread — continue existing conversation
    if isinstance(message.channel, discord.Thread):
        conversation_id = get_conversation_id(message.channel.id)
        try:
            async with message.channel.typing():
                response = await chat(message.content, conversation_id)
            if not conversation_id:
                set_conversation_id(message.channel.id, response["conversation_id"])
            if "error" in response:
                await message.channel.send(f"⚠️ {response['error']}")
            elif response.get("result"):
                await message.channel.send(response["result"])
        except Exception as e:
            await message.channel.send(f"⚠️ Error: {e}")
        return

    # message in the main channel — start new conversation + thread
    if message.channel.id != DISCORD_CHANNEL_ID:
        return

    thread = await message.create_thread(name=message.content[:50])

    try:
        async with message.channel.typing():
            response = await chat(message.content, discord_thread_id=thread.id)
        set_conversation_id(thread.id, response["conversation_id"])
        if "error" in response:
            await thread.send(f"⚠️ {response['error']}")
        elif response.get("result"):
            await thread.send(response["result"])
    except Exception as e:
        await thread.send(f"⚠️ Error: {e}")


# --- Internal HTTP server ---

async def handle_notify(request: web.Request) -> web.Response:
    try:
        body = await request.json()
        message = body.get("message", "").strip()
        if not message:
            return web.json_response({"error": "message required"}, status=400)
        _notify_queue.append({"message": message, "thread_id": body.get("thread_id")})
        return web.json_response({"ok": True})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def main():
    # start HTTP server first, before Discord connects
    http_app = web.Application()
    http_app.router.add_post("/notify", handle_notify)
    runner = web.AppRunner(http_app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", BOT_PORT)
    await site.start()
    print(f"[discord] internal server listening on port {BOT_PORT}")

    # now start the Discord client
    await client.start(DISCORD_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
