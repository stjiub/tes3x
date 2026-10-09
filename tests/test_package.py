import tempfile
import unittest
from pathlib import Path

from tes3x.package import MAX_PATH, PackageError, check_paths


class CheckPathsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.stage = Path(self.tmp.name) / 'TES3X-0.1.0'

    def tearDown(self):
        self.tmp.cleanup()

    def add(self, length):
        name = 'f' * (length - len(self.stage.name) - len('/python/'))
        path = self.stage / 'python' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()

    def test_at_the_limit(self):
        self.add(MAX_PATH)
        check_paths(self.stage)

    def test_over_the_limit(self):
        self.add(MAX_PATH + 1)
        with self.assertRaisesRegex(PackageError, f'{MAX_PATH + 1} characters'):
            check_paths(self.stage)


if __name__ == '__main__':
    unittest.main()
