import contextlib
import io
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import tes3x.console_signatures as console_signatures
import tes3x.inject as tes3x_inject
import tes3x.multi_signatures as multi_signatures
import tes3x.prof as prof
import tes3x.readlog as readlog
import tes3x.scriptasm as scriptasm
from tes3x.records import records, subrecords
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_sym_disasm import TEXT_VA, synthetic_xbe

TEXT_RAW = 0x1000
ENTRY = bytes.fromhex("558bec83ec1853565790")
ENTRY_SIG = "55 8b ec 83 ec ?? 53 56 57 90"
OPERAND_SIG = "11 22 33 66 8b 0d ?? ?? ?? ??"


def image_with(*placed):
    """The synthetic XBE with bytes written into .text at the given offsets."""
    data = bytearray(synthetic_xbe())
    for offset, blob in placed:
        data[TEXT_RAW + offset:TEXT_RAW + offset + len(blob)] = blob
    return tes3x_inject.Xbe(bytes(data))


class SignatureCases:
    """Shared cases; a subclass says how to register and find a signature."""

    def test_finds_a_unique_function_entry(self):
        image = image_with((0x100, b"\xCC" + ENTRY))
        self.assertEqual(self.find(image, "T_ENTRY"), TEXT_VA + 0x101)

    def test_rejects_a_signature_that_matches_twice(self):
        image = image_with((0x100, b"\xCC" + ENTRY), (0x200, b"\xCC" + ENTRY))
        with self.assertRaisesRegex(ValueError, "2 signatures"):
            self.find(image, "T_ENTRY")

    def test_rejects_a_match_in_the_middle_of_a_function(self):
        image = image_with((0x100, b"\x90" + ENTRY))
        with self.assertRaisesRegex(ValueError, "function entry"):
            self.find(image, "T_ENTRY")

    def test_rejects_no_match(self):
        with self.assertRaisesRegex(ValueError, "0 signatures"):
            self.find(image_with(), "T_ENTRY")

    def test_operand_reads_the_address_after_its_instruction(self):
        code = bytes.fromhex("112233668b0d") + struct.pack("<I", TEXT_VA + 0x50)
        self.assertEqual(self.find(image_with((0x300, code)), "T_OPERAND"), TEXT_VA + 0x50)

    def test_operand_after_the_wrong_instruction_is_refused(self):
        code = bytes.fromhex("112233668b0e") + struct.pack("<I", TEXT_VA + 0x50)
        with self.assertRaises(ValueError):
            self.find(image_with((0x300, code)), "T_OPERAND")

    def test_operand_outside_its_section_is_refused(self):
        code = bytes.fromhex("112233668b0d") + struct.pack("<I", 0x90000)
        with self.assertRaisesRegex(ValueError, "outside"):
            self.find(image_with((0x300, code)), "T_OPERAND")


class ConsoleSignatureTests(SignatureCases, unittest.TestCase):
    def setUp(self):
        # VK_CAPS is the one operand the checker expects to live in .text
        table = {"T_ENTRY": ("match", 0, "", ENTRY_SIG),
                 "VK_CAPS": ("operand", 6, "668b0d", OPERAND_SIG)}
        patcher = mock.patch.dict(console_signatures.SIGNATURES, table)
        patcher.start()
        self.addCleanup(patcher.stop)

    def find(self, image, name):
        name = "VK_CAPS" if name == "T_OPERAND" else name
        return console_signatures.find_console_signature(image, name)


class MultiSignatureTests(SignatureCases, unittest.TestCase):
    def setUp(self):
        table = {"T_ENTRY": ("match", 0, ".text", "", ENTRY_SIG),
                 "T_OPERAND": ("operand", 6, ".text", "668b0d", OPERAND_SIG)}
        patcher = mock.patch.dict(multi_signatures.SIGNATURES, table)
        patcher.start()
        self.addCleanup(patcher.stop)

    def find(self, image, name):
        return multi_signatures.find_multi_signature(image, name)


class ScriptAsmTests(unittest.TestCase):
    def test_call_bytes_are_little_endian_opcodes(self):
        self.assertEqual(scriptasm.call_bytes([0x2001, 0x3042]), b"\x01\x20\x42\x30")
        self.assertEqual(scriptasm.call_bytes([]), b"")

    def test_out_of_range_and_nul_opcodes_are_refused(self):
        for opcode in (0, 0x8000, 0x2000, 0x0042, 0x4200):
            with self.assertRaises(SystemExit, msg=hex(opcode)):
                scriptasm.call_bytes([opcode])


def sub(tag, value):
    return tag + struct.pack("<I", len(value)) + value


def scpt_esm(path, name="Main", code=b"\x01\x20\x01\x01", end=True, with_scdt=True):
    """A one-record plugin holding a compiled script called NAME."""
    schd = (name.encode().ljust(scriptasm.SCHD_NAME, b"\0")
            + struct.pack("<5I", 1, 2, 3, len(code), 12))
    body = sub(b"SCHD", schd) + sub(b"SCTX", b"begin Main\r\nend\r\n")
    if with_scdt:
        body += sub(b"SCDT", code if end else code[:-2])
    Path(path).write_bytes(b"SCPT" + struct.pack("<III", len(body), 0, 7) + body)


class ScriptAsmBuildTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.esm = Path(tmp.name) / "in.esm"
        self.out = Path(tmp.name) / "out.esp"

    def build(self, opcodes=(0x2001,), **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            scriptasm.build(str(self.esm), "main", list(opcodes), str(self.out), **kwargs)
        recs = list(records(self.out))
        return recs, dict(subrecords(recs[1][2]))

    def test_find_script_is_case_insensitive_and_refuses_a_missing_name(self):
        scpt_esm(self.esm, name="Main")
        flags, subs = scriptasm.find_script(str(self.esm), "MAIN")
        self.assertEqual(flags, 7)
        self.assertEqual([t for t, _ in subs], [b"SCHD", b"SCTX", b"SCDT"])
        with self.assertRaises(SystemExit):
            scriptasm.find_script(str(self.esm), "Other")

    def test_build_appends_the_call_before_end_and_grows_the_size(self):
        scpt_esm(self.esm)
        recs, subs = self.build([0x2001, 0x3042], bytecode=b"\xAA")
        self.assertEqual([r[0] for r in recs], [b"TES3", b"SCPT"])
        self.assertEqual(recs[1][1], 7)
        self.assertEqual(subs[b"SCDT"],
                         b"\x01\x20" + b"\x01\x20\x42\x30\xAA" + scriptasm.SCRIPT_END)
        size, local = struct.unpack_from("<II", subs[b"SCHD"], scriptasm.SCHD_NAME + 12)
        self.assertEqual((size, local), (4 + 5, 12))
        self.assertIn(b"appended before End", subs[b"SCTX"])

    def test_build_names_the_source_master_in_the_header(self):
        scpt_esm(self.esm)
        recs, _ = self.build()
        header = dict(subrecords(recs[0][2]))
        self.assertEqual(header[b"MAST"], b"Morrowind.esm\0")
        self.assertEqual(struct.unpack("<Q", header[b"DATA"])[0], self.esm.stat().st_size)

    def test_build_refuses_a_script_without_data_or_end(self):
        scpt_esm(self.esm, with_scdt=False)
        with self.assertRaises(SystemExit):
            self.build()
        scpt_esm(self.esm, end=False)
        with self.assertRaises(SystemExit):
            self.build()


class ProfilerTests(unittest.TestCase):
    RECORDS = [{"name": "Actor::update", "va": "0x00010010"},
               {"name": "Actor::render", "va": "0x00010020"},
               {"name": "Cell::load", "va": "0x00010030"}]

    def test_resolve_takes_hex_names_and_unique_substrings(self):
        self.assertEqual(prof.resolve("0x1234", self.RECORDS), 0x1234)
        self.assertEqual(prof.resolve("Cell::load", self.RECORDS), 0x10030)
        self.assertEqual(prof.resolve("cell", self.RECORDS), 0x10030)

    def test_resolve_refuses_unknown_and_ambiguous_names(self):
        with self.assertRaises(SystemExit):
            prof.resolve("nothing", self.RECORDS)
        with self.assertRaises(SystemExit):
            prof.resolve("actor", self.RECORDS)

    @staticmethod
    def block(slots=2, active=1, version=2):
        layout = prof.HEADER_BY_VERSION[version]
        header = list(struct.unpack(layout, bytes(struct.calcsize(layout))))
        header[0], header[1], header[2], header[3] = prof.MAGIC, version, slots, prof.SLOT_SIZE
        if version >= 2:
            header[34], header[35] = active, prof.ACTIVE_V2_SIZE
        data = struct.pack(layout, *header)
        data += struct.pack(f"<{slots}I", *[0x10000 + 16 * i for i in range(slots)])
        data += b"".join(struct.pack(prof.SLOT, i, 0, 1, 2, 3, 4) for i in range(slots))
        if version >= 2:
            data += b"".join(struct.pack(prof.ACTIVE_V2, 1, 2, 3, 4, 5, 6, 7, 8, 9)
                             for _ in range(active))
        return data

    def read(self, data):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "prof.bin"
            path.write_bytes(data)
            return list(prof.blocks(path))

    def test_blocks_reads_every_block_in_a_file(self):
        found = self.read(self.block() + self.block(slots=3, active=0))
        self.assertEqual(len(found), 2)
        _header, targets, rows, active = found[0]
        self.assertEqual(targets, (0x10000, 0x10010))
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(active), 1)
        self.assertEqual(len(found[1][1]), 3)

    def test_blocks_reads_the_older_version(self):
        found = self.read(self.block(version=1))
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0][3], [])

    def test_a_bad_magic_or_version_is_refused(self):
        data = bytearray(self.block())
        data[0] ^= 0xFF
        with self.assertRaises(SystemExit):
            self.read(bytes(data))
        data = bytearray(self.block())
        struct.pack_into("<I", data, 4, 99)
        with self.assertRaises(SystemExit):
            self.read(bytes(data))

    def test_a_truncated_block_ends_the_read(self):
        with mock.patch("builtins.print"):
            self.assertEqual(self.read(self.block()[:-10]), [])

    def report(self, data, names=None, hz=0.0):
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "prof.bin"
            path.write_bytes(data)
            with contextlib.redirect_stdout(out), \
                    mock.patch.object(prof, "name_index", return_value=names or {}):
                prof.cmd_report(mock.Mock(dump=str(path), hz=hz))
        return out.getvalue()

    def test_report_names_targets_and_lists_active_calls(self):
        text = self.report(self.block(), {0x10000: {"name": "Actor::update"}}, hz=1e6)
        self.assertIn("=== block 0", text)
        self.assertIn("Actor::update", text)
        self.assertIn("active instrumented calls at dump (1)", text)

    def test_report_reads_the_older_version_without_active_calls(self):
        text = self.report(self.block(version=1), hz=1e6)
        self.assertIn("=== block 0", text)
        self.assertNotIn("active instrumented", text)

    def test_report_with_no_complete_block_is_refused(self):
        with mock.patch("builtins.print"), self.assertRaises(SystemExit):
            self.report(self.block()[:-10])

    def test_ms_converts_cycles_at_the_given_rate(self):
        self.assertEqual(prof.ms(733_000, 733e6), 1.0)


class ReadLogTests(unittest.TestCase):
    def test_session_of_finds_the_session_marker(self):
        self.assertEqual(readlog.session_of(b"x diag.session 0x0000ABCD y"), b"0x0000ABCD")
        self.assertIsNone(readlog.session_of(b"no marker"))

    def scan(self, image, partition):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "disk.img"
            path.write_bytes(image)
            with mock.patch.dict(readlog.PARTITIONS, {"E": partition}):
                return readlog.raw_logs(str(path), "E")

    def test_raw_logs_splits_on_nul_and_ff(self):
        one = readlog.LOG_START + b"a diag.session 0x00000001\n"
        two = readlog.LOG_START + b"b diag.session 0x00000002\n"
        image = b"\x00" * 40 + one + b"\x00" + two + b"\xFF" + b"\x00" * 10
        self.assertEqual(self.scan(image, (16, 4000)), [one, two])

    def test_raw_logs_ignores_data_outside_the_partition(self):
        image = readlog.LOG_START + b"c\n" + b"\x00" * 100
        self.assertEqual(self.scan(image, (50, 50)), [])

    def read_log(self, entry, raw):
        with mock.patch.object(readlog, "read_file", return_value=entry), \
                mock.patch.object(readlog, "raw_logs", return_value=raw), \
                contextlib.redirect_stderr(io.StringIO()):
            return readlog.read_log("img")

    def test_read_log_prefers_the_longer_raw_copy_of_the_same_session(self):
        short = readlog.LOG_START + b"diag.session 0x00000001\n"
        longer = short + b"more\n"
        self.assertEqual(self.read_log(short, [longer]), longer)

    def test_read_log_ignores_another_session_when_the_entry_exists(self):
        entry = readlog.LOG_START + b"diag.session 0x00000001\n"
        other = readlog.LOG_START + b"diag.session 0x00000002\nlonger\n"
        self.assertEqual(self.read_log(entry, [other]), entry)

    def test_read_log_falls_back_to_the_fullest_raw_log_without_an_entry(self):
        a = readlog.LOG_START + b"a\n"
        b = readlog.LOG_START + b"bb\n"
        self.assertEqual(self.read_log(None, [a, b]), b)

    def test_read_log_returns_none_when_nothing_is_found(self):
        self.assertIsNone(self.read_log(None, []))


if __name__ == "__main__":
    unittest.main()
