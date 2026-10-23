import json
import unittest

from tes3x import script_commands


class ScriptCommandsTest(unittest.TestCase):
    def test_injected_commands_match_the_hook_source(self):
        data = json.loads(script_commands.COMMANDS.read_text(encoding='utf-8'))
        listed = [(int(e['opcode'], 16), e['name']) for e in data['injected']]
        self.assertEqual(listed, script_commands.injected())

    def test_names_resolve_case_insensitively(self):
        self.assertEqual(script_commands.opcode('Begin'), 0x0100)
        self.assertEqual(script_commands.opcode('getpos'), 0x100A)
        self.assertEqual(script_commands.opcode('TES3XGETVERSION'), 0x2001)

    def test_long_name_wins_over_a_short_name(self):
        table = script_commands.load()
        data = json.loads(script_commands.COMMANDS.read_text(encoding='utf-8'))
        for entry in data['retail']:
            self.assertEqual(table[entry['name'].lower()], int(entry['opcode'], 16))

    def test_unknown_name_fails(self):
        with self.assertRaises(SystemExit):
            script_commands.opcode('NoSuchCommand')


if __name__ == '__main__':
    unittest.main()
