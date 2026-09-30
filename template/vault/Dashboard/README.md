# Dashboard

The dashboard is rows of cards, listed in `settings.json` under `plugins.dashboard.rows`.
A card comes from one of three places:

- `dashboard:<name>` — built in: `queue`, `claude`, `week`, `today`, `zipper`,
  `sources`, and `todo` (the checkboxes in any markdown file).
- `<plugin>:<name>` — brought by a plugin while it is on, e.g. `canvas:week`.
- `vault:<name>` — yours, in this folder: `Dashboard/<name>/backend.py`, and
  optionally `frontend.py` beside it.

A vault card's **backend** has `data(ctx)`, returning what the card shows. Its
**frontend** has `render(data, ctx, ui)`, returning the card's HTML; `ui` gives you
`ui.items(rows)`, `ui.button(action, label, **args)` and `ui.esc(text)`, so it matches
the rest of the page. Without a frontend, `{"frontend": "dashboard:list"}` draws
`data["items"]` as a list.

A button calls `act_<action>(args, ctx)` in the backend; whatever string it returns is
shown briefly in the card's header. `recent/` is a small working example.

A card that crashes shows its error inside its own frame, and one that takes longer
than four seconds is skipped for a minute; the rest of the page is never affected.
