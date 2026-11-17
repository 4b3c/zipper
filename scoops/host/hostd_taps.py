"""hostd_taps

Approving a held request with one tap in Discord instead of a code. Part of hostd
(`hostd.py`), installed beside it, standard library only.

hostd posts each held request through its webhook, as it always has. With `taps`
configured it also puts a check mark and a cross under that post, using its own bot,
and a thread here reads who has tapped them every few seconds. A tap counts only from
a Discord user listed in `taps.approvers`. The check mark approves exactly that request
and opens the approval window, as a code does. The cross refuses it.

**Why a tap is as safe as the code.** What approves is the operator's own Discord account
tapping, and no zipper can tap as them. The post is the webhook's, so nothing holding the
bot token can change what the operator reads. The bot's own reactions count for nothing.
A stolen token can therefore read the host channel and grant nothing; give the bot that
one channel and no other permission.

**Reactions, not buttons.** A button's click reaches only a gateway connection or a public
HTTPS endpoint: the first would be most of this daemon's code, the second would put the
thing that grants root on the internet. A reaction is read with one GET.

The authenticator code still works for every request; it is the fallback when Discord
or this bot is down.
"""
import json, os, re, threading, time, urllib.error, urllib.parse, urllib.request

API = 'https://discord.com/api/v10'
YES, NO = '✅', '❌'
EVERY = 3           # seconds between reads of the held requests


def configured(cfg):
    t = cfg.get('taps') or {}
    return bool(t.get('token') and t.get('approvers'))


def _api(cfg, method, path):
    req = urllib.request.Request(API + path, method=method, data=b'' if method == 'PUT' else None,
                                 headers={'Authorization': 'Bot %s' % cfg['taps']['token'],
                                          'User-Agent': 'DiscordBot (zipper-hostd, 1)'})
    with urllib.request.urlopen(req, timeout=15) as r:
        raw = r.read()
    return json.loads(raw) if raw else None


def _path(channel, message, emoji):
    return '/channels/%s/messages/%s/reactions/%s' % (channel, message,
                                                     urllib.parse.quote(emoji))


def add_buttons(cfg, channel, message):
    """Put the two reactions under the post. Failing here costs one extra tap (the
    operator adds the reaction themselves), so it is logged, not fatal."""
    for e in (YES, NO):
        _api(cfg, 'PUT', _path(channel, message, e) + '/@me')
        time.sleep(0.3)     # Discord allows about four reactions a second


def _tapped(cfg, channel, message, emoji):
    users = _api(cfg, 'GET', _path(channel, message, emoji) + '?limit=100') or []
    approvers = {str(a) for a in cfg['taps']['approvers']}
    return any(str(u.get('id')) in approvers for u in users)


def decision(cfg, channel, message):
    """'no', 'yes' or None. A cross wins over a check mark: when both are tapped,
    the operator has changed their mind or slipped, and not running is the safe side."""
    if _tapped(cfg, channel, message, NO):
        return 'no'
    if _tapped(cfg, channel, message, YES):
        return 'yes'
    return None


def offer(state, rid, msg, log):
    """Put the reactions under request `rid`'s post and start watching it. True if the
    request can be tapped; False leaves it to a code, as before taps existed."""
    if not (msg and msg.get('id') and msg.get('channel_id')):
        return False
    try:
        add_buttons(state.cfg, msg['channel_id'], msg['id'])
    except Exception as e:
        log('taps: could not add reactions to #%d (they can still be added by hand): %s' % (rid, e))
    with state.lock:
        if rid in state.pending:
            state.pending[rid]['post'] = (msg['channel_id'], msg['id'])
    return True


def watch(state, h, log, sleep=time.sleep, once=False):
    """Read the taps on every held request with a post and act on them (`decide`).
    Runs forever on its own thread; `h` is the hostd module."""
    while True:
        sleep(EVERY)
        with state.lock:
            now = time.time()
            for rid in [k for k, r in state.pending.items() if now - r['at'] > h.PENDING_TTL]:
                state.results[rid] = {'zipper': state.pending.pop(rid)['zipper'],
                                      'at': now, 'state': 'expired'}
            held = [(rid, r['post']) for rid, r in state.pending.items() if r.get('post')]
        for rid, (channel, message) in held:
            try:
                d = decision(state.cfg, channel, message)
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    sleep(float(e.headers.get('Retry-After') or 5))
                    break
                if e.code in (403, 404):
                    # The post is gone or the bot lost the channel: the code still works.
                    log('taps: cannot read #%d (%s); approve it with a code' % (rid, e.code))
                    with state.lock:
                        if rid in state.pending:
                            state.pending[rid]['post'] = None
                continue
            except Exception as e:      # a network blip: try again next round
                log('taps: reading #%d failed: %s' % (rid, e))
                continue
            if d:
                decide(h, state, rid, d)
        if once:
            return


def decide(h, state, rid, choice):
    """Act on a tap. Taking the request out of `pending` under the lock is what makes it
    run once, whichever of a tap and a code arrives first."""
    with state.lock:
        req = state.pending.pop(rid, None)
        if not req:
            return
        zid, now = req['zipper'], time.time()
        if now - req['at'] > h.PENDING_TTL:
            choice = 'expired'
        elif choice == 'yes':
            state.open_window(zid)
        state.results[rid] = {'zipper': zid, 'at': now,
                              'state': {'yes': 'running', 'no': 'refused'}.get(choice, choice)}
    if choice == 'yes':
        threading.Thread(target=h._carry_out, args=(state, rid, req, 'with a tap'),
                         daemon=True).start()
    elif choice == 'no':
        h.log('%s: #%d refused with a tap' % (zid, rid))
        h.announce(state.cfg, 'Request #%d refused.' % rid)


def result(state, zid, args, ttl):
    """`result <id>`: where a request stands -- waiting, running, done (with its output),
    refused or expired. Only the zipper that asked may see it."""
    if len(args) != 1 or not args[0].isdigit():
        return {'ok': False, 'error': 'result <id>'}
    rid = int(args[0])
    with state.lock:
        now = time.time()
        for k in [k for k, r in state.results.items() if now - r['at'] > ttl]:
            del state.results[k]
        req, res = state.pending.get(rid), state.results.get(rid)
        if req and req['zipper'] == zid:
            if now - req['at'] > ttl:
                return {'ok': False, 'state': 'expired', 'error': 'request #%d expired' % rid}
            return {'ok': True, 'state': 'waiting'}
        if not res or res['zipper'] != zid:
            return {'ok': False, 'state': 'unknown', 'error': 'no such request'}
        out = {k: v for k, v in res.items() if k not in ('zipper', 'at')}
    if out['state'] in ('refused', 'expired'):
        out.update(ok=False, error='request #%d was %s' % (rid, out['state']))
    elif out['state'] == 'running':
        out['ok'] = True
    return out


# ---------------------------------------------------------------- setup (as root, on the host)

def setup(config_path, env_file, name, approver):
    """Write the bot token and approver into hostd's config. The token is read from a
    file on the host (a zipper's .env), so it never appears in the command, which hostd
    posts in full when it asks for approval."""
    if not re.fullmatch(r'\d{15,22}', approver):
        raise SystemExit('the approver is a Discord user id (digits only)')
    token = ''
    with open(env_file, encoding='utf-8') as fh:
        for line in fh:
            k, _, v = line.strip().partition('=')
            if k.strip() == name:
                token = v.strip().strip('"\'')
    if not token:
        raise SystemExit('%s is not set in %s' % (name, env_file))
    with open(config_path, encoding='utf-8') as fh:
        cfg = json.load(fh)
    cfg['taps'] = {'token': token, 'approvers': [approver]}
    tmp = config_path + '.new'
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as fh:
        json.dump(cfg, fh, indent=2)
    os.replace(tmp, config_path)
    print('taps on for user %s; restart zipper-hostd to start reading them' % approver)
