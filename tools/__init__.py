from pathlib import Path
from tools.file import run as file_run, _list_tree, PROJECT_ROOT, DEFAULT_HIDDEN_DIRS
from tools.bash import run as bash_run
from tools.web import run as web_run
from tools.restart import run as restart_run
from tools.task import run as task_run
from tools.notify import run as notify_run
from storage.trace import get_trace

_CODEBASE_MD = PROJECT_ROOT / "system_prompts" / "codebase.md"
_BASH_MD = PROJECT_ROOT / "system_prompts" / "bash.md"
_FILE_TOOL_USAGE = """
## File Tool Modes
- list — recursive tree (project root default). Hidden entries shown as stubs. Pass include_data=true to expand data/.
- read — single file or filenames=[] for multi-read. line_start/line_end for ranges.
- grep — regex across files. glob= to filter by extension (e.g. "*.py").
- edit — exact search/replace. Errors on 0 or 2+ matches. all=true for bulk. Shows 3-line context on success.
- write — full overwrite. Prefer edit for targeted changes.
- delete — removes a single file.

Rules: always read before editing. Use grep to locate symbols before diving into files. After source changes, use the restart tool — never bash.
""".strip()


def _file_onboarding(args: dict) -> str:
    parts = ["[first use — file tool + codebase orientation]", ""]

    codebase = _CODEBASE_MD.read_text(encoding="utf-8").strip() if _CODEBASE_MD.exists() else ""
    if codebase:
        parts.append(codebase)
        parts.append("")

    # skip tree if this call is already listing files — output would be identical
    if args.get("mode") != "list":
        tree = _list_tree(PROJECT_ROOT, PROJECT_ROOT, DEFAULT_HIDDEN_DIRS)
        parts.append("## Current File Tree")
        parts.append("\n".join(tree))
        parts.append("")

    parts.append(_FILE_TOOL_USAGE)
    return "\n".join(parts)


ONBOARDING = {
    "file": _file_onboarding,

    "bash": lambda _: "[first use — bash tool guide]\n\n" + (
        _BASH_MD.read_text(encoding="utf-8").strip() if _BASH_MD.exists()
        else "See system_prompts/bash.md (not found)."
    ),

    "web": """
[first use — web tool guide]
Two modes:
- search — queries Brave Search, returns title/URL/description per result. Use for finding docs, articles, or anything requiring current information. Pass limit=N for more results (default 5).
- fetch — HTTP GET a URL, returns page text with HTML stripped. Use to read the actual content of a page found via search, or any public URL. Truncates at 20k chars.

Workflow: search to find relevant URLs, then fetch to read the content.
""".strip(),

    "restart": """
[first use — restart tool guide]
Restarts zipper service components. Always use this after modifying source code — never use bash to restart manually.

Modes:
- zipper — async restart of the main process via systemctl. Registers a watchdog with the discord bot that resumes this conversation once zipper is healthy. If startup fails, code changes are stashed and the previous state is restored automatically.
- discord — synchronous restart of the zipper-discord service. Returns when done.

Workflow after a code change: edit files → test with bash if possible → restart(zipper) → verify the watchdog result → push to GitHub.
""".strip(),

    "task": """
[first use — task tool guide]
Manages the persistent task queue across sessions. Use it to track work that spans multiple conversations.

Modes:
- list — all tasks, filter by status (pending/running/done/failed).
- create — requires title. Optional: description (full instructions), due_at (ISO 8601), schedule (recurrence string).
- update — patch any field on a task. Always pass result when marking done, error when marking failed.
- due — tasks that are pending and past their due_at.
- archive — completed/failed tasks, most recent first. Check this before starting a recurring task to see what you did last time.

Recurrence: "daily", "weekly", "every N hours", "every N days", "every monday" (any weekday). Marking a scheduled task done automatically creates the next occurrence.
""".strip(),
}

TOOLS = [
    {
        "name": "file",
        "description": "Read, write, edit, list, or grep files on the filesystem. Ignores .venv, __pycache__, .git, .env automatically.",
        "input_schema": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["list", "read", "write", "edit", "delete", "grep"],
                    "description": (
                        "list — recursive file tree (defaults to project root, hides data/ by default). "
                        "read — read one or multiple files, optionally a line range. "
                        "write — write full file content. "
                        "edit — exact search/replace (errors on 0 or 2+ matches). "
                        "delete — delete a single file. "
                        "grep — search files using a regex pattern."
                    ),
                },
                "directory": {
                    "type": "string",
                    "description": "Target directory. Defaults to project root if omitted.",
                },
                "filename": {
                    "type": "string",
                    "description": "Filename. Required for read (single), write, edit.",
                },
                "filenames": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of filenames to read in one call. Use instead of filename for multi-file reads.",
                },
                "content": {
                    "type": "string",
                    "description": "File content. Required for write.",
                },
                "search": {
                    "type": "string",
                    "description": "Exact string to find. Required for edit.",
                },
                "replace": {
                    "type": "string",
                    "description": "String to replace with. Required for edit.",
                },
                "pattern": {
                    "type": "string",
                    "description": "Regex pattern to search for. Required for grep. Use re syntax (e.g. 'def \\w+', 'import.*os').",
                },
                "glob": {
                    "type": "string",
                    "description": "Filename glob filter for grep (e.g. '*.py'). Defaults to all files.",
                },
                "line_start": {
                    "type": "integer",
                    "description": "First line to return (1-indexed). For read mode.",
                },
                "line_end": {
                    "type": "integer",
                    "description": "Last line to return (inclusive). For read mode.",
                },
                "include_data": {
                    "type": "boolean",
                    "description": "Include the data/ directory in list/grep. Default false.",
                },
                "all": {
                    "type": "boolean",
                    "description": "For edit: replace all occurrences instead of erroring on multiple matches. Default false.",
                },
                "help": {
                    "type": "boolean",
                    "description": "Return usage guide for this tool without performing any action.",
                },
            },
            "required": ["mode"],
        },
    },
    {
        "name": "web",
        "description": "Search the web or fetch a URL. search mode queries Brave Search; fetch mode HTTP GETs a URL and returns the page text.",
        "input_schema": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["search", "fetch"],
                    "description": "search — Brave web search. fetch — HTTP GET a URL, returns page text with HTML stripped.",
                },
                "query": {
                    "type": "string",
                    "description": "Search query. Required for search mode.",
                },
                "url": {
                    "type": "string",
                    "description": "URL to fetch. Required for fetch mode.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Number of search results to return. Default 5. Search mode only.",
                },
                "help": {
                    "type": "boolean",
                    "description": "Return usage guide for this tool without performing any action.",
                },
            },
            "required": ["mode"],
        },
    },
    {
        "name": "notify",
        "description": "Send a message to Discord. Defaults to the main channel; pass thread_id to post in a specific conversation thread.",
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "Message to send. Markdown is supported.",
                },
                "thread_id": {
                    "type": "integer",
                    "description": "Discord thread ID to post in. Omit to post to the main channel.",
                },
                "help": {
                    "type": "boolean",
                    "description": "Return usage guide for this tool without performing any action.",
                },
            },
            "required": ["message"],
        },
    },
    {
        "name": "restart",
        "description": "Restart a zipper service component.",
        "input_schema": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["zipper", "discord", "dashboard"],
                    "description": (
                        "zipper — restart the main zipper process via systemctl. "
                        "Spawns a watcher that resumes this conversation with the result. "
                        "If startup fails, code changes are stashed and previous state is restored. "
                        "discord — restart the zipper-discord systemd user service synchronously. "
                        "dashboard — restart the dashboard (not yet implemented)."
                    ),
                },
                "help": {
                    "type": "boolean",
                    "description": "Return usage guide for this tool without performing any action.",
                },
            },
            "required": ["mode"],
        },
    },
    {
        "name": "task",
        "description": "Manage the task queue. Use this to create, list, update, and complete tasks.",
        "input_schema": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["list", "create", "update", "due", "archive"],
                    "description": (
                        "list — all tasks, optionally filtered by status. "
                        "create — add a new task. "
                        "update — patch any fields on an existing task (title, description, due_at, schedule, status, result, error). Only id is required. "
                        "due — tasks that are due now (pending and past due_at). "
                        "archive — completed/failed tasks, most recent first."
                    ),
                },
                "status": {
                    "type": "string",
                    "enum": ["pending", "running", "done", "failed"],
                    "description": "Filter for list mode, or new status for update mode.",
                },
                "id": {
                    "type": "string",
                    "description": "Task ID. Required for update.",
                },
                "title": {
                    "type": "string",
                    "description": "Short task title. Required for create. Used as the task ID slug.",
                },
                "description": {
                    "type": "string",
                    "description": "Full task details. Optional for create — defaults to title if omitted.",
                },
                "due_at": {
                    "type": "string",
                    "description": "ISO 8601 datetime when the task is due. Defaults to now.",
                },
                "schedule": {
                    "type": "string",
                    "description": "Optional human-readable recurrence note (e.g. 'every monday').",
                },
                "result": {
                    "type": "string",
                    "description": "Result summary. Optional for update.",
                },
                "error": {
                    "type": "string",
                    "description": "Error message. Optional for update when marking failed.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max entries to return for archive mode. Default 20.",
                },
                "help": {
                    "type": "boolean",
                    "description": "Return usage guide for this tool without performing any action.",
                },
            },
            "required": ["mode"],
        },
    },
    {
        "name": "bash",
        "description": "Execute a shell command. Returns stdout and stderr.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to run.",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds. Default 30.",
                },
                "help": {
                    "type": "boolean",
                    "description": "Return usage guide for this tool without performing any action.",
                },
            },
            "required": ["command"],
        },
    },
]


def _is_first_use(name: str, conversation_id: str) -> bool:
    if not conversation_id:
        return False
    trace = get_trace(conversation_id)
    return not any(e.get("tool") == name for e in trace.get("entries", []))


# empty primary field triggers help, same as calling with no args in a CLI
_EMPTY_TRIGGERS = {
    "bash": lambda a: not a.get("command", "").strip(),
    "web": lambda a: not a.get("query", "").strip() and not a.get("url", "").strip(),
    "notify": lambda a: not a.get("message", "").strip(),
}


def _wants_help(name: str, args: dict) -> bool:
    if args.get("help"):
        return True
    return _EMPTY_TRIGGERS.get(name, lambda a: False)(args)


def _get_onboarding(name: str, args: dict) -> str:
    entry = ONBOARDING.get(name)
    if entry is None:
        return f"No guide available for '{name}'."
    return entry(args) if callable(entry) else entry


def execute_tool(name: str, args: dict, conversation_id: str = "") -> str:
    if _wants_help(name, args):
        return _get_onboarding(name, args)

    first_use = _is_first_use(name, conversation_id)

    if name == "file":
        result = file_run(args)
    elif name == "bash":
        result = bash_run(args)
    elif name == "web":
        result = web_run(args)
    elif name == "restart":
        result = restart_run(args, conversation_id)
    elif name == "task":
        result = task_run(args)
    elif name == "notify":
        result = notify_run(args)
    else:
        raise ValueError(f"Unknown tool: {name}")

    if first_use and name in ONBOARDING:
        result = _get_onboarding(name, args) + "\n\n---\n\n" + result

    return result
