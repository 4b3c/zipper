import json
from storage.tasks import create_task, get_due_tasks, update_task_status, list_tasks, ARCHIVE_PATH


def run(args: dict) -> str:
    mode = args["mode"]

    if mode == "list":
        status = args.get("status")
        tasks = list_tasks(status)
        if not tasks:
            label = f"status={status}" if status else "all"
            return f"no tasks ({label})"
        lines = []
        for t in tasks:
            due = t.get("due_at", "")[:16].replace("T", " ")
            result_snippet = f" | result: {t['result'][:60]}" if t.get("result") else ""
            error_snippet = f" | error: {t['error'][:60]}" if t.get("error") else ""
            lines.append(
                f"[{t['status']}] {t['id']} — {t['description'][:80]} (due {due}){result_snippet}{error_snippet}"
            )
        return "\n".join(lines)

    if mode == "create":
        title = args.get("title")
        if not title:
            return "error: title required"
        task_id = create_task(
            title=title,
            description=args.get("description"),
            due_at=args.get("due_at"),
            schedule=args.get("schedule"),
            conversation_id=args.get("conversation_id"),
        )
        return f"ok: created task {task_id}"

    if mode == "update":
        task_id = args.get("id")
        status = args.get("status")
        if not task_id or not status:
            return "error: id and status required"
        update_task_status(
            task_id=task_id,
            status=status,
            result=args.get("result"),
            error=args.get("error"),
        )
        return f"ok: task {task_id} → {status}"

    if mode == "archive":
        if not ARCHIVE_PATH.exists():
            return "archive is empty"
        archive = json.loads(ARCHIVE_PATH.read_text())
        if not archive:
            return "archive is empty"
        limit = args.get("limit", 20)
        recent = archive[-limit:][::-1]
        lines = []
        for t in recent:
            ts = t.get("archived_at", "")[:16].replace("T", " ")
            result_snippet = f" | {t['result'][:80]}" if t.get("result") else ""
            error_snippet = f" | error: {t['error'][:80]}" if t.get("error") else ""
            lines.append(f"[{t['status']}] {t['id']} ({ts}){result_snippet}{error_snippet}")
        return "\n".join(lines)

    if mode == "due":
        tasks = get_due_tasks()
        if not tasks:
            return "no tasks due"
        return json.dumps(tasks, indent=2)

    return f"error: unknown mode: {mode}"
