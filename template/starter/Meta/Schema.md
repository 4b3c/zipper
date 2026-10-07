---
tags: [meta]
type: moc
status: living
---

# Schema

The frontmatter contract. `zipper lint` enforces it and `zipper/core.py` holds the enums.
Keep it small enough to maintain — a field nobody queries should be deleted.

## Every note

| Field | Values |
|---|---|
| `type` | `project` · `area` · `topic` · `person` · `life` · `moc` · `decision` · `log` · `tasks` · `class` · `reference` · `view` · `event` |
| `status` | `active` · `dormant` · `handing-off` · `shipped` · `retired` · `archived` · `idea` · `past` · `living` · `ongoing` (decisions and events have their own, below) |
| `tags` | Free-form |
| `note_updated` | Auto: when the file was last edited |

`status` is about the **thing**; `note_updated` is about the **note**. A note marked `active`
that nobody has edited in two months means the note or the project is stale.

## Projects

| Field | Meaning |
|---|---|
| `stage` | `idea` → `designing` → `building` → `shipped` → `selling` → `verifying` → `closed` |
| `started` / `ended` | `YYYY-MM` |
| `last_touched` | `YYYY-MM` — when *work* happened, not when the note changed |
| `repos` | `[name, some-org/name]`, first is primary. Drives `zipper github` |
| `revenue_to_date` | USD. `0` is a fact, not a blank |
| `revenue_intent` | `true`/`false` — meant to earn? Ventures vs builds |
| `paying_users` / `customers` / `team_size` | Numbers |
| `blocked_by` | What's actually in the way |
| `open_loop` | `true` if deliberately left running long |
| `people` / `domain` | `[Sam]` / `[robotics, cad]` |
| `review` | `YYYY-MM-DD` — when to revisit |
| `status_verified` | `YYYY-MM-DD` — the operator confirmed the status despite contradicting evidence |
| `cad_url` | Onshape link, for projects whose artifact is CAD. No automatic evidence exists for these; set `last_touched` by hand |
| `meets` | Standing meeting, e.g. `Mon 14:00`. Never shown in Agenda |

## Areas

`horizon` (`months`/`years`), `target` + `target_by`, `role`, `open_problem`, plus
`review`, `people`, `domain`.

## People

`relationship`, `since`/`met`, `met_through`, `orgs`, `shared`.

Most people don't need a file. Someone who appears in one note is described in that note;
someone who spans several contexts gets a section in a shared `People/Others.md`. Give a
person their own file only when the operator decides they are central.

## Topics

`kind` — `taste` · `tooling` · `skill` · `thread` · `tension` · `channel` · `record` ·
`personal`. `feeds: [Project]` for where it shows up; `learning: true` if actively being
picked up.

## Rules that keep this honest

1. **Next steps are tasks, not fields.** Put them in `Tasks/` with `[project:: [[Note]]]`.
   "Land one B2B customer" is a goal; "email three coffee shops the demo link" is a task.
   Goals go in the body.
2. **A `0` beats a blank.** Absent fields are invisible to queries.
3. **`dormant` is a legitimate answer** — better than a note rotting as `active`.
4. **A `review:` date is a promise.** Don't write one you won't keep.

## Decisions

In `Decisions/`, scaffolded by `zipper decide "title"`.

| Field | Meaning |
|---|---|
| `status` | `open` · `settled` · `superseded` · `reversed` |
| `decided` | `YYYY-MM-DD` |
| `review` | Required — a decision with no review date is an opinion |
| `confidence` | `low`/`medium`/`high`, at the time |
| `reversible` | `true`/`false` — cheap-to-undo decisions deserve less agonising |
| `affects` | `[My App, Career]` |

The load-bearing part is the **What would change my mind** section.

## Events

In `Events/`. **One note per calendar event that exists for a reason** — a meeting scheduled
to get something out of, not class. When it's over, the brief flags it and Claude asks how
it went.

| Field | Meaning |
|---|---|
| `status` | `scheduled` · `debriefed` · `cancelled` |
| `event_uid` | The ICS UID from `Inbox/calendar-*.json` |
| `event_start` | `YYYY-MM-DDTHH:MM`, or a bare date for all-day. **A T, not a space** |
| `event_summary` | The event's title, so a dangling note still says what it was |
| `calendar` | `gcal` · `canvas` · `classes` |
| `about` | `[[Note]]` it serves |
| `debriefed` | `YYYY-MM-DD` |

Body: **Why this is on the calendar** · **Going in** (both before) · **How it went** (after).
**No checkboxes** — every `- [ ]` in the vault is read as a task.

```bash
zipper event "Design review" --date 2026-09-04 --about "My App"
zipper events --pending
```

**Keyed on `(uid, start)`**, since a uid names a whole recurring series. A single-occurrence
uid that moved is followed and `event_start` rewritten; a uid matching nothing is flagged
**dangling**, never guessed.

`Meta/Agenda.md` opens with a `## Today` time sheet, embedded in [[Now]] and [[Dashboard]]
as `![[Agenda#Today]]` — **the heading must stay literally `## Today`**. Noted events get a
📝, with their links listed below the grid (a link inside a code block is dead text).

## Logs

In `Log/`, named `YYYY-MM-DD.md`, headings Did / Decided / Friction / Tomorrow. Excluded from
lint and queries. Its job is feeding `zipper sync`, which moves `last_touched` on every
linked note — so **link only what was actually worked on**, and never in Tomorrow.

## Metrics

`Metrics/metrics.csv`, append-only: `date,key,value,note,source`. Keys in [[Metrics]].

## Views — `type: view`

A page showing data it doesn't own. Never a source of truth; nothing is recorded only in a
view.

| | `query` | `generated` |
|---|---|---|
| Examples | `Dashboard` `Now` `Ventures` `School` | `Status` `Agenda` `Queue` `Repos` |
| Built by | Dataview, when opened | `zipper`, on the last run |
| Sees | The vault only | Anything — GitHub, calendars |
| Safe to edit | **Yes** — the query is yours | **No** — overwritten |

Generated views also carry `source:` and `generated:` (lint checks both). They're excluded
from the note diff because they only restate events already in the queue.

**An external view holds pointers, not copies** — subject and link, never the body. An
email's next steps go into the project note and the task list; the email stays in the inbox.

Related: [[Dashboard]] · [[Home]]
