"""zipper.discord

The Discord plugin: whether the bot runs. The bot itself is `bot/`, started by the
supervisor when this plugin is on and `DISCORD_TOKEN` is set, and everything that
posts goes through `zipper.chat.discord_send`, which says so and sends nothing when
it is off. Its settings stay where they always were, `discord.channel` and
`discord.notify_channel`.
"""
name = 'discord'
