import os
import subprocess
import json
import urllib.request

from tools.signals import BreakLoop


def run(args: dict, conversation_id: str) -> str:
    mode = args.get("mode", "zipper")

    if mode == "zipper":
        if not conversation_id:
            return "error: no conversation_id available, cannot resume after restart"

        # register watchdog on the Discord bot process (survives zipper restart)
        bot_url = os.environ.get("BOT_URL", "http://127.0.0.1:4200")
        payload = json.dumps({"conversation_id": conversation_id}).encode()
        req = urllib.request.Request(
            f"{bot_url}/watch-restart",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        registration_error = None
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                r.read()
        except Exception as e:
            registration_error = str(e)

        # trigger the restart — this process will die here
        subprocess.Popen(
            ["systemctl", "--user", "restart", "zipper"],
            close_fds=True,
            start_new_session=True,
        )

        if registration_error:
            raise BreakLoop(f"restarting... (warning: watchdog registration failed: {registration_error})")
        raise BreakLoop("restarting...")

    if mode == "discord":
        project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        result = subprocess.run(
            ["docker", "compose", "up", "-d", "--build", "--remove-orphans"],
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=120,
        )
        output = (result.stdout + result.stderr).strip()
        if result.returncode != 0:
            return f"error restarting discord bot:\n{output}"
        return f"discord bot restarted\n{output}".strip()

    if mode == "dashboard":
        return "dashboard not yet implemented"

    return f"error: unknown mode: {mode}"
