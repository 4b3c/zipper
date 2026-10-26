"""The queue: what changed since the last commit, and the standing flags."""
from zipper.web import home

TITLE = 'queue'


def panel(ctx):
    return home.queue_card()
