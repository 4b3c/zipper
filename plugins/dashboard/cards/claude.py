"""Claude: every conversation, each in its own terminal. Not live-swapped -- a
redraw would cut a terminal off mid-sentence."""
from zipper.web import home

TITLE = 'claude'


def panel(ctx):
    return home.claude_panel()
