## Dashboard (on unless they turn it off)

A web page of cards: the queue, Claude terminals in the browser, and whatever else they
want to see. It is the only plugin on by default. Ask whether they want it; if not,
`../zipper plugin disable dashboard`.

If they keep it:

- It needs a login, because its terminals are a shell on this machine:
  `../zipper secret ZIPPER_TERM_CRED` (the value is `username:password`). Without it
  the page only answers on this machine itself.
- **Show them how it is theirs to shape.** The cards and their order are
  `plugins.dashboard.rows` in `settings.json`; `Dashboard/README.md` explains the three
  kinds. Ask what they would want to glance at each day. Built-in cards and plugin
  cards just need adding to a row; for anything else, write a vault card in
  `Dashboard/<name>/` — `Dashboard/recent/` is a working example to copy.
- The "setting up" card reads `Dashboard/setup.md`. Tick its items as you finish the
  matching sections (edit the file: `- [ ]` to `- [x]`), and once setup is done,
  remove that card from the first row.
