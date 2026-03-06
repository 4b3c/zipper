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

from fastapi import FastAPI, BackgroundTasks
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.requests import Request

import sys
sys.path.insert(0, "/opt/zipper/app")

from storage.conversations import list_conversations, get_conversation, create_conversation as create_conv, get_latest_version
from storage.memory import get, set, delete, all as list_all
from storage.tasks import list_tasks, create_task, update_task_status, patch_task
from llm import run_conversation

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
    page_size = 10
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

        status_dot = '<span class="w-1.5 h-1.5 rounded-full bg-green-500 inline-block flex-shrink-0"></span>' if status == "active" else ""
        summary_preview = escape_html((summary[:55] + "…") if summary and len(summary) > 55 else summary)

        html += f'''<a href="/?conversation={convo_id}"
   class="block px-2 py-2 rounded-lg hover:bg-slate-800 transition group">
    <div class="flex items-center gap-1.5 mb-0.5">
        {status_dot}
        <div class="font-medium text-slate-200 text-xs truncate flex-1 group-hover:text-white">{title}</div>
        <span class="text-[10px] text-slate-600 flex-shrink-0">{timestamp}</span>
    </div>
    {f'<div class="text-[11px] text-slate-500 truncate pl-3">{summary_preview}</div>' if summary_preview else ''}
</a>'''

    if offset + page_size < total:
        remaining = total - (offset + page_size)
        html += f'''<button id="load-more-btn"
        class="w-full mt-1 px-2 py-1.5 text-center text-xs text-slate-500 hover:text-slate-300 transition"
        onclick="loadMoreConversations({offset + page_size})">
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


def format_text_as_html(text: str) -> str:
    """Wrap raw markdown text in a markdown-body div (rendered client-side by marked.js)."""
    return f'<div class="markdown-body text-sm">{escape_html(text)}</div>'


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
            html_parts.append(render_tool_call_bubble(
                block.get("name", "unknown"),
                block.get("input", {}),
                block.get("id", ""),
            ))
        elif block_type == "tool_result":
            html_parts.append(render_tool_result_bubble(
                block.get("tool_use_id", ""),
                block.get("content", ""),
            ))

    return "".join(html_parts)


def render_tool_call_bubble(tool_name: str, tool_input: dict, tool_id: str = "") -> str:
    """Collapsible tool call bubble — pill summary + JSON input."""
    input_json = json.dumps(tool_input, indent=2)
    param_count = len(tool_input)
    label = f"{escape_html(tool_name)} <span class='text-slate-500 font-normal'>({param_count} param{'s' if param_count != 1 else ''})</span>"

    return f'''<details class="my-1.5">
    <summary class="cursor-pointer select-none inline-flex items-center gap-2 px-3 py-1.5 rounded-full bg-slate-800 border border-slate-700 text-xs text-slate-300 hover:border-slate-500 hover:text-white transition list-none">
        <svg class="w-3 h-3 text-slate-500 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/></svg>
        <code class="font-mono font-medium">{label}</code>
    </summary>
    <div class="mt-2 rounded-lg bg-slate-900/80 border border-slate-700/60 overflow-hidden">
        <div class="px-3 py-1 bg-slate-800/60 border-b border-slate-700/60 text-[10px] text-slate-500 font-mono uppercase tracking-wider">input</div>
        <pre class="p-3 text-xs text-slate-300 overflow-x-auto"><code>{escape_html(input_json)}</code></pre>
    </div>
</details>'''


def render_tool_result_bubble(tool_use_id: str, result_content) -> str:
    """Collapsible tool result bubble."""
    if isinstance(result_content, list):
        # List of content blocks — extract text
        parts = []
        for item in result_content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
            else:
                parts.append(json.dumps(item, indent=2))
        result_text = "\n".join(parts)
    elif isinstance(result_content, dict):
        result_text = json.dumps(result_content, indent=2)
    else:
        result_text = str(result_content)

    char_count = len(result_text)
    size_label = f"{char_count:,} chars" if char_count > 100 else ""

    return f'''<details class="my-1.5">
    <summary class="cursor-pointer select-none inline-flex items-center gap-2 px-3 py-1.5 rounded-full bg-slate-800/60 border border-slate-700/60 text-xs text-slate-400 hover:border-slate-500 hover:text-slate-300 transition list-none">
        <svg class="w-3 h-3 text-slate-600 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"/></svg>
        <span>result</span>
        {f'<span class="text-slate-600">{escape_html(size_label)}</span>' if size_label else ''}
    </summary>
    <div class="mt-2 rounded-lg bg-slate-900/80 border border-slate-700/60 overflow-hidden">
        <div class="px-3 py-1 bg-slate-800/60 border-b border-slate-700/60 text-[10px] text-slate-500 font-mono uppercase tracking-wider">output</div>
        <pre class="p-3 text-xs text-slate-300 overflow-x-auto max-h-72 overflow-y-auto"><code>{escape_html(result_text)}</code></pre>
    </div>
</details>'''


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
        return f'''<div class="pl-10 space-y-1">{content_html}</div>'''

    if role == "user":
        return f'''<div class="flex justify-end">
    <div class="flex flex-col items-end max-w-[80%] min-w-0">
        <div class="user-bubble px-4 py-3 bg-blue-600 rounded-2xl rounded-tr-sm text-white text-sm min-w-0">{content_html}</div>
        <div class="text-[11px] text-slate-500 mt-1 mr-1">{escape_html(USER_NAME)}{time_html}</div>
    </div>
</div>'''
    else:
        return f'''<div class="flex gap-3">
    <div class="w-7 h-7 rounded-full bg-violet-700 flex items-center justify-center text-xs font-bold flex-shrink-0 mt-0.5 select-none">Z</div>
    <div class="flex-1 min-w-0">
        <div class="text-[11px] text-slate-400 mb-2">Zipper{time_html}</div>
        <div class="space-y-1 min-w-0">{content_html}</div>
    </div>
</div>'''


def render_messages_html(messages: list) -> str:
    """Render all messages as HTML."""
    if not messages:
        return '<div class="text-slate-600 text-sm">No messages yet.</div>'
    return "\n".join(render_message(msg) for msg in messages)


@app.get("/api/conversations/{conversation_id}/metadata", response_class=JSONResponse)
async def get_conversation_metadata(conversation_id: str):
    """Get conversation metadata (title, status, summary, dates, etc.)."""
    meta = get_conversation(conversation_id)
    if not meta:
        return JSONResponse({"error": "Conversation not found"}, status_code=404)
    
    # Count messages in latest version
    version = get_latest_version(conversation_id)
    message_count = len(version.get("messages", []))
    
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
        version = get_latest_version(conversation_id)
    except (FileNotFoundError, json.JSONDecodeError):
        return HTMLResponse(
            "<div class='text-red-400 p-4'>Error loading conversation data.</div>",
            status_code=500
        )
    
    messages = version.get("messages", [])
    messages_html = render_messages_html(messages)

    return f'''<div class="flex flex-col overflow-hidden flex-1">
    <div id="chat-messages" class="flex-1 overflow-y-auto space-y-5 px-5 py-5">
        {messages_html}
    </div>
    <div class="border-t border-slate-800 px-4 py-3 flex-shrink-0">
        <form id="chat-form" class="flex gap-2 items-end">
            <textarea id="chat-input"
                      name="text"
                      rows="1"
                      placeholder="Message Zipper…"
                      class="flex-1 px-3 py-2 bg-slate-800 text-white rounded-xl border border-slate-700 focus:outline-none focus:border-blue-500 text-sm placeholder-slate-500 transition"
                      required
                      autocomplete="off"></textarea>
            <button type="submit"
                    class="w-9 h-9 flex items-center justify-center bg-blue-600 text-white rounded-xl hover:bg-blue-500 transition flex-shrink-0 disabled:opacity-40 disabled:cursor-not-allowed">
                <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M5 12h14M12 5l7 7-7 7"/></svg>
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
    background_tasks.add_task(run_conversation, conversation_id=conversation_id)
    
    # Return updated view immediately
    meta = get_conversation(conversation_id)
    if not meta:
        return JSONResponse({"error": "Conversation not found"}, status_code=404)
    
    version = get_latest_version(conversation_id)
    messages = version.get("messages", [])
    return HTMLResponse(render_messages_html(messages))


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
