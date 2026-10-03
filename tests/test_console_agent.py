import importlib.util
import ftplib
import tempfile
import tomllib
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeConnection:
    def __init__(self, request):
        self.chunks = [request.encode('latin-1'), b'']
        self.sent = b''

    def settimeout(self, _timeout):
        pass

    def recv(self, _size):
        return self.chunks.pop(0)

    def sendall(self, data):
        self.sent += data


class FakeDashboardFtp:
    def cwd(self, path):
        if path != 'C:/skins/Profile/xml':
            raise ftplib.error_perm('missing')

    def retrbinary(self, _command, callback):
        callback(b'<window/>')


class DashboardAgentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.body = load('tes3x_agent_body', ROOT / 'addons' / 'console' / 'agent_body.py')

    def test_only_private_lan_peers_are_allowed(self):
        allowed = ('10.0.0.1', '172.16.0.1', '172.31.255.255', '192.168.1.2')
        refused = ('127.0.0.1', '172.15.0.1', '172.32.0.1', '192.169.1.2', '203.0.113.1')
        self.assertTrue(all(self.body.private_peer(value) for value in allowed))
        self.assertFalse(any(self.body.private_peer(value) for value in refused))

    def test_token_is_required_and_checked(self):
        token = 'a' * 64
        self.body.TOKEN = token
        connection = FakeConnection(token + ' ping\n')
        self.assertIsNone(self.body.serve_one(connection))
        self.assertTrue(connection.sent.startswith(b'ok tes3xagent 6 '))

        connection = FakeConnection('b' * 64 + ' ping\n')
        self.assertIsNone(self.body.serve_one(connection))
        self.assertEqual(connection.sent, b'err unauthorized\n')

    def test_builtin_is_narrow_and_reload_is_explicit(self):
        self.assertEqual(self.body.handle('builtin Notification(test)')[0], 'ok')
        self.assertEqual(self.body.handle('builtin XBMC.ActivateWindow(Home)')[0], 'ok')
        self.assertTrue(self.body.handle('builtin RunScript(anything)')[0].startswith('err'))
        self.assertEqual(self.body.handle('reload'), ('ok', self.body.RELOAD))
        self.assertEqual(self.body.handle('restart'), ('ok', 'XBMC.RestartApp'))

    def test_install_token_is_kept_in_the_selected_target(self):
        console = load('tes3x_console_client', ROOT / 'addons' / 'console' / 'console.py')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'local.toml'
            path.write_text('[deploy]\nhost = "192.0.2.50"\n# keep this\n', encoding='utf-8')
            args = SimpleNamespace(config=str(path), target='xbox', agent_token=None)
            console.save_token(args, 'c' * 64)
            self.assertEqual(tomllib.loads(path.read_text(encoding='utf-8'))['deploy']
                             ['agent_token'], 'c' * 64)
            self.assertIn('# keep this', path.read_text(encoding='utf-8'))
            console.save_token(args, 'd' * 64)
            text = path.read_text(encoding='utf-8')
            self.assertEqual(text.count('agent_token'), 1)
            self.assertEqual(tomllib.loads(text)['deploy']['agent_token'], 'd' * 64)

    def test_dashboard_setting_prefers_the_selected_target(self):
        console = load('tes3x_console_settings', ROOT / 'addons' / 'console' / 'console.py')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'local.toml'
            path.write_text(
                '[console]\ndashboard = "F:/XBMC4Gamers"\n'
                '[targets.bench]\nkind = "xbox"\ngames_root = "F:/Games"\n'
                'dashboard = "C:"\n', encoding='utf-8')
            self.assertEqual(console.dashboard_setting(path, 'bench'), 'C:')

    def test_dashboard_detection_checks_before_creating_files(self):
        console = load('tes3x_console_detect', ROOT / 'addons' / 'console' / 'console.py')
        self.assertEqual(console.detect_dashboard(FakeDashboardFtp()), 'C:')


if __name__ == '__main__':
    unittest.main()
