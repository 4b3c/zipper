import os
import subprocess
import sys


def run(args: dict, conversation_id: str) -> str:
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

    # return value never reaches the caller, but keeps the signature clean
    return "restarting..."
