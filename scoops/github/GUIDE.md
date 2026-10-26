# Working GitHub rows

A push row is **evidence of activity, never proof of completion.** It may move a project's
`last_touched` (the engine's writer already does, forward only) and it never ticks a task.

- **A push to a repo named in a note's `repos:`** lands on that note. Read what changed
  before writing about it (`zipper inspect <repo>`) and update what the note says is true
  now: what shipped, what's being worked on. A commit that plainly finishes a task is a
  reason to ask, not to tick.
- **A push to a repo no note claims** stays unassigned unless the evidence names the
  project. Experiments and coursework are often unassigned on purpose.
- **A "pushed but never logged" flag** means work happened that the vault didn't record.
  Find out what it was; a log line saying so is the usual fix.
- **`Tasks/GitHub.md`** is written from GitHub's assigned issues on every pull. GitHub owns
  it.

## Don't

- **Don't write about a repo you haven't read.** `inspect` first.
- **Don't fuzzy-match repos to notes.** Map by evidence or leave it unassigned.
- **Don't read an NDA repo's contents.** For an org repo not listed in `scoops.github.open`,
  only metadata exists: never its files, README or commit messages. A rule in the vault's
  CLAUDE.md wins over seeing the repository.
- **Don't edit `Tasks/GitHub.md`.** The next pull undoes it.
