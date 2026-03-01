import os
import json
import urllib.request


def run(args: dict) -> str:
    message = args.get("message", "").strip()
    if not message:
        return "error: message required"

    token = os.environ.get("DISCORD_TOKEN")
    channel_id = os.environ.get("DISCORD_CHANNEL_ID")
    if not token or not channel_id:
        return "error: DISCORD_TOKEN or DISCORD_CHANNEL_ID not set"

    payload = json.dumps({"content": message}).encode()
    req = urllib.request.Request(
        f"https://discord.com/api/v10/channels/{channel_id}/messages",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bot {token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            r.read()
        return "ok"
    except Exception as e:
        return f"error: {e}"
