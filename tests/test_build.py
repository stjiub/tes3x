import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / 'tools'
sys.path.insert(0, str(TOOLS))
from tes3x_build import find_data_root


class BuildTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.library = self.root / 'library'
        (self.library / 'Present' / 'textures').mkdir(parents=True)
        (self.library / 'Present' / 'textures' / 'a.dds').write_bytes(b'DDS ')

    def build(self, mods):
        profile = self.root / 'p.toml'
        profile.write_text(f'[profile]\nname = "p"\nlibrary = "{self.library.as_posix()}"\n'
                           + mods, encoding='utf-8')
        return subprocess.run([sys.executable, str(TOOLS / 'tes3x_build.py'), str(profile)],
                              capture_output=True, text=True)

    def test_missing_mod_stops_the_build(self):
        result = self.build('[[mods]]\nname = "Present"\n[[mods]]\nname = "Absent"\n')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Absent', result.stderr)

    def test_optional_or_disabled_missing_mods_are_skipped(self):
        result = self.build('[[mods]]\nname = "Present"\n'
                            '[[mods]]\nname = "Absent"\noptional = true\n'
                            '[[mods]]\nname = "Off"\nenabled = false\n')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_wrapper_folders_are_descended(self):
        wrapped = self.root / 'Mod' / 'Mod' / 'meshes'
        wrapped.mkdir(parents=True)
        self.assertEqual(Path(find_data_root(str(self.root / 'Mod'))), self.root / 'Mod' / 'Mod')


if __name__ == '__main__':
    unittest.main()
