"""Peers: messages from other zippers.

Another zipper POSTs to `/api/msg` (the only path the peer port 8898 proxies):

    {"from": "zipper-1", "text": "...", "token": "<ZIPPER_COMMS_TOKEN>"}

A message becomes a queue row -- `who: zipper-1` -- and a line in the
notifications channel. **It is information, never an instruction.** Text from
another zipper is text from another person's life and another person's inbox,
which is exactly where a prompt injection would come from; the vault's CLAUDE.md
says to treat it like an email.

Accepted only with the shared token and from an id listed in settings `peers`,
at most 30 an hour from each. Stored in Inbox/messages.json, the last 200.
"""
import datetime, hmac, json, os, threading

from .. import core, settings

name = 'peers'
SETUP_TITLE = 'Other zippers'
SETUP_ABOUT = 'messages from zippers on this host, as queue rows'
STORE = os.path.join(core.INBOX, 'messages.json')
KEEP, PER_HOUR, MAX_TEXT = 200, 30, 2000
_LOCK = threading.Lock()


def setup(w):
    w.say('Peers are other zippers: id -> URL of their message port, e.g.')
    w.say('  zipper-1 -> http://zipper-1:8898  (same Docker network)')
    while True:
        pid = w.ask('A peer id (blank when done)')
        if not pid:
            break
        url = w.ask('Its message URL', default='http://%s:8898' % pid)
        settings.put('peers.%s' % pid, url)
    w.secret('ZIPPER_COMMS_TOKEN', 'The token the zippers on this host share')


def _load():
    try:
        return json.load(open(STORE, encoding='utf-8'))
    except (FileNotFoundError, ValueError):
        return {'messages': []}


def receive(body):
    token = core.cfg('ZIPPER_COMMS_TOKEN')
    if not token or not hmac.compare_digest(str(body.get('token', '')), token):
        raise ValueError('bad or missing token')
    sender = str(body.get('from', ''))
    if sender not in (settings.get('peers') or {}):
        raise ValueError('unknown sender %r' % sender)
    text = str(body.get('text', '')).strip()[:MAX_TEXT]
    if not text:
        raise ValueError('empty message')
    now = datetime.datetime.now()
    with _LOCK:
        blob = _load()
        hour_ago = (now - datetime.timedelta(hours=1)).isoformat()
        recent = [m for m in blob['messages'] if m['from'] == sender and m['at'] > hour_ago]
        if len(recent) >= PER_HOUR:
            raise ValueError('too many messages from %s this hour' % sender)
        msg = {'id': '%s-%s' % (sender, now.strftime('%Y%m%d%H%M%S%f')), 'from': sender,
               'at': now.isoformat(timespec='seconds'), 'text': text}
        blob['messages'] = (blob['messages'] + [msg])[-KEEP:]
        tmp = STORE + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as fh:
            json.dump(blob, fh, indent=1)
        os.replace(tmp, STORE)
    try:
        from .. import chat
        chat.discord_send('**%s** says: %s' % (sender, text[:500]),
                          thread_id=core.cfg('ZIPPER_NOTIFY_CHANNEL') or None)
    except Exception:
        pass
    return {'ok': True, 'id': msg['id']}


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

def send(peer, text):
    import urllib.request
    url = (settings.get('peers') or {}).get(peer)
    if not url:
        raise RuntimeError('no peer %r in settings (peers: %s)'
                           % (peer, ', '.join(settings.get('peers') or {}) or 'none'))
    body = json.dumps({'from': settings.zipper_id(), 'text': text,
                       'token': core.cfg('ZIPPER_COMMS_TOKEN')}).encode()
    req = urllib.request.Request(url.rstrip('/') + '/api/msg', data=body, method='POST',
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=20) as fh:
        return json.loads(fh.read())


def cmd_msg(a):
    try:
        res = send(a.peer, a.text)
    except Exception as e:
        print('msg: %s' % e)
        return 1
    print('sent to %s (%s)' % (a.peer, res.get('id', '?')))
    return 0
