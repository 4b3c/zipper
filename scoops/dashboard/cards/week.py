"""The week, as a column of days; picking one sets the day every card shows."""
from zipper.web import home

TITLE = 'week'
WIDTH = '92px'


def panel(ctx):
    return home.week_panel(ctx.day)
