"""Save comparisons distinguish restored statistics from other mobile state."""

import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import tes3x_ess as ess


class AttributeDiffTest(unittest.TestCase):
    def save(self, folder, name, acdt):
        body = b'ACDT' + struct.pack('<I', len(acdt)) + acdt
        path = Path(folder) / name
        path.write_bytes(b'REFR' + struct.pack('<III', len(body), 0, 0) + body)
        return path

    def test_current_attribute_difference(self):
        reference = bytearray(0x108)
        struct.pack_into('<ff', reference, 0x50, 43.0, 50.0)
        rebuilt = bytearray(reference)
        struct.pack_into('<f', rebuilt, 0x50, 50.0)
        with tempfile.TemporaryDirectory() as folder:
            a = self.save(folder, 'reference.ess', reference)
            b = self.save(folder, 'rebuilt.ess', rebuilt)
            differences, units, _ = ess.diff(a, b)
        row = 'Attributes (base, current)'
        self.assertEqual(units[row, 'Strength'], (50.0, 43.0))
        self.assertEqual(len([key for key in units if key[0] == row]), 8)
        self.assertEqual(differences[row], ['changed   Strength: (50.0, 43.0) -> (50.0, 50.0)'])
        self.assertFalse(differences['Player mobile (current stats, undecoded)'])

    def test_other_mobile_bytes_remain_compared(self):
        reference = bytearray(0x108)
        rebuilt = bytearray(reference)
        rebuilt[0x94] = 1
        with tempfile.TemporaryDirectory() as folder:
            differences, _, _ = ess.diff(self.save(folder, 'a.ess', reference),
                                         self.save(folder, 'b.ess', rebuilt))
        self.assertFalse(differences['Attributes (base, current)'])
        self.assertEqual(len(differences['Player mobile (current stats, undecoded)']), 1)

    def test_unknown_layout_remains_opaque(self):
        with tempfile.TemporaryDirectory() as folder:
            units = ess.save_units(self.save(folder, 'short.ess', bytes(0x50)))
        self.assertNotIn(('Attributes (base, current)', 'Strength'), units)
        self.assertEqual(units['Player mobile (current stats, undecoded)', 'REFR ACDT'],
                         ess.blob(bytes(0x50)))


if __name__ == '__main__':
    unittest.main()
