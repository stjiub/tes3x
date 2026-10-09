import struct
import tempfile
import unittest
from pathlib import Path

from tes3x.bsa import write_bsa
from tes3x.reach import nif_textures, plugin_paths, prune
from tes3x.records import records, subrecords


def sub(tag, data):
    return struct.pack('<4sI', tag, len(data)) + data


def rec(tag, data):
    return struct.pack('<4sIII', tag, len(data), 0, 0) + data


def nif(texture):
    name = texture.encode()
    return (b'NetImmerse File Format, Version 4.0.0.2\n' + struct.pack('<II', 0x04000002, 1)
            + struct.pack('<I', 15) + b'NiSourceTexture' + struct.pack('<III', 0, 0xffffffff, 0xffffffff)
            + b'\x01' + struct.pack('<I', len(name)) + name + bytes(13))


class ReachabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.vanilla = self.root / 'vanilla'
        self.vanilla.mkdir()
        (self.vanilla / 'Morrowind.esm').write_bytes(rec(b'TES3', b''))
        write_bsa(self.vanilla / 'Morrowind.bsa', [])
        self.files = {}

    def put(self, path, data):
        p = self.root / 'mods' / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        self.files[path.lower()] = (None, str(p))
        return p

    def test_transitive_texture_alternatives_animation_and_orphan(self):
        self.put('mod.esp', rec(b'STAT', sub(b'MODL', b'item.nif\0')))
        self.put('meshes/item.nif', nif('Textures\\A.TGA'))
        self.put('meshes/xitem.kf', b'animation')
        self.put('textures/a.dds', b'texture')
        self.put('textures/orphan.dds', b'orphan')
        self.put('meshes/orphan.nif', nif('orphan.dds'))
        kept, report = prune(self.files, self.vanilla)
        self.assertEqual(set(kept), {'mod.esp', 'meshes/item.nif', 'meshes/xitem.kf', 'textures/a.dds'})
        self.assertEqual(report['removed_files'], 2)

    def test_vanilla_definition_and_archive_replacer_are_roots(self):
        (self.vanilla / 'Morrowind.esm').write_bytes(rec(b'STAT', sub(b'MODL', b'base.nif')))
        self.put('meshes/base.nif', nif('new.dds'))
        self.put('textures/new.dds', b'new')
        source = self.put('meshes/hardcoded.nif', nif('effect.dds'))
        write_bsa(self.vanilla / 'Morrowind.bsa', [('meshes/hardcoded.nif', source)])
        self.put('textures/effect.dds', b'effect')
        kept, _ = prune(self.files, self.vanilla)
        self.assertEqual(set(kept), set(self.files))

    def test_unsupported_mesh_preserves_textures(self):
        self.put('mod.esp', rec(b'STAT', sub(b'MODL', b'new.nif')))
        self.put('meshes/new.nif', b'unsupported')
        self.put('textures/unknown.dds', b'texture')
        kept, report = prune(self.files, self.vanilla)
        self.assertIn('textures/unknown.dds', kept)
        self.assertEqual(len(report['warnings']), 1)

    def test_keep_globs_voice_and_non_assets(self):
        for key in ('sound/vo/custom/line.wav', 'music/explore/track.mp3', 'textures/ui/a.dds', 'textures/orphan.dds'):
            self.put(key, b'data')
        kept, _ = prune(self.files, self.vanilla, ['textures/ui/*'])
        self.assertEqual(set(self.files) - set(kept), {'textures/orphan.dds'})

    def test_plugin_fields_scripts_and_books(self):
        p = self.put('mod.esp', rec(b'BOOK', sub(b'TEXT', b'<IMG SRC="Art\\page.tga" WIDTH=64>'))
                     + rec(b'SOUN', sub(b'FNAM', b'fx\\noise.wav'))
                     + rec(b'SCPT', sub(b'SCTX', b'Say "Vo\\custom\\some line.wav" "Hello"'))
                     + rec(b'LTEX', sub(b'DATA', b'land.dds'))
                     + rec(b'MGEF', sub(b'ITEX', b'magic.tga') + sub(b'PTEX', b'particle.tga')))
        self.assertTrue({'bookart/art/page.tga', 'sound/fx/noise.wav', 'sound/vo/custom/some line.wav',
                         'textures/land.dds', 'icons/magic.tga', 'textures/particle.tga'} <= set(plugin_paths(p)))

    def test_stub_and_truncation(self):
        p = self.put('Tribunal.esm', b'TES3')
        self.assertEqual(list(records(p)), [])
        p.write_bytes(rec(b'STAT', b'foo')[:-1])
        with self.assertRaises(ValueError):
            list(records(p))
        with self.assertRaises(ValueError):
            list(subrecords(sub(b'MODL', b'foo')[:-1]))
        with self.assertRaises(ValueError):
            list(nif_textures(nif('a.dds')[:-10]))


if __name__ == '__main__':
    unittest.main()
