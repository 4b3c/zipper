## Slack

Puts Slack messages into the queue as one row each: who, where, and the message id. Never
the text. It watches every DM and group DM they are in, any message that @mentions them,
and the channels they list; everything else is ignored. The pass reads a message when it
needs to, with `zipper slack <id>`. Ask whether they want it: it is for someone who reads
Slack on their phone, so Slack's email notifications never arrive.

It reads Slack as their browser does, with the browser's session, not an installed app.
That works in workspaces that forbid apps, which is a reason some owners want it and a rule
some workspaces have: **their call.** Two secrets, from a browser signed in to every
workspace they want watched (app.slack.com, desktop Chrome or Firefox):

- **`SLACK_TOKENS`**, a JSON object of `{"workspace name": "xoxc-..."}`, one token per
  workspace. With the Slack tab open, in the developer console:
  `Object.fromEntries(Object.values(JSON.parse(localStorage.localConfig_v2).teams).map(t => [t.name, t.token]))`
  then copy the object it prints.
- **`SLACK_COOKIE`**, the value of the `d` cookie for `.slack.com` (developer tools →
  Application/Storage → Cookies), as shown, starting `xoxd-`. Copy it exactly; it is
  already URL-encoded.

Send each through the one-time page, never the chat:

    zipper secret SLACK_TOKENS
    zipper secret SLACK_COOKIE
    zipper scoop enable slack

To watch channels, put their ids in `scoops.slack.channels` (`zipper settings set
scoops.slack.channels '["C0123ABCD"]'`); after a pull, `zipper slack --channels` lists the
ids the tokens can see. `scoops.slack.dms` and `scoops.slack.mentions` turn those off.

**Logging out of Slack in that browser ends the session**, and so does Slack expiring it.
The scoop then raises a flag saying so, and they re-send both secrets from a fresh login.
The first pull is a baseline: messages already there are not news, so it adds no rows.
