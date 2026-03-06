import copy
from pathlib import Path
from tools.file import run as file_run, _list_tree, ROOT, DEFAULT_HIDDEN_DIRS
from tools.bash import run as bash_run
from tools.web import run as web_run
from tools.restart import run as restart_run
from tools.task import run as task_run
from tools.discord import run as discord_run
from tools.memory import run as memory_run
from tools.search_tools import run as search_tools_run
from tools.summarize import run as summarize_run
from tools.todo import run as todo_run
from storage.trace import get_trace

import tools.file as _file_tool
import tools.bash as _bash_tool
import tools.web as _web_tool
import tools.discord as _discord_tool
import tools.restart as _restart_tool
import tools.task as _task_tool
import tools.memory as _memory_tool
import tools.search_tools as _search_tools_tool
import tools.summarize as _summarize_tool
import tools.todo as _todo_tool

_CODEBASE_MD = ROOT / "prompts" / "codebase.md"
_BASH_MD = ROOT / "prompts" / "bash.md"
_DISCORD_MD = ROOT / "prompts" / "discord.md"
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
        tree = _list_tree(ROOT, ROOT, DEFAULT_HIDDEN_DIRS)
        parts.append("## Current File Tree")
        parts.append("\n".join(tree))
        parts.append("")

    parts.append(_FILE_TOOL_USAGE)
    return "\n".join(parts)


ONBOARDING = {
    "file": _file_onboarding,

    "bash": lambda _: "[first use — bash tool guide]\n\n" + (
        _BASH_MD.read_text(encoding="utf-8").strip() if _BASH_MD.exists()
        else "See prompts/bash.md (not found)."
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
- zipper — async restart of the main process via systemctl. Spawns a watcher that resumes this conversation once zipper is healthy. If startup fails, code changes are stashed and the previous state is restored automatically.
- discord — synchronous restart of the zipper-discord service. Returns when done.

Workflow after a code change: edit files → test with bash if possible → restart(zipper) → verify the watchdog result → push to GitHub.
""".strip(),

    "discord": lambda _: "[first use — discord tool guide]\n\n" + (
        _DISCORD_MD.read_text(encoding="utf-8").strip() if _DISCORD_MD.exists()
        else "See prompts/discord.md (not found)."
    ),

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

    "memory": """
[first use — memory tool guide]
Persistent key/value store that survives restarts, plus quick context snapshots.

Modes:
- list — show all stored keys and values.
- get — retrieve a single value by key.
- set — store a value (any type) under a key.
- delete — remove a key.
- recent_conversations — one-sentence summary of each of the last 5 non-test conversations.
- recent_logs — last 30 lines of the zipper systemd service log.

Use memory to persist facts, preferences, or state across conversations. Use recent_conversations and recent_logs to quickly orient yourself when starting a new session.
""".strip(),

    "search_tools": """
[first use — search_tools guide]
Look up full parameter documentation for any tool by name or keyword.
Tool schemas sent to the API are intentionally slim — call this whenever you need to know exact parameters, modes, or usage rules.

Usage: search_tools(query="file") or search_tools(query="edit") or search_tools(query="discord send")
""".strip(),

    "summarize": """
[first use — summarize guide]
Condense a long text using a fast model (Haiku). Useful for reducing large tool outputs or file contents before reasoning over them.

Parameters:
- text (required) — the text to summarize.
- direction (optional) — a focus, e.g. "how errors are handled". Preserves detail relevant to that focus, lossy elsewhere.

Example: summarize(text=file_contents, direction="what API endpoints are defined")
""".strip(),

    "todo": """
[first use — todo tool guide]
Manages the user's todo list and schedules direct Discord notifications.

Modes:
- add — add a new item. Required: title. Optional: description, category, priority, subtasks (list of strings), due_at, thread_id, reminder_message, task_id.
- list — all todos, filter by status (pending/in_progress/done/cancelled) or category.
- update — patch any field on a todo. id required. Use subtask_done=N (0-based index) to check off a subtask.
- schedule_notification — schedule a Discord message for a future time without waking Zipper. Required: message, at (ISO 8601). Optional: thread_id.

Categories:
- zipper_now — Zipper is handling this immediately. Add as in_progress or done.
- zipper_scheduled — Zipper will handle it later; link via task_id to the task queue entry.
- remind_user — user needs a reminder; set due_at and Zipper auto-schedules a notification.
- user_todo — backlog item for the user, no action needed.

When classifying a new todo:
1. Can Zipper do it right now? → category=zipper_now, do the work, mark done.
2. Can Zipper do it at a specific later time? → category=zipper_scheduled, create a task entry too.
3. Does the user need a reminder? → category=remind_user, set due_at (notification fires automatically).
4. Just tracking it? → category=user_todo.

For large tasks, break into subtasks= list of step titles.
""".strip(),
}

_CODE_EXEC = "code_execution_20260120"


def _slim(schema: dict) -> dict:
    """Strip parameter descriptions from a schema to reduce context tokens.
    Keeps type, enum, items, and required — Claude uses search_tools for full docs."""
    s = copy.deepcopy(schema)
    for prop in s["input_schema"]["properties"].values():
        prop.pop("description", None)
    return s


TOOLS = [
    {**_slim(_file_tool.SCHEMA),         "allowed_callers": [_CODE_EXEC]},
    {**_slim(_web_tool.SCHEMA),          "allowed_callers": [_CODE_EXEC]},
    {**_slim(_discord_tool.SCHEMA),      "allowed_callers": ["direct", _CODE_EXEC]},
    {**_slim(_restart_tool.SCHEMA),      "allowed_callers": ["direct"]},
    {**_slim(_task_tool.SCHEMA),         "allowed_callers": [_CODE_EXEC]},
    {**_slim(_bash_tool.SCHEMA),         "allowed_callers": [_CODE_EXEC]},
    {**_slim(_memory_tool.SCHEMA),       "allowed_callers": [_CODE_EXEC]},
    {**_slim(_summarize_tool.SCHEMA),    "allowed_callers": [_CODE_EXEC]},
    {**_slim(_todo_tool.SCHEMA),          "allowed_callers": [_CODE_EXEC]},
    {**_search_tools_tool.SCHEMA,        "allowed_callers": ["direct"]},
    {"type": _CODE_EXEC, "name": "code_execution"},
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
    "discord": lambda a: False,  # mode is always required; use help=true
    "search_tools": lambda a: not a.get("query", "").strip(),
    "summarize": lambda a: not a.get("text", "").strip(),
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
    elif name == "discord":
        result = discord_run(args)
    elif name == "memory":
        result = memory_run(args)
    elif name == "search_tools":
        result = search_tools_run(args)
    elif name == "summarize":
        result = summarize_run(args)
    elif name == "todo":
        result = todo_run(args)
    else:
        raise ValueError(f"Unknown tool: {name}")

    if first_use and name in ONBOARDING:
        result = _get_onboarding(name, args) + "\n\n---\n\n" + result

    return result
