import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
MEMORY_PATH = ROOT / "data" / "memory.json"


def _load() -> dict:
    if not MEMORY_PATH.exists():
        return {}
    return json.loads(MEMORY_PATH.read_text())


def _save(memory: dict):
    MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    MEMORY_PATH.write_text(json.dumps(memory, indent=2))


def get(key: str):
    return _load().get(key)


def set(key: str, value):
    memory = _load()
    memory[key] = {"value": value, "updated_at": datetime.now(timezone.utc).isoformat()}
    _save(memory)


def delete(key: str):
    memory = _load()
    memory.pop(key, None)
    _save(memory)


def all() -> dict:
    return _load()
