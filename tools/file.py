import fnmatch
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
PROJECT_ROOT = ROOT  # backward-compatible alias

IGNORED_DIRS = {".venv", "__pycache__", ".git"}
IGNORED_FILES = {".env"}
DEFAULT_HIDDEN_DIRS = {"data"}


def _iter_tree(root: Path, hidden_dirs: set) -> list[Path]:
    """Recursively yield file paths, fully skipping ignored and hidden dirs (used by grep)."""
    results = []
    try:
        entries = sorted(root.iterdir(), key=lambda e: (e.is_file(), e.name))
    except PermissionError:
        return results
    for entry in entries:
        if entry.is_dir():
            if entry.name in IGNORED_DIRS or entry.name in hidden_dirs:
                continue
            results.extend(_iter_tree(entry, hidden_dirs))
        else:
            if entry.name in IGNORED_FILES:
                continue
            results.append(entry)
    return results


DIR_FILE_LIMIT = 30


def _list_tree(root: Path, base: Path, hidden_dirs: set) -> list[str]:
    """Recursively build a listing, showing ignored/hidden entries as '(hidden)' stubs.
    Directories with more than DIR_FILE_LIMIT direct files are truncated."""
    results = []
    try:
        entries = sorted(root.iterdir(), key=lambda e: (e.is_file(), e.name))
    except PermissionError:
        return results

    dirs = [e for e in entries if e.is_dir()]
    files = [e for e in entries if e.is_file()]

    for entry in dirs:
        try:
            rel = entry.relative_to(base)
        except ValueError:
            rel = Path(entry.name)
        if entry.name in IGNORED_DIRS or entry.name in hidden_dirs:
            results.append(f"{rel}/ (hidden)")
        else:
            results.extend(_list_tree(entry, base, hidden_dirs))

    visible_files = [f for f in files if f.name not in IGNORED_FILES]
    hidden_files = [f for f in files if f.name in IGNORED_FILES]

    for entry in hidden_files:
        try:
            rel = entry.relative_to(base)
        except ValueError:
            rel = Path(entry.name)
        results.append(f"{rel} (hidden)")

    truncated = len(visible_files) > DIR_FILE_LIMIT
    for entry in visible_files[:DIR_FILE_LIMIT]:
        try:
            rel = entry.relative_to(base)
        except ValueError:
            rel = Path(entry.name)
        results.append(str(rel))

    if truncated:
        try:
            dir_rel = root.relative_to(base)
        except ValueError:
            dir_rel = Path(root.name)
        results.append(f"{dir_rel}/ ... ({len(visible_files) - DIR_FILE_LIMIT} more files)")

    return results


def _edit_snippets(original: str, updated: str, search: str, replace: str, replace_all: bool) -> list[str]:
    """Return context snippets around each replacement, located via char offsets in original."""
    # collect char offsets of every match in original
    positions = []
    start = 0
    while True:
        idx = original.find(search, start)
        if idx == -1:
            break
        positions.append(idx)
        if not replace_all:
            break
        start = idx + len(search)

    search_line_count = search.count("\n")
    replace_line_count = replace.count("\n")
    line_delta = replace_line_count - search_line_count
    updated_lines = updated.splitlines()
    context = 3
    snippets = []

    for i, pos in enumerate(positions):
        # line number of this match in original (0-indexed)
        orig_line = original[:pos].count("\n")
        # adjust for line count changes from previous replacements
        adj_line = orig_line + i * line_delta
        start_ctx = max(0, adj_line - context)
        end_ctx = min(len(updated_lines), adj_line + replace_line_count + 1 + context)
        block = "\n".join(f"{j + 1:4}: {updated_lines[j]}" for j in range(start_ctx, end_ctx))
        snippets.append(block)

    return snippets


def run(args: dict) -> str:
    mode = args["mode"]
    raw_dir = args.get("directory")
    directory = Path(raw_dir) if raw_dir else PROJECT_ROOT
    include_data = args.get("include_data", False)
    hidden_dirs = set() if include_data else DEFAULT_HIDDEN_DIRS

    if mode == "list":
        if not directory.exists():
            return f"error: directory does not exist: {directory}"
        lines = _list_tree(directory, directory, hidden_dirs)
        return "\n".join(lines) if lines else "(empty)"

    if mode == "grep":
        pattern = args.get("pattern")
        if not pattern:
            return "error: pattern required for grep"
        try:
            compiled = re.compile(pattern)
        except re.error as e:
            return f"error: invalid regex: {e}"
        file_glob = args.get("glob", "*")
        paths = _iter_tree(directory, hidden_dirs)
        results = []
        for p in paths:
            if not fnmatch.fnmatch(p.name, file_glob):
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if compiled.search(line):
                    try:
                        rel = p.relative_to(directory)
                    except ValueError:
                        rel = p
                    results.append(f"{rel}:{i}: {line.rstrip()}")
                    if len(results) >= 200:
                        results.append("... [truncated at 200 matches]")
                        return "\n".join(results)
        return "\n".join(results) if results else f"no matches for '{pattern}'"

    if mode == "read":
        filenames = args.get("filenames")
        filename = args.get("filename")

        if filenames:
            parts = []
            for fn in filenames:
                fp = directory / fn
                if not fp.exists():
                    parts.append(f"=== {fn} ===\nerror: file not found")
                    continue
                try:
                    parts.append(f"=== {fn} ===\n{fp.read_text(encoding='utf-8')}")
                except Exception as e:
                    parts.append(f"=== {fn} ===\nerror: {e}")
            return "\n\n".join(parts)

        if not filename:
            return "error: filename required for read"

        filepath = directory / filename
        if not filepath.exists():
            return f"error: file not found: {filepath}"

        text = filepath.read_text(encoding="utf-8")
        line_start = args.get("line_start")
        line_end = args.get("line_end")
        if line_start is not None or line_end is not None:
            all_lines = text.splitlines()
            start = (line_start or 1) - 1
            end = line_end or len(all_lines)
            sliced = all_lines[start:end]
            text = "\n".join(f"{start + i + 1}: {l}" for i, l in enumerate(sliced))
        return text

    filename = args.get("filename")
    if not filename:
        return "error: filename required for this mode"

    filepath = directory / filename

    if mode == "write":
        content = args.get("content")
        if content is None:
            return "error: content required for write"
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text(content, encoding="utf-8")
        return f"ok: wrote {filepath}"

    if mode == "delete":
        if not filepath.exists():
            return f"error: file not found: {filepath}"
        if filepath.is_dir():
            return f"error: {filepath} is a directory, not a file"
        filepath.unlink()
        return f"ok: deleted {filepath}"

    if mode == "edit":
        search = args.get("search")
        replace = args.get("replace")
        replace_all = args.get("all", False)
        if search is None or replace is None:
            return "error: search and replace required for edit"
        if not filepath.exists():
            return f"error: file not found: {filepath}"
        original = filepath.read_text(encoding="utf-8")
        count = original.count(search)
        if count == 0:
            return f"error: search string not found in {filepath}"
        if count > 1 and not replace_all:
            hits = []
            for i, line in enumerate(original.splitlines(), 1):
                if search in line:
                    hits.append(f"  line {i}: {line.strip()}")
            return (
                f"error: search string found {count} times in {filepath} — be more specific or pass all=true to replace all.\n"
                f"Occurrences:\n" + "\n".join(hits)
            )
        updated = original.replace(search, replace) if replace_all else original.replace(search, replace, 1)
        filepath.write_text(updated, encoding="utf-8")

        n = f"{count}x" if replace_all and count > 1 else "1x"
        header = f"ok: edited {filepath} ({n})"
        snippets = _edit_snippets(original, updated, search, replace, replace_all)
        if snippets:
            return header + "\n\n" + "\n\n...\n\n".join(snippets)
        return header

    return f"error: unknown mode: {mode}"


SCHEMA = {
    "name": "file",
    "description": "Read, write, edit, list, or grep files on the filesystem. Ignores .venv, __pycache__, .git, .env automatically.",
    "input_schema": {
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "enum": ["list", "read", "write", "edit", "delete", "grep"],
                "description": (
                    "list — recursive file tree (defaults to project root, hides data/ by default). "
                    "read — read one or multiple files, optionally a line range. "
                    "write — write full file content. "
                    "edit — exact search/replace (errors on 0 or 2+ matches). "
                    "delete — delete a single file. "
                    "grep — search files using a regex pattern."
                ),
            },
            "directory": {
                "type": "string",
                "description": "Target directory. Defaults to project root if omitted.",
            },
            "filename": {
                "type": "string",
                "description": "Filename. Required for read (single), write, edit.",
            },
            "filenames": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of filenames to read in one call. Use instead of filename for multi-file reads.",
            },
            "content": {
                "type": "string",
                "description": "File content. Required for write.",
            },
            "search": {
                "type": "string",
                "description": "Exact string to find. Required for edit.",
            },
            "replace": {
                "type": "string",
                "description": "String to replace with. Required for edit.",
            },
            "pattern": {
                "type": "string",
                "description": "Regex pattern to search for. Required for grep. Use re syntax (e.g. 'def \\w+', 'import.*os').",
            },
            "glob": {
                "type": "string",
                "description": "Filename glob filter for grep (e.g. '*.py'). Defaults to all files.",
            },
            "line_start": {
                "type": "integer",
                "description": "First line to return (1-indexed). For read mode.",
            },
            "line_end": {
                "type": "integer",
                "description": "Last line to return (inclusive). For read mode.",
            },
            "include_data": {
                "type": "boolean",
                "description": "Include the data/ directory in list/grep. Default false.",
            },
            "all": {
                "type": "boolean",
                "description": "For edit: replace all occurrences instead of erroring on multiple matches. Default false.",
            },
            "help": {
                "type": "boolean",
                "description": "Return usage guide for this tool without performing any action.",
            },
        },
        "required": ["mode"],
    },
}
