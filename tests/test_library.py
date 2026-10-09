import sys
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path


from tes3x.build import Mod, materialize, resolve  # noqa: E402
from tes3x.library import (LibraryError, available_plugins, convert_profile, dependency_order,  # noqa: E402
                           discover_library, extract_archive, guess_release, index_library,
                           install_files, install_layout, load_library, nexus_id,
                           resolve_selection, write_library)
from tes3x.pipeline import PipelineError, validate_profile  # noqa: E402


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
        # resolved, as the tools resolve it: Windows may name temp by its 8.3 short form
        self.root = Path(temp.name).resolve()
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
        result = subprocess.run([sys.executable, '-m', 'tes3x', 'build', str(profile)],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("needs profile mod ids base", result.stderr)
        profile.write_text(head + '[[mods]]\nid = "base"\norder = 10\n'
                           '[[mods]]\nid = "travel"\norder = 20\n', encoding="utf-8")
        result = subprocess.run([sys.executable, '-m', 'tes3x', 'build', str(profile)],
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

    def test_indexing_adds_new_folders_and_keeps_existing_entries(self):
        (self.root / "New Mod" / "meshes").mkdir(parents=True)
        (self.root / "Travel").mkdir()  # same slug as an indexed id, different folder
        before = (self.root / "library.toml").read_text(encoding="utf-8")
        path, added = index_library(self.root)
        after = path.read_text(encoding="utf-8")
        self.assertTrue(after.startswith(before))
        self.assertEqual(set(added), {"new-mod", "travel-2"})
        self.assertEqual(set(load_library(self.root)), {"base", "travel", "new-mod", "travel-2"})
        self.assertEqual(index_library(self.root)[1], {})
        self.assertEqual(path.read_text(encoding="utf-8"), after)

    def test_convert_rewrites_whole_folder_mods_only(self):
        (self.root / "Loose Mod").mkdir()
        index_library(self.root)
        profile = ('[profile]\nname = "p"\n\n'
                   '[[mods]]\nname = "Base"\norder = 10\n\n'
                   '[[mods]]\nname = "Travel 1.0"\norder = 20\n\n'
                   '[[mods]]\nname = "loose mod"  # comment kept\norder = 30\n\n'
                   '[[mods]]\nname = "Missing"\norder = 40\n')
        text, converted, skipped = convert_profile(profile, load_library(self.root))
        self.assertEqual(converted, ["Base", "loose mod"])
        self.assertEqual([name for name, _reason in skipped], ["Travel 1.0", "Missing"])
        self.assertIn('id = "base"\norder = 10', text)
        self.assertIn('id = "loose-mod"  # comment kept\n', text)
        self.assertIn('name = "Travel 1.0"', text)
        self.assertEqual(text.replace('id = "base"', 'name = "Base"')
                         .replace('id = "loose-mod"', 'name = "loose mod"'), profile)


class InstallTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        # resolved, as the tools resolve it: Windows may name temp by its 8.3 short form
        self.root = Path(temp.name).resolve()

    def tree(self, *files):
        base = Path(tempfile.mkdtemp(dir=self.root))
        for name in files:
            (base / name).parent.mkdir(parents=True, exist_ok=True)
            (base / name).write_bytes(b"x")
        return base

    def test_layout_unwraps_single_folders(self):
        base = self.tree("Wrapper/Data Files/Meshes/a.nif", "Wrapper/Data Files/mod.esp")
        self.assertEqual(install_layout(base), (["Wrapper/Data Files"], ["Wrapper/Data Files"]))
        self.assertEqual(install_layout(self.tree("mod.esp")), (["."], ["."]))

    def test_layout_offers_option_folders_with_core_chosen(self):
        base = self.tree("Mod/00 Core/mod.esp", "Mod/01 Extra/textures/a.dds", "Mod/docs/r.txt")
        self.assertEqual(install_layout(base), (["Mod/00 Core", "Mod/01 Extra"], ["Mod/00 Core"]))
        self.assertEqual(install_layout(self.tree("Mod/readme.txt")), ([], []))

    def test_guess_release_reads_nexus_names(self):
        self.assertEqual(guess_release("Better Bodies-3880-2-2-1609876543.7z"),
                         ("Better Bodies", "2.2"))
        self.assertEqual(guess_release("Some_Mod.zip"), ("Some Mod", ""))
        self.assertEqual(guess_release("Folder 1.0"), ("Folder 1.0", ""))

    def test_nexus_id_from_page_or_download_name(self):
        self.assertEqual(nexus_id("https://www.nexusmods.com/morrowind/mods/45384?tab=files"),
                         45384)
        self.assertEqual(nexus_id(None, "D:/dl/Better Bodies-3880-2-2-1609876543.7z"), 3880)
        self.assertIsNone(nexus_id("https://www.tamriel-rebuilt.org/", "Some_Mod.zip"))

    def test_mod_page_and_description_round_trip(self):
        plain = self.tree("Mod/x.esp")
        catalog = discover_library(plain)
        catalog["mod"].update(url="https://example.org/mod", author="Me", summary="What it is")
        write_library(plain, catalog)
        self.assertEqual({key: load_library(plain)["mod"][key]
                          for key in ("url", "author", "summary")},
                         {"url": "https://example.org/mod", "author": "Me",
                          "summary": "What it is"})

    def test_install_merges_roots_in_order_and_rejects_unsafe_zips(self):
        base = self.tree("A/x.esp", "B/x.esp", "B/meshes/m.nif")
        (base / "B/x.esp").write_bytes(b"later")
        target = self.root / "installed"
        self.assertEqual(install_files(base, [("A", ["x.esp"]), ("B", ["x.esp", "meshes/m.nif"])],
                                       target), 3)
        self.assertEqual((target / "x.esp").read_bytes(), b"later")
        with self.assertRaises(LibraryError):
            install_files(base, [], target)
        archive = self.root / "bad.zip"
        with zipfile.ZipFile(archive, "w") as stream:
            stream.writestr("../evil.esp", "x")
        with self.assertRaises(LibraryError):
            extract_archive(archive, self.root / "out")
        self.assertFalse((self.root / "evil.esp").exists())


if __name__ == "__main__":
    unittest.main()
