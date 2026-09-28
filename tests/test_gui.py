import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox
    from tes3x_gui import LocalSettingsDialog, ProfileWindow
except ImportError:
    QApplication = None
    LocalSettingsDialog = None
    ProfileWindow = None


@unittest.skipIf(QApplication is None, "optional PySide6 dependency is not installed")
class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        library = self.root / "library"
        (library / "Mod/00 Core").mkdir(parents=True)
        (library / "Mod/00 Core/mod.esp").write_bytes(b"TES3")
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

    def test_profile_window_loads_and_saves_canonical_toml(self):
        window = ProfileWindow(self.profile)
        self.addCleanup(window.close)
        self.assertEqual(window.tabs.count(), 3)
        self.assertEqual(window.body_split.indexOf(window.output), 1)
        self.assertTrue(window.discard_after_deploy.isCheckable())
        self.assertEqual(window.action_save.shortcut().toString(), "Ctrl+S")
        self.assertEqual(window.selected.count(), 1)
        self.assertEqual(window.plugins.count(), 1)
        self.assertGreater(window.patch_catalog.topLevelItemCount(), 0)
        window.patch_search.setText("script-ext")
        window.patch_combos["script-ext"].setCurrentText("Enable")
        window.plugins.item(0).setCheckState(Qt.CheckState.Unchecked)
        self.assertTrue(window.save_profile())
        with open(self.profile, "rb") as stream:
            profile = tomllib.load(stream)
        self.assertEqual(profile["mods"][0]["id"], "mod")
        self.assertEqual(profile["mods"][0]["plugins"], [])
        self.assertEqual(profile["patches"]["enable"], ["script-ext"])

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
        with open(profiles / "a.toml", "rb") as stream:
            self.assertEqual(tomllib.load(stream)["patches"]["preset"], "minimal")

        with patch.object(QInputDialog, "getText", return_value=("c", True)):
            window.new_profile()
        with open(profiles / "c.toml", "rb") as stream:
            created = tomllib.load(stream)
        self.assertEqual(created["profile"]["name"], "c")
        self.assertNotIn("mods", created)
        self.assertEqual(window.profile_path, (profiles / "c.toml").resolve())

        with patch.object(QInputDialog, "getText", return_value=("d", True)):
            window.rename_profile()
        self.assertFalse((profiles / "c.toml").exists())
        self.assertEqual(window.profile_path, (profiles / "d.toml").resolve())

    def test_build_tab_writes_profile_settings(self):
        window = ProfileWindow(self.profile)
        self.addCleanup(window.close)
        original = self.profile.read_text(encoding="utf-8")
        self.assertTrue(window.save_profile())
        with open(self.profile, "rb") as stream:
            untouched = tomllib.load(stream)
        # Defaults the user never set stay out of the file.
        self.assertNotIn("package", untouched)
        self.assertNotIn("rules", untouched)
        self.assertEqual(set(untouched["profile"]), {"name", "library"})

        build = window.build
        build.title.setText("Modded")
        build.select(build.mode, "loose")
        build.archive_only.setChecked(True)
        build.select(build.plugin_order, "mlox")
        build.select(build.invert_look, True)
        build.add_ini_row("General:Show FPS", "1")
        build.add_ini_row("Xbox:ConsoleCombo", "7,9")
        window.selected.setCurrentRow(0)
        window.mod_switches["loose"][0].setChecked(True)
        self.assertTrue(window.is_dirty())
        self.assertTrue(window.save_profile())
        with open(self.profile, "rb") as stream:
            saved = tomllib.load(stream)
        self.assertEqual(saved["profile"]["title"], "Modded")
        self.assertEqual(saved["package"], {"mode": "loose"})
        self.assertEqual(saved["rules"], {"plugin_order": "mlox"})
        self.assertEqual(saved["preferences"], {"invert_look": True})
        self.assertEqual(saved["ini"], {"General:Show FPS": 1, "Xbox:ConsoleCombo": "7,9"})
        self.assertTrue(saved["mods"][0]["loose"])
        self.assertNotEqual(original, self.profile.read_text(encoding="utf-8"))

    def test_folder_name_profiles_work_with_or_without_an_index(self):
        plain = self.root / "plain"
        (plain / "Folder Mod").mkdir(parents=True)
        (plain / "Folder Mod" / "folder.esp").write_bytes(b"TES3")
        (plain / "Other").mkdir()
        profile = self.root / "folders.toml"
        profile.write_text(f'[profile]\nname = "folders"\nlibrary = "{plain.as_posix()}"\n\n'
                           '[[mods]]\nname = "Folder Mod"\norder = 10\n', encoding="utf-8")
        window = ProfileWindow(profile)
        self.addCleanup(window.close)
        self.assertEqual(window.profile_path, profile.resolve())
        self.assertEqual(window.selected.item(0).text(), "Folder Mod  (folder)")
        window.selected.setCurrentRow(0)
        self.assertEqual(window.plugins.count(), 1)
        self.assertFalse(window.is_dirty())

        # No library.toml: a mod added in the GUI is written by folder name too.
        other = next(window.library.topLevelItem(i).child(0)
                     for i in range(window.library.topLevelItemCount())
                     if window.library.topLevelItem(i).text(0) == "Other")
        window.library.setCurrentItem(other)
        window.add_selected_release()
        self.assertTrue(window.save_profile())
        with open(profile, "rb") as stream:
            saved = tomllib.load(stream)
        self.assertEqual([mod["name"] for mod in saved["mods"]], ["Folder Mod", "Other"])

        window.write_library_index()
        self.assertTrue((plain / "library.toml").is_file())
        with patch.object(QMessageBox, "information"):
            window.convert_to_ids()
        with open(profile, "rb") as stream:
            saved = tomllib.load(stream)
        self.assertEqual([mod["id"] for mod in saved["mods"]], ["folder-mod", "other"])
        self.assertEqual(window.selected.count(), 2)

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
