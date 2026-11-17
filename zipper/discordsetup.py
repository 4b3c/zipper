"""zipper.discordsetup -- `zipper discord connect`, run on the machine.

**Every zipper reaches Discord through a gateway** (`gateway/`), one per machine, which
alone holds the real bot token. One zipper or several, the shape is the same, and a
second zipper on the machine is one more `connect`, with no second bot.

    ./zipper discord connect           from the zipper's folder, on the machine

1. **The gateway.** If none is running here, it asks for a bot token (or offers the one
   this zipper already had), writes the gateway's folder beside this zipper's
   (`compose.yml`, `.env`, `state/`) and starts it on the `zipper-gateway` network.
   One already running is used as it is, and no token is asked for.
2. **Pairing.** The gateway mints this zipper's own token and a code. The token goes
   into this zipper's `config/.env` with `DISCORD_RELAY`; the code is printed.
3. **This zipper** joins the gateway's network and restarts. Its bot asks the relay
   until the code is claimed, then saves that channel as `discord.channel`.
4. **The person** adds the bot to their server and pastes the code in any channel.
   That channel is home.

It runs on the host because the gateway is a container beside this one: a zipper
cannot write another container's `.env`, and the bot token is never typed into the
dashboard or a chat. Inside a container it says so and stops.
"""
import base64, binascii, getpass, json, os, shutil, subprocess, sys

from . import settings
from .core import ENV_FILE

GATEWAY = 'zipper-gateway'          # its container, hostname and network
RELAY = 'http://zipper-gateway:4300'
# View Channel, Send Messages, Add Reactions, Attach Files, Read Message History,
# Create Public Threads, Send Messages in Threads.
PERMISSIONS = 309237746752

BOT_STEPS = """\
Discord needs a bot. Make one (about five minutes):

  1. discord.com/developers/applications -> New Application, any name.
  2. Bot tab -> Reset Token -> copy it. On the same page turn on
     Message Content Intent.

Paste the token below. It goes to this machine's gateway and nowhere else; the
input is hidden.
"""

DONE = """
Paired. Last step, in Discord:

  1. Add the bot to your server%(link)s
  2. Paste this code in any channel there; that channel becomes {zid}'s home:

        %(code)s

The code lasts a week. {zid} starts answering as soon as it is pasted.
"""


def home():
    """This zipper's folder on the machine: the one holding its config/.env."""
    return os.path.dirname(os.path.dirname(os.path.abspath(ENV_FILE)))


def _docker(*args, **kw):
    return subprocess.run(['docker'] + list(args), capture_output=True, text=True, **kw)


def compose():
    from .install import docker_compose
    return docker_compose()


def find_gateway():
    """(its folder, running) for this machine's gateway, or (None, False)."""
    r = _docker('inspect', GATEWAY, '--format',
                '{{index .Config.Labels "com.docker.compose.project.working_dir"}}|{{.State.Running}}')
    if r.returncode != 0:
        return None, False
    folder, _, running = r.stdout.strip().rpartition('|')
    # One started by hand has no compose label; it is still this machine's gateway.
    return folder or '(not made by compose)', running == 'true'


def _env_value(path, key):
    """One value from a .env file, read here on the host by this command only."""
    try:
        with open(path, encoding='utf-8') as fh:
            for ln in fh:
                k, _, v = ln.strip().partition('=')
                if k.strip() == key:
                    return v.strip().strip('"\'')
    except OSError:
        pass
    return ''


def ask_token():
    """The bot token: the one this zipper ran its own bot with, if it had one and
    the owner says to reuse it, else pasted (hidden)."""
    own = _env_value(ENV_FILE, 'DISCORD_TOKEN')
    if own and not own.startswith('zt_') and not _env_value(ENV_FILE, 'DISCORD_RELAY'):
        ans = input('This zipper has its own Discord bot. Make it the gateway\'s bot? [Y/n] ')
        if ans.strip().lower() in ('', 'y', 'yes'):
            return own
    print(BOT_STEPS)
    return getpass.getpass('Bot token: ').strip()


def make_gateway(folder, token):
    """Write the gateway's folder and start it. Returns '' or what went wrong."""
    from .setup import ROOT, env_set
    os.makedirs(os.path.join(folder, 'state'), exist_ok=True)
    shutil.copyfile(os.path.join(ROOT, 'gateway', 'compose.yml'),
                    os.path.join(folder, 'compose.yml'))
    envf = os.path.join(folder, '.env')
    if not os.path.exists(envf):
        fd = os.open(envf, os.O_WRONLY | os.O_CREAT, 0o600)
        os.close(fd)
    env_set('DISCORD_TOKEN', token, envf)
    r = _docker('network', 'create', GATEWAY)
    if r.returncode != 0 and 'already exists' not in r.stderr:
        return r.stderr.strip()
    r = subprocess.run(compose() + ['up', '-d'], cwd=folder)
    return '' if r.returncode == 0 else '`docker compose up` failed in %s; the output above says why' % folder


def join_network(folder):
    """Put this zipper on the gateway's network, editing its compose.yml once.
    Returns '' or what to do by hand."""
    path = os.path.join(folder, 'compose.yml')
    with open(path, encoding='utf-8') as fh:
        text = fh.read()
    if GATEWAY in text:
        return ''
    lines = text.splitlines(keepends=True)
    at = next((i for i, ln in enumerate(lines) if ln.startswith('    hostname:')), None)
    if at is None or any(ln.startswith('networks:') for ln in lines):
        return ('add this zipper to the external network "%s" in %s, then '
                '`docker compose up -d` there' % (GATEWAY, path))
    lines.insert(at + 1, '    networks: [default, %s]   # its Discord gateway\n' % GATEWAY)
    text = ''.join(lines).rstrip('\n') + '\n\nnetworks:\n  %s:\n    external: true\n' % GATEWAY
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(text)
    return ''


def client_id(token):
    head = (token or '').split('.')[0]
    try:
        cid = base64.b64decode(head + '=' * (-len(head) % 4)).decode()
    except (binascii.Error, UnicodeDecodeError):
        return ''
    return cid if cid.isdigit() else ''


def invite_link(cid):
    return ('https://discord.com/oauth2/authorize?client_id=%s&scope=bot&permissions=%d'
            % (cid, PERMISSIONS)) if cid else ''


def connect(again=False):
    if os.path.exists('/.dockerenv') or not shutil.which('docker') or not compose():
        print('discord connect runs on the machine, not in a zipper: from the zipper\'s '
              'folder there, `./zipper discord connect`. (It needs Docker.)')
        return 1
    folder = home()
    if not os.path.exists(os.path.join(folder, 'compose.yml')):
        print('discord connect: %s has no compose.yml -- run it with the `./zipper` in a '
              'zipper\'s own folder' % folder)
        return 1
    zid = settings.zipper_id()

    gw, running = find_gateway()
    token = ''
    if gw is None:
        token = ask_token()
        if not token:
            print('discord connect: no token, nothing changed'); return 1
        gw = os.path.join(os.path.dirname(folder), 'gateway')
        if os.path.exists(os.path.join(gw, 'compose.yml')):
            print('discord connect: %s already holds a gateway that isn\'t running; '
                  'start it there (`docker compose up -d`) and run this again' % gw)
            return 1
        print('Starting the gateway in %s...' % gw)
        err = make_gateway(gw, token)
        if err:
            print('discord connect: %s' % err); return 1
    elif not running:
        print('Starting the gateway (%s)...' % gw)
        if _docker('start', GATEWAY).returncode != 0:
            print('discord connect: the gateway would not start; see above'); return 1

    r = _docker('exec', GATEWAY, 'python3', '-m', 'gateway', 'pair', zid,
                *(['--again'] if again else []))
    try:
        out = json.loads((r.stdout.strip().splitlines() or ['{}'])[-1])
    except ValueError:
        out = {'error': (r.stderr or r.stdout).strip() or 'the gateway gave no answer'}
    if out.get('error') or not out.get('token'):
        err = out.get('error') or 'no token from the gateway'
        print('discord connect: %s' % err)
        if 'already linked' in err:
            print('It already answers there. To move it to another server: '
                  '`./zipper discord connect --again`.')
        return 1

    from .setup import env_set
    env_set('DISCORD_TOKEN', out['token'], ENV_FILE)
    env_set('DISCORD_RELAY', RELAY, ENV_FILE)
    # The channel the code is pasted in becomes home; an old one would win over it.
    settings.put('discord.channel', '')
    settings.put('scoops.discord.enabled', True)
    err = join_network(folder)
    if err:
        print('discord connect: paired, but %s' % err); return 1
    print('Restarting %s on the gateway\'s network...' % zid)
    if subprocess.run(compose() + ['up', '-d', '--force-recreate'], cwd=folder).returncode != 0:
        print('discord connect: paired, but the restart failed; run `docker compose up -d` in %s'
              % folder)
        return 1
    link = invite_link(out.get('client_id') or client_id(token))
    print(DONE.replace('{zid}', zid) % {
        'code': out['code'],
        'link': (' with this link:\n\n        %s\n' % link) if link else
                ' (developer portal -> OAuth2 -> URL Generator: scope bot; View Channels, '
                'Send Messages, Send Messages in Threads, Create Public Threads, Read '
                'Message History, Attach Files, Add Reactions).'})
    return 0


def cmd_connect(a):
    try:
        return connect(again=getattr(a, 'again', False))
    except (KeyboardInterrupt, EOFError):
        print('\ndiscord connect: stopped, nothing more changed')
        return 1
