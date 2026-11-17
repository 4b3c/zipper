## Discord

Optional: without it they use the zipper from the dashboard, which is always there. With
it they can reach the zipper from their phone, where each thread in one channel is its
own conversation, and the evening digest can reach them. It takes a Discord server of
their own and, if this machine has no Discord gateway yet, a bot they make (about five
minutes). Ask whether they want it now; it can always be added later.

**It is set up on the machine, not here.** The bot's token belongs to the machine's
Discord gateway, a container this zipper cannot write to, so it is never pasted into
the dashboard or a chat. If they said yes when they installed, it may already be done:
`zipper settings get scoops.discord.enabled`.

Otherwise, on the machine, in this zipper's folder (`{{HOME}}`):

    ./zipper discord connect

It walks them through making the bot if the machine needs one, then prints a link to
add the bot to their server and a code. Pasting the code in any channel of their server
makes that channel this zipper's home. Connect restarts the zipper, which closes this
terminal: tell them so first, and that afterwards they reopen it and say "continue setup".
Then `zipper discord status` should say it is connected.

Optionally a second channel for messages the zipper sends on its own (the digest, alerts),
so they don't bury the conversations: `zipper settings set discord.notify_channel <id>`
(Developer Mode on in Discord's settings, then right-click the channel, Copy Channel ID).
