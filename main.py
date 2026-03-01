import json
import asyncio
from datetime import datetime, date
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import uvicorn

from llm import run_task
from storage.conversations import create_conversation
from storage.tasks import get_due_tasks

ROOT = Path(__file__).parent
SCHEDULE_PATH = ROOT / "data" / "schedule.json"
WAKE_LOG_PATH = ROOT / "data" / "wake_log.json"

app = FastAPI()


# --- Models ---

class ChatRequest(BaseModel):
    prompt: str
    conversation_id: str | None = None
    source: str = "api"
    discord_thread_id: int | None = None

class WakeRequest(BaseModel):
    time: str  # HH:MM


# --- Schedule helpers ---

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


# --- Routes ---

@app.get("/status")
def status():
    return {"status": "ok", "time": datetime.now().isoformat()}


@app.post("/chat")
async def chat(req: ChatRequest):
    if req.conversation_id:
        conversation_id = req.conversation_id
    else:
        conversation_id = create_conversation(
            title=req.prompt[:60],
            source=req.source,
            discord_thread_id=req.discord_thread_id
        )

    result = await run_task(req.prompt, conversation_id)
    return {"conversation_id": conversation_id, "result": result}


@app.post("/wake")
async def wake(req: WakeRequest):
    now = datetime.now()
    wake_log = load_wake_log()

    # check oneshots
    schedule = load_schedule()
    current_dt = now.replace(second=0, microsecond=0)
    for entry in schedule.get("oneshot", []):
        entry_dt = datetime.fromisoformat(entry["at"]).replace(second=0, microsecond=0)
        if current_dt >= entry_dt:
            schedule["oneshot"] = [e for e in schedule["oneshot"] if e["id"] != entry["id"]]
            save_schedule(schedule)

            due_tasks = get_due_tasks()
            if due_tasks:
                task_lines = "\n".join(
                    f"- [{t['id']}] {t['title']}: {t['description']} (due {t['due_at'][:16].replace('T', ' ')})"
                    for t in due_tasks
                )
                task_section = f"\n\nThe following tasks are due:\n{task_lines}\n\nWork through them, mark each done or failed using the task tool when finished."
            else:
                task_section = f"\n\n{entry['prompt']}"

            prompt = (
                f"You have been woken up by a scheduled reminder for {entry['at']}. "
                f"It is now {now.strftime('%Y-%m-%d %H:%M')}."
                f"{task_section}"
            )
            conversation_id = create_conversation(title=f"Oneshot: {entry['prompt'][:50]}", source="cron")
            result = await run_task(prompt, conversation_id)
            return {"conversation_id": conversation_id, "result": result}

    # daily check-in
    slot = req.time
    if wake_log.get(slot) == date.today().isoformat():
        return {"skipped": True, "reason": f"{slot} already fired today"}

    wake_log[slot] = date.today().isoformat()
    save_wake_log(wake_log)

    due_tasks = get_due_tasks()
    if due_tasks:
        import json
        task_lines = "\n".join(
            f"- [{t['id']}] {t['description']} (due {t['due_at'][:16].replace('T', ' ')})"
            for t in due_tasks
        )
        task_section = f"\n\nThe following tasks are due:\n{task_lines}\n\nWork through them, mark each done or failed using the task tool when finished."
    else:
        task_section = "\n\nNo tasks are currently due."

    prompt = (
        f"You have woken up for your scheduled check-in at {slot}. "
        f"Today is {now.strftime('%A, %B %d %Y')}. "
        f"Review your task queue, handle anything pending, and do anything useful."
        f"{task_section}"
    )
    conversation_id = create_conversation(title=f"Check-in {slot}", source="cron")
    result = await run_task(prompt, conversation_id)
    return {"conversation_id": conversation_id, "result": result}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=4199, reload=False)
