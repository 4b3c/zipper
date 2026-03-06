"""
Zipper Dashboard — web frontend for Zipper using WebSocket streaming.
Serves HTML + Tailwind, streams LLM responses in real-time.
"""

import asyncio
import json
import os
from datetime import datetime
from typing import Optional, Callable

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.requests import Request

import sys
sys.path.insert(0, "/opt/zipper/app")

from storage.conversations import list_conversations, get_conversation, create_conversation as create_conv
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
async def list_convos():
    """Return conversation list as HTML (for sidebar)."""
    conversations = list_conversations()
    
    html = '<div class="space-y-2">'
    for convo in conversations:
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
    
    html += '</div>'
    return html


@app.get("/api/conversations/{conversation_id}/view", response_class=HTMLResponse)
async def view_conversation(conversation_id: str):
    """Load and render a conversation thread."""
    convo = get_conversation(conversation_id)
    if not convo:
        return "<div>Conversation not found</div>"
    
    messages_html = ""
    for msg in convo.get("messages", []):
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
    
    return f'''
    <div id="chat-messages" class="flex-1 overflow-y-auto space-y-4 p-4">
        {messages_html}
    </div>
    <div id="message-input" class="border-t border-slate-600 p-4">
        <form id="chat-form">
            <div class="flex gap-2">
                <input type="text" 
                       name="text"
                       placeholder="Message..." 
                       class="flex-1 px-3 py-2 bg-slate-800 text-white rounded border border-slate-600 focus:outline-none focus:border-blue-500"
                       required
                       autocomplete="off">
                <button type="submit" class="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700">
                    Send
                </button>
            </div>
        </form>
    </div>
    '''


@app.websocket("/ws/conversations/{conversation_id}")
async def websocket_endpoint(websocket: WebSocket, conversation_id: str):
    """
    WebSocket endpoint for streaming conversation.
    Accepts JSON: {"text": "user message"}
    Streams back: {"type": "token"|"tool_call"|"tool_result"|"done"|"error", ...}
    """
    await websocket.accept()
    
    try:
        # Wait for user message
        data = await websocket.receive_text()
        message_data = json.loads(data)
        user_text = message_data.get("text", "").strip()
        
        if not user_text:
            await websocket.send_json({"type": "error", "message": "Empty message"})
            return
        
        # Create streaming callback
        async def stream_callback(event_type: str, data: dict):
            try:
                await websocket.send_json({"type": event_type, **data})
            except Exception as e:
                print(f"[dashboard] websocket send error: {e}")
        
        # Run the conversation with streaming
        try:
            result = await run_conversation_with_streaming(
                prompt=user_text,
                conversation_id=conversation_id,
                stream_callback=stream_callback
            )
        except Exception as e:
            print(f"[dashboard] conversation error: {e}")
            await websocket.send_json({"type": "error", "message": str(e)})
            return
        
        await websocket.send_json({"type": "done"})
        
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"[dashboard] websocket error: {e}")
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except:
            pass
    finally:
        await websocket.close()


async def run_conversation_with_streaming(
    prompt: str,
    conversation_id: str,
    stream_callback: Callable
) -> str:
    """
    Wrapper around run_conversation that injects streaming callbacks.
    For now, this is a thin wrapper. In the future, we'd modify llm_loop
    to accept a callback for token-level streaming.
    """
    result = await run_conversation(prompt, conversation_id)
    
    # TODO: implement token-level streaming callback
    # For now, just send the final result
    await stream_callback("message", {"content": result})
    
    return result


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


@app.get("/health")
async def health():
    """Health check."""
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=4201, reload=False)
