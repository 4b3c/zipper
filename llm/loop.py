"""Core LLM loop, ownership checks, model selection, and compaction."""

import asyncio
import json
import re
from datetime import datetime

from storage.conversations import (
    get_latest_version,
    append_message,
    pop_last_message,
    create_version,
    get_conversation,
    get_conversation_thread_id,
    update_meta,
)
from storage.trace import append_trace_entry
from tools import TOOLS, execute_tool
from tools.signals import BreakLoop
from llm.messages import serialize_content, _sanitize_messages
from utils.notify import notify_discord_async

RATING_RE = re.compile(r'\{\{c:(\d),\s*d:(\d),\s*a:(\d)\}\}')


def _owns(conversation_id: str, token: str) -> bool:
    """Check whether token is still the active owner of the conversation."""
    try:
        return get_conversation(conversation_id).get("last_owner_token") == token
    except Exception:
        return False


def select_model(ratings: tuple | None) -> str:
    """Select model based on c+d+a rating from previous turn. None = first turn, default to Haiku."""
    if ratings is None:
        return "claude-haiku-4-5-20251001"
    total = sum(ratings)
    if total > 11:
        return "claude-opus-4-6"
    if total > 6:
        return "claude-sonnet-4-6"
    return "claude-haiku-4-5-20251001"


def parse_ratings(text: str) -> tuple | None:
    m = RATING_RE.search(text)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return None


def strip_ratings(text: str) -> str:
    return RATING_RE.sub("", text).strip()


async def llm_loop(conversation_id: str, messages: list, system: str, owner_token: str, stream_callback=None) -> str:
    # Import client here to avoid circular import issues at module level
    from llm import client

    ratings = None

    def owns() -> bool:
        return _owns(conversation_id, owner_token)

    while True:
        # Yield to the event loop so any pending request for this conversation
        # can run run_conversation() and write a new ownership token before we check.
        await asyncio.sleep(0)
        if not owns():
            return ""

        model = select_model(ratings)
        print(f"[llm] {model} (ratings={ratings})")

        retry_delay = 5
        retries = 0
        while True:
            try:
                response = None
                async with client.messages.stream(
                    model=model,
                    max_tokens=8096,
                    system=system,
                    tools=TOOLS,
                    messages=messages,
                ) as stream:
                    async for event in stream:
                        if not owns():
                            return ""
                        if (stream_callback
                                and event.type == "content_block_delta"
                                and getattr(event.delta, "type", None) == "text_delta"):
                            try:
                                await stream_callback("token", text=event.delta.text)
                            except Exception:
                                stream_callback = None  # client disconnected, stop sending
                    response = await stream.get_final_message()
                break  # success
            except __import__('anthropic').APIStatusError as e:
                status = getattr(e, 'status_code', 0)
                is_overloaded = status == 529 or 'overloaded' in str(e).lower()
                if status != 429 and not is_overloaded:
                    if owns():
                        pop_last_message(conversation_id)
                    raise
                if not owns():
                    return ""
                retries += 1
                label = "overloaded" if is_overloaded else "rate limited"
                print(f"[llm] {label}, retry {retries}/5 in {retry_delay}s")
                if retries >= 5:
                    if owns():
                        pop_last_message(conversation_id)
                    thread_id = get_conversation_thread_id(conversation_id)
                    await notify_discord_async(
                        f"⚠️ Claude API is {label} — gave up after 5 retries. Please try again later.",
                        thread_id=thread_id,
                    )
                    return ""
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 120)
                if not owns():
                    return ""
                continue
            except Exception as e:
                if owns():
                    pop_last_message(conversation_id)
                raise

        if not owns():
            return ""

        # Parse and strip ratings tag from text blocks before storing
        assistant_content = serialize_content(response.content)
        for block in assistant_content:
            if isinstance(block, dict) and block.get("type") == "text":
                r = parse_ratings(block["text"])
                if r:
                    ratings = r
                block["text"] = strip_ratings(block["text"])

        append_message(conversation_id, "assistant", assistant_content)
        messages = get_latest_version(conversation_id)["messages"]

        if response.stop_reason == "end_turn":
            for block in assistant_content:
                if isinstance(block, dict) and block.get("type") == "text":
                    return block["text"]
            return ""

        if response.stop_reason == "tool_use":
            tool_results = []
            for block in assistant_content:
                if block.get("type") != "tool_use":
                    continue

                tool_name = block["name"]
                tool_input = block["input"]
                tool_id = block["id"]

                if stream_callback:
                    try:
                        await stream_callback("tool_call", tool=tool_name, args=tool_input, tool_id=tool_id)
                    except Exception:
                        stream_callback = None

                start = datetime.now()
                try:
                    # Run synchronous tool in a thread pool so the event loop
                    # remains free to process incoming requests (e.g. an interrupt
                    # claiming ownership of this conversation) while tools execute.
                    output = await asyncio.to_thread(execute_tool, tool_name, tool_input, conversation_id)
                    error = None
                    status = "ok"
                except BreakLoop as e:
                    msg = str(e)
                    duration_ms = int((datetime.now() - start).total_seconds() * 1000)
                    append_trace_entry(conversation_id, {
                        "tool": tool_name,
                        "args": tool_input,
                        "output": msg,
                        "error": None,
                        "duration_ms": duration_ms,
                        "status": "ok",
                    })
                    if not owns():
                        return ""
                    tool_results.append({"type": "tool_result", "tool_use_id": tool_id, "content": msg})
                    if stream_callback:
                        try:
                            await stream_callback("tool_result", tool_use_id=tool_id, result=msg)
                        except Exception:
                            stream_callback = None
                    append_message(conversation_id, "user", tool_results)
                    return ""
                except Exception as e:
                    output = str(e)
                    error = str(e)
                    status = "error"

                duration_ms = int((datetime.now() - start).total_seconds() * 1000)

                append_trace_entry(conversation_id, {
                    "tool": tool_name,
                    "args": tool_input,
                    "output": output,
                    "error": error,
                    "duration_ms": duration_ms,
                    "status": status,
                })

                if not owns():
                    return ""

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "content": output,
                })
                if stream_callback:
                    try:
                        await stream_callback("tool_result", tool_use_id=tool_id, result=output)
                    except Exception:
                        stream_callback = None

            if not owns():
                return ""

            append_message(conversation_id, "user", tool_results)
            messages = get_latest_version(conversation_id)["messages"]


async def maybe_compact(conversation_id: str):
    from llm import client, COMPACTION_THRESHOLD

    version = get_latest_version(conversation_id)
    if len(version["messages"]) < COMPACTION_THRESHOLD:
        return

    old_messages = version["messages"][:-6]
    keep_messages = version["messages"][-6:]

    summary_response = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system="Summarize the following conversation concisely, preserving key decisions, actions taken, and outcomes.",
        messages=[{"role": "user", "content": json.dumps(old_messages)}],
    )
    new_summary = summary_response.content[0].text

    prior_summary = version.get("summary", "")
    combined_summary = f"{prior_summary}\n\n{new_summary}".strip()

    create_version(conversation_id, summary=combined_summary, messages=keep_messages)
