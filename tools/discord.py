import json
import urllib.request

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


def run(args: dict) -> str:
    mode = args.get("mode")

    if mode == "send":
        message = args.get("message", "").strip()
        if not message:
            return "error: message is required"
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
