"""The other boards: /p/<key>, one per entry in `plugins.dashboard.pages`.

The front page's machinery and look, with fewer moving parts: no day strip, no
Claude terminal, just the page's rows of cards, live-updated the same way.
"""
import json

from .home import CSS, NAV, PAGE_JS, _page, esc
from .cards import CARD_CSS, CARD_JS
from .live import LIVE_JS, live_sig
from . import cards


def page(key):
    """The page's HTML, or None if no page has that key."""
    p = cards.page_of(key)
    if p is None:
        return None
    body = ('<div class="wrap">%s'
            '<div class="head"><h1>%s</h1><span class="meta">%s</span>'
            '<a class="back" href="/">&larr; dashboard</a></div>%s</div>'
            % (NAV, esc(p['title']), esc(p['about']), cards.body(None, key)))
    state = '<script>window.__sig=%s;</script>' % json.dumps(live_sig(None, key))
    return _page('%s · Zipper' % p['title'], CSS + CARD_CSS, body + state,
                 PAGE_JS + LIVE_JS + CARD_JS)
