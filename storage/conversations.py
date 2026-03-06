import json
import uuid
from datetime import datetime
from pathlib import Path

from utils.text import title_to_slug

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data" / "conversations"


def _generate_conversation_id(title: str) -> str:
    """Generate a slug-based conversation ID, handling collisions."""
    slug = title_to_slug(title, fallback="conversation")
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


def _full_path(conversation_id: str) -> Path:
    return _conversation_path(conversation_id) / "full.json"


def conversation_exists(conversation_id: str) -> bool:
    return _conversation_path(conversation_id).exists()


def create_conversation(title: str, source: str, discord_thread_id: str = None, conversation_id: str = None) -> str:
    if conversation_id is None:
        conversation_id = _generate_conversation_id(title)
    path = _conversation_path(conversation_id)
    path.mkdir(parents=True, exist_ok=True)
    _versions_path(conversation_id).mkdir(exist_ok=True)

    meta = {
        "id": conversation_id,
        "title": title,
        "source": source,
        "discord_thread_id": discord_thread_id,
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
        "status": "active",
        "summary": "",
        "key_points": [],
    }
    _meta_path(conversation_id).write_text(json.dumps(meta, indent=2))

    # create first version
    create_version(conversation_id, summary="", messages=[])

    # initialize full history log
    _full_path(conversation_id).write_text(json.dumps({"messages": []}, indent=2))

    return conversation_id


def get_conversation(conversation_id: str) -> dict:
    return json.loads(_meta_path(conversation_id).read_text())


def update_meta(conversation_id: str, **kwargs):
    meta = get_conversation(conversation_id)
    meta.update(kwargs)
    meta["updated_at"] = datetime.now().isoformat()
    _meta_path(conversation_id).write_text(json.dumps(meta, indent=2))


def get_full_history(conversation_id: str) -> list:
    """Return all messages ever in this conversation (across compaction boundaries).
    Falls back to latest version if full.json doesn't exist (older conversations).
    """
    full_path = _full_path(conversation_id)
    if full_path.exists():
        return json.loads(full_path.read_text()).get("messages", [])
    return get_latest_version(conversation_id).get("messages", [])


def get_latest_version(conversation_id: str) -> dict:
    versions_path = _versions_path(conversation_id)
    version_files = sorted(versions_path.glob("*.json"), key=lambda f: int(f.stem))
    if not version_files:
        create_version(conversation_id, summary="", messages=[])
        return get_latest_version(conversation_id)
    latest = version_files[-1]
    return json.loads(latest.read_text())


# Keep old name as alias for callers not yet updated
get_active_version = get_latest_version


def _latest_version_path(conversation_id: str) -> Path:
    versions_path = _versions_path(conversation_id)
    version_files = sorted(versions_path.glob("*.json"), key=lambda f: int(f.stem))
    return version_files[-1]


# Keep old name as alias
_active_version_path = _latest_version_path


def create_version(conversation_id: str, summary: str, messages: list):
    versions_path = _versions_path(conversation_id)
    existing = sorted(versions_path.glob("*.json"), key=lambda f: int(f.stem))
    next_num = len(existing)
    version = {"version": next_num, "summary": summary, "system_prompt": None, "messages": messages}
    (versions_path / f"{next_num}.json").write_text(json.dumps(version, indent=2))


def set_system_prompt(conversation_id: str, system_prompt: str):
    version_path = _latest_version_path(conversation_id)
    version = json.loads(version_path.read_text())
    version["system_prompt"] = system_prompt
    version_path.write_text(json.dumps(version, indent=2))


def pop_last_message(conversation_id: str):
    """Remove the last message from the active version. Used to roll back on API failure."""
    version_path = _latest_version_path(conversation_id)
    version = json.loads(version_path.read_text())
    if version["messages"]:
        version["messages"].pop()
    version_path.write_text(json.dumps(version, indent=2))


def save_messages(conversation_id: str, messages: list):
    """Overwrite messages in the active version. Used to persist sanitization."""
    version_path = _latest_version_path(conversation_id)
    version = json.loads(version_path.read_text())
    version["messages"] = messages
    version_path.write_text(json.dumps(version, indent=2))


def append_message(conversation_id: str, role: str, content):
    msg = {"role": role, "content": content}

    version_path = _latest_version_path(conversation_id)
    version = json.loads(version_path.read_text())
    version["messages"].append(msg)
    version_path.write_text(json.dumps(version, indent=2))

    # keep full history in sync
    full_path = _full_path(conversation_id)
    if full_path.exists():
        full = json.loads(full_path.read_text())
        full["messages"].append(msg)
        full_path.write_text(json.dumps(full, indent=2))

    update_meta(conversation_id)


def find_conversation_by_thread(discord_thread_id: int) -> str | None:
    """Scan conversation meta files to find one matching the given discord thread ID."""
    if not DATA_DIR.exists():
        return None
    for path in sorted(DATA_DIR.iterdir()):
        meta_file = path / "meta.json"
        if not meta_file.exists():
            continue
        try:
            meta = json.loads(meta_file.read_text())
            if meta.get("discord_thread_id") == discord_thread_id:
                return meta["id"]
        except Exception:
            pass
    return None


def get_conversation_thread_id(conversation_id: str) -> int | None:
    """Return the discord_thread_id for a conversation, or None if not set."""
    try:
        thread_id = get_conversation(conversation_id).get("discord_thread_id")
        return int(thread_id) if thread_id else None
    except Exception:
        return None


def list_conversations() -> list:
    if not DATA_DIR.exists():
        return []
    results = []
    for path in sorted(DATA_DIR.iterdir()):
        meta_file = path / "meta.json"
        if meta_file.exists():
            results.append(json.loads(meta_file.read_text()))
    return results
