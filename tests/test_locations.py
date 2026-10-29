import unittest

from tes3x import locations
from tes3x.catalog import CatalogError, load as load_catalog


class LocationsTest(unittest.TestCase):
    def test_file_loads_and_presets_resolve(self):
        places = locations.load()
        presets = locations.presets(places)
        self.assertEqual(presets["all"], list(places))
        for name in ("cities", "dense", "expansions"):
            self.assertTrue(presets[name])
        self.assertEqual(locations.select(places, "balmora,cities")[0], "seyda-neen")
        with self.assertRaises(locations.LocationError):
            locations.select(places, "nowhere")

    def test_script_visits_each_place_and_marks_it(self):
        places = locations.load()
        lines = locations.script(places, ["balmora", "vivec-library"], 30).splitlines()
        self.assertEqual(lines, ["mark start", "coe -3, -2", "wait 30", "mark balmora",
                                 'coc "Vivec, Library of Vivec"', "wait 30",
                                 "mark vivec-library"])

    def test_summary_measures_against_baseline(self):
        places = locations.load()
        log = "0 ms entry.free_kb 9\n10 ms mem.start 5000\n20 ms mem.balmora 4000\n"
        free = locations.measure(log)
        result = locations.summary(places, ["balmora", "suran"], free, {"balmora": 4500})
        self.assertEqual(result["visited"], 1)
        self.assertEqual(result["extra_kb"], 500)
        self.assertEqual(result["low"], {"id": "balmora", "free_kb": 4000})

    def test_catalog_memory_must_be_whole_kb(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "c.toml"
            path.write_text('schema = 1\n[[mod]]\nid = "a"\nname = "A"\nfolder = "A"\n'
                            'memory_kb = "lots"\n', encoding="utf-8")
            with self.assertRaises(CatalogError):
                load_catalog(path)


if __name__ == "__main__":
    unittest.main()


class CatalogMatchTest(unittest.TestCase):
    def test_nexus_id_matches_a_renamed_folder(self):
        from tes3x.catalog import match
        catalog = {"a": {"id": "a", "name": "Alpha", "folder": "Alpha",
                         "url": "https://www.nexusmods.com/morrowind/mods/42"}}
        self.assertIsNone(match(catalog, "Renamed"))
        self.assertIs(match(catalog, "Renamed", nexus=42), catalog["a"])

    def test_lenient_load_ignores_newer_fields(self):
        import tempfile
        from pathlib import Path
        from tes3x.catalog import load
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "c.toml"
            path.write_text('schema = 1\n[[mod]]\nid = "a"\nname = "A"\nfolder = "A"\n'
                            'future = 1\n', encoding="utf-8")
            with self.assertRaises(CatalogError):
                load(path)
            self.assertIn("a", load(path, lenient=True))


class UpdateTest(unittest.TestCase):
    def test_newer_compares_release_numbers(self):
        from tes3x.updates import newer
        self.assertTrue(newer("v0.2.0", "0.1.9"))
        self.assertFalse(newer("v0.1.0", "0.1.0-dev.352+gabc"))
        self.assertFalse(newer("v0.1.0", "0.1.0"))


class PluginMatchTest(unittest.TestCase):
    def test_plugins_find_a_renamed_mod(self):
        from tes3x.catalog import match
        catalog = {"a": {"id": "a", "name": "Alpha", "folder": "Alpha",
                         "plugins": ["Alpha.esp"]},
                   "b": {"id": "b", "name": "Beta", "folder": "Beta.esp"}}
        self.assertIsNone(match(catalog, "Renamed", plugins=["Other.esp"]))
        self.assertIs(match(catalog, "Renamed", plugins=["alpha.ESP", "Extra.esp"]), catalog["a"])
        self.assertIs(match(catalog, "Renamed", plugins=["Beta.esp"]), catalog["b"])
        self.assertIsNone(match(catalog, "Renamed", plugins=["Morrowind.esm"]))
