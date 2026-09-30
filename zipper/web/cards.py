"""zipper.web.cards

The dashboard is rows of cards, and a card comes from one of three places:

    dashboard:<name>   built in       plugins/dashboard/cards/<name>.py
    <plugin>:<name>    a plugin's     plugins/<plugin>/cards/<name>.py (only while it is on)
    vault:<name>       the owner's    <vault>/Dashboard/<name>/backend.py (+ frontend.py)

**Which cards, and where, is the settings file** -- `plugins.dashboard.rows` in the
vault's settings.json:

    "rows": [
      {"style": "cols", "cards": ["dashboard:week", "dashboard:today", "canvas:week",
                                  {"card": "vault:projects"}]},
      {"cards": [{"card": "dashboard:todo", "id": "setup", "title": "setup",
                  "options": {"file": "Dashboard/setup.md"}}]}
    ]

A card entry is a string or an object: `card` (required), `id` (default: the card
name; needed when one card is used twice), `title`, `width` (in a plain row),
`options` (handed to the card), and for vault cards `backend` / `frontend` paths
(default `Dashboard/<name>/backend.py` and `frontend.py` beside it). `frontend` may
also name a built-in renderer, e.g. "dashboard:list".

**A card's code** -- a backend module with

    data(ctx) -> dict            what it shows (required, unless it has panel())
    render(data, ctx, ui) -> str its frontend, if it has no frontend.py and no renderer
    act_<name>(args, ctx) -> str a button's backend; the string is shown briefly
    sig(ctx) -> str              optional cheap fingerprint for live updates
    TITLE, WIDTH                 defaults for the frame and a plain row
    panel(ctx) -> str            instead of data/render: the whole panel's markup, for a
                                 card that must look exactly as it does (it should carry
                                 data-live="<ctx.card_id>" to update live)

**A broken card cannot break the page.** Every card renders inside a try: an error is
drawn in the card's own frame, and a card that takes longer than LIMIT seconds is
drawn as slow while the rest of the page carries on. Vault cards are unreviewed code;
this is the whole of the protection, and it is enough because the only person a
vault card can hurt is the vault's owner.
"""
import concurrent.futures, datetime, hashlib, html, importlib, importlib.util, json, os
import sys, time, traceback

from .. import core, plugins, settings

LIMIT = 4.0
_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=8, thread_name_prefix='card')
_VAULT_MODS = {}            # path -> (mtime, module): a vault card is re-read when edited
# A card that timed out is not called again for PENALTY seconds: otherwise every
# live-update poll would wait on it again and its stuck threads would fill the pool.
PENALTY = 60
_SLOW = {}                  # card id -> monotonic time it may be tried again


class Ctx:
    def __init__(self, day, card_id, options):
        self.day = day or core.TODAY.isoformat()
        self.today = core.TODAY.isoformat()
        self.card_id = card_id
        self.options = options or {}
        self.vault = core.VAULT


class Card:
    def __init__(self, entry):
        if isinstance(entry, str):
            entry = {'card': entry}
        self.entry = entry
        self.ref = entry['card']
        self.source, _, self.name = self.ref.partition(':')
        # Built-ins keep their bare names; anything else is prefixed by its source,
        # so canvas:week and dashboard:week never share an id.
        self.id = entry.get('id') or (self.name if self.source == 'dashboard'
                                      else '%s-%s' % (self.source, self.name))
        self.options = entry.get('options') or {}
        self.backend = None
        self.frontend = None        # callable(data, ctx, ui) or None
        self.error = ''
        try:
            self._load()
        except Exception as e:
            self.error = '%s: %s' % (type(e).__name__, e)

    # -- resolution ------------------------------------------------------------
    def _load(self):
        if self.source == 'vault':
            base = os.path.join(core.VAULT, 'Dashboard', self.name)
            back = _vault_path(self.entry.get('backend')) or os.path.join(base, 'backend.py')
            self.backend = _load_file(back)
            front = self.entry.get('frontend')
            if front and ':' in front and not front.startswith(('/', '.')):
                self.frontend = _renderer(front)
            else:
                fp = _vault_path(front) or os.path.join(os.path.dirname(back), 'frontend.py')
                if os.path.exists(fp):
                    self.frontend = _load_file(fp).render
            return
        if self.source != 'dashboard' and not plugins.is_enabled(self.source):
            raise RuntimeError('the %s plugin is off' % self.source)
        self.backend = importlib.import_module('plugins.%s.cards.%s' % (self.source, self.name))
        if self.entry.get('frontend'):
            self.frontend = _renderer(self.entry['frontend'])

    @property
    def title(self):
        return self.entry.get('title') or getattr(self.backend, 'TITLE', None) or self.name

    def ctx(self, day):
        return Ctx(day, self.id, self.options)

    # -- drawing ---------------------------------------------------------------
    def _draw(self, day):
        ctx = self.ctx(day)
        if hasattr(self.backend, 'panel'):
            return self.backend.panel(ctx)
        data = self.backend.data(ctx) if hasattr(self.backend, 'data') else {}
        front = self.frontend or getattr(self.backend, 'render', None) or _renderer('dashboard:list')
        body = front(data, ctx, UI)
        return UI.frame(self, body, count=(data or {}).get('count'),
                        tools=(data or {}).get('tools', ''))

    def _slow(self):
        return _SLOW.get(self.id, 0) > time.monotonic()

    def start(self, day):
        """Begin drawing; None if the card will not be called at all."""
        if self.error or self._slow():
            return None
        return _POOL.submit(self._draw, day)

    def html(self, day, fut=None, deadline=None):
        if self.error:
            return UI.error(self, self.error)
        if self._slow() and fut is None:
            return UI.error(self, 'took longer than %gs -- skipped for a minute' % LIMIT)
        fut = fut or _POOL.submit(self._draw, day)
        wait = LIMIT if deadline is None else max(0.0, deadline - time.monotonic())
        try:
            return fut.result(timeout=wait)
        except concurrent.futures.TimeoutError:
            _SLOW[self.id] = time.monotonic() + PENALTY
            return UI.error(self, 'took longer than %gs -- drawn without it' % LIMIT)
        except Exception as e:
            tb = traceback.extract_tb(sys.exc_info()[2])[-1]
            return UI.error(self, '%s: %s (%s:%d)' % (type(e).__name__, e,
                                                    os.path.basename(tb.filename), tb.lineno))

    def sig(self, day):
        """A cheap fingerprint: the card's own sig(), else a hash of its data. Built-in
        panels are covered by the page's existing signature."""
        if self.error:
            return self.error
        if self._slow():
            return 'slow'
        ctx = self.ctx(day)
        try:
            if hasattr(self.backend, 'sig'):
                return str(self.backend.sig(ctx))
            if hasattr(self.backend, 'panel'):
                if self.source == 'dashboard':
                    return ''               # covered by the page's own signature
                fut = _POOL.submit(self.backend.panel, ctx)
                return hashlib.sha1(fut.result(timeout=LIMIT).encode()).hexdigest()[:10]
            fut = _POOL.submit(self.backend.data, ctx)
            return hashlib.sha1(json.dumps(fut.result(timeout=LIMIT), sort_keys=True,
                                           default=str).encode()).hexdigest()[:10]
        except concurrent.futures.TimeoutError:
            _SLOW[self.id] = time.monotonic() + PENALTY
            return 'slow'
        except Exception as e:
            return 'err:%s' % e

    def act(self, action, args, day):
        fn = getattr(self.backend, 'act_' + action, None)
        if not callable(fn):
            raise KeyError('card %s has no action %r' % (self.id, action))
        return fn(args or {}, self.ctx(day))


def _vault_path(p):
    if not p:
        return ''
    return p if os.path.isabs(p) else os.path.join(core.VAULT, p)


def _load_file(path):
    """Import a vault file as a module, again whenever it changes on disk."""
    mt = os.path.getmtime(path)
    hit = _VAULT_MODS.get(path)
    if hit and hit[0] == mt:
        return hit[1]
    name = 'vaultcard_' + hashlib.sha1(path.encode()).hexdigest()[:10]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _VAULT_MODS[path] = (mt, mod)
    return mod


def _renderer(ref):
    """'dashboard:list' -> plugins.dashboard.renderers.list"""
    src, _, name = ref.partition(':')
    mod = importlib.import_module('plugins.%s.renderers' % src)
    return getattr(mod, name)


# ---------------------------------------------------------------- the layout

DEFAULT_ROWS = [
    {'cards': ['dashboard:queue', 'dashboard:claude']},
]


def rows():
    """[(style, width template or '', [Card])] from the settings file."""
    out = []
    for r in settings.get('plugins.dashboard.rows') or DEFAULT_ROWS:
        if isinstance(r, list):
            r = {'cards': r}
        cards = [Card(e) for e in r.get('cards') or []]
        out.append((r.get('style', ''), r, cards))
    seen = set()
    for _s, _r, cards in out:
        for c in cards:
            if c.id in seen and not c.error:
                c.error = ('another card on this dashboard already has the id %r -- give one '
                           'of them an "id"' % c.id)
            seen.add(c.id)
    return out


def find(card_id):
    for _style, _r, cards in rows():
        for c in cards:
            if c.id == card_id:
                return c
    return None


def body(day):
    """Every row. A row with a `style` uses that CSS class as it always has; a plain
    row is a grid whose columns come from its cards' widths."""
    parts = []
    layout = rows()
    # Every card starts at once and shares one deadline, so a page with several
    # slow cards waits LIMIT seconds once, not once per card.
    futs = {id(c): c.start(day) for _s, _r, cs in layout for c in cs}
    deadline = time.monotonic() + LIMIT
    for style, r, cards in layout:
        drawn = [c.html(day, futs[id(c)], deadline) if futs[id(c)] else c.html(day)
                 for c in cards]
        if style:
            parts.append('<div class="%s">%s</div>' % (html.escape(style), ''.join(drawn)))
        elif len(cards) == 1:
            parts.append(drawn[0])
        else:
            cols = ' '.join(_width(c) for c in cards)
            parts.append('<div class="cardrow" style="--cols:%s">%s</div>'
                         % (html.escape(cols), ''.join(drawn)))
    return ''.join(parts)


def _width(c):
    w = c.entry.get('width') or getattr(c.backend, 'WIDTH', None) or 'fill'
    return 'minmax(0,1fr)' if w == 'fill' else str(w)


def sig(day):
    """Fingerprint of every card that is not a built-in panel (those are in live_sig)."""
    return '|'.join('%s=%s' % (c.id, c.sig(day)) for _s, _r, cs in rows() for c in cs
                    if c.error or c.source != 'dashboard')


def act(card_id, action, args, day=None):
    """A button pressed on a card: find it in the layout, call its backend."""
    c = find(card_id)
    if not c:
        return {'ok': False, 'message': 'no card %r on this dashboard' % card_id}
    if c.error:
        return {'ok': False, 'message': c.error}
    try:
        fut = _POOL.submit(c.act, action, args, day)
        msg = fut.result(timeout=LIMIT * 5)
        return {'ok': True, 'message': msg or ''}
    except Exception as e:
        return {'ok': False, 'message': '%s: %s' % (type(e).__name__, e)}


# ---------------------------------------------------------------- the frontend kit

class UI:
    """What a frontend draws with, so a vault card matches everything else."""

    esc = staticmethod(lambda s: html.escape('' if s is None else str(s)))

    @staticmethod
    def frame(card, body, count=None, tools=''):
        n = '<span class="n">%s</span>' % UI.esc(count) if count not in (None, '') else ''
        t = '<span class="tabs">%s</span>' % tools if tools else ''
        return ('<div class="panel card" data-live="%s" data-card="%s"><div class="ph">%s%s%s'
                '</div><div class="pb">%s</div></div>'
                % (UI.esc(card.id), UI.esc(card.id), UI.esc(card.title), n, t, body))

    @staticmethod
    def error(card, message):
        return ('<div class="panel card cardbad" data-live="%s" data-card="%s"><div class="ph">%s'
                '</div><div class="pb"><p class="empty">This card could not be drawn: %s</p>'
                '</div></div>' % (UI.esc(card.id), UI.esc(card.id), UI.esc(card.title),
                                  UI.esc(message)))

    @staticmethod
    def button(action, label, **args):
        """A button that calls the card's act_<action>(args) and redraws the card."""
        return ('<button class="tabb cardact" data-act="%s" data-args="%s">%s</button>'
                % (UI.esc(action), UI.esc(json.dumps(args)), UI.esc(label)))

    @staticmethod
    def items(rows, empty='Nothing here.'):
        """A plain list: each row {title, sub, url, when, done}, plus `lead` (markup
        before the title, e.g. a checkbox button) and `extra` (markup after)."""
        if not rows:
            return '<p class="empty">%s</p>' % UI.esc(empty)
        out = []
        for r in rows:
            title = UI.esc(r.get('title'))
            if r.get('url'):
                title = '<a href="%s" target="_blank" rel="noopener">%s</a>' % (
                    UI.esc(r['url']), title)
            out.append('<li class="row%s">%s<span class="rt">%s</span>%s%s%s</li>' % (
                ' done' if r.get('done') else '', r.get('lead', ''), title,
                '<span class="rs">%s</span>' % UI.esc(r['sub']) if r.get('sub') else '',
                '<span class="at">%s</span>' % UI.esc(r['when']) if r.get('when') else '',
                r.get('extra', '')))
        return '<ul class="cardlist">%s</ul>' % ''.join(out)

    @staticmethod
    def ago(iso):
        if not iso:
            return 'never'
        try:
            s = (datetime.datetime.now() - datetime.datetime.fromisoformat(iso)).total_seconds()
        except ValueError:
            return iso
        return ('%ds' % s if s < 60 else '%dm' % (s // 60) if s < 3600 else
                '%dh' % (s // 3600) if s < 86400 else '%dd' % (s // 86400)) + ' ago'


# ---------------------------------------------------------------- page assets

# A plain row's columns come from its cards' widths (--cols); the styled rows
# (.cols, .cols2, .cols3) keep the CSS they always had.
CARD_CSS = """
.cardrow{display:grid;grid-template-columns:var(--cols);gap:12px;margin-top:12px;align-items:start}
@media(max-width:820px){.cardrow{grid-template-columns:1fr}}
.cardrow .panel{height:auto;max-height:var(--ph)}
.cardrow .pb{max-height:calc(var(--ph) - 44px);overflow:auto}
.cardlist .cardact.box{border:0;background:none;padding:0;font-size:1.1em;cursor:pointer;line-height:1}
.panel.card + .panel.card{margin-top:0}
.cardlist{list-style:none;margin:0;padding:0}
.cardlist .row{display:flex;gap:8px;align-items:baseline;padding:4px 0}
.cardlist .row.done .rt{text-decoration:line-through;opacity:.55}
.cardlist .rs,.cardlist .at{color:var(--mut,#888);font-size:.85em}
.cardbad .empty{color:#b44}
.cardmsg{font-size:.8em;color:var(--mut,#888);margin-left:6px}
"""

# One handler for every card button: POST the action, show its message in the
# card's header for a few seconds, and redraw the page's live parts at once.
CARD_JS = """
document.addEventListener('click',async e=>{
  const b=e.target.closest('[data-act]'); if(!b) return;
  const card=b.closest('[data-card]'); if(!card) return;
  e.preventDefault(); b.disabled=true;
  const day=new URLSearchParams(location.search).get('day');
  let res={ok:false,message:'no answer'};
  try{
    const r=await fetch('/api/card/'+encodeURIComponent(card.dataset.card)+'/'+encodeURIComponent(b.dataset.act),
      {method:'POST',headers:{'Content-Type':'application/json'},
       body:JSON.stringify({args:JSON.parse(b.dataset.args||'{}'),day})});
    res=await r.json();
  }catch(err){res={ok:false,message:String(err)};}
  b.disabled=false;
  if(res.message){
    const ph=card.querySelector('.ph'); let m=ph&&ph.querySelector('.cardmsg');
    if(ph&&!m){m=document.createElement('span');m.className='cardmsg';ph.appendChild(m);}
    if(m){m.textContent=(res.ok?'':'failed: ')+res.message;setTimeout(()=>m.remove(),5000);}
  }
  if(window.zipperRefresh) window.zipperRefresh();
});
"""
