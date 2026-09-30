## Where it runs

Ask first: **is this a server they reached over SSH, or their own computer?** Offer to
explain either before they choose. What it means:

- **A server** (a VPS, or a computer at home that never sleeps) -- the zipper answers
  Discord at any hour, and the morning and evening passes and the evening digest always
  run. This is how a zipper is meant to be run.
- **Their own computer** -- fine for trying it out, but when the lid is closed or the
  machine is asleep the zipper is gone: Discord messages go unanswered, and scheduled
  passes and digests are skipped until it wakes.

Note the answer; it changes how secrets and the dashboard are reached.

**If it is a server, set up Tailscale now**, before anything asks for a secret. Tailscale
puts the server and their phone and laptop on one private network, so the secret pages and
the dashboard can be opened from their devices without being on the internet:

    tailscale version || curl -fsSL https://tailscale.com/install.sh | sh
    tailscale up          # prints a login link; they open it and sign in
    tailscale ip -4       # the server's private address, 100.x.y.z

They also need the Tailscale app on their phone and laptop, signed in to the same account.
From then on:

- run every secret page on that address: `../zipper secret NAME --host <100.x.y.z>`, and
  they open the link it prints on any of their devices;
- publish the dashboard on it: in `{{HOME}}/compose.yml`, change `127.0.0.1:8899:8899` to
  `<100.x.y.z>:8899:8899`.

If they would rather not use Tailscale, `../zipper secret NAME --tty` works instead: give
them the command to run in their own SSH window (with `cd {{HOME}}/vault` first); it asks
with a hidden prompt, and you check it with `--check`.

**If it is their own computer**, nothing to do: the secret links and the dashboard work on
`127.0.0.1` as they are.
