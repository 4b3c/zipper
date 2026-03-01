import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data" / "conversations"


def _title_to_slug(title: str) -> str:
    """Convert a title to a URL-safe slug.
    
    - Convert to lowercase
    - Strip special characters (keep only alphanumerics, spaces, and hyphens)
    - Replace spaces with hyphens
    - Remove consecutive hyphens
    - Strip leading/trailing hyphens
    """
    slug = title.lower()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"\s+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    slug = slug.strip("-")
    return slug or "conversation"


def _generate_conversation_id(title: str) -> str:
    """Generate a slug-based conversation ID, handling collisions."""
    slug = _title_to_slug(title)
    candidate = slug
    counter = 1
    
    while _conversation_path(candidate).exists():
        candidate = f"{slug}-{counter}"
        counter += 1
    
    return candidate


def _conversation_path(conversation_id: str) -> Path:
    return DATA_DIR / conversation_id


def _meta_path(conversation_id: str) -> Path:
    return _conversation_path(conversation_id) / "meta.json"


def _versions_path(conversation_id: str) -> Path:
    return _conversation_path(conversation_id) / "versions"


def create_conversation(title: str, source: str, discord_thread_id: str = None) -> str:
    conversation_id = _generate_conversation_id(title)
    path = _conversation_path(conversation_id)
    path.mkdir(parents=True, exist_ok=True)
    _versions_path(conversation_id).mkdir(exist_ok=True)

    meta = {
        "id": conversation_id,
        "title": title,
        "source": source,
        "discord_thread_id": discord_thread_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "status": "active",
        "summary": "",
        "key_points": [],
    }
    _meta_path(conversation_id).write_text(json.dumps(meta, indent=2))

    # create first version
    create_version(conversation_id, summary="", messages=[])

    return conversation_id


def get_conversation(conversation_id: str) -> dict:
    return json.loads(_meta_path(conversation_id).read_text())


def update_meta(conversation_id: str, **kwargs):
    meta = get_conversation(conversation_id)
    meta.update(kwargs)
    meta["updated_at"] = datetime.now(timezone.utc).isoformat()
    _meta_path(conversation_id).write_text(json.dumps(meta, indent=2))


def get_active_version(conversation_id: str) -> dict:
    versions_path = _versions_path(conversation_id)
    version_files = sorted(versions_path.glob("*.json"), key=lambda f: int(f.stem))
    if not version_files:
        create_version(conversation_id, summary="", messages=[])
        return get_active_version(conversation_id)
    latest = version_files[-1]
    return json.loads(latest.read_text())


def _active_version_path(conversation_id: str) -> Path:
    versions_path = _versions_path(conversation_id)
    version_files = sorted(versions_path.glob("*.json"), key=lambda f: int(f.stem))
    return version_files[-1]


def create_version(conversation_id: str, summary: str, messages: list):
    versions_path = _versions_path(conversation_id)
    existing = sorted(versions_path.glob("*.json"), key=lambda f: int(f.stem))
    next_num = len(existing)
    version = {"version": next_num, "summary": summary, "system_prompt": None, "messages": messages}
    (versions_path / f"{next_num}.json").write_text(json.dumps(version, indent=2))


def set_system_prompt(conversation_id: str, system_prompt: str):
    version_path = _active_version_path(conversation_id)
    version = json.loads(version_path.read_text())
    version["system_prompt"] = system_prompt
    version_path.write_text(json.dumps(version, indent=2))


def pop_last_message(conversation_id: str):
    """Remove the last message from the active version. Used to roll back on API failure."""
    version_path = _active_version_path(conversation_id)
    version = json.loads(version_path.read_text())
    if version["messages"]:
        version["messages"].pop()
    version_path.write_text(json.dumps(version, indent=2))


def save_messages(conversation_id: str, messages: list):
    """Overwrite messages in the active version. Used to persist sanitization."""
    version_path = _active_version_path(conversation_id)
    version = json.loads(version_path.read_text())
    version["messages"] = messages
    version_path.write_text(json.dumps(version, indent=2))


def append_message(conversation_id: str, role: str, content):
    version_path = _active_version_path(conversation_id)
    version = json.loads(version_path.read_text())
    version["messages"].append({"role": role, "content": content})
    version_path.write_text(json.dumps(version, indent=2))
    update_meta(conversation_id)


def list_conversations() -> list:
    if not DATA_DIR.exists():
        return []
    results = []
    for path in sorted(DATA_DIR.iterdir()):
        meta_file = path / "meta.json"
        if meta_file.exists():
            results.append(json.loads(meta_file.read_text()))
    return results
