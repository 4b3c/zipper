from tools.file import run as file_run
from tools.bash import run as bash_run
from tools.search import run as search_run

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


def execute_tool(name: str, args: dict) -> str:
    if name == "file":
        return file_run(args)
    if name == "bash":
        return bash_run(args)
    if name == "search":
        return search_run(args)
    raise ValueError(f"Unknown tool: {name}")
