import json
import urllib.request
from pathlib import Path
import requests

BOT_URL = "http://127.0.0.1:4200"


def _post(endpoint: str, payload: dict) -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{BOT_URL}/{endpoint}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except urllib.request.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace").strip()
        return {"error": f"HTTP {e.code}: {body}"}
    except Exception as e:
        return {"error": str(e)}


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
            resp = _post("send", payload)
        
        if "error" in resp:
            return f"error: {resp['error']}"
        return f"ok: sent (message_id: {resp.get('message_id')})"

    if mode == "history":
        payload = {"limit": args.get("limit", 5)}
        if args.get("thread_id"):
            payload["thread_id"] = args["thread_id"]
        resp = _post("history", payload)
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
        resp = _post("edit", payload)
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
        resp = _post("react", payload)
        if "error" in resp:
            return f"error: {resp['error']}"
        return f"ok: reacted with {emoji}"

    return f"error: unknown mode: {mode}"
