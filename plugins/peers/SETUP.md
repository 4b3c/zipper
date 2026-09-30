## Other zippers

Only relevant if someone else runs a zipper on the same machine: the two can send each
other short messages, which arrive as queue items. Skip otherwise. If yes, both zippers
need the same shared token and each other's address:

    ../zipper plugin enable peers
    ../zipper secret ZIPPER_COMMS_TOKEN
    ../zipper settings set plugins.peers.zippers '{"zipper-1": "http://zipper-1:8898"}'
