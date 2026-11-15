"""The Zipper calendar (zipper/gcal.py), with Google replaced by a fake.

    python3 -m unittest tests.test_gcal -v
"""
import io, json, os, shutil, sys, tempfile, unittest, urllib.error

TMP = tempfile.mkdtemp(prefix='zipper-gcal-')
for k in [k for k in os.environ if k.startswith(('ZIPPER_', 'DISCORD_', 'GITHUB_', 'BOT_'))]:
    del os.environ[k]
os.environ.update(ZIPPER_SETTINGS=os.path.join(TMP, 'settings.json'),
                  ZIPPER_ENV_FILE=os.path.join(TMP, 'env'),
                  ZIPPER_VAULT=os.path.join(TMP, 'vault'))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from zipper import core, gcal, google   # noqa: E402


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


class FakeGoogle:
    """Calendars and events in memory, answering the paths gcal calls."""
    def __init__(self):
        self.calendars, self.events, self.calls = [], {}, []

    def __call__(self, path, method='GET', payload=None, **params):
        self.calls.append((method, path))
        if path == '/calendars' and method == 'POST':
            cid = 'cal%d@group.calendar.google.com' % len(self.calendars)
            self.calendars.append(cid)
            return {'id': cid, 'summary': payload['summary']}
        parts = path.split('/')
        if method == 'POST':
            if payload['id'] in self.events:
                raise urllib.error.HTTPError(path, 409, 'exists', {}, io.BytesIO(b''))
            self.events[payload['id']] = dict(payload)
            return payload
        if method == 'PUT':
            self.events[parts[-1]] = dict(payload)
            return payload
        if method == 'DELETE':
            if parts[-1] not in self.events:
                raise urllib.error.HTTPError(path, 404, 'gone', {}, io.BytesIO(b''))
            del self.events[parts[-1]]
            return {}
        return {'items': list(self.events.values())}


class ZipperCalendar(unittest.TestCase):
    def setUp(self):
        self.fake = FakeGoogle()
        self._call = gcal._call
        gcal._call = self.fake
        os.environ.pop('ZIPPER_GCAL_ID', None)
        # core and google keep whichever env path loaded first; when another test
        # module set it, its teardown may have removed the folder.
        os.makedirs(os.path.dirname(google.ENV), exist_ok=True)
        open(google.ENV, 'w').close()

    def tearDown(self):
        gcal._call = self._call

    def test_event_id_is_stable_and_legal(self):
        a = gcal.event_id('2026-11-17 Team photo')
        self.assertEqual(a, gcal.event_id(' 2026-11-17 team photo '))
        self.assertNotEqual(a, gcal.event_id('2026-11-18 Team photo'))
        self.assertTrue(5 <= len(a) <= 1024)
        self.assertTrue(set(a) <= set('0123456789abcdefghijklmnopqrstuv'))

    def test_default_lengths(self):
        timed = gcal.body('Photo', '2026-11-17T18:00')
        self.assertTrue(timed['start']['dateTime'].startswith('2026-11-17T18:00'))
        self.assertTrue(timed['end']['dateTime'].startswith('2026-11-17T19:00'))
        day = gcal.body('Form opens', '2026-11-20')
        self.assertEqual(day['start'], {'date': '2026-11-20'})
        self.assertEqual(day['end'], {'date': '2026-11-21'})

    def test_same_key_updates_not_duplicates(self):
        self.assertEqual(gcal.put('k', 'Photo', '2026-11-17T18:00'), 'added')
        self.assertEqual(gcal.put('k', 'Photo at 18:30', '2026-11-17T18:30'), 'updated')
        self.assertEqual(len(self.fake.events), 1)
        self.assertEqual(list(self.fake.events.values())[0]['summary'], 'Photo at 18:30')

    def test_calendar_made_once_and_remembered(self):
        gcal.put('a', 'One', '2026-11-17')
        gcal.put('b', 'Two', '2026-11-18')
        self.assertEqual(len(self.fake.calendars), 1)
        self.assertIn('ZIPPER_GCAL_ID=cal0@group.calendar.google.com',
                      open(google.ENV).read())

    def test_remove_missing_is_not_an_error(self):
        gcal.put('a', 'One', '2026-11-17')
        self.assertTrue(gcal.remove('a'))
        self.assertFalse(gcal.remove('a'))

    def test_sync_writes_the_agenda_file(self):
        gcal.put('k', 'Photo', '2026-11-17T18:00', note='Juan takes it')
        evs = gcal.sync()
        data = json.load(open(os.path.join(core.INBOX, 'calendar-zipper.json')))
        self.assertEqual(data['label'], 'zipper')
        self.assertEqual(len(evs), 1)
        self.assertEqual(data['events'][0]['summary'], 'Photo')
        self.assertEqual(data['events'][0]['description'], 'Juan takes it')
        self.assertRegex(data['events'][0]['start'], r'^2026-11-1[78] \d\d:\d\d$')

    def test_no_calendar_yet_reads_as_empty(self):
        self.assertEqual(gcal.events(), [])
        self.assertEqual(self.fake.calls, [])


class Errors(unittest.TestCase):
    def _err(self, code, message):
        body = json.dumps({'error': {'code': code, 'message': message}}).encode()
        return urllib.error.HTTPError('u', code, 'x', {}, io.BytesIO(body))

    def test_disabled_api_is_not_called_a_scope_problem(self):
        msg = gcal._explain(self._err(403, 'Google Calendar API has not been used in project 1'))
        self.assertIn('has not been used', msg)
        self.assertNotIn('--auth', msg)

    def test_missing_scope_says_sign_in_again(self):
        msg = gcal._explain(self._err(403, 'Request had insufficient authentication scopes.'))
        self.assertIn('zipper google --auth', msg)


class Scopes(unittest.TestCase):
    def test_calendar_scope_is_requested(self):
        self.assertIn(gcal.SCOPE, google.scopes())


if __name__ == '__main__':
    unittest.main()
