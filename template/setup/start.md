## Starting it

Everything is chosen; now run it. From `{{HOME}}`:

    cd {{HOME}} && docker compose up -d --build
    docker compose logs --tail 20

The first build takes a few minutes. In the logs, `[run] {{ID}} up: web` (with `bot` too,
if Discord is on) means it is running; if it says `DISCORD_TOKEN` is not set, the Discord
plugin is on without its token.

If they chose their subscription, ask them to run `docker exec -it {{ID}} claude` in a
terminal and `/login`, then `/exit`.

Then the real test: ask them to send a message in their zipper's channel. A thread should
open and Claude should answer within a minute. If nothing happens, `docker compose logs`
shows why.

If the dashboard is on, it is at http://127.0.0.1:8899 on this machine (or
http://<tailnet address>:8899 from their devices, on a server), behind the
password saved as `ZIPPER_TERM_CRED` (`user:password`; set it with `../zipper secret`
before starting, or the page stays loopback-only).

