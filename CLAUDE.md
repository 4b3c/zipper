# CLAUDE.md

This file provides guidance to Claude Code when working on the Zipper codebase.

## Project Overview

Zipper is a self-building, self-repairing AI assistant that runs 24/7 on a VPS. It receives tasks via Discord or a web dashboard, executes them autonomously using tools, and can modify its own source code, restart itself, and push to GitHub.

## Architecture

### Runtime
- **Zipper** runs as a persistent FastAPI server on port `4199` managed by systemd on the VPS (not Docker)
- **Discord bot** runs in Docker (`docker-compose.discord.yml`), communicates with zipper over `http://localhost:4199`
- **Cron** hits the `/wake` endpoint at scheduled times defined in `data/schedule.json`

### Entry Points
- `main.py` — FastAPI server, exposes `/chat`, `/wake`, `/status`
- `discord_bot.py` — Discord bot, maps threads to conversations
- `run.py` — dev tool, POSTs to `/chat` from CLI
- `setup_cron.py` — generates crontab entries from `data/schedule.json`

### LLM Loop (`llm.py`)
- Calls Claude, parses tool calls, executes them, loops until `end_turn`
- Model routing by keyword: `opus` → Opus, `sonnet` → Sonnet, default → Haiku
- Validates messages before each API call to prevent corrupted tool_result/tool_use pairs
- Rolls back last message on API failure to keep conversation clean
- Compacts conversation after `COMPACTION_THRESHOLD` messages

### Tools (`tools/`)
- `file` — list/read/write/edit files
- `bash` — shell execution, 30s timeout
- `search` — Brave Search API

### Storage (`storage/`)
All data is JSON, no database.

```
data/
├── conversations/
│   └── {id}/
│       ├── meta.json        # title, source, status, summary, key_points
│       ├── trace.json       # tool call log (tool, args, output, error, duration)
│       └── versions/
│           ├── 0.json       # messages + system_prompt + rolling summary
│           └── 1.json       # created on compaction
├── tasks/
│   └── queue.json           # pending/running/done/failed tasks
├── schedule.json            # daily wakeup times + oneshot timers
├── wake_log.json            # tracks which daily slots fired today
├── discord_threads.json     # discord thread_id → conversation_id
└── memory.json              # persistent key-value store
```

### Conversation Model
- **Conversation** = a folder, maps to one Discord thread or dashboard session
- **Version** = one compaction window inside a conversation
- When a version exceeds `COMPACTION_THRESHOLD` messages, older messages are summarized and a new version file is created
- Old versions are never deleted — full history always auditable

## Deployment (VPS)

- OS: Ubuntu
- Zipper runs directly via systemd as root: `systemctl --user start zipper`
- Service file: `zipper.service` (uses `%h` for home directory)
- Discord bot: `docker compose up -d`
- Logs: `journalctl --user -u zipper -n 50 --no-pager`
- Code lives at `/opt/zipper/app`

## Environment Variables

```
ANTHROPIC_API_KEY=
BRAVE_API_KEY=
DISCORD_TOKEN=
DISCORD_CHANNEL_ID=
ZIPPER_URL=http://localhost:4199
```

## Key Design Decisions

- Zipper runs on the host (not Docker) so `bash` tool has full VPS access
- Discord threads map 1:1 to conversations — replying in a thread continues the same conversation
- New Discord message in the main channel = new conversation + new thread
- Cron wakeups hit `/wake` endpoint with a `time` field; zipper builds the check-in prompt
- Oneshot timers in `schedule.json` are removed after firing
- `pop_last_message` rolls back conversation on API failure to prevent corruption
- Messages are validated before each API call to strip orphaned `tool_result` blocks

## Development

```bash
# start server locally
python main.py

# send a prompt
python run.py "your prompt here"

# resume a conversation
python run.py "follow up" <conversation_id>
```

## Known Limitations / Future Work

- No streaming responses yet (Discord gets full reply after completion)
- Self-repair not yet formally implemented (zipper can fix itself ad-hoc but no dedicated failure→repair pipeline)
- `zipper-device` repo (remote device agents) not yet built
- Dashboard (web UI) not yet built
- Git commits show as repo owner, not Zipper — set `user.email` to an unrecognized email to show name-only
