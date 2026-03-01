"""
Detached watcher script spawned by the restart tool.
Survives the zipper restart, monitors recovery, and resumes the conversation.

Usage: python restart_watcher.py <conversation_id> <project_dir>
"""

import os
import sys
import time
import urllib.request
import json
import subprocess
from pathlib import Path
from utils import notify_discord


def load_env(project_dir: str):
    env_path = Path(project_dir) / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())

STATUS_URL = "http://localhost:4199/status"
CHAT_URL = "http://localhost:4199/chat"
POLL_INTERVAL = 2       # seconds between health checks
STARTUP_TIMEOUT = 45    # seconds to wait for zipper to come back up
SETTLE_DELAY = 3        # seconds after detecting it's up before posting


def is_up() -> bool:
    try:
        with urllib.request.urlopen(STATUS_URL, timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def get_discord_thread_id(conversation_id: str, project_dir: str) -> int | None:
    meta_path = Path(project_dir) / "data" / "conversations" / conversation_id / "meta.json"
    if not meta_path.exists():
        return None
    try:
        return json.loads(meta_path.read_text()).get("discord_thread_id")
    except Exception:
        return None


def post_resume(conversation_id: str, message: str) -> str:
    payload = json.dumps({
        "prompt": message,
        "conversation_id": conversation_id,
        "source": "restart_watcher",
    }).encode()
    req = urllib.request.Request(
        CHAT_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read())
    return data.get("result", "")


def git_stash(project_dir: str) -> str:
    result = subprocess.run(
        ["git", "stash", "--include-untracked", "-m", "restart-watcher: auto-stash on crash"],
        cwd=project_dir,
        capture_output=True,
        text=True,
    )
    return (result.stdout + result.stderr).strip()


def main():
    if len(sys.argv) < 3:
        print("usage: restart_watcher.py <conversation_id> <project_dir>")
        sys.exit(1)

    conversation_id = sys.argv[1]
    project_dir = sys.argv[2]

    load_env(project_dir)

    # wait for zipper to go down first (up to 10s)
    for _ in range(10):
        if not is_up():
            break
        time.sleep(1)

    # now wait for it to come back up
    deadline = time.time() + STARTUP_TIMEOUT
    came_up = False
    while time.time() < deadline:
        if is_up():
            came_up = True
            break
        time.sleep(POLL_INTERVAL)

    thread_id = get_discord_thread_id(conversation_id, project_dir)

    if came_up:
        time.sleep(SETTLE_DELAY)
        result = post_resume(
            conversation_id,
            "Restart successful. Zipper came back up cleanly. "
            "Now verify that your changes work as intended.",
        )
        notify_discord(f"✅ Restarted successfully.\n\n{result}", thread_id=thread_id)
    else:
        # restart failed — stash changes and recover
        stash_output = git_stash(project_dir)

        # restart again with clean code
        subprocess.run(
            ["systemctl", "--user", "restart", "zipper"],
            capture_output=True,
        )

        # wait for recovery
        deadline = time.time() + STARTUP_TIMEOUT
        recovered = False
        while time.time() < deadline:
            if is_up():
                recovered = True
                break
            time.sleep(POLL_INTERVAL)

        if recovered:
            time.sleep(SETTLE_DELAY)
            result = post_resume(
                conversation_id,
                f"RESTART FAILED: Your code changes caused a crash and zipper could not start. "
                f"Changes have been automatically stashed (git stash). "
                f"Zipper is now running the previous working code.\n\n"
                f"Stash output: {stash_output}\n\n"
                f"Review the error, fix the issue, and try again.",
            )
            notify_discord(f"❌ Restart failed — rolled back.\n\n{result}", thread_id=thread_id)
        else:
            # truly broken — notify discord directly
            notify_discord(
                f"**Zipper is down and could not recover automatically.**\n"
                f"Code changes caused a crash. Git stash was attempted but zipper still won't start.\n"
                f"**Manual intervention required.**\n"
                f"conversation_id: `{conversation_id}`\n"
                f"stash output: ```{stash_output}```",
                thread_id=thread_id,
            )


if __name__ == "__main__":
    main()
