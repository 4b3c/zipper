"""How old each plugin's data is, with a refresh button for those that pull, and
the saved-query pages."""
import threading

from zipper import plugins
from zipper.web import home
from zipper.web.cards import UI

TITLE = 'sources'


def data(ctx):
    fresh = home.freshness()
    pulling = {i.name for i in plugins.pullers()}
    return {'sources': [{'name': k, 'ago': home.ago(v), 'pulls': k in pulling}
                        for k, v in sorted(fresh.items(), key=lambda kv: kv[0] != 'vault')],
            'pages': [(p['key'], p['title']) for p in home.views_blob().get('pages', [])]}


def render(d, ctx, ui):
    chips = ''.join('<span class="chip"><b>%s</b> %s%s</span>'
                    % (ui.esc(s['name']), ui.esc(s['ago']),
                       ' ' + ui.button('pull', '\u21bb', name=s['name']) if s['pulls'] else '')
                    for s in d['sources'])
    links = ' &middot; '.join('<a href="/views/%s">%s</a>' % (ui.esc(k), ui.esc(t))
                              for k, t in d['pages'])
    return ('<footer class="foot" data-live="%s" data-card="%s"><div>%s</div>'
            '<div class="vn">%s</div></footer>' % (ui.esc(ctx.card_id), ui.esc(ctx.card_id),
                                                  chips, links))


def panel(ctx):
    return render(data(ctx), ctx, UI)


def sig(ctx):
    # Ages change with the clock; the page's five-minute bucket covers that.
    return ','.join(sorted(plugins.names()))


def act_pull(args, ctx):
    name = args.get('name', '')
    if name not in {i.name for i in plugins.pullers()}:
        return 'nothing to pull for %s' % name
    from zipper.web.feed import do_refresh
    threading.Thread(target=do_refresh, args=([name],), daemon=True).start()
    return 'pulling %s' % name
