import os
import subprocess
import json
import urllib.request
import urllib.error

from tools.signals import BreakLoop


def run(args: dict, conversation_id: str) -> str:
    mode = args.get("mode", "zipper")

    if mode == "zipper":
        if not conversation_id:
            return "error: no conversation_id available, cannot resume after restart"

        # register watchdog on the Discord bot process (survives zipper restart)
        bot_url = "http://127.0.0.1:4200"
        payload = json.dumps({"conversation_id": conversation_id}).encode()
        req = urllib.request.Request(
            f"{bot_url}/watch-restart",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        registration_error = None
        registration_response = None
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                if r.status != 200:
                    raise RuntimeError(f"watchdog registration returned HTTP {r.status}")
                raw = r.read()
                registration_response = json.loads(raw or b"{}")
                if not isinstance(registration_response, dict):
                    raise RuntimeError("watchdog registration returned non-object JSON")
                if registration_response.get("ok") is not True:
                    raise RuntimeError(
                        f"watchdog registration rejected request: {json.dumps(registration_response)}"
                    )
                if str(registration_response.get("conversation_id", "")).strip() != conversation_id:
                    raise RuntimeError(
                        "watchdog registration returned mismatched conversation_id"
                    )
                thread_id = registration_response.get("thread_id")
                if thread_id is None:
                    raise RuntimeError("watchdog registration returned no thread_id")
                try:
                    int(thread_id)
                except (TypeError, ValueError):
                    raise RuntimeError("watchdog registration returned invalid thread_id")
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace").strip()
            except Exception:
                pass
            if body:
                registration_error = f"HTTP {e.code}: {body}"
            else:
                registration_error = f"HTTP {e.code}"
        except Exception as e:
            registration_error = str(e)

        # Never restart unless watchdog acknowledged the request meaningfully.
        if registration_error:
            return (
                "error: restart aborted because watchdog did not acknowledge the restart request. "
                f"{registration_error}"
            )

        # trigger the restart — this process will die here
        subprocess.Popen(
            ["systemctl", "--user", "restart", "zipper"],
            close_fds=True,
            start_new_session=True,
        )

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
