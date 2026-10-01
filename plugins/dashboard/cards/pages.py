"""The catalog: a link to every board in `plugins.dashboard.pages`, and to the
saved-query pages under /views."""
from zipper.web import cards, home

TITLE = 'pages'


def data(ctx):
    return {'pages': [{'href': '/p/' + p['key'], 'title': p['title'], 'about': p['about']}
                      for p in cards.pages()],
            'views': [{'href': '/views/' + v['key'], 'title': v['title']}
                      for v in home.views_blob().get('pages', [])]}


def render(d, ctx, ui):
    tiles = ''.join('<a class="pgtile" href="%s"><b>%s</b>%s</a>'
                    % (ui.esc(p['href']), ui.esc(p['title']),
                       '<span>%s</span>' % ui.esc(p['about']) if p['about'] else '')
                    for p in d['pages'])
    if not tiles:
        tiles = ('<p class="empty">No pages yet -- add one under '
                 '<code>plugins.dashboard.pages</code> in settings.json.</p>')
    views = ' &middot; '.join('<a href="%s">%s</a>' % (ui.esc(v['href']), ui.esc(v['title']))
                              for v in d['views'])
    return ('<div class="pgcat">%s</div>%s'
            % (tiles, '<p class="pgviews">views: %s</p>' % views if views else ''))

