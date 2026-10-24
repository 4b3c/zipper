"""Claude: every conversation, each in its own terminal. Not live-swapped -- a
redraw would cut a terminal off mid-sentence.

Its script and styles ride with the card, so a layout without it loads neither and
nothing polls for conversations."""
import json

from zipper.web import home
from zipper.web.css import TERM_CSS as CSS      # noqa: F401 -- read by cards.assets
from zipper.web.js import TERM_JS as JS         # noqa: F401

TITLE = 'claude'


def panel(ctx):
    state = ('<script>window.__session=%s;window.__queueready=%s;window.__mounted=false;'
             '</script>' % (json.dumps(bool(home.current_conversation())),
                            json.dumps(bool(home._queue_prompt()))))
    return home.claude_panel() + state
