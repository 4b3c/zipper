# Zipper

Zipper is a personal assistant that runs on your own server. It keeps a folder of markdown
notes about your projects, classes and plans, and updates them from wherever it can read
your activity — right now that's your calendar, Canvas, GitHub and a work timesheet, and more
can be added. You can ask it things over Discord or a web dashboard.

## What it does

- Shows everything that's due in one list: assignments, meetings and your own tasks. You see
  it on the dashboard, in a Discord message every evening, and in place of Canvas's to-do list.
- Updates your notes when something changes. If you push code, submit an assignment or a
  meeting moves, the notes that mention it get updated.
- Tells you when a note is wrong. If a project says it's active but nothing has happened in
  45 days, you hear about it. Same if you did work and never wrote it down.
- Answers questions from your notes, like "what did I decide about X?" or "what am I behind
  on?"
- Handles small admin. Text it "worked 1:30 to 9:30" and it adds the row to your timesheet.
- Works from anywhere: Discord on your phone, or a terminal in the browser. You can have
  several conversations going at once.
- Stays on your server. This repository is only the code; your notes stay on your machine.

Each input — GitHub, calendars, Canvas, the timesheet — is one module in `zipper/inputs/`,
and `ZIPPER_INPUTS` picks which run. Adding a source means writing one file. Claude Code
does the thinking; the Python engine just fetches data and writes it into the notes.

---

## How the pieces fit

```
   GitHub · calendars · bank CSV              the vault (markdown, git)
          │                                   Projects/ Areas/ Topics/ Tasks/ ...
          │ fetch                                          ▲
          ▼                                                │ facts only
   ┌──────────────┐                                        │
   │    zipper    │────────────────────────────────────────┘
   │ (the engine) │──► Inbox/*.json, generated views, the queue
   └──────────────┘
          ▲ reads
   ┌──────────────┐  ttyd+tmux  ┌────────────────────┐
   │ zipper.serve │◀───────────▶│ Claude Code panes  │  judgment: the notes,
   │  dashboard,  │             └────────────────────┘  the arguments, the prose
   │ POST /discord│◀──┐         ┌────────────────────┐
   └──────▲───────┘   └────────▶│ headless claude -p │  one per Discord thread
          │ /api/canvas         └────────────────────┘
   ┌──────┴───────┐             ┌────────────────────┐
   │  extension/  │             │  bot/ (Discord)    │
   │  (browser)   │             └────────────────────┘
   └──────────────┘
```

**The division of labour is the design.** The engine writes what's verifiable: a push date,
a commit count, a diff. It never decides a project is dormant — that's a judgment about
someone's life. Claude's job is to surface the contradiction and argue about it, not resolve
it quietly.

---

## The data model

Every note is markdown with YAML frontmatter. `type` and `status` are required; the rest is
per type. `zipper lint` is the authority; enums are in `zipper/core.py`.

| Directory | Holds |
|---|---|
| `Projects/` | One note per project. The core |
| `Areas/` | Ongoing involvements — school, work, money, career |
| `Topics/` | Domains, skills, tooling, tensions |
| `People/` | Relationship context |
| `Classes/` | Current coursework |
| `Tasks/` | Checkbox lists with `[project:: [[Note]]]` |
| `Decisions/` | Dated, each with *what would change my mind* |
| `Events/` | Calendar events that exist for a reason, debriefed afterwards |
| `Log/` | Daily notes. Evidence, not structure |
| `Metrics/` | `metrics.csv`, append-only |
| `Meta/` | Schema, and the generated and query views |
| `Inbox/` | Machine state. Regenerable, gitignored |

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
| `fetch` | Starts a pass: github → calendars → canvas → sync → agenda → status → views → queue → brief. Idempotent |
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
| `digest` | Evening what's-due message |
| `discord send\|read\|status` | Talk through the relay |
| `conversations` | List live Claude conversations |
| `ghapp` / `ext` | The bot's GitHub identity / build and sign the extension |
| `lint` | Validate all frontmatter. **Run before finishing** |

---

## A pass: fetch → reasoning → commit

Only the two ends are code. `fetch` writes `Meta/Queue.md`, the brief, in three parts:

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

Stdlib only, no build step. Cards: **Today** (a real time grid), **Week** (coursework by
day), **What to work on** (coursework and tasks, ranked), **Claude** (every conversation,
each in its own terminal), **Signals** (flags and metrics), **Queue** (read-only).

**It owns no data** — every panel reads what the engine wrote. **It never fetches on
launch** — a timer does, hourly — and sources publish over SSE as they land. **Looking is
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

```bash
cp .env.example .env                    # ZIPPER_VAULT, plus whatever you use
pip install -r requirements.txt         # bot/ only

python3 -m zipper fetch
python3 -m zipper.serve --daemon
python3 -m bot.discord_bot
```

### Deployment

`deploy/` has systemd units and an nginx vhost.

```
zipper-web.service       dashboard, views, POST /discord
zipper-discord.service   the gateway connection (run with python3 -u)
zipper-fetch.timer       hourly fetch
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
Anything host-specific lives in the environment, never in the tree.

## Licence

MIT. See `LICENSE`.
