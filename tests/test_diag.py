import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from tes3x_diag import PATCH_BITS, assertion_failures, parse_log, report, sessions
from tes3x_patch import PATCH_BITS as PATCHER_BITS


class DiagnosticsTests(unittest.TestCase):
    def test_patch_bits_agree_with_the_patcher(self):
        """A mask the patcher writes has to decode back to the names it was built from."""
        self.assertEqual({bit: name for name, bit in PATCHER_BITS.items()},
                         {1 << bit: name for bit, name in PATCH_BITS.items()})

    def test_high_patch_mask_word_is_decoded(self):
        old = dict(PATCH_BITS)
        try:
            PATCH_BITS[32] = "net"
            records = parse_log(
                "1 ms diag.patches 0x00000040\n"
                "2 ms diag.patches_hi 0x00000001\n")
            out = io.StringIO()
            report(records, stream=out)
            self.assertIn("diagnostics, net (0x0000000100000040)", out.getvalue())
        finally:
            PATCH_BITS.clear()
            PATCH_BITS.update(old)

    def test_sessions_start_at_xbe_entry(self):
        records = parse_log(
            "0 ms entry.free_kb 59820\r\n"
            "1 ms diag.session 0x12345678\r\n"
            "0 ms entry.free_kb 59812\r\n"
            "1 ms diag.session 0x87654321\r\n")
        runs = sessions(records)
        self.assertEqual(len(runs), 2)
        self.assertEqual(runs[1][1]["value"], 0x87654321)

    def test_blank_lines_are_not_records(self):
        records = parse_log(
            "0 ms entry.free_kb 59820\r\n\r\n"
            "1 ms diag.session 0x12345678\r\n\n")
        self.assertEqual(len(records), 2)
        self.assertEqual([r["tag"] for r in records],
                         ["entry.free_kb", "diag.session"])

    def test_crash_report_decodes_access_violation(self):
        records = parse_log(
            "0 ms entry.free_kb 59820\n"
            "1 ms diag.build 0x12345678\n"
            "2 ms diag.patches 0x000000C0\n"
            "3 ms diag.enabled 1\n"
            "20 ms crash.code 0xC0000005\n"
            "21 ms crash.eip 0x00123456\n"
            "22 ms crash.info0 1\n"
            "23 ms crash.info1 0xDEADBEEF\n")
        out = io.StringIO()
        report(records, stream=out)
        text = out.getvalue()
        self.assertIn("access violation", text)
        self.assertIn("write at 0xDEADBEEF", text)
        self.assertIn("console, diagnostics", text)

    def test_assertions_report_failures_and_missing_runs(self):
        script = "assert a == 1\nassert b == 2\nexit\n"
        log = ("assert> a == 1\n10 ms assert.pass 1\n"
               "assert> b == 2\n11 ms assert.fail 2\nassert.got 3\n"
               "12 ms assert.total 2\n13 ms assert.failed 1\n")
        self.assertEqual(assertion_failures(log, script), ["assert 2: b == 2 (got 3)"])
        self.assertEqual(assertion_failures("assert> a == 1\n10 ms assert.pass 1\n", script),
                         ["no assertions ran of 2"])
        self.assertEqual(assertion_failures("", "exit\n"), [])


if __name__ == "__main__":
    unittest.main()
