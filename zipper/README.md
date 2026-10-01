# The engine — operational reference

    cd <your checkout>
    python3 -m zipper --help

`ZIPPER_VAULT` comes from `.env` (see below), so the engine finds the notes from anywhere.
What was removed, and why, is in `../HISTORY.md`.

## Configuration

The vault's `settings.json` holds structure; `.env` (outside the vault) holds secrets. `zipper/settings.py` fills any
unset environment variable from the settings file when the package is imported, and
`core.cfg(key)` then reads `.env` first, then the environment -- so a shell that started
before a key was added still sees it. **Anything reading config at call time goes through
`cfg`.** `zipper settings show|get|set|check|migrate`.

## Plugins

The core is the Discord relay, the Claude conversations, the vault and its queue, lint
and commit. Everything else is a plugin: a folder in `plugins/` at the top of the
repository.

    plugins/<name>/plugin.json    manifest: name, title, about, default settings,
                                  secrets it needs, env variables its settings stand for
    plugins/<name>/__init__.py    the code, imported only when the plugin is enabled

**On means `plugins.<name>.enabled` in settings** (a manifest's `default_on` covers an
absent key; only the dashboard has it). `zipper plugin list|info|enable|disable`.
Commands that belong to a plugin refuse, with the line that turns it on, while it is
off. With the dashboard off, the web process still runs: it is where the bot hands in
Discord messages, and it serves only that relay and plugin posts.

A plugin provides whichever hooks it has data for (the full list is in
`zipper/plugins.py`): `pull`, `receive` (`POST /api/inputs/<name>`), `fetched`,
`snapshot` + `events` (queue rows), `target`, `timeline` (event rows), `work` (work
items), `facts` (through `zipper/writer.py`, the only thing that writes plugin facts
into frontmatter), `toggle`, `CALENDARS`, `flags`, `on_commit`, `jobs` (scheduled runs
under `zipper run`).

**Callers go through the registry** (`plugins.timeline`, `plugins.work`,
`plugins.toggle`), never a plugin's files: each dashboard card once read `canvas.json`
itself, and a cross-off made in one place came back in the others.

**The writer** applies only `last_push`, `commits_recent`, `commits_mine` and
`last_touched`, moves `last_touched` only forward, and refuses (and logs) anything else —
a plugin can never set `status`.

**Adding a plugin:** a folder with `plugin.json`, `__init__.py` (with `name`) and a
`SETUP.md` — the section the setup guide shows for it: what it is, and the exact commands. Its
settings default in the manifest and appear under `plugins.<name>`; any the rest of the
code reads as environment variables go in the manifest's `env` map. A newly enabled
plugin's first snapshot is a baseline and emits no rows.

## Feeds

**Canvas due dates** — Canvas → Calendar → *Calendar Feed* → copy the `webcal://` URL:

    python3 -m zipper ingest-ics "webcal://<canvas-host>/feeds/calendars/xxx.ics" --label canvas

**Google Calendar** — Settings → *Settings for my calendars* → *Secret address in iCal format*:

    python3 -m zipper ingest-ics "https://calendar.google.com/calendar/ical/.../basic.ics" --label gcal

A URL is remembered in `Inbox/calendars.json` and refetched whenever the calendar plugin pulls; a file goes
stale. The secret URL grants read access to anyone holding it, so it lives only in `Inbox/`.

**Budget** — `python3 -m zipper ingest-budget transactions.csv`. Column detection is
best-effort; only monthly totals are written.

`Inbox/` and `Log/` are skipped by vault scans; `Tasks/` is not. `sync` only moves
`last_touched` forward.

## Canvas submission status

`Inbox/canvas.json` is the only place that knows **submitted** rather than **due**. It is
written by the browser extension (`../extension/`), which POSTs to `/api/canvas` from a
logged-in tab; the engine holds no Canvas credential. `/bookmarklet` still accepts the older
bookmarklet payload. `zipper canvas --file planner.json` ingests a saved dump.

**Cross-offs.** Canvas can't be written to, so a hand cross-off goes in
`Inbox/overrides.json` and is reattached at read time by `canvas.stamp_overrides`, matching
on course + normalized title *or* `plannable_id`, so it survives renames and the extension's
wholesale rewrites. `zipper canvas` lists the cross-offs, since they rest on the operator's
word rather than Canvas.

**The join lives in the Canvas plugin.** An ICS row learns whether its assignment is done
there (matching on date and normalized title, accepting the next day too, since Canvas
files a 23:59 deadline on its own day and the feed often on the next).

## Dashboard

    python3 -m zipper.serve --port 8800 [--host ADDR] [--daemon] [--open]

`--daemon` keeps it up with no tabs open; without it the last tab closing (after a 4s grace
for reloads) stops the server. The service always runs `--daemon`.

**No fetch at launch.** Each plugin pulls on its own timer (`zipper pull --due`), and every
pass pulls what is due first; a plugin's card can pull just that plugin (`POST
/api/pull/<plugin>`). The `sources` card shows each plugin's age — every data bug so far was
stale data shown as current.

**The page is rows of cards** (`plugins.dashboard.rows` in the vault's settings). The
contract, the three sources and the isolation rules are in `zipper/web/cards.py`; the ones a
new vault is given, and how to write one, are in `template/vault/Dashboard/README.md`. A card
button posts to `/api/card/<id>/<action>`, which calls the card's `act_<action>(args, ctx)`.
**More boards** sit at `/p/<key>`, one per entry in `plugins.dashboard.pages`, each with its
own rows; the `pages` card is the catalog that links them. A card id is unique across pages.
With the dashboard plugin off, the web process serves only the Discord relay and plugin posts.

### Today

A time grid: height is duration (`PX_PER_MIN` = 0.85), overlaps get lanes, a hairline marks
now, finished items dim and strike through. `‹ ›` or ←/→ walk days (`window.__day`, passed to
`/api/panels?day=`, so an SSE refresh keeps your day).

- Events with an `Events/` note get an accent border, a 📝 and the note's *why*, fading out
  where the block ends. The block is a flex column — **never size text by counting lines**.
- Click a block for *open note* / *write the debrief*, *join* (a meeting URL in the
  location), *calendar*, *Canvas*. A block with no note offers **+ event note**, which POSTs
  `/api/eventnote` and runs `events.cmd_event` — identical to the CLI.
- **The calendar link is built.** `eid` = `base64url("<event id> <calendar id>")`, the
  calendar id parsed from the iCal URL, plus `_<UTC stamp>` for a recurring instance. A wrong
  `eid` fails silently as a Google error page; check changes against a real `htmlLink`.
- Every event needs an `end`; `parse_ics` copies the master's duration onto occurrences.

### The queue card

**One queue:** `Inbox/feed.json`. `Meta/Queue.md` is its rendering plus the note diff and
the flags. Rows are typed events (`system`, `action`, optional `who`/`when`); uncommitted
notes (`vault` rows, from `git status`) sit in the same list and count as outstanding.
Polled every 4s, because another conversation's edits write no file to watch.

**The card is read-only.** A row means the vault hasn't accounted for something; the only
thing that accounts for it is working out what it affected. So rows clear two ways:
`zipper commit` closing a pass, or `--mark` from the session that did the work.

    python3 -m zipper.serve --queue           # list, with keys
    python3 -m zipper.serve --mark <key>      # key or unique prefix; toggles, so also undo
    python3 -m zipper.serve --mark-all        # cross off everything open

A watcher pushes changes to every open tab within a second. Rows are keyed by a hash of
their text, so a repeated fact is one row.

- **Flags never enter the queue** — they go to **Signals**.
- **Terminal lifecycle isn't an event.** Session start/stop publishes `status` or nothing.
- **No "no changes" row.** An empty queue shows nothing.

The **Canvas** and **Projects** panels do tick: a task writes back to its markdown line; a Canvas item goes
to `overrides.json`.

## Terminals

Each conversation gets its own ttyd from `ZIPPER_TTYD_BASE` (8810) up, remembered in the
registry:

    ttyd -p <port> -i 127.0.0.1 -W --base-path /t/<port> tmux attach-session -t <name>

**`-W` is a live shell. It binds loopback, always.** nginx proxies `^/t/(88[0-9][0-9])(/.*)?$`
with WebSocket headers, on the dashboard's own origin, so one sign-in covers every terminal.
The port range is pinned so the proxy can't be walked onto other loopback services. The page
builds `location.origin + '/t/' + port + '/'`; ttyd needs `--base-path` to match.
`serve.py` refuses a non-loopback `--term-host` without `--term-cred`.

**The session outlives the server.** `zipper-web.service` sets `KillMode=process`, so a
restart leaves ttyd and tmux running and the new server adopts them. Check `tmux ls`
creation times.

**Nothing starts on its own.** Opening the dashboard costs no tokens; starting a session is a
button. `new conversation` adds one (a `local-` id, no Discord thread) and closes nothing.

**PATH.** systemd gives services a minimal PATH without `ttyd`, `tmux`, `gh` or `claude`.
`web/base.py` and `claude-session.sh` prepend `~/.local/bin` and `/usr/local/bin`. Without
it, `gh auth token` fails silently and a fetch writes public-repo-only data over the notes.

Traps, each already hit:
- **ttyd attaches, never creates.** With `tmux new -A`, Ctrl-C ended Claude, the browser
  reconnected, and the session resurrected forever. Sessions are started separately
  (`conversations.start()`).
- **Kill ttyds by port** (`kill_ttyd_on`, only if `/proc/<pid>/comm` is `ttyd`). An adopted
  ttyd keeps its original argv; after changing ttyd's command, kill running ones —
  restarting `zipper-web` doesn't.
- **`sweep()` drops a ttyd whose session is gone** (or whose pane is a bare shell), so it
  can't serve a new conversation under an old name. `reap_terminal()` does the same for the
  dashboard's own terminal.
- **Ask the pane's process, not `pane_current_command`.** During a tool call the foreground
  is `bash`; trusting it killed two working conversations. `running_claude()` reads `/proc`,
  and the sweep double-checks before ending anything.
- **A row adopted from a running pane has no stated session id.** `detect_session()` infers
  it (newest unclaimed transcript) and re-checks on every listing.

## The chat list

One row per conversation: a name and a light. **Yellow** working, **green** waiting,
**grey** closed. Polled every 6s.

- **Busy is read from Claude Code's status line** — `esc to interrupt` or the spinner, in the
  bottom 14 lines, anchored on line starts, sticky for 25s so a blank frame doesn't flicker
  green. Not the whole pane (anything that *prints* those words looked busy), and not
  transcript writes (resuming writes too).
- **Clicking a closed row selects it** and offers **reload conversation**. Resuming re-reads
  the transcript at full price, so it's never a side effect. A page reload auto-opens the
  top *live* conversation only.
- **Order is the last message's timestamp** from the transcript — not the registry (misses
  typed input) and not mtime (resuming appends untimestamped bookkeeping).
- **Names come from Claude's `ai-title`**, falling back to the Discord thread name, then the
  id. Titles are user-influenced: escape them (`chatEsc`).
- The selected row isn't scrolled into view; a list moving under you is worse.

### Usage meters

Two bars under the list: the 5-hour and 7-day plan windows, amber at 70%, red at 90%. One
line each — bar, percentage in a fixed column, reset time (local; bare time if today, weekday
otherwise).

From Anthropic's OAuth usage endpoint (`zipper/usage.py`), the same source as Claude Code's
`/usage`; nothing local knows the denominator. `/api/usage` caches 5 minutes
(`ZIPPER_USAGE_TTL`). The token is read from `~/.claude/.credentials.json` at call time and
never stored; `Inbox/usage.json` holds percentages only. `_pct` hunts for the number and
`normalise` drops unreadable windows — a blank meter beats a wrong one. A failed call shows
the last good numbers dimmed; a 401 means Claude Code hasn't refreshed its token yet.

### Copy and paste

**Copy needs a secure context**, so the dashboard is also served by `tailscale serve` at
`https://<machine>.<tailnet>.ts.net:8443` → nginx on `127.0.0.1:8899`. Plain http works but
can't copy.

- **Claude Code owns the selection**, not xterm: it copies into a **tmux buffer**. So the
  page polls `/api/tmuxbuffer` and writes the clipboard **from inside the iframe**, the
  focused document; if refused for want of a gesture, it writes on the next click or key.
- In panes not running Claude Code, the xterm selection path is used (`term.getSelection()`,
  written synchronously inside the event; `execCommand('copy')` as fallback). Ctrl/Cmd+C
  copies only with a selection, so bare Ctrl-C still interrupts. On macOS both ttyd launches
  set `macOptionClickForcesSelection` and `rightClickSelectsWord`.
- **Images** go to `/api/pasteimage`, land in `<tmp>/zipper-pastes/` (last 40; png, jpeg,
  gif, webp; ≤16MB), and the *path* is typed into the prompt.
- **`/api/clipdebug`** reports browser-side failures to `journalctl -u zipper-web`. The
  browser is the one thing the box can't test; keep it.

## Discord conversations

One conversation per Discord thread, headless: `claude -p` over streaming JSON
(`zipper/convhead.py`).

| Piece | Where |
|---|---|
| Registry, start/resume | `convcore.py` |
| Liveness, titles, listing, idle sweep | `convstate.py` |
| One import for all of it | `conversations.py` |
| Routing a message to its thread | `web/http.py`, `/discord` |
| Forwarding the reply | `hooks/forward_reply.py` on `Stop` |
| Opening a thread | `bot/client.py`, `on_message` |
| Typing indicator | `chat.py` → the bot's `/typing` |
| CLI | `zipper conversations [--close THREAD]` |

- **The session id is derived:** `uuid5(NS, thread_id)`. Nothing to fall out of sync; losing
  `conversations.json` costs timers and titles, not conversations. `--session-id` starts,
  `--resume` continues; the transcript on disk decides which.
- **`ZIPPER_DISCORD_THREAD`** is set for Discord conversations only;
  **`ZIPPER_CONVERSATION`** for all. Panes use `local-` ids and never get a thread.
- **Registry writes go through `convcore.mutate()`** (flock'd).

### Forwarding replies

`forward_reply.py` runs on `Stop`. It posts the turn's last assistant text to the thread **if
any prompt in that turn** (including mid-turn `enqueue` rows) matches a delivery fingerprint
from `note_delivery` (a set: 12 entries, 6 hours). Otherwise it was typed, and the terminal
already shows the answer. So a session writes its reply once and never `discord send`s an
answer.

The hook's rules:
- **Always exit 0.** Exit 2 on `Stop` blocks the turn from ending — an outage would loop.
- **Dedupe on the assistant message uuid.** It can fire twice; posting isn't idempotent.
- **Skip `local-` ids.**
- **Wait for the closing row.** `Stop` fires while the final message is still being written;
  reading too early finds an empty turn and the reply is lost for good.
- **Log every decision** to `Inbox/forward.log`, including "decided this was typed".
- `discord_send` clears the typing indicator in a `finally`.

### The idle sweep

A price signal, not a saving: an idle instance costs nothing, but after the prompt cache
expires the next message re-reads everything.

| | When | What |
|---|---|---|
| **warn** | `ZIPPER_IDLE_SECONDS`, 55 min | Notice in the thread with the re-read size, while answering is still cheap |
| **close** | `ZIPPER_CACHE_TTL_SECONDS`, 60 min | Closes the row silently |

Activity resets the clock (`last_active()`: newest of the registry stamp and transcript
mtime) and clears the warned mark. **Idle is not `!alive()`** — a headless process exits after
every turn, so the sweep asks about idle time, never tmux. `alive()` still means tmux
everywhere else.

**Nothing locks the vault between conversations.** Deliberate (2026-09-06). Don't work one
project in two threads at once; if that becomes a problem, the lock belongs in `convcore.py`.

## Timers

`zipper-fetch.timer` runs `zipper pull --due` every five minutes; under `zipper run` the
supervisor does the same, plus each plugin's `jobs()`.

### The scheduled pass

`zipper pass` runs at 09:00 and 21:00 (`zipper-pass.timer`, or the passes plugin under
`zipper run`). It pulls what is due; if the brief is
empty it stops. Otherwise a headless `claude -p` does the middle step of a pass and commits,
and its final message starts `NOTIFY: yes` or `NOTIFY: no`. Only *yes* reaches Discord: a new
thread in the main channel, attached to the pass's session (`session_fixed` on the registry
row), so replying there continues the conversation that did the work. An unparseable answer
counts as *yes*. `--dry-run` never posts; `--force` runs on an empty brief.

It never passes `--force` to `commit`. A conversation counts as live if it has a pane with
Claude in it *or* a headless turn holding its lock (`convhead.turn_running`), so a pass
cannot sweep up edits a Discord conversation is in the middle of.

### The evening digest

`zipper digest` posts what's due tomorrow, tomorrow's timed events, what's overdue, and the
week ahead. `zipper-digest.timer` runs it at 19:00; `--dry-run` prints it.

- **Reads through `web/data.py`**, so it and *What to work on* never disagree.
- **Coursework and self-set tasks are counted separately** — different obligations, and a
  merged ranking pushes homework below the cut.
- **Goes to `ZIPPER_NOTIFY_CHANNEL`**, not the main channel the operator writes in. Unset
  falls back to main.
- **Once per date**, recorded in `Inbox/digest-sent.json` only after a successful send — which
  is what makes `Persistent=true` safe.

