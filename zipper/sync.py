"""zipper.sync

Evidence -> dates. Reads Log/ backlinks; only ever moves them forward.
"""
import os, re, datetime, glob

from .core import *          # noqa: F401,F403 -- the shared vocabulary
from . import core



def log_latest():
    """{note title: YYYY-MM-DD} of the newest log entry linking each note."""
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
    return latest


def log_facts():
    """{note title: {'last_touched': YYYY-MM}} from the newest log entry linking each note.

    Only a fact, like any input's: `zipper.writer` decides what is written, and it
    moves `last_touched` forward only. Log evidence can prove a note was touched,
    never that it wasn't -- an unconditional write once dragged dates that `github`
    had set back to whatever the newest log mention happened to say.
    """
    return {t: {'last_touched': d[:7]} for t, d in log_latest().items()}


IGNORED_DAYS = 30

def last_work(d, linked=''):
    """The newest evidence of work on a note, YYYY-MM-DD, or '' with none: its newest
    log link, its last push, or a hand-set `last_touched` read at the *last* day it
    could mean (a year at Dec 31, a month at its last day). A coarse date only proves
    work somewhere inside it, and ignoring a project is fine -- so the doubt goes to
    not raising it early."""
    dates = [linked or '', str(d.get('last_push') or '')[:10]]
    lt = str(d.get('last_touched') or '')
    if MONTH_RE.match(lt):
        if len(lt) == 4:
            dates.append(lt + '-12-31')
        elif len(lt) == 7:
            y, m = int(lt[:4]), int(lt[5:7])
            nxt = datetime.date(y + m // 12, m % 12 + 1, 1)
            dates.append((nxt - datetime.timedelta(days=1)).isoformat())
        else:
            dates.append(lt)
    return max(dates)


def ignored_for(d, linked=''):
    """Days since the last evidence of work, or None with no evidence at all."""
    lw = last_work(d, linked)
    return _days_since(lw) if lw else None


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
- [[{tomorrow}]]
- 
"""

def cmd_today(a):
    os.makedirs(LOGDIR, exist_ok=True)
    day = a.date or core.TODAY.isoformat()
    p = os.path.join(LOGDIR, day + '.md')
    if not os.path.exists(p):
        dt = datetime.date(*[int(x) for x in day.split('-')])
        with open(p, 'w', encoding='utf-8') as fh:
            fh.write(LOG_TEMPLATE.format(date=day, day=dt.strftime('%A'),
                                         tomorrow=dt + datetime.timedelta(days=1)))
        print('created %s' % rel(p))
        chain_logs()
    else:
        print('exists  %s' % rel(p))
    return 0


CHAIN_RE = re.compile(r'^## Tomorrow\n- \[\[(\d{4}-\d\d-\d\d)\]\]\n', re.M)

def chain_logs():
    """Point each log's first `## Tomorrow` line at the next log, so the days read
    as one string in Obsidian. The newest points at the day after it, which
    resolves once that log exists. Logs are skipped by `sync`'s writer, so these
    links are never read as evidence of work. Returns the logs rewritten."""
    logs = sorted(p for p in glob.glob(os.path.join(LOGDIR, '*.md'))
                  if DATE_RE.match(title_of(p)))
    n = 0
    for i, p in enumerate(logs):
        day = title_of(p)
        nxt = (title_of(logs[i + 1]) if i + 1 < len(logs) else
               (datetime.date.fromisoformat(day) + datetime.timedelta(days=1)).isoformat())
        with open(p, encoding='utf-8') as fh:
            text = fh.read()
        m = CHAIN_RE.search(text)
        if m and m.group(1) == nxt:
            continue
        if m:
            new = text[:m.start(1)] + nxt + text[m.end(1):]
        elif re.search(r'^## Tomorrow$', text, re.M):
            new = re.sub(r'^## Tomorrow\n?', '## Tomorrow\n- [[%s]]\n' % nxt, text,
                         count=1, flags=re.M)
        else:
            new = text.rstrip('\n') + '\n\n## Tomorrow\n- [[%s]]\n' % nxt
        with open(p, 'w', encoding='utf-8') as fh:
            fh.write(new)
        n += 1
    return n
