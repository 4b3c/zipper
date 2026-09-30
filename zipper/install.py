"""zipper.install

The host half of `zipper init`: where the zipper will be reached, its dashboard
password, and starting it. Everything after that -- Claude's login, who the owner
is, which plugins -- happens in the dashboard's terminal, inside the container.

    ssh'd into a server   the dashboard goes on this machine's Tailscale address,
                          so the owner opens it from their own laptop or phone.
                          No Tailscale yet: init says how, and stops before making
                          anything.
    their own computer    the dashboard goes on 127.0.0.1.

**Why the dashboard comes first.** The first real install (zipper-1, 2026-09-30) was
set up over SSH, and a link printed in an SSH window could not be opened from the
laptop -- the secret page for the Discord token never worked. With the dashboard
open in their own browser, every link Claude gives (its login, a secret page)
opens where they already are.
"""
import os, secrets, shutil, socket, subprocess

FIRST_PORT = 8899


def remote():
    """Is init being run over SSH?"""
    return bool(os.environ.get('SSH_CONNECTION') or os.environ.get('SSH_TTY'))


def tailnet_address():
    """This machine's Tailscale IPv4 address, or '' if it has none."""
    if not shutil.which('tailscale'):
        return ''
    try:
        r = subprocess.run(['tailscale', 'ip', '-4'], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return ''
    ip = (r.stdout.split() or [''])[0] if r.returncode == 0 else ''
    return ip if ip.startswith('100.') else ''


TAILSCALE_FIRST = """\
You are on a server (over SSH), so the dashboard will go on this machine's Tailscale
address and you'll open it from your own laptop or phone. This machine isn't on
Tailscale yet. Set it up, then run init again:

    curl -fsSL https://tailscale.com/install.sh | sh
    tailscale up          # prints a link: open it and sign in

and install the Tailscale app on your laptop or phone, signed in to the same account.
(Setting this up on the computer you are sitting at? Add --local.)"""


def free_port(addr, start=FIRST_PORT):
    """The first port from `start` that nothing on `addr` is listening on."""
    for port in range(start, start + 100):
        s = socket.socket()
        try:
            s.bind((addr, port))
            return port
        except OSError:
            continue
        finally:
            s.close()
    raise RuntimeError('no free port on %s from %d' % (addr, start))


def password():
    return secrets.token_urlsafe(12)


def docker_compose():
    """The command for compose on this machine, or None."""
    if shutil.which('docker'):
        r = subprocess.run(['docker', 'compose', 'version'], capture_output=True)
        if r.returncode == 0:
            return ['docker', 'compose']
    if shutil.which('docker-compose'):
        return ['docker-compose']
    return None


def start(home):
    """`docker compose up -d --build` in `home`. Returns (ok, message)."""
    dc = docker_compose()
    if not dc:
        return False, 'Docker is not installed here; install it, then run in %s:\n    docker compose up -d --build' % home
    print('Building and starting it (the first build takes a few minutes)...')
    r = subprocess.run(dc + ['up', '-d', '--build'], cwd=home)
    if r.returncode != 0:
        return False, '`docker compose up` failed; the output above says why. Fix it, then run it again in %s' % home
    return True, ''
