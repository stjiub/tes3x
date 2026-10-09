import struct
import tempfile
import unittest
from pathlib import Path

from tes3x.bsa import Bsa, extract_bsa, tes3_hash, write_bsa


def pc_bsa(path, files):
    """A PC-layout archive, which keeps a name table the Xbox layout leaves out."""
    names = b''.join(name.encode() + b'\0' for name, _data in files)
    offsets, position = [], 0
    for name, _data in files:
        offsets.append(position)
        position += len(name) + 1
    count = len(files)
    records, data_offset = [], 0
    for _name, data in files:
        records += [len(data), data_offset]
        data_offset += len(data)
    header = struct.pack('<3I', 0x100, count * 12 + len(names), count)
    hashes = b''.join(struct.pack('<2I', *tes3_hash(name)) for name, _data in files)
    Path(path).write_bytes(header + struct.pack(f'<{count * 2}I', *records)
                           + struct.pack(f'<{count}I', *offsets) + names + hashes
                           + b''.join(data for _name, data in files))


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


    def test_pc_archive_names_are_read_and_extracted(self):
        path = self.root / 'mod.bsa'
        pc_bsa(path, [('meshes\\a.nif', b'aa'), ('textures\\b.dds', b'bbb')])
        archive = Bsa(str(path))
        self.assertTrue(archive.named)
        self.assertEqual(archive.read(archive.entries[1]), b'bbb')
        self.assertEqual(extract_bsa(path, self.root / 'out'), 2)
        self.assertEqual((self.root / 'out/textures/b.dds').read_bytes(), b'bbb')
        xbox = self.root / 'xbox.bsa'
        write_bsa(str(xbox), [('meshes\\a.nif', str(self.source('a', b'aa')))])
        self.assertFalse(Bsa(str(xbox)).named)
        with self.assertRaises(ValueError):
            extract_bsa(xbox, self.root / 'none')


if __name__ == '__main__':
    unittest.main()
