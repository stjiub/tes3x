import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import tes3x.layouts as tes3x_layouts  # noqa: E402

CURATED = ROOT / "symbols" / "curated.json"
STRUCTS = ROOT / "symbols" / "structs.json"
ADDRESS = re.compile(r"0x[0-9A-F]{8}\Z")
HEX = re.compile(r"0x[0-9A-F]+\Z")


class CuratedSymbolTests(unittest.TestCase):
    def test_records_are_well_formed_and_unique(self):
        records = json.loads(CURATED.read_text(encoding="utf-8"))["records"]
        self.assertTrue(records)
        seen = set()
        for record in records:
            where = record.get("va")
            self.assertRegex(record["va"], ADDRESS, where)
            self.assertNotIn(record["va"], seen, "duplicate address")
            seen.add(record["va"])
            self.assertTrue(record["name"], where)
            self.assertIn(record["confidence"], ("verified", "matched", "guess"), where)
            self.assertIn(record.get("kind", "function"), ("function", "site", "data"), where)
            if "pc_va" in record:
                self.assertRegex(record["pc_va"], ADDRESS, where)
            if str(record.get("provenance", "")).startswith("mwse:"):
                self.assertEqual(record["confidence"], "matched", where)
            if "signature" in record:
                self.assertIn("(", record["signature"], where)


class StructOverlayTests(unittest.TestCase):
    def test_overlay_is_well_formed(self):
        structs = json.loads(STRUCTS.read_text(encoding="utf-8"))["structs"]
        names = set()
        for s in structs:
            self.assertNotIn(s["name"], names, "duplicate struct")
            names.add(s["name"])
            for key in ("size", "pc_valid_until"):
                if key in s:
                    self.assertRegex(s[key], HEX, s["name"])
            offsets = set()
            for f in s.get("fields", []):
                where = f"{s['name']}+{f.get('offset')}"
                self.assertRegex(f["offset"], HEX, where)
                self.assertNotIn(f["offset"], offsets, where)
                offsets.add(f["offset"])
                self.assertTrue(f["name"] and f["type"], where)
                self.assertIn(f["confidence"], ("verified", "matched", "guess"), where)

    def test_merge_overrides_and_truncates(self):
        types = {
            "records": {
                "/TES3/Stat": {"kind": "struct", "size": 12, "fields": [[4, "base", "float"]]},
                "/TES3/World": {"kind": "struct", "size": 16, "fields": [
                    [0, "a", "int"], [4, "stat", "/TES3/Stat*"], [8, "b", "int"],
                    [12, "c", "int"]]},
            },
            "enums": {},
        }
        curated = {"structs": [{"name": "World", "pc_valid_until": "0x8", "fields": [
            {"offset": "0x4", "name": "stats", "type": "TES3::Stat *[1]"},
            {"offset": "0xC", "name": "flag", "type": "unsigned char"}]}]}
        out = tes3x_layouts.merge(types, curated)
        self.assertEqual(out["records"]["/TES3/World"]["fields"],
                         [[0, "a", "int"], [4, "stats", "/TES3/Stat*[1]"], [12, "flag", "uchar"]])
        self.assertEqual(types["records"]["/TES3/World"]["fields"][1][1], "stat")

    def test_signature_drops_convention_name_qualifier_and_const(self):
        text, conv = tes3x_layouts.ghidra_signature(
            "float __thiscall TES3::MobileActor::getSkill(const TES3::Skill * s, int i)")
        self.assertEqual(text, "float getSkill(TES3::Skill * s, int i)")
        self.assertEqual(conv, "__thiscall")


if __name__ == "__main__":
    unittest.main()
