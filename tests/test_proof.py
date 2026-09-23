import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import tes3x_proof as proof

MCP102_BIT = 1 << 12


def log(mask, loaded):
    return (f'0 ms entry.free_kb 59800\n16 ms diag.session 0x1\n18 ms diag.build 0x12345678\n'
            f'20 ms diag.patches 0x{mask:08X}\n25570 ms mcp102.loaded {loaded}\n')


class ProofTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for name, value in (('ROOT', self.root), ('PATCH_DIRS', self.root / 'patches')):
            patcher = patch.object(proof, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def record(self, control_mask, patched_mask):
        (self.root / 'control.txt').write_text(log(control_mask, 0))
        (self.root / 'patched.txt').write_text(log(patched_mask, 1))
        proof.main(['record', 'mcp-102', '--env', 'xemu', '--platform', 'xemu test',
                    '--control', str(self.root / 'control.txt'),
                    '--patched', str(self.root / 'patched.txt'),
                    '--watch', r'mcp102\.loaded', '--claim', 'c', '--method', 'm',
                    '--date', '2026-09-23'])
        return self.root / 'patches' / 'mcp-102' / '2026-09-23-xemu.toml'

    def test_record_keeps_logs_masks_and_observed_lines(self):
        path = self.record(0x5, 0x5 | MCP102_BIT)
        record = proof.load_record(path)
        control, patched = record['run']
        self.assertEqual(patched['patches'], '0x00001005')
        self.assertEqual(patched['build'], '0x12345678')
        self.assertEqual(control['observed'], ['25570 ms mcp102.loaded 0'])
        self.assertTrue((path.parent / patched['log']).is_file())

    def test_patched_run_must_carry_the_patch(self):
        with self.assertRaises(SystemExit):
            self.record(0x5, 0x5)
        self.assertFalse(list((self.root / 'patches').rglob('*.toml')))

    def test_edited_log_breaks_the_record(self):
        path = self.record(0x5, 0x5 | MCP102_BIT)
        (path.parent / '2026-09-23-xemu-patched.log').write_text('edited')
        with self.assertRaises(proof.ProofError):
            proof.load_record(path)


class RepositoryRecordTests(unittest.TestCase):
    def test_records_and_table_evidence_agree(self):
        self.assertEqual(proof.check_all(), [])


if __name__ == '__main__':
    unittest.main()
