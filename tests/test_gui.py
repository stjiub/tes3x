import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
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
        self.assertEqual(window.tabs.count(), 2)
        self.assertEqual(window.centralWidget().layout().indexOf(window.output), 1)
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
