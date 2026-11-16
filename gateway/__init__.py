"""gateway -- one Discord bot shared by many zippers.

    python3 -m gateway serve                         hold the bot, serve the relay
    python3 -m gateway add <zipper id> <guild id>    link a server; prints the zipper's token
    python3 -m gateway list | remove <zipper id>

**The gateway is the only thing holding the bot's token.** A zipper holds a token of
its own, minted here, which means nothing to Discord. If a zipper's container leaks
it, the most it can do is act in that zipper's own server.

**It looks like Discord to each zipper.** A zipper's `bot/` runs unchanged, with
discord.py pointed here by `DISCORD_RELAY` (`bot/__init__.py`): its REST calls come
to `/api/v10/...` and its websocket to `/ws`. So every feature of the bot -- threads,
status messages, attachments, `/login` -- works the same in a container that has
never seen the real token, and nothing about conversations lives here.

**Scoping is the whole job** (`relay.py`):

- *Inbound.* An event reaches a zipper only if it belongs to that zipper's guild.
  READY lists only that guild; a DM, or another server's message, goes nowhere.
- *Outbound.* A REST call passes only if it names a channel or thread in that
  guild, or the guild itself; reading the bot's own user and application, and
  gateway discovery, are the only exceptions. Everything else is refused with
  403 before Discord sees it.

The upstream connection (`upstream.py`) is a plain discord.py client whose raw
events feed the relay, and which answers in servers no zipper owns yet.

Nothing here reads a vault. It is the most exposed process on the box -- it reads
every message in every linked server -- so it stays small enough to read in one
sitting.
"""
