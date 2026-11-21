"""Two things the browser extension hands over: time on sites, and course files.

Both are evidence the box cannot gather itself -- the browser is where the
person is, and where the Canvas session lives -- and both stay machine state.
Neither enters the vault: site time backs up hours he reports (it never writes
hours), and course files are originals kept so Zipper can read them, which the
vault's "conclusions, not caches" rule keeps out of notes.
"""
import datetime, json, os, re, threading

from .. import core, settings

# ---------------------------------------------------------------- site time

LOCK = threading.Lock()
DOMAIN = re.compile(r'^[a-z0-9.-]{1,253}$')
DAY = re.compile(r'^\d{4}-\d{2}-\d{2}$')
DEVICE = re.compile(r'^[A-Za-z0-9_-]{4,64}$')


def sitetime_path():
    return os.path.join(core.INBOX, 'sitetime.json')


def _load(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return {}


def _save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp.%d' % os.getpid()
    with open(tmp, 'w') as fh:
        json.dump(data, fh, indent=1, sort_keys=True)
    os.replace(tmp, path)


def receive_sitetime(body):
    """`{device, totals: {day: {domain: seconds}}}` -- running totals, not deltas.

    Totals per device make a resend harmless: a lost reply, a retry, two tabs
    flushing at once all write the same number again, and the larger one wins.
    A delta would be counted twice by exactly those. The device id keeps two
    browsers from overwriting each other; the day's figure is their sum.
    """
    if not isinstance(body, dict):
        raise ValueError('expected an object')
    dev = str(body.get('device') or '')
    if not DEVICE.match(dev):
        raise ValueError('bad device id')
    totals = body.get('totals')
    if not isinstance(totals, dict):
        raise ValueError('expected totals')
    n = 0
    with LOCK:
        data = _load(sitetime_path())
        for day, doms in totals.items():
            if not DAY.match(str(day)) or not isinstance(doms, dict):
                continue
            for dom, secs in doms.items():
                dom = str(dom).lower()
                if not DOMAIN.match(dom):
                    continue
                try:
                    secs = max(0, min(86400, int(secs)))
                except (TypeError, ValueError):
                    continue
                slot = data.setdefault(day, {}).setdefault(dom, {})
                if secs > slot.get(dev, 0):
                    slot[dev] = secs
                    n += 1
        _save(sitetime_path(), data)
    return {'ok': True, 'updated': n}


def sitetime(days=7, today=None):
    """{day: {domain: seconds}} for the last `days` days, devices summed."""
    today = today or core.TODAY
    data = _load(sitetime_path())
    out = {}
    for i in range(days - 1, -1, -1):
        d = (today - datetime.timedelta(days=i)).isoformat()
        out[d] = {dom: sum(v.values()) for dom, v in (data.get(d) or {}).items()}
    return out


def cmd_sitetime(days=7):
    for day, doms in sitetime(days).items():
        if not doms:
            print('%s  -' % day)
            continue
        parts = ['%s %dm' % (dom, round(s / 60))
                 for dom, s in sorted(doms.items(), key=lambda kv: -kv[1])]
        print('%s  %s' % (day, ', '.join(parts)))


# ------------------------------------------------------------- course files

MAX_BYTES = 50 * 1024 * 1024
KEEP = {'pdf', 'ppt', 'pptx', 'doc', 'docx', 'xls', 'xlsx', 'txt', 'md', 'csv',
        'rtf', 'odt', 'odp', 'tex', 'ipynb', 'py', 'zip'}


def files_root():
    return os.environ.get('ZIPPER_COURSE_FILES') or os.path.join(settings.ROOT, 'data', 'course-files')


def _safe(name, limit=120):
    name = re.sub(r'[^\w.\- ()&,+]', '_', str(name)).strip(' .') or 'file'
    return name[:limit]


def _course_dir(course_id):
    if not re.match(r'^\d{1,12}$', str(course_id)):
        raise ValueError('bad course id')
    return os.path.join(files_root(), str(course_id))


def course_index(course_id):
    """What is already here, so the browser skips files that haven't changed."""
    return _load(os.path.join(_course_dir(course_id), 'index.json'))


def have(course_id):
    idx = course_index(course_id)
    return {'ok': True, 'files': {fid: f.get('updated_at') for fid, f in
                                  (idx.get('files') or {}).items()}}


def receive_file(headers, raw):
    """One file's bytes, with what it is in headers (a JSON body would need
    base64 and a third more memory for the biggest slides).

    Written to a temporary name and renamed, so a process killed mid-write
    leaves no half-PDF under a real name, and the index is updated only after
    the file is in place: an index entry always points at a whole file.
    """
    course = headers.get('X-Course-Id', '')
    cdir = _course_dir(course)
    fid = headers.get('X-File-Id', '')
    if not re.match(r'^\d{1,15}$', fid):
        raise ValueError('bad file id')
    name = _safe(urlunquote(headers.get('X-File-Name', '') or 'file'))
    ext = name.rsplit('.', 1)[-1].lower() if '.' in name else ''
    if ext not in KEEP:
        raise ValueError('not a document type: %r' % ext)
    if len(raw) > MAX_BYTES:
        raise ValueError('over the size cap')
    # A body that isn't the file must never be stored as it: the index would
    # then call it held, and every later press would skip it.
    if not raw:
        raise ValueError('empty body')
    want = headers.get('X-Expected-Size', '')
    if not re.match(r'^\d{1,12}$', want or ''):
        raise ValueError('missing X-Expected-Size')
    if int(want) != len(raw):
        raise ValueError('got %d bytes, Canvas says %s' % (len(raw), want))
    folder = urlunquote(headers.get('X-Folder', '') or '').strip()
    folder = _safe(folder, 80) if folder else ''
    course_name = urlunquote(headers.get('X-Course-Name', '') or '')[:200]
    with LOCK:
        idx = _load(os.path.join(cdir, 'index.json'))
        files = idx.setdefault('files', {})
        old = files.get(fid)
        rel = os.path.join(folder, name) if folder else name
        # Two files may share a name in one folder; the id keeps them apart.
        taken = {f['path'] for k, f in files.items() if k != fid}
        if rel in taken:
            stem, dot, ext2 = name.rpartition('.')
            rel = os.path.join(folder, '%s (%s).%s' % (stem, fid, ext2))
        dest = os.path.join(cdir, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        tmp = dest + '.part'
        with open(tmp, 'wb') as fh:
            fh.write(raw)
        os.replace(tmp, dest)
        if old and old.get('path') != rel:
            try:
                os.remove(os.path.join(cdir, old['path']))
            except OSError:
                pass
        files[fid] = {'path': rel, 'name': name, 'size': len(raw),
                      'updated_at': headers.get('X-Updated-At', ''),
                      'fetched': datetime.datetime.now().isoformat(timespec='seconds')}
        if course_name:
            idx['course'] = course_name
        _save(os.path.join(cdir, 'index.json'), idx)
    return {'ok': True, 'path': rel}


def urlunquote(s):
    from urllib.parse import unquote
    return unquote(s)


def cmd_files():
    root = files_root()
    if not os.path.isdir(root):
        print('no course files yet (%s)' % root)
        return
    for cid in sorted(os.listdir(root)):
        idx = _load(os.path.join(root, cid, 'index.json'))
        files = idx.get('files') or {}
        size = sum(f.get('size', 0) for f in files.values())
        print('%s  %-40s %4d files  %6.1f MB  %s' % (
            cid, (idx.get('course') or '?')[:40], len(files), size / 1e6,
            os.path.join(root, cid)))
