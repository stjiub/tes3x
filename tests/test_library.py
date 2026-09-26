import sys
import subprocess
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

from tes3x_build import Mod, materialize, resolve  # noqa: E402
from tes3x_library import (LibraryError, available_plugins, dependency_order,  # noqa: E402
                           discover_library, load_library, resolve_selection, write_library)
from tes3x_pipeline import PipelineError, validate_profile  # noqa: E402


CATALOG = '''
schema = 1

[[mod]]
id = "base"
name = "Base Assets"

[[mod.release]]
version = "1.0"
folder = "Base"
default = true

[[mod]]
id = "travel"
name = "Travel Mod"

[[mod.release]]
version = "1.0"
folder = "Travel 1.0"
default = true
roots = ["00 Core"]
dependencies = ["base"]

[[mod.release.component]]
id = "music"
name = "Travel music"
roots = ["10 Music"]
default = true

[[mod.release.component]]
id = "boats-vanilla"
roots = ["20 Boats/Vanilla"]
group = "boats"

[[mod.release.component]]
id = "boats-hd"
roots = ["20 Boats/HD"]
group = "boats"
conflicts = ["boats-vanilla"]
'''


class LibraryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / "library.toml").write_text(CATALOG, encoding="utf-8")
        for relative in ("Travel 1.0/00 Core/meshes", "Travel 1.0/10 Music/music",
                         "Travel 1.0/20 Boats/Vanilla/meshes",
                         "Travel 1.0/20 Boats/HD/meshes", "Base/meshes"):
            (self.root / relative).mkdir(parents=True)
        (self.root / "Travel 1.0/00 Core/travel.esp").write_bytes(b"TES3")
        (self.root / "Travel 1.0/00 Core/meshes/boat.nif").write_bytes(b"core")
        (self.root / "Travel 1.0/10 Music/music/travel.mp3").write_bytes(b"music")
        (self.root / "Travel 1.0/20 Boats/HD/meshes/boat.nif").write_bytes(b"hd")

    def test_default_release_and_component(self):
        catalog = load_library(self.root)
        selected = resolve_selection({"id": "travel"}, self.root, catalog)
        self.assertEqual(selected["version"], "1.0")
        self.assertEqual(selected["components"], ["music"])
        self.assertEqual(selected["dependencies"], ["base"])
        self.assertEqual(dependency_order("travel", catalog), ["base", "travel"])
        self.assertEqual([path.relative_to(self.root).as_posix() for path in selected["roots"]],
                         ["Travel 1.0/00 Core", "Travel 1.0/10 Music"])
        self.assertEqual(available_plugins(selected), ["travel.esp"])

    def test_dependency_order_uses_selected_release(self):
        text = CATALOG + '''
[[mod.release]]
version = "2.0"
folder = "Travel 2.0"
'''
        (self.root / "library.toml").write_text(text, encoding="utf-8")
        catalog = load_library(self.root)
        self.assertEqual(dependency_order("travel", catalog, "1.0"), ["base", "travel"])
        self.assertEqual(dependency_order("travel", catalog, "2.0"), ["travel"])

    def test_choice_group_rejects_two_components(self):
        with self.assertRaisesRegex(LibraryError, "conflicts with boats-vanilla"):
            resolve_selection({"id": "travel", "components": ["boats-vanilla", "boats-hd"]},
                              self.root, load_library(self.root))

    def test_components_are_ordered_overlay_layers(self):
        selected = resolve_selection({"id": "travel", "components": ["boats-hd"]}, self.root,
                                     load_library(self.root))
        mod = Mod("Travel Mod 1.0", [str(path) for path in selected["roots"]], 10)
        filemap, conflicts = resolve([mod])
        self.assertEqual(filemap["meshes/boat.nif"][1].replace("\\", "/").split("/")[-3:],
                         ["HD", "meshes", "boat.nif"])
        output = self.root / "out"
        materialize(filemap, [mod], output, {})
        self.assertEqual((output / "meshes/boat.nif").read_bytes(), b"hd")
        self.assertFalse(conflicts)  # Component choices are one logical mod.

    def test_component_folder_is_hidden_from_a_parent_base_root(self):
        (self.root / "Bundle/Optional").mkdir(parents=True)
        (self.root / "Bundle/core.esp").write_bytes(b"TES3")
        (self.root / "Bundle/Optional/optional.esp").write_bytes(b"TES3")
        text = CATALOG + '''
[[mod]]
id = "bundle"
name = "Bundle"
[[mod.release]]
version = "1"
folder = "Bundle"
default = true
roots = ["."]
[[mod.release.component]]
id = "optional"
roots = ["Optional"]
'''
        (self.root / "library.toml").write_text(text, encoding="utf-8")
        catalog = load_library(self.root)

        core = resolve_selection({"id": "bundle", "components": []}, self.root, catalog)
        core_mod = Mod("Bundle", core["layers"], 10)
        self.assertEqual(set(core_mod.files), {"core.esp"})
        self.assertEqual(available_plugins(core), ["core.esp"])

        optional = resolve_selection({"id": "bundle", "components": ["optional"]},
                                     self.root, catalog)
        optional_mod = Mod("Bundle", optional["layers"], 10)
        self.assertEqual(set(optional_mod.files), {"core.esp", "optional.esp"})
        self.assertEqual(available_plugins(optional), ["core.esp", "optional.esp"])

    def test_profile_accepts_managed_or_legacy_mods(self):
        base = {"profile": {"name": "p", "library": str(self.root)}}
        for mod in ({"id": "travel", "version": "1.0", "components": ["music"]},
                    {"name": "Travel 1.0"}):
            validate_profile(dict(base, mods=[mod]))
        with self.assertRaisesRegex(PipelineError, "exactly one"):
            validate_profile(dict(base, mods=[{"id": "travel", "name": "Travel 1.0"}]))

    def test_builder_requires_explicit_dependency(self):
        profile = self.root / "profile.toml"
        head = f'[profile]\nname = "p"\nlibrary = "{self.root.as_posix()}"\n'
        profile.write_text(head + '[[mods]]\nid = "travel"\n', encoding="utf-8")
        result = subprocess.run([sys.executable, str(TOOLS / "tes3x_build.py"), str(profile)],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("needs profile mod ids base", result.stderr)
        profile.write_text(head + '[[mods]]\nid = "base"\norder = 10\n'
                           '[[mods]]\nid = "travel"\norder = 20\n', encoding="utf-8")
        result = subprocess.run([sys.executable, str(TOOLS / "tes3x_build.py"), str(profile)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_plain_library_scan_round_trips(self):
        plain = self.root / "plain"
        (plain / "Folder Mod" / "textures").mkdir(parents=True)
        (plain / "Single.esp").write_bytes(b"TES3")
        catalog = discover_library(plain)
        self.assertEqual(set(catalog), {"folder-mod", "single"})
        write_library(plain, catalog)
        loaded = load_library(plain)
        self.assertEqual(set(loaded), set(catalog))
        selected = resolve_selection({"id": "single"}, plain, loaded)
        self.assertEqual(selected["roots"], [plain / "Single.esp"])


if __name__ == "__main__":
    unittest.main()
