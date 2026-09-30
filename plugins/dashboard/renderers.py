"""Frontends any card can name instead of writing its own ("frontend": "dashboard:list").
Each takes (data, ctx, ui) and returns the card's body."""


def list(data, ctx, ui):
    """data: {"items": [{title, sub, url, when, done}], "empty": "..."}"""
    return ui.items(data.get('items') or [], empty=data.get('empty') or 'Nothing here.')


def text(data, ctx, ui):
    """data: {"text": "..."} -- a paragraph, escaped."""
    return '<p>%s</p>' % ui.esc(data.get('text', ''))
