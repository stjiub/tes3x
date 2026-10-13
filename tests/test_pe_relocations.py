import json
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest

from tes3x.inject import apply_relocations, bind_payload, load_pe, rebase_hooks
from tes3x.payload import PayloadError, find_tool, link_symbol


class RelocationTests(unittest.TestCase):
    def image(self, entry=0x3020):
        blob = bytearray(64)
        struct.pack_into('<I', blob, 32, 0x401234)
        struct.pack_into('<IIHH', blob, 48, 0, 12, entry, 0)
        return blob

    def test_highlow_moves_payload_pointer_and_leaves_engine_pointer(self):
        blob = self.image()
        struct.pack_into('<I', blob, 36, 0x123456)
        apply_relocations(blob, 48, 12, 0x2000)
        self.assertEqual(struct.unpack_from('<II', blob, 32), (0x403234, 0x123456))

    def test_negative_delta(self):
        blob = self.image()
        apply_relocations(blob, 48, 12, -0x2000)
        self.assertEqual(struct.unpack_from('<I', blob, 32)[0], 0x3FF234)

    def test_rejects_malformed_directories_without_partial_edits(self):
        for entry in (0x1020, 0x303F, 0x3030):
            with self.subTest(entry=entry):
                blob = self.image(entry)
                before = bytes(blob)
                with self.assertRaises(ValueError):
                    apply_relocations(blob, 48, 12, 0x2000)
                self.assertEqual(blob, before)
        for length in (0, 7, 9, 14):
            blob = self.image()
            struct.pack_into('<I', blob, 52, length)
            with self.assertRaises(ValueError):
                apply_relocations(blob, 48, 12, 0x2000)

    def test_rejects_duplicate_fixups(self):
        blob = self.image()
        struct.pack_into('<H', blob, 58, 0x3020)
        with self.assertRaises(ValueError):
            apply_relocations(blob, 48, 12, 0x2000)

    def test_rejects_overlapping_fixups(self):
        blob = self.image()
        struct.pack_into('<H', blob, 58, 0x3021)
        with self.assertRaises(ValueError):
            apply_relocations(blob, 48, 12, 0x2000)

    def test_legacy_manifest_accepts_a_hex_string_base(self):
        hooks = rebase_hooks(dict(base='0x400000', hooks=dict(entry='0x401000',
                             engine='0x123456')), 0x500000, 0x2000)
        self.assertEqual(hooks, dict(entry='0x00501000', engine='0x123456'))

    def test_manifest_rebases_only_payload_symbols(self):
        manifest = dict(base=0x400000, payload_hooks=['entry'],
                        hooks=dict(entry='0x401000', opcode_base='0x2000',
                                   engine_site='0x401010', prof_count='16'))
        before = json.dumps(manifest)
        hooks = rebase_hooks(manifest, 0x500000, 0x3000)
        self.assertEqual(hooks, dict(entry='0x00501000', opcode_base='0x2000',
                                     engine_site='0x401010', prof_count='16'))
        self.assertEqual(json.dumps(manifest), before)

    def test_bind_payload_uses_original_entry_and_host_thunk_order(self):
        class Host:
            entry = 0x123456
            thunk = 0x234000

            @staticmethod
            def thunk_ordinals():
                return [9, 2, 7]

        manifest = dict(base=0x400000, bindings=dict(orig_entry='0x400008',
                        thunk_slots='0x400010', thunk_count=10, required_thunks=[2, 9]))
        original = bytes(64)
        bound = bind_payload(original, manifest, 0x500000, Host())
        self.assertEqual(original, bytes(64))
        self.assertEqual(struct.unpack_from('<I', bound, 8)[0], Host.entry)
        self.assertEqual(struct.unpack_from('<I', bound, 16 + 9 * 4)[0], Host.thunk)
        self.assertEqual(struct.unpack_from('<I', bound, 16 + 2 * 4)[0], Host.thunk + 4)
        self.assertEqual(struct.unpack_from('<I', bound, 16 + 7 * 4)[0], Host.thunk + 8)
        self.assertEqual(struct.unpack_from('<I', bound, 16 + 3 * 4)[0], 0)
        manifest['bindings']['required_thunks'] = [3]
        with self.assertRaisesRegex(ValueError, 'ordinal.*3'):
            bind_payload(original, manifest, 0x500000, Host())


class LinkedRelocationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.clang, cls.lld = find_tool('clang'), find_tool('lld-link')
        except PayloadError as exc:
            raise unittest.SkipTest(str(exc)) from exc

    def test_relocated_image_and_every_hook_match_fixed_link(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            source = folder / 'entry.c'
            source.write_text('volatile unsigned engine = 0x123456;\n'
                              'unsigned data = 7;\nunsigned *pointer = &data;\n'
                              'void entry(void) { *pointer = engine; }\n')
            obj = folder / 'entry.obj'
            subprocess.run([self.clang, '-target', 'i386-pc-win32', '-ffreestanding',
                            '-c', str(source), '-o', str(obj)], check=True,
                           capture_output=True)
            paths, maps = [], []
            for label, base, fixed in [('reloc', 0x400000, '/fixed:no'),
                                       ('fixed', 0x502000, '/fixed')]:
                path, map_path = folder / (label + '.pe'), folder / (label + '.map')
                subprocess.run([self.lld, '/nologo', '/subsystem:native', '/entry:entry',
                                '/nodefaultlib', fixed, f'/base:{base}',
                                f'/out:{path}', f'/map:{map_path}', str(obj)], check=True,
                               capture_output=True)
                paths.append(path)
                maps.append(map_path.read_text().splitlines())
            blob, _, entry, _ = load_pe(paths[0], base=0x502000)
            fixed_blob, _, fixed_entry, _ = load_pe(paths[1])
            self.assertEqual(entry, fixed_entry)
            self.assertEqual(blob[:len(fixed_blob)], fixed_blob)
            names = ['_entry', '_data', '_pointer', '_engine']
            manifest = dict(base=0x400000, payload_hooks=names,
                            hooks={name: link_symbol(maps[0], name) for name in names})
            hooks = rebase_hooks(manifest, 0x502000, len(blob))
            self.assertEqual({name: int(value, 16) for name, value in hooks.items()},
                             {name: int(link_symbol(maps[1], name), 16) for name in names})
            with self.assertRaisesRegex(ValueError, 'fixed payload has no relocations'):
                load_pe(paths[1], base=0x600000)
