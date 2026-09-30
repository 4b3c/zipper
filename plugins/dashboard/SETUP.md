## Dashboard (on unless they turn it off)

A web page showing the day, the queue of what changed, what is due, and Claude terminals
in the browser. It is the only plugin on by default. Ask whether they want it; if not,
`../zipper plugin disable dashboard`.

If they keep it, it needs a login, because its terminals are a shell on this machine:
`../zipper secret ZIPPER_TERM_CRED` — the value is `username:password`, no spaces.
Without it the page only answers on this machine itself (127.0.0.1:8899).
