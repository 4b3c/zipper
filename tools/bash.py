import subprocess


def run(args: dict) -> str:
    command = args["command"]
    timeout = args.get("timeout", 30)

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            output += result.stderr
        if result.returncode != 0:
            output += f"\nexit code: {result.returncode}"
        output = output.strip() or "ok"
        if len(output) > 10000:
            output = output[:10000] + f"\n... [truncated, {len(output)} chars total]"
        return output
    except subprocess.TimeoutExpired:
        return f"error: command timed out after {timeout}s"
    except Exception as e:
        return f"error: {e}"
