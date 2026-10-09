import hashlib
import tempfile
import unittest
from pathlib import Path

import tes3x.manifest as manifest


class ManifestTests(unittest.TestCase):
    def test_create_records_where_each_file_comes_from(self):
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
            self.assertEqual({path: entry["origin"] for path, entry in made["files"].items()}, {
                "Data Files/Morrowind.bsa": "retail", "Data Files/mod.esp": "build",
                "Morrowind.ini": "build", "default.xbe": "xbe"})
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

    def test_the_build_id_leads_and_follows_only_files_and_load_order(self):
        files = {"Data Files/mod.esp": {"size": 3, "sha256": "ab", "origin": "build"}}
        made = manifest.create(None, profile="p", source={}, plugins=["mod.esp"], files=files)
        self.assertEqual(list(made)[:2], ["format", "build"])
        again = manifest.create(None, profile="q", source={"kind": "tree"}, plugins=["mod.esp"],
                                files=files)
        self.assertEqual(again["build"], made["build"])
        installed = {"server": "10.0.0.7", "deployed": "2026-10-06", **made}
        self.assertEqual(manifest.build_id(installed), made["build"])
        self.assertNotEqual(manifest.create(None, profile="p", source={}, plugins=[],
                                            files=files)["build"], made["build"])
        self.assertIn('"build": "' + made["build"],
                      manifest.write(Path(tempfile.mkdtemp()), made).read_text()[:120])

    def test_a_delta_rebuilds_the_output_from_the_reference(self):
        try:
            __import__("zstandard")
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
