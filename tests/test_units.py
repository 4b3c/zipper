"""Unit tests: the pure parts, fast, with no network, no Claude and no Discord.

    python3 -m unittest tests.test_units -v

Run in CI on every pull request (.github/workflows/check.yml). The environment is
emptied of anything zipper reads before the package is imported, so a run on the
box cannot touch the live vault, settings or .env -- a sandbox that inherited the
live environment once logged a second bot in with the real token.
"""
import base64, datetime, json, os, shutil, subprocess, sys, tempfile, time, unittest

TMP = tempfile.mkdtemp(prefix='zipper-units-')
for k in [k for k in os.environ if k.startswith(('ZIPPER_', 'DISCORD_', 'GITHUB_', 'BOT_'))]:
    del os.environ[k]
os.environ.update(ZIPPER_SETTINGS=os.path.join(TMP, 'settings.json'),
                  ZIPPER_ENV_FILE=os.path.join(TMP, 'env'),
                  ZIPPER_VAULT=os.path.join(TMP, 'vault'))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from zipper import plugins, settings, supervise, setup, runqueue   # noqa: E402
from zipper import presence                                        # noqa: E402
from plugins import backup, peers, upstream                        # noqa: E402
from plugins.host import hostd                                     # noqa: E402


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


class TOTP(unittest.TestCase):
    def test_rfc6238_vector(self):
        # RFC 6238 appendix B, SHA-1, T=59: 94287082.
        self.assertEqual(hostd._hotp(b'12345678901234567890', 59 // 30, 8), '94287082')

    def test_code_works_once(self):
        secret = base64.b32encode(b'A' * 20).decode()
        code, counter = hostd.totp(secret)
        used = hostd.check_code(secret, code, last_used=0)
        self.assertEqual(used, counter)
        self.assertIsNone(hostd.check_code(secret, code, last_used=used))

    def test_wrong_code(self):
        secret = base64.b32encode(b'A' * 20).decode()
        self.assertIsNone(hostd.check_code(secret, '000000', last_used=0, t=10 ** 9))


class HostTiers(unittest.TestCase):
    cfg = dict(hostd.DEFAULT_CONFIG, services=['zipper-web', 'pantry'],
               restart_auto=['zipper-web'])

    def tier(self, verb, *args):
        return hostd.classify(self.cfg, 'zipper-1', verb, list(args))[0]

    def test_tiers(self):
        self.assertEqual(self.tier('status'), 'read')
        self.assertEqual(self.tier('service.logs', 'pantry'), 'read')
        self.assertEqual(self.tier('service.restart', 'zipper-web'), 'routine')
        self.assertEqual(self.tier('service.restart', 'pantry'), 'advanced')
        self.assertEqual(self.tier('run', 'ls'), 'advanced')

    def test_own_container_only(self):
        _, argv, _ = hostd.classify(self.cfg, 'zipper-1', 'container.restart', ['zipper-0'])
        self.assertEqual(argv[-1], 'zipper-1')

    def test_refusals(self):
        for verb, args in (('service.status', ['nginx']), ('rm', ['-rf', '/']), ('run', [])):
            with self.assertRaises(ValueError):
                hostd.classify(self.cfg, 'zipper-1', verb, args)

    def test_approval_is_the_requesters(self):
        secret = base64.b32encode(b'C' * 20).decode()
        st = hostd.State(dict(self.cfg, totp_secret=secret, zippers={
            'zipper-0': {'tiers': ['advanced']}, 'zipper-1': {'tiers': ['advanced']}}))
        st.pending[1] = {'zipper': 'zipper-0', 'what': 'true', 'desc': 'true', 'at': time.time()}
        code = hostd.totp(secret)[0]
        self.assertIn('not yours', hostd._approve(st, 'zipper-1', ['1', code])['error'])
        self.assertIn(1, st.pending)

    def test_routine_commands_match_exactly(self):
        cfg = dict(self.cfg, routine_commands=['systemctl restart zipper-bridge'])
        self.assertEqual(hostd.classify(cfg, 'zipper-0', 'run',
                                        ['systemctl', 'restart', 'zipper-bridge'])[0], 'routine')
        self.assertEqual(hostd.classify(cfg, 'zipper-0', 'run',
                                        ['systemctl restart zipper-bridge; rm -rf /x'])[0], 'advanced')


class HostWindow(unittest.TestCase):
    secret = base64.b32encode(b'D' * 20).decode()

    def setUp(self):
        hostd.LOG = os.path.join(TMP, 'hostd.log')
        self.st = hostd.State(dict(hostd.DEFAULT_CONFIG, totp_secret=self.secret, window_minutes=15,
                                   zippers={'zipper-0': {'tiers': ['advanced']},
                                            'zipper-1': {'tiers': ['advanced']}}))

    def ask(self, zid='zipper-0', why='check the box', cmd='true'):
        return hostd.handle(self.st, zid, {'verb': 'run', 'args': [cmd], 'why': why})

    def test_run_needs_a_reason(self):
        self.assertIn('--why', self.ask(why='')['error'])
        self.assertEqual(self.st.pending, {})

    def test_an_approval_opens_a_window_for_that_zipper_only(self):
        rid = self.ask()['pending']
        res = hostd._approve(self.st, 'zipper-0', [str(rid), hostd.totp(self.secret)[0]])
        self.assertTrue(res['ok'])
        self.assertGreater(self.st.window_left('zipper-0'), 14 * 60)
        now = self.ask(cmd='echo inside')
        self.assertTrue(now['ok'])
        self.assertNotIn('pending', now)
        self.assertIn('inside', now['output'])
        self.assertIn('pending', self.ask(zid='zipper-1'))

    def test_the_window_closes(self):
        self.st.windows['zipper-0'] = time.time() - 1
        self.assertIn('pending', self.ask())

    def test_no_window_when_turned_off(self):
        self.st.cfg['window_minutes'] = 0
        rid = self.ask()['pending']
        hostd._approve(self.st, 'zipper-0', [str(rid), hostd.totp(self.secret)[0]])
        self.assertEqual(self.st.window_left('zipper-0'), 0)
        self.assertIn('pending', self.ask())


class Schedule(unittest.TestCase):
    JOBS = [('pass', '09:00'), ('pass', '21:00'), ('digest', '19:00')]

    def due(self, now, st, pulls=False, jobs=None):
        return supervise.due(now, st, self.JOBS if jobs is None else jobs, pulls_due=pulls)

    def test_catches_up_once(self):
        now = datetime.datetime(2026, 9, 29, 9, 1)
        self.assertEqual(self.due(now, {}, pulls=True),
                         [('pull --due', 'pull'), ('pass', 'pass@09:00')])
        ran = {'pass@09:00': '2026-09-29T09:00:30'}
        self.assertEqual(self.due(now, ran), [])

    def test_missed_slot_is_skipped(self):
        now = datetime.datetime(2026, 9, 29, 12, 0)
        self.assertEqual(self.due(now, {}), [])

    def test_every_n_minutes(self):
        now = datetime.datetime(2026, 9, 29, 12, 0)
        jobs = [('update', 'every:30')]
        self.assertEqual(self.due(now, {}, jobs=jobs), [('update', 'update')])
        self.assertEqual(self.due(now, {'update': '2026-09-29T11:45:00'}, jobs=jobs), [])
        self.assertEqual(self.due(now, {'update': '2026-09-29T11:15:00'}, jobs=jobs),
                         [('update', 'update')])

    def test_nothing_to_do_without_due_pulls_or_jobs(self):
        now = datetime.datetime(2026, 9, 29, 12, 0)
        self.assertEqual(self.due(now, {}, jobs=[]), [])


class Settings(unittest.TestCase):
    def setUp(self):
        if os.path.exists(settings.PATH):
            os.remove(settings.PATH)

    def test_defaults_and_env(self):
        settings.put('ports.web', 9100)
        settings.put('plugins.hours.sheet', 'abc')
        settings.put('plugins.github.orgs', ['one', 'two'])
        env = settings.env()
        self.assertEqual(env['ZIPPER_URL'], 'http://127.0.0.1:9100')
        self.assertEqual(env['ZIPPER_SHEET_ID'], 'abc')
        self.assertEqual(env['ZIPPER_GH_ORGS'], 'one,two')
        self.assertEqual(settings.get('ports.bot'), 4200)

    def test_check_flags_mistakes(self):
        settings.save({'ports': {'web': 'eighty'}, 'colour': 'blue', 'plugins': {'slak': {}}})
        probs = ' '.join(settings.check())
        for word in ('ports.web', 'colour', 'plugins.slak'):
            self.assertIn(word, probs)

    def test_old_layout_is_translated(self):
        settings.save({'inputs': {'github': {'enabled': True, 'user': 'sam'}},
                       'peers': {'zipper-1': 'http://z1:8898'}, 'host': {'socket': '/s'},
                       'code': {'repo': 'a/b', 'auto_update': False},
                       'schedule': {'fetch_minutes': 30, 'pass': ['08:00'], 'digest': '18:00'}})
        self.assertTrue(settings.get('plugins.github.enabled'))
        self.assertEqual(settings.get('plugins.github.user'), 'sam')
        self.assertEqual(settings.get('plugins.peers.zippers'), {'zipper-1': 'http://z1:8898'})
        self.assertEqual(settings.get('plugins.passes.times'), ['08:00'])
        self.assertIs(settings.get('plugins.upstream.auto_update'), False)
        self.assertEqual(settings.get('code.repo'), 'a/b')
        self.assertIn('old layout', ' '.join(settings.check()))
        settings.migrate(os.path.join(TMP, 'no-such-env'))
        self.assertEqual(settings.check(), [])
        self.assertNotIn('inputs', settings.raw())
        self.assertEqual(settings.get('plugins.github.user'), 'sam')

    def test_an_old_files_vault_key_still_locates_the_vault(self):
        settings.save({'vault': '/some/old/vault'})
        saved = os.environ.pop('ZIPPER_VAULT')
        try:
            settings.apply()
            self.assertEqual(os.environ.get('ZIPPER_VAULT'), '/some/old/vault')
        finally:
            os.environ['ZIPPER_VAULT'] = saved
        settings.save({})

    def test_environment_outranks_settings(self):
        settings.put('owner', 'Settings')
        os.environ['ZIPPER_OWNER'] = 'Env'
        try:
            settings.apply()
            self.assertEqual(os.environ['ZIPPER_OWNER'], 'Env')
        finally:
            del os.environ['ZIPPER_OWNER']


class Plugins(unittest.TestCase):
    def setUp(self):
        if os.path.exists(settings.PATH):
            os.remove(settings.PATH)

    def test_every_manifest_is_valid_and_every_plugin_imports(self):
        ms = plugins.manifests()
        self.assertIn('dashboard', ms)
        for name, m in ms.items():
            for key in ('name', 'title', 'about', 'defaults', 'secrets', 'env'):
                self.assertIn(key, m, '%s: %s' % (name, key))
            self.assertEqual(m['name'], name)
            mod = plugins.load(name)
            self.assertEqual(mod.name, name)
            for var, key in m['env'].items():
                self.assertIn(key, m['defaults'], '%s: env %s -> %s' % (name, var, key))

    def test_only_the_dashboard_is_on_by_default(self):
        self.assertEqual(plugins.names(), ['dashboard'])

    def test_enable_and_disable(self):
        cmd = plugins.cmd_plugin

        class A:
            action, name = 'enable', 'github'
        cmd(A)
        self.assertTrue(plugins.is_enabled('github'))
        self.assertIn('github', plugins.names())
        A.action = 'disable'
        cmd(A)
        self.assertFalse(plugins.is_enabled('github'))
        A.name = 'dashboard'
        cmd(A)
        self.assertEqual(plugins.names(), [])

    def test_a_disabled_plugin_is_absent(self):
        self.assertIsNone(plugins.get('github'))
        self.assertIn('plugin enable github', plugins.require('github'))

    def test_the_digest_needs_discord(self):
        class A:
            action, name = 'enable', 'digest'
        self.assertEqual(plugins.cmd_plugin(A), 1)            # refused: discord is off
        self.assertFalse(plugins.is_enabled('digest'))
        A.name = 'discord'
        plugins.cmd_plugin(A)
        A.name = 'digest'
        self.assertEqual(plugins.cmd_plugin(A), 0)
        self.assertTrue(plugins.is_enabled('digest'))
        A.action, A.name = 'disable', 'discord'
        plugins.cmd_plugin(A)
        self.assertFalse(plugins.is_enabled('digest'))         # off with it

    def test_a_zipper_that_had_discord_keeps_it(self):
        self.assertFalse(plugins.is_enabled('discord'))
        settings.put('discord.channel', '123')
        self.assertTrue(plugins.is_enabled('discord'))
        settings.put('plugins.discord.enabled', False)
        self.assertFalse(plugins.is_enabled('discord'))

    def test_nothing_is_sent_with_discord_off(self):
        from zipper import chat
        self.assertIn('plugin is off', chat.discord_send('hello')['error'])
        self.assertEqual(chat.discord_history(), [])


class Relay(unittest.TestCase):
    def test_without_the_dashboard_only_the_relay_answers(self):
        from zipper.web import http
        http.SRV['dashboard'] = False
        try:
            self.assertFalse(http._relay_only_refuses('POST', '/discord'))
            self.assertFalse(http._relay_only_refuses('POST', '/api/inputs/canvas'))
            self.assertFalse(http._relay_only_refuses('POST', '/api/msg'))
            self.assertTrue(http._relay_only_refuses('GET', '/'))
            self.assertTrue(http._relay_only_refuses('POST', '/api/session'))
        finally:
            http.SRV['dashboard'] = True
        self.assertFalse(http._relay_only_refuses('GET', '/'))


class InitVault(unittest.TestCase):
    def test_init_substitutes_and_refuses_twice(self):
        path = os.path.join(TMP, 'v1')
        setup.init_vault(path, owner='Sam', zid='zipper-9')
        with open(os.path.join(path, 'CLAUDE.md'), encoding='utf-8') as fh:
            text = fh.read()
        self.assertIn("Sam's vault", text)
        self.assertIn('zipper-9', text)
        self.assertNotIn('{{', text)
        with self.assertRaises(RuntimeError):
            setup.init_vault(path)

    def test_a_vault_is_git_claude_md_settings_and_its_cards(self):
        path = os.path.join(TMP, 'v3')
        setup.init_vault(path)
        self.assertEqual(sorted(os.listdir(path)),
                         ['.git', '.gitignore', 'CLAUDE.md', 'Dashboard', 'settings.json'])

    def test_starter_is_optional_extra(self):
        path = os.path.join(TMP, 'v4')
        setup.init_vault(path, starter=True)
        for rel in ('CLAUDE.md', 'Tasks/Main.md', 'Meta/Schema.md', 'Projects'):
            self.assertTrue(os.path.exists(os.path.join(path, rel)), rel)

    def test_engine_output_is_not_notes(self):
        path = os.path.join(TMP, 'v2')
        os.makedirs(os.path.join(path, 'Meta'))
        os.makedirs(os.path.join(path, 'Inbox'))
        open(os.path.join(path, 'Meta', 'Status.md'), 'w').close()
        self.assertFalse(setup._has_notes(path))
        open(os.path.join(path, 'Meta', 'Mine.md'), 'w').close()
        self.assertTrue(setup._has_notes(path))


class Backup(unittest.TestCase):
    def git(self, path, *args, **env):
        return subprocess.run(['git', '-C', path] + list(args), capture_output=True, text=True,
                              env=dict(os.environ, **env)).stdout.strip()

    def use(self, vault):
        old = backup.VAULT
        backup.VAULT = vault
        self.addCleanup(setattr, backup, 'VAULT', old)

    def test_init_with_backup_pushes(self):
        vault, bare = os.path.join(TMP, 'b1'), os.path.join(TMP, 'b1.git')
        setup.init_vault(vault, owner='Sam', backup=bare)
        self.assertEqual(self.git(vault, 'remote', 'get-url', 'origin'), bare)
        self.assertEqual(self.git(bare, 'log', '-1', '--format=%s', 'main'), 'A new vault')
        self.use(vault)
        self.assertEqual(backup.flags(), [])

    def test_no_remote_is_flagged(self):
        vault = os.path.join(TMP, 'b2')
        setup.init_vault(vault)
        self.use(vault)
        self.assertIn('no backup remote', ' '.join(backup.flags()))

    def test_push_catches_up_and_stale_is_flagged(self):
        vault, bare = os.path.join(TMP, 'b3'), os.path.join(TMP, 'b3.git')
        setup.init_vault(vault, backup=bare)
        self.use(vault)
        with open(os.path.join(vault, 'Note.md'), 'w') as fh:
            fh.write('---\ntype: topic\nstatus: active\n---\nsomething\n')
        old = '2026-01-01T00:00:00'
        self.git(vault, 'add', 'Note.md')
        self.git(vault, 'commit', '-qm', 'old work', GIT_COMMITTER_DATE=old, GIT_AUTHOR_DATE=old)
        self.assertIn('1 commit(s) behind', ' '.join(backup.flags()))
        self.assertEqual(backup.push(), '')
        self.assertEqual(backup.flags(), [])
        self.assertEqual(self.git(bare, 'log', '-1', '--format=%s', 'main'), 'old work')


class Setup(unittest.TestCase):
    def test_every_plugin_has_a_setup_section_and_the_guide_has_them_all(self):
        g = setup.guide({'{{HOME}}': '/h', '{{ID}}': 'z', '{{CODE}}': '/c', '{{OWNER}}': 'Sam'})
        left = setup.remaining(g)
        for name in plugins.manifests():
            self.assertIn(name, left)
        self.assertEqual(left[:3], ['about', 'notes', 'discord'])
        self.assertEqual(left[-1:], ['finish'])
        self.assertNotIn('{{', g)

    def test_sections_come_out_one_at_a_time_then_the_guide(self):
        g = setup.guide({}) + '# the rules\n'
        with self.assertRaises(RuntimeError):
            setup.done(g)                        # not while sections remain
        for name in setup.remaining(g):
            g = setup.done(g, name)
        self.assertEqual(setup.remaining(g), [])
        self.assertEqual(setup.done(g), '# the rules\n')
        with self.assertRaises(RuntimeError):
            setup.done(g, 'discord')

    def test_init_home_makes_a_whole_zipper(self):
        home = os.path.join(TMP, 'home1')
        setup.init_home(home, owner='Sam', zid='zipper-9')
        for rel in ('vault/CLAUDE.md', 'vault/settings.json', 'vault/Dashboard/setup.md',
                    'config/.env', 'backup/vault.git/HEAD', 'compose.yml', 'zipper'):
            self.assertTrue(os.path.exists(os.path.join(home, rel)), rel)
        self.assertEqual(oct(os.stat(os.path.join(home, 'config/.env')).st_mode & 0o777), '0o600')
        self.assertTrue(os.access(os.path.join(home, 'zipper'), os.X_OK))
        with open(os.path.join(home, 'vault/CLAUDE.md'), encoding='utf-8') as fh:
            self.assertIn('<!-- setup:discord -->', fh.read())
        with open(os.path.join(home, 'vault/settings.json'), encoding='utf-8') as fh:
            cfg = json.load(fh)
        self.assertEqual(cfg['id'], 'zipper-9')
        self.assertTrue(cfg['plugins']['backup']['enabled'])
        with self.assertRaises(RuntimeError):
            setup.init_home(home)

    def test_the_guide_asks_about_the_notes_and_names_the_plugins_apart(self):
        home = os.path.join(TMP, 'home-guide')
        setup.init_home(home, owner='Sam', zid='zipper-7')
        with open(os.path.join(home, 'vault/CLAUDE.md'), encoding='utf-8') as fh:
            text = fh.read()
        self.assertEqual(setup.remaining(text)[:2], ['about', 'notes'])
        self.assertIn('Two things are called "plugins"', text)
        with open(os.path.join(home, 'vault/settings.json'), encoding='utf-8') as fh:
            self.assertTrue(json.load(fh).get('timezone'))

    def test_init_publishes_the_dashboard_with_a_password(self):
        home = os.path.join(TMP, 'home-dash')
        setup.init_home(home, zid='zipper-6', addr='100.64.0.9', port=8905, cred='zipper:pw')
        with open(os.path.join(home, 'compose.yml'), encoding='utf-8') as fh:
            self.assertIn('"100.64.0.9:8905:8899"', fh.read())
        with open(os.path.join(home, 'config', '.env'), encoding='utf-8') as fh:
            env = fh.read()
        self.assertIn('ZIPPER_TERM_CRED=zipper:pw', env)
        self.assertIn('ZIPPER_DASHBOARD_URL=http://100.64.0.9:8905', env)

    def test_over_ssh_without_tailscale_init_makes_nothing(self):
        from zipper import install
        home = os.path.join(TMP, 'home-ssh')
        saved = install.remote, install.tailnet_address
        install.remote, install.tailnet_address = (lambda: True), (lambda: '')
        try:
            class A:
                path, owner, id, starter = home, 'Sam', 'zipper-4', False
                vault_only = local = no_start = False
                address = None
            self.assertEqual(setup.cmd_init(A), 1)
        finally:
            install.remote, install.tailnet_address = saved
        self.assertFalse(os.path.exists(home))

    def test_a_zipper_is_named_after_its_folder(self):
        self.assertEqual(setup.default_id('/opt/zippers/zipper-1'), 'zipper-1')
        self.assertEqual(setup.default_id('~/Quinn Z'), 'quinn-z')

    def test_secret_links_go_through_the_dashboard_in_a_container(self):
        from zipper import secret
        self.assertEqual(secret.dashboard_url(), '')
        setup.env_set('ZIPPER_DASHBOARD_URL', 'http://100.64.0.9:8905/')
        os.environ['ZIPPER_NGINX'] = '1'
        try:
            self.assertEqual(secret.dashboard_url(), 'http://100.64.0.9:8905')
        finally:
            del os.environ['ZIPPER_NGINX']

    def test_free_port_skips_a_taken_one(self):
        import socket
        from zipper import install
        s = socket.socket()
        s.bind(('127.0.0.1', 0))
        s.listen()
        taken = s.getsockname()[1]
        try:
            self.assertNotEqual(install.free_port('127.0.0.1', taken), taken)
        finally:
            s.close()

    def test_starter_never_overwrites(self):
        vault = os.path.join(TMP, 'starter-vault')
        os.makedirs(os.path.join(vault, 'Tasks'))
        with open(os.path.join(vault, 'Tasks', 'Main.md'), 'w', encoding='utf-8') as fh:
            fh.write('mine\n')
        added = setup.add_starter(vault)
        self.assertIn('Projects/', added)
        self.assertNotIn(os.path.join('Tasks', 'Main.md'), added)
        with open(os.path.join(vault, 'Tasks', 'Main.md'), encoding='utf-8') as fh:
            self.assertEqual(fh.read(), 'mine\n')
        self.assertEqual(setup.add_starter(vault), [])

    def test_compose_carries_the_hosts_timezone(self):
        home = os.path.join(TMP, 'home-tz')
        old = os.environ.get('TZ')
        os.environ['TZ'] = 'America/Phoenix'
        try:
            setup.init_home(home, zid='zipper-8')
        finally:
            if old is None:
                os.environ.pop('TZ', None)
            else:
                os.environ['TZ'] = old
        with open(os.path.join(home, 'compose.yml'), encoding='utf-8') as fh:
            text = fh.read()
        self.assertIn('TZ: America/Phoenix', text)
        self.assertNotIn('{{', text)


class Secret(unittest.TestCase):
    def test_the_page_saves_once_and_closes(self):
        import threading, urllib.request, urllib.parse
        from zipper import secret
        port, token = secret._free_port('127.0.0.1'), 'tok'
        t = threading.Thread(target=secret.serve,
                             args=('UNIT_SECRET', '127.0.0.1', port, token, 20), daemon=True)
        t.start()
        time.sleep(0.3)
        base = 'http://127.0.0.1:%d/' % port
        with self.assertRaises(Exception):
            urllib.request.urlopen(base + 'wrong', timeout=5)
        body = urllib.parse.urlencode({'value': 's3cret'}).encode()
        with urllib.request.urlopen(base + token, data=body, timeout=5) as r:
            self.assertIn(b'Saved', r.read())
        t.join(5)
        self.assertFalse(t.is_alive())
        from zipper import core
        self.assertEqual(core._env_file().get('UNIT_SECRET'), 's3cret')

    def test_names_are_checked(self):
        from zipper import secret
        self.assertTrue(secret.valid_name('DISCORD_TOKEN'))
        for bad in ('bad name', '1ABC', 'a;rm', ''):
            self.assertFalse(secret.valid_name(bad))


class QueueRows(unittest.TestCase):
    def test_every_system_has_a_label(self):
        for s in runqueue.SYSTEM_ORDER:
            self.assertIn(s, runqueue.SYSTEM_LABEL)

    def test_peer_message_row(self):
        rows = peers.events({}, {'m1': ['2026-09-29T20:29', 'zipper-a', 'hello\nthere']})
        self.assertEqual(rows[0]['system'], 'zipper')
        self.assertEqual(rows[0]['who'], 'zipper-a')
        self.assertNotIn('\n', rows[0]['text'])

    def test_upstream_rows_only_for_new_commits(self):
        before = {'a' * 40: ['2026-09-29T20:00', 'dev', 'old']}
        after = dict(before, **{'b' * 40: ['2026-09-29T21:00', 'dev', 'new']})
        rows = upstream.events(before, after)
        self.assertEqual(len(rows), 1)
        self.assertIn('new', rows[0]['text'])


class Presence(unittest.TestCase):
    """The bot's status line: the newest busy conversation, plus a count."""

    def text(self, busy):
        real = presence.working
        presence.working = lambda: busy
        try:
            return presence.text()
        finally:
            presence.working = real

    def test_idle(self):
        self.assertEqual(self.text([]), 'Waiting')

    def test_one(self):
        self.assertEqual(self.text([('1', 'Pantry pricing')]), 'Pantry pricing')

    def test_several(self):
        self.assertEqual(self.text([('1', 'Pantry pricing'), ('2', 'x'), ('3', 'y')]),
                         'Pantry pricing (+2)')

    def test_long_title_is_cut(self):
        name = presence._name('none', {'title': 'see how it shows my activity, is there a way to set'})
        self.assertLessEqual(len(name), presence.TITLE_MAX)
        self.assertTrue(name.endswith('\u2026'))

    def test_no_name(self):
        self.assertEqual(presence._name('none', {}), 'a conversation')


class CommandLine(unittest.TestCase):
    """The CLI end to end on a fresh vault, in a child with the same empty env."""

    def run_zipper(self, *args):
        return subprocess.run([sys.executable, '-m', 'zipper'] + list(args), cwd=ROOT,
                              capture_output=True, text=True, env=dict(os.environ))

    def test_init_lint_status(self):
        vault = os.environ['ZIPPER_VAULT']
        if not os.path.exists(os.path.join(vault, 'CLAUDE.md')):
            r = self.run_zipper('init', '--vault-only', vault)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for cmd in (['lint'], ['status'], ['agenda'], ['views'], ['settings', 'check']):
            r = self.run_zipper(*cmd)
            self.assertEqual(r.returncode, 0, '%s: %s' % (cmd, r.stdout + r.stderr))


if __name__ == '__main__':
    unittest.main()
