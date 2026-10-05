import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import tes3x_manifest as manifest


class ManifestTests(unittest.TestCase):
    def test_create_marks_what_a_server_may_hand_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            build, retail = Path(tmp, "build"), Path(tmp, "retail")
            for root in (build, retail):
                (root / "Data Files").mkdir(parents=True)
                (root / "Data Files" / "Morrowind.bsa").write_bytes(b"retail")
                (root / "default.xbe").write_bytes(b"xbe")
            (build / "Data Files" / "mod.esp").write_bytes(b"mod")
            (build / "Morrowind.ini").write_bytes(b"ini")
            made = manifest.create(build, profile="p", source={"kind": "pipeline"},
                                   plugins=["mod.esp"], retail=retail)
            self.assertEqual({path: entry["serve"] for path, entry in made["files"].items()}, {
                "Data Files/Morrowind.bsa": False, "Data Files/mod.esp": True,
                "Morrowind.ini": True, "default.xbe": False})
            self.assertEqual(made["files"]["Data Files/mod.esp"]["sha256"],
                             hashlib.sha256(b"mod").hexdigest())
            self.assertEqual((made["format"], made["plugins"]), (manifest.FORMAT, ["mod.esp"]))

            manifest.write(build, made)
            self.assertNotIn(manifest.NAME, manifest.load(build)["files"])
            self.assertEqual(manifest.diff(made, build), ([], [], []))
            (build / "Morrowind.ini").write_bytes(b"INI")
            (build / "Data Files" / "mod.esp").unlink()
            (build / "extra.txt").write_bytes(b"")
            self.assertEqual(manifest.diff(made, build),
                             (["Data Files/mod.esp"], ["Morrowind.ini"], ["extra.txt"]))

    def test_a_delta_rebuilds_the_output_from_the_reference(self):
        try:
            import zstandard  # noqa: F401
        except ImportError:
            self.skipTest("zstandard is not installed")
        reference = bytes(range(256)) * 4096
        output = reference[:500000] + b"patched" * 1000 + reference[600000:]
        delta = manifest.make_delta(reference, output)
        self.assertLess(len(delta), 20000)
        self.assertEqual(manifest.apply_delta(reference, delta), output)

    def test_a_newer_format_is_refused(self):
        with self.assertRaisesRegex(ValueError, "newer"):
            manifest.parse(b'{"format": 99, "files": {}}')
        with self.assertRaisesRegex(ValueError, "not a TES3X"):
            manifest.parse(b'{"default.xbe": [1, "x"]}')


if __name__ == "__main__":
    unittest.main()
