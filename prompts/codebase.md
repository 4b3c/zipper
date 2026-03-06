## Zipper Codebase

This is Zipper's own source code. Update this file when you add new components.

### Entry Points
- `main.py` — FastAPI server (port 4199). Routes: `/chat`, `/discord`, `/wake`, `/status`
- `bot/discord_bot.py` — thin relay (port 4200). Creates Discord threads, forwards to `/discord`, serves `/send`, `/history`, `/edit`, `/react`, `/inject`
- `utils/restart_watcher.py` — spawned by `tools/restart.py` after a zipper restart; polls `/status`, resumes via `/chat`, rolls back via git stash on crash
- `run.py` — CLI dev tool. POSTs to `/chat` from terminal
- `utils/setup_cron.py` — writes crontab entries from `data/schedule.json` (daily recurring + date-pinned oneshot entries)

### Core
- `llm/__init__.py` — `run_conversation()`, loads system prompt, Anthropic client, compaction threshold
- `llm/loop.py` — `llm_loop`, `_owns`, `maybe_compact`, `select_model`, `parse_ratings`, `strip_ratings`
- `llm/messages.py` — `_sanitize_messages`, `serialize_content`, `_has_tool_use`, `_is_tool_result_message`

### Utils (`utils/`)
- `notify.py` — `notify_discord` / `notify_discord_async`: POST to discord bot's `/send` (sync + async)
- `constants.py` — `BOT_URL`, `ZIPPER_URL` (single source of truth for service URLs)
- `http.py` — `post_json(url, data, timeout)`: shared urllib POST helper
- `text.py` — `smart_split`, `title_to_slug`
- `restart_watcher.py` — spawned by `tools/restart.py`; polls `/status`, resumes conversation via `/chat`
- `setup_cron.py` — writes crontab entries from `data/schedule.json`

### Tools (`tools/`)
- `__init__.py` — tool schemas (TOOLS list), dispatch (execute_tool), per-conversation onboarding
- `file.py` — filesystem operations: list, read, write, edit, delete, grep
- `bash.py` — shell execution, 30s default timeout
- `restart.py` — registers watchdog with discord bot, then triggers systemctl restart via BreakLoop
- `task.py` — task queue CRUD
- `discord.py` — Discord interactions: send (returns message_id), history, edit, react
- `web.py` — Brave Search (`search` mode) and HTTP GET (`fetch` mode, HTML stripped to text)
- `signals.py` — `BreakLoop` exception, raised by tools to stop the LLM loop early

### Storage (`storage/`)
- `conversations.py` — conversation + version CRUD. Each conversation is a folder under `data/conversations/`. `find_conversation_by_thread(discord_thread_id)` scans meta files to resolve Discord thread → conversation.
- `trace.py` — append-only tool call log per conversation (`trace.json`)
- `memory.py` — persistent key-value store (`data/memory.json`)
- `tasks.py` — task queue (`data/tasks/queue.json`)
- `schedule.py` — `load/save_schedule`, `load/save_wake_log`, `add_oneshot`

### Dashboard (`dashboard/`)
- `main.py` — FastAPI app (port 4201). Routes: `GET /`, `GET /api/conversations`, `GET /api/conversations/{id}/view`, `POST /api/conversations`, `WS /ws/conversations/{id}`
- `templates/index.html` — main page layout (sidebar + chat area)
- `static/app.js` — frontend logic (WebSocket, DOM updates, streaming render)
- `zipper-dashboard.service` — systemd service file (copy to `/etc/systemd/system/` to install)

### Prompts (`prompts/`)
- `main.md` — Zipper's main system prompt. Edit this to change behavior, rules, or tool documentation
- `codebase.md` — this file. Keep it current as you add components
- `bash.md` — shell environment facts, injected into the bash tool's first-use onboarding
- `discord.md` — Discord tool usage guide, injected into the discord tool's first-use onboarding

### Key Patterns
- Model routing: self-rating system — Zipper appends `{{c:X, d:X, a:X}}` to responses; total score picks the next model (>11=Opus, >6=Sonnet, else Haiku). See `llm.py:select_model`.
- Interrupt system: `run_task` writes `last_owner_token` to `meta.json` synchronously before any await. `_owns()` reads meta to check ownership at every yield point. Lost ownership → silent exit.
- Discord flow: discord_bot creates thread → POSTs to `/discord` → zipper finds/creates conversation → fires background task → POSTs result to `/send` when done.
- Restart flow: `restart(zipper)` → `tools/restart.py` spawns `utils/restart_watcher.py` as a detached subprocess, then triggers `systemctl restart zipper` → watcher polls `/status` → resumes via `/chat` when healthy.
- Compaction: after 20 messages, old messages are summarized and a new version file is created
- Message sanitization: orphaned tool_use/tool_result pairs stripped before each API call; consecutive user messages get a synthetic `[interrupted]` assistant turn inserted
- Tool onboarding: first call to each tool per conversation prepends a usage guide (from `tools/__init__.py:ONBOARDING`)
