"""Scheduled passes: at set times, `zipper pass` fetches, and if the brief has anything
a headless Claude works it into the notes and commits. Discord hears only if
something needs the owner. The pass itself is zipper/scheduled.py.
"""
name = 'passes'


def jobs(conf):
    return [('pass', t) for t in conf.get('times') or []]
