"""Tool search — look up full parameter docs for any tool by name or keyword."""

FULL_DOCS = {
    "file": """\
## file
Read, write, edit, list, delete, and search files. Ignores .venv, __pycache__, .git, .env.

**Modes:**
- `list` — recursive file tree from project root (or `directory`). Hides `data/` by default; pass `include_data=true` to show it.
- `read` — read a file (`filename`) or multiple files (`filenames: [...]`). Optional `line_start`/`line_end` for line ranges.
- `write` — overwrite a file completely (`filename`, `content`). Prefer `edit` for targeted changes.
- `edit` — exact search/replace (`filename`, `search`, `replace`). Errors if 0 or 2+ matches. Pass `all=true` to replace all occurrences.
- `delete` — delete a single file (`filename`).
- `grep` — regex search across files (`pattern`). Optional `glob` (e.g. `"*.py"`) and `directory`. Returns file:line matches.

**Rules:** always read before editing. Use grep to locate symbols first. After source changes use the restart tool, not bash.
""",

    "bash": """\
## bash
Run any shell command. Returns stdout + stderr combined.

**Parameters:**
- `command` (required) — the shell command to run.
- `timeout` (optional) — seconds before kill, default 30. Pass higher for long operations.

**Rules:**
- No interactive sessions (vim, top, python REPL, ssh).
- Long-running commands: `nohup cmd > /tmp/out.log 2>&1 &` then poll with `tail /tmp/out.log`.
- For reading or editing source files, prefer the `file` tool.
- Use full path `/opt/zipper/app/.venv/bin/python`, not bare `python`.
""",

    "web": """\
## web
Search the web or fetch a URL.

**Modes:**
- `search` — Brave web search. Pass `query` (required) and optional `limit` (default 5). Returns title, URL, description per result.
- `fetch` — HTTP GET a URL. Pass `url` (required). Returns page text with HTML stripped, truncated at 20k chars.

**Workflow:** use `search` to find relevant URLs, then `fetch` to read actual content.
""",

    "discord": """\
## discord
Interact with Discord.

**Modes:**
- `send` — post a message. `message` (text) and/or `file` (path to upload). Returns `message_id`. Optional `thread_id` to target a thread vs. main channel.
- `history` — fetch recent messages. Optional `thread_id`, `limit` (default 5, max 100).
- `edit` — edit a sent message. Requires `message_id` and `content` (new text). Optional `thread_id`.
- `react` — add emoji reaction. Requires `message_id` and `emoji` (unicode, e.g. "✅"). Optional `thread_id`.

**Tip:** to react to a thread message: call `history` with `thread_id` to get `message_id`, then call `react` with both `message_id` and `thread_id`.
""",

    "restart": """\
## restart
Restart a zipper service component. Always use this after modifying source code — never use bash to restart manually.

**Modes:**
- `zipper` — async restart of the main process. Spawns a watcher that resumes this conversation once zipper is healthy. If startup fails, changes are stashed and previous state is restored.
- `discord` — synchronous restart of `zipper-discord`. Returns when done.
- `dashboard` — restart the dashboard (not yet implemented).

**Workflow after a code change:** edit files → test with bash if possible → `restart(zipper)` → verify watchdog result → push to GitHub.
""",

    "task": """\
## task
Manage the persistent task queue. Tasks survive restarts.

**Modes:**
- `list` — all tasks, optional `status` filter (pending/running/done/failed).
- `create` — add a task. Requires `title`. Optional: `description`, `due_at` (ISO 8601), `schedule` (e.g. "daily", "every monday").
- `update` — patch a task. Requires `id`. Can update: `title`, `description`, `status`, `due_at`, `schedule`, `result`, `error`.
- `due` — tasks that are pending and past their `due_at`.
- `archive` — completed/failed tasks, most recent first. Optional `limit` (default 20).

**Convention:** always pass `result` when marking `done`, `error` when marking `failed`. Scheduled tasks auto-create the next occurrence when marked done.
""",

    "memory": """\
## memory
Persistent key/value store that survives restarts, plus quick context snapshots.

**Modes:**
- `list` — show all stored keys and values.
- `get` — retrieve a single value by `key`.
- `set` — store any JSON value under `key` + `value`.
- `delete` — remove a key.
- `recent_conversations` — one-sentence summary of each of the last 5 conversations.
- `recent_logs` — last 30 lines of the zipper systemd service log.

Use memory to persist facts, preferences, or state that needs to survive across conversations.
""",

    "summarize": """\
## summarize
Condense a long text using a fast model (Haiku).

**Parameters:**
- `text` (required) — the text to summarize.
- `direction` (optional) — a focus for the summary, e.g. "how errors are handled" or "what API endpoints are defined". If omitted, produces a general summary. With a direction, the summary preserves detail relevant to that focus and is lossy elsewhere.

**Use cases:** summarize a large file before reasoning about it, distill web page content, reduce tool output before passing it forward.
""",

    "search_tools": """\
## search_tools
Look up full documentation for any tool by name or keyword.

**Parameters:**
- `query` (required) — tool name (e.g. "file") or keyword (e.g. "edit", "react", "schedule").

Returns full parameter docs and usage notes for all matching tools.
Use this when you need to know the exact parameters for a tool before calling it.
""",
}


SCHEMA = {
    "name": "search_tools",
    "description": "Look up full documentation for any tool by name or keyword.",
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Tool name or keyword to search for (e.g. 'file', 'edit', 'discord send').",
            }
        },
        "required": ["query"],
    },
}


def run(args: dict) -> str:
    query = args.get("query", "").strip().lower()
    if not query:
        return "Available tools: " + ", ".join(FULL_DOCS.keys())

    # Exact tool name match — return immediately
    if query in FULL_DOCS:
        return FULL_DOCS[query]

    # Keyword search — deduplicate by insertion order
    seen: set[str] = set()
    matches = []
    for name, doc in FULL_DOCS.items():
        if name not in seen and (query in name or query in doc.lower()):
            seen.add(name)
            matches.append(doc)

    if not matches:
        return (
            f"No tools found matching '{query}'.\n"
            f"Available tools: {', '.join(FULL_DOCS.keys())}"
        )

    return "\n\n---\n\n".join(matches)
