"""Canvas: assignments, when they're due, and whether each is done.

One subject, two ways in, joined here and nowhere else:

- **Due dates** come from Canvas' ICS feed (the `canvas` label), pulled like any
  calendar. Current whenever the fetch runs.
- **Submitted, bodies and points** come from the browser extension, which reads a
  logged-in tab and POSTs here. The engine holds no Canvas credential, so this is
  only as fresh as the last time the operator had Canvas open.

So a calendar row for an assignment knows whether it's done, and a work item knows
its course's note. Storage and cross-offs live in `zipper.canvas`.
"""
import argparse, datetime, glob, os

from .. import canvas as lib, core, ics
from . import OWNER, as_list, blob_fetched, event_row, read_calendar

name = 'canvas'
SETUP_TITLE = 'Canvas'
SETUP_ABOUT = 'assignments and due dates; submission status comes from the browser extension'


def setup(w):
    w.setting('inputs.canvas.host', 'Your Canvas address, e.g. https://canvas.example.edu')
    w.say('Canvas -> Calendar -> "Calendar Feed" gives a private URL.')
    url = w.ask('Canvas calendar feed URL (blank to add later)')
    if url:
        w.run(['ingest-ics', url, '--label', 'canvas'])
    w.say('For submitted/not-submitted, install the extension in extension/ -- see its README.')
CALENDARS = ('canvas',)
ICS_FILE = os.path.join(core.INBOX, 'calendar-canvas.json')


def pull():
    """Refetch the Canvas ICS feed. The extension's half arrives by `receive`."""
    ics.cmd_calendars(argparse.Namespace(only=CALENDARS))


def receive(body):
    """Two payload shapes. The bookmarklet posts a bare array; the extension posts
    `{items, assignments, source}`, where `assignments` carries the bodies that show
    an assignment's work lives on another platform."""
    if isinstance(body, dict):
        items = body.get('items') or []
        assignments = body.get('assignments')
        source = str(body.get('source') or 'unknown')[:32]
    else:
        items, assignments, source = body, None, 'bookmarklet'
    rows, skipped, described = lib.ingest(items, assignments, source)
    return {'ok': True, 'kept': len(rows), 'described': described}


def fetched():
    """The extension's reading: that's the half that goes stale."""
    return blob_fetched(lib.CANVAS_JSON)


# ---------------------------------------------------------------- the join

def _done_map():
    """{(YYYY-MM-DD, normalized title): done} from the extension's items.

    *Done*, not *submitted*: a crossed-off item strikes through too, or the Today
    grid would contradict the Week card. Canvas dates a 23:59 deadline on its own
    day and the ICS feed often on the next, so both dates are accepted.
    """
    out = {}
    for r in lib.items():
        if not r['due']:
            continue
        out[(r['due'][:10], core._norm_title(r['title']))] = lib.is_done(r)
        nxt = (datetime.date(*map(int, r['due'][:10].split('-')))
               + datetime.timedelta(days=1)).isoformat()
        out.setdefault((nxt, core._norm_title(r['title'])), r['submitted'])
    return out


def timeline(first, last):
    label, evs = read_calendar(ICS_FILE)
    if not label:
        return []
    done = _done_map()
    return [event_row(label, e, done.get((e['start'][:10], core._norm_title(e['summary']))))
            for e in evs if first <= e['start'][:10] <= last]


def _class_notes():
    """course code -> the `Classes/` note for it. Nothing guessed: a class note
    without `code:` gets no link."""
    out = {}
    for p in glob.glob(os.path.join(core.VAULT, 'Classes', '*.md')):
        fm = dict(core.read_note(p)[0])
        if fm.get('code'):
            out[str(fm['code']).strip()] = core.title_of(p)
    return out


def work():
    cls = _class_notes()
    out = []
    for r in lib.items():
        out.append({'source': 'canvas', 'title': r['title'], 'due': r['due'][:10],
                    'at': r['due'][11:16], 'tag': r['course'], 'url': r['url'],
                    'points': r.get('points'), 'next': False,
                    'elsewhere': r.get('elsewhere', ''),
                    'desc': r.get('description', ''), 'kind': r.get('type', ''),
                    'links': [cls[r['course']]] if r['course'] in cls else [],
                    'course': '', 'submitted': bool(r['submitted']),
                    'done': bool(lib.is_done(r)),
                    'done_by_hand': bool(r.get('done_by_hand')),
                    'key': lib._ov_key(r['course'], r['title'])})
    return out


def toggle(key):
    return lib.toggle_override(key)


# ---------------------------------------------------------------- the queue

def snapshot():
    """The extension's items (title -> submitted), and the ICS feed's occurrences."""
    label, evs = read_calendar(ICS_FILE)
    return {'items': {r['title']: r['submitted'] for r in lib.raw_items()},
            'ics': {(e['start'], e['summary'], e.get('uid') or '') for e in evs}}


def events(before, after):
    from . import calendar
    out = []
    was_items, items = before.get('items', {}), after.get('items', {})
    for title, done in sorted(items.items()):
        was = was_items.get(title)
        if was is None:
            out.append({'system': 'canvas', 'action': 'add', 'who': OWNER,
                        'text': '+ canvas    %s%s'
                                % (title, '  (already submitted)' if done else '')})
        elif done and not was:
            out.append({'system': 'canvas', 'action': 'submit', 'who': OWNER,
                        'text': 'submitted   %s' % title})
    # Due dates moving in the feed: the same series grouping as any calendar.
    for r in calendar.events(before.get('ics', set()), after.get('ics', set())):
        r['system'] = 'canvas'
        r['text'] = r['text'].replace(' calendar  ', ' canvas    ', 1)
        out.append(r)
    return out


def target(row, notes):
    """The project a course's work lands in: the first `feeds:` entry on the
    `Classes/` note whose `code` appears in the row."""
    text = row.get('text', '').replace(' ', '')
    for title, d in notes:
        if d.get('type') == 'class' and d.get('code'):
            feeds = [str(f).strip().strip('[]') for f in as_list(d.get('feeds'))]
            if feeds and str(d['code']).strip().replace(' ', '') in text:
                return feeds[0]
    return None
