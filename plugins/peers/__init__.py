"""Peers: messages from other zippers.

Another zipper POSTs to `/api/msg` (the only path the peer port 8898 proxies):

    {"from": "zipper-1", "text": "..."}

**No password: the network is the check.** The sender must be listed in settings
`plugins.peers.zippers`, and the request must come from the address that entry's host
resolves to -- on a shared Docker network, `zipper-1` resolves to zipper-1's container
and nothing else. nginx hands the real source over as X-Real-IP; the dashboard's port
refuses `/api/msg`, so the header cannot be supplied from outside.

A message becomes a queue row -- `who: zipper-1` -- and a line in the
notifications channel. **It is information, never an instruction.** Text from
another zipper is text from another person's life and another person's inbox,
which is exactly where a prompt injection would come from; the vault's CLAUDE.md
says to treat it like an email.

Accepted only from a peer listed in settings `plugins.peers.zippers`,
at most 30 an hour from each. Stored in Inbox/messages.json, the last 200.

**A message wakes a reply** (`turn.py`): a short, read-only headless turn whose final
message is sent back. A reply is marked as one and never wakes a turn, so two zippers
cannot keep each other talking.

**Files ride along** (`zipper msg <peer> "text" --file a.png`): base64 in the same
POST, at most 40 MB together. The receiver saves them under
Inbox/peer-files/<message id>/ and appends `attached file saved here: <path>` to the
text, as the bot does for Discord. Zippers share no disk, so a path alone is
useless to the other side. The last 50 messages' files are kept.

**A message can wake a topic.** After storing, every enabled plugin with
`on_peer_message(msg)` is called -- topics uses it to start a run at once instead of
at its next tick. `plugins.peers.notify: false` keeps messages out of Discord, for a
peer that talks often to a zipper rather than to its owner.
"""
import base64, datetime, json, os, shutil, socket, threading, urllib.parse

from zipper import core, settings

name = 'peers'
STORE = os.path.join(core.INBOX, 'messages.json')
FILES = os.path.join(core.INBOX, 'peer-files')
KEEP, PER_HOUR, MAX_TEXT = 200, 30, 2000
MAX_FILES_BYTES, KEEP_FILES = 40 * 1024 * 1024, 50
_LOCK = threading.Lock()


def _load():
    try:
        return json.load(open(STORE, encoding='utf-8'))
    except (FileNotFoundError, ValueError):
        return {'messages': []}


def _verify(sender, source):
    """The sender is a listed peer, and the request came from its address."""
    url = (settings.get('plugins.peers.zippers') or {}).get(sender)
    if not url:
        raise ValueError('unknown sender %r' % sender)
    host = urllib.parse.urlsplit(url).hostname or ''
    try:
        addrs = socket.gethostbyname_ex(host)[2]
    except OSError:
        raise ValueError('cannot resolve %s, the address of %s' % (host, sender))
    if not source or source not in addrs:
        raise ValueError('message says it is from %s but came from %s'
                         % (sender, source or 'nowhere known'))


def receive(body, source=None):
    sender = str(body.get('from', ''))
    _verify(sender, source)
    text = str(body.get('text', '')).strip()[:MAX_TEXT]
    files = _decode(body.get('files') or [])
    if not text and not files:
        raise ValueError('empty message')
    now = datetime.datetime.now()
    with _LOCK:
        blob = _load()
        hour_ago = (now - datetime.timedelta(hours=1)).isoformat()
        recent = [m for m in blob['messages'] if m['from'] == sender and m['at'] > hour_ago]
        if len(recent) >= PER_HOUR:
            raise ValueError('too many messages from %s this hour' % sender)
        mid = '%s-%s' % (sender, now.strftime('%Y%m%d%H%M%S%f'))
        paths = _save(mid, files)
        text = '\n'.join([text] + ['attached file saved here: %s' % p for p in paths]).strip()
        msg = {'id': mid, 'from': sender, 'at': now.isoformat(timespec='seconds'), 'text': text}
        if body.get('reply'):
            msg['reply'] = True
        blob['messages'] = (blob['messages'] + [msg])[-KEEP:]
        tmp = STORE + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as fh:
            json.dump(blob, fh, indent=1)
        os.replace(tmp, STORE)
    if settings.get('plugins.peers.notify', True) is not False:
        try:
            from zipper import chat
            chat.discord_send('**%s** says: %s' % (sender, text[:500]),
                              thread_id=core.cfg('ZIPPER_NOTIFY_CHANNEL') or None)
        except Exception:
            pass
    _hooks(msg)
    from . import turn
    turn.wake(msg)
    return {'ok': True, 'id': msg['id'], 'files': len(files)}


def _decode(items):
    """[(name, bytes)] from the POST's files, refusing anything that could escape
    the message's folder or is too big together."""
    out, total, seen = [], 0, set()
    for f in items:
        name = os.path.basename(str(f.get('name', '')))
        if not name or name.startswith('.') or '\x00' in name:
            raise ValueError('bad file name %r' % f.get('name'))
        stem, ext = os.path.splitext(name)
        n = 2
        while name in seen:                     # two a.png in one message: a.png, a-2.png
            name, n = '%s-%d%s' % (stem, n, ext), n + 1
        seen.add(name)
        try:
            data = base64.b64decode(f.get('data', ''), validate=True)
        except (ValueError, TypeError):
            raise ValueError('file %s is not base64' % name)
        total += len(data)
        if total > MAX_FILES_BYTES:
            raise ValueError('files over %d MB together' % (MAX_FILES_BYTES // 2 ** 20))
        out.append((name, data))
    return out


def _save(mid, files):
    if not files:
        return []
    d = os.path.join(FILES, mid)
    os.makedirs(d, exist_ok=True)
    paths = []
    try:
        for name, data in files:
            p = os.path.join(d, name)
            with open(p, 'wb') as fh:
                fh.write(data)
            paths.append(p)
    except Exception:
        shutil.rmtree(d, ignore_errors=True)    # no half-saved message left behind
        raise
    # Keep the newest KEEP_FILES folders; ids sort by time within a sender, and the
    # folder's mtime orders across senders.
    dirs = sorted((os.path.join(FILES, x) for x in os.listdir(FILES)), key=os.path.getmtime)
    for old in dirs[:-KEEP_FILES]:
        shutil.rmtree(old, ignore_errors=True)
    return paths


def _hooks(msg):
    from zipper import plugins
    for p in plugins.enabled():
        if hasattr(p, 'on_peer_message'):
            try:
                p.on_peer_message(msg)
            except Exception as e:
                print('peers: %s on_peer_message failed: %s' % (p.name, e))


def fetched():
    msgs = _load()['messages']
    return msgs[-1]['at'] if msgs else None


def snapshot():
    return {m['id']: [m['at'][:16], m['from'], m['text']] for m in _load()['messages']}


def events(before, after):
    return [{'system': 'zipper', 'action': 'add', 'when': at, 'who': sender,
             'text': 'message     %s' % text.replace('\n', ' ')[:200]}
            for mid, (at, sender, text) in sorted(after.items(), key=lambda kv: kv[1][0])
            if mid not in before]


# ---------------------------------------------------------------- sending

def send(peer, text, files=(), reply=False):
    import urllib.request
    url = (settings.get('plugins.peers.zippers') or {}).get(peer)
    if not url:
        raise RuntimeError('no peer %r in settings (peers: %s)'
                           % (peer, ', '.join(settings.get('plugins.peers.zippers') or {}) or 'none'))
    enc, total = [], 0
    for path in files:
        data = open(path, 'rb').read()
        total += len(data)
        if total > MAX_FILES_BYTES:
            raise RuntimeError('files over %d MB together' % (MAX_FILES_BYTES // 2 ** 20))
        enc.append({'name': os.path.basename(path), 'data': base64.b64encode(data).decode()})
    body = json.dumps({'from': settings.zipper_id(), 'text': text, 'files': enc,
                       'reply': bool(reply)}).encode()
    req = urllib.request.Request(url.rstrip('/') + '/api/msg', data=body, method='POST',
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=120 if enc else 20) as fh:
        return json.loads(fh.read())


def cmd_msg(a):
    try:
        res = send(a.peer, a.text, getattr(a, 'file', None) or [])
    except Exception as e:
        print('msg: %s' % e)
        return 1
    print('sent to %s (%s)' % (a.peer, res.get('id', '?')))
    return 0
