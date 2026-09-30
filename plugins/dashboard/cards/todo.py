"""A to-do list: the checkboxes in one markdown file, tickable from the card.

    {"card": "dashboard:todo", "id": "setup", "title": "setup",
     "options": {"file": "Dashboard/setup.md"}}

The file stays the record: a tick here rewrites that one line (`- [ ]` <-> `- [x]`),
exactly as ticking it in an editor would, and nothing is kept anywhere else.
"""
import os, re

from zipper.web.cards import UI

TITLE = 'to do'
BOX = re.compile(r'^(\s*[-*] )\[( |x|X)\] (.*)$')


def _path(ctx):
    f = ctx.options.get('file') or 'Tasks/Main.md'
    return f if os.path.isabs(f) else os.path.join(ctx.vault, f)


def data(ctx):
    items = []
    try:
        with open(_path(ctx), encoding='utf-8') as fh:
            lines = fh.read().splitlines()
    except FileNotFoundError:
        return {'items': [], 'count': 0, 'missing': ctx.options.get('file', '')}
    for n, ln in enumerate(lines):
        m = BOX.match(ln)
        if m:
            items.append({'line': n, 'title': m.group(3).strip(),
                          'done': m.group(2).lower() == 'x'})
    return {'items': items, 'count': sum(1 for i in items if not i['done'])}


def render(d, ctx, ui):
    if d.get('missing'):
        return '<p class="empty">No file at %s yet.</p>' % ui.esc(d['missing'])
    rows = [{'title': i['title'], 'done': i['done'],
             'lead': ui.button('tick', '\u2611' if i['done'] else '\u2610',
                               line=i['line'], title=i['title']).replace(
                                   'class="tabb cardact"', 'class="cardact box"', 1)}
            for i in d['items']]
    return ui.items(rows, empty='Nothing to do.')


def act_tick(args, ctx):
    """Flip one checkbox. Found by line number *and* text, so an edit made since the
    page was drawn can never tick the wrong line."""
    p = _path(ctx)
    with open(p, encoding='utf-8') as fh:
        lines = fh.read().split('\n')
    n, want = int(args.get('line', -1)), args.get('title', '')
    m = BOX.match(lines[n]) if 0 <= n < len(lines) else None
    if not m or m.group(3).strip() != want:
        raise ValueError('that item changed since the page was drawn -- refresh')
    done = m.group(2).lower() == 'x'
    lines[n] = '%s[%s] %s' % (m.group(1), ' ' if done else 'x', m.group(3))
    tmp = p + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(lines))
    os.replace(tmp, p)
    return 'reopened' if done else 'done'
