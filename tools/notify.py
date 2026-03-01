import os
import json
import urllib.request

BOT_URL = os.environ.get("BOT_URL", "http://127.0.0.1:4200")


def run(args: dict) -> str:
    message = args.get("message", "").strip()
    if not message:
        return "error: message required"

    body = {"message": message}
    thread_id = args.get("thread_id")
    if thread_id:
        body["thread_id"] = thread_id

    payload = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{BOT_URL}/notify",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            r.read()
        return "ok"
    except Exception as e:
        return f"error: {e}"
