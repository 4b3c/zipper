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
    
    # Reverse to show newest first, then paginate
    conversations.reverse()
    total = len(conversations)
    page_size = 10
    paginated = conversations[offset : offset + page_size]
    
    html = '<div class="space-y-2" id="conversation-list-items">'
    for convo in paginated:
        convo_id = convo["id"]
        title = convo.get("title", "Untitled")
        created = convo.get("created_at", "")
        
        html += f'''
        <a href="/?conversation={convo_id}"
           class="block p-3 rounded hover:bg-slate-700 transition text-sm">
            <div class="font-medium text-white">{title}</div>
            <div class="text-xs text-slate-400">{created[:10]}</div>
        </a>
        '''
    
    # Add "Load More" button if there are more conversations
    if offset + page_size < total:
        remaining = total - (offset + page_size)
        html += f'''
        <button id="load-more-btn" 
                class="w-full p-3 text-center text-xs text-slate-400 hover:text-slate-200 transition border-t border-slate-700 mt-2"
                onclick="loadMoreConversations({offset + page_size})">
            Load {min(page_size, remaining)} more...
        </button>
        '''
    
    html += '</div>'
    return html


def format_message_content(content) -> str:
    """
    Format message content (text, tool_use, tool_result blocks) as rich HTML.
    Handles Markdown formatting and creates collapsible bubbles.
    """
    if isinstance(content, str):
        # Simple string — apply basic markdown + HTML escaping
        return format_text_as_html(content)
    
    if not isinstance(content, list):
        # Unknown format — escape and return
        return escape_html(json.dumps(content, indent=2))
    
    # List of content blocks (text, tool_use, tool_result, etc.)
    html_parts = []
    for block in content:
        if not isinstance(block, dict):
            html_parts.append(f"<div>{escape_html(str(block))}</div>")
            continue
        
        block_type = block.get("type")
        
        if block_type == "text":
            text = block.get("text", "")
            html_parts.append(format_text_as_html(text))
        
        elif block_type == "tool_use":
            tool_name = block.get("name", "unknown")
            tool_input = block.get("input", {})
            tool_id = block.get("id", "")
            html_parts.append(render_tool_call_bubble(tool_name, tool_input, tool_id))
        
        elif block_type == "tool_result":
            tool_use_id = block.get("tool_use_id", "")
            result_content = block.get("content", "")
            html_parts.append(render_tool_result_bubble(tool_use_id, result_content))
    
    return "".join(html_parts)


def format_text_as_html(text: str) -> str:
    """
    Convert plain text with Markdown-style formatting to HTML.
    - **bold** → <strong>bold</strong>
    - - bullet → <li>bullet</li>
    - etc.
    """
    text = escape_html(text)
    
    # Convert **bold** to <strong>bold</strong>
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    
    # Convert - bullet lists (lines starting with -)
    lines = text.split("\n")
    result = []
    in_list = False
    for line in lines:
        if line.strip().startswith("- "):
            if not in_list:
                result.append("<ul class='list-disc list-inside ml-2'>")
                in_list = True
            item_text = line.strip()[2:]
            result.append(f"<li>{item_text}</li>")
        else:
            if in_list:
                result.append("</ul>")
                in_list = False
            if line.strip():
                result.append(f"<div>{line}</div>")
    if in_list:
        result.append("</ul>")
    
    return "<div class='text-white text-sm space-y-2'>" + "".join(result) + "</div>"


def escape_html(text: str) -> str:
    """Escape HTML special characters."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_tool_call_bubble(tool_name: str, tool_input: dict, tool_id: str = "") -> str:
    """
    Render a collapsible 'tool call' bubble.
    Shows tool name and parameter count, expandable to see full input.
    """
    input_json = json.dumps(tool_input, indent=2)
    param_count = len(tool_input)
    
    # Generate unique ID for this details element
    details_id = f"tool-{tool_id[:8]}" if tool_id else f"tool-{id(tool_input)}"
    
    return f'''
    <details class="mb-3 group">
        <summary class="cursor-pointer bg-blue-900/30 border border-blue-700 rounded p-3 text-sm font-mono text-blue-300 hover:bg-blue-900/50 transition">
            🔧 called <strong>{escape_html(tool_name)}</strong> with {param_count} param{'s' if param_count != 1 else ''}
        </summary>
        <div class="mt-2 ml-4 bg-slate-900 border border-slate-700 rounded p-3">
            <pre class="text-xs text-slate-300 overflow-x-auto whitespace-pre-wrap break-words"><code>{escape_html(input_json)}</code></pre>
        </div>
    </details>
    '''


def render_tool_result_bubble(tool_use_id: str, result_content) -> str:
    """
    Render a collapsible 'result' bubble.
    Shows truncated result, expandable to see full output.
    """
    # Handle different result content types
    if isinstance(result_content, dict):
        result_text = json.dumps(result_content, indent=2)
    else:
        result_text = str(result_content)
    
    # Truncate for display
    truncated = result_text[:200] + "..." if len(result_text) > 200 else result_text
    details_id = f"result-{tool_use_id[:8]}" if tool_use_id else f"result-{id(result_content)}"
    
    return f'''
    <details class="mb-3 group">
        <summary class="cursor-pointer bg-amber-900/30 border border-amber-700 rounded p-3 text-sm font-mono text-amber-300 hover:bg-amber-900/50 transition">
            📦 result {f'({len(result_text)} bytes)' if len(result_text) > 100 else ''}
        </summary>
        <div class="mt-2 ml-4 bg-slate-900 border border-slate-700 rounded p-3">
            <pre class="text-xs text-slate-300 overflow-x-auto whitespace-pre-wrap break-words max-h-64"><code>{escape_html(result_text)}</code></pre>
        </div>
    </details>
    '''


@app.get("/api/conversations/{conversation_id}/view", response_class=HTMLResponse)
async def view_conversation(conversation_id: str):
    """Load and render a conversation thread."""
    meta = get_conversation(conversation_id)
    if not meta:
        return "<div>Conversation not found</div>"
    
    version = get_latest_version(conversation_id)
    messages = version.get("messages", [])
    
    messages_html = ""
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        timestamp = msg.get("timestamp", "")
        
        # Format content using rich formatter
        content_html = format_message_content(content)
        
        role_class = "bg-blue-900" if role == "user" else "bg-slate-700"
        messages_html += f'''
        <div class="mb-4 p-4 rounded {role_class} space-y-2">
            <div class="text-xs text-slate-300">{role.title()} • {timestamp or 'just now'}</div>
            {content_html}
        </div>
        '''
    
    return f'''
    <div class="flex flex-col h-full overflow-hidden">
        <div id="chat-messages" class="flex-1 overflow-y-auto space-y-4 p-4">
            {messages_html if messages_html else '<div class="text-slate-400 text-sm">No messages yet</div>'}
        </div>
        <div id="message-input" class="border-t border-slate-600 p-4 flex-shrink-0">
            <form id="chat-form" class="flex gap-2">
                <input type="text" 
                       name="text"
                       placeholder="Message..." 
                       class="flex-1 px-3 py-2 bg-slate-800 text-white rounded border border-slate-600 focus:outline-none focus:border-blue-500"
                       required
                       autocomplete="off">
                <button type="submit" class="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 flex-shrink-0">
                    Send
                </button>
            </form>
        </div>
    </div>
    '''


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
    
    messages_html = ""
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        timestamp = msg.get("timestamp", "")
        
        # Format content (could be string or list of blocks)
        if isinstance(content, list):
            content_text = json.dumps(content, indent=2)
        else:
            content_text = str(content)
        
        # Basic HTML escaping
        content_text = content_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        
        role_class = "bg-blue-900" if role == "user" else "bg-slate-700"
        messages_html += f'''
        <div class="mb-4 p-4 rounded {role_class}">
            <div class="text-xs text-slate-300 mb-2">{role} • {timestamp}</div>
            <div class="text-white whitespace-pre-wrap text-sm">{content_text}</div>
        </div>
        '''
    
    return HTMLResponse(messages_html)


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


@app.get("/health")
async def health():
    """Health check."""
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=4201, reload=False)
