import contextlib
import io
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tes3x.bsa import write_bsa
from tes3x.build import Mod, materialize
from tes3x.init import initialize
from tes3x.library import discover_library, write_library
from tes3x.paths import mod_library
from tes3x import scenario


class InstalledPathTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.data = self.root / 'data'
        self.other = self.root / 'unrelated'
        self.data.mkdir()
        self.other.mkdir()
        self.config = self.data / 'tes3x.local.toml'
        self.config.write_text('[paths]\nmod_library = "mods"\n', encoding='utf-8')
        self.library = self.data / 'mods'
        (self.library / 'Example' / 'meshes').mkdir(parents=True)
        (self.library / 'Example' / 'meshes' / 'unused.nif').write_bytes(b'unused')
        self.profile = self.root / 'profile.toml'
        self.profile.write_text('[profile]\nname = "example"\n'
                                '[[mods]]\nname = "Example"\n', encoding='utf-8')
        self.env = dict(os.environ, TES3X_DATA=str(self.data), TES3X_CONFIG=str(self.config))

    def command(self, *args):
        result = subprocess.run([sys.executable, '-m', 'tes3x', *map(str, args)],
                                cwd=self.other, env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def test_init_keeps_existing_config_and_copies_profile(self):
        original = self.config.read_bytes()
        with contextlib.redirect_stdout(io.StringIO()):
            initialize(self.data)
            initialize(self.data)
        self.assertEqual(self.config.read_bytes(), original)
        profile = self.data / 'profiles' / 'my-build.toml'
        self.assertTrue(profile.is_file())
        self.assertNotIn('\r\n', profile.read_bytes().decode())

    def test_init_defaults_to_data_from_an_unrelated_directory(self):
        self.command('init')
        self.assertTrue((self.data / 'profiles' / 'my-build.toml').is_file())
        self.assertEqual(list(self.other.iterdir()), [])

    def test_library_precedence_and_config_relative_paths(self):
        with patch.dict(os.environ, self.env):
            self.assertEqual(mod_library({}), self.library)
            self.assertEqual(mod_library({'profile': {'library': 'profile-mods'}}),
                             self.data / 'profile-mods')
            self.assertEqual(mod_library({'profile': {'library': 'profile-mods'}}, 'override'),
                             self.data / 'override')

    def test_build_prune_uses_config_library_from_an_unrelated_directory(self):
        vanilla = self.root / 'vanilla'
        vanilla.mkdir()
        (vanilla / 'Morrowind.esm').write_bytes(struct.pack('<4sIII', b'TES3', 0, 0, 0))
        write_bsa(vanilla / 'Morrowind.bsa', [])
        result = self.command('build', self.profile, '--prune', '--vanilla', vanilla)
        self.assertIn('removed 1 files', result.stdout)
        self.assertEqual(list(self.other.iterdir()), [])

    def test_convert_uses_config_library_from_an_unrelated_directory(self):
        write_library(self.library, discover_library(self.library))
        self.command('library', 'convert', self.profile)
        self.assertIn('id = "example"', self.profile.read_text())

    def test_archive_cache_defaults_to_data_folder(self):
        from test_bsa import pc_bsa

        pc_bsa(self.library / 'Example' / 'Example.bsa', [('meshes/packed.nif', b'packed')])
        self.command('build', self.profile)
        self.assertTrue(any((self.data / 'cache' / 'bsa').rglob('packed.nif')))
        self.assertEqual(list(self.other.iterdir()), [])

    def test_texture_conversion_defaults_to_data_cache_and_accepts_override(self):
        source = self.library / 'Example' / 'textures' / 'test.dds'
        source.parent.mkdir()
        source.write_bytes(b'texture')
        mod = Mod('Example', self.library / 'Example', 10)
        filemap = {'textures/test.dds': (mod, str(source))}
        with patch.dict(os.environ, self.env), patch('tes3x.build.texture_dims', return_value=(1024, 1024)), \
                patch('tes3x.convert.convert_cached', return_value=(b'converted', None)) as convert:
            materialize(filemap, [mod], self.root / 'output', {})
            self.assertEqual(convert.call_args.args[1], self.data / 'cache' / 'tex')
            materialize(filemap, [mod], self.root / 'output', {}, cache=self.root / 'custom-cache')
            self.assertEqual(convert.call_args.args[1], self.root / 'custom-cache')

    def test_scenario_reuse_finds_data_runs_with_normal_config_discovery(self):
        run = self.data / 'build' / 'xemu' / 'existing-test'
        run.mkdir(parents=True)
        (run / 'tes3xlog.txt').write_text('exec.exit 0\n')
        spec = {'kind': 'single', 'script': 'exit', 'expect': {'test': ['exec.exit 0']}}
        with patch.dict(os.environ, self.env), patch('tes3x.scenario.load', return_value=spec), \
                patch.object(sys, 'argv', ['scenario', 'console', str(self.profile),
                                         '--reuse', '--name', 'existing']), \
                contextlib.redirect_stdout(io.StringIO()):
            scenario.main()

    def test_census_scenarios_select_the_pipeline_options(self):
        for name in ('heap-census', 'mem-census'):
            self.assertIn('--' + name, scenario.build_flags({}, name, 'test'))
            self.assertNotIn('--' + name, scenario.build_flags({}, name, 'control'))


if __name__ == '__main__':
    unittest.main()
