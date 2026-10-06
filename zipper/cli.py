"""zipper.cli

The command line. Every subcommand is `fn=<module>.cmd_*`, so this file is the
only place that knows the whole surface -- and the only place to look when you
want to know what zipper can do.
"""
import argparse

from . import (canvas, chat, conversations, decisions, digest, events, ext, gh, ghapp,
               google, hours, ics, lint, metrics, runqueue, settings, status, sync, views)


def _pass_cmd(a):
    from . import scheduled
    return scheduled.cmd_pass(a)


def _mod(module, name):
    """Import on use: `run` and `setup` pull in more than a quick `lint` needs.
    `module` is under zipper/ unless it starts with `plugins.`."""
    def run(a):
        import importlib
        from . import plugins      # puts the checkout on sys.path for `plugins.*`
        path = module if module.startswith('plugins.') else 'zipper.' + module
        return getattr(importlib.import_module(path), name)(a)
    return run


def _needs(plugins_, fn):
    """A command that belongs to a plugin: refuse, with the line that turns it on,
    while none of `plugins_` is enabled."""
    names = (plugins_,) if isinstance(plugins_, str) else plugins_

    def run(a):
        from . import plugins
        if not any(plugins.is_enabled(n) for n in names):
            print(plugins.require(names[0]))
            return 1
        return fn(a)
    return run


def _setup(name):
    return _mod('setup', name)


def _github_cmd(a):
    from plugins import github
    return github.cmd(a)


def main():
    ap = argparse.ArgumentParser(prog='zipper', description='vault command line')
    sub = ap.add_subparsers(dest='cmd')

    s = sub.add_parser('today');   s.add_argument('--date'); s.set_defaults(fn=sync.cmd_today)
    s = sub.add_parser('lint');    s.set_defaults(fn=lint.cmd_lint)
    s = sub.add_parser('status');  s.set_defaults(fn=status.cmd_status)
    s = sub.add_parser('sync');    s.set_defaults(fn=sync.cmd_sync)
    s = sub.add_parser('metrics'); s.set_defaults(fn=metrics.cmd_metrics)

    s = sub.add_parser('touch'); s.add_argument('note'); s.add_argument('--date')
    s.set_defaults(fn=sync.cmd_touch)

    s = sub.add_parser('metric'); s.add_argument('key'); s.add_argument('value')
    s.add_argument('--date'); s.add_argument('--note'); s.set_defaults(fn=metrics.cmd_metric)

    s = sub.add_parser('decide'); s.add_argument('title'); s.add_argument('--date')
    s.add_argument('--review-days', type=int, default=90); s.set_defaults(fn=decisions.cmd_decide)

    s = sub.add_parser('ingest-ics'); s.add_argument('source')
    s.add_argument('--match', default=None,
                   help='regex; keep only events whose summary matches')
    s.add_argument('--label', default='calendar'); s.set_defaults(fn=_needs(('calendar', 'canvas'), ics.cmd_ingest_ics))
    sub.add_parser('calendars').set_defaults(fn=_needs(('calendar', 'canvas'), ics.cmd_calendars))

    # The timesheet. `add` captures; the sheet stays the system of record and
    # the extension reconciles the two, so nothing here submits anything.
    s = sub.add_parser('google', help='the Google account behind the timesheet')
    s.add_argument('--auth', action='store_true', help='print the consent link')
    s.set_defaults(fn=_needs(('hours', 'gmail'), google.cmd_google))

    s = sub.add_parser('gmail', help='recent mail the queue saw, or read one message')
    s.add_argument('id', nargs='?', help='a message id from a queue row (#...)')
    s.add_argument('--limit', type=int, default=15)
    s.set_defaults(fn=_needs('gmail', _mod('plugins.gmail', 'cmd_gmail')))

    s = sub.add_parser('hours', help='the timesheet ledger')
    hs = s.add_subparsers(dest='action')
    s.set_defaults(fn=_needs('hours', hours.cmd_hours))
    a1 = hs.add_parser('add'); a1.add_argument('--date', required=True)
    a1.add_argument('--start'); a1.add_argument('--end')
    a1.add_argument('--hours', type=float); a1.add_argument('--note', default='')
    a1.add_argument('--source', default='cli'); a1.set_defaults(fn=_needs('hours', hours.cmd_hours))
    a2 = hs.add_parser('rm'); a2.add_argument('key'); a2.set_defaults(fn=_needs('hours', hours.cmd_hours))
    a3 = hs.add_parser('import'); a3.add_argument('csvfile'); a3.set_defaults(fn=_needs('hours', hours.cmd_hours))
    a4 = hs.add_parser('pull', help='read the sheet; it wins')
    a4.set_defaults(fn=_needs('hours', hours.cmd_hours))
    a6 = hs.add_parser('week', help="open the next `Week N` block in the tab")
    a6.add_argument('--date', help='any date in the week; default today')
    a6.add_argument('--dry-run', action='store_true')
    a6.set_defaults(fn=_needs('hours', hours.cmd_hours))
    a5 = hs.add_parser('push', help='write pending entries into the sheet')
    a5.add_argument('--dry-run', action='store_true')
    a5.set_defaults(fn=_needs('hours', hours.cmd_hours))

    s = sub.add_parser('ingest-budget'); s.add_argument('csvfile')
    s.set_defaults(fn=metrics.cmd_ingest_budget)

    s = sub.add_parser('agenda'); s.add_argument('--days', type=int, default=14)
    s.set_defaults(fn=ics.cmd_agenda)

    s = sub.add_parser('github'); s.add_argument('--since-days', type=int, default=30)
    s.add_argument('--full', action='store_true'); s.set_defaults(fn=_needs('github', _github_cmd))

    # A bookkeeping pass is fetch -> reasoning -> commit. Only the ends are
    # commands; the middle is an agent reading the brief against the vault, so
    # there is deliberately no `bookkeep` subcommand to imply otherwise.
    s = sub.add_parser('pull', help='pull plugins (named, --due, or all), then write the brief')
    s.add_argument('names', nargs='*'); s.add_argument('--due', action='store_true',
                   help='only plugins whose own timer has run out')
    s.add_argument('--days', type=int, default=14); s.set_defaults(fn=runqueue.cmd_pull)
    s = sub.add_parser('fetch', help='(old) every pulling plugin, then the brief')
    s.add_argument('--days', type=int, default=14); s.set_defaults(fn=runqueue.cmd_fetch)
    s = sub.add_parser('brief', help='rewrite the brief without pulling anything')
    s.add_argument('--days', type=int, default=14); s.set_defaults(fn=runqueue.cmd_brief)
    s = sub.add_parser('commit', help='close a pass: tick every event, commit the notes')
    s.add_argument('message')
    s.add_argument('--force', action='store_true',
                   help='commit even while another conversation is live')
    s.set_defaults(fn=runqueue.cmd_commit)
    s = sub.add_parser('queue', help='deprecated spelling of fetch')
    s.add_argument('--days', type=int, default=14)
    s.set_defaults(fn=runqueue.cmd_queue)
    s = sub.add_parser('views');  s.set_defaults(fn=views.cmd_views)
    s = sub.add_parser('discord', help='talk to the always-on Discord bot')
    s.add_argument('action', choices=['send', 'read', 'status'])
    s.add_argument('text', nargs='?', default='')
    s.add_argument('--file', help='path to attach')
    s.add_argument('--thread', help='thread id; default is the main channel')
    s.add_argument('--limit', type=int, default=5)
    s.set_defaults(fn=chat.cmd_discord)
    # The evening reminder. Reads the dashboard's own ranking and posts it to
    # Discord; --dry-run is how you look at one without sending it.
    s = sub.add_parser('pass', help='fetch, and if the brief has anything, run a bookkeeping pass with Claude')
    s.add_argument('--dry-run', action='store_true', help='run the pass but never post to Discord')
    s.add_argument('--force', action='store_true', help='run even when the brief is empty')
    s.add_argument('--timeout', type=int, default=1800, help='seconds to allow Claude')
    s.set_defaults(fn=_needs('passes', _pass_cmd))
    s = sub.add_parser('digest', help='post the evening what-is-due message to Discord')
    s.add_argument('--days', type=int, default=7, help='how far past tomorrow to look ahead')
    s.add_argument('--dry-run', action='store_true', help='print it instead of sending')
    s.add_argument('--force', action='store_true', help='send even if one already went today')
    s.add_argument('--thread', help='thread id; default is the main channel')
    s.set_defaults(fn=_needs('digest', digest.cmd_digest))

    # No --days and no fetch: the browser extension takes the reading, this
    # reports it. --file still ingests a saved planner dump.
    s = sub.add_parser('canvas'); s.add_argument('--file')
    s.set_defaults(fn=_needs('canvas', canvas.cmd_canvas))
    s = sub.add_parser('conversations'); s.add_argument('--close', metavar='THREAD')
    s.add_argument('--force', action='store_true',
                   help='close even a bound conversation -- kills a live terminal')
    s.set_defaults(fn=conversations.cmd_conversations)
    s = sub.add_parser('score'); s.add_argument('--window', type=int, default=30)
    s.add_argument('--force', action='store_true'); s.set_defaults(fn=metrics.cmd_score)

    s = sub.add_parser('event'); s.add_argument('match')
    s.add_argument('--date'); s.add_argument('--about'); s.add_argument('--why')
    s.set_defaults(fn=events.cmd_event)
    s = sub.add_parser('events'); s.add_argument('--pending', action='store_true')
    s.set_defaults(fn=events.cmd_events)

    s = sub.add_parser('inspect'); s.add_argument('repos', nargs='*')
    s.add_argument('--limit', type=int, default=12)
    s.add_argument('--readme-chars', type=int, default=6000)
    s.set_defaults(fn=_needs('github', gh.cmd_inspect))

    s = sub.add_parser('ghapp', help='the bot identity: show it, mint a token, push as it')
    s.add_argument('--push', action='store_true', help='push a repo as the App')
    s.add_argument('--repo', help='which repo to push (default: this checkout)')
    s.add_argument('--token', dest='print_token', action='store_true',
                   help='print a raw installation token')
    s.set_defaults(fn=ghapp.cmd_ghapp)

    s = sub.add_parser('init', help='stand up a zipper in a folder: vault, config, backup, compose')
    s.add_argument('path'); s.add_argument('--owner'); s.add_argument('--id')
    s.add_argument('--local', action='store_true',
                   help='this is your own computer: the dashboard on 127.0.0.1, even over SSH')
    s.add_argument('--address', help='publish the dashboard on this address instead')
    s.add_argument('--no-start', action='store_true', help='make the folder, start nothing')
    s.add_argument('--vault-only', action='store_true',
                   help='only make a vault at PATH (what a container or a test wants)')
    s.add_argument('--backup', help='a bare git repo, outside the vault, pushed on every commit')
    s.add_argument('--starter', action='store_true',
                   help='add a suggested layout: folders, a schema, Obsidian pages')
    s.set_defaults(fn=_setup('cmd_init'))
    s = sub.add_parser('setup', help='the setup guide in CLAUDE.md, and the Stop hook')
    s.add_argument('--hook', action='store_true', help='(the default; kept for old scripts)')
    ss = s.add_subparsers(dest='action')
    ss.add_parser('hook', help="point Claude Code's Stop hook at this checkout")
    ss.add_parser('remaining', help='setup sections still in CLAUDE.md')
    ss.add_parser('starter', help='add the suggested layout to this vault; never overwrites')
    g = ss.add_parser('done', help='remove a finished section; with none left, the guide')
    g.add_argument('section', nargs='?')
    s.set_defaults(fn=_setup('cmd_setup'))
    s = sub.add_parser('plugin', help='list, describe, enable or disable plugins')
    ps = s.add_subparsers(dest='action')
    ps.add_parser('list')
    for act in ('info', 'enable', 'disable'):
        g = ps.add_parser(act); g.add_argument('name')
    s.set_defaults(fn=_mod('plugins', 'cmd_plugin'))

    s = sub.add_parser('run', help='supervise the dashboard, bot and schedule (containers)')
    s.add_argument('--no-schedule', action='store_true', help='run the services only')
    s.set_defaults(fn=_mod('supervise', 'cmd_run'))
    s = sub.add_parser('restart', help='reload this zipper\'s code; conversations survive')
    s.add_argument('--when-idle', action='store_true',
                   help='wait until no turn is running (use from inside a conversation)')
    s.set_defaults(fn=_mod('supervise', 'cmd_restart'))
    s = sub.add_parser('_when_idle')    # internal: the detached waiter
    s.add_argument('argv', nargs=argparse.REMAINDER)
    s.set_defaults(fn=_mod('supervise', 'cmd_when_idle'))

    s = sub.add_parser('host', help='ask the host daemon: status, services, approved root commands')
    s.add_argument('verb'); s.add_argument('args', nargs=argparse.REMAINDER)
    s.set_defaults(fn=_needs('host', _mod('plugins.host', 'cmd_host')))
    s = sub.add_parser('hostd', help='the host daemon itself (as root, on the host)')
    s.add_argument('action', choices=['init', 'install', 'serve'])
    s.set_defaults(fn=_mod('plugins.host.hostd', 'cmd_hostd'))

    s = sub.add_parser('code', help='propose a change to the shared code as a pull request')
    cs = s.add_subparsers(dest='action', required=True)
    g = cs.add_parser('start'); g.add_argument('slug')
    g = cs.add_parser('propose'); g.add_argument('title'); g.add_argument('--body')
    g.add_argument('--draft', action='store_true'); g.add_argument('--path')
    cs.add_parser('prs')
    s.set_defaults(fn=_mod('code', 'cmd_code'))
    g = cs.add_parser('review', help='test the change; merge and restart if the tester approves')
    g.add_argument('title'); g.add_argument('purpose')
    g.add_argument('--test', help='how to test it'); g.add_argument('--path')
    g.set_defaults(fn=_mod('review', 'cmd_review'))
    g = cs.add_parser('_review'); g.add_argument('job')
    g.set_defaults(fn=_mod('review', 'cmd_run_review'))
    s = sub.add_parser('update', help='take merged changes: pull, check, restart, or roll back')
    s.add_argument('--check', action='store_true', help='only say what is new')
    s.add_argument('--force', action='store_true', help='even with conversations live')
    s.add_argument('--finish', nargs=2, metavar=('OLD', 'NEW'), help=argparse.SUPPRESS)
    s.set_defaults(fn=_mod('code', 'cmd_update'))

    s = sub.add_parser('msg', help='send a message to another zipper')
    s.add_argument('peer'); s.add_argument('text')
    s.add_argument('--file', action='append', help='attach a file (repeatable; 40 MB together)')
    s.set_defaults(fn=_needs('peers', _mod('plugins.peers', 'cmd_msg')))

    s = sub.add_parser('topic', help='standing jobs for Claude: fresh sessions that carry a condensed context')
    s.add_argument('action', choices=['list', 'show', 'run', 'add', 'rm', '_exec'])
    s.add_argument('name', nargs='?')
    s.add_argument('--every', type=int, help='add: minutes between runs')
    s.add_argument('--gate', help='add: shell command in the topic folder; non-zero skips the run')
    s.add_argument('--wake-on', action='append', help='add: a peer zipper whose messages start a run at once (repeatable)')
    s.add_argument('--timeout', type=int, help='add: minutes a run may take (default 30)')
    s.add_argument('--model', help='add: the model a run uses')
    s.add_argument('--channel', help='add: the Discord channel id its threads open in (its own; required for a new topic)')
    s.add_argument('--force', action='store_true', help='run: ignore the gate')
    s.add_argument('--wait', action='store_true', help='run: in the foreground, not detached')
    s.add_argument('--limit', type=int, default=5, help='show: how many runs')
    s.set_defaults(fn=_needs('topics', _mod('plugins.topics', 'cmd_topic')))

    s = sub.add_parser('secret', help='put a secret into .env without it passing through a chat')
    s.add_argument('name'); s.add_argument('--check', action='store_true', help='say whether it is set')
    s.add_argument('--tty', action='store_true', help='hidden prompt in this terminal instead of a link')
    s.add_argument('--host', default='127.0.0.1', help='address the page listens on (default loopback)')
    s.set_defaults(fn=_mod('secret', 'cmd_secret'))
    s = sub.add_parser('_secret_page')      # internal: the detached page
    for x in ('name', 'host', 'port', 'token'):
        s.add_argument(x)
    s.set_defaults(fn=_mod('secret', 'cmd_secret_page'))

    s = sub.add_parser('settings', help="show or change the vault's settings.json (never secrets)")
    ss = s.add_subparsers(dest='action')
    ss.add_parser('show'); ss.add_parser('path'); ss.add_parser('check')
    g = ss.add_parser('migrate', help='move the non-secret keys of .env into the file')
    g.add_argument('--env', help='a .env to read; default: this checkout\'s')
    g = ss.add_parser('get'); g.add_argument('key')
    g = ss.add_parser('set'); g.add_argument('key'); g.add_argument('value')
    s.set_defaults(fn=settings.cmd_settings)

    s = sub.add_parser('ext', help='build, sign and publish the browser extension')
    s.add_argument('--build', action='store_true', help='sign a new version')
    s.add_argument('--bump', choices=['major', 'minor', 'patch'], default='patch')
    s.add_argument('--set-version', dest='set_version', metavar='X.Y.Z')
    s.add_argument('--clean', action='store_true',
                   help='put the update_url placeholder back after a failed sign')
    s.set_defaults(fn=_needs('canvas', ext.cmd_ext))

    a = ap.parse_args()
    if not getattr(a, 'fn', None):
        ap.print_help(); return 0
    return a.fn(a)
