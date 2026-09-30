## Claude inside the zipper

The zipper runs its own Claude, inside its container. It needs one of:

- **Their Claude subscription** — after the zipper is started (the *Starting it* section),
  they run `docker exec -it {{ID}} claude` once in a terminal and use `/login`. Do not copy
  a login file from this machine: two copies of one login log each other out.
- **An API key** (pay per use) — `../zipper secret ANTHROPIC_API_KEY`.

Ask which, and note the answer for *Starting it*.

