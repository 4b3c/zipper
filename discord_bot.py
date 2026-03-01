import os
import json
import asyncio
from collections import deque
import aiohttp
from aiohttp import ClientTimeout
from aiohttp import web
from pathlib import Path
from dotenv import load_dotenv
import discord

load_dotenv()

ZIPPER_URL = os.environ.get("ZIPPER_URL", "http://localhost:4199")
DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
DISCORD_CHANNEL_ID = int(os.environ["DISCORD_CHANNEL_ID"])
BOT_PORT = int(os.environ.get("BOT_PORT", 4200))
POLL_INTERVAL = 2
STARTUP_TIMEOUT = 45
SETTLE_DELAY = 3

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


def get_thread_id_for_conversation(conversation_id: str) -> int | None:
    meta_path = ROOT / "data" / "conversations" / conversation_id / "meta.json"
    if not meta_path.exists():
        return None
    try:
        thread_id = json.loads(meta_path.read_text()).get("discord_thread_id")
        return int(thread_id) if thread_id else None
    except Exception:
        return None


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


async def is_zipper_up() -> bool:
    timeout = ClientTimeout(total=3)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"{ZIPPER_URL}/status") as resp:
                return resp.status == 200
    except Exception:
        return False


async def resolve_thread(thread_id: int):
    await client.wait_until_ready()
    for attempt in range(1, 6):
        thread = client.get_channel(thread_id)
        if thread is None:
            try:
                thread = await client.fetch_channel(thread_id)
            except Exception as e:
                print(f"[discord] resolve_thread: fetch failed for {thread_id} (attempt {attempt}): {e}")
        if thread is not None:
            return thread
        await asyncio.sleep(min(2 ** attempt, 10))
    return None


async def inject_prompt_to_thread(prompt: str, thread_id: int, conversation_id: str | None = None) -> bool:
    thread = await resolve_thread(thread_id)
    if thread is None:
        print(f"[discord] inject: thread {thread_id} not found after retries")
        return False

    active_conversation_id = conversation_id or get_conversation_id(thread_id)
    try:
        async with thread.typing():
            response = await chat(prompt, active_conversation_id, discord_thread_id=thread_id)
        if not active_conversation_id and response.get("conversation_id"):
            set_conversation_id(thread_id, response["conversation_id"])
        if "error" in response:
            await thread.send(f"⚠️ {response['error']}")
        elif response.get("result"):
            await thread.send(response["result"])
        return True
    except Exception as e:
        print(f"[discord] inject error: {e}")
        try:
            await thread.send(f"⚠️ Error: {e}")
        except Exception:
            pass
        return False


async def run_restart_watch(conversation_id: str, thread_id: int):
    print(f"[discord] restart watch started: conversation={conversation_id} thread={thread_id}")
    thread = await resolve_thread(thread_id)
    if thread is not None:
        await thread.send("Restart watchdog: monitoring zipper restart now.")

    # wait for zipper to go down first (up to 10s)
    saw_down = False
    for _ in range(10):
        if not await is_zipper_up():
            saw_down = True
            break
        await asyncio.sleep(1)
    if thread is not None:
        if saw_down:
            await thread.send("Restart watchdog: zipper service is down.")
        else:
            await thread.send(
                "Restart watchdog: did not observe zipper go down; continuing to wait for healthy startup."
            )

    # now wait for it to come back up
    if thread is not None:
        await thread.send("Restart watchdog: waiting for zipper service to come back up.")
    deadline = asyncio.get_running_loop().time() + STARTUP_TIMEOUT
    came_up = False
    while asyncio.get_running_loop().time() < deadline:
        if await is_zipper_up():
            came_up = True
            break
        await asyncio.sleep(POLL_INTERVAL)

    if came_up:
        if thread is not None:
            await thread.send("Restart watchdog: zipper service is up and HTTP /status is healthy.")
        await asyncio.sleep(SETTLE_DELAY)
        if thread is not None:
            await thread.send("Restart watchdog: resuming conversation now.")
        ok = await inject_prompt_to_thread(
            "Restart successful. Zipper came back up cleanly. "
            "Now verify that your changes work as intended.",
            thread_id=thread_id,
            conversation_id=conversation_id,
        )
        print(
            f"[discord] restart watch completed: conversation={conversation_id} "
            f"thread={thread_id} injected={ok}"
        )
    else:
        if thread is not None:
            await thread.send(
                "⚠️ Restart watchdog timeout: zipper did not become healthy within "
                f"{STARTUP_TIMEOUT}s."
            )
        print(
            f"[discord] restart watch timeout: conversation={conversation_id} "
            f"thread={thread_id}"
        )


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


async def handle_inject(request: web.Request) -> web.Response:
    """Inject a synthetic message into a thread as if it came from a user."""
    try:
        body = await request.json()
        prompt = body.get("prompt", "").strip()
        thread_id = body.get("thread_id")
        if not prompt:
            return web.json_response({"error": "prompt required"}, status=400)
        if thread_id is None:
            return web.json_response({"error": "thread_id required"}, status=400)
        try:
            thread_id = int(thread_id)
        except (TypeError, ValueError):
            return web.json_response({"error": "thread_id must be an integer"}, status=400)

        async def _do_inject():
            await inject_prompt_to_thread(prompt, thread_id)

        asyncio.create_task(_do_inject())
        return web.json_response({"ok": True})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def handle_watch_restart(request: web.Request) -> web.Response:
    try:
        body = await request.json()
        conversation_id = body.get("conversation_id", "").strip()
        if not conversation_id:
            return web.json_response({"error": "conversation_id required"}, status=400)

        thread_id = body.get("thread_id")
        if thread_id is None:
            thread_id = get_thread_id_for_conversation(conversation_id)
        try:
            thread_id = int(thread_id) if thread_id is not None else None
        except (TypeError, ValueError):
            return web.json_response({"error": "thread_id must be an integer"}, status=400)
        if thread_id is None:
            return web.json_response({"error": "thread_id not found for conversation"}, status=400)

        asyncio.create_task(run_restart_watch(conversation_id, thread_id))
        return web.json_response({"ok": True, "conversation_id": conversation_id, "thread_id": thread_id})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def main():
    # start HTTP server first, before Discord connects
    http_app = web.Application()
    http_app.router.add_post("/notify", handle_notify)
    http_app.router.add_post("/inject", handle_inject)
    http_app.router.add_post("/watch-restart", handle_watch_restart)
    runner = web.AppRunner(http_app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", BOT_PORT)
    await site.start()
    print(f"[discord] internal server listening on port {BOT_PORT}")

    # now start the Discord client
    await client.start(DISCORD_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
