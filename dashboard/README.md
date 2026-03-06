# Zipper Dashboard

A web-based interface for Zipper — FastAPI + Tailwind + WebSocket streaming.

## Features

- **Conversation Management**: Browse all conversations, create new ones, continue existing threads
- **Real-time Streaming**: WebSocket connection for live message rendering (tokens as they arrive)
- **Syntax Highlighting**: Code blocks auto-highlighted with Highlight.js
- **No Build Step**: Served as plain HTML/CSS/JS, uses Tailwind CDN
- **Responsive Design**: Built with Tailwind CSS for mobile-friendly interface

## Architecture

```
dashboard/
├── main.py              # FastAPI app, routes, WebSocket handler
├── templates/
│   └── index.html       # Main page layout (sidebar + chat area)
├── static/
│   └── app.js          # Frontend logic (WebSocket, DOM updates)
└── README.md
```

## Running

The service is managed by systemd:

```bash
systemctl status zipper-dashboard
systemctl restart zipper-dashboard
journalctl -u zipper-dashboard -f
```

Environment file: `/opt/zipper/app/.env` (loaded by systemd)

Port: `127.0.0.1:4201`

## API Endpoints

### HTTP Routes

- `GET /` — Renders the main chat interface
- `GET /api/conversations` — List all conversations (HTML)
- `GET /api/conversations/{id}/view` — Load and render a specific conversation
- `POST /api/conversations` — Create a new conversation

### WebSocket

- `WS /ws/conversations/{id}` — Stream conversation responses

**Client sends:**
```json
{"text": "user message"}
```

**Server streams back:**
```json
{"type": "token", "data": "hello"}
{"type": "tool_call", "tool": "bash", "args": "..."}
{"type": "tool_result", "tool": "bash", "result": "..."}
{"type": "done"}
```

## Frontend (app.js)

- Fetches conversation list on load
- Loads conversation view on URL change (`/?conversation={id}`)
- Opens WebSocket connection when sending messages
- Renders tokens as they arrive (typewriter effect)
- Shows typing indicators and tool execution
- Auto-scrolls to latest message

## Next Steps

- **Token-level streaming**: Modify `llm/loop.py` to accept a callback for per-token events
- **Auto-refresh conversations**: WebSocket-based sidebar updates when new conversations are created
- **Memory/Tasks viewers**: Embed memory and task viewers in separate tabs
- **File browser**: Quick access to important files (prompts, configs, etc.)
- **Nginx reverse proxy**: Set up HTTPS + public access

## Notes

- No framework bloat (no React, Vue, Svelte)
- Streaming works at the HTTP level via WebSocket
- Conversation data comes from `storage/conversations.py` layer
- Tool output formatting happens client-side for fast rendering
