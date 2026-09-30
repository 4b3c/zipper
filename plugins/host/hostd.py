"""zipper.hostd

The host daemon: how a zipper in a container reaches the machine outside it,
without holding root.

    zipper hostd init      (as root, once) write /etc/zipper-hostd.json + a TOTP secret
    zipper hostd install   (as root) copy this file to /opt/zipper-hostd, install the unit
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
               container.restart <the caller's own id>              and announced
    advanced   service.restart <any other allowed service>,         needs approval
               run <command>  (a root shell command)

**Approval the agent cannot fake.** An advanced request is held and posted, by
hostd itself, to a Discord *webhook* -- send-only, and its URL lives only in
hostd's config, which no container can read. The post carries the exact command.
The operator approves with a code from their authenticator app; the zipper passes
it on with `zipper host approve <id> <code>`. The code is checked here against a
secret that also exists only here, is single-use, and five wrong codes lock
approvals for ten minutes. So a fully compromised zipper can ask, but it cannot
change what the operator reads and cannot produce the code.

**It runs from its own copy.** `install` copies this file to /opt/zipper-hostd.
A merged change to it therefore does nothing on the host until someone runs
`install` again -- as root, on the host, which is the approval for updating the
thing that grants root.

Standard library only; this is the most security-sensitive file in the repo, so
it stays small enough to read in one sitting.
"""
import base64, datetime, hashlib, hmac, json, os, secrets, socketserver, struct
import subprocess, sys, threading, time, urllib.request

CONFIG = os.environ.get('ZIPPER_HOSTD_CONFIG', '/etc/zipper-hostd.json')
LOG = os.environ.get('ZIPPER_HOSTD_LOG', '/var/log/zipper-hostd.log')
INSTALL_DIR = '/opt/zipper-hostd'
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
    if verb == 'run':
        cmd = ' '.join(args).strip()
        if not cmd:
            raise ValueError('run needs a command')
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
    return _run(what, shell=True) if isinstance(what, str) else _run(what)


def handle(state, zid, req):
    cfg = state.cfg
    verb, args = req.get('verb', ''), [str(a) for a in req.get('args', [])]
    allowed = cfg['zippers'].get(zid, {}).get('tiers', [])

    if verb == 'verbs':
        return {'ok': True, 'tiers': allowed,
                'output': 'status, containers, service.status|logs|restart <%s>, '
                          'container.restart, run <command>' % '|'.join(cfg['services'])}

    if verb == 'approve':
        return _approve(state, zid, args)

    try:
        tier, what, desc = classify(cfg, zid, verb, args)
    except ValueError as e:
        return {'ok': False, 'error': str(e)}
    if tier not in allowed:
        log('%s refused %s (tier %s not granted)' % (zid, desc, tier))
        return {'ok': False, 'error': 'this zipper is not granted %s requests' % tier}

    if tier == 'read':
        return _box() if verb == 'status' else _execute(what)
    if tier == 'routine':
        log('%s ran %s' % (zid, desc))
        res = _execute(what)
        announce(cfg, '`%s` ran `%s` on %s -- %s' % (zid, desc, cfg.get('host_name') or 'the host',
                                                     'ok' if res['ok'] else 'exit %s' % res['code']))
        return res

    with state.lock:
        rid = state.next_id
        state.next_id += 1
        state.pending[rid] = {'zipper': zid, 'what': what, 'desc': desc, 'at': time.time()}
    log('%s requested #%d: %s' % (zid, rid, desc))
    posted = announce(cfg, '**%s** asks to run as root on %s (request #%d):\n```\n%s\n```\n'
                      'Tell the zipper `approve %d <code from your app>`. '
                      'Expires in %d minutes.' % (zid, cfg.get('host_name') or 'the host', rid,
                                                 desc, rid, PENDING_TTL // 60))
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
    print('installed %s and started zipper-hostd' % dst)


def cmd_hostd(a):
    if a.action == 'init':
        return init()
    if a.action == 'install':
        return install()
    serve(load_config())


if __name__ == '__main__':
    # Run from its installed copy: `python3 /opt/zipper-hostd/hostd.py serve`.
    if len(sys.argv) > 1 and sys.argv[1] == 'serve':
        serve(load_config())
    else:
        print('usage: hostd.py serve')
