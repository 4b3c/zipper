from pathlib import Path
import requests

from utils.constants import BOT_URL
from utils.http_utils import post_json


def _post_multipart(endpoint: str, files: dict, data: dict = None) -> dict:
    """Send multipart/form-data request (for file uploads) using requests library."""
    try:
        resp = requests.post(f"{BOT_URL}/{endpoint}", files=files, data=data, timeout=60)
        if resp.status_code == 200:
            return resp.json()
        else:
            return {"error": f"HTTP {resp.status_code}: {resp.text}"}
    except Exception as e:
        return {"error": str(e)}


def run(args: dict) -> str:
    mode = args.get("mode")

    if mode == "send":
        message = args.get("message", "").strip()
        file_path = args.get("file", "").strip()

        if not message and not file_path:
            return "error: message or file is required"

        if file_path:
            file_content = Path(file_path).read_bytes()
            files = {"file": (Path(file_path).name, file_content)}
            data = {"message": message}
            if args.get("thread_id"):
                data["thread_id"] = args["thread_id"]
            resp = _post_multipart("send", files, data)
        else:
            payload = {"message": message}
            if args.get("thread_id"):
                payload["thread_id"] = args["thread_id"]
            resp = post_json(f"{BOT_URL}/send", payload)

        if "error" in resp:
            return f"error: {resp['error']}"
        return f"ok: sent (message_id: {resp.get('message_id')})"

    if mode == "history":
        payload = {"limit": args.get("limit", 5)}
        if args.get("thread_id"):
            payload["thread_id"] = args["thread_id"]
        resp = post_json(f"{BOT_URL}/history", payload)
        if "error" in resp:
            return f"error: {resp['error']}"
        messages = resp.get("messages", [])
        if not messages:
            return "no messages found"
        lines = []
        for m in messages:
            ts = m["timestamp"][:16].replace("T", " ")
            line = f"[{ts}] {m['author']}: {m['content']}"
            if m.get("thread_id"):
                line += f"  [started thread: {m['thread_id']}]"
            lines.append(line)
        return "\n".join(lines)

    if mode == "edit":
        message_id = args.get("message_id")
        content = args.get("content", "").strip()
        if not message_id:
            return "error: message_id is required"
        if not content:
            return "error: content is required"
        payload = {"message_id": message_id, "content": content}
        if args.get("thread_id"):
            payload["thread_id"] = args["thread_id"]
        resp = post_json(f"{BOT_URL}/edit", payload)
        if "error" in resp:
            return f"error: {resp['error']}"
        return "ok: message edited"

    if mode == "react":
        message_id = args.get("message_id")
        emoji = args.get("emoji", "").strip()
        if not message_id:
            return "error: message_id is required"
        if not emoji:
            return "error: emoji is required"
        payload = {"message_id": message_id, "emoji": emoji}
        if args.get("thread_id"):
            payload["thread_id"] = args["thread_id"]
        resp = post_json(f"{BOT_URL}/react", payload)
        if "error" in resp:
            return f"error: {resp['error']}"
        return f"ok: reacted with {emoji}"

    return f"error: unknown mode: {mode}"


SCHEMA = {
    "name": "discord",
    "description": "Interact with Discord. Send messages, read history, edit messages, or add reactions.",
    "input_schema": {
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "enum": ["send", "history", "edit", "react"],
                "description": (
                    "send — post a message, returns message_id for use with edit/react. "
                    "history — fetch recent messages from a channel or thread. "
                    "edit — edit a previously sent message by message_id. "
                    "react — add an emoji reaction to a message by message_id."
                ),
            },
            "message": {
                "type": "string",
                "description": "Message content. Required for send (unless file is provided).",
            },
            "file": {
                "type": "string",
                "description": "Path to file to upload. Optional for send. Can be used with or without message.",
            },
            "content": {
                "type": "string",
                "description": "Replacement content. Required for edit.",
            },
            "emoji": {
                "type": "string",
                "description": "Unicode emoji to react with (e.g. '✅', '👍', '🎉'). Standard emoji only — no custom server emoji. Required for react.",
            },
            "message_id": {
                "type": "string",
                "description": "ID of the message to edit or react to.",
            },
            "thread_id": {
                "type": "integer",
                "description": "Discord thread ID. Omit to target the main channel.",
            },
            "limit": {
                "type": "integer",
                "description": "Number of messages to return. Default 5, max 100. history mode only.",
            },
            "help": {
                "type": "boolean",
                "description": "Return usage guide for this tool without performing any action.",
            },
        },
        "required": ["mode"],
    },
}
