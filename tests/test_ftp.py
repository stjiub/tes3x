import argparse
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import tes3x_ftp


class FtpSettingsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.config = Path(temp.name) / 'local.toml'
        self.config.write_text('[deploy]\nhost = "192.0.2.1"\nuser = "me"\npassword = "file"\n',
                               encoding='utf-8')

    def resolve(self, argv, environ=None):
        parser = argparse.ArgumentParser()
        tes3x_ftp.add_arguments(parser)
        return tes3x_ftp.resolve(parser.parse_args(argv + ['--config', str(self.config)]),
                                 environ or {})

    def test_config_fills_what_the_command_line_leaves_out(self):
        args = self.resolve([])
        self.assertEqual((args.host, args.port, args.user, args.password),
                         ('192.0.2.1', 21, 'me', 'file'))

    def test_command_line_then_environment_win(self):
        self.assertEqual(self.resolve(['--password', 'flag']).password, 'flag')
        self.assertEqual(self.resolve([], {tes3x_ftp.PASSWORD_ENV: 'env'}).password, 'env')
        self.assertEqual(self.resolve(['--host', '192.0.2.9']).host, '192.0.2.9')

    def test_dashboard_default_login(self):
        self.config.write_text('[deploy]\nhost = "192.0.2.1"\n', encoding='utf-8')
        args = self.resolve([])
        self.assertEqual((args.user, args.password), ('xbox', 'xbox'))


if __name__ == '__main__':
    unittest.main()
