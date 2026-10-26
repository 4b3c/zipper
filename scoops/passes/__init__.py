"""Scheduled passes: at set times, `zipper pass` fetches, and if the brief has anything
a headless Claude works it into the notes and commits. Discord hears only if
something needs the owner. The pass itself is zipper/scheduled.py.

`queue_trigger`: also run one early when that many queue rows are unticked, so a
busy day is worked in before the queue's 100-row cap. 0 turns it off.
"""
name = 'passes'


def jobs(conf):
    out = [('pass', t) for t in conf.get('times') or []]
    if conf.get('queue_trigger'):
        out.append(('pass', 'queue:%d' % int(conf['queue_trigger'])))
    return out
