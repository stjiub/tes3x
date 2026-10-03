import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json
import signal
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
import tes3x_saves as saves_tool  # noqa: E402

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
        self.assertTrue(window.windowTitle().startswith("TES3X "))
        self.assertEqual([window.tabs.tabText(index) for index in range(window.tabs.count())],
                         ["Mods", "Plugins", "Archives", "Data Files", "Patches", "INI",
                          "Resources", "Saves", "Build"])
        self.assertEqual(window.counts.contentsMargins().right(), 8)
        self.assertEqual(window.details_container.layout().contentsMargins().top(),
                         window.tabs.tabBar().sizeHint().height())
        self.assertEqual(window.menuBar().font(), window.tabs.tabBar().font())
        self.assertTrue(window.statusBar().spinner.isHidden())
        for action, colour in ((window.action_build, "#1976d2"),
                               (window.action_deploy, "#d97706"),
                               (window.action_play, "#2e7d32")):
            self.assertFalse(action.icon().isNull())
            self.assertEqual(action.property("accentColour"), colour)
        budget = window.resource_budget.topLevelItem(0)
        self.assertEqual((budget.text(0), budget.text(1)), ("Mod", "2"))
        self.assertIs(window.details_stack.widget(0), window.mod_details)
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

        file_row = next(index for index, value in enumerate(window.files_model.entries)
                        if value[0] == "meshes/a.nif")
        window.tabs.setCurrentIndex(3)
        window.files_view.selectRow(window.files_filter.mapFromSource(
            window.files_model.index(file_row, 0)).row())
        details = window.context_info.toPlainText()
        self.assertIn("Build result: Packed in tes3xmods.bsa", details)
        self.assertIn("1. Mod — overridden", details)
        self.assertIn("2. Other — included", details)

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

        dependencies = [window.resource_dependencies.topLevelItem(i)
                        for i in range(window.resource_dependencies.topLevelItemCount())]
        other_master = next(item for item in dependencies if item.text(1) == "other.esp")
        self.assertEqual((other_master.text(2), other_master.text(3)),
                         ("Morrowind.esm", "Present"))

        window.tabs.setCurrentIndex(1)
        rows[4].setSelected(True)
        self.assertIn("Provided by: Other", window.context_info.toPlainText())
        self.assertIn("Masters: Morrowind.esm", window.context_info.toPlainText())

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
        window.tabs.setCurrentIndex(4)
        self.assertEqual(window.patch_items["mcp-92"].text(0),
                         "Summoned creature crash fix")
        self.assertEqual(window.patch_items["mcp-92"].text(1), "mcp-92")
        window.patch_tree.setCurrentItem(window.patch_items["rotating-autosaves"])
        self.assertIn("Rotate automatic saves", window.context_info.toPlainText())
        window.set_patch("rotating-autosaves", True)
        self.assertEqual(window.patch_configuration()["enable"], ["rotating-autosaves"])
        self.assertEqual(window.patch_items["rotating-autosaves"].text(3), "Profile")
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
        window.patch_preset.setCurrentText("testing")
        window.set_patch("console", False)
        self.assertEqual(window.patch_configuration()["disable"], ["console"])
        window.set_patch("console", True)
        self.assertEqual(window.patch_configuration()["disable"], [])

    def test_developer_mode_allows_selecting_visible_dev_patches(self):
        class Settings:
            def __init__(self):
                self.values = {}

            def value(self, key, default=None, *_args):
                return self.values.get(key, default)

            def setValue(self, key, value):
                self.values[key] = value

        settings = Settings()
        window = self.window(settings=settings)
        heap = window.patch_items["heap-census"]
        profiler = window.patch_items["profile"]
        transition = window.patch_items["transition-autosaves"]
        self.assertFalse(heap.isHidden())
        self.assertFalse(profiler.isHidden())
        self.assertFalse(transition.isHidden())
        self.assertFalse(transition.flags() & Qt.ItemFlag.ItemIsUserCheckable)
        self.assertFalse(profiler.flags() & Qt.ItemFlag.ItemIsUserCheckable)
        self.assertEqual(heap.text(2), "dev")
        self.assertEqual(window.patch_tree.headerItem().text(3), "Included by")

        window.action_developer_mode.setChecked(True)
        self.assertFalse(heap.isHidden())
        self.assertTrue(transition.flags() & Qt.ItemFlag.ItemIsUserCheckable)
        self.assertEqual(heap.text(3), "Build option")
        window.set_patch("mwse-legacy", True)
        script_ext = window.patch_items["script-ext"]
        self.assertFalse(script_ext.flags() & Qt.ItemFlag.ItemIsUserCheckable)
        self.assertEqual(script_ext.text(3), "mwse-legacy")
        self.assertTrue(settings.values["developer_mode"])

    def test_preview_patch_is_visible_without_developer_mode(self):
        window = self.window()
        patch = window.patch_items["rotating-autosaves"]
        self.assertFalse(patch.isHidden())
        window.set_patch("rotating-autosaves", True)
        self.assertFalse(patch.isHidden())

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
        self.assertEqual(window.patch_items["multi-bsa"].text(3), "Delta-BSA packaging")
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
        build.install_dir.setText("MorrowindModded")
        build.select(build.install_layout, "overlay")
        self.assertEqual(window.patch_items["data-overlay"].text(3), "Overlay install layout")
        self.assertFalse(window.patch_items["data-overlay"].flags()
                         & Qt.ItemFlag.ItemIsUserCheckable)
        build.select(build.mode, "loose")
        build.archive_only.setChecked(True)
        build.select(build.invert_look, True)
        window.toggle_flag(self.row(window, "Mod"), "loose")
        window.refresh_analysis()
        window.tabs.setCurrentIndex(
            [window.tabs.tabText(i) for i in range(window.tabs.count())].index("Build"))
        preview = window.context_info.toPlainText()
        self.assertIn("Dashboard title: Modded", preview)
        self.assertIn("Install layout: Shared retail base", preview)
        self.assertIn("Packaging: Loose files", preview)
        self.assertIn("Mods: 1 active", preview)
        self.assertTrue(window.is_dirty())
        self.assertTrue(window.save_profile())
        saved = self.saved()
        self.assertEqual(saved["profile"]["title"], "Modded")
        self.assertEqual(saved["profile"]["install_dir"], "MorrowindModded")
        self.assertEqual(saved["profile"]["install_layout"], "overlay")
        self.assertEqual(saved["package"], {"mode": "loose"})
        self.assertEqual(saved["preferences"], {"invert_look": True})
        self.assertTrue(saved["mods"][0]["loose"])

    def test_skip_intro_writes_movie_keys(self):
        window = self.window()
        window.build.skip_intro.setChecked(True)
        self.assertTrue(window.save_profile())
        self.assertEqual(self.saved()["ini"], {"Movies:Morrowind Logo": "none.bik",
                                               "Movies:New Game": "none.bik"})

        # Resetting one key in the INI tab clears the box; unticking drops the other.
        del window.ini.values["Movies:New Game"]
        window.ini.changed.emit()
        self.assertFalse(window.build.skip_intro.isChecked())
        window.build.skip_intro.setChecked(True)
        window.build.skip_intro.setChecked(False)
        self.assertTrue(window.save_profile())
        self.assertNotIn("ini", self.saved())

        window.ini.set_value("Movies:New Game", "none.bik")
        window.ini.set_value("Movies:Morrowind Logo", "NONE.BIK")
        self.assertTrue(window.build.skip_intro.isChecked())

    def test_build_tab_can_reset_to_defaults(self):
        config = self.root / "local.toml"
        config.write_text(f'[paths]\nmod_library = "{self.library.as_posix()}"\n',
                          encoding="utf-8")
        window = self.window(config=config)
        build = window.build
        build.title.setText("Modded")
        build.remote_root.setText("F:/Games/Test")
        build.dashboard.setChecked(False)
        build.select(build.mode, "loose")
        build.archive_name.setText("custom.bsa")
        build.archive_only.setChecked(True)
        build.drive_letter.setCurrentText("F")
        build.loose_assets.setPlainText("textures/*")
        build.select(build.max_texture_size, 1024)
        build.convert_all.setChecked(True)
        build.max_filename.setValue(20)
        build.clear_cache.setChecked(True)
        build.keep_assets.setPlainText("meshes/*")
        build.exclude.setPlainText("music")
        build.select(build.invert_look, True)

        build.reset_button.click()

        self.assertEqual(build.title.text(), "")
        self.assertEqual(build.remote_root.text(), "")
        self.assertEqual(build.library(), "")
        self.assertTrue(build.dashboard.isChecked())
        self.assertEqual(build.mode.currentData(), "delta-bsa")
        self.assertEqual(build.archive_name.text(), "")
        self.assertFalse(build.archive_only.isChecked())
        self.assertEqual(build.drive_letter.currentText(), "D")
        self.assertEqual(build.lines(build.loose_assets), [])
        self.assertEqual(build.max_texture_size.currentData(), 512)
        self.assertFalse(build.convert_all.isChecked())
        self.assertEqual(build.max_filename.value(), 42)
        self.assertFalse(build.clear_cache.isChecked())
        self.assertEqual(build.lines(build.keep_assets), [])
        self.assertEqual(build.lines(build.exclude), [])
        self.assertIsNone(build.invert_look.currentData())
        self.assertTrue(window.is_dirty())

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
        for text in ("Other", page, "Find on Nexus", "Xbox compatibility: Confirmed"):
            self.assertIn(text, shown)
        self.assertIn("1 plugin · 0 archives · 1 other file", window.mod_contents_summary.text())
        plugin_row = window.mod_plugins.topLevelItem(0)
        self.assertEqual((plugin_row.text(0), plugin_row.text(1)),
                         ("other.esp", "Not active"))
        self.assertIn("Someone", plugin_row.text(2))
        self.assertIn("Adds a thing", plugin_row.text(2))

        window.nexus_finished("id:123", {"id": 123, "name": "Other", "summary": "From Nexus",
                                         "author": "Author", "version": "1", "url": page}, None)
        self.assertIn("From Nexus", window.mod_info.toPlainText())
        with open(self.library / "library.toml", "rb") as stream:
            other = next(mod for mod in tomllib.load(stream)["mod"] if mod["id"] == "other")
        self.assertEqual((other["url"], other["summary"], other["author"]),
                         (page, "From Nexus", "Author"))

    @patch("tes3x_gui.running_xemu", return_value=[])
    def test_play_builds_first_then_boots_the_build(self, _running):
        import hashlib
        import json
        config = self.root / "local.toml"
        config.write_text('default_target = "xemu"\n[targets.xemu]\nkind = "xemu"\n'
                          'ram = 64\nexe = "xemu.exe"\n[targets.xemu-128]\nkind = "xemu"\n'
                          'ram = 128\nexe = "xemu-new.exe"\n[paths]\nbuild_root = "out"\n',
                          encoding="utf-8")
        window = self.window(config=config)
        self.assertEqual(window.action_settings.text(), "&Settings…")
        self.assertEqual(window.build_status()[0], "missing")
        self.assertEqual(window.build_button.property("state"), "idle")
        self.assertEqual(window.check_button.property("state"), "idle")
        self.assertEqual(window.deploy_button.property("state"), "idle")
        self.assertFalse(window.action_deploy.isEnabled())
        self.assertEqual(window.target_picker.currentData(), "xemu")
        self.assertEqual([button.text() for button in
                          [*window.command_buttons, window.play_button]],
                         ["Check", "Build", "Deploy", "Play"])

        def start(program, arguments, *_args):
            window.process = "running"
            calls.append((Path(program).name, arguments))

        def start_play(program, arguments, *_args):
            window.play_process = "running"
            calls.append((Path(program).name, arguments))

        calls = []
        with patch.object(window, "start_command", side_effect=start), \
                patch.object(window, "start_play_process", side_effect=start_play):
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
        self.assertEqual(window.build_button.property("state"), "current")
        self.assertEqual(calls[1][0], "tes3x_xemu.py")
        self.assertEqual(calls[1][1][1:], ["--deploy", str(output / "deploy"), "--keep-iso",
                                           "--target", "xemu", "--config", str(config), "--disk",
                                           str(self.root / "build/play/profile/hdd.qcow2")])

        self.assertTrue(calls[1][1][0].startswith("play-profile-xemu-"))

        # RAM comes from the selected target; a 128 MB target needs its BIOS set.
        window.play_process = None
        window.target_picker.setCurrentIndex(window.target_picker.findData("xemu-128"))
        window.target_selection_changed()
        with patch.object(window, "error") as error:
            window.play()
        self.assertIn("128 MB BIOS", error.call_args.args[0])
        config.write_text(config.read_text(encoding="utf-8").replace(
            'ram = 128\nexe = "xemu-new.exe"',
            'ram = 128\nexe = "xemu-new.exe"\nbios_128mb = "cerbios.bin"'), encoding="utf-8")
        window.process = None
        window.play_process = None
        window.refresh_play_menu()
        self.assertTrue(window.action_play.isEnabled())
        with patch.object(window, "start_play_process", side_effect=start_play):
            window.play()
        self.assertEqual(calls[2][1][calls[2][1].index("--target") + 1], "xemu-128")
        self.assertEqual(window.play_button.text(), "Play")
        with patch.object(window, "error") as error:
            window.run_pipeline([])
        self.assertIn("Stop xemu", error.call_args.args[0])

        # Debug with GDB opens the stub; the transient status names the runner's port.
        window.play_process = None
        window.set_play_gdb()
        with patch.object(window, "start_play_process", side_effect=start_play):
            window.play()
        self.assertIn("--gdb", calls[3][1])
        window.play_run.mkdir(parents=True)
        (window.play_run / "gdb.port").write_text("1234")
        window.play_process = type("Process", (), {"readAllStandardOutput": lambda self:
                                                   b"xemu: started, pid 1\n"})()
        window.append_play_output()
        self.assertEqual(window.statusBar().message.text(), "Playing · GDB :1234")
        self.assertEqual(window.play_pid, 1)
        with patch("tes3x_gui.os.kill") as kill:
            window.stop_play()
        kill.assert_called_once_with(1, signal.SIGTERM)
        window.play_process = None
        window.play_pid = None
        window.set_play_gdb()

        self.profile.write_text(self.profile.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        self.assertEqual(window.build_status()[0], "stale")

    def test_check_deploy_and_target_status_show_progress_and_results(self):
        config = self.root / "local.toml"
        config.write_text('default_target = "bench"\n[targets.bench]\nkind = "xbox"\n'
                          'host = "192.0.2.5"\ngames_root = "F:/Games"\n'
                          '[targets.spare]\nkind = "xbox"\nhost = "192.0.2.6"\n'
                          'games_root = "E:/Games"\n', encoding="utf-8")
        window = self.window(config=config)
        with patch.object(window, "start_command"):
            window.run_pipeline(["--check"])
        window.update_check_state()
        self.assertEqual(window.check_button.property("state"), "running")
        window.command_finished(0, None)
        self.assertEqual(window.check_button.property("state"), "current")
        check_record = json.loads((window.build_output() / ".tes3x-check.json").read_text())
        self.assertEqual(check_record["result"], "pass")

        window.command_kind = "check"
        window.command_finished(1, None)
        self.assertEqual(window.check_button.property("state"), "failed")
        window.target_states["bench"] = "checking"
        window.refresh_target_item("bench")
        self.assertIn("Checking connection", window.target_picker.toolTip())
        window.target_states["bench"] = "connected"
        window.refresh_target_item("bench")
        self.assertIn("Connected", window.target_picker.toolTip())

        window.build_output().mkdir(parents=True, exist_ok=True)
        (window.build_output() / ".tes3x-pipeline.json").write_text('{"build": true}\n')
        window.command_kind = "deploy"
        window.command_target = "bench"
        window.update_build_state()
        self.assertEqual(window.deploy_button.property("state"), "running")
        window.command_finished(0, None)
        self.assertEqual(window.deploy_button.property("state"), "current")
        deploys = json.loads((window.build_output() / ".tes3x-deploys.json").read_text())
        self.assertIn("bench", deploys)
        window.target_picker.setCurrentIndex(window.target_picker.findData("spare"))
        window.target_selection_changed()
        self.assertEqual(window.deploy_button.property("state"), "stale")
        window.target_picker.setCurrentIndex(window.target_picker.findData("bench"))
        window.target_selection_changed()
        self.assertEqual(window.deploy_button.property("state"), "current")
        reopened = self.window(config=config)
        self.assertEqual(reopened.check_button.property("state"), "failed")
        self.assertEqual(reopened.deploy_button.property("state"), "current")
        self.profile.write_text(self.profile.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        window.update_build_state()
        self.assertEqual(window.deploy_button.property("state"), "stale")

        with patch("tes3x_gui.QProcess"):
            window.start_command("tool.py", [], "Working…")
        self.assertFalse(window.statusBar().spinner.isHidden())
        window.process.readAllStandardOutput.return_value = b"xemu: started, pid 1\n"
        window.append_process_output()
        self.assertTrue(window.statusBar().spinner.isHidden())
        window.process = None
        window.command_finished(0, None)

    def test_xbox_addon_deploys_then_starts_the_build(self):
        import hashlib
        import json
        config = self.root / "local.toml"
        config.write_text('[paths]\nbuild_root = "out"\n[deploy]\nhost = "192.0.2.5"\n'
                          'remote_root = "F:/Games/Test"\n', encoding="utf-8")
        window = self.window(config=config)
        self.assertFalse(window.action_play.isEnabled())

        dialog = LocalSettingsDialog(config)
        self.addCleanup(dialog.close)
        dialog.fields["addons.console"].setChecked(True)
        self.assertTrue(dialog.save_settings())
        with open(config, "rb") as stream:
            self.assertEqual(tomllib.load(stream)["addons"], {"console": True})
        window.refresh_targets()
        self.assertTrue(window.action_play.isEnabled())

        self.assertTrue(window.save_profile())
        output = self.root / "out" / "gui"
        (output / "deploy").mkdir(parents=True)
        (output / ".tes3x-pipeline.json").write_text(json.dumps(
            {"profile_sha256": hashlib.sha256(self.profile.read_bytes()).hexdigest()}),
            encoding="utf-8")
        calls = []

        def start(program, arguments, message, *_args, clear=True):
            window.process = "running"
            calls.append((Path(program).name, arguments[0], clear))

        with patch.object(window, "start_command", side_effect=start), \
                patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            window.play()
            for _ in range(2):
                window.process = None
                window.command_finished(0, None)
        self.assertEqual(calls, [("console.py", "ping", True),
                                 ("tes3x_deploy.py", str(output / "deploy"), False),
                                 ("console.py", "run", False)])
        self.assertEqual(window.play_button.text(), "Play")

        # A failed step stops the rest.
        window.process = None
        calls.clear()
        with patch.object(window, "start_command", side_effect=start),                 patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            window.play()
            window.process = None
            window.command_finished(1, None)
        self.assertEqual([call[1] for call in calls], ["ping"])

        # A deploy that stops on a conflict is asked about, then repeated with --replace.
        window.process = None
        calls.clear()
        full = []

        def start_full(program, arguments, message, *_args, clear=True):
            window.process = "running"
            full.append(arguments)

        warning = patch.object(QMessageBox, "warning", return_value=QMessageBox.StandardButton.Yes)
        with patch.object(window, "start_command", side_effect=start_full), warning as asked:
            window.play()
            window.process = None
            window.command_finished(0, None)
            window.output.setPlainText("  conflict: F:/Games/Test holds 3 files (1.0 KB) that "
                                       "TES3X did not deploy\n")
            window.process = None
            window.command_finished(3, None)
            self.assertIn("F:/Games/Test holds 3 files", asked.call_args[0][2])
            window.process = None
            window.command_finished(0, None)
        self.assertEqual([args[0] for args in full],
                         ["ping", str(output / "deploy"), str(output / "deploy"), "run"])
        self.assertNotIn("--replace", full[1])
        self.assertIn("--replace", full[2])

        dialog.fields["addons.console"].setChecked(False)
        self.assertTrue(dialog.save_settings())
        with open(config, "rb") as stream:
            self.assertNotIn("addons", tomllib.load(stream))

    def test_saves_tab_picks_a_pool_and_checks_saves_against_the_profile(self):
        config = self.root / "local.toml"
        config.write_text("[paths]\n", encoding="utf-8")
        window = self.window(config=config)
        self.assertEqual(window.current_pool(), (0x42530005, None))
        with patch.object(QInputDialog, "getText", return_value=("TR test", True)):
            window.new_pool()
        value, name = window.current_pool()
        self.assertEqual((value >> 16, name), (0x5433, "TR test"))
        self.assertTrue(window.save_profile())
        self.assertEqual(self.saved()["profile"]["save_pool"], "TR test")
        self.assertEqual(self.saved()["profile"]["save_pool_id"], f"{value:08X}")
        self.assertIn(f"E:\\UDATA\\{value:08X}", window.pool_note.text())

        saves = [{"source": "pc", "folder": "A", "name": "fits", "size": 1 << 20,
                  "masters": ["Morrowind.esm", "mod.esp"]},
                 {"source": "xemu", "folder": "B", "name": "lacks", "size": 1 << 20,
                  "masters": ["Morrowind.esm", "gone.esp"]},
                 {"source": "xbox", "folder": "C", "name": "reordered", "size": 1 << 20,
                  "masters": ["mod.esp", "Morrowind.esm"]}]
        window.populate_saves(saves)
        fits = {window.save_list.topLevelItem(i).text(0):
                window.save_list.topLevelItem(i).text(window.SAVE_FIT)
                for i in range(window.save_list.topLevelItemCount())}
        self.assertEqual(fits, {"fits": "Compatible", "lacks": "Missing gone.esp",
                                "reordered": "Compatible"})
        self.assertIn("1 of 3 saves need plugins", window.pool_note.text())

        steps = window.push_steps(saves)
        self.assertEqual([arguments[0] for _script, arguments, _message in steps],
                         ["pull", "push"])
        self.assertIn("xemu", steps[0][1])
        push = steps[1][1]
        self.assertEqual(push[1:3], ["A", "B"])
        self.assertEqual(push[push.index("--pool-name") + 1], "TR test")

        # Each save changes pool where it is: on the Xbox, on the PC or on the xemu disk.
        moves = window.transfer_steps(saves, 0x42530005, None, True)
        self.assertEqual([arguments[arguments.index("--where") + 1] for _s, arguments, _m in moves],
                         ["xbox", "pc", "xemu"])
        self.assertTrue(all("--move" in arguments for _s, arguments, _m in moves))
        copies = window.transfer_steps(saves, 0x42530005, None, False)
        self.assertEqual(len(copies), 3)

        # Back to the shared pool: the new pool stays on the list to choose again.
        window.pool_chosen(window.pool_combo.findData(0x42530005))
        self.assertTrue(window.save_profile())
        self.assertNotIn("save_pool", self.saved()["profile"])
        self.assertNotEqual(window.pool_combo.findData(value), -1)
        window.pool_chosen(window.pool_combo.findData(value))
        self.assertEqual(window.current_pool(), (value, "TR test"))

        # The Xbox's last listing of a pool shows until the Xbox answers again.
        saves_tool.write_index(window.save_library(), {
            "pools": {f"{value:08X}": "TR test"},
            "xbox": {f"{value:08X}": {"time": "2026-09-28 20:00", "saves": saves[2:]}}})
        window.xbox_listing = None
        window.refresh_saves(False)
        self.assertEqual(window.save_list.topLevelItemCount(), 1)
        self.assertIn("listed 2026-09-28 20:00", window.saves_status.text())

    def test_local_settings_dialog_preserves_xemu_and_writes_public_fields(self):
        config = self.root / "local.toml"
        config.write_text('# keep this comment\n[xemu]\nexe = "xemu.exe"\ncustom = "keep"\n',
                          encoding="utf-8")
        dialog = LocalSettingsDialog(config)
        self.addCleanup(dialog.close)
        self.assertEqual([dialog.categories.item(row).text()
                          for row in range(dialog.categories.count())],
                         ["Paths", "Targets", "Add-ons"])
        self.assertEqual(dialog.target_values["xemu"]["exe"], "xemu.exe")
        dialog.duplicate_target("xemu-new")
        dialog.xemu_target_fields["exe"].setText("D:/xemu-new/xemu.exe")
        dialog.xemu_target_fields["bios"].setText("D:/xemu-new/bios.bin")
        dialog.fields["paths.mod_library"].setText("D:/Mods")
        dialog.add_target("bench")
        dialog.target_host.setText("192.0.2.5")
        dialog.target_games_root.setText("F:/Games")
        self.assertIn("F:/Games/MorrowindRetail", dialog.target_retail_root.placeholderText())
        dialog.target_retail_root.setText("F:/Games/MorrowindRetail")
        dialog.target_port.setValue(2121)
        self.assertTrue(dialog.save_settings())
        with open(config, "rb") as stream:
            values = tomllib.load(stream)
        self.assertEqual(values["paths"]["mod_library"], "D:/Mods")
        self.assertEqual(values["default_target"], "bench")
        self.assertEqual(values["targets"]["bench"]["host"], "192.0.2.5")
        self.assertEqual(values["targets"]["bench"]["games_root"], "F:/Games")
        self.assertEqual(values["targets"]["bench"]["retail_root"],
                         "F:/Games/MorrowindRetail")
        self.assertEqual(values["targets"]["bench"]["port"], 2121)
        self.assertEqual(values["targets"]["xemu"]["exe"], "xemu.exe")
        self.assertEqual(values["targets"]["xemu-new"]["exe"], "D:/xemu-new/xemu.exe")
        self.assertEqual(values["targets"]["xemu-new"]["bios"], "D:/xemu-new/bios.bin")
        self.assertEqual(values["xemu"]["custom"], "keep")
        self.assertIn("# keep this comment", config.read_text(encoding="utf-8"))

    def test_local_settings_offers_legacy_target_conversion(self):
        config = self.root / "local.toml"
        config.write_text('[deploy]\nhost = "192.0.2.5"\n'
                          'remote_root = "F:/Games/MorrowindTest"\n', encoding="utf-8")
        dialog = LocalSettingsDialog(config)
        self.addCleanup(dialog.close)
        self.assertFalse(dialog.use_targets)
        self.assertFalse(dialog.legacy_notice.isHidden())
        dialog.convert_legacy()
        self.assertTrue(dialog.use_targets)
        self.assertTrue(dialog.save_settings())
        values = self.saved(config)
        self.assertNotIn("deploy", values)
        self.assertEqual(values["targets"]["xbox"]["games_root"], "F:/Games")

    def test_legacy_xemu_settings_can_change_without_converting_deploy(self):
        config = self.root / "local.toml"
        config.write_text('[deploy]\nhost = "192.0.2.5"\n'
                          'remote_root = "F:/Games/MorrowindTest"\n'
                          '[xemu]\nexe = "old/xemu.exe"\nbios = "bios.bin"\n', encoding="utf-8")
        dialog = LocalSettingsDialog(config)
        self.addCleanup(dialog.close)
        row = next(index for index in range(dialog.target_list.count())
                   if dialog.target_list.item(index).data(Qt.ItemDataRole.UserRole) == "xemu")
        dialog.target_list.setCurrentRow(row)
        dialog.xemu_target_fields["exe"].setText("new/xemu.exe")
        self.assertTrue(dialog.save_settings())
        values = self.saved(config)
        self.assertIn("deploy", values)
        self.assertNotIn("targets", values)
        self.assertEqual(values["xemu"]["exe"], "new/xemu.exe")

    def test_target_tooltip_shows_drive_space_from_the_agent(self):
        config = self.root / "local.toml"
        config.write_text('default_target = "bench"\n[targets.bench]\nkind = "xbox"\n'
                          'host = "192.0.2.5"\ngames_root = "F:/Games"\n', encoding="utf-8")
        window = self.window(config=config)

        class Reply:
            def readAllStandardOutput(self):
                return b"ok C=120/480 E=3000/4882 F=1536/60000 G=?/?"

        window.drive_probe = Reply()
        window.drive_probe_target = "bench"
        window.drive_probe_finished(0, None)
        self.assertIn("F: 1.5 GB free of 58.6 GB", window.target_picker.toolTip())
        self.assertIn("E: 2.9 GB free of 4.8 GB, 39% used", window.target_picker.toolTip())
        window.drive_probe = Reply()
        window.drive_probe_target = "bench"
        window.drive_probe.readAllStandardOutput = lambda: b"err unknown command: drives"
        window.drive_probe_finished(1, None)
        self.assertIn("unknown command: drives", window.target_picker.toolTip())


if __name__ == "__main__":
    unittest.main()
