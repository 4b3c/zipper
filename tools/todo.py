import json
from datetime import datetime
from storage.todos import add_todo, list_todos, update_todo, get_todo
from storage.schedule import add_notification


def run(args: dict) -> str:
    mode = args["mode"]

    if mode == "add":
        title = args.get("title", "").strip()
        if not title:
            return "error: title required"

        subtasks_raw = args.get("subtasks")
        subtasks = []
        if subtasks_raw:
            for i, s in enumerate(subtasks_raw):
                if isinstance(s, str):
                    subtasks.append({"id": str(i), "title": s, "done": False})
                elif isinstance(s, dict):
                    subtasks.append({"id": str(i), "title": s.get("title", ""), "done": s.get("done", False)})

        due_at = args.get("due_at")
        thread_id = args.get("thread_id")

        todo_id = add_todo(
            title=title,
            description=args.get("description"),
            category=args.get("category", "user_todo"),
            priority=args.get("priority", "normal"),
            subtasks=subtasks,
            due_at=due_at,
            thread_id=thread_id,
            task_id=args.get("task_id"),
        )

        # if a reminder time is provided, schedule a direct discord notification
        notification_id = None
        if due_at and args.get("category") == "remind_user":
            try:
                at_dt = datetime.fromisoformat(due_at)
                reminder_msg = args.get("reminder_message") or f"Reminder: {title}"
                notification_id = add_notification(
                    message=reminder_msg,
                    at=at_dt,
                    thread_id=thread_id,
                )
            except ValueError as e:
                return f"ok: created todo {todo_id} (warning: could not schedule notification — {e})"

        parts = [f"ok: created todo {todo_id}"]
        if notification_id:
            parts.append(f"(notification scheduled: {notification_id})")
        return " ".join(parts)

    if mode == "list":
        status = args.get("status")
        category = args.get("category")
        todos = list_todos(status=status, category=category)
        if not todos:
            label_parts = []
            if status:
                label_parts.append(f"status={status}")
            if category:
                label_parts.append(f"category={category}")
            label = ", ".join(label_parts) if label_parts else "all"
            return f"no todos ({label})"
        lines = []
        for t in todos:
            due = f" due {t['due_at'][:16].replace('T', ' ')}" if t.get("due_at") else ""
            cat = t.get("category", "user_todo")
            pri = t.get("priority", "normal")
            
            # normalize subtasks: can be list of strings or list of dicts
            subtasks_raw = t.get("subtasks", [])
            if isinstance(subtasks_raw, str):
                subtasks_raw = json.loads(subtasks_raw) if subtasks_raw else []
            subtasks = []
            for s in subtasks_raw:
                if isinstance(s, dict):
                    subtasks.append(s)
                elif isinstance(s, str):
                    subtasks.append({"title": s, "done": False})
            
            subtask_count = len(subtasks)
            done_count = sum(1 for s in subtasks if s.get("done"))
            sub_info = f" [{done_count}/{subtask_count} subtasks]" if subtask_count else ""
            lines.append(
                f"[{t['status']}] {t['id']} ({cat}, {pri}){due}{sub_info} — {t['title']}"
            )
            for s in subtasks:
                check = "x" if s.get("done") else " "
                lines.append(f"    [{check}] {s.get('title', s)}")
        return "\n".join(lines)

    if mode == "update":
        todo_id = args.get("id")
        if not todo_id:
            return "error: id required"
        todo = get_todo(todo_id)
        if not todo:
            return f"error: todo {todo_id!r} not found"

        fields = {k: v for k, v in args.items() if k not in {"mode", "id"}}

        # handle subtask completion by index
        subtask_done = args.get("subtask_done")
        if subtask_done is not None:
            subtasks = todo.get("subtasks", [])
            if isinstance(subtasks, str):
                subtasks = json.loads(subtasks) if subtasks else []
            try:
                idx = int(subtask_done)
                if idx < len(subtasks) and isinstance(subtasks[idx], dict):
                    subtasks[idx]["done"] = True
                    fields["subtasks"] = subtasks
                else:
                    return f"error: subtask index {subtask_done!r} out of range"
            except (ValueError, TypeError):
                return f"error: subtask index {subtask_done!r} invalid"

        # if marking remind_user category and providing a due_at, auto-schedule notification
        if fields.get("category") == "remind_user" and fields.get("due_at"):
            try:
                at_dt = datetime.fromisoformat(fields["due_at"])
                reminder_msg = fields.get("reminder_message") or f"Reminder: {todo['title']}"
                add_notification(
                    message=reminder_msg,
                    at=at_dt,
                    thread_id=fields.get("thread_id") or todo.get("thread_id"),
                )
            except ValueError as e:
                pass  # non-fatal

        update_todo(todo_id, fields)
        return f"ok: todo {todo_id} updated"

    if mode == "schedule_notification":
        message = args.get("message", "").strip()
        at_str = args.get("at", "").strip()
        if not message:
            return "error: message required"
        if not at_str:
            return "error: at (ISO 8601 datetime) required"
        try:
            at_dt = datetime.fromisoformat(at_str)
        except ValueError:
            return f"error: invalid datetime {at_str!r} — use ISO 8601 format"
        thread_id = args.get("thread_id")
        notif_id = add_notification(message=message, at=at_dt, thread_id=thread_id)
        return f"ok: notification scheduled ({notif_id}) for {at_str}"

    return f"error: unknown mode: {mode}"


SCHEMA = {
    "name": "todo",
    "description": (
        "Manage the user's todo list and schedule direct Discord reminders. "
        "Use this when the user mentions something they need to do, want tracked, or want to be reminded about. "
        "Also use schedule_notification to send a Discord message at a future time without waking Zipper up."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "enum": ["add", "list", "update", "schedule_notification"],
                "description": (
                    "add — add a new todo item. "
                    "list — show todos, optionally filtered. "
                    "update — update any fields on a todo (including marking done/cancelled, checking off subtasks). "
                    "schedule_notification — schedule a direct Discord message at a future time (no LLM wake-up)."
                ),
            },
            "title": {
                "type": "string",
                "description": "Short todo title. Required for add.",
            },
            "description": {
                "type": "string",
                "description": "Full description of the todo item.",
            },
            "category": {
                "type": "string",
                "enum": ["zipper_now", "zipper_scheduled", "remind_user", "user_todo"],
                "description": (
                    "zipper_now — Zipper is handling this immediately in this session. "
                    "zipper_scheduled — Zipper will handle this later (link via task_id). "
                    "remind_user — user needs a reminder; schedule a notification via due_at. "
                    "user_todo — lower-priority item for the user's backlog."
                ),
            },
            "priority": {
                "type": "string",
                "enum": ["high", "normal", "low"],
                "description": "Priority level. Default: normal.",
            },
            "subtasks": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of subtask titles to break the todo into steps.",
            },
            "due_at": {
                "type": "string",
                "description": "ISO 8601 datetime for when to remind the user (remind_user category) or when it's due.",
            },
            "thread_id": {
                "type": "integer",
                "description": "Discord thread ID to send reminders/notifications to.",
            },
            "reminder_message": {
                "type": "string",
                "description": "Custom reminder message for notifications. Defaults to 'Reminder: <title>'.",
            },
            "task_id": {
                "type": "string",
                "description": "ID of a linked task queue entry (for zipper_scheduled items).",
            },
            "id": {
                "type": "string",
                "description": "Todo ID. Required for update.",
            },
            "status": {
                "type": "string",
                "enum": ["pending", "in_progress", "done", "cancelled"],
                "description": "Filter for list mode, or new status for update mode.",
            },
            "subtask_done": {
                "type": "integer",
                "description": "Zero-based index of a subtask to mark done. For update mode.",
            },
            "message": {
                "type": "string",
                "description": "Message to send. Required for schedule_notification.",
            },
            "at": {
                "type": "string",
                "description": "ISO 8601 datetime. Required for schedule_notification.",
            },
        },
        "required": ["mode"],
    },
}
