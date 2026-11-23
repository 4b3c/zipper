"""zipper.runqueue

The queue, and the bookkeeping pass over it.

There is **one** queue: `Inbox/feed.json`, an append-only stream of typed events
that happened outside the vault -- a push, a Canvas submission, a calendar
change. Each row carries a `kind` and, where one can be resolved, a `target`
note, so working through it is a matter of reading rather than inference. Rows
are ticked off individually and never re-appear.

Note edits are *not* rows. They are the working tree, read straight out of git
at bookkeeping time and cleared by committing -- git is the baseline, and there
is no second one. (There was, once: HISTORY.md, 2026-09-06.)

Flags are neither. They are conditions derived fresh from current state on every
run, so ticking one off is meaningless: it re-fires next run, and worse, looks
handled while the project it names keeps stalling. They are reported, never
queued.

`cmd_brief` renders all three into `Meta/Queue.md` -- the brief for a
bookkeeping pass; `cmd_pull` pulls the scoops that are due and ends by calling it. The
reading in between is not a command and cannot be one. `cmd_commit` ends the
pass by ticking the rows and committing the notes.
"""
import os, re, json, datetime, glob, subprocess, time, fcntl

from .core import *          # noqa: F401,F403 -- the shared vocabulary
from . import core
from .events import resolve_events
from .metrics import _ledger_update, ledger_lock, ledger_write
from . import ics, status, sync, views
from . import scoops as inputs


QUEUE_JSON = os.path.join(INBOX, 'queue.json')
FEED_JSON = os.path.join(INBOX, 'feed.json')
# One file per pass, naming the rows its brief showed. `zipper commit` ticks
# those and only those.
PASS_DIR = os.path.join(INBOX, 'passes')
PASS_MAX_AGE = 12 * 3600     # a brief older than this is no longer what the pass is working from


def pass_id():
    """Who is running this pass: the scheduled pass's session (`ZIPPER_PASS`),
    else the conversation. None for the timer's `pull --due`, which is nobody's pass."""
    from . import chat
    return os.environ.get('ZIPPER_PASS') or chat.current_conversation()


def _pass_file(pid):
    return os.path.join(PASS_DIR, re.sub(r'[^A-Za-z0-9_.-]', '_', pid) + '.json')


def _claims_elsewhere(pid):
    """{row key: conversation} for rows another live pass has claimed. A pass
    stops claiming when its conversation is no longer live, or when it commits."""
    from . import locks
    out = {}
    for f in glob.glob(os.path.join(PASS_DIR, '*.json')):
        try:
            with open(f, encoding='utf-8') as fh:
                rec = json.load(fh)
            if time.time() - os.path.getmtime(f) > PASS_MAX_AGE:
                continue
        except (OSError, ValueError):
            continue
        if rec.get('id') == pid or not locks.live(rec.get('id')):
            continue
        for k in rec.get('keys') or ():
            out.setdefault(k, rec['id'])
    return out


def record_pass(rows):
    """Claim the rows this pass's brief showed, minus those another live pass
    already claimed, and return those others' claims {key: conversation}.

    Rewritten on every brief the pass pulls. Two conversations started together
    would otherwise both work the same email and file the same task twice; under
    the flock, the first to pull takes the rows and the second is told whose they are.
    """
    pid = pass_id()
    os.makedirs(PASS_DIR, exist_ok=True)
    with open(os.path.join(PASS_DIR, '.lock'), 'a+') as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            # Conversations that pulled and never committed leave a file; drop stale ones.
            for f in glob.glob(os.path.join(PASS_DIR, '*.json')):
                try:
                    if time.time() - os.path.getmtime(f) > PASS_MAX_AGE:
                        os.remove(f)
                except OSError:
                    pass
            claims = _claims_elsewhere(pid)
            if not pid:
                return claims
            out = _pass_file(pid)
            tmp = '%s.%d.tmp' % (out, os.getpid())
            with open(tmp, 'w', encoding='utf-8') as fh:
                json.dump({'id': pid, 'at': datetime.datetime.now().isoformat(timespec='seconds'),
                           'keys': [r['key'] for r in rows
                                    if r.get('key') and r['key'] not in claims]}, fh)
            os.replace(tmp, out)
            return claims
        finally:
            fcntl.flock(lk, fcntl.LOCK_UN)


def take_pass():
    """The row keys this pass's brief showed, or None if this process never
    pulled a brief (or pulled one too long ago). The id inside the file is
    checked as well as its name, so two ids that sanitise alike can't trade rows."""
    pid = pass_id()
    if not pid:
        return None
    try:
        rec = json.load(open(_pass_file(pid), encoding='utf-8'))
        at = datetime.datetime.fromisoformat(rec['at'])
    except Exception:
        return None
    if rec.get('id') != pid or (datetime.datetime.now() - at).total_seconds() > PASS_MAX_AGE:
        return None
    return rec.get('keys') or []


def end_pass():
    pid = pass_id()
    if pid:
        try:
            os.remove(_pass_file(pid))
        except OSError:
            pass

# ------------------------------------------------------------------ git

def _git(*args):
    """Run git in the vault. Returns stdout, or '' if git itself failed --
    the vault being a repo is load-bearing here, but a missing git must
    degrade to "no note changes", never take the whole brief down."""
    # core.quotePath=false: git otherwise renders any non-ASCII byte in a path
    # as an octal escape inside double quotes -- `Zipper \342\200\224 the
    # system.md`. Decoding that back is fiddly and was being got wrong (see
    # note_changes), so the fix is to never be handed it: with quotePath off,
    # git emits the real UTF-8 path and there is nothing to decode.
    try:
        r = subprocess.run(['git', '-C', VAULT, '-c', 'core.quotePath=false']
                           + list(args),
                           capture_output=True, text=True, timeout=30)
        return r.stdout if r.returncode == 0 else ''
    except Exception:
        return ''

def _is_generated(path):
    """True for a `view_kind: generated` note.

    These are excluded from the note diff on purpose. `catchup` rewrites them
    every run, so they are never clean, and everything they say is an echo of
    an event already sitting in the queue as its own row -- Agenda restates the
    calendar rows, Repos restates the pushes. Reviewing them is reading the same
    change twice and mistaking it for two.
    """
    full = os.path.join(VAULT, path)
    if not os.path.exists(full):
        return False                    # deleted: report it, do not guess
    try:
        with open(full, encoding='utf-8') as fh:
            head = fh.read(400)
    except OSError:
        return False
    return bool(re.search(r'^view_kind:\s*generated\s*$', head, re.M))

def note_changes():
    """Uncommitted note edits, whoever made them.

    Authorship is deliberately not modelled. The operator, this session and another
    Discord session all edit the same files, and a change needs looking at
    because it is unreviewed, not because of who typed it.
    """
    out = []
    for line in _git('status', '--porcelain', '--untracked-files=all').split('\n'):
        if not line.strip():
            continue
        code, _, path = line[:2], line[2], line[3:].strip()
        if path.startswith('"') and path.endswith('"'):
            # Only reachable for genuinely odd names now that quotePath is off
            # (a quote or a control character in the filename). The round trip
            # through latin-1 matters: `unicode_escape` maps each octal escape
            # to one *codepoint*, so the three bytes of an em dash come back as
            # three mojibake characters unless they are re-packed as bytes and
            # decoded as UTF-8. Getting this wrong silently produced a path that
            # matched no file, and `git add` aborts the whole batch on one bad
            # pathspec -- so a single em dash in a filename stopped four
            # unrelated notes from being committed at all.
            try:
                path = (path[1:-1].encode().decode('unicode_escape')
                        .encode('latin-1').decode('utf-8'))
            except (UnicodeDecodeError, UnicodeEncodeError):
                path = path[1:-1]
        if not path.endswith('.md') or _is_generated(path):
            continue
        action = ('add' if 'A' in code or '?' in code else
                  'delete' if 'D' in code else 'edit')
        # `when` is the file's mtime -- the one thing git genuinely knows about
        # an uncommitted change. `who` is absent and stays absent: git records
        # an author at commit time, and nothing anywhere records which session
        # wrote a byte to the working tree. Optional means absent, not guessed.
        when = None
        try:
            when = datetime.datetime.fromtimestamp(
                os.path.getmtime(os.path.join(VAULT, path))).isoformat(timespec='minutes')
        except OSError:
            pass
        out.append({'system': 'vault', 'action': action, 'path': path,
                    'when': when, 'clears': 'commit',
                    'text': '%-11s %s' % (action, path)})
    return sorted(out, key=lambda x: x['path'])

def note_diff(path, context=1):
    """The actual hunks for one note, so a bookkeeping pass can read what
    changed instead of re-reading the whole file to find out."""
    d = _git('diff', 'HEAD', '-U%d' % context, '--', path)
    if not d:
        d = _git('diff', '--no-index', '-U%d' % context, '--',
                 os.devnull, os.path.join(VAULT, path))
    body = [l for l in d.split('\n')
            if l[:1] in '+- ' and not l.startswith(('+++', '---'))]
    return '\n'.join(body)

# ------------------------------------------------------------------ the queue

def feed_rows():
    try:
        return json.load(open(FEED_JSON, encoding='utf-8')).get('rows', [])
    except Exception:
        return []

def open_rows():
    return [r for r in feed_rows() if not r.get('done')]

def classify(row, resolve=None):
    """Fill in system/action/target for a row, migrating pre-2026-09-06 ones.

    Rows written before the typed schema are flat display strings, so their
    system has to be read back out of the text. New rows arrive already typed
    and only need their target resolved -- the mapping is hand-maintained and
    can change after a row was written.
    """
    text = row.get('text', '')
    out = dict(row)
    out.setdefault('clears', 'tick')
    if not out.get('system'):
        if text.startswith('pushed'):
            out['system'], out['action'] = 'github', 'push'
        elif text.startswith('submitted'):
            out['system'], out['action'] = 'canvas', 'submit'
        elif text.startswith('+ canvas'):
            out['system'], out['action'] = 'canvas', 'add'
        elif text.startswith(('+ calendar', '- calendar')):
            out['system'] = 'calendar'
            out['action'] = 'add' if text.startswith('+') else 'remove'
        elif text.startswith('error'):
            out['system'] = 'error'
        else:
            out['system'] = 'other'
    if not out.get('target'):
        t = (resolve or inputs.target_resolver())(out)
        if t:
            out['target'] = t
    return out

# ------------------------------------------------------------------ flags

def _tasks():
    """Every checkbox in the vault, normalized. Feeds the task ledger, which
    keeps its own history and so needs no baseline of its own."""
    tasks = {}
    for p in _all_md():
        for line in defenced(open(p, encoding='utf-8').read().split('\n')):
            m = TASK_RE.match(line)
            if not m:
                continue
            text = re.sub(r'\[[a-z_]+::\s*(?:\[\[[^\]]+\]\]|[^\]]*)\]', '',
                          m.group(2)).strip()
            text = re.sub(r'#\w+', '', text).strip()
            if text:
                tasks[text[:120]] = (m.group(1).lower() == 'x')
    return tasks

def flags(evrecs=None):
    """Conditions, re-derived every run. Never queued, never ticked."""
    if evrecs is None:
        evrecs = resolve_events(fix=False)
    out = inputs.flags()
    notes = [(p, fm_dict(read_note(p)[0])) for p in iter_notes()]
    linked = sync.log_latest()
    logged_recently = set()
    for f in glob.glob(os.path.join(LOGDIR, '*.md')):
        n = _days_since(title_of(f))
        if n is not None and n <= 7:
            logged_recently.update(x.strip() for x in LINK_RE.findall(
                open(f, encoding='utf-8').read()))
    for p, d in notes:
        t = title_of(p)
        if d.get('last_push') and _days_since(d['last_push']) is not None \
           and _days_since(d['last_push']) <= 7 and t not in logged_recently:
            out.append('%s was pushed to on %s but never appears in a log entry' %
                       (t, d['last_push']))
        if d.get('type') == 'project' and d.get('status') == 'active':
            # Ignoring a project is fine; a month of it is worth one mention.
            n = sync.ignored_for(d, linked.get(t, ''))
            if n is None:
                out.append('%s is active with no evidence of work at all' % t)
            elif n > sync.IGNORED_DAYS:
                # status_verified is the owner confirming the status despite the
                # evidence, so it suppresses this too -- but only for 45 days,
                # or a single confirmation would hide the drift permanently.
                sv = d.get('status_verified', '')
                if not (sv and _days_since(sv) is not None and _days_since(sv) <= 45):
                    out.append('%s is active but ignored for %d days (last work %s)%s'
                               % (t, n, sync.last_work(d, linked.get(t, '')),
                                  ' (last verified %s)' % sv if sv else ''))
        if d.get('repos') and d.get('last_push') and d.get('status') in (
                'dormant', 'idea', 'archived', 'retired'):
            n = _days_since(d['last_push'])
            verified = d.get('status_verified', '')
            if n is not None and n <= 120 and verified < d['last_push']:
                out.append('%s is marked %s but was pushed to on %s — status may be stale'
                           % (t, d['status'], d['last_push']))
        if d.get('review') and d['review'] <= core.TODAY.isoformat():
            out.append('%s is due for review (%s)' % (t, d['review']))
    # Event notes. A meeting they scheduled for a reason is not finished when it
    # ends -- it is finished when what came out of it is written down. This is
    # the only flag that expects an answer from them rather than an edit.
    for r in evrecs:
        when = (r['start'] or '?').replace('T', ' ')
        if r['state'] == 'due':
            out.append('event needs a debrief: "%s" (%s) — ask how it went, then '
                       'write it into %s' % (r['summary'] or r['title'], when,
                                             rel(r['path'])))
        elif r['state'] == 'dangling':
            out.append('event note matches nothing on the calendar: %s (%s) — '
                       'deleted, or outside the ingest window' % (rel(r['path']), when))
        elif r['moved_to']:
            out.append('event moved: "%s" is now %s — its note followed it'
                       % (r['summary'] or r['title'], r['moved_to'].replace('T', ' ')))
    return out

# ------------------------------------------------------------------ bookkeep

def cmd_pull(a):
    """Pull scoops -- named ones, the ones whose timer is due (`--due`), or all
    that pull -- then regenerate everything derived from them and the brief.

    There is no global fetch any more: every scoop that polls has its own timer
    (`scoops.<name>.poll_minutes`), and `zipper run` or a systemd timer calls
    `zipper pull --due` every few minutes. A bookkeeping pass does the same first,
    so the brief it reads is never staler than the scoops' own intervals.
    """
    from . import serve, fanout
    fanout.install_hook()     # every pass, so a vault made before the hook gets it
    try:                      # and Claude Code's hooks, for the same reason
        from . import setup
        # Not from a worktree under review: it would point the live Stop hook at
        # a checkout that is deleted after the verdict, and replies stop arriving.
        if os.path.isdir(os.path.join(setup.ROOT, '.git')):
            setup.install_hook()
    except Exception as e:
        print('hooks: %s' % e)
    serve.feed_load()
    names = list(getattr(a, 'names', None) or [])
    if getattr(a, 'due', False):
        names = inputs.due()
    elif not names:
        names = [i.name for i in inputs.pullers()]
    for n in names:
        print('== %s ==' % n)
        added, err = inputs.pull(n)
        print('%s: %d new row(s)%s' % (n, added, ' -- ' + err if err else ''))
    if not names:
        print('pull: nothing due')
    print('== sync ==');   sync.cmd_sync(a)
    print('\n== agenda =='); a.days = getattr(a, 'days', 14) or 14; ics.cmd_agenda(a)
    print('\n== status =='); status.cmd_status(a)
    print('\n== views ==');  views.cmd_views(a)
    print()
    return cmd_brief(a)


def cmd_fetch(a):
    """The old whole-refresh command: now every pulling scoop, then the brief."""
    print('note: `fetch` is `zipper pull` now -- scoops also pull on their own timers.\n')
    a.names, a.due = [], False
    return cmd_pull(a)


def cmd_brief(a):
    os.makedirs(INBOX, exist_ok=True)
    # Reconcile event notes against the calendar *before* reading the working
    # tree. A rescheduled meeting rewrites its note, and doing that afterwards
    # would leave the rewrite sitting uncommitted with nothing explaining it.
    evrecs = resolve_events(fix=True)
    # Locked: the timer's `pull --due` runs this same brief every few minutes,
    # and two passes interleaving here lose one's drops or double-report them.
    tasks = _tasks()
    with ledger_lock():
        led = _ledger_update(tasks)

        # Only this pass's drops. A 30-day window re-reported the same drop every
        # run for a month, which made a single edit look like sustained abandonment.
        seen = led.get('__meta__', {}).get('reported_at', '')
        dropped = sorted(t for t, e in led.items()
                         if t != '__meta__' and e.get('dropped_on', '') > seen)
        renamed = sorted(t for t, e in led.items()
                         if t != '__meta__' and e.get('renamed_on', '') > seen)
        led.setdefault('__meta__', {})['reported_at'] = datetime.datetime.now().isoformat()
        ledger_write(led)

    fl = flags(evrecs)
    for t in dropped:
        fl.append('task left the list unfinished: "%s"' % t[:70])

    resolve = inputs.target_resolver()
    rows = [classify(r, resolve) for r in open_rows()]
    claims = record_pass(rows)
    for r in rows:
        if r.get('key') in claims:
            r['claimed_by'] = claims[r['key']]
    changes = note_changes()
    guides = sorted({n for n in map(inputs.row_scoop, rows) if n and inputs.has_guide(n)})
    q = {'generated': datetime.datetime.now().isoformat(timespec='seconds'),
         'events': rows, 'notes_uncommitted': changes, 'guides': guides,
         'tasks_dropped': dropped, 'tasks_renamed': renamed, 'flags': fl}
    tmp = '%s.%d.tmp' % (QUEUE_JSON, os.getpid())
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(q, fh, indent=1)
    os.replace(tmp, QUEUE_JSON)     # read by other processes; never half a file
    _write_brief(q)
    print('brief: %d open event(s), %d uncommitted note(s), %d flag(s)'
          % (len(rows), len(changes), len(fl)))
    print('  -> Meta/Queue.md  +  Inbox/queue.json')
    return 0

SYSTEM_LABEL = {'github': 'GitHub', 'calendar': 'Calendar', 'canvas': 'Canvas', 'gmail': 'Gmail',
                'slack': 'Slack', 'tasks': 'Tasks closed with a reason', 'vault': 'Vault',
                'zipper': 'Zipper', 'error': 'Errors',
                'other': 'Other'}
SYSTEM_ORDER = ('github', 'canvas', 'calendar', 'gmail', 'slack', 'tasks', 'zipper', 'vault', 'error',
                'other')


def _ev_line(e):
    """One event, one line: what it was, when, who, where it lands, how it clears.

    who and when are optional and simply absent when the source does not know
    them -- a push knows both, a calendar entry knows when it is but not who
    added it, a note edit knows when it was written and never who wrote it.
    """
    bits = []
    if e.get('when'):
        bits.append(str(e['when']).replace('T', ' '))
    if e.get('who'):
        bits.append(str(e['who']))
    meta = '  ·  %s' % ' · '.join(bits) if bits else ''
    tgt = '  → **[[%s]]**' % e['target'] if e.get('target') else ''
    key = '`%s` ' % e['key'] if e.get('key') else ''
    claimed = ('  ·  *claimed by conversation %s, which is working it: leave it*'
               % e['claimed_by'] if e.get('claimed_by') else '')
    line = '- %s%s%s%s%s' % (key, e.get('text', ''), meta, tgt, claimed)
    if e.get('context'):
        # What the operator's own message answered. Read it against every open task
        # pointing at this conversation: a reply owed may already be sent.
        line += '\n  *answering:*' + ''.join('\n  > %s' % c for c in e['context'])
    return line


def _write_brief(q):
    L = ['---', 'tags: [meta, view]', 'type: view', 'view_kind: generated',
         'status: living', 'source: zipper pull',
         'generated: ' + core.TODAY.isoformat(), '---', '', '# Queue', '',
         '*Generated by `zipper pull`. The brief for a bookkeeping pass: what '
         'happened, what is unreviewed, and what is wrong.*', '',
         '**As of:** %s' % q['generated'], '']

    events = list(q['events']) + list(q['notes_uncommitted'])
    L += ['## Events', '',
          '*One queue, every system. Each row is something that happened: work out '
          'what it affected, update the note, then clear it. **How a row clears is '
          'stated on the row** — `tick` means `zipper.serve --mark <key>`, `commit` '
          'means it goes away when the change is committed.*', '']
    if not events:
        L.append('- nothing open; the tree is clean')
    for sysname in SYSTEM_ORDER:
        rows = [e for e in events if e.get('system') == sysname]
        if not rows:
            continue
        clears = 'commit' if sysname == 'vault' else 'tick'
        L += ['', '### %s  <sub>clears by %s</sub>' % (SYSTEM_LABEL[sysname], clears), '']
        if sysname == 'tasks':
            # Closed from the dashboard with the operator's own reason. The reason is
            # an answer, usually a decision: it belongs in the notes it settles.
            L += ['*The operator ticked these from the dashboard and said why. The reason '
                  'is theirs: carry it into every note it settles (a choice is a '
                  '`zipper decide`), and fix anything it makes false.*', '']
        # Only the guides this pass needs: a scoop with no rows costs no reading.
        have = set(q.get('guides') or ())
        need = sorted({inputs.row_scoop(e) for e in rows} & have)
        if need:
            L += ['*Read first: %s*' % ' · '.join('`zipper scoop guide %s`' % n for n in need), '']
        L += [_ev_line(e) for e in rows]
    L.append('')

    if q['tasks_dropped'] or q['tasks_renamed']:
        L += ['## Tasks', '']
        for t in q['tasks_dropped']:
            L.append('- dropped unfinished: %s' % t)
        for t in q['tasks_renamed']:
            L.append('- reworded (not dropped): %s' % t)
        L.append('')

    L += ['## Flags', '',
          '*Conditions, not events. These are re-derived every run and cannot be '
          'ticked off — a flag stops firing when the underlying data changes, and '
          'not before.*', '']
    L.extend(['- %s' % f for f in q['flags']] or ['- none firing'])
    L += ['', '---', '',
          '**This is a brief, not a report.** `zipper pull` wrote it and stopped; '
          'the middle step of a bookkeeping pass is reasoning, and it needs an agent. '
          'Work each row to the note it affected, read the diff, then close the pass '
          'with `python3 -m zipper commit "<message>"`.', '',
          'Related: [[Status]] · [[Now]] · [[Review]] · [[Home]]', '']
    os.makedirs(METADIR, exist_ok=True)     # a new vault has no Meta/ until now
    out = os.path.join(METADIR, 'Queue.md')
    # The temp file sits in Inbox/ (gitignored, same filesystem), so a writer
    # killed mid-write leaves nothing in the notes.
    os.makedirs(INBOX, exist_ok=True)
    tmp = os.path.join(INBOX, '.Queue.md.%d.tmp' % os.getpid())
    with open(tmp, 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(L))
    os.replace(tmp, out)

def live_others():
    """Conversations other than this one that are live right now. Shared by
    `commit` (sweeping up their edits) and `update` (restarting under them)."""
    from . import conversations, chat
    try:
        # Anyone else with a live terminal may be mid-edit. Nothing locks the
        # vault -- that was a deliberate call -- so the check is a warning, not
        # a mutex, and --force is the way past it.
        #
        # `chat.current_conversation()`, not `default_thread()`: this asks who
        # this process *is*, not where its replies go. A local guess at the name
        # got it wrong once (ZIPPER_THREAD, never set by anything), so every
        # commit from inside a conversation counted *itself* as the other one
        # and demanded --force. Since 2026-09-17 a pane carries
        # ZIPPER_CONVERSATION and no thread, so reading the thread here would
        # bring that bug back for every commit typed at the keyboard.
        mine = chat.current_conversation()
        # Live means a pane with Claude in it, *or* a headless Discord turn in
        # progress. `alive` alone only sees panes, so a Discord conversation
        # mid-edit used to count as idle and its edits could be swept up.
        from . import convhead
        live = [c for c in conversations.listing()
                if c.get('thread_id') != mine
                and (c.get('alive') or convhead.turn_running(c.get('thread_id')))]
        return live
    except Exception:
        return []


def cmd_commit(a):
    """Close a bookkeeping pass: tick the rows its brief showed and commit the notes.

    **Only the rows this pass was handed.** The timer pulls every few minutes,
    so rows keep arriving while a pass works, and a commit from a conversation
    that never pulled a brief has read none of them. Ticking everything open is
    how an "action required" email got marked handled without anyone opening
    it. So `zipper pull` (or `brief`) records the keys it showed, per pass, and
    commit ticks exactly those. A commit with no brief behind it commits notes
    and ticks nothing.

    The last of the pass's three steps -- fetch, reason, commit -- and the only
    other one that is a command. What happens in between is an agent reading
    the brief against the vault, which is why there is no `bookkeep` command:
    naming one would suggest the machine does the part it cannot do.

    **A pass always ends in a commit.** That is not tidiness -- it is what makes
    the next pass's diff mean anything. The note diff is defined as "changed
    since the last bookkeeping pass", and the only thing making that true is
    that the last pass left the tree clean. Skip the commit once and the diff
    silently becomes general backlog, which is how the tree came to hold five
    sessions' conclusions with nothing marking where one ended.

    Ticking and committing are therefore one step: they are two halves of the
    same claim, that everything in this pass has been looked at and its
    consequences written down.
    """
    from . import serve, locks, chat
    # Only this conversation's files, and the ones nobody holds (the engine's,
    # or a turn that died before its commit). Another live conversation's
    # half-finished edits stay out, so this no longer has to refuse while they
    # are open; --force is accepted and means nothing now.
    me = locks.owner()
    theirs = locks.held_by_others(me)
    from .sync import chain_logs
    chain_logs()   # before the diff, so a log made by hand joins the chain
    changes = [c for c in note_changes() if c['path'] not in theirs]
    paths = [c['path'] for c in changes]
    if theirs:
        print('commit: leaving %d file(s) another conversation is editing' % len(theirs))
    # Before ticking: a pass the hook would refuse must not mark its rows done.
    from . import fanout
    if fanout.check(paths=paths) == fanout.BLOCKED:
        print('commit: nothing ticked or committed.')
        return 1
    serve.feed_load()
    keys = take_pass()
    marked = serve.feed_mark_keys(keys)['marked'] if keys else 0
    end_pass()
    if keys is None:
        left = len(open_rows())
        if left:
            print('commit: no brief pulled in this conversation, so no rows ticked '
                  '(%d open, left for the next pass)' % left)
    # `git add` then `git commit --only` on exactly these paths, under the
    # commit mutex the turn-end hook shares (`locks.commit`). Never the whole
    # index: another conversation may have staged something of its own.
    ok, out = locks.commit(paths, a.message)
    ok = ok and bool(paths)
    print('commit: %d event(s) ticked, %s'
          % (marked, 'committed %d note(s)' % len(paths) if ok
             else 'nothing to commit'))
    if paths and not ok:
        for ln in out.strip().split('\n')[:20]:
            print('  ' + ln)
    else:
        locks.release(me)
    for w in inputs.on_commit():
        print('  WARNING: %s' % w)

    # The invariant, checked rather than assumed. Anything still dirty here
    # would silently widen the next pass's diff, and the whole point of the
    # commit is that it does not.
    left = [c for c in note_changes() if c['path'] not in theirs]
    if left:
        print('  WARNING: %d note(s) still uncommitted — the next pass will show '
              'them as new:' % len(left))
        for c in left[:10]:
            print('    %s %s' % (c['action'], c['path']))
        return 1
    # Prune here rather than on every write. The end of a pass is the one
    # moment the queue is known to be reconciled, so it is the only safe place
    # to throw history away -- and only ticked rows are ever dropped.
    # A vault that has never fetched has no queue file yet -- a new vault's first
    # commit is exactly that case.
    with serve.feed_txn():
        try:
            with open(serve.FEED_JSON, encoding='utf-8') as fh:
                blob = json.load(fh)
        except FileNotFoundError:
            blob = {'rows': []}
        kept = serve.feed_prune(blob.get('rows', []), serve.FEED_MAX)
        dropped = len(blob.get('rows', [])) - len(kept)
        blob['rows'] = kept
        with open(serve.FEED_JSON + '.tmp', 'w', encoding='utf-8') as fh:
            json.dump(blob, fh, indent=1)
        os.replace(serve.FEED_JSON + '.tmp', serve.FEED_JSON)
    if dropped:
        print('  pruned %d ticked row(s); %d kept' % (dropped, len(kept)))

    _write_brief({'generated': datetime.datetime.now().isoformat(timespec='seconds'),
                  'events': [], 'notes_uncommitted': [], 'tasks_dropped': [],
                  'tasks_renamed': [], 'flags': flags()})
    return 0

def cmd_queue(a):
    """Deprecated spelling."""
    return cmd_fetch(a)
