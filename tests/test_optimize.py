import json
import struct
import tempfile
import unittest
from pathlib import Path

from tes3x.optimize import (OptimizeError, inspect_plugins, plan_scripts, state_signature,
                            verify, write_plugins)
from tes3x.records import records, subrecords


def sub(tag, data):
    return struct.pack("<4sI", tag, len(data)) + data


def rec(tag, data, flags=0, unknown=0):
    return struct.pack("<4sIII", tag, len(data), unknown, flags) + data


def script(name, text, flags=0):
    schd = name.encode("cp1252").ljust(32, b"\0") + bytes(20)
    return rec(b"SCPT", sub(b"SCHD", schd) + sub(b"SCTX", text.encode("cp1252")), flags)


def plugin(path, body, masters=()):
    hedr = struct.pack("<fI", 1.3, 0) + bytes(32 + 256) + struct.pack("<I", len(body))
    header = sub(b"HEDR", hedr)
    for name, size in masters:
        header += sub(b"MAST", name.encode("cp1252") + b"\0")
        header += sub(b"DATA", struct.pack("<Q", size))
    path.write_bytes(rec(b"TES3", header) + b"".join(body))
    return path


class OptimizerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def load_order(self):
        base = plugin(self.root / "Base.esm", [
            script("replace_me", "old"),
            script("keep_me", "keep"),
        ])
        later = plugin(self.root / "Later.esp", [script("replace_me", "new")],
                       [(base.name, base.stat().st_size)])
        return base, later

    def test_superseded_script_is_removed_and_headers_are_rewritten(self):
        base, later = self.load_order()
        plugins = inspect_plugins([base, later])
        drops, report = plan_scripts(plugins)
        self.assertEqual(report["removed_records"], 1)
        self.assertEqual(report["guarded_records"], 0)

        output = self.root / "derived"
        derived = write_plugins(plugins, output, drops)
        self.assertEqual(verify([base, later], derived), state_signature([base, later])["digest"])
        self.assertEqual([tag for tag, _flags, _data in records(derived[0])], [b"TES3", b"SCPT"])

        header = next(data for tag, _flags, data in records(derived[1]) if tag == b"TES3")
        parts = list(subrecords(header))
        count = struct.unpack_from("<I", parts[0][1], 296)[0]
        master_size = struct.unpack("<Q", parts[2][1])[0]
        self.assertEqual(count, 1)
        self.assertEqual(master_size, derived[0].stat().st_size)

    def test_script_order_follows_last_definition(self):
        base, later = self.load_order()
        rows = state_signature([base, later])["scripts"]
        self.assertEqual([row["key"] for row in rows], ["keep_me", "replace_me"])
        self.assertEqual(rows[-1]["owner"], "Later.esp")

    def test_flagged_script_is_retained_and_deletion_propagates(self):
        base = plugin(self.root / "Base.esm", [script("gone", "old", 0x20)])
        later = plugin(self.root / "Later.esp", [script("gone", "new")],
                       [(base.name, base.stat().st_size)])
        plugins = inspect_plugins([base, later])
        drops, report = plan_scripts(plugins)
        self.assertFalse(drops)
        self.assertEqual(report["guarded_records"], 1)
        self.assertTrue(state_signature([base, later])["scripts"][0]["deleted"])

    def test_checker_rejects_changed_winner(self):
        base, later = self.load_order()
        changed = self.root / "changed"
        changed.mkdir()
        (changed / base.name).write_bytes(base.read_bytes())
        data = later.read_bytes().replace(b"new", b"bad")
        (changed / later.name).write_bytes(data)
        with self.assertRaisesRegex(OptimizeError, "script state"):
            verify([base, later], [changed / base.name, changed / later.name])

    def test_manifest_values_are_json_safe(self):
        base, later = self.load_order()
        plugins = inspect_plugins([base, later])
        _drops, report = plan_scripts(plugins)
        json.dumps(report)


if __name__ == "__main__":
    unittest.main()
