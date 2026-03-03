import subprocess
import sys
from pathlib import Path

from tools.signals import BreakLoop

ROOT = Path(__file__).parent.parent


def run(args: dict, conversation_id: str) -> str:
    mode = args.get("mode", "zipper")

    if mode == "zipper":
        if not conversation_id:
            return "error: no conversation_id available, cannot resume after restart"

        # Spawn restart_watcher.py as a detached subprocess — it survives the zipper restart,
        # monitors recovery, and resumes this conversation via /chat.
        watcher = ROOT / "utils" / "restart_watcher.py"
        subprocess.Popen(
            [sys.executable, str(watcher), conversation_id, str(ROOT)],
            close_fds=True,
            start_new_session=True,
        )

        # Trigger the restart — this process will die here
        subprocess.Popen(
            ["systemctl", "restart", "zipper"],
            close_fds=True,
            start_new_session=True,
        )

        raise BreakLoop("restarting...")

    if mode == "discord":
        result = subprocess.run(
            ["systemctl", "restart", "zipper-discord"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        output = (result.stdout + result.stderr).strip()
        if result.returncode != 0:
            return f"error restarting discord bot service:\n{output}"
        return f"discord bot service restarted\n{output}".strip()

    if mode == "dashboard":
        return "dashboard not yet implemented"

    return f"error: unknown mode: {mode}"


SCHEMA = {
    "name": "restart",
    "description": "Restart a zipper service component.",
    "input_schema": {
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "enum": ["zipper", "discord", "dashboard"],
                "description": (
                    "zipper — restart the main zipper process via systemctl. "
                    "Spawns a watcher that resumes this conversation with the result. "
                    "If startup fails, code changes are stashed and previous state is restored. "
                    "discord — restart the zipper-discord systemd user service synchronously. "
                    "dashboard — restart the dashboard (not yet implemented)."
                ),
            },
            "help": {
                "type": "boolean",
                "description": "Return usage guide for this tool without performing any action.",
            },
        },
        "required": ["mode"],
    },
}
