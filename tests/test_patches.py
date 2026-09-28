import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import tes3x_patches as registry
from tes3x_patch import PATCHES as PATCHER


class RegistryTests(unittest.TestCase):
    def test_every_patch_has_code_and_every_function_an_entry(self):
        self.assertEqual(set(PATCHER), set(registry.BY_NAME))

    def test_payload_sources_exist(self):
        hooks = registry.ROOT / 'hooks'
        for entry in registry.PATCHES:
            if 'source' in entry:
                self.assertTrue((hooks / entry['source']).is_file(), entry['source'])

    def test_every_ini_key_is_documented(self):
        read = re.compile(r'get\("Xbox", "(\w+)"|(?:ini|prof)_uint\("(\w+)"')
        keys = set()
        for source in (registry.ROOT / 'hooks').glob('*.c'):
            for match in read.finditer(source.read_text(encoding='utf-8')):
                keys.add(match.group(1) or match.group(2))
        page = (registry.ROOT / 'docs' / 'ini-keys.md').read_text(encoding='utf-8')
        self.assertTrue(keys)
        self.assertEqual(sorted(k for k in keys if f'`{k}`' not in page), [])
        declared = {key for entry in registry.PATCHES for key in entry.get('ini', {})}
        self.assertEqual(declared, keys)
        sections = {match.group(1): match.group(2)
                    for match in re.finditer(r'(?ms)^## `([\w-]+)`$(.*?)(?=^## |\Z)', page)}
        for entry in registry.PATCHES:
            for key in entry.get('ini', {}):
                self.assertIn(f'`{key}`', sections.get(entry['name'], ''), entry['name'])

    def test_catalog_loads_and_matches_folder_or_plugin_names(self):
        import tes3x_catalog
        catalog = tes3x_catalog.load(patch_names=registry.BY_NAME)
        self.assertEqual(tes3x_catalog.match(catalog, 'mop')['id'], 'mop')
        self.assertEqual(tes3x_catalog.match(catalog, 'Graphic Herbalism.ESP')['id'],
                         'graphic-herbalism')
        self.assertIsNone(tes3x_catalog.match(catalog, 'Nothing Like It'))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'catalog.toml'
            path.write_text('schema = 1\n[[mod]]\nid = "x"\nname = "X"\nfolder = "X"\n'
                            'patches = ["no-such-patch"]\n', encoding='utf-8')
            with self.assertRaises(tes3x_catalog.CatalogError):
                tes3x_catalog.load(path, patch_names=registry.BY_NAME)

    def test_example_profile_lists_every_selectable_patch(self):
        example = (registry.ROOT / 'examples' / 'profile.toml').read_text(encoding='utf-8')
        missing = [entry['name'] for entry in registry.PATCHES
                   if entry['selection'] == 'preset' and f'"{entry["name"]}"' not in example]
        self.assertEqual(missing, [])

    def test_generated_pages_are_current(self):
        self.assertEqual(registry.stale_pages(), [], 'run tools/tes3x_patches.py --write')

    def test_patch_channel_is_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'patches.toml'
            path.write_text('[[patch]]\nname = "x"\ncategory = "core"\nchannel = "verified"\n'
                            'selection = "preset"\nsummary = "x"\n', encoding='utf-8')
            with self.assertRaises(registry.RegistryError):
                registry.load(path)


if __name__ == '__main__':
    unittest.main()
