import json
import subprocess
from pathlib import Path
from storage.tasks import create_task, get_due_tasks, update_task_status, patch_task, list_tasks, ARCHIVE_PATH

ROOT = Path(__file__).parent.parent


def _sync_crontab():
    """Run setup_cron.py to sync crontab with schedule.json"""
    try:
        subprocess.run([
            str(ROOT / ".venv" / "bin" / "python"), str(ROOT / "utils" / "setup_cron.py")
        ], capture_output=True, timeout=10, check=False)
    except Exception as e:
        # Log but don't fail the task operation
        print(f"[task] warning: failed to sync crontab: {e}")


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
        _sync_crontab()
        return f"ok: created task {task_id}"

    if mode == "update":
        task_id = args.get("id")
        if not task_id:
            return "error: id required"
        status = args.get("status")
        # patch arbitrary fields first (title, description, due_at, schedule, etc.)
        patch_fields = {k: v for k, v in args.items() if k not in {"mode", "id", "status", "result", "error"}}
        if patch_fields:
            patch_task(task_id, patch_fields)
        # then apply status transition if provided
        if status:
            update_task_status(
                task_id=task_id,
                status=status,
                result=args.get("result"),
                error=args.get("error"),
            )
        _sync_crontab()
        return f"ok: task {task_id} updated"

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
