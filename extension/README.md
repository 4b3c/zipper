# Zipper Collector

A browser extension that reads what only a logged-in browser can see and hands it to Zipper.

**Why it exists:** Canvas' `canvas_session` cookie is rotated by the school's SSO several
times a day, so a copy in `.env` starts dying the moment it's taken. The same session in a
browser never dies, because the browser renews it. `zipper/canvas.py` was fine; only the
credential's lifetime failed. Moving the request into the browser removes lifetime from the
problem.

**It never concludes.** A collector fetches JSON and posts it. Every judgment — what a
submission means, which note it lands on — happens in the backend, next to the vault. The
moment a collector interprets, there are two systems thinking, and they'll disagree.

## Layout

```
manifest.json          both browsers, one file
background.js          the reporter; the only part that knows Zipper exists
collectors/canvas.js   one site: fetches, posts, concludes nothing
ui/canvas-todo.js      draws Zipper's work list into Canvas
options.html/.js       where Zipper lives, granted as a runtime permission
```

Adding a site is one file in `collectors/` plus a `content_scripts` entry. `ui/` is the other
direction — it draws Zipper into the page, under the same rule: it renders a list it didn't
rank and writes through an endpoint it doesn't own.

## When a reading is sent

A collector runs on every page load; `background.js` decides what goes out, by hash:

| Trigger | Sent? |
|---|---|
| Page load, reading differs from the last one Zipper acknowledged | **yes** |
| Page load, identical | no |
| 30-minute heartbeat in an open tab | **always** |

- The hash is SHA-256 over sorted keys, minus a small `VOLATILE` list (`new_activity` flips
  when an item is merely viewed).
- **The heartbeat ignores the hash** so `canvas.json`'s `fetched` stamp can tell
  "unchanged, read just now" from "unchanged, read this morning".
- **The hash is stored only after Zipper acknowledges the POST**, so a failed send is
  retried. A 60-second floor stops two tabs double-sending; it never suppresses the heartbeat.

## The Canvas to-do panel

`ui/canvas-todo.js` replaces Canvas' sidebar *To Do* and *Coming Up* with this Monday–Sunday
week's coursework from `GET /api/worklist` (`data.week_worklist()`). Ticking a row POSTs
`/api/done` — the dashboard's endpoint, so a cross-off lands in the vault.

- **Coursework only.** Not the dashboard's `ranked()`, which mixes in tasks. In Canvas the
  question is "what's due this week".
- **Unfinished work from before Monday leads**, marked `still open`.
- **Finished rows sit behind a Done tab.** They stay (a vanished row looks like a failed
  cross-off) but don't bury open work.
- **A week-progress bar** above the tabs, and **grade badges** on course cards from
  `/api/v1/courses?include[]=total_scores`. `--%` means nothing graded, not zero. Badges never
  reach Zipper — a course score isn't a conclusion.
- **No ranking in the browser.** Two surfaces disagreeing about what's pressing is worse than
  Canvas' own bad list. The browser draws; Zipper decides.

How it stays safe inside Canvas:
- **A shadow root**, because Canvas ships broad `!important` CSS. `all: initial` walls it out
  but also resets `display` to `inline` — set it back.
- **Native widgets are hidden, never removed, and only after a successful fetch.** If Zipper
  is unreachable the panel says so and Canvas' list stays.
- **Remounted from a coalesced `MutationObserver`**: the sidebar renders late and React
  restores hidden widgets.
- **Each content script is a closure.** Every frame gets one isolated world, so two files
  declaring `const api` throws on line 1. `node --check` won't catch it.
- The page can reach exactly the two paths in `ALLOWED` in `background.js`.

## Why Canvas is a content script

`canvas_session` is SameSite, so a request from the background context goes without it and
Canvas answers with the SSO login page **at status 200** — parsed as an empty planner, it
says nothing is due. From a content script the request is same-origin. The POST *out* is the
reverse: from the page CORS refuses it, so it goes from the background.

## Install

**Chrome / Arc** — `chrome://extensions`, Developer mode, *Load unpacked*, pick this directory.

**Firefox / Zen** — release Firefox won't permanently install an unsigned add-on. For
development, `about:debugging` → *Load Temporary Add-on* (gone on restart). To ship, sign it
as an **unlisted** AMO add-on:

```bash
python3 -m zipper ext                 # what's built, what's served
python3 -m zipper ext --build         # bump, sign at AMO, publish to data/ext/
python3 -m zipper ext --build --bump minor
```

The build writes the `.xpi` and `updates.json` to `data/ext/`, served by the dashboard at
`/ext/`. Browsers poll `update_url` and update themselves. Needs `AMO_JWT_ISSUER`,
`AMO_JWT_SECRET`, and `ZIPPER_EXT_BASE`.

- **Every machine installs once by hand.** Firefox Sync reinstalls add-ons from AMO's public
  catalog, and an unlisted one isn't in it.
- **`update_url` is inside the signature.** The tracked manifest holds a placeholder;
  `--build` fills it from `ZIPPER_EXT_BASE` before upload and restores it after
  (`--clean` restores it by hand).
- **Serve the `.xpi` as `application/x-xpinstall`**, or Firefox downloads it as a file.
- **AMO never accepts a version twice**, even from a failed upload, so `--build` keeps the
  bump on failure.

Then set the dashboard address in the extension's options — the HTTPS MagicDNS name,
`https://<machine>.<tailnet>.ts.net:9443`. It's requested as a runtime permission because a
tailnet address doesn't belong in a public manifest.

## Traps, all invisible outside a real browser

- **No `strict_min_version`.** Zen reports its own version (`1.22.2b`) as the application
  version while running Gecko 156, so a Firefox-numbered floor is unsatisfiable and the
  install is refused silently. Check `Services.appinfo.version` in the Browser Console
  (needs `devtools.chrome.enabled`).
- **HTTPS-Only mode** (on by default in Zen) rewrites `http://` to `https://`; against a
  plain-HTTP server `fetch` reports a generic CORS error. Read the scheme in the error. Hence
  the Tailscale certificate on 9443 — nginx owns 443 on the box.
- **A Flatpak browser can't read the unpacked directory.** The file picker grants
  `manifest.json` and nothing beside it: blank options page, empty sources. Use a signed
  `.xpi`, or `flatpak override --user --filesystem=...`.
- **`curl -I` gets 501.** The server doesn't implement `HEAD`; use `-i`.
- **`/ext/` logs the requesting tailnet IP and User-Agent**, because `tailscale serve` makes
  everything arrive from `127.0.0.1`. That's how you tell which machine fetched.

## Status

| | |
|---|---|
| Chrome / Arc, Canvas | Working end to end (2026-09-08): 110 items, `source: "extension"` |
| Firefox / Zen, Canvas | Working, signed and self-updating (2026-09-17), Fedora and macOS |
| To-do panel, tabs, week bar, badges | Rendering in Chrome and Zen (2026-09-17) |
| Ticking through to `/api/done` | Working both ways (2026-09-17) |
| Unreachable-Zipper path | **Unexercised.** Stop `zipper-web` and load Canvas |
| Hash-gated sending | **Unwatched.** A second load appears to skip, but only `skipped: 'unchanged'` in the background console proves it |
| Any site but Canvas | Nothing exists. The one-collector-per-site shape is untested |

**The limitation:** it only runs while a Canvas tab is open. `canvas.json` records `source`
and `fetched` so stale data says it's stale — better than the expired cookie it replaced,
which left the flags wrong with nothing admitting it.

## Cross-browser notes

- One manifest declares **both** background forms: `service_worker` (Chrome) and `scripts`
  (Firefox). Chrome warns `'background.scripts' requires manifest version of 2 or lower` —
  harmless. Don't delete the key; Firefox would have no background.
- `const api = globalThis.browser ?? globalThis.chrome;`
- `browser_specific_settings.gecko` is required by Firefox, ignored by Chrome.
- **Written for Chrome's service worker**, which is killed aggressively: no module-level
  state in `background.js`; everything durable is in `storage`.
