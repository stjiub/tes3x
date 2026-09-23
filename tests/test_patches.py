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

    def test_generated_table_is_current(self):
        self.assertEqual(registry.TABLE.read_text(encoding='utf-8'), registry.render(),
                         'run tools/tes3x_patches.py --write')

    def test_verified_status_needs_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'patches.toml'
            path.write_text('[[patch]]\nname = "x"\ncategory = "core"\nstatus = "verified-xemu"\n'
                            'selection = "preset"\nsummary = "x"\n', encoding='utf-8')
            with self.assertRaises(registry.RegistryError):
                registry.load(path)


if __name__ == '__main__':
    unittest.main()
