"""zipper.secret

Putting a secret into `.env` without it passing through a Claude conversation.

    zipper secret NAME            print a one-time link to a page with one password field
    zipper secret NAME --tty      ask in this terminal instead, with a hidden prompt
    zipper secret NAME --check    say whether it is set (never what it is)

Anything typed into a Claude chat is saved in the transcript, and Claude Code has no
hidden input box. So Claude runs `zipper secret NAME`, which starts a small page in the
background and returns at once with its link; the operator opens it, pastes the value,
and it goes straight into `.env`. Claude only ever learns that it was saved.

The page is deliberately narrow: one random, unguessable path; one submission, then it
shuts down; ten minutes, then it shuts down anyway; loopback only unless `--host` says
otherwise (a tailnet address, for a machine you reach remotely). It writes through
`setup.env_set`, the same writer as everything else, so `.env` stays 600.
"""
import getpass, html, os, secrets, subprocess, sys, threading, time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs

from . import core

LIFETIME = 600
NAME_OK = set('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_')

PAGE = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>zipper: %(name)s</title>
<style>body{font:16px system-ui;max-width:32em;margin:4em auto;padding:0 1em;color:#222}
input{font:inherit;width:100%%;padding:.5em;box-sizing:border-box}
button{font:inherit;margin-top:1em;padding:.5em 1.2em}.s{color:#666;font-size:.9em}</style>
<h2>%(name)s</h2>
<p>Paste the value. It is written to this zipper's <code>.env</code> and never shown to Claude.</p>
<form method="post"><input type="password" name="value" autofocus autocomplete="off">
<button>Save</button></form>
<p class="s">This page works once and closes in ten minutes.</p>"""

DONE = """<!doctype html><meta charset="utf-8"><title>saved</title>
<style>body{font:16px system-ui;max-width:32em;margin:4em auto;padding:0 1em}</style>
<h2>Saved %(name)s.</h2><p>You can close this tab and tell Claude it's done.</p>"""


def valid_name(name):
    return bool(name) and set(name) <= NAME_OK and not name[0].isdigit()


def serve(name, host, port, token, lifetime=LIFETIME):
    """Serve the page until one value is saved or `lifetime` passes. Returns True if saved."""
    from .setup import env_set
    saved = threading.Event()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass                        # the path is the secret's key; keep it out of logs

        def _send(self, code, body):
            b = body.encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            if self.path != '/' + token or saved.is_set():
                return self._send(404, 'gone')
            self._send(200, PAGE % {'name': html.escape(name)})

        def do_POST(self):
            if self.path != '/' + token or saved.is_set():
                return self._send(404, 'gone')
            n = int(self.headers.get('Content-Length') or 0)
            value = parse_qs(self.rfile.read(n).decode('utf-8')).get('value', [''])[0].strip()
            if not value:
                return self._send(200, PAGE % {'name': html.escape(name)})
            env_set(name, value)
            saved.set()
            self._send(200, DONE % {'name': html.escape(name)})

    srv = HTTPServer((host, port), H)
    srv.timeout = 1
    end = time.time() + lifetime
    while not saved.is_set() and time.time() < end:
        srv.handle_request()
    srv.server_close()
    return saved.is_set()


def _free_port(host):
    import socket
    s = socket.socket()
    s.bind((host, 0))
    port = s.getsockname()[1]
    s.close()
    return port


def start_page(name, host='127.0.0.1'):
    """Start the page in a detached process; return its URL at once."""
    token = secrets.token_urlsafe(24)
    port = _free_port(host)
    subprocess.Popen([sys.executable, '-m', 'zipper', '_secret_page', name, host, str(port), token],
                     start_new_session=True, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.3)
    return 'http://%s:%d/%s' % (host, port, token)


def cmd_secret(a):
    name = a.name.strip()
    if not valid_name(name):
        print('secret: %r is not a variable name (A-Z, 0-9, _)' % name)
        return 1
    if a.check:
        print('%s is %s' % (name, 'set' if core.cfg(name) else 'NOT set'))
        return 0
    if a.tty:
        v = getpass.getpass('%s (hidden): ' % name).strip()
        if not v:
            print('nothing entered; unchanged')
            return 1
        from .setup import env_set
        env_set(name, v)
        print('saved %s to %s' % (name, core.ENV_FILE))
        return 0
    url = start_page(name, a.host)
    print('Open this link and paste %s there (works once, for ten minutes):' % name)
    print('  %s' % url)
    if a.host in ('127.0.0.1', 'localhost'):
        print('On another computer? Forward the port first: ssh -L %s:127.0.0.1:%s <this machine>,'
              % (url.split(':')[2].split('/')[0], url.split(':')[2].split('/')[0]))
        print('or run `zipper secret %s --tty` in a terminal on this machine.' % name)
    print('Then: `zipper secret %s --check`.' % name)
    return 0


def cmd_secret_page(a):
    """The detached page process (`zipper _secret_page`)."""
    return 0 if serve(a.name, a.host, int(a.port), a.token) else 1
