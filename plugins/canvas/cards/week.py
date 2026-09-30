"""The week's coursework: open and handed-in tabs, by day, with what was carried
in from before, and a refresh button that pulls Canvas alone."""
import threading

from zipper.web import home
from zipper.web.home import esc, _row

TITLE = 'canvas'


def panel(ctx):
    day = ctx.day
    wk = home.week_canvas(day)
    allit = [it for v in wk['days'].values() for it in v]
    nopen = sum(1 for it in allit if not it['done'])
    carry = ''
    if wk['carried']:
        carry = ('<div class="carry"><div class="ch">Carried in &middot; %d</div><ul>%s</ul></div>'
                 % (len(wk['carried']),
                    ''.join(_row(it, showdue=True, pill=True) for it in wk['carried'])))
    return ('<div class="panel" data-tabs data-live="%s" data-card="%s"><div class="ph">canvas'
            '<span class="tabs"><button class="tabb on" data-tab="open">open'
            '<span class="c">%d</span></button>'
            '<button class="tabb" data-tab="done">done<span class="c">%d</span></button>'
            '<button class="tabb" data-act="pull" data-args="{}" title="pull Canvas now">'
            '↻</button>'
            '</span></div>'
            '<div class="pb" data-pane="open">%s%s</div>'
            '<div class="pb" data-pane="done" hidden>%s</div></div>'
            % (esc(ctx.card_id), esc(ctx.card_id), nopen, len(allit) - nopen, carry,
               home.day_sections(day, wk, lambda it: not it['done'])
               or '<p class="empty">Nothing due this week.</p>',
               home.day_sections(day, wk, lambda it: it['done'])
               or '<p class="empty">Nothing handed in this week yet.</p>'))


def act_pull(args, ctx):
    from zipper.web.feed import do_refresh
    threading.Thread(target=do_refresh, args=(['canvas'],), daemon=True).start()
    return 'pulling Canvas'
