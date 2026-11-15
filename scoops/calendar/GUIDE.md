# Working Calendar rows

Each row is an event added to or removed from one of the operator's calendars. A move is a
remove plus an add; rows only appear inside the window both fetches saw, so the sliding
window doesn't invent events.

- **Something the operator will walk into** (a meeting, an interview, a deadline someone
  else set) may deserve an event note: `zipper event "<summary>" --date YYYY-MM-DD --about
  "<Note>"`. Its **Going in** section says what they want from it. Routine entries (classes,
  standing blocks) need nothing.
- **A removed event** that had a note: check whether it moved (an add with the same name
  nearby) or was cancelled, and say which.
- **A debrief flag** after an event is the one flag that wants an answer from the operator:
  ask how it went against **Going in**, write **How it went**, set `status: debriefed`, and
  turn follow-ups into tasks.

## Rows Zipper wrote

A row marked `zipper ... (written by Zipper)` is an event a session put on, changed or took
off the Zipper calendar with `zipper cal`. It is there for a second look, not because
anything happened. Check it against where it came from (the email, Slack message or task):
the date and time, the details in the description, and that it really is something the
operator can only do at that moment. If it replaced a task, the task should be gone from
`Tasks/`. Fix what's wrong with `zipper cal add` under the same `--key`, or `zipper cal rm`.

## Don't

- **Don't put a `- [ ]` checkbox in an event note.** Every checkbox is read as a task.
- **Don't store a calendar's URL anywhere in the vault.** It is a password; it lives in
  `Inbox/`.
