"""gateway -- the one Discord bot on a machine, shared by every zipper on it.

    python3 -m gateway serve                         hold the bot, serve the relay
    python3 -m gateway pair <zipper id> [--again]    a token and a code; see registry.py
    python3 -m gateway add <zipper id> <guild id>    link a server; prints the zipper's token
    python3 -m gateway list | remove <zipper id>

**Every zipper uses one**, whether the machine runs one zipper or several: its bot
never connects to Discord directly. `zipper discord connect` (zipper/discordsetup.py)
makes the gateway and pairs a zipper with it.

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
  guild, or the guild itself, or answers a button click or form (an interaction)
  that came from that guild, with that click's own token. Reading the bot's own
  user and application, and gateway discovery, are the only other exceptions.
  Everything else is refused with 403 before Discord sees it.

The upstream connection (`upstream.py`) is a plain discord.py client whose raw
events feed the relay. It also links servers: in a server no zipper owns, a pasted
pairing code links the zipper it was made for, and any other code is tried as an
invite, which goes to the host (hostd `invite.redeem`) to build and start a zipper
with `zipper init --relay`; the gateway then links that server to it. Making an
invite takes the operator's authenticator code (`zipper host invite.create`).
Either way, the channel the code was pasted in is the zipper's home.

Deploying it: `gateway/compose.yml` says how.

Nothing here reads a vault. It is the most exposed process on the box -- it reads
every message in every linked server -- so it stays small enough to read in one
sitting.
"""
