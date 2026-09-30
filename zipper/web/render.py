"""HTML for the dashboard and the view pages.

Everything that turns data into markup. The stylesheet and the browser code
are literals in `css.py` and `js.py`.

Split out of `zipper/serve.py` on 2026-09-07. That file had grown to 2,788
lines, which meant no part of it could be read without loading all of it.
"""
from .base import *
from .base import core, conversations, metrics, usage
from .css import CSS
from .js import TICKJS
from .conv import _queue_prompt, current_conversation
from .data import flags, monday_of, ranked, today_split, week_canvas
from .feed import feed_rows, note_rows


# ---------------------------------------------------------------- render




def esc(s):
    return html.escape(s or '')


def _lanes(timed):
    """Blocks with start/end in minutes and a lane each, so overlaps sit side
    by side instead of stacking into one ambiguous column."""
    blocks = []
    for e in timed:
        st = core._dt(e['start'])
        en = core._dt(e.get('end') or '')
        if st is None:
            continue
        s0 = st.hour * 60 + st.minute
        if en is None or en <= st:
            e0 = s0 + 30                      # no DTEND in the feed
        elif en.date() != st.date():
            e0 = 24 * 60                      # runs past midnight
        else:
            e0 = en.hour * 60 + en.minute
        blocks.append({'s': s0, 'e': max(e0, s0 + 20), 'ev': e})
    blocks.sort(key=lambda b: (b['s'], b['e']))
    free = []
    for b in blocks:
        for i, until in enumerate(free):
            if b['s'] >= until:
                free[i] = b['e']; b['lane'] = i; break
        else:
            b['lane'] = len(free); free.append(b['e'])
    # How wide a block may be is a question about *its own* collisions, not the
    # day's. Splitting every block by the day's worst pile-up meant one pair of
    # overlapping evening meetings halved the width of a morning class that
    # collided with nothing. So group the blocks into clusters of transitively
    # overlapping events and give each block the lane count of its own cluster.
    #
    # The greedy assignment above is still sound cluster-locally: a block only
    # takes lane i when every lower lane is held by something ending after it
    # starts -- that is, by something it overlaps, which is in its cluster. So a
    # block's lane index never exceeds its own cluster's count.
    cluster, end = [], None
    for b in blocks:
        if end is not None and b['s'] >= end:
            n = max(x['lane'] for x in cluster) + 1
            for x in cluster:
                x['nlanes'] = n
            cluster, end = [], None
        cluster.append(b)
        end = b['e'] if end is None else max(end, b['e'])
    if cluster:
        n = max(x['lane'] for x in cluster) + 1
        for x in cluster:
            x['nlanes'] = n
    return blocks, max(1, len(free))


GCAL_IDS = {}

def _gcal_ids():
    """label -> Google calendar id, read out of the remembered feed URLs.

    A Google iCal URL is .../ical/<calendar id>/private-<token>/basic.ics, so the
    id is already on disk; Canvas URLs have no such segment and get skipped.
    """
    if GCAL_IDS:
        return GCAL_IDS
    path = os.path.join(core.INBOX, 'calendars.json')
    if os.path.exists(path):
        for label, cfg in json.load(open(path, encoding='utf-8')).items():
            m = re.search(r'/ical/([^/]+)/', cfg.get('url', '') or '')
            if m and 'google.com' in cfg.get('url', ''):
                GCAL_IDS[label] = urllib.parse.unquote(m.group(1))
    return GCAL_IDS


def _gcal_link(e, recurring):
    """The Google Calendar page for one event.

    Google's `eid` is base64url(event_id + ' ' + calendar_id). The ICS UID gives
    the *series* id, so a recurring occurrence needs the instance suffix
    `_<UTC stamp>` rebuilt from its local start. Verified against three real
    htmlLinks from the Calendar API, one of them a recurring instance.
    """
    cal = _gcal_ids().get(e['label'])
    uid = e.get('uid') or ''
    if not cal or '@google.com' not in uid:
        return ''
    eid_src = uid.split('@')[0]
    if recurring:
        st = core._dt(e['start'])
        if st is None:
            return ''
        utc = st.astimezone(datetime.timezone.utc)
        eid_src += '_' + utc.strftime('%Y%m%dT%H%M%SZ')
    eid = base64.urlsafe_b64encode(('%s %s' % (eid_src, cal)).encode()).decode().rstrip('=')
    return 'https://www.google.com/calendar/event?eid=' + eid


def _join_link(e):
    """A meeting URL hiding in the location field — Zoom, Meet, Teams."""
    for cand in (e.get('loc') or '', e.get('url') or ''):
        m = re.search(r'https?://\S+', cand)
        if m and re.search(r'zoom|meet\.google|teams\.microsoft|webex', m.group(0)):
            return m.group(0)
    return ''


def _obsidian(note):
    return ('obsidian://open?vault=%s&amp;file=%s'
            % (urllib.parse.quote(os.path.basename(core.VAULT)),
               urllib.parse.quote(note)))


# The assignment body is prose with list structure flattened into it by
# `canvas._html_to_text`. Splitting on the bullet glyph is enough to get it back
# to something scannable -- and scannable is the whole requirement, because the
# question being answered is "what is this and who does it", not "render Canvas
# faithfully".
def _detail_html(it):
    """What a row expands into. Empty string when there is nothing to add."""
    bits = []
    if it.get('desc'):
        txt = re.sub(r'\s+', ' ', it['desc']).strip()
        parts = [p.strip(' -') for p in re.split(r'\s+-\s+', txt) if p.strip(' -')]
        if len(parts) > 1:
            head, rest = parts[0], parts[1:]
            bits.append('<p>%s</p><ul class="dl">%s</ul>'
                        % (esc(head[:400]),
                           ''.join('<li>%s</li>' % esc(p[:300]) for p in rest[:24])))
        else:
            bits.append('<p>%s</p>' % esc(txt[:1200]))
    acts = []
    if it.get('url'):
        acts.append('<a href="%s" target="_blank" rel="noopener">open in Canvas</a>'
                    % esc(it['url']))
    if it.get('course'):
        acts.append('<a href="%s/courses/%s/assignments" target="_blank" rel="noopener">'
                    'course assignments</a>'
                    % (esc(canvas.CANVAS_HOST.rstrip('/')), esc(it['course'])))
    for n in (it.get('links') or []):
        acts.append('<a href="%s">open %s</a>' % (_obsidian(n), esc(n)))
    if it.get('points'):
        acts.append('<span class="sub">%s pts</span>' % esc(str(it['points'])))
    if it.get('kind'):
        acts.append('<span class="sub">%s</span>' % esc(it['kind']))
    if acts:
        bits.append('<p class="dacts">%s</p>' % ' &middot; '.join(acts))
    if not bits:
        return ''
    return '<div class="rowdet">%s</div>' % ''.join(bits)


def _item_li(it, show_score=True, detail=False):
    """One task row. Title leads; everything else drops to a dim second line.

    Priority leads that second line. It was a tooltip for a while, on the theory
    that a number beside the title competed with it — but the `#next` tag that
    was supposed to carry urgency is on half the open tasks, so it stopped
    discriminating. The score is the only thing that actually orders the list,
    so it says so out loud."""
    # Display only. `data.task_text` keeps the brackets because the ledger and
    # the queue key a task by that exact string -- stripping them there would
    # orphan every existing cross-off. The note is reachable from the detail
    # panel, so the title only has to read well.
    title = esc(re.sub(r'\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]', r'\1', it['title']))
    if it['url']:
        title = '<a class="plain" href="%s" target="_blank" rel="noopener">%s</a>' % (esc(it['url']), title)
    meta = []
    if show_score:
        meta.append('<span class="pri">%d</span>' % it['score'])
    if it['due']:
        meta.append('<span class="%s">%s</span>' % ('od' if it['overdue'] else '', it['due'][5:]))
    if it['tag']:
        meta.append(esc(it['tag']))
    if it.get('elsewhere'):
        # Canvas keeps only a grade column for these, so its "not submitted" is
        # silence, not a fact. Say which platform actually holds the work rather
        # than nagging about something already handed in.
        meta.append('<span class="elsewhere">on %s &mdash; Canvas can\'t tell</span>'
                    % esc(it['elsewhere']))
    det = _detail_html(it) if detail else ''
    if det:
        meta.append('<span class="more">details</span>')
    return ('<li class="row %s%s" title="priority %d">'
            '<button class="tick" data-key="%s" aria-label="cross off">%s</button>'
            '<span class="rowbody"><span class="rowtitle">%s</span>'
            '<span class="rowmeta">%s</span>%s</span>%s</li>'
            % ('crossed' if it.get('done') else '', ' has-det' if det else '',
               it['score'], esc(it['key']),
               '&#10003;' if it.get('done') else '', title,
               ' &middot; '.join(meta), det,
               # delete is for tasks only; a Canvas row belongs to Canvas
               '<button class="del" data-key="%s" aria-label="delete task" '
               'title="delete: not doing it, or done but not by me">&times;</button>'
               % esc(it['key']) if str(it['key']).startswith('task:') else ''))


def _side(items, empty, detail=False):
    if not items:
        return '<p class="sub">%s</p>' % empty
    return '<ul>' + ''.join(_item_li(i, detail=detail) for i in items) + '</ul>'


VIEWS_JSON = os.path.join(core.INBOX, 'views.json')

def views_blob():
    try:
        return json.load(open(VIEWS_JSON, encoding='utf-8'))
    except Exception:
        return {'views': {}, 'pages': [], 'generated': ''}

def _cell(c):
    """A cell is a scalar, or {'link': 'Note'} when it should open in Obsidian."""
    if isinstance(c, dict) and 'link' in c:
        return ('<a class="vlink" href="obsidian://open?vault=%s&amp;file=%s">%s</a>'
                % (urllib.parse.quote(os.path.basename(core.VAULT)),
                   urllib.parse.quote(c['link']), esc(c['link'])))
    if c is None or c == '':
        return '<span class="vnone">—</span>'
    return esc(str(c))

def _view_html(v, compact=False):
    if not v:
        return ''
    head = ''.join('<th>%s</th>' % esc(c) for c in v['columns'])
    rows = v['rows'][:6] if compact else v['rows']
    body = ''.join('<tr>%s</tr>' % ''.join('<td>%s</td>' % _cell(c) for c in r)
                   for r in rows)
    more = ''
    if compact and len(v['rows']) > 6:
        more = '<p class="sub vmore">+%d more</p>' % (len(v['rows']) - 6)
    if not v['rows']:
        return ('<div class="vwrap"><p class="sub">%s</p></div>' % esc(v['empty']))
    note = ('<p class="sub vnote">%s</p>' % esc(v['note'])) if v.get('note') and not compact else ''
    return ('<div class="vwrap">%s<table class="vtable"><thead><tr>%s</tr></thead>'
            '<tbody>%s</tbody></table>%s</div>' % (note, head, body, more))

def _views_page(page_key):
    blob = views_blob()
    pages = blob.get('pages', [])
    page = next((p for p in pages if p['key'] == page_key), None)
    if not page:
        return None
    nav = ' '.join('<a class="vnav%s" href="/views/%s">%s</a>'
                   % (' on' if p['key'] == page_key else '', p['key'], esc(p['title']))
                   for p in pages)
    cards = []
    for key in page['views']:
        v = blob['views'].get(key)
        if not v:
            continue
        cards.append('<div class="card"><h2>%s <span class="sub">&middot; %d</span></h2>%s</div>'
                     % (esc(v['title']), v['count'], _view_html(v)))
    stamp = blob.get('generated', '')[:16].replace('T', ' ')
    return """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>%s</title>
<style>%s</style></head><body><div class="wrap">
<h1>%s</h1><p class="sub">%s</p>
<p class="sub vnavbar"><a class="plain" href="/">&larr; today</a> &middot; %s</p>
%s
<footer><div class="fresh">computed %s &middot; <code>zipper views</code></div></footer>
</div></body></html>""" % (esc(page['title']), CSS, esc(page['title']),
                           esc(page['note']), nav, ''.join(cards), esc(stamp))


def _list_page(kind):
    _, allitems = ranked()
    items = [i for i in allitems if i['source'] == kind]
    label = 'Canvas' if kind == 'canvas' else 'Tasks'
    # Detail only here, never on the front card. The card answers "what is most
    # pressing" in one glance and a description would bury the ranking; this page
    # is where they have already asked about one specific thing.
    body = _side(items, 'Nothing here.', detail=True)
    return """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>%s</title>
<style>%s</style></head><body><div class="wrap">
<h1>%s <span class="sub">&middot; %d</span></h1>
<p class="sub"><a class="plain" href="/">&larr; back to today</a></p>
<div class="card">%s</div>
</div><script>%s</script></body></html>""" % (label, CSS, label, len(items), body, TICKJS)
