import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent


def _trace_path(conversation_id: str) -> Path:
    return ROOT / "data" / "conversations" / conversation_id / "trace.json"


def _load(conversation_id: str) -> dict:
    path = _trace_path(conversation_id)
    if not path.exists():
        return {"conversation_id": conversation_id, "entries": []}
    return json.loads(path.read_text())


def _save(conversation_id: str, trace: dict):
    _trace_path(conversation_id).write_text(json.dumps(trace, indent=2))


def append_trace_entry(conversation_id: str, entry: dict):
    trace = _load(conversation_id)
    entry["id"] = uuid.uuid4().hex[:8]
    entry["timestamp"] = datetime.now(timezone.utc).isoformat()
    trace["entries"].append(entry)
    _save(conversation_id, trace)


def get_trace(conversation_id: str) -> dict:
    return _load(conversation_id)
