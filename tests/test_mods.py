import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

from tes3x_mods import ModLedgerError, load_ledger, render  # noqa: E402


class ModLedgerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "mods.toml"

    def test_manual_status_and_evidence_are_rendered(self):
        self.path.write_text('''
schema = 1
[[mod]]
id = "m"
name = "A Mod"
version = "1"
status = "works-with-requirements"
requirements = ["Patch"]
observations = "Played on Xbox."
validation = ["results/m.json"]
''', encoding="utf-8")
        text = render(load_ledger(self.path), self.path.name)
        self.assertIn("works-with-requirements", text)
        self.assertIn("Played on Xbox.", text)
        self.assertIn("[`m.json`](results/m.json)", text)
        self.assertIn("never change status automatically", text)

    def test_unknown_status_is_rejected(self):
        self.path.write_text('schema = 1\n[[mod]]\nid = "m"\nname = "M"\nstatus = "passed"\n')
        with self.assertRaisesRegex(ModLedgerError, "status"):
            load_ledger(self.path)


if __name__ == "__main__":
    unittest.main()
