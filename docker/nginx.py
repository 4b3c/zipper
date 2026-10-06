"""Print nginx's config for a zipper in a container, from the environment.

Up to two listeners, each only if its plugin is on. 8899 (dashboard) is the operator's: the dashboard and every terminal under
/t/<port>/. It is behind basic auth, because in a container it is reachable from
the other zippers on the Docker network and from everyone on the tailnet, and the
terminal is a shell. Without ZIPPER_TERM_CRED it listens on loopback only and
says so. The extension's endpoints stay open, because a browser extension cannot
answer an auth prompt: the readings it posts, the Canvas panel's worklist and
cross-off, and /ext/ (the signed add-on and its update manifest).

8898 (peers) is for other zippers: POST /api/msg, and nothing else is proxied.
"""
import os, subprocess, sys

sys.path.insert(0, '/zipper/code')
from zipper import plugins
DASHBOARD = plugins.is_enabled('dashboard')
PEERS = plugins.is_enabled('peers')
web = os.environ.get('ZIPPER_PORT') or '8800'
cred = os.environ.get('ZIPPER_TERM_CRED', '')
if not cred:
    try:
        from zipper.core import _env_file
        cred = _env_file().get('ZIPPER_TERM_CRED', '')
    except Exception:
        pass

listen = '8899'
auth = ''
if ':' in cred:
    user, pw = cred.split(':', 1)
    h = subprocess.run(['openssl', 'passwd', '-apr1', pw], capture_output=True,
                       text=True, check=True).stdout.strip()
    with open('/etc/nginx/zipper.htpasswd', 'w') as fh:
        fh.write('%s:%s\n' % (user, h))
    auth = 'auth_basic "zipper"; auth_basic_user_file /etc/nginx/zipper.htpasswd;'
else:
    listen = '127.0.0.1:8899'
    print('zipper: ZIPPER_TERM_CRED is unset -- the dashboard is loopback-only',
          file=sys.stderr)

DASH = '''
map $http_upgrade $zipper_upgrade { default upgrade; '' close; }
server {
    listen %(listen)s;
    %(auth)s
    location ~ ^/t/(88[0-9][0-9])(/.*)?$ {
        proxy_pass http://127.0.0.1:$1$request_uri;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $zipper_upgrade;
        proxy_set_header Host $host;
        proxy_buffering off;
        proxy_read_timeout 1d;
    }
    location ~ ^/s/(886[0-9])/([A-Za-z0-9_-]+)$ {
        proxy_pass http://127.0.0.1:$1/$2;
    }
    location ~ ^/(api/inputs/|api/canvas|api/hours|api/worklist$|api/done$|ext/|bookmarklet) {
        auth_basic off;
        proxy_pass http://127.0.0.1:%(web)s;
    }
    location / {
        proxy_pass http://127.0.0.1:%(web)s;
        proxy_http_version 1.1;
        proxy_set_header Connection '';
        proxy_buffering off;
        proxy_read_timeout 1h;
    }
}
''' % {'listen': listen, 'auth': auth, 'web': web}
PEER = '''
server {
    listen 8898;
    location = /api/msg { client_max_body_size 64m; proxy_pass http://127.0.0.1:%(web)s; }
    location / { return 404; }
}
''' % {'web': web}
# Only what is switched on listens. With neither plugin, the supervisor does not
# start nginx at all and this file is never read.
print((DASH if DASHBOARD else '') + (PEER if PEERS else ''))
