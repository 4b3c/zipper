"""Canvas: assignments, and whether each is submitted.

Read by the browser extension from a logged-in tab and POSTed here; the engine holds
no Canvas credential. The storage and cross-offs live in `zipper.canvas`.
"""
from .. import canvas as lib
from . import OWNER, blob_fetched, as_list

name = 'canvas'


def receive(body):
    """Two payload shapes. The bookmarklet posts a bare array; the extension posts
    `{items, assignments, source}`, where `assignments` carries the bodies that show
    an assignment's work lives on another platform."""
    if isinstance(body, dict):
        items = body.get('items') or []
        assignments = body.get('assignments')
        source = str(body.get('source') or 'unknown')[:32]
    else:
        items, assignments, source = body, None, 'bookmarklet'
    rows, skipped, described = lib.ingest(items, assignments, source)
    return {'ok': True, 'kept': len(rows), 'described': described}


def fetched():
    return blob_fetched(lib.CANVAS_JSON)


def snapshot():
    return {r['title']: r['submitted'] for r in lib.raw_items()}


def events(before, after):
    out = []
    for title, done in sorted(after.items()):
        was = before.get(title)
        if was is None:
            out.append({'system': 'canvas', 'action': 'add', 'who': OWNER,
                        'text': '+ canvas    %s%s'
                                % (title, '  (already submitted)' if done else '')})
        elif done and not was:
            out.append({'system': 'canvas', 'action': 'submit', 'who': OWNER,
                        'text': 'submitted   %s' % title})
    return out


def target(row, notes):
    """The project a course's work lands in: the first `feeds:` entry on the
    `Classes/` note whose `code` appears in the row."""
    text = row.get('text', '').replace(' ', '')
    for title, d in notes:
        if d.get('type') == 'class' and d.get('code'):
            feeds = [str(f).strip().strip('[]') for f in as_list(d.get('feeds'))]
            if feeds and str(d['code']).strip().replace(' ', '') in text:
                return feeds[0]
    return None
