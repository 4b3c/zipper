#!/usr/bin/env python3
"""
Test runner — runs gold conversation tests with a simulated LLM.

Each gold file in tests/gold/*.json defines:
  - name: test name
  - prompt: the initial user message
  - turns: list of LLM responses (the mock replays these in order)
  - checks: assertions evaluated after the run
  - mocks.web: list of web tool outputs to return in sequence
  - mocks.discord: per-mode overrides for discord tool return values
  - sandbox.create: files to create before running (cleaned up after)

Usage:
  python tests/runner.py            # run all tests
  python tests/runner.py file       # run tests matching "file"
  python tests/runner.py -v         # verbose (show full errors)
"""

import asyncio
import json
import os
import sys
import tempfile
import traceback as tb
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# Load .env before importing any project modules (llm/__init__ reads ANTHROPIC_API_KEY at import time)
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from tests.mock_client import MockClient
from llm import run_conversation
from storage.conversations import create_conversation
from storage.trace import get_trace
from tools.signals import BreakLoop

GOLD_DIR = Path(__file__).parent / "gold"
SANDBOX_DIR = Path(__file__).parent / "sandbox"


# ---------------------------------------------------------------------------
# Mock factories
# ---------------------------------------------------------------------------

def _discord_mock(gold: dict):
    overrides = gold.get("mocks", {}).get("discord", {})
    call_index = [0]

    def run(args: dict) -> str:
        mode = args.get("mode", "send")
        if mode in overrides:
            v = overrides[mode]
            return v if isinstance(v, str) else json.dumps(v)
        defaults = {
            "send": f"ok: sent (message_id: mock-msg-{call_index[0]:03d})",
            "history": "mock-msg-000 | TestUser | Hello from mock",
            "edit": "ok: message edited",
            "react": "ok: reacted with ✅",
            "inject": "ok: injected",
        }
        call_index[0] += 1
        return defaults.get(mode, f"ok: {mode}")

    return run


def _web_mock(gold: dict):
    outputs = list(gold.get("mocks", {}).get("web", []))
    idx = [0]

    def run(args: dict) -> str:
        if idx[0] < len(outputs):
            out = outputs[idx[0]]
            idx[0] += 1
            return out if isinstance(out, str) else json.dumps(out)
        mode = args.get("mode", "search")
        if mode == "search":
            return "1. Mock Result — https://example.com\nA mocked search result."
        return "[mocked page content]"

    return run


def _restart_mock():
    def run(args: dict, conversation_id: str = "") -> str:
        mode = args.get("mode", "zipper")
        if mode == "zipper":
            raise BreakLoop("Restarting zipper... (mocked)")
        return f"{mode} restarted. (mocked)"

    return run


# ---------------------------------------------------------------------------
# Check evaluation
# ---------------------------------------------------------------------------

def _resolve_index(check: dict, entries: list) -> int:
    idx = check.get("index", -1)
    return len(entries) - 1 if idx == -1 else idx


def evaluate_checks(checks: list, entries: list, final_response: str) -> list[tuple]:
    """Returns list of (check_dict, passed: bool, message: str)."""
    results = []

    for check in checks:
        ctype = check["type"]
        idx = _resolve_index(check, entries)

        if ctype == "tool_called":
            if idx < 0 or idx >= len(entries):
                results.append((check, False, f"no entry at index {idx} ({len(entries)} total)"))
                continue
            entry = entries[idx]
            tool = check.get("tool")
            args_contains = check.get("args_contains", {})
            if tool and entry.get("tool") != tool:
                results.append((check, False, f"expected tool={tool!r}, got {entry.get('tool')!r}"))
                continue
            for k, v in args_contains.items():
                if entry.get("args", {}).get(k) != v:
                    results.append((check, False, f"args[{k}]={v!r} not found, got {entry.get('args', {}).get(k)!r}"))
                    break
            else:
                results.append((check, True, "ok"))

        elif ctype == "tool_succeeded":
            if idx < 0 or idx >= len(entries):
                results.append((check, False, f"no entry at index {idx}"))
                continue
            entry = entries[idx]
            if entry.get("status") == "ok":
                results.append((check, True, "ok"))
            else:
                results.append((check, False, f"tool errored: {entry.get('error') or entry.get('output', '')[:120]}"))

        elif ctype == "tool_output_contains":
            if idx < 0 or idx >= len(entries):
                results.append((check, False, f"no entry at index {idx}"))
                continue
            text = check["text"]
            output = entries[idx].get("output", "")
            if text in output:
                results.append((check, True, "ok"))
            else:
                results.append((check, False, f"{text!r} not in output\ngot: {output[:200]}"))

        elif ctype == "tool_output_not_contains":
            if idx < 0 or idx >= len(entries):
                results.append((check, False, f"no entry at index {idx}"))
                continue
            text = check["text"]
            output = entries[idx].get("output", "")
            if text not in output:
                results.append((check, True, "ok"))
            else:
                results.append((check, False, f"{text!r} unexpectedly found in output"))

        elif ctype == "tool_error_contains":
            if idx < 0 or idx >= len(entries):
                results.append((check, False, f"no entry at index {idx}"))
                continue
            text = check["text"]
            error = entries[idx].get("error") or entries[idx].get("output", "")
            if text in (error or ""):
                results.append((check, True, "ok"))
            else:
                results.append((check, False, f"{text!r} not in error: {error!r}"))

        elif ctype == "tool_count":
            count = check["count"]
            if len(entries) == count:
                results.append((check, True, "ok"))
            else:
                results.append((check, False, f"expected {count} tool calls, got {len(entries)}"))

        elif ctype == "final_response_contains":
            text = check["text"]
            if text in (final_response or ""):
                results.append((check, True, "ok"))
            else:
                results.append((check, False, f"{text!r} not in response\ngot: {(final_response or '')[:200]}"))

        elif ctype == "final_response_not_empty":
            if final_response and final_response.strip():
                results.append((check, True, "ok"))
            else:
                results.append((check, False, "response was empty"))

        else:
            results.append((check, False, f"unknown check type: {ctype!r}"))

    return results


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

async def run_gold_test(gold_path: Path) -> dict:
    gold = json.loads(gold_path.read_text())
    name = gold.get("name", gold_path.stem)

    SANDBOX_DIR.mkdir(exist_ok=True)
    sandbox_created = []

    for sf in gold.get("sandbox", {}).get("create", []):
        p = ROOT / sf["path"]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(sf["content"])
        sandbox_created.append(p)

    conversation_id = None
    tmp_queue = tmp_archive = None

    try:
        mock_client = MockClient(gold["turns"])

        conversation_id = create_conversation(title=f"[test] {name}", source="test")

        # Isolated task storage
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            f.write("[]")
            tmp_queue = f.name
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            f.write("[]")
            tmp_archive = f.name

        with (
            patch("llm.client", mock_client),
            patch("tools.discord_run", _discord_mock(gold)),
            patch("tools.web_run", _web_mock(gold)),
            patch("tools.restart_run", _restart_mock()),
            patch("storage.tasks.QUEUE_PATH", Path(tmp_queue)),
            patch("storage.tasks.ARCHIVE_PATH", Path(tmp_archive)),
            patch("tools.task.ARCHIVE_PATH", Path(tmp_archive)),
        ):
            final_response = await run_conversation(gold.get("prompt", "test"), conversation_id)

        trace = get_trace(conversation_id)
        entries = trace.get("entries", [])
        check_results = evaluate_checks(gold.get("checks", []), entries, final_response)
        passed = all(p for _, p, _ in check_results)

        return {"name": name, "passed": passed, "checks": check_results, "error": None}

    except Exception:
        return {"name": name, "passed": False, "checks": [], "error": tb.format_exc()}

    finally:
        for p in sandbox_created:
            if p.exists():
                p.unlink()
        if tmp_queue and os.path.exists(tmp_queue):
            os.unlink(tmp_queue)
        if tmp_archive and os.path.exists(tmp_archive):
            os.unlink(tmp_archive)


def _check_label(check: dict) -> str:
    parts = [check["type"]]
    if "index" in check:
        parts[0] += f"[{check['index']}]"
    if "tool" in check:
        parts.append(check["tool"])
    if "args_contains" in check:
        parts.append(str(check["args_contains"]))
    if "text" in check:
        parts.append(repr(check["text"]))
    if "count" in check:
        parts.append(str(check["count"]))
    return " ".join(parts)


def run_all(filter_name: str = None, verbose: bool = False) -> int:
    gold_files = sorted(GOLD_DIR.glob("*.json"))
    if filter_name:
        gold_files = [f for f in gold_files if filter_name.lower() in f.stem.lower()]

    if not gold_files:
        print(f"No gold files found in {GOLD_DIR}")
        return 1

    results = []
    for gf in gold_files:
        result = asyncio.run(run_gold_test(gf))
        results.append(result)

        status = "PASS" if result["passed"] else "FAIL"
        print(f"\n[{status}] {result['name']}")

        if result["error"]:
            if verbose:
                print(f"  {result['error']}")
            else:
                first_line = result["error"].strip().splitlines()[-1]
                print(f"  ERROR: {first_line}")
        else:
            for check, passed, msg in result["checks"]:
                icon = "  ✓" if passed else "  ✗"
                label = _check_label(check)
                detail = f" — {msg}" if not passed else ""
                print(f"{icon} {label}{detail}")

    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    print(f"\n{'─' * 40}")
    print(f"{passed}/{total} passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    args = sys.argv[1:]
    verbose = "-v" in args
    args = [a for a in args if a != "-v"]
    filter_name = args[0] if args else None
    sys.exit(run_all(filter_name, verbose=verbose))
