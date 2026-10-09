import tempfile
import unittest
from pathlib import Path

from tes3x.qcow2 import (OVERLAY_CLUSTER_BITS, CowView, Qcow2, create_overlay,  # noqa: E402
                         image_size, open_image)

CLUSTER = 1 << OVERLAY_CLUSTER_BITS


class Qcow2Tests(unittest.TestCase):
    def test_overlays_chain_over_raw_and_qcow2_bases(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            raw = root / "clean.img"
            raw.write_bytes(bytes(range(256)) * (4 * CLUSTER // 256))
            expected = bytearray(raw.read_bytes())

            # A base qcow2 over the raw image, and a run overlay over that.
            with CowView(str(raw)) as disk:
                disk.seek(CLUSTER + 10)
                disk.write(b"base")
                base_clusters = disk.changed()
            create_overlay(str(root / "base.qcow2"), str(raw), base_clusters)
            expected[CLUSTER + 10:CLUSTER + 14] = b"base"
            with CowView(str(root / "base.qcow2")) as disk:
                self.assertEqual(disk.size, len(expected))
                disk.seek(3 * CLUSTER - 2)
                disk.write(b"run!")
                run_clusters = disk.changed()
            self.assertEqual(sorted(run_clusters), [2, 3])
            create_overlay(str(root / "run.qcow2"), str(root / "base.qcow2"), run_clusters)
            expected[3 * CLUSTER - 2:3 * CLUSTER + 2] = b"run!"

            with Qcow2(str(root / "run.qcow2")) as image:
                self.assertTrue(image.backing.endswith("base.qcow2"))
            self.assertEqual(image_size(root / "run.qcow2"), len(expected))
            with open_image(str(root / "run.qcow2")) as image:
                self.assertEqual(image.read(), bytes(expected))
            with open(root / "run.qcow2", "rb") as stream:
                self.assertIn(b"qcow2", stream.read(256))


if __name__ == "__main__":
    unittest.main()
