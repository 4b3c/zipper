"""Schedule and wake-log persistence. Supports recurring daily entries, one-shot LLM wakeups, and direct Discord notifications (no LLM)."""

import json
from datetime import datetime
from pathlib import Path

from utils.text import title_to_slug

ROOT = Path(__file__).parent.parent
SCHEDULE_PATH = ROOT / "data" / "schedule.json"
WAKE_LOG_PATH = ROOT / "data" / "wake_log.json"


def load_schedule() -> dict:
    if not SCHEDULE_PATH.exists():
        return {"daily": [], "oneshot": []}
    return json.loads(SCHEDULE_PATH.read_text())


def save_schedule(schedule: dict):
    SCHEDULE_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCHEDULE_PATH.write_text(json.dumps(schedule, indent=2))


def load_wake_log() -> dict:
    if not WAKE_LOG_PATH.exists():
        return {}
    return json.loads(WAKE_LOG_PATH.read_text())


def save_wake_log(log: dict):
    WAKE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    WAKE_LOG_PATH.write_text(json.dumps(log, indent=2))


def add_oneshot(title: str, at: datetime):
    """Add a one-shot schedule entry for the given title and datetime."""
    schedule = load_schedule()

    oneshot_id = title_to_slug(title, fallback="task", max_length=50) + "-" + at.strftime("%Y%m%dT%H%M")
    # avoid duplicates
    if any(e["id"] == oneshot_id for e in schedule.get("oneshot", [])):
        return

    schedule.setdefault("oneshot", []).append({
        "id": oneshot_id,
        "at": at.isoformat(),
        "prompt": "Check for due tasks and work through them.",
    })
    save_schedule(schedule)


def add_notification(message: str, at: datetime, thread_id: int = None, notification_id: str = None):
    """Schedule a direct Discord notification at the given time (no LLM wake-up)."""
    schedule = load_schedule()

    notif_id = notification_id or (
        title_to_slug(message[:40], fallback="notif", max_length=40) + "-" + at.strftime("%Y%m%dT%H%M")
    )
    # avoid duplicates
    if any(e["id"] == notif_id for e in schedule.get("notifications", [])):
        return notif_id

    entry = {
        "id": notif_id,
        "at": at.isoformat(),
        "message": message,
    }
    if thread_id is not None:
        entry["thread_id"] = thread_id

    schedule.setdefault("notifications", []).append(entry)
    save_schedule(schedule)
    return notif_id
