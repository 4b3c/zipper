# CLAUDE.md

This file provides guidance to Claude Code when working on the Zipper codebase.

## Project Overview

Zipper is a self-building, self-repairing AI assistant that runs 24/7 on a VPS. It receives tasks via Discord or a web dashboard, executes them autonomously using tools, and can modify its own source code, restart itself, and push to GitHub.

## Architecture

### Runtime
- **Zipper** runs as a persistent FastAPI server on port `4199` managed by systemd on the VPS (not Docker)
- **Discord bot** runs as a persistent systemd service (`zipper-discord`) on the VPS
- **Cron** hits the `/wake` endpoint at scheduled times defined in `data/schedule.json`

### Entry Points
- `main.py` — FastAPI server, exposes `/chat`, `/discord`, `/wake`, `/status`
- `bot/discord_bot.py` — thin relay: creates Discord threads, forwards messages to zipper's `/discord` endpoint, serves HTTP API (port 4200) for zipper to push responses back
- `run.py` — dev tool, POSTs to `/chat` from CLI
- `utils/setup_cron.py` — generates crontab entries from `data/schedule.json` (daily recurring + date-pinned oneshot entries). Run after any schedule change.
- `utils/restart_watcher.py` — spawned by `tools/restart.py` after a zipper restart; polls `/status`, resumes the conversation via `/chat`, handles rollback on crash

### LLM Loop (`llm/`)
- Calls Claude, parses tool calls, executes them, loops until `end_turn`
- Model routing by self-rating: Zipper appends `{{c:X, d:X, a:X}}` to each response; total score picks the next model (>11=Opus, >6=Sonnet, else Haiku)
- Interrupt system: each `run_task` call writes a `last_owner_token` to the conversation meta synchronously (before any await). Concurrent tasks check ownership at every yield point and exit silently if they lost it.
- Validates messages before each API call to prevent corrupted tool_result/tool_use pairs
- Rolls back last message on API failure to keep conversation clean
- Compacts conversation after `COMPACTION_THRESHOLD` messages

### Tools (`tools/`)
- `file` — list (recursive, project root default), read (single/multi-file, line ranges), write, edit (exact search/replace with uniqueness enforcement, `all=true` for bulk replace), delete, grep (regex across files, `glob` filter). Automatically ignores `.venv`, `__pycache__`, `.git`, `.env`. `data/` hidden by default in list/grep.
- `bash` — shell execution, 30s timeout
- `web` — Brave Search (`search` mode) and HTTP GET page fetch (`fetch` mode, HTML stripped via stdlib html.parser)
- `discord` — send (returns message_id), history (read recent messages), edit (update by message_id), react (emoji reaction)
- Tool onboarding: each tool prepends a usage guide on its first call per conversation (detected via trace log). `help=true` or empty primary field returns the guide without running the tool.

### Prompts (`prompts/`)
- `main.md` — Zipper's main system prompt. Injected on every conversation.
- `codebase.md` — key file roles and architecture notes, injected into the file tool's first-use onboarding. **Keep this current when adding new files or components.**
- `bash.md` — shell environment facts (shell, user, runtimes, services), injected into the bash tool's first-use onboarding. Update if the environment changes.
- `discord.md` — Discord tool usage guide, injected into the discord tool's first-use onboarding.

### Storage (`storage/`)
All data is JSON, no database.

```
data/
├── conversations/
│   └── {id}/
│       ├── meta.json        # title, source, discord_thread_id, last_owner_token, summary
│       ├── trace.json       # tool call log (tool, args, output, error, duration)
│       └── versions/
│           ├── 0.json       # messages + system_prompt + rolling summary
│           └── 1.json       # created on compaction
├── tasks/
│   └── queue.json           # pending/running/done/failed tasks
├── schedule.json            # daily wakeup times + oneshot timers
├── wake_log.json            # tracks which daily slots fired today
└── memory.json              # persistent key-value store
```

### Conversation Model
- **Conversation** = a folder, maps to one Discord thread or dashboard session
- **Version** = one compaction window inside a conversation
- When a version exceeds `COMPACTION_THRESHOLD` messages, older messages are summarized and a new version file is created
- Old versions are never deleted — full history always auditable

## Deployment (VPS)

- OS: Ubuntu
- Zipper runs directly via systemd: `systemctl start zipper`
- Discord bot runs directly via systemd: `systemctl start zipper-discord`
- Service files: `/etc/systemd/system/zipper.service`, `/etc/systemd/system/zipper-discord.service`, `/etc/systemd/system/zipper-dashboard.service`
- Logs: `journalctl -u zipper -n 50 --no-pager`
- Code lives at `/opt/zipper/app`

## Environment Variables

```
ANTHROPIC_API_KEY=
BRAVE_API_KEY=
DISCORD_TOKEN=
DISCORD_CHANNEL_ID=
```

## Key Design Decisions

- Zipper runs on the host (not Docker) so `bash` tool has full VPS access
- Discord threads map 1:1 to conversations — replying in a thread continues the same conversation. The mapping is stored in each conversation's `meta.json` (`discord_thread_id`); `find_conversation_by_thread()` scans meta files to look it up.
- New Discord message in the main channel = new conversation + new thread (discord_bot creates the thread, zipper creates the conversation)
- discord_bot is a thin relay: it only creates threads and forwards messages. All conversation logic lives in zipper. Zipper pushes responses back via `/send`.
- Interrupt system: new message to an active conversation writes a new `last_owner_token` to meta. The displaced task sees the mismatch at its next yield and exits silently.
- Cron wakeups hit `/wake` endpoint with a `time` field; zipper builds the check-in prompt and is instructed to send a Discord summary itself when done
- Oneshot timers in `schedule.json` are removed after firing; their cron entries are cleaned up next time `utils/setup_cron.py` runs
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
- Git commits show as repo owner, not Zipper — set `user.email` to an unrecognized email to show name-only
