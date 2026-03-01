## Zipper Codebase

This is Zipper's own source code. Update this file when you add new components.

### Entry Points
- `main.py` — FastAPI server (port 4199). Routes: `/chat`, `/wake`, `/status`
- `discord_bot.py` — Discord bot. Maps threads → conversations. Hosts `/notify` and `/watch-restart` on port 4200
- `restart_watcher.py` — spawned by discord bot after a zipper restart; polls `/status` then resumes the conversation
- `run.py` — CLI dev tool. POSTs to `/chat` from terminal
- `setup_cron.py` — writes crontab entries from `data/schedule.json`

### Core
- `llm.py` — LLM loop. Calls Claude, executes tools, loops until `end_turn`. Handles compaction, message sanitization, model routing
- `utils.py` — shared helpers. `notify_discord` / `notify_discord_async` post to the bot's `/notify` endpoint

### Tools (`tools/`)
- `__init__.py` — tool schemas (TOOLS list), dispatch (execute_tool), per-conversation onboarding
- `file.py` — filesystem operations: list, read, write, edit, delete, grep
- `bash.py` — shell execution, 30s default timeout
- `restart.py` — registers watchdog with discord bot, then triggers systemctl restart via BreakLoop
- `task.py` — task queue CRUD
- `notify.py` — posts to discord bot `/notify`
- `web.py` — Brave Search (`search` mode) and HTTP GET (`fetch` mode, HTML stripped to text)
- `signals.py` — `BreakLoop` exception, raised by tools to stop the LLM loop early

### Storage (`storage/`)
- `conversations.py` — conversation + version CRUD. Each conversation is a folder under `data/conversations/`
- `trace.py` — append-only tool call log per conversation (`trace.json`)
- `memory.py` — persistent key-value store (`data/memory.json`)
- `tasks.py` — task queue (`data/tasks/queue.json`)

### System Prompts (`system_prompts/`)
- `main.md` — Zipper's main system prompt. Edit this to change behavior, rules, or tool documentation
- `codebase.md` — this file. Keep it current as you add components

### Key Patterns
- Model routing: message contains "opus" → Opus, "sonnet" → Sonnet, else Haiku (see `llm.py:select_model`)
- Restart flow: `restart(zipper)` → watchdog registered on bot → BreakLoop raised → systemctl restart → watcher resumes conversation
- Compaction: after 20 messages, old messages are summarized and a new version file is created
- Message sanitization: orphaned tool_use/tool_result pairs stripped before each API call
- Tool onboarding: first call to each tool per conversation prepends a usage guide (from `tools/__init__.py:ONBOARDING`)
