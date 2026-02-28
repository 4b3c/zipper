import os
import json
import asyncio
import aiohttp
from pathlib import Path
from dotenv import load_dotenv
import discord

load_dotenv()

ZIPPER_URL = os.environ.get("ZIPPER_URL", "http://localhost:4199")
DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
DISCORD_CHANNEL_ID = int(os.environ["DISCORD_CHANNEL_ID"])

ROOT = Path(__file__).parent
THREADS_PATH = ROOT / "data" / "discord_threads.json"

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)


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

async def chat(prompt: str, conversation_id: str | None = None) -> dict:
    payload = {"prompt": prompt, "source": "discord"}
    if conversation_id:
        payload["conversation_id"] = conversation_id

    async with aiohttp.ClientSession() as session:
        async with session.post(f"{ZIPPER_URL}/chat", json=payload) as resp:
            return await resp.json()


# --- Discord events ---

@client.event
async def on_ready():
    print(f"[discord] logged in as {client.user}")
    print(f"[discord] listening in channel {DISCORD_CHANNEL_ID}")


@client.event
async def on_message(message: discord.Message):
    if message.author == client.user:
        return

    # message in a thread — continue existing conversation
    if isinstance(message.channel, discord.Thread):
        conversation_id = get_conversation_id(message.channel.id)
        async with message.channel.typing():
            response = await chat(message.content, conversation_id)
        await message.channel.send(response["result"])
        return

    # message in the main channel — start new conversation + thread
    if message.channel.id != DISCORD_CHANNEL_ID:
        return

    async with message.channel.typing():
        response = await chat(message.content)

    conversation_id = response["conversation_id"]
    result = response["result"]

    # create a thread for this conversation
    thread = await message.create_thread(name=message.content[:50])
    set_conversation_id(thread.id, conversation_id)
    await thread.send(result)


if __name__ == "__main__":
    client.run(DISCORD_TOKEN)
