import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from tes3x_mwse import (
    COMMANDS, RUNTIME_COMMANDS, RUNTIME_VM_OPS, VM_OPS, corroborated_instructions,
    opcode_info, scan_bytecode, source_command_names,
)


class MwseAuditTests(unittest.TestCase):
    def test_catalogue_covers_legacy_range(self):
        self.assertEqual(COMMANDS[0x3C07], 'XGetPCTarget')
        self.assertEqual(COMMANDS[0x3E61], 'XSetValue')
        self.assertEqual(COMMANDS[0x3FA1], 'XRemoveSpell')
        self.assertEqual(opcode_info(0x3801), ('Call', 4, 'vm'))
        self.assertIsNone(opcode_info(0x4000))

    def test_operands_are_not_reported_as_opcodes(self):
        data = (
            struct.pack('<Hh', 0x3813, 0x3C07)
            + struct.pack('<H', 0x3C07)
            + struct.pack('<Hh', 0x380A, 0x3811)
        )
        found = scan_bytecode(data)
        self.assertEqual(
            [(i['opcode'], i['operand']) for i in found],
            [(0x3813, '073c'), (0x3C07, ''), (0x380A, '1138')],
        )

    def test_unknown_bytes_between_sequences_are_skipped(self):
        data = b'vanilla' + struct.pack('<H', 0x3F61) + b'\0x' + struct.pack('<H', 0x3E61)
        found = scan_bytecode(data)
        self.assertEqual([(i['offset'], i['name']) for i in found],
                         [(7, 'XGetValue'), (11, 'XSetValue')])

    def test_every_vm_operand_width_is_bounded(self):
        self.assertTrue(VM_OPS)
        self.assertTrue(all(0 <= width <= 4 for _name, width in VM_OPS.values()))

    def test_runtime_covers_the_complete_catalogue(self):
        self.assertEqual(RUNTIME_VM_OPS, frozenset(VM_OPS))
        self.assertEqual(RUNTIME_COMMANDS, frozenset(COMMANDS))
        source = (Path(__file__).resolve().parents[1] / 'hooks' / 'tes3xmwse.c').read_text()
        missing = [opcode for opcode in COMMANDS if 'case 0x%04X:' % opcode not in source]
        self.assertEqual(missing, [])

    def test_source_commands_are_exact_and_case_insensitive(self):
        source = (b'setx value to target->xgetvalue\n'
                  b'; xGetOwner is only a comment\n'
                  b'MessageBox "xSetValue"\n')
        self.assertEqual(source_command_names(source), {'XGetValue'})

    def test_corroboration_drops_incidental_command_and_lone_vm_pairs(self):
        data = (
            struct.pack('<H', 0x3F66) + b'ordinary text'
            + struct.pack('<H', 0x3813)
            + b'gap'
            + struct.pack('<HhHH', 0x3813, 2, 0x3C00, 0x3F61)
        )
        found = corroborated_instructions(data, b'setx value to xGetValue')
        self.assertEqual([i['name'] for i in found], ['PushS', 'GetLocal', 'XGetValue'])


if __name__ == '__main__':
    unittest.main()
