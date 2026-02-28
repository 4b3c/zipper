import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
QUEUE_PATH = ROOT / "data" / "tasks" / "queue.json"


def _load() -> list:
    if not QUEUE_PATH.exists():
        return []
    return json.loads(QUEUE_PATH.read_text())


def _save(tasks: list):
    QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    QUEUE_PATH.write_text(json.dumps(tasks, indent=2))


def create_task(description: str, due_at: str = None, schedule: str = None, conversation_id: str = None) -> str:
    tasks = _load()
    task_id = uuid.uuid4().hex[:12]
    task = {
        "id": task_id,
        "description": description,
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
    for task in tasks:
        if task["id"] == task_id:
            task["status"] = status
            if result is not None:
                task["result"] = result
            if error is not None:
                task["error"] = error
            break
    _save(tasks)


def list_tasks(status: str = None) -> list:
    tasks = _load()
    if status:
        return [t for t in tasks if t["status"] == status]
    return tasks
