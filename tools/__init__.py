from tools.file import run as file_run
from tools.bash import run as bash_run
from tools.search import run as search_run
from tools.restart import run as restart_run

TOOLS = [
    {
        "name": "file",
        "description": "Read, write, edit, or list files on the filesystem.",
        "input_schema": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["list", "read", "write", "edit"],
                    "description": "Operation to perform.",
                },
                "directory": {
                    "type": "string",
                    "description": "Target directory path.",
                },
                "filename": {
                    "type": "string",
                    "description": "Filename. Required for read, write, edit.",
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
            },
            "required": ["mode", "directory"],
        },
    },
    {
        "name": "search",
        "description": "Search the web using Brave Search. Returns titles, URLs, and descriptions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Number of results to return. Default 5.",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "restart",
        "description": (
            "Restart zipper to test code changes. "
            "Spawns a watcher that monitors whether the process comes back up, "
            "then resumes this conversation with the result. "
            "If the restart fails (crash/syntax error), code changes are automatically "
            "stashed with git stash and the previous working state is restored before "
            "resuming with an error message. "
            "Call this after making code changes you want to test."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
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
            },
            "required": ["command"],
        },
    },
]


def execute_tool(name: str, args: dict, conversation_id: str = "") -> str:
    if name == "file":
        return file_run(args)
    if name == "bash":
        return bash_run(args)
    if name == "search":
        return search_run(args)
    if name == "restart":
        return restart_run(args, conversation_id)
    raise ValueError(f"Unknown tool: {name}")
