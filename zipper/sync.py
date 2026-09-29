"""zipper.sync

Evidence -> dates. Reads Log/ backlinks; only ever moves them forward.
"""
import os, datetime, glob

from .core import *          # noqa: F401,F403 -- the shared vocabulary
from . import core



def log_facts():
    """{note title: {'last_touched': YYYY-MM}} from the newest log entry linking each note.

    Only a fact, like any input's: `zipper.writer` decides what is written, and it
    moves `last_touched` forward only. Log evidence can prove a note was touched,
    never that it wasn't -- an unconditional write once dragged dates that `github`
    had set back to whatever the newest log mention happened to say.
    """
    latest = {}
    for lp in sorted(glob.glob(os.path.join(LOGDIR, '*.md'))):
        stem = title_of(lp)
        if not DATE_RE.match(stem):
            continue
        with open(lp, encoding='utf-8') as fh:
            text = fh.read()
        for link in LINK_RE.findall(text):
            link = link.strip()
            if link > '' and (link not in latest or stem > latest[link]):
                latest[link] = stem
    return {t: {'last_touched': d[:7]} for t, d in latest.items()}


def cmd_sync(a):
    """Derive last_touched from Log/ backlinks, through the writer."""
    from . import writer
    n = writer.apply(log_facts(), source='log')
    print('sync: %d note(s) updated from %d log entr(ies)' %
          (n, len(glob.glob(os.path.join(LOGDIR, '*.md')))))
    return 0

def cmd_touch(a):
    when = a.date or core.TODAY.isoformat()
    hits = [p for p in iter_notes() if title_of(p).lower() == a.note.lower()]
    if not hits:
        print('no note titled "%s"' % a.note); return 1
    pairs, body = read_note(hits[0])
    set_field(pairs, 'last_touched', when[:7])
    write_note(hits[0], pairs, body)
    print('%s last_touched -> %s' % (title_of(hits[0]), when[:7]))
    return 0

LOG_TEMPLATE = """---
type: log
date: {date}
---
# {date} ({day})

## Did
- 

## Decided
- 

## Friction
- 

## Tomorrow
- 
"""

def cmd_today(a):
    os.makedirs(LOGDIR, exist_ok=True)
    day = a.date or core.TODAY.isoformat()
    p = os.path.join(LOGDIR, day + '.md')
    if not os.path.exists(p):
        dt = datetime.date(*[int(x) for x in day.split('-')])
        with open(p, 'w', encoding='utf-8') as fh:
            fh.write(LOG_TEMPLATE.format(date=day, day=dt.strftime('%A')))
        print('created %s' % rel(p))
    else:
        print('exists  %s' % rel(p))
    return 0
