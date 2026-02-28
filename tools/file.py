import os
from pathlib import Path


def run(args: dict) -> str:
    mode = args["mode"]
    directory = args["directory"]

    if mode == "list":
        path = Path(directory)
        if not path.exists():
            return f"error: directory does not exist: {directory}"
        entries = sorted(path.iterdir(), key=lambda e: (e.is_file(), e.name))
        lines = []
        for entry in entries:
            prefix = "  " if entry.is_file() else "/ "
            lines.append(f"{prefix}{entry.name}")
        return "\n".join(lines) if lines else "(empty)"

    filename = args.get("filename")
    if not filename:
        return "error: filename required for this mode"

    filepath = Path(directory) / filename

    if mode == "read":
        if not filepath.exists():
            return f"error: file not found: {filepath}"
        return filepath.read_text(encoding="utf-8")

    if mode == "write":
        content = args.get("content")
        if content is None:
            return "error: content required for write"
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text(content, encoding="utf-8")
        return f"ok: wrote {filepath}"

    if mode == "edit":
        search = args.get("search")
        replace = args.get("replace")
        if search is None or replace is None:
            return "error: search and replace required for edit"
        if not filepath.exists():
            return f"error: file not found: {filepath}"
        original = filepath.read_text(encoding="utf-8")
        if search not in original:
            return f"error: search string not found in {filepath}"
        updated = original.replace(search, replace, 1)
        filepath.write_text(updated, encoding="utf-8")
        return f"ok: edited {filepath}"

    return f"error: unknown mode: {mode}"
