"""The selected day as a time grid, from every plugin's event rows."""
from zipper.web import home

TITLE = 'today'
WIDTH = '216px'


def panel(ctx):
    return home.day_panel(ctx.day)
