import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from tes3x_bsa import Bsa, tes3_hash, write_bsa


class BsaTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def source(self, name, data):
        path = self.root / name
        path.write_bytes(data)
        return path

    def test_hash_ignores_case_and_slash_direction(self):
        self.assertEqual(tes3_hash('Meshes/A.nif'), tes3_hash('meshes\\a.nif'))
        self.assertNotEqual(tes3_hash('meshes\\a.nif'), tes3_hash('meshes\\b.nif'))

    def test_round_trip_is_sorted_by_hash_with_xbox_layout(self):
        files = [('meshes\\b.nif', self.source('b', b'bee')),
                 ('textures\\a.dds', self.source('a', b'ay'))]
        out = self.root / 'out.bsa'
        self.assertEqual(write_bsa(out, files), (2, 5))
        version, hash_offset, count = struct.unpack_from('<3I', out.read_bytes())
        self.assertEqual((version, hash_offset, count), (0x100, 16, 2))
        archive = Bsa(out)
        self.assertEqual([e['hash'] for e in archive.entries],
                         sorted(tes3_hash(name) for name, _ in files))
        for name, source in files:
            self.assertEqual(archive.read(archive.by_hash[tes3_hash(name)]), source.read_bytes())

    def test_base_entries_are_kept_replaced_or_dropped(self):
        base_path = self.root / 'base.bsa'
        write_bsa(base_path, [('keep.nif', self.source('k', b'keep')),
                              ('swap.nif', self.source('s', b'old')),
                              ('drop.nif', self.source('d', b'gone'))])
        out = self.root / 'out.bsa'
        write_bsa(out, [('swap.nif', self.source('n', b'new'))], base=Bsa(base_path),
                  drop={tes3_hash('drop.nif')})
        archive = Bsa(out)
        self.assertEqual(archive.read(archive.by_hash[tes3_hash('keep.nif')]), b'keep')
        self.assertEqual(archive.read(archive.by_hash[tes3_hash('swap.nif')]), b'new')
        self.assertFalse(archive.contains('drop.nif'))


if __name__ == '__main__':
    unittest.main()
