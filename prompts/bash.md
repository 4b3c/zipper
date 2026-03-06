## Shell Environment

- **Shell:** `/bin/bash`
- **User:** `root`
- **Home:** `/root`
- **Working directory:** `/opt/zipper/app` (always — bash does not persist `cd` between calls)
- **OS:** Ubuntu 24.04, Linux 6.17, x86_64

## Runtimes

- **Python:** 3.13 at `/opt/zipper/app/.venv/bin/python` — always use the venv, not system python
- **pip:** `/opt/zipper/app/.venv/bin/pip` — use this, not pip3 (not on PATH)
- **Node:** v20 at `node`
- **git:** 2.51

## Package Management

- System packages: `apt install -y <pkg>`
- Python packages: `/opt/zipper/app/.venv/bin/pip install <pkg>`

## Systemd Services

All run as system services (`systemctl`):
- `zipper` — the main FastAPI process (port 4199)
- `zipper-discord` — the Discord bot (port 4200)
- `zipper-dashboard` — the web dashboard (port 4201)

Useful commands:
```
systemctl status zipper
journalctl -u zipper -n 50 --no-pager
journalctl -u zipper-discord -n 50 --no-pager
journalctl -u zipper-dashboard -n 50 --no-pager
```

Do not restart `zipper` directly — use the `restart` tool so the watchdog can resume the conversation.

## Rules

- No interactive sessions (vim, top, python REPL, ssh)
- Long-running commands: `nohup cmd > /tmp/out.log 2>&1 &` then poll with `tail /tmp/out.log`
- For reading or editing source files, use the `file` tool instead
- Timeout defaults to 30s — pass `timeout=N` for longer operations
