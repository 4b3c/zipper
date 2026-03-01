import os
import subprocess
import sys


def run(args: dict, conversation_id: str) -> str:
    mode = args.get("mode", "zipper")

    if mode == "zipper":
        if not conversation_id:
            return "error: no conversation_id available, cannot resume after restart"

        project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        watcher = os.path.join(project_dir, "restart_watcher.py")

        # spawn watcher detached so it survives the restart
        subprocess.Popen(
            [sys.executable, watcher, conversation_id, project_dir],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=True,
        )

        # trigger the restart — this process will die here
        subprocess.Popen(
            ["systemctl", "--user", "restart", "zipper"],
            close_fds=True,
            start_new_session=True,
        )

        return "restarting..."

    if mode == "discord":
        result = subprocess.run(
            ["docker", "compose", "-f", "docker-compose.discord.yml", "restart"],
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            capture_output=True,
            text=True,
            timeout=60,
        )
        output = (result.stdout + result.stderr).strip()
        if result.returncode != 0:
            return f"error restarting discord bot:\n{output}"
        return f"discord bot restarted\n{output}".strip()

    if mode == "dashboard":
        return "dashboard not yet implemented"

    return f"error: unknown mode: {mode}"
