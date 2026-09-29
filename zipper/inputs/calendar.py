"""Calendars: every ICS feed remembered by `zipper ingest-ics <url> --label X`.

Parsing and recurrence live in `zipper.ics`; this is its face as an input.
"""
import argparse, datetime, glob, json, os

from .. import core, ics
from . import blob_fetched

name = 'calendar'

CADENCE = {1: 'daily', 7: 'weekly', 14: 'fortnightly', 28: '4-weekly'}


def pull():
    ics.cmd_calendars(argparse.Namespace())


def _files():
    return sorted(glob.glob(os.path.join(core.INBOX, 'calendar-*.json')))


def fetched():
    """The oldest feed's age: one stale calendar makes the whole day suspect."""
    stamps = [s for s in (blob_fetched(f) for f in _files()) if s]
    return min(stamps) if stamps else None


def snapshot():
    """Every occurrence as (start, summary, uid). The uid is the same across a
    recurring series, which is what lets `events` group one."""
    out = set()
    for f in _files():
        try:
            for e in json.load(open(f, encoding='utf-8'))['events']:
                out.add((e['start'], e['summary'], e.get('uid') or ''))
        except Exception:
            pass
    return out


def _shape(starts):
    """'(weekly x58, through 2027-10-06)'. A cadence is only named when every gap is
    the same; a series with holidays cut out says 'repeats' rather than invent one."""
    if len(starts) < 2:
        return ''
    days = sorted({(datetime.date.fromisoformat(b[:10])
                    - datetime.date.fromisoformat(a[:10])).days
                   for a, b in zip(starts, starts[1:])})
    word = CADENCE.get(days[0]) if len(days) == 1 else None
    return '  (%s ×%d, through %s)' % (word or 'repeats', len(starts), starts[-1][:10])


def _window():
    """The part of the ingest window that did not slide since the last fetch.

    `parse_ics` expands recurrence over -180/+400 days *from today*, so an event
    crosses an edge just because a day passed. Only events inside both the old and
    new windows can have really changed. The slide is read from the feed's
    `last_fetch`, not assumed to be one day: a box that was off for a week slides
    seven at once.
    """
    last = None
    try:
        last = json.load(open(os.path.join(core.INBOX, 'feed.json'),
                              encoding='utf-8')).get('last_fetch')
    except Exception:
        pass
    try:
        slide = (core.TODAY - datetime.date.fromisoformat(last[:10])).days
    except Exception:
        slide = 1
    slide = max(1, min(slide, 400))
    return ((core.TODAY - datetime.timedelta(days=180 - slide)).isoformat(),
            (core.TODAY + datetime.timedelta(days=400 - slide)).isoformat())


def _rows(keys, action, lo, hi):
    """One row per series, not per occurrence. Events with no uid stay separate."""
    groups = {}
    for start, summary, uid in keys:
        groups.setdefault(uid or '%s|%s' % (start, summary), (summary, []))[1].append(start)
    out = []
    for summary, starts in groups.values():
        starts.sort()
        if not (lo <= starts[0][:10] <= hi):
            continue
        out.append({'system': 'calendar', 'action': action, 'when': starts[0],
                    'text': '%s calendar  %s  %s%s'
                            % ('+' if action == 'add' else '-', starts[0],
                               summary, _shape(starts))})
    return sorted(out, key=lambda e: e['when'])


def events(before, after):
    lo, hi = _window()
    return _rows(after - before, 'add', lo, hi) + _rows(before - after, 'remove', lo, hi)
