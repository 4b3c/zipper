"""LLM package — run_conversation, system prompt loading, compaction."""

import asyncio
import os
import uuid
from datetime import datetime

import anthropic

from storage.conversations import (
    get_latest_version,
    append_message,
    save_messages,
    set_system_prompt,
    update_meta,
    get_conversation_thread_id,
)
from utils.constants import BOT_URL
from utils.http import post_json

client = anthropic.AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

COMPACTION_THRESHOLD = 20  # messages before compaction

# Import submodules after client/COMPACTION_THRESHOLD are defined (circular-import safe)
from llm.loop import llm_loop, maybe_compact, _owns  # noqa: E402
from llm.messages import _sanitize_messages  # noqa: E402


def load_system_prompt() -> str:
    root = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(root, "..", "prompts", "main.md")
    current_time = datetime.now().isoformat()
    if os.path.exists(path):
        with open(path) as f:
            return f.read().replace("{{project_directory}}", os.path.dirname(root)).replace("{{current_time}}", current_time)
    return "You are Zipper, a self-building AI assistant."


def _set_typing(thread_id: int, active: bool):
    """Fire-and-forget typing indicator update (sync, called from executor)."""
    try:
        post_json(f"{BOT_URL}/typing", {"thread_id": thread_id, "active": active}, timeout=5)
    except Exception:
        pass


async def run_conversation(description: str, conversation_id: str, stream_callback=None) -> str:
    """Claim ownership of the conversation, append the user message, run the LLM loop."""
    # Claim ownership immediately (no awaits before this) — any running task
    # for this conversation will see the mismatch at its next yield point and exit.
    owner_token = str(uuid.uuid4())
    update_meta(conversation_id, last_owner_token=owner_token, status="active")

    version = get_latest_version(conversation_id)
    messages = _sanitize_messages(version["messages"])

    if len(messages) != len(version["messages"]):
        save_messages(conversation_id, messages)

    # append the new user message, then re-sanitize in case a concurrent task
    # already appended one (producing consecutive user messages)
    if not messages or messages[-1].get("content") != description:
        append_message(conversation_id, "user", description)
        messages = _sanitize_messages(get_latest_version(conversation_id)["messages"])
        save_messages(conversation_id, messages)

    system = load_system_prompt()
    summary = version.get("summary", "")
    if summary:
        system = f"{system}\n\n## Conversation History\n{summary}"

    set_system_prompt(conversation_id, system)

    thread_id = get_conversation_thread_id(conversation_id)
    if thread_id:
        asyncio.create_task(asyncio.to_thread(_set_typing, thread_id, True))

    try:
        result = await llm_loop(conversation_id, messages, system, owner_token, stream_callback=stream_callback)
    finally:
        if thread_id:
            asyncio.create_task(asyncio.to_thread(_set_typing, thread_id, False))
        if _owns(conversation_id, owner_token):
            update_meta(conversation_id, status="inactive")

    if _owns(conversation_id, owner_token):
        await maybe_compact(conversation_id)
    return result


# Backward-compatible alias
run_task = run_conversation
