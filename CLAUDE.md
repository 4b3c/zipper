# CLAUDE.md — working on this codebase

How this repository works and the rules for changing it. It says nothing about whose vault
it runs against: **the operator's context lives in `$ZIPPER_VAULT/CLAUDE.md`** — read that
at the start of a session. If it's absent you're working on code only; infer nothing about
the operator from this repo.

---

## 1. What this is

- **`python3 -m zipper`** — the engine. Reads a vault of markdown notes with YAML
  frontmatter, fetches from outside sources, writes back facts only.
- **`python3 -m zipper.serve`** — the dashboard. Stdlib HTTP server, no framework.
- **`bot/`** — the Discord relay. Posts each message to the server's `/discord`, which hands
  it to that thread's Claude conversation. Replies return via the `Stop` hook in `hooks/`.
- **The vault** — plain markdown, one directory per note type. Not in this repository.

The engine and server are **stdlib-only**: no pip, no venv. The target is a box where
`apt install python3` is the whole setup. Only `bot/` has dependencies.

## 2. Layout

| Path | What |
|---|---|
| `zipper/core.py` | Vault paths, frontmatter, shared helpers |
| `zipper/cli.py` | The whole command surface |
| `zipper/lint.py` `sync.py` `status.py` | Validation, evidence, the snapshot |
| `zipper/ics.py` `events.py` | Calendars, recurrence, event notes |
| `zipper/gh.py` `canvas.py` `metrics.py` | Fetchers and numbers |
| `zipper/runqueue.py` `views.py` | The queue, the saved queries |
| `zipper/chat.py` | The Discord CLI |
| `zipper/conversations.py` | Front door over `convcore.py` (identity, registry), `convhead.py` (headless Discord conversations), `ttyd.py` (a ttyd per pane), `convstate.py` (liveness, reaper) |
| `zipper/serve.py` + `zipper/web/` | The dashboard. `serve.py` is the entry point |
| `zipper/web/home.py` | `/`, the front page |
| `zipper/web/render.py` | `/old`. Still the only home of the Claude terminal — **live, not an archive** |
| `zipper/box.py` | The box's vital signs, sampled each minute into `Inbox/box-history.json` |
| `zipper/usage.py` | Plan usage meters. The OAuth token is read at call time, never stored |
| `hooks/forward_reply.py` | The `Stop` hook that posts a reply to its Discord thread |
| `bot/` | Gateway client and HTTP surface. Saves attachments to `/tmp/zipper-discord-files/<message id>/` |
| `utils/` | `constants.py`, `text.py` — the bot's only dependencies |
| `extension/` | The browser collector. **Collects, never concludes.** See its README |
| `zipper/README.md` | The operating reference. Read before changing anything |
| `HISTORY.md` | What was removed and why. Never how anything works today |

- **Read `core.TODAY` through the module; never import it by value.** The server runs for
  days and rolls the date at midnight. `import *` from `core` goes through an explicit
  `__all__`.
- **`PrivateTmp` must stay off** on `zipper-discord` and `zipper-web`, or attachment paths
  point at files the session can't see.

## 3. The vault contract

The engine expects `Projects/`, `Areas/`, `Topics/`, `People/`, `Classes/`, `Tasks/`,
`Decisions/`, `Events/`, `Log/`, `Metrics/`, `Meta/`, `Inbox/`. Every note needs `type` and
`status`; the rest is per type. Enums are in `zipper/core.py`; `zipper lint` is the
authority.

Four `Meta/` views are generated every run — never hand-edit them. `Inbox/` is machine
state: regenerable, gitignored, may hold secret URLs, never authoritative.

## 4. Rules the engine obeys, and so should you

- **Facts yes, judgments no.** Update dates, counts and links. Never decide a project is
  dormant or a person matters less; surface the contradiction and let the operator answer.
- **Evidence only moves dates forward.** A plan or a mention isn't proof of work.
- **Conclusions, not caches.** Keep the durable part of an external thing; leave the original.
- **Don't write about a repo you haven't read, and don't fuzzy-match repos to notes.**
- **Mark inference** with an italic line in the note.
- **Private-source rules are absolute.** Seeing a repository isn't permission to quote it;
  metadata isn't consent to read contents. A constraint in the vault's `CLAUDE.md` wins.

## 5. Working on the code

- **Restart to deploy.** A page reload doesn't pick up Python changes: `systemctl restart
  zipper-web`. Conversations survive (`KillMode=process`) — check `tmux ls` creation times.
- **Verify UI in a browser**, not in the HTML string.
- **Comments say why, in the present tense.** When something is removed, the reasoning goes
  in `HISTORY.md`, not in a comment about code that no longer exists.
- **Timestamps:** APIs and ICS are UTC; the vault is local. Convert, never slice. Don't touch
  `parse_ics` without a fixture.
- **Nothing here runs one at a time.** Walk every change through these four before calling it
  done — each has already broken something:
  1. **Several conversations at once.** Per-turn state needs the thread id in the filename
     *and* checked inside it. A shared file belongs to whoever wrote last.
  2. **Messages mid-turn, from either door.** *Recently written* never means *still running*;
     state must say when it's finished.
  3. **A process killed at any line.** What does a half-written file or orphaned lock do to
     the next start, and can you tell that failure from the feature being off?
  4. **Two components answering one question differently.** Make them consult the same record.

  Also: **overwriting is worse than duplicating** (a duplicate is visible; an overwrite
  destroys silently), and **a freshness check fed by what it checks measures nothing**.
- Finish a session that touched the vault with `lint`, `status`, then `commit`. If lint isn't
  clean, you broke something.

## 6. Discord

The bot is a separate always-on process holding the gateway connection, with a small HTTP
API on `BOT_URL`. Nothing else imports `discord`.

**Inbound.** The bot POSTs each message to `/discord`, which routes it by thread:

| Conversation | What happens |
|---|---|
| running | Delivered over the headless protocol (`convhead`) |
| closed | `claude -p --resume`, then delivered |
| never spoken to | A new headless conversation, primed with the message |

**Panes and threads never mix.** Discord conversations are headless `claude -p` processes;
dashboard panes live under `local-` ids and carry no thread. Both `tmux_name` and
`session_id` derive from the conversation id, so a pane with a thread id means two processes
on one transcript and keyboard input forwarded to Discord. **Never give a pane a thread id.**
A pane started before a restart keeps its old environment — compare `systemctl show -p
ExecMainStartTimestamp` with the commit time before trusting a fix.

**Replies.** Messages arrive verbatim, untagged. `hooks/forward_reply.py` forwards the reply
on `Stop` if *that turn's* input matches a delivery fingerprint (`note_delivery`).
`ZIPPER_DISCORD_THREAD` (Discord only) and `ZIPPER_CONVERSATION` (both) say what kind of
conversation this is, not where a given message came from. **Write one reply, to the
terminal; never `discord send` an answer** — it posts twice.

**Out of band** — a scheduled result, a long job finishing, an alert:

```bash
python3 -m zipper discord send "text"            # to this conversation's thread
python3 -m zipper discord send "here" --file report.html
python3 -m zipper discord read --limit 5
python3 -m zipper discord status
```

A Discord message is a request like any other; the same rules govern what it may ask for.

**Hook wiring** is in `~/.claude/settings.json`, outside both repos: one `Stop` entry running
`hooks/forward_reply.py`. A rebuilt box needs it re-added by hand. **Before adding any hook
that posts mid-turn, read the streaming entry in `HISTORY.md`.**

## 7. Configuration

Environment only — see `.env.example`. `ZIPPER_VAULT` is the seam between this code and
somebody's life. Every other default is empty or generic. **A default naming a real person,
school or host is a bug.**

### Commits and pushes

This repository is written by a GitHub App (since 2026-09-17). The local git identity is
`<slug>[bot]`, and pushes go through **`python3 -m zipper ghapp --push`**, which mints a
one-hour installation token. **Never `git push origin`** — it falls back to the operator's
credentials and lands as them.

**Push without asking.** The commit is a bot's and the remote is published code; the operator
has no stake in the timing, and unpushed work is invisible to the next session. The bar is
low: something works, is verified, is fixed, or is worth not losing. Several small pushes are
right. If something is knowingly half-built, say so in the message rather than holding it.

**This applies to this repository only.** The vault is local-only and never pushed anywhere.

- The key on disk only mints tokens — a compromised box gets an hour of `contents: write`.
- The token never reaches `.git/config`, and is scrubbed from a failed push's stderr.
- **Reading stays on `GITHUB_TOKEN`.** The App isn't installed on the org, so moving the
  fetcher onto it would silently lose most of the evidence.
- `zipper ghapp` prints the identity, mints a token, and counts reachable repos.
  `selection=all` is intended.
