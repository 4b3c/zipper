"""zipper.hostd

The host daemon: how a zipper in a container reaches the machine outside it,
without holding root.

    zipper hostd init      (as root, once) write /etc/zipper-hostd.json + a TOTP secret
    zipper hostd install   (as root) copy this file to /opt/zippers/hostd, install the unit
    zipper hostd serve     what the unit runs

**Identity is the socket.** Every zipper gets its own socket, `<socket_dir>/<id>/hostd.sock`,
and only that zipper's container has that directory mounted. A directory, not the
file: hostd recreates the socket when it restarts, and a bind-mounted file would
keep pointing at the old one. So hostd
knows who is asking from which socket the connection arrived on -- there is no
token to steal or forge from inside a container.

**Verbs, not a shell.** A request names a verb and its arguments; each verb is a
function. Three tiers:

    read       status, service.status, service.logs, containers     just runs
    routine    service.restart <name in restart_auto>,              runs, logged,
               container.restart <the caller's own id>,             and announced
               run <a command listed in routine_commands, exactly>
    advanced   service.restart <any other allowed service>,         needs approval
               run <command>  (a root shell command)

**Invites: the approval is the code.** `invite.create` is advanced, so making one
takes the operator's authenticator code. What it makes is a one-use code, stored here
only as a hash, that the shared Discord gateway (`gateway/`) can trade for exactly one
new zipper: `invite.redeem` is the gateway's only verb (tier `provision`), and it can
do nothing else -- not choose the folder, the image, the network or the memory cap,
all of which come from this config. Redeeming creates `<provision.dir>/<id>` with
`zipper init --relay` run inside the image, mounted on that one folder, then starts it.
A compromised gateway can therefore spend codes the operator made, and nothing more.

**Every `run` says why, in plain English** (`--why`). The operator may not read shell;
what they can judge is whether the reason matches what they were just doing. The post
leads with the reason and shows the command under it.

**An approval opens a window.** For `window_minutes` after a code is accepted (15 by
default, 0 to turn it off), that zipper's advanced requests run at once, each still
announced. The code guards against a zipper acting while nobody is there -- a pass at
3am tricked by something it read -- not against one command in a session the operator
is sitting in; asking for a code per command in that session was friction, not safety.

**Approval the agent cannot fake.** An advanced request is held and posted, by
hostd itself, to a Discord *webhook* -- send-only, and its URL lives only in
hostd's config, which no container can read. The post carries the exact command.
The operator approves with a code from their authenticator app; the zipper passes
it on with `zipper host approve <id> <code>`. The code is checked here against a
secret that also exists only here, is single-use, and five wrong codes lock
approvals for ten minutes. So a fully compromised zipper can ask, but it cannot
change what the operator reads and cannot produce the code.

**It runs from its own copy.** `install` copies this file to /opt/zippers/hostd, beside
the zippers' folders but never inside one: a copy a container could write would let
that container rewrite what runs as root. Nothing may mount /opt/zippers whole.
A merged change to it therefore does nothing on the host until someone runs
`install` again -- as root, on the host, which is the approval for updating the
thing that grants root.

Standard library only; this is the most security-sensitive file in the repo, so
it stays small enough to read in one sitting.
"""
import base64, datetime, hashlib, hmac, json, os, secrets, socketserver, struct
import shlex, subprocess, sys, threading, time, urllib.request

CONFIG = os.environ.get('ZIPPER_HOSTD_CONFIG', '/etc/zipper-hostd.json')
LOG = os.environ.get('ZIPPER_HOSTD_LOG', '/var/log/zipper-hostd.log')
INSTALL_DIR = '/opt/zippers/hostd'
OLD_INSTALL_DIR = '/opt/zipper-hostd'   # before 2026-09-30; install removes it
OUTPUT_CAP = 64 * 1024
PENDING_TTL = 30 * 60
LOCKOUT = (5, 10 * 60)          # wrong codes, seconds locked

DEFAULT_CONFIG = {
    'socket_dir': '/run/zipper-hostd',
    'webhook': '',
    'totp_secret': '',
    'host_name': '',
    # Per zipper: which tiers it may use at all. A zipper absent here gets no socket.
    'zippers': {'zipper-0': {'tiers': ['read', 'routine', 'advanced']}},
    'services': ['zipper-web', 'zipper-discord'],   # service.* may name only these
    'restart_auto': ['zipper-web', 'zipper-discord'],  # restarted without approval
    'routine_commands': [],     # `run` commands, matched exactly, that need no code
    # Sites: name -> a git checkout with a compose file. A zipper may manage the
    # ones its own entry lists (`zippers.<id>.sites`): deploy from clean main,
    # restart, status and logs, none of which needs a code.
    'sites': {},
    'window_minutes': 15,       # after an approval, advanced requests run without one
    # Invite codes (hashes only) and the zippers they made.
    'invites': '/etc/zipper-hostd-invites.json',
    'invite_days': 7,
    # Where redeemed invites become zippers. `relay` and `network` are the shared
    # gateway's; `max` caps how many zippers invites can make in all.
    'provision': {'dir': '/opt/zippers', 'image': 'zipper:local', 'network': 'zipper-gateway',
                  'relay': 'http://zipper-gateway:4300', 'mem': '1g', 'max': 5,
                  'prefix': 'zipper-', 'first': 2},
    'systemctl': 'systemctl',
    'journalctl': 'journalctl',
    'docker': 'docker',
}


# ---------------------------------------------------------------- TOTP (RFC 6238)

def totp(secret_b32, t=None, step=30, digits=6):
    key = base64.b32decode(secret_b32.upper() + '=' * (-len(secret_b32) % 8))
    counter = int((time.time() if t is None else t) // step)
    return _hotp(key, counter, digits), counter


def _hotp(key, counter, digits=6):
    h = hmac.new(key, struct.pack('>Q', counter), hashlib.sha1).digest()
    o = h[-1] & 0x0F
    code = (struct.unpack('>I', h[o:o + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits)


def check_code(secret_b32, code, last_used, t=None, window=1):
    """The matching counter, or None. One step either side for clock skew; a
    counter at or before `last_used` is refused, so a code works once."""
    key = base64.b32decode(secret_b32.upper() + '=' * (-len(secret_b32) % 8))
    now = int((time.time() if t is None else t) // 30)
    for c in range(now - window, now + window + 1):
        if c > last_used and hmac.compare_digest(_hotp(key, c), str(code).strip()):
            return c
    return None


# ---------------------------------------------------------------- state

class State:
    def __init__(self, cfg):
        self.cfg = cfg
        self.lock = threading.Lock()
        self.pending = {}           # id -> request
        self.next_id = 1
        self.last_counter = 0
        self.failures = []          # timestamps of wrong codes
        self.windows = {}           # zipper id -> time its approval window closes
        self.invite_lock = threading.Lock()
        self.bad_invites = []       # timestamps of codes that didn't match

    def window_left(self, zid):
        """Seconds left in this zipper's approval window, 0 if none is open."""
        return max(0, int(self.windows.get(zid, 0) - time.time()))

    def open_window(self, zid):
        mins = self.cfg.get('window_minutes', 0) or 0
        if mins > 0:
            self.windows[zid] = time.time() + mins * 60

    def locked_out(self):
        n, secs = LOCKOUT
        now = time.time()
        self.failures = [f for f in self.failures if now - f < secs]
        return len(self.failures) >= n


def log(line):
    stamp = datetime.datetime.now().isoformat(timespec='seconds')
    try:
        with open(LOG, 'a', encoding='utf-8') as fh:
            fh.write('%s  %s\n' % (stamp, line))
    except OSError:
        print('%s  %s' % (stamp, line), file=sys.stderr)


def announce(cfg, text):
    """Post to the operator through hostd's own webhook. Never through a zipper."""
    if not cfg.get('webhook'):
        log('announce (no webhook): %s' % text)
        return False
    body = json.dumps({'content': text[:1900], 'allowed_mentions': {'parse': []}}).encode()
    req = urllib.request.Request(cfg['webhook'], data=body, method='POST',
                                 headers={'Content-Type': 'application/json',
                                          'User-Agent': 'zipper-hostd'})
    try:
        urllib.request.urlopen(req, timeout=20).read()
        return True
    except Exception as e:
        log('announce failed: %s' % e)
        return False


def _run(argv, shell=False):
    p = subprocess.run(argv, shell=shell, capture_output=True, text=True,
                       executable='/bin/bash' if shell else None)
    out = (p.stdout + p.stderr)
    if len(out) > OUTPUT_CAP:
        out = out[:OUTPUT_CAP] + '\n[... %d more bytes]' % (len(out) - OUTPUT_CAP)
    return {'ok': p.returncode == 0, 'code': p.returncode, 'output': out}


# ---------------------------------------------------------------- verbs

def _site(cfg, zid, args):
    mine = cfg['zippers'].get(zid, {}).get('sites', [])
    if len(args) < 1 or args[0] not in mine or args[0] not in cfg.get('sites', {}):
        raise ValueError('not one of this zipper\'s sites (allowed: %s)'
                         % ', '.join(s for s in mine if s in cfg.get('sites', {})))
    return args[0], cfg['sites'][args[0]]


def _in_site(cfg, d, cmd):
    # The directory comes from the host's config, never from the request.
    return 'cd %s && %s' % (shlex.quote(d), cmd)


def _service(cfg, args):
    if len(args) < 1 or args[0] not in cfg['services']:
        raise ValueError('not an allowed service (allowed: %s)' % ', '.join(cfg['services']))
    return args[0]


def classify(cfg, zid, verb, args):
    """(tier, argv-or-shell, description). Raises ValueError for anything unknown."""
    if verb == 'status':
        return 'read', None, 'status'
    if verb == 'containers':
        return 'read', [cfg['docker'], 'ps', '-a', '--format',
                        '{{.Names}}\t{{.Status}}\t{{.Image}}'], 'containers'
    if verb == 'service.status':
        s = _service(cfg, args)
        return 'read', [cfg['systemctl'], 'status', '--no-pager', s], 'service.status %s' % s
    if verb == 'service.logs':
        s = _service(cfg, args)
        n = str(int(args[1])) if len(args) > 1 else '100'
        return 'read', [cfg['journalctl'], '-u', s, '-n', n, '--no-pager'], 'service.logs %s' % s
    if verb == 'service.restart':
        s = _service(cfg, args)
        tier = 'routine' if s in cfg['restart_auto'] else 'advanced'
        return tier, [cfg['systemctl'], 'restart', s], 'systemctl restart %s' % s
    if verb == 'container.restart':
        # Only its own container: a zipper restarting another would be one
        # tenant reaching into another's life.
        return 'routine', [cfg['docker'], 'restart', zid], 'docker restart %s' % zid
    if verb == 'disk':
        return 'read', ('df -h /; echo; du -sh /opt/* /var/lib/docker 2>/dev/null | sort -rh; '
                        'echo; journalctl --disk-usage'), 'disk'
    if verb in ('site.status', 'site.logs', 'site.restart', 'site.deploy'):
        s, d = _site(cfg, zid, args)
        dc = '%s compose' % cfg['docker']
        if verb == 'site.status':
            return 'read', _in_site(cfg, d, '%s ps -a' % dc), 'site.status %s' % s
        if verb == 'site.logs':
            n = str(int(args[1])) if len(args) > 1 else '100'
            return 'read', _in_site(cfg, d, '%s logs --no-color --tail %s' % (dc, n)), \
                'site.logs %s' % s
        if verb == 'site.restart':
            return 'routine', _in_site(cfg, d, '%s restart' % dc), 'site.restart %s' % s
        # Only what is merged on main, with nothing local in the way: a deploy
        # can't ship a branch or a hand edit, so it needs no code.
        git = 'git -c safe.directory=%s' % shlex.quote(d)
        return 'routine', _in_site(cfg, d, (
            '[ "$(%(g)s branch --show-current)" = main ] '
            '|| { echo "not on main"; exit 1; }; '
            '%(g)s diff --quiet HEAD || { echo "uncommitted changes"; exit 1; }; '
            '%(g)s pull --ff-only && %(dc)s up -d --build') % {'g': git, 'dc': dc}), \
            'site.deploy %s' % s
    if verb == 'invite.create':
        return 'advanced', None, 'invite.create'
    if verb == 'run':
        cmd = ' '.join(args).strip()
        if not cmd:
            raise ValueError('run needs a command')
        if cmd in cfg.get('routine_commands', []):
            return 'routine', cmd, cmd
        return 'advanced', cmd, cmd
    raise ValueError('unknown verb %r' % verb)


def _box():
    out = {}
    with open('/proc/loadavg') as fh:
        out['load'] = fh.read().split()[:3]
    mem = {}
    with open('/proc/meminfo') as fh:
        for ln in fh:
            k, v = ln.split(':', 1)
            mem[k] = int(v.split()[0])
    out['mem_used_mb'] = (mem['MemTotal'] - mem['MemAvailable']) // 1024
    out['mem_total_mb'] = mem['MemTotal'] // 1024
    st = os.statvfs('/')
    out['disk_free_gb'] = round(st.f_bavail * st.f_frsize / 1e9, 1)
    with open('/proc/uptime') as fh:
        out['uptime_s'] = int(float(fh.read().split()[0]))
    return {'ok': True, 'output': json.dumps(out)}


def _execute(what):
    if callable(what):
        return what()
    return _run(what, shell=True) if isinstance(what, str) else _run(what)


# ---------------------------------------------------------------- invites

INVITE_ALPHABET = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'   # nothing to misread
INVITE_BAD = (10, 10 * 60)      # wrong codes from the gateway, seconds locked


def _norm_code(code):
    return ''.join(ch for ch in str(code).upper() if ch in INVITE_ALPHABET)


def _code_hash(code):
    return hashlib.sha256(_norm_code(code).encode()).hexdigest()


def _invites_load(cfg):
    try:
        with open(cfg['invites'], encoding='utf-8') as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        data = {}
    data.setdefault('codes', {})
    data.setdefault('made', {})
    return data


def _invites_save(cfg, data):
    tmp = cfg['invites'] + '.tmp'
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, cfg['invites'])


def invite_create(state, zid):
    cfg = state.cfg
    raw = ''.join(secrets.choice(INVITE_ALPHABET) for _ in range(10))
    code = 'ZIP-%s-%s' % (raw[:5], raw[5:])
    expires = time.time() + cfg['invite_days'] * 86400
    with state.invite_lock:
        data = _invites_load(cfg)
        now = time.time()
        data['codes'] = {h: c for h, c in data['codes'].items() if c['expires'] > now}
        data['codes'][_code_hash(code)] = {'expires': expires, 'by': zid, 'made': now}
        _invites_save(cfg, data)
    when = datetime.datetime.fromtimestamp(expires).strftime('%Y-%m-%d')
    log('%s made an invite (expires %s)' % (zid, when))
    return {'ok': True, 'output': 'Invite code: %s\nOne use; expires %s. Whoever pastes it in '
                                  'a server with the Zipper bot gets their own zipper.'
                                  % (code, when)}


def _host_tz():
    try:
        with open('/etc/timezone') as fh:
            return fh.read().strip() or 'UTC'
    except OSError:
        link = os.path.realpath('/etc/localtime')
        return link.split('/zoneinfo/', 1)[1] if '/zoneinfo/' in link else 'UTC'


def _next_id(cfg, data):
    p = cfg['provision']
    n = int(p['first'])
    while True:
        zid = '%s%d' % (p['prefix'], n)
        if zid not in data['made'] and zid not in cfg['zippers'] \
                and not os.path.exists(os.path.join(p['dir'], zid)):
            return zid
        n += 1


def provision(cfg, zid, channel, token):
    """Make and start one relay zipper. (ok, output)."""
    p = cfg['provision']
    home = os.path.join(p['dir'], zid)
    dk = cfg['docker']
    os.makedirs(home, mode=0o700)
    if subprocess.run([dk, 'network', 'inspect', p['network']], capture_output=True).returncode:
        r = _run([dk, 'network', 'create', p['network']])
        if not r['ok']:
            return False, r['output']
    # The token reaches init through the environment, never argv (`ps` shows argv).
    # Only the new zipper's own folder is mounted: never /opt/zippers whole.
    env = dict(os.environ, ZIPPER_NEW_DISCORD_TOKEN=token)
    init = subprocess.run(
        [dk, 'run', '--rm', '-e', 'ZIPPER_NEW_DISCORD_TOKEN', '-e', 'TZ=' + _host_tz(),
         '-e', 'ZIPPER_SETTINGS=/nonexistent/settings.json',
         '-e', 'ZIPPER_ENV_FILE=/nonexistent/.env',
         '-v', '%s:%s' % (home, home), '-w', '/opt/zipper-seed', '--entrypoint', 'python3',
         p['image'], '-m', 'zipper', 'init', home, '--id', zid, '--relay', p['relay'],
         '--discord-channel', channel, '--network', p['network'], '--mem', p['mem']],
        env=env, capture_output=True, text=True)
    out = init.stdout + init.stderr
    if init.returncode:
        return False, out
    up = subprocess.run([dk, 'compose', 'up', '-d'], cwd=home, capture_output=True, text=True)
    return up.returncode == 0, out + up.stdout + up.stderr


def _valid_token(secret):
    return (secret.startswith('zt_') and 30 <= len(secret) <= 100
            and all(ch.isalnum() or ch in '_-' for ch in secret))


def invite_redeem(state, zid, args, secret):
    """The gateway trades a code for a new zipper in a Discord server."""
    cfg = state.cfg
    if len(args) != 3 or not all(a.isascii() and a.isdigit() and 5 <= len(a) <= 25 for a in args[1:]):
        return {'ok': False, 'error': 'invite.redeem <code> <guild id> <channel id>'}
    if not _valid_token(secret):
        return {'ok': False, 'error': 'invite.redeem needs the new zipper\'s token as `secret`'}
    code, guild, channel = args
    with state.invite_lock:
        n, secs = INVITE_BAD
        now = time.time()
        state.bad_invites = [t for t in state.bad_invites if now - t < secs]
        if len(state.bad_invites) >= n:
            return {'ok': False, 'error': 'too many wrong codes; try again in a few minutes'}
        data = _invites_load(cfg)
        h = _code_hash(code)
        entry = data['codes'].get(h)
        if not entry or entry['expires'] < now:
            state.bad_invites.append(now)
            log('%s: an invite code that did not match' % zid)
            return {'ok': False, 'error': 'that code is not valid (used, expired or mistyped)'}
        if len(data['made']) >= int(cfg['provision']['max']):
            return {'ok': False, 'error': 'no room for another zipper on this machine'}
        for other, m in data['made'].items():
            if m.get('guild') == guild:
                return {'ok': False, 'error': 'this server already has %s' % other}
        new = _next_id(cfg, data)
        del data['codes'][h]
        data['made'][new] = {'guild': guild, 'channel': channel, 'at': now,
                             'invite_by': entry['by']}
        _invites_save(cfg, data)
    log('%s redeemed an invite from %s: creating %s for server %s'
        % (zid, entry['by'], new, guild))
    try:
        ok, out = provision(cfg, new, channel, secret)
    except Exception as e:
        ok, out = False, 'provision: %s' % e
    if not ok:
        # The code comes back: a failed build is not a used invite. The half-made
        # folder stays for the operator to read, and _next_id skips it.
        with state.invite_lock:
            data = _invites_load(cfg)
            data['made'].pop(new, None)
            data['codes'][h] = entry
            _invites_save(cfg, data)
        log('creating %s failed:\n%s' % (new, out[-2000:]))
        announce(cfg, 'An invite was redeemed but creating **%s** failed; the code still works.'
                      '\n```\n%s\n```' % (new, out[-1200:]))
        return {'ok': False, 'error': 'could not create the zipper; the operator has been told'}
    announce(cfg, 'An invite from `%s` was redeemed: created **%s** for Discord server %s.'
                  % (entry['by'], new, guild))
    return {'ok': True, 'id': new, 'output': 'created %s' % new}


def handle(state, zid, req):
    cfg = state.cfg
    verb, args = req.get('verb', ''), [str(a) for a in req.get('args', [])]
    why = ' '.join(str(req.get('why') or '').split())[:300]
    allowed = cfg['zippers'].get(zid, {}).get('tiers', [])

    if verb == 'verbs':
        return {'ok': True, 'tiers': allowed,
                'output': 'status, containers, disk, service.status|logs|restart <%s>, '
                          'site.status|logs|restart|deploy <%s>, '
                          'container.restart, invite.create, run --why "<reason>" <command>'
                          % ('|'.join(cfg['services']),
                             '|'.join(cfg['zippers'].get(zid, {}).get('sites', [])))}

    if verb == 'approve':
        return _approve(state, zid, args)

    if verb == 'invite.redeem':
        if 'provision' not in allowed:
            log('%s refused invite.redeem (tier provision not granted)' % zid)
            return {'ok': False, 'error': 'this zipper is not granted provision requests'}
        return invite_redeem(state, zid, args, str(req.get('secret') or ''))

    try:
        tier, what, desc = classify(cfg, zid, verb, args)
    except ValueError as e:
        return {'ok': False, 'error': str(e)}
    if verb == 'invite.create':
        what = lambda: invite_create(state, zid)    # noqa: E731
        why = why or "Make a one-use invite code for a new friend's zipper"
    if verb == 'run' and not why:
        return {'ok': False, 'error': 'run needs --why: one plain sentence saying what it '
                                      'does and why, for someone who does not read shell'}
    host = cfg.get('host_name') or 'the host'
    said = ('%s\n' % why) if why else ''
    if tier not in allowed:
        log('%s refused %s (tier %s not granted)' % (zid, desc, tier))
        return {'ok': False, 'error': 'this zipper is not granted %s requests' % tier}

    if tier == 'read':
        return _box() if verb == 'status' else _execute(what)
    if tier == 'routine':
        log('%s ran %s' % (zid, desc))
        res = _execute(what)
        announce(cfg, '%s`%s` ran `%s` on %s -- %s' % (said, zid, desc, host,
                                                       'ok' if res['ok'] else 'exit %s' % res['code']))
        return res

    left = state.window_left(zid)
    if left:
        log('%s ran %s (approval window, %ds left)' % (zid, desc, left))
        res = _execute(what)
        announce(cfg, '**%s** ran as root on %s, inside your approval window (%d min left):\n'
                      '%s```\n%s\n```\n%s' % (zid, host, (left + 59) // 60, said, desc,
                                             'ok' if res['ok'] else 'exit %s' % res['code']))
        return res

    with state.lock:
        rid = state.next_id
        state.next_id += 1
        state.pending[rid] = {'zipper': zid, 'what': what, 'desc': desc, 'at': time.time()}
    log('%s requested #%d: %s -- %s' % (zid, rid, why, desc))
    mins = cfg.get('window_minutes', 0) or 0
    posted = announce(cfg, '**%s** asks to run as root on %s (request #%d):\n**%s**\n```\n%s\n```\n'
                      'Tell the zipper `approve %d <code from your app>`. Expires in %d minutes.%s'
                      % (zid, host, rid, why or '(no reason given)', desc, rid, PENDING_TTL // 60,
                         (' Approving also lets its next requests run without a code for %d '
                          'minutes.' % mins) if mins > 0 else ''))
    return {'ok': True, 'pending': rid, 'posted': posted,
            'output': 'request #%d is waiting for approval%s' %
                      (rid, '' if posted else ' (NOT posted: hostd has no webhook)')}


def _approve(state, zid, args):
    if len(args) != 2 or not args[0].isdigit():
        return {'ok': False, 'error': 'approve <id> <code>'}
    rid, code = int(args[0]), args[1]
    with state.lock:
        if state.locked_out():
            return {'ok': False, 'error': 'too many wrong codes; approvals are locked for a while'}
        req = state.pending.get(rid)
        if not req or time.time() - req['at'] > PENDING_TTL:
            state.pending.pop(rid, None)
            return {'ok': False, 'error': 'no such request (or it expired)'}
        if req['zipper'] != zid:
            log('%s tried to approve #%d, which belongs to %s' % (zid, rid, req['zipper']))
            return {'ok': False, 'error': 'that request is not yours'}
        c = check_code(state.cfg['totp_secret'], code, state.last_counter)
        if c is None:
            state.failures.append(time.time())
            log('%s: wrong code for #%d' % (zid, rid))
            return {'ok': False, 'error': 'wrong or reused code'}
        state.last_counter = c
        state.pending.pop(rid)
        state.open_window(zid)
    log('%s: #%d approved, running: %s' % (zid, rid, req['desc']))
    res = _execute(req['what'])
    log('#%d exit %s' % (rid, res.get('code')))
    announce(state.cfg, 'Request #%d ran -- %s' % (rid, 'ok' if res['ok'] else 'exit %s' % res['code']))
    return res


# ---------------------------------------------------------------- serving

def _handler_for(state, zid):
    class H(socketserver.StreamRequestHandler):
        def handle(self):
            try:
                req = json.loads(self.rfile.readline().decode('utf-8'))
                res = handle(state, zid, req)
            except Exception as e:
                res = {'ok': False, 'error': 'hostd: %s' % e}
            self.wfile.write((json.dumps(res) + '\n').encode('utf-8'))
    return H


class _Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def load_config(path=CONFIG):
    with open(path, encoding='utf-8') as fh:
        cfg = json.load(fh)
    return dict(DEFAULT_CONFIG, **cfg)


def serve(cfg):
    if not cfg.get('totp_secret'):
        raise SystemExit('hostd: no totp_secret in config -- run `zipper hostd init`')
    state = State(cfg)
    d = cfg['socket_dir']
    os.makedirs(d, exist_ok=True)
    servers = []
    for zid in cfg['zippers']:
        os.makedirs(os.path.join(d, zid), exist_ok=True)
        path = os.path.join(d, zid, 'hostd.sock')
        if os.path.exists(path):
            os.remove(path)
        s = _Server(path, _handler_for(state, zid))
        os.chmod(path, 0o660)
        servers.append(s)
        threading.Thread(target=s.serve_forever, daemon=True).start()
        log('listening for %s on %s' % (zid, path))
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    return state


# ---------------------------------------------------------------- setup (as root, on the host)

def init(path=CONFIG):
    if os.path.exists(path):
        raise SystemExit('%s exists -- edit it, or delete it to start over' % path)
    cfg = dict(DEFAULT_CONFIG)
    cfg['totp_secret'] = base64.b32encode(secrets.token_bytes(20)).decode().rstrip('=')
    cfg['host_name'] = os.uname().nodename
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as fh:
        json.dump(cfg, fh, indent=2)
    print('wrote %s (mode 600)' % path)
    print('Add this to your authenticator app (it is shown once, and lives only in that file):')
    print('  secret: %s' % cfg['totp_secret'])
    print('  otpauth://totp/zipper-hostd:%s?secret=%s&issuer=zipper-hostd'
          % (cfg['host_name'], cfg['totp_secret']))
    print('Then set "webhook" to a Discord webhook URL (channel -> Integrations -> Webhooks),')
    print('list your zippers and allowed services, and run `zipper hostd install`.')


UNIT = """[Unit]
Description=zipper-hostd -- host verbs for zippers, advanced ones approved by TOTP
After=network-online.target

[Service]
ExecStart=/usr/bin/python3 %(dir)s/hostd.py serve
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
"""


def install():
    os.makedirs(INSTALL_DIR, exist_ok=True)
    os.chmod(INSTALL_DIR, 0o700)
    src = os.path.abspath(__file__)
    dst = os.path.join(INSTALL_DIR, 'hostd.py')
    with open(src, 'rb') as a, open(dst, 'wb') as b:
        b.write(a.read())
    os.chmod(dst, 0o700)
    with open('/etc/systemd/system/zipper-hostd.service', 'w') as fh:
        fh.write(UNIT % {'dir': INSTALL_DIR})
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', 'enable', '--now', 'zipper-hostd'], check=True)
    subprocess.run(['systemctl', 'restart', 'zipper-hostd'], check=True)
    old = os.path.join(OLD_INSTALL_DIR, 'hostd.py')
    if OLD_INSTALL_DIR != INSTALL_DIR and os.path.exists(old):
        os.remove(old)
        try:
            os.rmdir(OLD_INSTALL_DIR)
        except OSError:
            pass
        print('removed the old copy in %s' % OLD_INSTALL_DIR)
    print('installed %s and started zipper-hostd' % dst)


def cmd_hostd(a):
    if a.action == 'init':
        return init()
    if a.action == 'install':
        return install()
    serve(load_config())


if __name__ == '__main__':
    # Run from its installed copy: `python3 /opt/zippers/hostd/hostd.py serve`.
    if len(sys.argv) > 1 and sys.argv[1] == 'serve':
        serve(load_config())
    else:
        print('usage: hostd.py serve')
