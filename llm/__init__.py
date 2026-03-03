"""LLM package — run_conversation, system prompt loading, compaction."""

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
)

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


async def run_conversation(description: str, conversation_id: str) -> str:
    """Claim ownership of the conversation, append the user message, run the LLM loop."""
    # Claim ownership immediately (no awaits before this) — any running task
    # for this conversation will see the mismatch at its next yield point and exit.
    owner_token = str(uuid.uuid4())
    update_meta(conversation_id, last_owner_token=owner_token)

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

    result = await llm_loop(conversation_id, messages, system, owner_token)
    if _owns(conversation_id, owner_token):
        await maybe_compact(conversation_id)
    return result


# Backward-compatible alias
run_task = run_conversation
