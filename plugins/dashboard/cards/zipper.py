"""This zipper: execution metrics, plan usage, and the machine's vitals."""
from zipper.web import home

TITLE = 'zipper'
WIDTH = '320px'


def panel(ctx):
    return home.zipper_card()
