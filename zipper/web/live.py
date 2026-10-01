"""Keeping the page current without reloading it.

Two clocks. **Data** changes are noticed by polling `/api/state` for a hash of
what the page draws (`live_sig`); when it moves, the page fetches its own HTML
and swaps each `[data-live]` card whose markup changed. **Time** needs no server
at all: the now-line and a meeting's past/live look are recomputed in the
browser every 30 seconds from minutes the grid carries in `data-` attributes.

Never a reload. The Claude card is a live terminal and is not `data-live`, so a
swap cannot touch it.
"""
import hashlib
import time

from .. import core
from .data import content_sig


def live_sig(day=None, page=''):
    """Hash of everything the data cards draw.

    `content_sig` covers calendar, Canvas, open tasks and flags. Added here:
    ticked tasks and the queue, which it misses, the date, so the page rolls
    over at midnight, and a five-minute bucket, because the zipper card and the
    footer's "13m ago" chips change with time and not with any file.
    """
    from .home import done_task_rows, queue_panel, task_rows
    from .data import flags
    h = hashlib.sha1(content_sig().encode())
    h.update(core.TODAY.isoformat().encode())
    h.update(str(int(time.time() // 300)).encode())
    for t in task_rows() + done_task_rows():
        h.update(('%s|%s|%s' % (t['key'], t['title'], t.get('done'))).encode())
    h.update(queue_panel(flags())[2].encode())
    # Cards that are not built-in panels fingerprint themselves (their data, by
    # default), so a vault card's change reaches an open page like any other.
    from .cards import sig as cards_sig
    h.update(cards_sig(day, page).encode())
    return h.hexdigest()[:12]


LIVE_JS = """
(function(){
  // Server markup as last received, per card. Compared against this rather than
  // the live DOM, which tabs, toggles and the clock have all written into.
  const last={};
  document.querySelectorAll('[data-live]').forEach(n=>last[n.dataset.live]=n.innerHTML);
  let sig=window.__sig||null, touched=0, busy=false;
  // A tick is shown crossed at once and written a moment later. A swap in that
  // gap would draw the old state back over it, so wait out a click.
  document.addEventListener('click',e=>{if(e.target.closest('[data-live]'))touched=Date.now();},true);

  // What a swap would otherwise throw away: the chosen tab, expanded groups,
  // opened rows and blocks, and how far each list is scrolled.
  function keep(el){
    const on=el.querySelector('[data-tab].on');
    return {tab:on&&on.dataset.tab,
      exp:[...el.querySelectorAll('.grp.expand .grph .nm')].map(x=>x.textContent),
      rows:[...el.querySelectorAll('li.open .tick')].map(x=>x.dataset.key),
      blks:[...el.querySelectorAll('.blk.open .bt')].map(x=>x.textContent),
      scroll:[...el.querySelectorAll('.pb')].map(p=>p.scrollTop)};
  }
  function restore(el,s){
    if(s.tab){
      el.querySelectorAll('[data-tab]').forEach(x=>x.classList.toggle('on',x.dataset.tab===s.tab));
      el.querySelectorAll('[data-pane]').forEach(p=>p.hidden=p.dataset.pane!==s.tab);
    }
    el.querySelectorAll('.grp').forEach(g=>{const n=g.querySelector('.grph .nm');
      if(n&&s.exp.includes(n.textContent)) g.classList.add('expand');});
    el.querySelectorAll('li .tick').forEach(t=>{
      if(s.rows.includes(t.dataset.key)) t.closest('li').classList.add('open');});
    el.querySelectorAll('.blk').forEach(b=>{const t=b.querySelector('.bt');
      if(t&&s.blks.includes(t.textContent)) b.classList.add('open');});
    el.querySelectorAll('.pb').forEach((p,i)=>{if(s.scroll[i]) p.scrollTop=s.scroll[i];});
  }

  async function refresh(){
    const r=await fetch(location.pathname+location.search,{cache:'no-store'});
    if(!r.ok) return false;
    const doc=new DOMParser().parseFromString(await r.text(),'text/html');
    doc.querySelectorAll('[data-live]').forEach(n=>{
      const k=n.dataset.live, cur=document.querySelector('[data-live="'+k+'"]');
      if(!cur||last[k]===n.innerHTML) return;
      last[k]=n.innerHTML;
      const s=keep(cur), fresh=document.importNode(n,true);
      cur.replaceWith(fresh); restore(fresh,s);
      if(window.bindSparks) bindSparks(fresh);
    });
    clock();
    return true;
  }

  async function poll(){
    if(busy||document.hidden||Date.now()-touched<4000) return;
    busy=true;
    try{
      // Which board this is travels as `path`: /p/<key> fingerprints its own cards.
      const q=new URLSearchParams(location.search); q.set('path',location.pathname);
      const st=await fetch('/api/state?'+q,{cache:'no-store'}).then(r=>r.json());
      if(st&&st.sig&&st.sig!==sig&&await refresh()) sig=st.sig;
    }catch(e){}finally{busy=false;}
  }

  // Minutes since midnight, against the grid's own span. Only today's grid has
  // data-today; another day's is left as the server drew it.
  function clock(){
    const g=document.querySelector('.day[data-today]'); if(!g) return;
    const lo=+g.dataset.lo, span=+g.dataset.span, d=new Date(), m=d.getHours()*60+d.getMinutes();
    let nl=g.querySelector('.nowline');
    if(m<lo||m>lo+span){ if(nl) nl.remove(); }
    else{
      if(!nl){ nl=document.createElement('div'); nl.className='nowline'; g.appendChild(nl); }
      nl.style.top=((m-lo)*100/span).toFixed(3)+'%';
    }
    g.querySelectorAll('.blk[data-s]').forEach(b=>{
      b.classList.toggle('past',+b.dataset.e<=m);
      b.classList.toggle('live',+b.dataset.s<=m&&m<+b.dataset.e);
    });
  }

  // A card's button redraws the page at once rather than waiting for the poll.
  window.zipperRefresh=()=>refresh();
  setInterval(poll,10000);
  setInterval(clock,30000);
  document.addEventListener('visibilitychange',()=>{if(!document.hidden){clock();poll();}});
})();
"""
