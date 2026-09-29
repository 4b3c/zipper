"""zipper.writer

The one place where facts from the inputs enter note frontmatter.

An input's `facts()` returns `{note title: {field: value}}`. It says what it saw; this
decides what may be written, so the rules hold for every input rather than being
re-implemented, slightly differently, by each:

- **Facts only.** Just the fields in WRITABLE. A judgment such as `status` is the
  operator's, and an input that returns one is refused and logged, never obeyed.
- **Never onto a generated view.** It is overwritten on the next run.
- **Evidence only moves `last_touched` forward.** A fetch that sees less than last
  time -- a lost token, a repo made private -- must not make a project look staler.
"""
from . import core

WRITABLE = {'last_push', 'commits_recent', 'commits_mine', 'last_touched'}
FORWARD_ONLY = {'last_touched'}


def plan(facts, current):
    """What would change. Pure: `current` is {title: frontmatter dict}.

    Returns (changes, refused): changes is [(title, field, old, new)], refused is
    [(title, field)] for fields no input may write.
    """
    changes, refused = [], []
    for title, fields in sorted(facts.items()):
        d = current.get(title)
        if d is None:
            continue                         # no such note: nothing to write onto
        if d.get('view_kind') == 'generated':
            continue                         # overwritten every run; holds no facts
        for field, value in fields.items():
            if field not in WRITABLE:
                refused.append((title, field))
                continue
            new, old = str(value), d.get(field)
            if field in FORWARD_ONLY and not new > (old or ''):
                continue
            if new != old:
                changes.append((title, field, old, new))
    return changes, refused


def apply(facts, source=''):
    """Write `facts` onto the notes. Returns how many notes changed."""
    idx = {core.title_of(p): p for p in core.iter_notes()}
    current = {t: core.fm_dict(core.read_note(p)[0]) for t, p in idx.items() if t in facts}
    changes, refused = plan(facts, current)
    for title, field in refused:
        print('  writer: refused %s.%s from %s -- not a fact an input may write'
              % (title, field, source or '?'))
    by_note = {}
    for title, field, old, new in changes:
        by_note.setdefault(title, []).append((field, new))
    for title, sets in by_note.items():
        pairs, body = core.read_note(idx[title])
        for field, new in sets:
            core.set_field(pairs, field, new)
        core.write_note(idx[title], pairs, body)
        print('  %-30s %s' % (title, '  '.join('%s %s' % fv for fv in sets)))
    print('updated %d note(s)%s' % (len(by_note), ' from ' + source if source else ''))
    return len(by_note)


def apply_all():
    """Every enabled input's facts."""
    from . import inputs
    n = 0
    for i in inputs.enabled():
        if hasattr(i, 'facts'):
            try:
                n += apply(i.facts(), source=i.name)
            except Exception as e:
                print('%s facts skipped: %s' % (i.name, e))
    return n
