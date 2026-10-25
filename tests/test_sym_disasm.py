import shutil
import unittest.mock
import struct
import tempfile
import unittest
from pathlib import Path

import tes3x.disasm as disasm
import tes3x.inject as tes3x_inject
import tes3x.sym as sym

BASE = 0x10000
TEXT_VA = 0x11000
DATA_VA = 0x12000

# 0x11000: helper (returns), 0x11010: caller (call helper; jmp helper), 0x11020: pushes helper
CODE = bytearray([0xCC]) * 0x40
CODE[0x00:0x01] = b"\xC3"
CODE[0x10:0x15] = b"\xE8" + struct.pack("<i", TEXT_VA - (TEXT_VA + 0x10 + 5))
CODE[0x15:0x1A] = b"\xE9" + struct.pack("<i", TEXT_VA - (TEXT_VA + 0x15 + 5))
CODE[0x1A:0x1B] = b"\xC3"
CODE[0x20:0x25] = b"\x68" + struct.pack("<I", TEXT_VA + 0x10)
CODE[0x25:0x26] = b"\xC3"


def synthetic_xbe():
    """A minimal XBE: a code section and a data section holding a vtable and a string."""
    data = bytearray(0x3000)
    data[0:4] = b"XBEH"
    names = BASE + 0x300
    data[0x300:0x300 + 14] = b".text\0.rdata\0"
    struct.pack_into("<I", data, 0x104, BASE)
    struct.pack_into("<I", data, 0x108, 0x1000)
    struct.pack_into("<I", data, 0x11C, 2)
    struct.pack_into("<I", data, 0x120, BASE + 0x200)
    struct.pack_into("<I", data, 0x128, TEXT_VA ^ tes3x_inject.ENTRY_XOR["retail"])
    struct.pack_into("<I", data, 0x158, DATA_VA ^ tes3x_inject.THUNK_XOR["retail"])
    for i, (flags, va, raw, name) in enumerate(
            ((0x4, TEXT_VA, 0x1000, names), (0x1, DATA_VA, 0x2000, names + 6))):
        struct.pack_into("<9I", data, 0x200 + i * 56, flags, va, 0x1000, raw, 0x1000,
                         name, 0, 0, 0)
    data[0x1000:0x1000 + len(CODE)] = CODE
    for i, target in enumerate((TEXT_VA, TEXT_VA + 0x10, TEXT_VA + 0x20)):
        struct.pack_into("<I", data, 0x2000 + i * 4, target)
    data[0x2100:0x2100 + 12] = b"hello world\0"
    return bytes(data)


class SyntheticXbeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "default.xbe"
        self.path.write_bytes(synthetic_xbe())
        self.xbe = tes3x_inject.Xbe(self.path.read_bytes())
        self.addCleanup(self.tmp.cleanup)


class DisasmTests(SyntheticXbeTests):
    def test_offsets_and_addresses_convert_both_ways(self):
        self.assertEqual(disasm.off_to_va(self.xbe, 0x1010), TEXT_VA + 0x10)
        self.assertEqual(disasm.va_to_off(self.xbe, DATA_VA + 4), 0x2004)
        self.assertIsNone(disasm.off_to_va(self.xbe, 0x2F000))
        self.assertIsNone(disasm.va_to_off(self.xbe, 0x90000))

    def test_xrefs_find_direct_calls_and_jumps_in_code_only(self):
        found = disasm.xrefs(self.xbe, TEXT_VA)
        self.assertEqual(sorted(found), [(TEXT_VA + 0x10, ".text call"),
                                         (TEXT_VA + 0x15, ".text jmp")])

    def test_xrefs_find_absolute_immediates(self):
        self.assertEqual(disasm.xrefs(self.xbe, TEXT_VA + 0x10),
                         [(TEXT_VA + 0x20, ".text")])

    def test_target_uses_the_symbol_name(self):
        with unittest.mock.patch.dict(disasm.NAMES, {TEXT_VA: "Helper"}, clear=True):
            self.assertEqual(disasm.target(TEXT_VA), "0x00011000 <Helper>")
            self.assertEqual(disasm.target(TEXT_VA + 1), "0x00011001")

    @unittest.skipUnless(shutil.which(disasm.OBJDUMP) and shutil.which(disasm.OBJCOPY),
                         "needs llvm-objdump and llvm-objcopy")
    def test_disasm_prints_real_addresses(self):
        import contextlib
        import io
        out = io.StringIO()
        with unittest.mock.patch.dict(disasm.NAMES, {TEXT_VA: "Helper"}, clear=True), \
                contextlib.redirect_stdout(out):
            disasm.disasm(self.xbe, TEXT_VA + 0x10, 6)
        self.assertIn("0x00011010", out.getvalue())
        self.assertIn("call", out.getvalue())
        self.assertIn("0x00011000 <Helper>", out.getvalue())

    def test_unmapped_address_is_refused(self):
        with self.assertRaises(SystemExit):
            disasm.disasm(self.xbe, 0x90000, 4)


class SymScanTests(SyntheticXbeTests):
    def setUp(self):
        super().setUp()
        self.img = sym.Image(self.path, "xbe")
        self.code = self.img.data[0x1000:0x2000]

    def test_image_reads_the_xbe_layout(self):
        self.assertEqual(self.img.base, BASE)
        self.assertEqual(self.img.entry, TEXT_VA)
        self.assertEqual(self.img.va_to_off(DATA_VA + 0x100), 0x2100)
        self.assertEqual(self.img.read(DATA_VA + 0x100, 5), b"hello")
        self.assertEqual(self.img.analysis_range(".text")[:2], (TEXT_VA, 0x1000))
        with self.assertRaises(SystemExit):
            self.img.analysis_range(".nope")

    def test_image_refuses_other_files(self):
        other = Path(self.tmp.name) / "other.bin"
        other.write_bytes(b"not an executable")
        with self.assertRaises(SystemExit):
            sym.Image(other, "x")

    def test_scan_targets_splits_calls_and_jumps(self):
        calls, jumps = sym.scan_targets(self.code, TEXT_VA, TEXT_VA, TEXT_VA + 0x1000)
        self.assertIn((TEXT_VA + 0x10, TEXT_VA), calls)
        self.assertIn((TEXT_VA + 0x15, TEXT_VA), jumps)

    def test_scan_targets_ignores_targets_outside_the_range(self):
        calls, jumps = sym.scan_targets(self.code, TEXT_VA, TEXT_VA + 0x100, TEXT_VA + 0x200)
        self.assertEqual((calls, jumps), ([], []))

    def test_scan_vtables_finds_a_run_in_data(self):
        tables = sym.scan_vtables(self.img, TEXT_VA, TEXT_VA + 0x1000)
        self.assertEqual(tables, [(DATA_VA, [TEXT_VA, TEXT_VA + 0x10, TEXT_VA + 0x20])])
        self.assertEqual(sym.scan_vtables(self.img, TEXT_VA, TEXT_VA + 0x1000, minrun=4), [])

    def test_scan_pointers_returns_the_table_entries(self):
        self.assertEqual(sym.scan_pointers(self.img, TEXT_VA, TEXT_VA + 0x1000),
                         {TEXT_VA, TEXT_VA + 0x10, TEXT_VA + 0x20})

    def test_scan_immediates_finds_a_pushed_function(self):
        self.assertEqual(sym.scan_immediates(self.code, TEXT_VA, TEXT_VA, TEXT_VA + 0x1000),
                         {TEXT_VA + 0x10})

    def test_find_strings_reports_virtual_addresses(self):
        self.assertEqual(sym.find_strings(self.img)[DATA_VA + 0x100], "hello world")

    def test_preceded_ok_wants_a_terminator_or_padding(self):
        self.assertTrue(sym.preceded_ok(self.code, TEXT_VA, TEXT_VA + 0x10))  # after int3
        self.assertTrue(sym.preceded_ok(self.code, TEXT_VA, TEXT_VA + 1))  # after ret
        self.assertFalse(sym.preceded_ok(self.code, TEXT_VA, TEXT_VA + 0x11))  # inside a call

    def test_schema_creates_the_tables_once(self):
        import sqlite3
        db = sqlite3.connect(":memory:")
        sym.schema(db)
        sym.schema(db)
        tables = {r[0] for r in db.execute("select name from sqlite_master where type='table'")}
        self.assertTrue({"image", "func", "edge", "str", "match", "vtab"} <= tables)


if __name__ == "__main__":
    unittest.main()
