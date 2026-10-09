import re
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch as mock_patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import tes3x_patches as registry
from tes3x_patch import (ALLOWED_PATCH_OVERLAPS, ASSET_PATHS, PATCHES as PATCHER, PatchError,
                         _validate_patch_changes, main as patch_main, retail_digest)


class RegistryTests(unittest.TestCase):
    def test_every_patch_has_code_and_every_function_an_entry(self):
        self.assertEqual(set(PATCHER), set(registry.BY_NAME))

    def test_payload_sources_exist(self):
        hooks = registry.ROOT / 'hooks'
        for entry in registry.PATCHES:
            if 'source' in entry:
                self.assertTrue((hooks / entry['source']).is_file(), entry['source'])

    def test_every_ini_key_is_documented(self):
        read = re.compile(r'(?:ini_uint|prof_uint|ini_text|tes3x_ini_xbox)\("(\w+)"')
        keys = set()
        for source in (registry.ROOT / 'hooks').rglob('*.c'):
            for match in read.finditer(source.read_text(encoding='utf-8')):
                keys.add(match.group(1))
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

    def test_every_patch_page_follows_the_contract(self):
        self.assertEqual(registry.page_problems(), [])

    def test_page_problems_are_reported(self):
        entry = {'name': 'x', 'title': 'Some fix'}
        with tempfile.TemporaryDirectory() as tmp:
            docs = Path(tmp)
            self.assertEqual(registry.page_problems([entry], docs), ['patches/x.md: missing'])
            (docs / 'x.md').write_text('# x\n\n## What remains\n', encoding='utf-8')
            (docs / 'y.md').write_text('# Y\n', encoding='utf-8')
            problems = registry.page_problems([entry], docs)
            self.assertIn('patches/y.md: no such patch in patches.toml', problems)
            self.assertTrue(any("'# Some fix'" in p for p in problems))
            self.assertTrue(any('What remains' in p for p in problems))
            self.assertTrue(any('patch table' in p for p in problems))
            (docs / 'y.md').unlink()
            (docs / 'x.md').write_text('# Some fix\n\nPatch key: `x`\n\n'
                                       'See the [patch table](../docs/patches.md).'
                                       '\n\n## How it works\n\n## Configuration\n',
                                       encoding='utf-8')
            self.assertEqual(registry.page_problems([entry], docs), [])

    def test_patch_channel_is_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'patches.toml'
            path.write_text('[[patch]]\nname = "x"\ntitle = "X"\ncategory = "core"\nchannel = "verified"\n'
                            'selection = "preset"\nsummary = "x"\n', encoding='utf-8')
            with self.assertRaises(registry.RegistryError):
                registry.load(path)

    def test_patch_bits_have_two_words(self):
        template = ('[[patch]]\nname = "x"\ntitle = "X"\ncategory = "core"\n'
                    'channel = "dev"\nselection = "preset"\nsummary = "x"\nbit = %d\n')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'patches.toml'
            path.write_text(template % 63, encoding='utf-8')
            self.assertEqual(registry.load(path)[0]['bit'], 63)
            path.write_text(template % 64, encoding='utf-8')
            with self.assertRaisesRegex(registry.RegistryError, 'bit must be 0-63'):
                registry.load(path)


class PatchOwnershipTests(unittest.TestCase):
    def validate(self, name, before, after, edits, owners=()):
        return _validate_patch_changes(name, bytes(before), bytearray(after), edits,
                                       list(owners))

    def test_accepts_instruction_range_with_unchanged_bytes(self):
        before = b"\xE8\x01\x02\x03\x04"
        after = b"\xE8\x05\x06\x07\x08"
        self.assertEqual(self.validate("a", before, after, [(0, 5, None)]), [(0, 5)])

    def test_rejects_unreported_change(self):
        with self.assertRaisesRegex(PatchError, "changed unreported byte"):
            self.validate("a", b"abcdef", b"abcXef", [])

    def test_rejects_reported_range_with_no_change(self):
        with self.assertRaisesRegex(PatchError, "changed no byte"):
            self.validate("a", b"abcdef", b"abcXef", [(0, 2, None), (3, 1, None)])

    def test_rejects_range_owned_by_an_earlier_patch(self):
        with self.assertRaisesRegex(PatchError, "overlaps first"):
            self.validate("second", b"abcdef", b"abXYef", [(2, 2, None)],
                          [(1, 2, "first")])

    def test_allows_the_explicit_profiler_overlap(self):
        self.assertIn(("script-ext", "profile"), ALLOWED_PATCH_OVERLAPS)
        claims = self.validate("profile", b"abcdef", b"abXYef", [(2, 2, None)],
                               [(1, 2, "script-ext")])
        self.assertEqual(claims, [(2, 2)])

    def test_rejects_duplicate_apply_before_opening_the_image(self):
        argv = ["tes3x_patch.py", "missing.xbe",
                "--apply", "boot-media", "--apply", "boot-media"]
        with mock_patch.object(sys, "argv", argv):
            with self.assertRaisesRegex(SystemExit, "duplicate --apply 'boot-media'"):
                patch_main()


class RetailDigestTests(unittest.TestCase):
    @staticmethod
    def image(drive, media, region):
        data = bytearray(b'XBEH' + bytes(0x300))
        struct.pack_into('<II', data, 0x220, media, region)
        data += b'\xC7\x44\x24\x0C' + drive + b':\\\x00'
        for tail in ASSET_PATHS:
            data += b'\x00' + drive + b':\\' + tail + b'\x00'
        return bytes(data)

    def test_retail_and_scene_copies_share_a_digest(self):
        retail = self.image(b'Z', 2, 1)
        scene = self.image(b'D', 0xC00001FF, 7)
        self.assertNotEqual(retail, scene)
        self.assertEqual(retail_digest(retail), retail_digest(scene))
        self.assertNotEqual(retail_digest(retail), retail_digest(retail[:-1] + b'!'))

    def test_an_image_without_asset_paths_gets_the_certificate_edit(self):
        launcher = bytearray(b'XBEH' + bytes(0x300))
        opened = bytearray(launcher)
        struct.pack_into('<II', opened, 0x220, 0xC00001FF, 7)
        self.assertEqual(retail_digest(bytes(launcher)), retail_digest(bytes(opened)))
        with self.assertRaises(ValueError):
            retail_digest(b'MZ')


if __name__ == '__main__':
    unittest.main()
