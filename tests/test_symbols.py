import json
import re
import unittest
from pathlib import Path

CURATED = Path(__file__).resolve().parents[1] / "symbols" / "curated.json"
ADDRESS = re.compile(r"0x[0-9A-F]{8}\Z")


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


if __name__ == "__main__":
    unittest.main()
