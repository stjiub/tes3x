import contextlib
import io
import struct
import tempfile
import unittest
from pathlib import Path

import tes3x.audit as audit
import tes3x.tour as tour


def printed(function, *args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        result = function(*args)
    return result, out.getvalue()


class AuditTests(unittest.TestCase):
    def test_human_sizes(self):
        self.assertEqual(audit.human(12), "12 B")
        self.assertEqual(audit.human(2048), "2.0 KB")
        self.assertEqual(audit.human(5 * 1024 ** 2), "5.0 MB")
        self.assertEqual(audit.human(3 * 1024 ** 4), "3072.0 GB")

    def test_scan_groups_files_by_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.esp").write_bytes(b"12345")
            (root / "sub").mkdir()
            (root / "sub" / "b.dds").write_bytes(b"xy")
            dirs = audit.scan(str(root))
        self.assertEqual(dirs["."], [("a.esp", 5)])
        self.assertEqual(dirs["sub"], [("b.dds", 2)])

    def test_plugins_are_split_and_sorted_in_enumeration_order(self):
        entries = [("b.esp", 1), ("A.esm", 2), ("a.esp", 3), ("z.esm", 4), ("x.bsa", 5)]
        masters, plugs = audit.plugins(entries)
        self.assertEqual([n for n, _ in masters], ["z.esm", "A.esm"])
        self.assertEqual([n for n, _ in plugs], ["b.esp", "a.esp"])

    def test_report_plugins_flags_stubs_and_totals(self):
        total, text = printed(audit.report_plugins, [("Stub.esp", 10), ("Big.esm", 2048)])
        self.assertEqual(total, 2058)
        self.assertIn("1 esm, 1 esp", text)
        self.assertEqual(text.count("<-- STUB"), 1)

    def test_junk_lists_non_game_files_largest_first(self):
        _, text = printed(audit.report_junk, [("a.esp", 1), ("b.txt", 5), ("c.zip", 900)])
        self.assertLess(text.index("c.zip"), text.index("b.txt"))
        self.assertNotIn("a.esp", text)
        self.assertEqual(printed(audit.report_junk, [("a.esp", 1)])[1], "")

    def test_fatx_names_report_only_the_long_ones(self):
        long_name = "x" * (audit.FATX_NAME_MAX + 1)
        dirs = {".": [(long_name, 1), ("ok.dds", 1)], "sub": [(long_name, 1)]}
        _, text = printed(audit.report_fatx_names, dirs)
        self.assertIn("(2)", text)
        self.assertIn(f"sub/{long_name}", text)
        self.assertNotIn("ok.dds", text)

    def test_fanout_marks_hot_folders(self):
        dirs = {"big": [("f", 1)] * 1000, "small": [("f", 1)], "empty": []}
        _, text = printed(audit.report_fanout, dirs)
        self.assertIn("HOT", text)
        self.assertEqual(text.count("HOT"), 1)
        self.assertNotIn("empty", text)

    def test_dupes_are_big_files_of_identical_size(self):
        mb = 2 * 1024 * 1024
        _, text = printed(audit.report_dupes, [("a.bsa", mb), ("b.bsa", mb), ("c.bsa", mb + 1),
                                               ("t1", 10), ("t2", 10)])
        self.assertIn("a.bsa, b.bsa", text)
        self.assertNotIn("c.bsa", text)
        self.assertNotIn("t1", text)


def cell(name, flags, x=0, y=0):
    data = b"NAME" + struct.pack("<I", len(name) + 1) + name.encode() + b"\0"
    data += b"DATA" + struct.pack("<I", 12) + struct.pack("<Iii", flags, x, y)
    return b"CELL" + struct.pack("<III", len(data), 0, 0) + data


class TourTests(unittest.TestCase):
    def test_cells_reads_interiors_and_exteriors(self):
        header = b"TES3" + struct.pack("<III", 0, 0, 0)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.esm"
            path.write_bytes(header + cell("Balmora, Temple", 1) + cell("", 0, 3, -4))
            found = tour.cells([str(path)])
        self.assertEqual(found[("int", "balmora, temple")], "Balmora, Temple")
        self.assertEqual(found[("ext", (3, -4))], (3, -4))

    FOUND = {("ext", (0, 0)): (0, 0), ("ext", (1, 0)): (1, 0), ("ext", (0, 1)): (0, 1),
             ("ext", (1, 1)): (1, 1), ("int", "a house"): "A House", ("int", "b hut"): "B Hut"}

    def test_exterior_stops_snake_from_the_top_row(self):
        stops = tour.tour(self.FOUND, False, True, None, [], 1, 1)
        self.assertEqual([s[1] for s in stops], ["0,1", "1,1", "1,0", "0,0"])
        self.assertEqual(stops[0][0], "coe 0, 1")

    def test_bounds_step_and_every_thin_the_stops(self):
        self.assertEqual(len(tour.tour(self.FOUND, False, True, (0, 0, 0, 1), [], 1, 1)), 2)
        self.assertEqual(len(tour.tour(self.FOUND, False, True, None, [], 1, 2)), 1)
        self.assertEqual(len(tour.tour(self.FOUND, False, True, None, [], 2, 1)), 2)

    def test_interior_stops_filter_by_prefix(self):
        stops = tour.tour(self.FOUND, True, False, None, ["b "], 1, 1)
        self.assertEqual(stops, [('coc "B Hut"', "B Hut")])

    def test_last_session_starts_at_the_last_entry_marker(self):
        text = "0 ms entry.free_kb 1\nold\n0 ms entry.free_kb 2\nnew"
        self.assertEqual(tour.last_session(text), ["0 ms entry.free_kb 2", "new"])
        self.assertEqual(tour.last_session("no marker\nhere"), ["no marker", "here"])


if __name__ == "__main__":
    unittest.main()
