import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / 'tools'
sys.path.insert(0, str(TOOLS))
from tes3x_bsa import Bsa, write_bsa


class LooseModTests(unittest.TestCase):
    """A loose mod's assets must leave every archive, and a retail entry they replace must be
    dropped (merged) or listed for invalidation (delta)."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.root = root
        vanilla = root / 'vanilla'
        (vanilla / 'Data Files').mkdir(parents=True)
        retail = root / 'retail'
        for rel in ('meshes/a.nif', 'meshes/b.nif'):
            (retail / rel).parent.mkdir(parents=True, exist_ok=True)
            (retail / rel).write_bytes(b'retail ' + rel.encode())
        write_bsa(str(vanilla / 'Data Files' / 'Morrowind.bsa'),
                  [(r.replace('/', '\\'), str(retail / r)) for r in ('meshes/a.nif', 'meshes/b.nif')])
        (vanilla / 'Data Files' / 'Morrowind.esm').write_bytes(b'TES3')
        (vanilla / 'Morrowind.ini').write_text('[General]\nTryArchiveFirst=0\n')
        self.vanilla = vanilla

        tree = root / 'tree'
        files = {'meshes/a.nif': 'Loose', 'meshes/new.nif': 'Loose', 'meshes/b.nif': 'Packed'}
        for rel in files:
            (tree / rel).parent.mkdir(parents=True, exist_ok=True)
            (tree / rel).write_bytes(b'mod ' + rel.encode())
        self.tree = tree
        self.manifest = root / 'manifest.json'
        self.manifest.write_text(json.dumps({k: {'mod': v, 'src': ''} for k, v in files.items()}))

    def tearDown(self):
        self.temp.cleanup()

    def pack(self, out, *extra):
        subprocess.run([sys.executable, str(TOOLS / 'tes3x_pack.py'), str(self.tree),
                        '--vanilla', str(self.vanilla / 'Data Files'),
                        '--ini', str(self.vanilla / 'Morrowind.ini'), '--out', str(out),
                        '--manifest', str(self.manifest), '--loose-mod', 'Loose', *extra],
                       check=True, capture_output=True)

    def test_delta(self):
        out = self.root / 'delta'
        self.pack(out, '--delta-archive', 'tes3xmods.bsa')
        df = out / 'Data Files'
        self.assertEqual((df / 'meshes/a.nif').read_bytes(), b'mod meshes/a.nif')
        self.assertTrue((df / 'meshes/new.nif').exists())
        delta = Bsa(str(df / 'tes3xmods.bsa'))
        self.assertTrue(delta.contains('meshes\\b.nif'))
        self.assertFalse(delta.contains('meshes\\a.nif'))
        self.assertFalse(delta.contains('meshes\\new.nif'))
        listed = (out / 'ArchiveInvalidationList.txt').read_bytes()
        self.assertEqual(listed, b'meshes\\a.nif\r\n')

    def test_merged(self):
        out = self.root / 'merged'
        self.pack(out)
        df = out / 'Data Files'
        merged = Bsa(str(df / 'Morrowind.bsa'))
        self.assertFalse(merged.contains('meshes\\a.nif'))
        self.assertTrue(merged.contains('meshes\\b.nif'))
        self.assertFalse((out / 'ArchiveInvalidationList.txt').exists())
        self.assertTrue((df / 'meshes/a.nif').exists())

    def test_no_archive(self):
        out = self.root / 'loose'
        self.pack(out, '--no-archive')
        df = out / 'Data Files'
        for rel in ('meshes/a.nif', 'meshes/b.nif', 'meshes/new.nif'):
            self.assertEqual((df / rel).read_bytes(), b'mod ' + rel.encode())
        self.assertEqual((df / 'Morrowind.bsa').read_bytes(),
                         (self.vanilla / 'Data Files' / 'Morrowind.bsa').read_bytes())
        self.assertFalse((df / 'tes3xmods.bsa').exists())
        listed = (out / 'ArchiveInvalidationList.txt').read_bytes()
        self.assertEqual(sorted(listed.split(b'\r\n')), [b'', b'meshes\\a.nif', b'meshes\\b.nif'])
        self.assertIn('TryArchiveFirst=0', (out / 'Morrowind.ini').read_text())


    def test_mod_archives_load_before_the_delta_archive(self):
        (self.tree / 'Mod.bsa').write_bytes(b'archive')
        listing = self.root / 'archives.json'
        listing.write_text(json.dumps(['Mod.bsa']))
        out = self.root / 'listed'
        self.pack(out, '--delta-archive', 'tes3xmods.bsa', '--mod-archives', str(listing))
        df = out / 'Data Files'
        self.assertEqual((df / 'Mod.bsa').read_bytes(), b'archive')
        self.assertEqual((df / 'tes3xarch.txt').read_bytes().splitlines()[1:3],
                         [b'Mod.bsa', b'tes3xmods.bsa'])
        out = self.root / 'loose-listed'
        self.pack(out, '--no-archive', '--mod-archives', str(listing))
        self.assertEqual((out / 'Data Files' / 'tes3xarch.txt').read_bytes().splitlines()[1],
                         b'Mod.bsa')

    def test_bsa_hash_collisions_are_staged_loose(self):
        colliding = ('meshes/tr/b/tr_b_altmer_f_hd_13.nif',
                     'meshes/tr/b/tr_b_altmer_m_hd_18.nif')
        for rel in colliding:
            path = self.tree / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(rel.encode())
        out = self.root / 'collisions'
        self.pack(out, '--delta-archive', 'tes3xmods.bsa')
        delta = Bsa(str(out / 'Data Files' / 'tes3xmods.bsa'))
        for rel in colliding:
            self.assertEqual((out / 'Data Files' / rel).read_bytes(), rel.encode())
            self.assertFalse(delta.contains(rel.replace('/', '\\')))
        self.assertIn('TryArchiveFirst=0', (out / 'Morrowind.ini').read_text())


if __name__ == '__main__':
    unittest.main()
