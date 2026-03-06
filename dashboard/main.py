"""
Zipper Dashboard — web frontend for Zipper using WebSocket streaming.
Serves HTML + Tailwind, streams LLM responses in real-time.
"""

import asyncio
import json
import os
import re
import requests
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, BackgroundTasks, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.requests import Request

import sys
sys.path.insert(0, "/opt/zipper/app")

from storage.conversations import list_conversations, get_conversation, create_conversation as create_conv, get_latest_version, get_full_history
from storage.memory import get, set, delete, all as list_all
from storage.tasks import list_tasks, create_task, update_task_status, patch_task
from llm import run_conversation, client as llm_client, load_system_prompt
from llm.messages import _sanitize_messages
from tools import TOOLS

app = FastAPI(title="Zipper Dashboard", version="0.1")

USER_NAME = os.environ.get("USER", "You")

# Serve static files (CSS, JS)
BASE_DIR = "/opt/zipper/app/dashboard"

app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    """Render the main chat interface."""
    with open(os.path.join(BASE_DIR, "templates", "index.html")) as f:
        return f.read()


@app.get("/api/conversations", response_class=HTMLResponse)
async def list_convos(offset: int = 0):
    """Return paginated conversation list as HTML (for sidebar)."""
    conversations = list_conversations()
    
    # Sort by updated_at (most recent first)
    conversations.sort(key=lambda c: c.get("updated_at", c.get("created_at", "")), reverse=True)
    
    total = len(conversations)
    page_size = 20
    paginated = conversations[offset : offset + page_size]
    
    html = '<div class="space-y-0.5" id="conversation-list-items">'
    for convo in paginated:
        convo_id = convo["id"]
        title = escape_html(convo.get("title", "Untitled"))
        updated = convo.get("updated_at", convo.get("created_at", ""))
        status = convo.get("status", "")
        summary = convo.get("summary", "")

        # Relative-ish timestamp
        timestamp = format_timestamp(updated) if updated else ""
        if not timestamp and updated:
            timestamp = updated[:10]

        status_dot = '<span class="dot-active"></span>' if status == "active" else ""
        summary_preview = escape_html((summary[:55] + "…") if summary and len(summary) > 55 else summary)

        html += f'''<a href="/?conversation={convo_id}" class="conv-item">
    <div style="display:flex;align-items:center;gap:6px;margin-bottom:2px;">
        {status_dot}
        <div class="conv-item-title">{title}</div>
        <span class="conv-item-meta" style="margin-left:auto;flex-shrink:0;">{timestamp}</span>
    </div>
    {f'<div class="conv-item-summary">{summary_preview}</div>' if summary_preview else ''}
</a>'''

    if offset + page_size < total:
        remaining = total - (offset + page_size)
        html += f'''<button class="load-more" onclick="loadMoreConversations({offset + page_size})">
    Load {min(page_size, remaining)} more…
</button>'''

    html += '</div>'
    return html


def escape_html(text: str) -> str:
    """Escape HTML special characters."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def format_timestamp(ts: str) -> str:
    """Format an ISO timestamp to a short time string."""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.strftime("%-I:%M %p")
    except Exception:
        return ts[:16] if ts else ""


RATING_RE = re.compile(r'\{\{c:(\d),\s*d:(\d),\s*a:(\d)\}\}')


def format_text_as_html(text: str) -> str:
    """Wrap raw markdown text in a markdown-body div (rendered client-side by marked.js).
    Extract any ratings tag and render it as a collapsible button instead.
    """
    m = RATING_RE.search(text)
    rating_html = ""
    if m:
        text = RATING_RE.sub("", text).strip()
        c, d, a = m.group(1), m.group(2), m.group(3)
        rating_html = (
            f'<div class="ratings-bar">'
            f'<button class="rating-btn" data-label="Complexity" data-value="{c}" onclick="showRatingPopup(this)">C</button>'
            f'<button class="rating-btn" data-label="Difficulty" data-value="{d}" onclick="showRatingPopup(this)">D</button>'
            f'<button class="rating-btn" data-label="Ambiguity" data-value="{a}" onclick="showRatingPopup(this)">A</button>'
            f'</div>'
        )
    return f'<div class="markdown-body">{escape_html(text)}</div>{rating_html}'


def format_message_content(content) -> str:
    """
    Format message content (text, tool_use, tool_result blocks) as rich HTML.
    Text blocks use markdown-body (rendered by marked.js on the client).
    Tool calls and results are collapsible bubbles.
    """
    if isinstance(content, str):
        return format_text_as_html(content)

    if not isinstance(content, list):
        return f'<pre class="text-xs text-slate-400">{escape_html(json.dumps(content, indent=2))}</pre>'

    html_parts = []
    for block in content:
        if not isinstance(block, dict):
            html_parts.append(f"<div class='text-sm'>{escape_html(str(block))}</div>")
            continue

        block_type = block.get("type")

        if block_type == "text":
            html_parts.append(format_text_as_html(block.get("text", "")))
        elif block_type == "tool_use":
            html_parts.append(render_tool_block(
                block.get("name", "unknown"),
                block.get("input", {}),
                block.get("id", ""),
                result_text=None,
            ))
        elif block_type == "tool_result":
            # Standalone tool_result (not grouped) — show minimal output block
            tid = block.get("tool_use_id", "")
            result_text = extract_result_text(block.get("content", ""))
            html_parts.append(render_tool_block("(result)", {}, tid, result_text))

    return "".join(html_parts)


def extract_result_text(result_content) -> str:
    """Extract plain text from a tool result (list, dict, or str)."""
    if isinstance(result_content, list):
        parts = []
        for item in result_content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
            else:
                parts.append(json.dumps(item, indent=2))
        return "\n".join(parts)
    elif isinstance(result_content, dict):
        return json.dumps(result_content, indent=2)
    else:
        return str(result_content)


def render_tool_block(tool_name: str, tool_input: dict, tool_id: str, result_text=None) -> str:
    """Render a collapsible tool call+result block (codeblock style)."""
    input_str = json.dumps(tool_input, indent=2) if isinstance(tool_input, dict) else str(tool_input)
    input_section = (
        f'<div class="tool-section">'
        f'<div class="tool-section-label">Input</div>'
        f'<pre class="tool-code">{escape_html(input_str)}</pre>'
        f'</div>'
    )
    if result_text is not None:
        result_section = (
            f'<div class="tool-section">'
            f'<div class="tool-section-label">Output</div>'
            f'<pre class="tool-code" data-result-for="{escape_html(tool_id)}">{escape_html(result_text)}</pre>'
            f'</div>'
        )
    else:
        result_section = (
            f'<div class="tool-section">'
            f'<div class="tool-section-label">Output</div>'
            f'<pre class="tool-code tool-code-pending" data-result-for="{escape_html(tool_id)}">…</pre>'
            f'</div>'
        )
    return (
        f'<div class="tool-block" data-tool-id="{escape_html(tool_id)}">'
        f'<div class="tool-block-header" onclick="this.closest(\'.tool-block\').classList.toggle(\'open\')">'
        f'<span class="tool-block-name">Ran: {escape_html(tool_name)}</span>'
        f'<span class="tool-block-arrow">&#9658;</span>'
        f'</div>'
        f'<div class="tool-block-body">{input_section}{result_section}</div>'
        f'</div>'
    )


def _is_tool_result_only(content) -> bool:
    """Return True if content is purely tool_result blocks (no user text)."""
    if not isinstance(content, list):
        return False
    return all(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)


def render_message(msg: dict) -> str:
    """Render a single message as a chat bubble."""
    role = msg.get("role", "user")
    content = msg.get("content", "")
    timestamp = msg.get("timestamp", "")
    content_html = format_message_content(content)
    time_str = format_timestamp(timestamp)
    time_html = f' <span class="text-slate-500">· {time_str}</span>' if time_str else ""

    # Tool result messages (role=user but contain only tool results) render inline, not as user bubbles
    if role == "user" and _is_tool_result_only(content):
        return f'<div style="padding-left:36px;">{content_html}</div>'

    if role == "user":
        return f'''<div class="msg-user">
    <div class="msg-user-inner">
        <div class="bubble-user">{content_html}</div>
        <div class="msg-meta">{escape_html(USER_NAME)}{time_html}</div>
    </div>
</div>'''
    else:
        return f'''<div class="msg-assistant">
    <div class="avatar-sm">Z</div>
    <div class="msg-assistant-body">
        <div class="msg-label">Zipper{time_html}</div>
        <div class="msg-content">{content_html}</div>
    </div>
</div>'''


def group_messages_for_display(messages: list) -> list:
    """
    Group consecutive assistant + tool_result-only user messages into logical units.
    Returns list of ("assistant_group", [msgs]) or ("user", msg) tuples.
    """
    groups = []
    i = 0
    while i < len(messages):
        msg = messages[i]
        if msg.get("role") == "assistant":
            group = [msg]
            j = i + 1
            # Absorb alternating tool_result user turns and subsequent assistant turns
            while j < len(messages):
                nxt = messages[j]
                if nxt.get("role") == "user" and _is_tool_result_only(nxt.get("content", [])):
                    group.append(nxt)
                    j += 1
                    if j < len(messages) and messages[j].get("role") == "assistant":
                        group.append(messages[j])
                        j += 1
                else:
                    break
            groups.append(("assistant_group", group))
            i = j
        else:
            groups.append(("user", msg))
            i += 1
    return groups


def render_assistant_group(group: list) -> str:
    """Render a logical group (assistant + tool turns) as one message bubble."""
    # Build map: tool_use_id → result text from tool_result user messages
    tool_results: dict = {}
    for msg in group:
        if msg.get("role") == "user":
            content = msg.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        tid = block.get("tool_use_id", "")
                        tool_results[tid] = extract_result_text(block.get("content", ""))

    parts_html = []
    for msg in group:
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content", "")
        if isinstance(content, str):
            if content.strip():
                parts_html.append(format_text_as_html(content))
        elif isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    text = block.get("text", "")
                    if text.strip():
                        parts_html.append(format_text_as_html(text))
                elif btype == "tool_use":
                    tid = block.get("id", "")
                    parts_html.append(render_tool_block(
                        block.get("name", "unknown"),
                        block.get("input", {}),
                        tid,
                        tool_results.get(tid),
                    ))

    content_html = "".join(parts_html)
    timestamp = group[0].get("timestamp", "")
    time_str = format_timestamp(timestamp)
    time_html = f' · {time_str}' if time_str else ""

    return (
        f'<div class="msg-assistant">'
        f'<div class="avatar-sm">Z</div>'
        f'<div class="msg-assistant-body">'
        f'<div class="msg-label">Zipper{time_html}</div>'
        f'<div class="msg-content">{content_html}</div>'
        f'</div></div>'
    )


def render_messages_html(messages: list) -> str:
    """Render all messages as HTML, grouping assistant+tool turns."""
    if not messages:
        return '<div style="color:var(--tx-3);font-size:0.875rem;">No messages yet.</div>'
    groups = group_messages_for_display(messages)
    parts = []
    for gtype, data in groups:
        if gtype == "assistant_group":
            parts.append(render_assistant_group(data))
        else:
            parts.append(render_message(data))
    return "\n".join(parts)


@app.get("/api/conversations/{conversation_id}/metadata", response_class=JSONResponse)
async def get_conversation_metadata(conversation_id: str):
    """Get conversation metadata (title, status, summary, dates, etc.)."""
    meta = get_conversation(conversation_id)
    if not meta:
        return JSONResponse({"error": "Conversation not found"}, status_code=404)
    
    message_count = len(get_full_history(conversation_id))
    
    return JSONResponse({
        "id": meta.get("id"),
        "title": meta.get("title", "Untitled"),
        "status": meta.get("status", ""),
        "summary": meta.get("summary", ""),
        "created_at": meta.get("created_at", ""),
        "updated_at": meta.get("updated_at", ""),
        "message_count": message_count,
        "source": meta.get("source", ""),
        "discord_thread_id": meta.get("discord_thread_id")
    })


@app.get("/api/conversations/{conversation_id}/context-length")
async def get_context_length(conversation_id: str):
    """Count tokens in the conversation context using the Anthropic count_tokens API."""
    TOKEN_LIMIT = 200_000
    messages_raw = get_full_history(conversation_id)
    message_count = len(messages_raw)

    # Strip to role+content only for the API
    clean = _sanitize_messages(messages_raw)
    api_messages = [{"role": m["role"], "content": m["content"]} for m in clean if m.get("content")]

    system = load_system_prompt()

    # Filter tools to ones the API accepts for counting (exclude code_execution type tool)
    countable_tools = [t for t in TOOLS if t.get("name")]

    try:
        result = await llm_client.messages.count_tokens(
            model="claude-sonnet-4-6",
            system=system,
            tools=countable_tools,
            messages=api_messages,
        )
        token_count = result.input_tokens
        estimated = False
    except Exception:
        # Fallback: rough char estimate + fixed overhead for system prompt + tools
        total_chars = sum(len(str(m.get("content", ""))) for m in messages_raw)
        token_count = int(total_chars / 3.5) + 2500  # +2500 for system + tool schemas
        estimated = True

    percent = round(token_count / TOKEN_LIMIT * 100, 1)
    return JSONResponse({
        "token_count": token_count,
        "token_limit": TOKEN_LIMIT,
        "percent": percent,
        "message_count": message_count,
        "estimated": estimated,
    })


@app.get("/api/conversations/{conversation_id}/view", response_class=HTMLResponse)
async def view_conversation(conversation_id: str):
    """Load and render a conversation thread."""
    try:
        meta = get_conversation(conversation_id)
    except (FileNotFoundError, json.JSONDecodeError):
        return HTMLResponse(
            "<div class='text-red-400 p-4'>Conversation not found. Try selecting one from the sidebar.</div>",
            status_code=404
        )
    
    if not meta:
        return HTMLResponse(
            "<div class='text-red-400 p-4'>Conversation not found.</div>",
            status_code=404
        )
    
    try:
        messages = get_full_history(conversation_id)
    except (FileNotFoundError, json.JSONDecodeError):
        return HTMLResponse(
            "<div class='text-red-400 p-4'>Error loading conversation data.</div>",
            status_code=500
        )

    messages_html = render_messages_html(messages)

    return f'''<div style="display:flex;flex-direction:column;overflow:hidden;flex:1;">
    <div id="chat-messages" class="flex-1 overflow-y-auto" style="padding:22px;display:flex;flex-direction:column;gap:18px;">
        {messages_html}
    </div>
    <div class="input-bar">
        <form id="chat-form" style="display:flex;gap:8px;align-items:flex-end;">
            <textarea id="chat-input" name="text" rows="1" placeholder="Message Zipper…" required autocomplete="off"></textarea>
            <button type="submit" class="btn-send">
                <svg width="14" height="14" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M5 12h14M12 5l7 7-7 7"/></svg>
            </button>
        </form>
    </div>
</div>'''


# @app.websocket("/ws/conversations/{conversation_id}")
# async def websocket_endpoint(websocket: WebSocket, conversation_id: str):
#     """
#     WebSocket endpoint for streaming conversation.
#     Accepts JSON: {"text": "user message"}
#     Streams back: {"type": "token"|"tool_call"|"tool_result"|"done"|"error", ...}
#     Handles multiple messages in a loop.
#     TODO: Re-enable when main zipper service supports streaming callbacks
#     """
#     pass


# async def run_conversation_with_streaming(
#     prompt: str,
#     conversation_id: str,
#     stream_callback: Callable
# ) -> str:
#     """
#     Wrapper around run_conversation that injects streaming callbacks.
#     For now, this is a thin wrapper. In the future, we'd modify llm_loop
#     to accept a callback for token-level streaming.
#     TODO: Re-enable when main zipper service supports streaming callbacks
#     """
#     pass


async def _maybe_generate_title(conversation_id: str, first_message: str):
    """If this is the first message, use Haiku to generate a title and summary."""
    meta = get_conversation(conversation_id)
    if meta.get("title", "New Conversation") != "New Conversation":
        return  # already titled
    try:
        resp = await llm_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=128,
            messages=[{"role": "user", "content": (
                f"In one short sentence (max 8 words), give a title for a conversation that starts with this message. "
                f"Reply with ONLY the title, no quotes, no punctuation at the end.\n\nMessage: {first_message[:400]}"
            )}],
        )
        title = resp.content[0].text.strip().rstrip(".")
        if title:
            from storage.conversations import update_meta
            update_meta(conversation_id, title=title)
    except Exception:
        pass


@app.websocket("/ws/conversations/{conversation_id}")
async def websocket_chat(websocket: WebSocket, conversation_id: str):
    """
    WebSocket endpoint for streaming conversation.
    Client sends: {"text": "user message"}
    Server sends: {"type": "token"|"tool_call"|"tool_result"|"done"|"error", ...}
    """
    await websocket.accept()
    is_first_message = True
    try:
        while True:
            data = await websocket.receive_json()
            text = (data.get("text") or "").strip()
            if not text:
                continue

            async def stream_callback(event_type, **kwargs):
                try:
                    if event_type == "tool_call":
                        html = render_tool_block(
                            kwargs["tool"], kwargs["args"], kwargs.get("tool_id", ""), result_text=None
                        )
                        await websocket.send_json({"type": "tool_call_html", "html": html})
                    elif event_type == "tool_result":
                        result_text = extract_result_text(kwargs.get("result", ""))
                        await websocket.send_json({
                            "type": "tool_result_data",
                            "tool_use_id": kwargs.get("tool_use_id", ""),
                            "result": result_text,
                        })
                    else:
                        await websocket.send_json({"type": event_type, **kwargs})
                except Exception:
                    raise  # propagate so llm_loop nulls the callback

            try:
                first = is_first_message
                is_first_message = False
                await run_conversation(
                    description=text,
                    conversation_id=conversation_id,
                    stream_callback=stream_callback,
                )
                if first:
                    asyncio.create_task(_maybe_generate_title(conversation_id, text))
                meta = get_conversation(conversation_id)
                await websocket.send_json({
                    "type": "done",
                    "title": meta.get("title", ""),
                    "status": meta.get("status", "inactive"),
                })
            except Exception as e:
                try:
                    await websocket.send_json({"type": "error", "message": str(e)})
                except Exception:
                    pass

    except WebSocketDisconnect:
        pass


@app.post("/api/conversations/{conversation_id}/message")
async def send_message(conversation_id: str, request: Request, background_tasks: BackgroundTasks):
    """
    Send a message to a conversation.
    Request body: {"text": "user message"}
    Runs the conversation in the background, returns immediately with the prompt added.
    """
    body = await request.json()
    text = body.get("text", "").strip()
    
    if not text:
        return JSONResponse({"error": "Empty message"}, status_code=400)
    
    # Add user message to conversation
    from storage.conversations import append_message
    append_message(conversation_id, "user", text)
    
    # Run the conversation in background
    background_tasks.add_task(run_conversation, description="dashboard", conversation_id=conversation_id)
    
    # Return updated view immediately
    meta = get_conversation(conversation_id)
    if not meta:
        return JSONResponse({"error": "Conversation not found"}, status_code=404)
    
    return HTMLResponse(render_messages_html(get_full_history(conversation_id)))


@app.post("/api/conversations", response_class=HTMLResponse)
async def new_conversation():
    """Create a new conversation."""
    from uuid import uuid4
    
    convo_id = str(uuid4())[:8]
    create_conv(
        title="New Conversation",
        source="dashboard",
        conversation_id=convo_id,
    )
    
    return f'<script>window.location.href = "/?conversation={convo_id}"</script>'


@app.get("/api/status")
async def status():
    """Get system status."""
    try:
        resp = requests.get("http://127.0.0.1:4199/status", timeout=2)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/api/tasks")
async def tasks_list(status_filter: Optional[str] = None):
    """List tasks, optionally filtered by status."""
    tasks = list_tasks(status=status_filter)
    return {"tasks": tasks}


@app.post("/api/tasks")
async def tasks_create(request: Request):
    """Create a new task."""
    data = await request.json()
    task_id = create_task(
        title=data.get("title"),
        description=data.get("description"),
        due_at=data.get("due_at"),
        schedule=data.get("schedule")
    )
    tasks = list_tasks()
    for task in tasks:
        if task["id"] == task_id:
            return task
    return {"error": "task creation failed"}


@app.put("/api/tasks/{task_id}")
async def tasks_update(task_id: str, request: Request):
    """Update a task."""
    data = await request.json()
    if data.get("status") in ("done", "failed"):
        update_task_status(task_id, data.get("status"), result=data.get("result"), error=data.get("error"))
    else:
        patch_task(task_id, data)
    tasks = list_tasks()
    for task in tasks:
        if task["id"] == task_id:
            return task
    return {"error": "task not found"}


@app.get("/api/memory")
async def memory_list():
    """List all memory entries."""
    return {"memory": list_all()}


@app.get("/api/memory/{key}")
async def memory_get(key: str):
    """Get a memory entry."""
    value = get(key)
    return {"key": key, "value": value}


@app.post("/api/memory/{key}")
async def memory_set(key: str, request: Request):
    """Set a memory entry."""
    data = await request.json()
    set(key, data.get("value"))
    return {"key": key, "value": data.get("value")}


@app.delete("/api/memory/{key}")
async def memory_delete(key: str):
    """Delete a memory entry."""
    delete(key)
    return {"key": key, "status": "deleted"}


@app.get("/api/config")
async def config():
    """Return public client configuration."""
    return {"user": USER_NAME}


@app.get("/health")
async def health():
    """Health check."""
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=4201, reload=False)
