"""The gateway's own connection to Discord: the one place the real token is used.

A plain discord.py client. Every raw event it receives goes to the hub, which hands
it on to whichever zipper owns that guild. It acts on its own only in a server no
zipper owns yet, where it says so -- once, when it joins.
"""
import discord

INTENTS = discord.Intents.default()
INTENTS.message_content = True      # the same intents a zipper's bot asks for

NOT_LINKED = ("Hi, I'm Zipper. This server isn't linked to a zipper yet, so I can't "
              "answer here. Ask whoever invited me to link it.")


def client(hub):
    c = discord.Client(intents=INTENTS, enable_debug_events=True)

    @c.event
    async def on_socket_raw_receive(raw):
        hub.feed(raw)

    @c.event
    async def on_ready():
        print('[gateway] upstream ready as %s in %d server(s)' % (c.user, len(c.guilds)))

    @c.event
    async def on_guild_join(guild):
        if hub.registry.owner(guild.id):
            return
        channel = guild.system_channel
        if channel is None or not channel.permissions_for(guild.me).send_messages:
            channel = next((ch for ch in guild.text_channels
                            if ch.permissions_for(guild.me).send_messages), None)
        if channel is not None:
            await channel.send(NOT_LINKED)
        print('[gateway] joined unlinked server %s (%s)' % (guild.name, guild.id))

    return c
