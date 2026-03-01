import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def _title_to_slug(title: str) -> str:
    slug = title.lower()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"\s+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    slug = slug.strip("-")
    return (slug[:50].rstrip("-")) or "task"


def _generate_task_id(title: str, existing: list) -> str:
    existing_ids = {t["id"] for t in existing}
    slug = _title_to_slug(title)
    candidate = slug
    counter = 1
    while candidate in existing_ids:
        candidate = f"{slug}-{counter}"
        counter += 1
    return candidate


def _next_due(schedule: str, from_dt: datetime) -> datetime | None:
    s = schedule.strip().lower()

    if s == "daily":
        return from_dt + timedelta(days=1)

    if s == "weekly":
        return from_dt + timedelta(weeks=1)

    m = re.match(r"every (\d+) hours?", s)
    if m:
        return from_dt + timedelta(hours=int(m.group(1)))

    m = re.match(r"every (\d+) days?", s)
    if m:
        return from_dt + timedelta(days=int(m.group(1)))

    for i, day in enumerate(WEEKDAYS):
        if s == f"every {day}":
            days_ahead = (i - from_dt.weekday()) % 7 or 7
            return from_dt + timedelta(days=days_ahead)

    return None

ROOT = Path(__file__).parent.parent
QUEUE_PATH = ROOT / "data" / "tasks" / "queue.json"
ARCHIVE_PATH = ROOT / "data" / "tasks" / "archive.json"
SCHEDULE_PATH = ROOT / "data" / "schedule.json"


def _archive(task: dict):
    if ARCHIVE_PATH.exists():
        archive = json.loads(ARCHIVE_PATH.read_text())
    else:
        archive = []
    task["archived_at"] = datetime.now(timezone.utc).isoformat()
    archive.append(task)
    ARCHIVE_PATH.parent.mkdir(parents=True, exist_ok=True)
    ARCHIVE_PATH.write_text(json.dumps(archive, indent=2))


def _add_oneshot(title: str, at: datetime):
    if SCHEDULE_PATH.exists():
        schedule = json.loads(SCHEDULE_PATH.read_text())
    else:
        schedule = {"daily": [], "oneshot": []}

    oneshot_id = _title_to_slug(title) + "-" + at.strftime("%Y%m%dT%H%M")
    # avoid duplicates
    if any(e["id"] == oneshot_id for e in schedule.get("oneshot", [])):
        return

    schedule.setdefault("oneshot", []).append({
        "id": oneshot_id,
        "at": at.isoformat(),
        "prompt": f"Check for due tasks and work through them.",
    })
    SCHEDULE_PATH.write_text(json.dumps(schedule, indent=2))


def _load() -> list:
    if not QUEUE_PATH.exists():
        return []
    return json.loads(QUEUE_PATH.read_text())


def _save(tasks: list):
    QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    QUEUE_PATH.write_text(json.dumps(tasks, indent=2))


def create_task(title: str, description: str = None, due_at: str = None, schedule: str = None, conversation_id: str = None) -> str:
    tasks = _load()
    task_id = _generate_task_id(title, tasks)
    task = {
        "id": task_id,
        "title": title,
        "description": description or title,
        "status": "pending",
        "schedule": schedule,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "due_at": due_at or datetime.now(timezone.utc).isoformat(),
        "conversation_id": conversation_id,
        "result": None,
        "error": None,
    }
    tasks.append(task)
    _save(tasks)
    return task_id


def get_due_tasks() -> list:
    tasks = _load()
    now = datetime.now(timezone.utc).isoformat()
    return [t for t in tasks if t["status"] == "pending" and t["due_at"] <= now]


def update_task_status(task_id: str, status: str, result: str = None, error: str = None):
    tasks = _load()
    completed_task = None
    for task in tasks:
        if task["id"] == task_id:
            task["status"] = status
            if result is not None:
                task["result"] = result
            if error is not None:
                task["error"] = error
            completed_task = task
            break

    if completed_task and status in ("done", "failed"):
        tasks = [t for t in tasks if t["id"] != task_id]
        _save(tasks)
        _archive(completed_task)
    else:
        _save(tasks)

    if completed_task and completed_task.get("schedule"):
        prev_due = datetime.fromisoformat(completed_task["due_at"])
        next_dt = _next_due(completed_task["schedule"], prev_due)
        if next_dt:
            create_task(
                title=completed_task["title"],
                description=completed_task.get("description"),
                due_at=next_dt.isoformat(),
                schedule=completed_task["schedule"],
            )
            _add_oneshot(completed_task["title"], next_dt)


def list_tasks(status: str = None) -> list:
    tasks = _load()
    if status:
        return [t for t in tasks if t["status"] == status]
    return tasks
