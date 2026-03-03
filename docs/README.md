# Zipper

A self-building, self-repairing AI assistant that modifies its own source code and lives on a VPS.

Zipper can:
- Execute shell commands
- Read, write, and edit files
- Manage a task queue across sessions
- Interact with Discord
- Search the web and fetch URLs
- Restart itself to test code changes
- Modify its own system prompts and behavior

## Architecture

**Entry Points:**
- `main.py` — FastAPI server on port 4199. Routes: `/chat`, `/discord`, `/wake`, `/status`
- `discord_bot.py` — Discord bot on port 4200. Thin relay: creates threads, forwards messages to zipper, sends zipper's responses back to Discord.
- `run.py` — CLI dev tool for testing

**Core Loop:**
- `llm.py` — LLM execution engine. Calls Claude, executes tools, loops until turn complete
- `tools/` — Tool implementations: file, bash, task, discord, web, restart
- `storage/` — Conversation history, task queue, memory, trace logs

## Usage

### Chat API
```bash
curl -X POST http://localhost:4199/chat \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "hello",
    "conversation_id": "my-chat"
  }'
```

### CLI
```bash
python run.py "what's the current status?"
```

### Discord
Post in a thread in the configured Discord server. Zipper will reply in the same thread.

## Key Concepts

**Conversations** — Each `/chat` call or Discord thread is a conversation. State is persisted in `data/conversations/`

**Self-Repair** — Use `restart(zipper)` after code changes. It:
1. Registers a watchdog with the Discord bot
2. Restarts the main process
3. Resumes this conversation when healthy

**Task Queue** — Recurring tasks survive restarts. Mark a task done with `task(update, status="done")` and the next occurrence is created automatically

**Model Routing** — Zipper self-rates each response with complexity/difficulty/ambiguity scores. The total determines the model for the next turn: high scores use Opus, medium use Sonnet, low use Haiku.

## Development

**Add a Tool:**
1. Create `tools/my_tool.py` with an `execute(params)` function
2. Add schema to `tools/__init__.py:TOOLS`
3. Add usage guide to `tools/__init__.py:ONBOARDING`

**Change Behavior:**
Edit `prompts/main.md` and restart with `restart(zipper)`

**Test Changes:**
```bash
python run.py "test message"
# or
curl -X POST http://localhost:4199/chat ...
```

## Files

- `llm.py` — Main LLM loop
- `main.py` — FastAPI server
- `discord_bot.py` — Discord integration
- `tools/` — Tool implementations
- `storage/` — Conversation, task, and memory persistence
- `prompts/` — Behavior and tool documentation
- `requirements.txt` — Python dependencies
