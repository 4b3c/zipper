"""Schedule and wake-log persistence. Supports recurring daily entries, one-shot LLM wakeups, and direct Discord notifications (no LLM)."""

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

from utils.text import title_to_slug
from utils.constants import ZIPPER_URL

ROOT = Path(__file__).parent.parent
SCHEDULE_PATH = ROOT / "data" / "schedule.json"
WAKE_LOG_PATH = ROOT / "data" / "wake_log.json"
CRON_LOG = ROOT / "logs" / "cron.log"
WAKE_HISTORY_PATH = ROOT / "data" / "wake_history.jsonl"

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


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


def log_wake_event(event_type: str, prompt: str, response: str, conversation_id: str, **extra) -> None:
    """
    Log a wake event to wake_history.jsonl (newline-delimited JSON).
    event_type: 'oneshot', 'task', or 'checkin'
    extra: optional fields like task_id, entry_id, etc.
    """
    WAKE_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.now().isoformat(),
        "type": event_type,
        "conversation_id": conversation_id,
        "prompt_length": len(prompt),
        "response_length": len(response),
        **extra
    }
    # Append as newline-delimited JSON
    with open(WAKE_HISTORY_PATH, "a") as f:
        f.write(json.dumps(entry) + "\n")


def generate_cron_line(task_id: str, due_dt: datetime, schedule: str) -> str:
    """
    Generate a cron entry for a task with the given schedule pattern.
    Returns the cron line: "minute hour day month weekday command"
    """
    s = schedule.strip().lower()
    
    # For recurring schedules (daily, every N days, etc.), use the time from due_dt
    # For named weekdays, trigger on that specific day
    
    cmd = f'curl -s -X POST {ZIPPER_URL}/wake -H "Content-Type: application/json" -d \'{{"task_id":"{task_id}"}}\' >> {CRON_LOG} 2>&1'
    
    minute = due_dt.minute
    hour = due_dt.hour
    
    if s == "daily":
        # Every day at the specified time
        return f"{minute} {hour} * * * {cmd}"
    
    if s == "weekly":
        # Every week on the same weekday at the specified time
        day_of_week = due_dt.weekday()
        return f"{minute} {hour} * * {day_of_week} {cmd}"
    
    m = re.match(r"every (\d+) days?", s)
    if m:
        # Can't do "every N days" precisely in cron, so use the day-of-month and month
        # This is approximate and works best if the task recurs monthly
        day = due_dt.day
        return f"{minute} {hour} {day} * * {cmd}"
    
    m = re.match(r"every (\d+) hours?", s)
    if m:
        # For hourly, we use a simpler approach: trigger at the minute mark of each hour
        return f"{minute} * * * * {cmd}"
    
    for i, day in enumerate(WEEKDAYS):
        if s == f"every {day}":
            # Specific day of week
            return f"{minute} {hour} * * {i} {cmd}"
    
    # Fallback: treat as one-shot at the given time
    return f"{minute} {hour} {due_dt.day} {due_dt.month} * {cmd}"
