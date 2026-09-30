## Discord (required)

A zipper is reached through Discord: each thread in one channel is its own conversation
with Claude. They need a Discord server of their own (any — a new one is fine) and a bot:

1. discord.com/developers/applications → **New Application**, name it.
2. **Bot** tab → **Reset Token** → copy it. On the same page turn on **Message Content
   Intent**.
3. **OAuth2 → URL Generator**: scope `bot`; permissions *Send Messages*, *Send Messages in
   Threads*, *Create Public Threads*, *Read Message History*, *Attach Files*. Open the
   generated link and add the bot to their server.
4. In Discord, **Settings → Advanced → Developer Mode** on; right-click the channel the
   zipper should live in → **Copy Channel ID**.

Then:

    ../zipper secret DISCORD_TOKEN          # they paste the token on the page
    ../zipper settings set discord.channel <channel id>

Optionally a second channel for messages the zipper sends on its own (the digest, alerts),
so they don't bury the conversations: `../zipper settings set discord.notify_channel <id>`.

