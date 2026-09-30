"""zipper.host

`zipper host`: the client end of `zipper.hostd`. A zipper talks to the host daemon
over the socket mounted into it (settings `host.socket`); without one, the host
is simply out of reach and this says so.

    zipper host verbs
    zipper host status | containers
    zipper host service.status|service.logs|service.restart <name> [lines]
    zipper host container.restart
    zipper host run <command...>          held for the operator's approval
    zipper host approve <id> <code>       the code the operator read off their app

An advanced request returns at once with an id; hostd has already posted the
exact command to the operator. **Never ask for a code in advance, and never
re-request a refused command with different wording** -- the vault's CLAUDE.md §10.
"""
import json, os, socket

from . import core


def socket_path():
    return core.cfg('ZIPPER_HOST_SOCKET')


def call(verb, args=()):
    path = socket_path()
    if not path:
        return {'ok': False, 'error': 'no host connection: this zipper was not given one '
                                      '(settings host.socket)'}
    if not os.path.exists(path):
        return {'ok': False, 'error': 'host socket %s is missing -- is zipper-hostd running, '
                                      'and is the socket mounted?' % path}
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.connect(path)
        s.sendall((json.dumps({'verb': verb, 'args': list(args)}) + '\n').encode('utf-8'))
        buf = b''
        while not buf.endswith(b'\n'):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    finally:
        s.close()
    return json.loads(buf.decode('utf-8') or '{"ok": false, "error": "empty reply"}')


def cmd_host(a):
    res = call(a.verb, a.args)
    if res.get('output'):
        print(res['output'].rstrip())
    if res.get('tiers') is not None:
        print('granted: %s' % (', '.join(res['tiers']) or 'nothing'))
    if not res.get('ok'):
        print('host: %s' % res.get('error', 'failed'))
        return 1
    return 0
