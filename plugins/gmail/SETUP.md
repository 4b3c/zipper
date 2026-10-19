## Gmail

Puts every email they receive or send into the queue as one row: who, the subject, and the
message id. Never the body. The pass reads a message when it needs to, with
`zipper gmail read <id>`, and works out what it means for the notes. Ask whether they want
it. It uses the same Google login as the timesheet, so first make sure
`ZIPPER_GOOGLE_CLIENT_ID` and `_SECRET` are set, then:

    zipper plugin enable gmail
    zipper google --auth        # once: the consent screen now includes read-only Gmail

Read-only on purpose. Email reaches the unattended passes, and a token that could send
would let one message tell the box to forward others. The first pull is a baseline: mail
already there is not news, so it adds no rows.

**Folders (optional).** `plugins.gmail.folders` is a list of `{label, query}` Gmail-search
rules; once set, each pull moves matching inbox mail into those labels, and
`zipper gmail --sort --backlog` files the old mail. It needs `gmail.modify`, which can also
send, so ask before setting it, then re-run `zipper google --auth`. See `sort.py`.
