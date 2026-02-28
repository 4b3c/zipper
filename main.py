import asyncio
import json
from pathlib import Path
from datetime import datetime, timezone

from llm import run_task
from storage.tasks import get_due_tasks, update_task_status
from storage.conversations import create_conversation

CRON_INTERVAL = 10  # seconds


async def cron_loop():
    print(f"[zipper] started at {datetime.now(timezone.utc).isoformat()}")
    while True:
        tasks = get_due_tasks()
        if tasks:
            print(f"[zipper] {len(tasks)} task(s) due")
            for task in tasks:
                await handle_task(task)
        await asyncio.sleep(CRON_INTERVAL)


async def handle_task(task: dict):
    update_task_status(task["id"], "running")
    conversation_id = task.get("conversation_id")

    if not conversation_id:
        conversation_id = create_conversation(
            title=task["description"],
            source="cron",
        )

    try:
        result = await run_task(task["description"], conversation_id)
        update_task_status(task["id"], "done", result=result)
        print(f"[zipper] task {task['id']} done")
    except Exception as e:
        update_task_status(task["id"], "failed", error=str(e))
        print(f"[zipper] task {task['id']} failed: {e}")


if __name__ == "__main__":
    asyncio.run(cron_loop())
