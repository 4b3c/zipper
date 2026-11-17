"""python3 -m gateway serve | add <zipper id> <guild id> [<channel id>] | pair <zipper id>
| list | remove <id>"""
import argparse, asyncio, base64, binascii, json, os, sys

from aiohttp import web

from gateway import registry, relay

PORT = int(os.environ.get('GATEWAY_PORT', 4300))
HOST = os.environ.get('GATEWAY_HOST', '0.0.0.0')


async def serve():
    from gateway import upstream
    token = os.environ.get('DISCORD_TOKEN')
    if not token:
        raise SystemExit('gateway: DISCORD_TOKEN is not set')
    hub = relay.Hub(registry.Registry(), token)
    runner = web.AppRunner(relay.app(hub))
    await runner.setup()
    await web.TCPSite(runner, HOST, PORT).start()
    print('[gateway] relay listening on %s:%d' % (HOST, PORT))

    async def prune():
        while True:
            await asyncio.sleep(30)
            hub.prune()
    asyncio.ensure_future(prune())
    await upstream.client(hub).start(token)


def client_id():
    """The bot's application id, for its invite link: a bot token's first part is
    that id in base64. '' when the token isn't shaped that way."""
    head = (os.environ.get('DISCORD_TOKEN') or '').split('.')[0]
    try:
        cid = base64.b64decode(head + '=' * (-len(head) % 4)).decode()
    except (binascii.Error, UnicodeDecodeError):
        return ''
    return cid if cid.isdigit() else ''


def main(argv=None):
    p = argparse.ArgumentParser(prog='python3 -m gateway')
    sub = p.add_subparsers(dest='cmd', required=True)
    sub.add_parser('serve')
    a = sub.add_parser('add', help='link a server to a zipper and print its token')
    a.add_argument('zipper')
    a.add_argument('guild')
    a.add_argument('channel', nargs='?', default='')
    pr = sub.add_parser('pair', help='a token and a code for a zipper on this machine; '
                                     'pasting the code in a server links it there')
    pr.add_argument('zipper')
    pr.add_argument('--again', action='store_true', help='move a zipper that is already linked')
    sub.add_parser('list')
    r = sub.add_parser('remove')
    r.add_argument('zipper')
    args = p.parse_args(argv)
    reg = registry.Registry()
    if args.cmd == 'serve':
        asyncio.run(serve())
    elif args.cmd == 'add':
        try:
            token = reg.add(args.zipper, args.guild, args.channel)
        except ValueError as e:
            raise SystemExit('gateway: %s' % e)
        print('linked %s to server %s. Its token, shown once -- put it in that zipper\'s '
              'config/.env as DISCORD_TOKEN, with DISCORD_RELAY=http://<this host>:%d:'
              % (args.zipper, args.guild, PORT))
        print(token)
    elif args.cmd == 'pair':
        # One line of JSON for `zipper discord connect`, which runs this through
        # `docker exec` and writes the token into the zipper's .env; the code is
        # the only part a person sees.
        try:
            code, token = reg.pair(args.zipper, again=args.again)
        except ValueError as e:
            print(json.dumps({'error': str(e)}))
            return 3
        print(json.dumps({'code': code, 'token': token, 'client_id': client_id()}))
    elif args.cmd == 'list':
        for zid, e in sorted(reg.zippers().items()):
            print('%s\tserver %s\tchannel %s\tsince %s'
                  % (zid, e['guild'], e.get('channel') or '-', e.get('added', '?')))
        for zid in sorted(reg.pending()):
            print('%s\twaiting for its pairing code' % zid)
    elif args.cmd == 'remove':
        if not reg.remove(args.zipper):
            raise SystemExit('gateway: no zipper %s' % args.zipper)
        print('unlinked %s; its token no longer works' % args.zipper)


if __name__ == '__main__':
    sys.exit(main())
