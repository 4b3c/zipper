"""Todo list persistence — user-facing items separate from the task queue."""

import json
from datetime import datetime
from pathlib import Path

from utils.text import title_to_slug

ROOT = Path(__file__).parent.parent
TODOS_PATH = ROOT / "data" / "todos.json"


def _load() -> list:
    if not TODOS_PATH.exists():
        return []
    return json.loads(TODOS_PATH.read_text())


def _save(todos: list):
    TODOS_PATH.parent.mkdir(parents=True, exist_ok=True)
    TODOS_PATH.write_text(json.dumps(todos, indent=2))


def _generate_id(title: str, existing: list) -> str:
    existing_ids = {t["id"] for t in existing}
    slug = title_to_slug(title, fallback="todo", max_length=50)
    candidate = slug
    counter = 1
    while candidate in existing_ids:
        candidate = f"{slug}-{counter}"
        counter += 1
    return candidate


def add_todo(
    title: str,
    description: str = None,
    category: str = "user_todo",
    priority: str = "normal",
    subtasks: list = None,
    due_at: str = None,
    thread_id: int = None,
    task_id: str = None,
) -> str:
    """Add a new todo item. Returns the new todo ID."""
    todos = _load()
    todo_id = _generate_id(title, todos)
    todo = {
        "id": todo_id,
        "title": title,
        "description": description or title,
        "status": "pending",
        "category": category,
        "priority": priority,
        "subtasks": subtasks or [],
        "due_at": due_at,
        "thread_id": thread_id,
        "task_id": task_id,
        "created_at": datetime.now().isoformat(),
        "completed_at": None,
    }
    todos.append(todo)
    _save(todos)
    return todo_id


def list_todos(status: str = None, category: str = None) -> list:
    todos = _load()
    if status:
        todos = [t for t in todos if t["status"] == status]
    if category:
        todos = [t for t in todos if t["category"] == category]
    return todos


def update_todo(todo_id: str, fields: dict):
    """Update arbitrary fields on a todo item."""
    todos = _load()
    for todo in todos:
        if todo["id"] == todo_id:
            if fields.get("status") in ("done", "cancelled") and not todo.get("completed_at"):
                fields["completed_at"] = datetime.now().isoformat()
            todo.update(fields)
            break
    _save(todos)


def get_todo(todo_id: str) -> dict | None:
    todos = _load()
    return next((t for t in todos if t["id"] == todo_id), None)
