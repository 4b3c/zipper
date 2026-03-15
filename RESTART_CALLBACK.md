# Restart Callback System

## Overview

When `restart(mode=zipper)` is called, the platform spawns a detached `restart_watcher` process that monitors Zipper's recovery and resumes the conversation after restart.

## Architecture

### 1. Restart Watcher (`utils/restart_watcher.py`)

**Spawned by:** Platform's `restart` tool  
**Args:** `<conversation_id> <project_dir>`

**Flow:**
1. Loads `.env` for API credentials
2. Waits up to 10s for Zipper to go down (cleanup)
3. Polls `/status` endpoint every 2s, waits up to 45s for recovery
4. If healthy:
   - Waits 3s for full startup
   - POSTs a resume message to `/chat` with `source=restart_watcher`
   - Notifies Discord with ✅ success + LLM response
5. If unhealthy:
   - Git stashes uncommitted code changes
   - Restarts Zipper with clean state
   - Polls again for recovery
   - If recovered: notifies Discord with ❌ failure + stash details
   - If still broken: notifies Discord for manual intervention

### 2. Chat Endpoint (`main.py`, `/chat`)

**Method:** POST  
**Request Model:** `ChatRequest`
- `prompt: str` — the message to process
- `conversation_id: str | None` — existing or new conversation
- `source: str = "api"` — caller source (can be "restart_watcher")

**Behavior:**
- If conversation_id is provided and doesn't exist, creates it
- Runs LLM via `run_conversation()`
- Returns `{"conversation_id": "...", "result": "..."}`

The `source` field allows tracking where requests originated.

### 3. Integration Points

**Conversation metadata (`storage/conversations.py`):**
- Each conversation stores `source` field
- Restart-triggered conversations have `source="restart_watcher"`
- Enables audit trail and filtering

**Thread ID mapping (`storage/conversations.py`):**
- `get_conversation_thread_id(conversation_id)` retrieves Discord thread for notifications
- Used by restart_watcher to post recovery status to the correct channel

**Notification handler (`utils/notify.py`):**
- `notify_discord(message, thread_id=...)` posts recovery status
- Used for both success and failure notifications

## Testing the Flow

### Manual Simulation

```bash
cd /opt/zipper/app
python3 << 'EOF'
import json
import urllib.request
from storage.conversations import create_conversation

conv = create_conversation(
    title="Test restart flow",
    source="test",
)

payload = json.dumps({
    "prompt": "Restart successful. All systems nominal.",
    "conversation_id": conv,
    "source": "restart_watcher",
}).encode()

req = urllib.request.Request(
    "http://127.0.0.1:4199/chat",
    data=payload,
    headers={"Content-Type": "application/json"},
    method="POST",
)

with urllib.request.urlopen(req, timeout=30) as r:
    print(json.loads(r.read()))
EOF
```

### Real Restart Test

```python
from restart import restart
restart(mode="zipper")
```

Wait for:
1. Zipper to restart (systemctl)
2. Restart watcher to spawn and monitor recovery
3. Recovery message posted to Discord
4. Conversation resumed with LLM response

## Failure Modes & Recovery

| Scenario | Watcher Action | Outcome |
|----------|---|----------|
| Zipper crashes on startup | Detects failed `/status`, stashes code, restarts clean | Discord notified, manual review needed if still broken |
| Network timeout | Retries up to 45s | Automatic recovery if transient |
| `/chat` endpoint error | Exception caught, logged | POST fails, watcher logs error |
| Missing .env | Loads gracefully, continues | May fail at Discord notification step |
| Wrong conversation_id | Creates new conversation | New thread used, possible confusion |

## Future Enhancements

- [ ] Retry logic for transient `/chat` failures
- [ ] Configurable STARTUP_TIMEOUT per environment
- [ ] Structured logging (JSON) for restart events
- [ ] Alert escalation if restart fails N times
- [ ] Dashboard widget showing restart history

## Files Modified

- `utils/restart_watcher.py` — primary implementation
- `main.py` — `/chat` endpoint (unchanged, already works)
- `storage/conversations.py` — already has thread_id lookup
- `utils/notify.py` — already has Discord integration

All components were already in place; restart_watcher ties them together.
