"""Screenshot a dashboard page in headless Chromium -- the way to verify UI changes.

    python3 tests/shot.py [path] [out.png] [--width N] [--height N] [--click SELECTOR ...]

Signs in with ZIPPER_TERM_CRED from .env (never printed), so terminal iframes load.
Prints any JavaScript errors the page raised. Needs `pip install playwright` and
`python3 -m playwright install --with-deps chromium`.
"""
import argparse, base64, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from zipper import core
from playwright.sync_api import sync_playwright

ap = argparse.ArgumentParser()
ap.add_argument('path', nargs='?', default='/')
ap.add_argument('out', nargs='?', default='/tmp/zipper-shot.png')
ap.add_argument('--width', type=int, default=1600)
ap.add_argument('--height', type=int, default=1000)
ap.add_argument('--click', action='append', default=[], help='CSS selector to click first')
ap.add_argument('--base', default='http://127.0.0.1:8899', help='nginx, so /t/<port>/ works')
a = ap.parse_args()

cred = core.cfg('ZIPPER_TERM_CRED')
with sync_playwright() as p:
    b = p.chromium.launch()
    # Sent up front: nginx redirects a browser without a session to /login rather
    # than challenging it, and Playwright only answers a challenge.
    auth = {'Authorization': 'Basic ' + base64.b64encode(cred.encode()).decode()} if cred else {}
    ctx = b.new_context(viewport={'width': a.width, 'height': a.height},
                        extra_http_headers=auth)
    pg = ctx.new_page()
    errs = []
    pg.on('pageerror', lambda e: errs.append(str(e)))
    pg.goto(a.base + a.path, wait_until='networkidle', timeout=30000)
    pg.wait_for_timeout(1500)
    for sel in a.click:
        pg.locator(sel).first.click()
        pg.wait_for_timeout(1500)
    pg.screenshot(path=a.out, full_page=True)
    b.close()
print('%s  js errors: %s' % (a.out, errs or 'none'))
