"""Memory tool — persistent key/value store + conversation and log summaries."""

import subprocess
from storage import memory as _mem
from storage.conversations import list_conversations, get_latest_version


def _summarize_conversation(meta: dict) -> str:
    """One-sentence summary of a conversation from its title, source, and last message."""
    cid = meta.get("id", "")
    title = meta.get("title", "untitled")
    source = meta.get("source", "")
    created = meta.get("created_at", "")[:10]

    last_text = ""
    try:
        version = get_latest_version(cid)
        messages = version.get("messages", [])
        # Walk backwards to find last assistant text block
        for msg in reversed(messages):
            if msg.get("role") == "assistant":
                content = msg.get("content", [])
                for block in reversed(content) if isinstance(content, list) else []:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = block["text"].strip()
                        if text:
                            last_text = text[:120]
                            break
                if last_text:
                    break
    except Exception:
        pass

    suffix = f' — "{last_text}"' if last_text else ""
    return f"[{created}] {title} (via {source}){suffix}"


def run(args: dict) -> str:
    mode = args.get("mode", "list")

    # --- key/value modes ---

    if mode == "get":
        key = args.get("key", "").strip()
        if not key:
            return "error: key required"
        entry = _mem.get(key)
        if entry is None:
            return f"no entry for key: {key!r}"
        return f"{key}: {entry['value']}  (updated {entry['updated_at'][:10]})"

    if mode == "set":
        key = args.get("key", "").strip()
        value = args.get("value")
        if not key:
            return "error: key required"
        if value is None:
            return "error: value required"
        _mem.set(key, value)
        return f"ok: set {key!r}"

    if mode == "delete":
        key = args.get("key", "").strip()
        if not key:
            return "error: key required"
        _mem.delete(key)
        return f"ok: deleted {key!r}"

    if mode == "list":
        data = _mem.all()
        if not data:
            return "memory is empty"
        lines = []
        for k, entry in sorted(data.items()):
            val = str(entry["value"])
            snippet = val[:80] + ("…" if len(val) > 80 else "")
            lines.append(f"{k}: {snippet}  (updated {entry['updated_at'][:10]})")
        return "\n".join(lines)

    # --- context modes ---

    if mode == "recent_conversations":
        convs = list_conversations()
        # Sort by created_at descending, skip test conversations
        convs = [c for c in convs if c.get("source") != "test"]
        convs.sort(key=lambda c: c.get("created_at", ""), reverse=True)
        recent = convs[:5]
        if not recent:
            return "no conversations found"
        return "\n".join(_summarize_conversation(c) for c in recent)

    if mode == "recent_logs":
        try:
            result = subprocess.run(
                ["/usr/bin/journalctl", "-u", "zipper", "-n", "30", "--no-pager", "-o", "short"],
                capture_output=True, text=True, timeout=10,
            )
            lines = (result.stdout or "").strip()
            if not lines:
                return "no log output"
            return lines
        except Exception as e:
            return f"error reading logs: {e}"

    return f"error: unknown mode: {mode!r}"


SCHEMA = {
    "name": "memory",
    "description": (
        "Persistent key/value memory store, plus quick context summaries. "
        "Use list to see all stored values. Use set/get/delete for individual keys. "
        "Use recent_conversations for a one-line summary of the last 5 conversations. "
        "Use recent_logs for the last 30 lines of the zipper service log."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "enum": ["get", "set", "delete", "list", "recent_conversations", "recent_logs"],
                "description": (
                    "get — retrieve a value by key. "
                    "set — store a value by key. "
                    "delete — remove a key. "
                    "list — show all stored keys and values. "
                    "recent_conversations — one-sentence summary of each of the last 5 conversations. "
                    "recent_logs — last 30 lines of the zipper systemd service log."
                ),
            },
            "key": {
                "type": "string",
                "description": "Key name. Required for get, set, delete.",
            },
            "value": {
                "description": "Value to store. Required for set. Can be any JSON type.",
            },
        },
        "required": ["mode"],
    },
}
