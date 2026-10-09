## Other zippers

Only relevant if someone else runs a zipper on the same machine: the two can send each
other short messages, which arrive as queue items and get a read-only reply. Skip
otherwise. If yes, both containers go on one Docker network (the operator's compose
file), and each lists the other by its container name -- no password: a message must
come from the address that name resolves to.

    zipper plugin enable peers
    zipper settings set plugins.peers.zippers '{"zipper-1": "http://zipper-1:8898"}'
