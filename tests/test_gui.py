import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import tempfile
import tomllib
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_bsa import pc_bsa  # noqa: E402

try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QDialog, QInputDialog, QMessageBox
    from tes3x_gui import InstallDialog, LocalSettingsDialog, ProfileWindow
except ImportError:
    QApplication = None
    LocalSettingsDialog = None
    ProfileWindow = None


def plugin(path, masters=()):
    """A minimal TES3 plugin naming masters."""
    import struct
    body = b"".join(b"MAST" + struct.pack("<I", len(m) + 1) + m.encode() + b"\0"
                    + b"DATA" + struct.pack("<I", 8) + bytes(8) for m in masters)
    hedr = b"HEDR" + struct.pack("<I", 300) + bytes(300)
    data = hedr + body
    path.write_bytes(b"TES3" + struct.pack("<III", len(data), 0, 0) + data)


@unittest.skipIf(QApplication is None, "optional PySide6 dependency is not installed")
class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.library = library = self.root / "library"
        (library / "Mod/00 Core").mkdir(parents=True)
        plugin(library / "Mod/00 Core/mod.esp")
        (library / "Mod/00 Core/meshes").mkdir()
        (library / "Mod/00 Core/meshes/a.nif").write_bytes(b"a")
        (library / "Other").mkdir()
        plugin(library / "Other/other.esp", ["Morrowind.esm"])
        (library / "Other/meshes").mkdir()
        (library / "Other/meshes/a.nif").write_bytes(b"b")
        (library / "library.toml").write_text('''
schema = 1
[[mod]]
id = "mod"
name = "Mod"
[[mod.release]]
version = "1"
folder = "Mod"
default = true
roots = ["00 Core"]

[[mod]]
id = "other"
name = "Other"
[[mod.release]]
version = "unknown"
folder = "Other"
default = true
''', encoding="utf-8")
        self.profile = self.root / "profile.toml"
        self.profile.write_text(f'''
[profile]
name = "gui"
library = "{library.as_posix()}"

[[mods]]
id = "mod"
version = "1"
order = 10
''', encoding="utf-8")

    def window(self, path=None, **kwargs):
        window = ProfileWindow(path or self.profile, **kwargs)
        self.addCleanup(window.close)
        # Cleanups run last-first: drop unsaved changes so closing does not ask to save them.
        self.addCleanup(setattr, window, "document", None)
        window.refresh_analysis()
        return window

    def saved(self, path=None):
        with open(path or self.profile, "rb") as stream:
            return tomllib.load(stream)

    def row(self, window, name):
        return next(item for item in window.mod_rows() if item.text(0) == name)

    def test_lists_every_library_mod_and_saves_checked_ones(self):
        window = self.window()
        self.assertEqual(window.windowTitle(), "TES3X — profile")
        self.assertEqual(window.tabs.count(), 4)
        self.assertEqual([item.text(0) for item in window.mod_rows()], ["Mod", "Other"])
        self.assertEqual(self.row(window, "Other").text(1), "")
        self.assertEqual(self.row(window, "Mod").checkState(0), Qt.CheckState.Checked)
        self.assertEqual(self.row(window, "Other").checkState(0), Qt.CheckState.Unchecked)
        self.assertFalse(window.is_dirty())

        self.row(window, "Other").setCheckState(0, Qt.CheckState.Checked)
        window.refresh_analysis()
        self.assertEqual(self.row(window, "Other").text(2), "+1")
        self.assertEqual(self.row(window, "Mod").text(2), "-1")
        self.assertEqual(window.files_model.rowCount(), 3)
        self.assertTrue(window.save_profile())
        self.assertEqual([mod["id"] for mod in self.saved()["mods"]], ["mod", "other"])

        self.row(window, "Mod").setCheckState(0, Qt.CheckState.Unchecked)
        window.mod_list.move_items([self.row(window, "Other")], 0)
        self.assertTrue(window.save_profile())
        mods = self.saved()["mods"]
        self.assertEqual([mod["id"] for mod in mods], ["other", "mod"])
        self.assertFalse(mods[1]["enabled"])

    def test_plugins_panel_filters_and_orders_plugins(self):
        window = self.window()
        self.row(window, "Other").setCheckState(0, Qt.CheckState.Checked)
        window.refresh_analysis()
        rows = window.plugin_list.rows()
        self.assertEqual([item.text(0) for item in rows][3:], ["mod.esp", "other.esp"])
        self.assertEqual(rows[0].text(1), "Retail")
        self.assertEqual(rows[1].text(1), "Placeholder")
        self.assertEqual(rows[2].text(1), "Placeholder")
        self.assertIn("four-byte expansion placeholders", window.plugin_note.text())
        self.assertEqual(rows[4].text(2), "04")

        rows[3].setCheckState(0, Qt.CheckState.Unchecked)
        self.assertEqual(self.row(window, "Mod").data(0, 0x0100)["plugins"], [])
        window.plugin_list.move_items([window.plugin_list.rows()[4]], 3)
        self.assertEqual(window.plugin_order, ["other.esp", "mod.esp"])
        self.assertTrue(window.save_profile())
        saved = self.saved()
        self.assertEqual(saved["plugins"]["order"], ["other.esp", "mod.esp"])
        self.assertEqual(saved["mods"][0]["plugins"], [])

        window.mlox_at_build.setChecked(True)
        self.assertTrue(window.save_profile())
        saved = self.saved()
        self.assertNotIn("plugins", saved)
        self.assertEqual(saved["rules"]["plugin_order"], "mlox")

    def test_patches_are_a_checklist_and_carry_their_ini_keys(self):
        window = self.window()
        window.set_patch("rotating-autosaves", True)
        self.assertEqual(window.patch_configuration()["enable"], ["rotating-autosaves"])
        self.assertEqual(window.patch_items["rotating-autosaves"].text(2), "added")
        self.assertIn(("xbox", "autosaveslots"), window.ini.patch_keys)
        window.ini.set_value("Xbox:AutosaveSlots", 5)
        self.assertTrue(window.save_profile())
        self.assertEqual(self.saved()["ini"], {"Xbox:AutosaveSlots": 5})

        window.set_patch("rotating-autosaves", False)
        self.assertEqual(window.patch_configuration()["enable"], [])
        self.assertNotIn(("xbox", "autosaveslots"), window.ini.patch_keys)
        self.assertEqual(window.ini.values, {})
        window.set_patch("rotating-autosaves", True)
        self.assertEqual(window.ini.values, {"Xbox:AutosaveSlots": 5})

        # Turning off a patch the preset includes records a disable.
        window.set_patch("console", False)
        self.assertEqual(window.patch_configuration()["disable"], ["console"])
        window.set_patch("console", True)
        self.assertEqual(window.patch_configuration()["disable"], [])

    def test_ini_panel_shows_retail_values_and_saves_changes(self):
        vanilla = self.root / "vanilla"
        vanilla.mkdir()
        (vanilla / "Morrowind.ini").write_text(
            "[Game Files]\nGameFile0=Morrowind.esm\n[General]\nShow FPS=0\n", encoding="latin-1")
        config = self.root / "local.toml"
        config.write_text(f'[paths]\nvanilla_root = "{vanilla.as_posix()}"\n', encoding="utf-8")
        window = self.window(config=config)
        sections = [window.ini.tree.topLevelItem(i).text(0)
                    for i in range(window.ini.tree.topLevelItemCount())]
        self.assertIn("General", sections)
        self.assertNotIn("Game Files", sections)
        window.ini.set_value("general:show fps", 1)
        self.assertEqual(window.ini.values, {"General:Show FPS": 1})
        self.assertTrue(window.save_profile())
        self.assertEqual(self.saved()["ini"], {"General:Show FPS": 1})

    def test_archives_tab_shows_what_the_build_ships(self):
        pc_bsa(self.library / "Mod/00 Core/Mod.bsa", [("meshes\\x.nif", b"x")])
        window = self.window()
        rows = lambda: [[window.archives.topLevelItem(i).text(c) for c in range(3)]
                        for i in range(window.archives.topLevelItemCount())]
        self.assertEqual(rows(), [["Morrowind.bsa", "Retail", "yes"],
                                  ["tes3xmods.bsa", "TES3X build: 1 mod files",
                                   "yes, after Morrowind.bsa"],
                                  ["Mod.bsa", "Mod", "unpacked"]])
        window.build.select(window.build.mode, "loose")
        window.toggle_archives(self.row(window, "Mod"))
        window.refresh_analysis()
        self.assertEqual(rows(), [["Morrowind.bsa", "Retail", "yes"],
                                  ["Mod.bsa", "Mod", "yes, via multi-bsa"]])
        self.assertEqual(window.patch_items["multi-bsa"].text(2), "Required")
        self.assertTrue(window.save_profile())
        self.assertEqual(self.saved()["mods"][0]["archives"], "load")

    def test_xbox_column_shows_catalog_verdicts_and_unmet_patches(self):
        window = self.window()
        other = self.row(window, "Other")
        self.assertEqual(other.text(window.MOD_XBOX), "?")
        window.compat = {"mod": {"id": "mod", "name": "Mod", "folder": "Mod",
                                 "status": "works-with-requirements", "patches": ["dxt5-size"]}}
        window.refresh_patch_states()
        mod = self.row(window, "Mod")
        self.assertEqual(mod.text(window.MOD_XBOX), "*")
        self.assertIn("Turn on in Patches: dxt5-size", mod.toolTip(window.MOD_XBOX))
        window.set_patch("dxt5-size", True)
        self.assertNotIn("Turn on", mod.toolTip(window.MOD_XBOX))

        # Ticking a mod turns on the patches it needs.
        window.set_patch("dxt5-size", False)
        window.compat["other"] = {"id": "other", "name": "Other", "folder": "Other",
                                  "status": "works-with-requirements", "patches": ["mcp-154"]}
        self.row(window, "Other").setCheckState(0, Qt.CheckState.Checked)
        self.assertEqual(window.patch_configuration()["enable"], ["mcp-154"])

    def test_build_tab_writes_profile_settings(self):
        window = self.window()
        self.assertTrue(window.save_profile())
        untouched = self.saved()
        # Defaults the user never set stay out of the file.
        self.assertNotIn("package", untouched)
        self.assertNotIn("rules", untouched)
        self.assertEqual(set(untouched["profile"]), {"name", "library"})

        build = window.build
        build.title.setText("Modded")
        build.select(build.mode, "loose")
        build.archive_only.setChecked(True)
        build.select(build.invert_look, True)
        window.toggle_flag(self.row(window, "Mod"), "loose")
        self.assertTrue(window.is_dirty())
        self.assertTrue(window.save_profile())
        saved = self.saved()
        self.assertEqual(saved["profile"]["title"], "Modded")
        self.assertEqual(saved["package"], {"mode": "loose"})
        self.assertEqual(saved["preferences"], {"invert_look": True})
        self.assertTrue(saved["mods"][0]["loose"])

    def test_opens_without_arguments_and_switches_profiles(self):
        profiles = self.root / "profiles"
        profiles.mkdir()
        (profiles / "b.toml").write_text('[profile]\nname = "b"\n', encoding="utf-8")
        (profiles / "a.toml").write_text('[profile]\nname = "a"\n', encoding="utf-8")
        config = self.root / "local.toml"
        config.write_text("", encoding="utf-8")
        window = ProfileWindow(config=config)
        self.addCleanup(window.close)
        self.assertEqual(window.profile_path, (profiles / "a.toml").resolve())
        self.assertEqual([window.profile_picker.itemText(i)
                          for i in range(window.profile_picker.count())], ["a", "b"])
        self.assertFalse(window.is_dirty())

        window.patch_preset.setCurrentText("minimal")
        self.assertTrue(window.is_dirty())
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Save):
            window.picker_activated(1)
        self.assertEqual(window.profile_path, (profiles / "b.toml").resolve())
        self.assertEqual(self.saved(profiles / "a.toml")["patches"]["preset"], "minimal")

        with patch.object(QInputDialog, "getText", return_value=("c", True)):
            window.new_profile()
        created = self.saved(profiles / "c.toml")
        self.assertEqual(created["profile"]["name"], "c")
        self.assertNotIn("mods", created)

        with patch.object(QInputDialog, "getText", return_value=("d", True)):
            window.rename_profile()
        self.assertFalse((profiles / "c.toml").exists())
        self.assertEqual(window.profile_path, (profiles / "d.toml").resolve())

    def test_folder_name_profiles_work_with_or_without_an_index(self):
        plain = self.root / "plain"
        (plain / "Folder Mod").mkdir(parents=True)
        plugin(plain / "Folder Mod" / "folder.esp")
        (plain / "Other").mkdir()
        profile = self.root / "folders.toml"
        profile.write_text(f'[profile]\nname = "folders"\nlibrary = "{plain.as_posix()}"\n\n'
                           '[[mods]]\nname = "Folder Mod"\norder = 10\n', encoding="utf-8")
        window = self.window(profile)
        self.assertEqual([item.text(0) for item in window.mod_rows()], ["Folder Mod", "Other"])
        self.assertEqual(window.plugin_list.topLevelItemCount(), 4)
        self.assertFalse(window.is_dirty())

        self.row(window, "Other").setCheckState(0, Qt.CheckState.Checked)
        self.assertTrue(window.save_profile())
        self.assertEqual([mod["name"] for mod in self.saved(profile)["mods"]],
                         ["Folder Mod", "Other"])

        window.write_library_index()
        self.assertTrue((plain / "library.toml").is_file())
        with patch.object(QMessageBox, "information"):
            window.convert_to_ids()
        self.assertEqual([mod["id"] for mod in self.saved(profile)["mods"]],
                         ["folder-mod", "other"])
        self.assertEqual(len(window.mod_rows()), 2)

    def test_installs_an_archive_with_option_folders(self):
        archive = self.root / "Travel Mod-1234-1-2-1700000000.zip"
        with zipfile.ZipFile(archive, "w") as stream:
            stream.writestr("Travel Mod/00 Core/travel.esp", "TES3")
            stream.writestr("Travel Mod/00 Core/meshes/t.nif", "t")
            stream.writestr("Travel Mod/01 Music/music/m.mp3", "m")
            stream.writestr("Travel Mod/readme.txt", "r")
        window = self.window()
        seen = {}

        def accept(dialog):
            seen["name"], seen["version"] = dialog.name(), dialog.version()
            seen["roots"] = list(dialog.roots)
            dialog.items["Travel Mod/01 Music"].setCheckState(0, Qt.CheckState.Checked)
            dialog.items["Travel Mod/00 Core/meshes/t.nif"].setCheckState(
                0, Qt.CheckState.Unchecked)
            return QDialog.DialogCode.Accepted

        with patch.object(InstallDialog, "exec", accept):
            window.install_paths([str(archive)])
        self.assertEqual(seen, {"name": "Travel Mod", "version": "1.2",
                                "roots": ["Travel Mod/00 Core", "Travel Mod/01 Music"]})
        installed = self.library / "Travel Mod"
        self.assertEqual(sorted(path.relative_to(installed).as_posix()
                                for path in installed.rglob("*") if path.is_file()),
                         ["music/m.mp3", "travel.esp"])
        self.assertEqual(list(self.library.glob(".tes3x-install-*")), [])
        catalog = tomllib.loads((self.library / "library.toml").read_text(encoding="utf-8"))
        release = catalog["mod"][-1]["release"][0]
        self.assertEqual((catalog["mod"][-1]["id"], release["version"], release["source"]),
                         ("travel-mod", "1.2", str(archive)))
        row = self.row(window, "Travel Mod")
        self.assertEqual(row.checkState(0), Qt.CheckState.Unchecked)
        self.assertTrue(row.isSelected())

    def test_mod_details_show_page_description_and_plugins(self):
        import struct
        hedr = (struct.pack("<fI", 1.3, 0) + b"Someone".ljust(32, b"\0")
                + b"Adds a thing".ljust(256, b"\0") + struct.pack("<I", 0))
        data = b"HEDR" + struct.pack("<I", len(hedr)) + hedr
        (self.library / "Other/other.esp").write_bytes(
            b"TES3" + struct.pack("<III", len(data), 0, 0) + data)
        window = self.window()
        page = "https://www.nexusmods.com/morrowind/mods/123"
        window.compat = {"other": {"id": "other", "name": "Other", "folder": "Other",
                                   "status": "works", "url": page}}
        with patch.object(window, "nexus_lookup") as lookup:
            self.row(window, "Other").setSelected(True)
        self.assertEqual(lookup.call_args.args[0], "id:123")
        shown = window.mod_info.toPlainText()
        for text in ("Other", "Someone", "Adds a thing", page, "Find on Nexus"):
            self.assertIn(text, shown)

        window.nexus_finished("id:123", {"id": 123, "name": "Other", "summary": "From Nexus",
                                         "author": "Author", "version": "1", "url": page}, None)
        self.assertIn("From Nexus", window.mod_info.toPlainText())
        with open(self.library / "library.toml", "rb") as stream:
            other = next(mod for mod in tomllib.load(stream)["mod"] if mod["id"] == "other")
        self.assertEqual((other["url"], other["summary"], other["author"]),
                         (page, "From Nexus", "Author"))

    def test_play_builds_first_then_boots_the_build(self):
        import hashlib
        import json
        config = self.root / "local.toml"
        config.write_text('[paths]\nbuild_root = "out"\n', encoding="utf-8")
        window = self.window(config=config)
        self.assertEqual(window.action_settings.text(), "&Settings…")
        self.assertEqual(window.build_status()[0], "missing")

        def start(program, arguments, *_args):
            window.process = "running"
            calls.append((Path(program).name, arguments))

        calls = []
        with patch.object(window, "start_command", side_effect=start):
            window.play()
            self.assertEqual(calls[0][0], "tes3x_pipeline.py")
            output = self.root / "out" / "gui"
            (output / "deploy").mkdir(parents=True)
            (output / ".tes3x-pipeline.json").write_text(json.dumps(
                {"profile_sha256": hashlib.sha256(self.profile.read_bytes()).hexdigest()}),
                encoding="utf-8")
            window.process = None
            window.command_finished(0, None)
        self.assertEqual(window.build_status()[0], "built")
        self.assertEqual(calls[1][0], "tes3x_xemu.py")
        self.assertEqual(calls[1][1][1:], ["--deploy", str(output / "deploy"), "--keep-iso", "--disk",
                                           str(self.root / "build/play/profile/hdd.qcow2")])

        self.profile.write_text(self.profile.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        self.assertEqual(window.build_status()[0], "stale")

    def test_local_settings_dialog_preserves_xemu_and_writes_public_fields(self):
        config = self.root / "local.toml"
        config.write_text('[xemu]\nexe = "xemu.exe"\ncustom = "keep"\n', encoding="utf-8")
        dialog = LocalSettingsDialog(config)
        self.addCleanup(dialog.close)
        dialog.fields["paths.mod_library"].setText("D:/Mods")
        dialog.fields["deploy.host"].setText("192.0.2.5")
        dialog.fields["deploy.port"].setValue(2121)
        self.assertTrue(dialog.save_settings())
        with open(config, "rb") as stream:
            values = tomllib.load(stream)
        self.assertEqual(values["paths"]["mod_library"], "D:/Mods")
        self.assertEqual(values["deploy"]["host"], "192.0.2.5")
        self.assertEqual(values["deploy"]["port"], 2121)
        self.assertEqual(values["xemu"]["custom"], "keep")


if __name__ == "__main__":
    unittest.main()
