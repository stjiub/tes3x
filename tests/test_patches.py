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
