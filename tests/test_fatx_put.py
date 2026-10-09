import io
import tempfile
import unittest
from pathlib import Path

import tes3x.put as tes3x_put
from tes3x.fatx import Fatx, FatxReader

SIZE = 64 << 20


class PutTests(unittest.TestCase):
    """Writing into an existing FATX partition, as the xemu runner's --put does."""

    def setUp(self):
        self.img = io.BytesIO()
        fs = Fatx(self.img, 0, SIZE)
        fs.format()
        fs.write_dir_at(1, [])
        fs.flush_fat()
        self.partitions = tes3x_put.PARTITIONS
        tes3x_put.PARTITIONS = {'E': (0, SIZE)}
        self.addCleanup(setattr, tes3x_put, 'PARTITIONS', self.partitions)
        self.host = Path(tempfile.mkdtemp())

    def tearDown(self):
        for path in sorted(self.host.rglob('*'), reverse=True):
            path.rmdir() if path.is_dir() else path.unlink()
        self.host.rmdir()

    def files(self):
        reader = FatxReader(self.img, 0).bind(SIZE)
        return {path: reader.read_chain(first, size) for path, first, size in reader.walk()}

    def test_a_folder_of_more_entries_than_a_cluster_goes_in_one_pass(self):
        tree = self.host / 'Base'
        (tree / 'Sound').mkdir(parents=True)
        for i in range(600):  # 256 entries fill a 16 KB directory cluster
            (tree / 'Sound' / f'clip{i:03}.wav').write_bytes(b'%d' % i)
        (tree / 'morrowind.xbe').write_bytes(b'XBEH' * 5000)
        tes3x_put.put_dir(self.img, tree, 'Games', 'Base')
        files = self.files()
        self.assertEqual(len(files), 601)
        self.assertEqual(files['Games/Base/Sound/clip599.wav'], b'599')
        self.assertEqual(files['Games/Base/morrowind.xbe'], b'XBEH' * 5000)

    def test_a_full_directory_is_extended(self):
        source = self.host / 'one.txt'
        source.write_bytes(b'x')
        tes3x_put.make_dirs(self.img, 'Many')
        for i in range(300):
            tes3x_put.put_file(self.img, source, 'Many', f'f{i:03}.txt')
        files = self.files()
        self.assertEqual(len(files), 300)
        self.assertEqual(files['Many/f299.txt'], b'x')


if __name__ == '__main__':
    unittest.main()
