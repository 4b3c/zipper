"""The dashboard's cards, per-plugin pulls, and the queue's cross-process lock.

    python3 -m unittest tests.test_dashboard -v

Same rule as tests/test_units.py: the environment is emptied of anything zipper
reads before the package is imported, so nothing here can touch a live vault.
"""
import json, os, shutil, subprocess, sys, tempfile, textwrap, time, unittest

TMP = tempfile.mkdtemp(prefix='zipper-dash-')
for k in [k for k in os.environ if k.startswith(('ZIPPER_', 'DISCORD_', 'GITHUB_', 'BOT_'))]:
    del os.environ[k]
VAULT = os.path.join(TMP, 'vault')
os.makedirs(os.path.join(VAULT, 'Inbox'))
os.environ.update(ZIPPER_SETTINGS=os.path.join(VAULT, 'settings.json'),
                  ZIPPER_ENV_FILE=os.path.join(TMP, 'env'), ZIPPER_VAULT=VAULT)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
ENV = dict(os.environ)      # as set here: another test module may change os.environ later

from zipper import plugins, settings                               # noqa: E402
from zipper.web import cards                                       # noqa: E402


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


def layout(*rows):
    settings.save({'plugins': {'dashboard': {'rows': list(rows)}}})


def vault_card(name, backend, frontend=None):
    d = os.path.join(VAULT, 'Dashboard', name)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, 'backend.py'), 'w') as fh:
        fh.write(textwrap.dedent(backend))
    if frontend:
        with open(os.path.join(d, 'frontend.py'), 'w') as fh:
            fh.write(textwrap.dedent(frontend))


class Resolution(unittest.TestCase):
    def setUp(self):
        cards._SLOW.clear()

    def test_the_three_sources(self):
        vault_card('hello', '''
            def data(ctx):
                return {"items": [{"title": "hi " + ctx.options.get("who", "")}], "count": 1}
            ''', '''
            def render(data, ctx, ui):
                return ui.items(data["items"])
            ''')
        layout({'cards': ['dashboard:queue', {'card': 'vault:hello', 'options': {'who': 'sam'}},
                          'canvas:week']})
        html = cards.body(None)
        self.assertIn('data-live="queue"', html)
        self.assertIn('hi sam', html)
        self.assertIn('data-card="vault-hello"', html)
        # A plugin's card is only there while its plugin is on.
        self.assertIn('the canvas plugin is off', html)

    def test_a_named_renderer_instead_of_a_frontend(self):
        vault_card('plain', '''
            def data(ctx):
                return {"items": [{"title": "from the list renderer"}]}
            ''')
        layout({'cards': [{'card': 'vault:plain', 'frontend': 'dashboard:list'}]})
        self.assertIn('from the list renderer', cards.body(None))

    def test_styled_rows_keep_their_class_and_plain_rows_use_widths(self):
        layout({'style': 'cols2', 'cards': ['dashboard:queue']},
               {'cards': ['dashboard:queue', {'card': 'dashboard:todo', 'width': '300px'}]})
        html = cards.body(None)
        self.assertIn('<div class="cols2">', html)
        self.assertIn('--cols:minmax(0,1fr) 300px', html)


class Isolation(unittest.TestCase):
    def setUp(self):
        cards._SLOW.clear()

    def test_a_crash_stays_in_its_own_card(self):
        vault_card('boom', '''
            def data(ctx):
                raise ValueError("boom")
            ''')
        layout({'cards': ['vault:boom', 'dashboard:queue']})
        html = cards.body(None)
        self.assertIn('ValueError: boom', html)
        self.assertIn('data-live="queue"', html)

    def test_a_slow_card_is_skipped_and_then_left_alone(self):
        vault_card('slow', '''
            import time
            def data(ctx):
                time.sleep(3)
                return {}
            ''')
        layout({'cards': ['vault:slow', {'card': 'vault:slow', 'id': 'slow2'}]})
        old = cards.LIMIT
        cards.LIMIT = 0.3
        try:
            t = time.monotonic()
            html = cards.body(None)
            first = time.monotonic() - t
            t = time.monotonic()
            cards.body(None)
            second = time.monotonic() - t
        finally:
            cards.LIMIT = old
        self.assertEqual(html.count('took longer'), 2)
        self.assertLess(first, 1.0)         # one shared deadline, not one per card
        self.assertLess(second, 0.2)        # in the penalty box: not called again

    def test_duplicate_ids_are_refused(self):
        layout({'cards': ['dashboard:queue', 'dashboard:queue']})
        self.assertIn('already has the id', cards.body(None))


class Pages(unittest.TestCase):
    def setUp(self):
        cards._SLOW.clear()
        vault_card('pinned', '''
            def data(ctx):
                return {"items": [{"title": "on its own page"}]}
            def act_ping(args, ctx):
                return "pong"
            ''')

    def save(self, rows, pages):
        settings.save({'plugins': {'dashboard': {'rows': rows, 'pages': pages}}})

    def test_a_page_draws_its_own_rows_and_the_front_page_does_not(self):
        self.save([{'cards': ['dashboard:pages']}],
                  [{'key': 'money', 'title': 'Money', 'about': 'the bills',
                    'rows': [{'cards': ['vault:pinned']}]}])
        self.assertIn('on its own page', cards.body(None, 'money'))
        front = cards.body(None)
        self.assertNotIn('on its own page', front)
        # The catalog links the page, with its title and line.
        self.assertIn('href="/p/money"', front)
        self.assertIn('the bills', front)
        self.assertEqual(cards.body(None, 'nope'), '')

    def test_a_button_finds_its_card_on_another_page(self):
        self.save([{'cards': ['dashboard:queue']}],
                  [{'key': 'p', 'rows': [{'cards': ['vault:pinned']}]}])
        res = cards.act('vault-pinned', 'ping', {})
        self.assertEqual(res, {'ok': True, 'message': 'pong'})

    def test_an_id_is_unique_across_pages(self):
        self.save([{'cards': ['vault:pinned']}],
                  [{'key': 'p', 'rows': [{'cards': ['vault:pinned']}]}])
        self.assertIn('already has the id', cards.body(None, 'p'))

    def test_bad_and_repeated_keys_are_skipped(self):
        self.save([], [{'key': 'a'}, {'key': 'a', 'title': 'second'}, {'title': 'no key'},
                       'not a page', {'key': 'x/y'}])
        self.assertEqual([p['key'] for p in cards.pages()], ['a'])
        self.assertEqual(cards.pages()[0]['title'], 'a')

    def test_the_page_renders_and_a_missing_one_is_none(self):
        from zipper.web import board
        self.save([], [{'key': 'money', 'title': 'Money',
                        'rows': [{'cards': ['vault:pinned']}]}])
        html = board.page('money')
        self.assertIn('<h1>Money</h1>', html)
        self.assertIn('on its own page', html)
        self.assertIsNone(board.page('nope'))


class Actions(unittest.TestCase):
    def setUp(self):
        cards._SLOW.clear()
        with open(os.path.join(VAULT, 'list.md'), 'w') as fh:
            fh.write('- [ ] one\n- [x] two\n')
        layout({'cards': [{'card': 'dashboard:todo', 'id': 'l',
                           'options': {'file': 'list.md'}}]})

    def test_tick_and_untick(self):
        self.assertEqual(cards.act('l', 'tick', {'line': 0, 'title': 'one'}),
                         {'ok': True, 'message': 'done'})
        self.assertEqual(open(os.path.join(VAULT, 'list.md')).read().splitlines()[0], '- [x] one')
        self.assertTrue(cards.act('l', 'tick', {'line': 1, 'title': 'two'})['ok'])
        self.assertEqual(open(os.path.join(VAULT, 'list.md')).read().splitlines()[1], '- [ ] two')

    def test_a_stale_tick_touches_nothing(self):
        r = cards.act('l', 'tick', {'line': 0, 'title': 'something else'})
        self.assertFalse(r['ok'])
        self.assertIn('changed since', r['message'])
        self.assertEqual(open(os.path.join(VAULT, 'list.md')).read().splitlines()[0], '- [ ] one')

    def test_unknown_card_or_action(self):
        self.assertFalse(cards.act('nope', 'tick', {})['ok'])
        self.assertIn('no action', cards.act('l', 'explode', {})['message'])

    def test_a_vault_card_action(self):
        vault_card('counter', '''
            def data(ctx):
                return {"items": []}
            def act_bump(args, ctx):
                return "bumped %d" % args["by"]
            ''')
        layout({'cards': ['vault:counter']})
        self.assertEqual(cards.act('vault-counter', 'bump', {'by': 3}),
                         {'ok': True, 'message': 'bumped 3'})


class Pulls(unittest.TestCase):
    def test_each_plugin_is_due_on_its_own_timer(self):
        settings.save({'plugins': {'calendar': {'enabled': True, 'poll_minutes': 15},
                                   'github': {'enabled': True, 'poll_minutes': 60}}})
        import datetime
        now = datetime.datetime(2026, 9, 29, 12, 0)
        with open(plugins.PULLS, 'w') as fh:
            json.dump({'calendar': '2026-09-29T11:50:00', 'github': '2026-09-29T11:50:00'}, fh)
        self.assertEqual(plugins.due(now), [])
        later = datetime.datetime(2026, 9, 29, 12, 6)
        self.assertEqual(plugins.due(later), ['calendar'])
        settings.save({'plugins': {'calendar': {'enabled': True, 'poll_minutes': 0}}})
        self.assertEqual(plugins.due(later), [])         # 0: only by hand


class QueueLock(unittest.TestCase):
    def test_two_processes_adding_rows_lose_nothing(self):
        prog = textwrap.dedent('''
            import sys
            sys.path.insert(0, %r)
            from zipper import serve
            for i in range(40):
                serve.publish('diff', 'row %%s-%%d' %% (sys.argv[1], i), system='other', action='add')
            ''' % ROOT)
        env = dict(ENV)
        procs = [subprocess.Popen([sys.executable, '-c', prog, tag], env=env) for tag in 'ab']
        for p in procs:
            p.wait(60)
        with open(os.path.join(VAULT, 'Inbox', 'feed.json')) as fh:
            rows = json.load(fh)['rows']
        self.assertEqual(len([r for r in rows if r['text'].startswith('row ')]), 80)


if __name__ == '__main__':
    unittest.main()
