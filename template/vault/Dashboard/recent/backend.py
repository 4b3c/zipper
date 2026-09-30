"""The notes edited most recently -- a small example of a vault card.

`data` finds them; `frontend.py` draws them."""
import os, time

HIDE = {'.git', '.obsidian', 'Inbox', 'Dashboard'}
NOT_NOTES = {'CLAUDE.md', 'README.md'}


def data(ctx):
    notes = []
    for base, dirs, files in os.walk(ctx.vault):
        dirs[:] = [d for d in dirs if d not in HIDE]
        for f in files:
            if f.endswith('.md') and f not in NOT_NOTES:
                p = os.path.join(base, f)
                notes.append((os.path.getmtime(p), os.path.relpath(p, ctx.vault)))
    notes.sort(reverse=True)
    n = int(ctx.options.get('show', 6))
    return {'items': [{'title': rel[:-3], 'when': time.strftime('%b %d %H:%M', time.localtime(t))}
                      for t, rel in notes[:n]],
            'count': len(notes)}
