"""zipper.extras

The claude.ai connectors (Gmail, Drive, Calendar, Figma, Docs) and the synced
plugins (productivity, engineering), switched on for one conversation.

**Off by default, in Claude's user settings** (`disableClaudeAiConnectors`,
`enabledPlugins: {"<name>@synced": false}` in `~/.claude/settings.json`).
Together they were about 8k tokens of tool names, server instructions and
skill descriptions at the head of every conversation -- a quarter of the
prefix -- and almost no turn used them. A conversation that needs them opts
in, and its Claude process is started with `--settings` carrying the reverse.

The flag lives on the conversation's registry row, so it outlives a restart.
A Discord turn is its own `claude -p` process, so the switch takes effect on
the **next message**; a pane has one long-lived process and needs reopening.
"""
import json
import os

from . import convcore

PLUGINS = ('productivity@synced', 'engineering@synced')
OVERLAY = json.dumps({'disableClaudeAiConnectors': False,
                      'enabledPlugins': {p: True for p in PLUGINS}},
                     separators=(',', ':'))


def enabled(thread_id):
    return bool((convcore.load().get(str(thread_id)) or {}).get('extras'))


def flags(thread_id):
    """Extra `claude` arguments for this conversation: none unless opted in."""
    return ['--settings', OVERLAY] if enabled(thread_id) else []


def _current():
    return os.environ.get('ZIPPER_CONVERSATION') or os.environ.get('ZIPPER_DISCORD_THREAD')


def cmd_extras(a):
    thread = a.thread or _current()
    if not thread:
        print('no conversation: pass --thread, or run this from inside one')
        return 1
    if a.action == 'status':
        print('extras %s for %s' % ('on' if enabled(thread) else 'off', thread))
        return 0
    on = a.action == 'on'
    convcore.touch(thread, extras=on)
    headless = not str(thread).startswith('local-')
    print('extras %s for %s -- %s' % (
        'on' if on else 'off', thread,
        'from the next message' if headless else 'reopen the pane to apply'))
    return 0
