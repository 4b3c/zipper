"""The timesheet: hours read back from the Google Sheet that is the system of record.

It produces no queue rows on purpose: a week's total is current state, not an
event, for the same reason a flag is not a row. The ledger lives in `zipper.hours`.
"""
import json, os

from .. import core, hours as lib

name = 'hours'
SETUP_TITLE = 'Timesheet (Google Sheets)'
SETUP_ABOUT = 'log hours by message; a Google Sheet stays the record'


def setup(w):
    w.setting('inputs.hours.sheet', 'The sheet id -- the part of its URL after /d/')
    w.setting('inputs.hours.metric', 'Metric name for weekly totals', default='hours_worked')
    w.setting('google.client_id', 'Google OAuth client id (console.cloud.google.com -> Credentials)')
    w.secret('ZIPPER_GOOGLE_CLIENT_SECRET', 'Its client secret')
    w.say('Then run `zipper google --auth` and open the link to connect the account.')


def pull():
    lib.cmd_refresh()


def receive(body):
    """A reading of the sheet from the browser panel. The sheet wins: rows typed there
    are adopted, and a captured row seen there stops being pending."""
    rows = body.get('rows') if isinstance(body, dict) else body
    if not isinstance(rows, list):
        raise ValueError('expected {"rows": [...]}')
    d = body if isinstance(body, dict) else {}
    res = lib.reconcile(rows, tab=d.get('tab'), complete=bool(d.get('complete')),
                        force=bool(d.get('force')))
    res['write'] = lib.to_write()
    return dict(res, ok=True)


def fetched():
    try:
        with open(os.path.join(core.INBOX, 'hours.json')) as fh:
            return json.load(fh).get('sheet', {}).get('fetched')
    except Exception:
        return None
