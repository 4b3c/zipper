import json
import os
from datetime import datetime, timezone

import anthropic

from storage.conversations import (
    get_active_version,
    append_message,
    pop_last_message,
    create_version,
    get_conversation,
    set_system_prompt,
)
from storage.trace import append_trace_entry
from tools import TOOLS, execute_tool

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

COMPACTION_THRESHOLD = 20  # messages before compaction


def select_model(messages: list) -> str:
    # heuristics: length, keywords
    raw = messages[-1]["content"] if messages else ""
    last = raw if isinstance(raw, str) else ""
    if "opus" in last.lower():
        return "claude-opus-4-6"
    if "sonnet" in last.lower():
        return "claude-sonnet-4-6"
    return "claude-haiku-4-5-20251001"


def load_system_prompt() -> str:
    root = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(root, "system_prompts/main.md")
    if os.path.exists(path):
        with open(path) as f:
            return f.read().replace("{{project_directory}}", root)
    return "You are Zipper, a self-building AI assistant."


async def run_task(description: str, conversation_id: str) -> str:
    version = get_active_version(conversation_id)
    messages = version["messages"]

    # append the task as a user message if not already present
    if not messages or messages[-1]["content"] != description:
        append_message(conversation_id, "user", description)
        messages = get_active_version(conversation_id)["messages"]

    system = load_system_prompt()
    summary = version.get("summary", "")
    if summary:
        system = f"{system}\n\n## Conversation History\n{summary}"

    set_system_prompt(conversation_id, system)

    result = await llm_loop(conversation_id, messages, system)
    maybe_compact(conversation_id)
    return result


def serialize_content(content) -> list:
    result = []
    for block in content:
        if hasattr(block, "model_dump"):
            result.append(block.model_dump())
        else:
            result.append(block)
    return result


async def llm_loop(conversation_id: str, messages: list, system: str) -> str:
    while True:
        model = select_model(messages)
        print(f"[llm] {model}")
        try:
            response = client.messages.create(
                model=model,
                max_tokens=8096,
                system=system,
                tools=TOOLS,
                messages=messages,
            )
        except Exception as e:
            # roll back the last user message so the conversation stays clean
            pop_last_message(conversation_id)
            raise

        assistant_content = response.content
        append_message(conversation_id, "assistant", serialize_content(assistant_content))
        messages = get_active_version(conversation_id)["messages"]

        if response.stop_reason == "end_turn":
            # extract text response
            for block in assistant_content:
                if hasattr(block, "text"):
                    return block.text
            return ""

        if response.stop_reason == "tool_use":
            tool_results = []
            for block in assistant_content:
                if block.type != "tool_use":
                    continue

                start = datetime.now(timezone.utc)
                try:
                    output = execute_tool(block.name, block.input)
                    error = None
                    status = "ok"
                except Exception as e:
                    output = str(e)
                    error = str(e)
                    status = "error"

                duration_ms = int(
                    (datetime.now(timezone.utc) - start).total_seconds() * 1000
                )

                append_trace_entry(conversation_id, {
                    "tool": block.name,
                    "args": block.input,
                    "output": output,
                    "error": error,
                    "duration_ms": duration_ms,
                    "status": status,
                })

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": output,
                })

            append_message(conversation_id, "user", tool_results)
            messages = get_active_version(conversation_id)["messages"]


def maybe_compact(conversation_id: str):
    version = get_active_version(conversation_id)
    if len(version["messages"]) < COMPACTION_THRESHOLD:
        return

    # summarize old messages
    old_messages = version["messages"][:-6]  # keep last 6 verbatim
    keep_messages = version["messages"][-6:]

    summary_response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system="Summarize the following conversation concisely, preserving key decisions, actions taken, and outcomes.",
        messages=[{"role": "user", "content": json.dumps(old_messages)}],
    )
    new_summary = summary_response.content[0].text

    prior_summary = version.get("summary", "")
    combined_summary = f"{prior_summary}\n\n{new_summary}".strip()

    create_version(conversation_id, summary=combined_summary, messages=keep_messages)
