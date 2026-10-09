import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tes3x.build import Mod, find_data_root
from tes3x.bsa import write_bsa
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_bsa import pc_bsa  # noqa: E402


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.mod = self.root / 'Mod'
        (self.mod / 'meshes').mkdir(parents=True)
        (self.mod / 'meshes' / 'a.nif').write_bytes(b'loose')
        (self.mod / 'mod.esp').write_bytes(b'TES3')

    def test_archives_unpack_beneath_the_mods_loose_files(self):
        pc_bsa(self.mod / 'Mod.bsa', [('meshes\\a.nif', b'packed'), ('meshes\\b.nif', b'b')])
        cache = self.root / 'cache'
        mod = Mod('Mod', self.mod, 10, unpack=str(cache))
        self.assertEqual(sorted(mod.files), ['meshes/a.nif', 'meshes/b.nif', 'mod.esp'])
        self.assertEqual(Path(mod.files['meshes/a.nif']).read_bytes(), b'loose')
        self.assertEqual(Path(mod.files['meshes/b.nif']).read_bytes(), b'b')
        self.assertEqual(mod.unpacked, ['Mod.bsa'])
        self.assertIn('mod.bsa', Mod('Mod', self.mod, 10).files)

    def test_hash_only_archive_cannot_be_unpacked(self):
        (self.root / 'b.nif').write_bytes(b'b')
        write_bsa(str(self.mod / 'Mod.bsa'), [('meshes\\b.nif', str(self.root / 'b.nif'))])
        with self.assertRaisesRegex(ValueError, 'archives = "load"'):
            Mod('Mod', self.mod, 10, unpack=str(self.root / 'cache'))


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
        return subprocess.run([sys.executable, '-m', 'tes3x', 'build', str(profile)],
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
