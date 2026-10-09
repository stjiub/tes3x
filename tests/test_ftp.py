import argparse
import tempfile
import unittest
from pathlib import Path

import tes3x.ftp as tes3x_ftp


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
        self.config.write_text(self.config.read_text(encoding='utf-8') +
                               'agent_token = "' + 'a' * 64 + '"\n', encoding='utf-8')
        args = self.resolve([])
        self.assertEqual((args.host, args.port, args.user, args.password),
                         ('192.0.2.1', 21, 'me', 'file'))
        self.assertEqual(args.agent_token, 'a' * 64)

    def test_command_line_then_environment_win(self):
        self.assertEqual(self.resolve(['--password', 'flag']).password, 'flag')
        self.assertEqual(self.resolve([], {tes3x_ftp.PASSWORD_ENV: 'env'}).password, 'env')
        self.assertEqual(self.resolve(['--host', '192.0.2.9']).host, '192.0.2.9')

    def test_dashboard_default_login(self):
        self.config.write_text('[deploy]\nhost = "192.0.2.1"\n', encoding='utf-8')
        args = self.resolve([])
        self.assertEqual((args.user, args.password), ('xbox', 'xbox'))

    def test_named_target_supplies_connection(self):
        self.config.write_text(
            'default_target = "bench"\n[targets.bench]\nkind = "xbox"\n'
            'host = "192.0.2.2"\ngames_root = "F:/Games"\nuser = "target-user"\n'
            '[targets.other]\nkind = "xbox"\nhost = "192.0.2.3"\n'
            'games_root = "E:/Games"\n', encoding='utf-8')
        self.assertEqual(self.resolve([]).host, '192.0.2.2')
        other = self.resolve(['--target', 'other'])
        self.assertEqual((other.target, other.host), ('other', '192.0.2.3'))


if __name__ == '__main__':
    unittest.main()
