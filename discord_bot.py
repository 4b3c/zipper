import os
import json
import asyncio
import io
import aiohttp
from aiohttp import ClientTimeout
from aiohttp import web
from pathlib import Path
from dotenv import load_dotenv
import discord

load_dotenv()


def smart_split(text: str, limit: int = 1990) -> list[str]:
    """Split text into chunks ≤ limit chars at natural break points.

    Avoids splitting inside code fences (``` blocks). Prefers paragraph
    breaks > line breaks > sentence ends > word boundaries.
    """
    if len(text) <= limit:
        return [text]

    chunks = []
    while len(text) > limit:
        window = text[:limit]

        # Don't split inside a code fence — find the last ``` before limit
        # If the count of ``` in window is odd, we're inside a fence; pull back
        fence_count = window.count("```")
        if fence_count % 2 == 1:
            # Find the last ``` and split before it
            idx = window.rfind("```")
            if idx > 0:
                chunks.append(text[:idx].rstrip())
                text = text[idx:]
                continue

        # Find best split point, preferring natural breaks
        split_at = None
        for sep in ["\n\n", "\n", ". ", " "]:
            idx = window.rfind(sep)
            if idx > limit // 3:
                split_at = idx
                break

        if split_at is None:
            split_at = limit

        chunks.append(text[:split_at].rstrip())
        text = text[split_at:].lstrip("\n") if "\n" in text[:split_at + 2] else text[split_at:].lstrip(" ")

    if text.strip():
        chunks.append(text.strip())

    return [c for c in chunks if c]


ZIPPER_URL = "http://127.0.0.1:4199"
DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
DISCORD_CHANNEL_ID = int(os.environ["DISCORD_CHANNEL_ID"])
BOT_PORT = int(os.environ.get("BOT_PORT", 4200))
BOT_HOST = os.environ.get("BOT_HOST", "0.0.0.0")
POLL_INTERVAL = 2
STARTUP_TIMEOUT = 45
SETTLE_DELAY = 3

ROOT = Path(__file__).parent

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)


# --- Helpers ---

async def is_zipper_up() -> bool:
    timeout = ClientTimeout(total=3)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"{ZIPPER_URL}/status") as resp:
                return resp.status == 200
    except Exception:
        return False


async def post_to_zipper(prompt: str, discord_thread_id: int) -> bool:
    """Forward a message to zipper. Returns True if zipper acknowledged."""
    try:
        timeout = ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(f"{ZIPPER_URL}/discord", json={
                "prompt": prompt,
                "discord_thread_id": discord_thread_id,
            }) as resp:
                return resp.status == 200
    except Exception as e:
        print(f"[discord] post_to_zipper error: {e}")
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


def get_thread_id_for_conversation(conversation_id: str) -> int | None:
    meta_path = ROOT / "data" / "conversations" / conversation_id / "meta.json"
    if not meta_path.exists():
        return None
    try:
        thread_id = json.loads(meta_path.read_text()).get("discord_thread_id")
        return int(thread_id) if thread_id else None
    except Exception:
        return None


async def inject_prompt_to_thread(prompt: str, thread_id: int) -> bool:
    """Send a synthetic prompt to zipper for a given thread."""
    ok = await post_to_zipper(prompt, thread_id)
    if not ok:
        thread = await resolve_thread(thread_id)
        if thread:
            await thread.send("⚠️ Zipper disconnected")
    return ok


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


# --- Discord events ---

@client.event
async def on_ready():
    print(f"[discord] logged in as {client.user}")
    print(f"[discord] listening in channel {DISCORD_CHANNEL_ID}")


@client.event
async def on_message(message: discord.Message):
    if message.author == client.user:
        return

    # Message in a thread — relay to zipper
    if isinstance(message.channel, discord.Thread):
        ok = await post_to_zipper(message.content, message.channel.id)
        if not ok:
            await message.channel.send("⚠️ Zipper disconnected")
        return

    # Message in the main channel — create a thread, then relay to zipper
    if message.channel.id != DISCORD_CHANNEL_ID:
        return

    thread = await message.create_thread(name=message.content[:50])
    ok = await post_to_zipper(message.content, thread.id)
    if not ok:
        await thread.send("⚠️ Zipper disconnected")


# --- Internal HTTP server ---


async def handle_inject(request: web.Request) -> web.Response:
    """Forward a synthetic prompt to zipper for a given thread."""
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

        asyncio.create_task(inject_prompt_to_thread(prompt, thread_id))
        return web.json_response({"ok": True})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def handle_send(request: web.Request) -> web.Response:
    """Send a message and/or file synchronously and return its ID."""
    try:
        # Handle both JSON and multipart requests
        if request.content_type and 'multipart' in request.content_type:
            reader = await request.multipart()
            fields = {}
            file_data = None
            file_name = "file"
            async for field in reader:
                if field.name == "file":
                    file_name = field.filename or "file"
                    file_data = await field.read()
                else:
                    fields[field.name] = await field.text()
            message = fields.get("message", "").strip()
            thread_id = fields.get("thread_id")
        else:
            body = await request.json()
            message = body.get("message", "").strip()
            thread_id = body.get("thread_id")
            file_data = None
            file_name = "file"

        if not message and not file_data:
            return web.json_response({"error": "message or file required"}, status=400)
        if not client.is_ready():
            return web.json_response({"error": "discord client not ready"}, status=503)

        target = client.get_channel(int(thread_id)) if thread_id else client.get_channel(DISCORD_CHANNEL_ID)
        if target is None:
            return web.json_response({"error": "channel not found"}, status=404)

        # Prepare file attachment if provided
        file_obj = None
        if file_data:
            if len(file_data) > 8 * 1024 * 1024:  # 8MB Discord limit for free servers
                return web.json_response({
                    "error": f"file too large: {len(file_data) / 1024 / 1024:.1f}MB (Discord limit: 8MB)"
                }, status=400)
            file_obj = discord.File(io.BytesIO(file_data), filename=file_name)

        try:
            last_msg = None
            if message:
                chunks = smart_split(message)
                for i, chunk in enumerate(chunks):
                    # attach file to the last chunk only
                    f = file_obj if (i == len(chunks) - 1 and file_obj) else None
                    last_msg = await asyncio.wait_for(target.send(chunk, file=f), timeout=60)
            else:
                last_msg = await asyncio.wait_for(target.send(file=file_obj), timeout=60)
            return web.json_response({"ok": True, "message_id": str(last_msg.id)})
        except asyncio.TimeoutError:
            return web.json_response({"error": "send timed out"}, status=504)
        except Exception as e:
            return web.json_response({"error": f"send failed: {type(e).__name__}: {str(e)}"}, status=500)
    except Exception as e:
        return web.json_response({"error": f"send error: {str(e)}"}, status=500)


async def handle_history(request: web.Request) -> web.Response:
    """Return recent messages from a channel or thread."""
    try:
        body = await request.json()
        if not client.is_ready():
            return web.json_response({"error": "discord client not ready"}, status=503)
        thread_id = body.get("thread_id")
        limit = min(int(body.get("limit", 20)), 100)
        target = client.get_channel(int(thread_id)) if thread_id else client.get_channel(DISCORD_CHANNEL_ID)
        if target is None:
            return web.json_response({"error": "channel not found"}, status=404)
        messages = []
        async for msg in target.history(limit=limit):
            entry = {
                "id": str(msg.id),
                "author": msg.author.display_name,
                "content": msg.content,
                "timestamp": msg.created_at.isoformat(),
            }
            if msg.thread:
                entry["thread_id"] = str(msg.thread.id)
            messages.append(entry)
        return web.json_response({"messages": messages})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def handle_edit(request: web.Request) -> web.Response:
    """Edit a message by ID."""
    try:
        body = await request.json()
        if not client.is_ready():
            return web.json_response({"error": "discord client not ready"}, status=503)
        message_id = body.get("message_id")
        content = body.get("content", "").strip()
        if not message_id or not content:
            return web.json_response({"error": "message_id and content required"}, status=400)
        thread_id = body.get("thread_id")
        target = client.get_channel(int(thread_id)) if thread_id else client.get_channel(DISCORD_CHANNEL_ID)
        if target is None:
            return web.json_response({"error": "channel not found"}, status=404)
        msg = await target.fetch_message(int(message_id))
        await msg.edit(content=content)
        return web.json_response({"ok": True})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def handle_react(request: web.Request) -> web.Response:
    """Add a reaction to a message."""
    try:
        body = await request.json()
        if not client.is_ready():
            return web.json_response({"error": "discord client not ready"}, status=503)
        message_id = body.get("message_id")
        emoji = body.get("emoji", "").strip()
        if not message_id or not emoji:
            return web.json_response({"error": "message_id and emoji required"}, status=400)
        if emoji.startswith("<:") or emoji.startswith("<a:"):
            return web.json_response({"error": "custom server emoji not supported, use standard unicode emoji only"}, status=400)
        thread_id = body.get("thread_id")
        target = client.get_channel(int(thread_id)) if thread_id else client.get_channel(DISCORD_CHANNEL_ID)
        if target is None:
            return web.json_response({"error": "channel not found"}, status=404)
        msg = await target.fetch_message(int(message_id))
        await msg.add_reaction(emoji)
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
    http_app.router.add_post("/send", handle_send)
    http_app.router.add_post("/history", handle_history)
    http_app.router.add_post("/edit", handle_edit)
    http_app.router.add_post("/react", handle_react)
    http_app.router.add_post("/inject", handle_inject)
    http_app.router.add_post("/watch-restart", handle_watch_restart)
    runner = web.AppRunner(http_app)
    await runner.setup()
    site = web.TCPSite(runner, BOT_HOST, BOT_PORT)
    await site.start()
    print(f"[discord] internal server listening on {BOT_HOST}:{BOT_PORT}")

    # now start the Discord client
    await client.start(DISCORD_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
