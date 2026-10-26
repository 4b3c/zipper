# Working merged rows

A `merged` row is a change to the shared code that another zipper, or the maintainer, has
landed. This zipper is not running it yet.

Run `zipper update` (it checks the new code, waits until no conversation is mid-turn,
restarts, and rolls back if it doesn't come up), then tick the row. With
`scoops.upstream.auto_update` on, the scheduler may already have taken it: `zipper update`
then says there's nothing new, and the row still gets ticked.

A zipper's own merges never appear here; it is already running them.

## Don't

- **Don't restart by hand** around an update. `zipper update` waits for running turns; a
  restart under one cuts its reply off.
