# Zipper

Zipper is a personal assistant that keeps a folder of notes about your life true. You talk to
it on Discord; Claude reads and edits your notes, answers from them, and tells you when what
you wrote down and what actually happened disagree.

**At its core it is small:** a Discord bot handing each message to a Claude conversation that
works in your **vault** — a folder of markdown files in git. That's the whole requirement: a
Discord server with a bot, a Claude account, and a folder. It runs in a container.

**Everything else is a plugin** you switch on: reading GitHub, calendars, Canvas or a
timesheet; a dashboard; twice-daily bookkeeping passes; an evening digest; backups; messages
between zippers. Several people can each run their own zipper on one machine, all on the same
code, and every change to that code is a pull request a person approves.

## What it does

- **Keeps notes current.** Push code, submit an assignment, move a meeting, and the notes that
  mention it get updated. Plugins bring the news; Claude decides what it means.
- **Tells you when a note is wrong.** A project called active that nobody has touched in 45
  days, work you did but never wrote down, a review date that passed.
- **Answers from your notes.** "What did I decide about X?", "what am I behind on?"
- **Handles small admin.** "Worked 1:30 to 9:30" becomes a timesheet row.
- **Shows your day** on a dashboard of cards you choose — built in, from a plugin, or written
  in your own vault.
- **Stays yours.** This repository is only code; your notes and everything personal stay in
  your vault, and secrets stay out of both.

---

## How the pieces fit

```
  Discord ── bot/ ──► zipper.serve (relay) ──► a Claude conversation per thread
                           │                          │ reads, argues, edits
                           │                          ▼
  plugins/, each on its    ├─ the queue ◀── git ── the vault (markdown in git)
  own timer: GitHub,       │   (Inbox/feed.json,       notes · settings.json
  calendars, Canvas,  ─────┘    Meta/Queue.md)         Dashboard/ (your cards)
  hours, passes, digest…   │
                           └─ the dashboard plugin: rows of cards (owns no data)
```

**The division of labour is the design.** The engine writes what's verifiable: a push date,
a commit count, a diff. It never decides a project is dormant — that's a judgment about
someone's life. Claude's job is to surface the contradiction and argue about it, not resolve
it quietly.

---

## The vault

**A vault is a folder of markdown files in a git repository.** That is the whole
requirement. `zipper init <path>` makes one: a `.git`, a `.gitignore`, and a `CLAUDE.md` that
tells the agent how to work in it.

**Git is load-bearing.** The engine is pointed at the folder, and every edit since the last
commit becomes a `vault` row in the queue — that is how one conversation's changes reach the
next. `zipper commit` ends a pass and pushes the vault's backup remote. Whatever the engine
regenerates is gitignored, so it never looks like an edit.

**There are no required folders.** The engine owns four things and makes them on first use:
`Inbox/` (machine state, gitignored), the generated views in `Meta/` (gitignored), `Log/`
(daily notes, whose `[[links]]` count as evidence of work) and `Metrics/metrics.csv`. A few
commands write to conventional places — `Decisions/`, `Events/`, `Tasks/` — which appear when
used. Everything else is the owner's to arrange. `zipper init --starter` adds one suggested
layout (Projects, Areas, Topics, People, Tasks, a schema, a few query pages); it is a
starting point, not a contract.

**Viewing and syncing are optional and outside the system.** Any editor reads markdown.
Obsidian is a good viewer, and the starter's query pages use its Dataview plugin. Getting the
folder onto other devices — CouchDB with LiveSync, iCloud, Syncthing — is up to the owner;
nothing here depends on it.

Every note has YAML frontmatter with at least `type` and `status`. `zipper lint` is the
authority; the allowed values are in `zipper/core.py`.

A project note:

```yaml
type: project
status: active            # active dormant handing-off shipped retired archived idea
stage: building           # idea → designing → building → shipped → selling → verifying → closed
last_touched: 2026-09     # evidence of activity; only moves forward
last_push: 2026-09-03     # written by `zipper github`
commits_recent: 8         # written by `zipper github`
commits_mine: 3           # on an org repo: yours, where commits_recent is the team's
repos: [my-app, some-org/team-repo]      # hand-maintained; first is primary
revenue_to_date: 0        # 0 is a fact; blank is invisible to queries
revenue_intent: true      # ventures vs builds
next_action: email three coffee shops the demo link   # ONE physical action
blocked_by: waiting on the API token
review: 2026-12-01
status_verified: 2026-09-01   # "this status is right despite the evidence"
```

- **`next_action` is an action, not a goal.** "Land a B2B customer" is a wish; "email three
  coffee shops the demo link" can be done before lunch.
- **`status_verified` has a fuse.** It silences the stale-status flag, and the drift flag for
  45 days only. `stall_days_max` keeps counting regardless.
- **Repo↔note mapping is by hand.** Guessing by name was removed: a wrong mapping moves
  `last_touched` and erases the drift the flags exist to catch. Unmapped is honest.

---

## Commands

```bash
export ZIPPER_VAULT=/absolute/path/to/vault
python3 -m zipper <command>          # --help lists everything
```

| Command | Does |
|---|---|
| `pull [plugin…] [--due]` | Pull plugins — named, due by their own timers, or all — then sync, agenda, status, views and the brief. Each plugin also pulls on its own `poll_minutes` |
| `brief` | Re-render the brief without pulling |
| `commit "<msg>" [--force]` | Ends a pass: ticks the queue, commits the notes, fails if the tree is dirty after |
| `github [--full]` | Repos + commits → `last_push`, `commits_*`, `last_touched`, `Meta/Repos.md` |
| `inspect [repos]` | READMEs + 40 commits → `Inbox/repo-details.json`, so a note can be written from source |
| `ingest-ics <url> --label X` / `calendars` | Add an ICS feed (a URL is remembered) / refetch all |
| `canvas [--file]` | Report what the browser extension last sent |
| `ingest-budget <csv>` | Monthly totals only; transactions never enter the vault |
| `sync` | `[[links]]` in `Log/` move `last_touched` forward |
| `today` / `touch <Note>` | Today's log note / bump `last_touched` by hand |
| `metric` / `metrics` / `score` | Append a number / print trends / compute execution metrics |
| `decide "<title>"` / `event "<summary>"` / `events` | Scaffold a decision / an event note / list event notes |
| `status` / `agenda` / `views` | Regenerate the snapshot / the agenda / the saved queries |
| `hours …` | A timesheet ledger, pushed to Google Sheets |
| `pass` | Pull what is due, and if anything's in the brief, have Claude do the pass; Discord only if something needs you |
| `digest` | Evening what's-due message |
| `discord send\|read\|status` | Talk through the relay |
| `conversations` | List live Claude conversations |
| `ghapp` / `ext` | The bot's GitHub identity / build and sign the extension |
| `lint` | Validate all frontmatter. **Run before finishing** |
| `init <path>` / `setup done\|remaining` | Make a whole zipper (vault with a setup guide, config, backup, compose) / work through the guide |
| `plugin list\|info\|enable\|disable` | Which plugins are on |
| `settings get\|set\|check\|migrate` / `secret NAME` | The vault's `settings.json` / put a secret in `.env` through a one-time page |
| `run` / `restart [--when-idle]` | Supervise the relay, bot, dashboard and timers (containers) / reload the code without cutting a turn off |
| `code start\|propose\|prs` / `update` | Propose a change to the shared code as a PR / take merged changes, rolling back if they break |
| `msg <zipper> "text"` / `host <verb>` | Message another zipper (peers plugin) / ask the host daemon (host plugin) |

---

## A pass: pull → reasoning → commit

Only the two ends are code. `pull` (what is due) writes `Meta/Queue.md`, the brief, in three parts:

1. **The queue** — `Inbox/feed.json`: events from outside the vault (a push, a submission, a
   calendar change), each with a `target` note. Cleared by `--mark` or `commit`.
2. **Uncommitted note edits** — from `git status`. Git is the baseline; committing clears it.
3. **The flags** — re-derived every run, never tickable:
   - repo pushed but never logged
   - active with no `last_touched`
   - active but untouched 45+ days
   - dormant/idea/archived but pushed recently
   - past its `review` date
   - task left the list unfinished
   - event needs a debrief — the one that wants an answer, not an edit
   - event moved / event note matches nothing on the calendar

**A flag says something is inconsistent, not which side is wrong.** Investigate first.

Diff rows group by identity: a weekly class expanded to a semester is one row, *(weekly ×58,
through 2027-10-06)*, not 58 — and a series running a year past term is visibly a mistake.

---

## Views and metrics

21 saved queries, computed into `Inbox/views.json` and served at `/views/<page>` (`now`,
`ventures`, `school`, `drift`). Every view is title + columns + rows, so a new one is a
function returning rows and costs nothing in the renderer.

`zipper score` writes metrics built to be **hard to game**, since the person they describe
could inflate them:

| Metric | Defends against |
|---|---|
| `stall_days_max`, `stall_days_median` | Suppressed flags — keeps counting |
| `hard_closes` (open ≥14 days), `quick_closes` (≤1 day) | Closing trivia to feel productive |
| `tasks_dropped` | Work that left the list unfinished |
| `tasks_open`, `tasks_overdue` | Missed self-set dates |
| `projects_active`, `projects_drifting` | Calling six things active while touching two |

---

## The dashboard

```bash
python3 -m zipper.serve --port 8800 [--host ADDR] [--daemon] [--open]
```

The dashboard is rows of cards, listed in the vault's `settings.json` under
`plugins.dashboard.rows`. A card comes from one of three places:

- **Built in** (`dashboard:<name>`): the queue, Claude (every conversation in its own
  terminal), the week and the day as a time grid, the Zipper panel (metrics, plan usage,
  the machine), sources (how old each plugin's data is, with a refresh button each), and a
  to-do card for the checkboxes in any markdown file.
- **Brought by a plugin** (`<plugin>:<name>`), while it is on — e.g. `canvas:week`, with
  its own refresh button.
- **Written in the vault** (`vault:<name>`): `Dashboard/<name>/backend.py`, and optionally
  `frontend.py`. This is where a card built on one person's notes lives, never in this
  repository.

A card's backend has `data(ctx)`; its frontend `render(data, ctx, ui)` draws with the same
pieces as everything else. Buttons are read-write: `ui.button(action, ...)` calls the
backend's `act_<action>(args, ctx)`. **A broken card only breaks itself**: an error is drawn
in its frame, and a card slower than four seconds is skipped for a minute. See
`template/vault/Dashboard/README.md` and the `recent` example a new vault starts with.

**It owns no data** — every panel reads what the engine wrote. **It never fetches on
launch** — each plugin pulls on its own timer — and sources publish over SSE as they land. **Looking is
free**: no Claude session starts until you press a button. Details: `zipper/README.md`.

## Discord

The relay is the door when you're away from a screen. A message in the channel opens a
thread, and each thread is its own headless Claude conversation (`claude -p`, resumed from
disk after idling). The reply is forwarded by Claude Code's `Stop` hook when the turn came
from Discord. The engine never imports `discord`, so a cron job can post without owning a
socket:

```bash
python3 -m zipper discord send "the build finished"
python3 -m zipper discord send "results" --file report.html
```

## The browser extension

`extension/` reads what only a logged-in browser can — Canvas submission status — and POSTs it
to the dashboard. It also draws the week's coursework back into Canvas. **It collects and
renders; it never concludes.** See `extension/README.md`.

---

## Running it

You need three things: **Docker**, **Claude Code** (`claude`), and a **Discord server with a
bot** (the setup explains how to make one). Then:

```bash
git clone https://github.com/4b3c/Zipper && cd Zipper
./bin/zipper init ~/zipper          # the vault, its config and backup, a compose file
cd ~/zipper/vault && claude         # and say "set me up"
```

`init` makes one folder per zipper:

- `vault/` — the notes (a git repository). Its first `CLAUDE.md` is a **setup guide**:
  Claude walks through it with you — your name, the Discord bot, how the zipper's own Claude
  logs in, then each plugin: what it does, whether you want it, and its settings — and
  removes each section as it is done, until only the everyday rules are left.
- `vault/settings.json` — the one settings file: plugins, their timers, the dashboard's cards.
- `config/` — `.env`: secrets only, never in the vault.
- `backup/` — a second copy of the vault, pushed on every commit.
- `compose.yml`, and `./zipper`: this zipper's command on this machine.

**Secrets never go through the chat.** `zipper secret NAME` prints a one-time link to a
page with a single password field; the value goes straight into `.env` and Claude only
learns that it was saved (`--tty` asks in a terminal instead).

The last step of the guide starts it (`docker compose up -d --build`) and has you send a
first message on Discord. Plugins can be changed any time after: ask the zipper, or
`zipper plugin enable|disable <name>`.

**Several people on one machine:** one `init` per person, each with its own `--id`, or
`compose.example.yml` for all of them in one file. Zippers on a shared Docker network can
message each other through the peers plugin (port 8898, `/api/msg` only).

**Without Docker:** `pip install -r requirements.txt` (plus git, tmux, ttyd and `claude`),
then `zipper run` — or the systemd units in `deploy/`.

### The host

`zipper hostd` is a small root daemon on the host that zippers reach through a socket
mounted into their container: status and logs freely, restarts of their own services,
anything else only on a code from the operator's authenticator app, posted with the exact
command through a webhook the containers cannot see. `zipper hostd init`, then `install`.

### Deployment (systemd)

`deploy/` has systemd units and an nginx vhost.

```
zipper-web.service       dashboard, views, POST /discord
zipper-discord.service   the gateway connection (run with python3 -u)
zipper-fetch.timer       every 5 minutes: `zipper pull --due`
zipper-pass.timer        09:00 and 21:00 bookkeeping pass
zipper-digest.timer      19:00 digest
```

- **`zipper-web.service` needs `KillMode=process`.** Otherwise a restart kills ttyd and tmux
  with it, and every open conversation. Check `tmux ls` after a restart.
- **Bind to loopback or a tailnet address, never `0.0.0.0`.** Nothing is authenticated, and
  it can start a shell.

---

## Design rules

- **The vault holds conclusions, not caches.** Extract the durable part; leave the original
  where it lives. External views hold pointers, never bodies. A dead pointer is honest; a
  stale copy lies.
- **The engine writes facts, never judgments.**
- **Evidence moves dates forward, never back.** Deferring a project isn't touching it.
- **A commit is evidence of activity, not completion.** It may move `last_touched`; it never
  ticks a box.
- **Three tiers of truth:** externally verified (Canvas submissions), evidence-driven (repos —
  freshness, never completion), and manual (the only place a checkbox is authoritative).
- **Looking must be free.**
- **Generated files are never hand-edited.**
- **A button that can't do the thing mustn't offer to.**

## Traps

- **A `[[link]]` in a log is evidence**, and `sync` can't tell a plan from a record. Links
  under "Tomorrow", or naming a project as something that *didn't* happen, both moved
  `last_touched` and erased real drift. Link only what was worked on.
- **External times are UTC; the vault is local.** Convert, never slice.
- **On an org repo only `commits_mine` moves `last_touched`.**
- **Only the default branch is counted.** Side-branch work reads as zero — known, left alone.
- **A bad `ingest-ics` overwrites a calendar with no undo** (`Inbox/` is gitignored). Check a
  known event.
- **A page reload doesn't load new Python.** Restart the service.
- **Verify UI in a browser**, not the HTML string.
- **`el.hidden` needs `[hidden]{display:none!important}`** — author `display` rules outrank it.

---

## Layout

```
zipper/        the engine and the dashboard — see zipper/README.md
  web/         the dashboard server, one module per concern, importing one way:
               base → data → feed → conv → css/js → render/home → http
bot/           the Discord relay
utils/         the bot's two helper modules
hooks/         forward_reply.py, the Stop hook
extension/     the browser collector
deploy/        systemd units, nginx vhost
HISTORY.md     what was removed, and why
CLAUDE.md      how an agent works in this repo
```

**Not here: the vault.** The code is public; the notes aren't, and that split is the point.
Anything host-specific lives in settings or `.env`, never in the tree. A new vault starts
from `template/vault/` (`zipper init`).

## Licence

MIT. See `LICENSE`.
