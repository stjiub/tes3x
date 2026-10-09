import ast
import contextlib
import io
import subprocess
import sys
import unittest
from pathlib import Path

import tes3x.cli as cli

PACKAGE = Path(__file__).resolve().parents[1] / 'src' / 'tes3x'


class CommandTests(unittest.TestCase):
    def test_every_module_with_a_command_line_is_a_command(self):
        found = {path.stem for path in PACKAGE.glob('*.py') if path.stem != 'cli'
                 and any(isinstance(node, ast.If) and '__name__' in ast.unparse(node.test)
                         for node in ast.parse(path.read_text(encoding='utf-8')).body)}
        found.update(path.parent.name for path in PACKAGE.glob('*/__main__.py'))
        self.assertEqual(set(cli.COMMANDS), found)

    def test_help_lists_each_command_with_its_summary(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(['--help']), 0)
        lines = out.getvalue().splitlines()
        for name in cli.COMMANDS:
            line = next(l for l in lines if l.split()[:1] == [name.replace('_', '-')])
            self.assertGreater(len(line.split()), 1, name)

    def test_an_unknown_command_is_refused(self):
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(cli.main(['no-such-command']), 2)
        self.assertIn("no command 'no-such-command'", err.getvalue())

    def test_a_command_runs_under_its_own_name(self):
        done = subprocess.run([sys.executable, '-m', 'tes3x', 'net', '--help'],
                              capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(done.stdout.startswith('usage: tes3x net '), done.stdout[:80])
        done = subprocess.run([sys.executable, '-m', 'tes3x', 'xemu-setup', '--help'],
                              capture_output=True, text=True)
        self.assertTrue(done.stdout.startswith('usage: tes3x xemu-setup'), done.stdout[:80])


if __name__ == '__main__':
    unittest.main()
