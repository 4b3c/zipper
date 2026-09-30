"""The evening digest: once a day, a Discord message of what is due tomorrow and
after. The message itself is zipper/digest.py; it is empty without plugins that
provide work items (Canvas, tasks).
"""
name = 'digest'


def jobs(conf):
    return [('digest', conf['time'])] if conf.get('time') else []
