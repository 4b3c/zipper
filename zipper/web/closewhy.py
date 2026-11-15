"""Closing a task with a reason, from the dashboard.

A third way off the list, between the tick (did it) and the × (not doing it, no
record). "Decide where the spring lab hours go" isn't done by doing anything;
it's closed by an answer, and the answer is the valuable part: it belongs in
the notes it decides. So the row opens to a box, the reason is written under
the task in its markdown file, the task is ticked, and a queue row asks the next
bookkeeping pass to carry the reason into the notes it affects.
"""
import datetime

from .base import core
from .render import esc


def close_form(it):
    """The box inside an open task row. Tasks only, and only open, writable ones:
    Canvas rows belong to Canvas, read-only tasks to the source that writes them."""
    if not str(it.get('key', '')).startswith('task:') or it.get('readonly') or it.get('done'):
        return ''
    return ('<span class="closewhy" data-key="%s">'
            '<input type="text" maxlength="500" placeholder="close with a reason…" '
            'aria-label="reason for closing">'
            '<button type="button">close</button></span>' % esc(it['key']))


def reason_line(reason, today=None):
    """The line written under the task. One line, indented as a description is."""
    text = ' '.join(str(reason).split())[:500]
    return '  Closed %s: %s' % ((today or core.TODAY).isoformat(), text)


CLOSE_CSS = """
.closewhy{display:none;gap:6px;align-items:center;padding:4px 0 2px}
.row.open .closewhy{display:flex}
.closewhy input{flex:1;min-width:0;font:13px/1.4 var(--sans);padding:5px 8px;
  border:1px solid rgba(127,127,127,.35);border-radius:6px;background:transparent;color:inherit}
.closewhy button{font:11px/1 var(--mono);letter-spacing:.04em;padding:6px 10px;
  border-radius:99px;border:1px solid currentColor;background:transparent;color:inherit;
  cursor:pointer;opacity:.75}
.closewhy button:hover{opacity:1}
.closewhy.sent{font:12.5px/1.5 var(--sans);opacity:.68;display:block}
"""

CLOSE_JS = """
// Close with a reason. The row toggle skips clicks inside the box, so typing
// doesn't fold the row. Enter or the button sends; an empty box does nothing
// (that is what the tick is for). The row is crossed off only once the server
// says the line was written.
async function closeWhy(w){
  const inp = w.querySelector('input'), why = (inp && inp.value || '').trim();
  if(!why || w.dataset.busy) return;
  w.dataset.busy = '1';
  const r = await fetch('/api/closetask', {method:'POST', headers:{'Content-Type':'application/json'},
                        body: JSON.stringify({key: w.dataset.key, reason: why})})
                  .then(x=>x.json()).catch(()=>({ok:false, error:'no answer'}));
  delete w.dataset.busy;
  if(!r.ok){ alert('Not closed: ' + (r.error || 'unknown error')); return; }
  const li = w.closest('li'); if(li) li.classList.add('crossed');
  const t = li && li.querySelector('.tick'); if(t) t.innerHTML = '\\u2713';
  w.classList.add('sent'); w.textContent = 'Closed: ' + why;
}
document.addEventListener('click', e=>{
  const b = e.target.closest('.closewhy button'); if(b) closeWhy(b.closest('.closewhy'));
});
document.addEventListener('keydown', e=>{
  if(e.key==='Enter' && e.target.closest('.closewhy input')){
    e.preventDefault(); closeWhy(e.target.closest('.closewhy'));
  }
});
"""
