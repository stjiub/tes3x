import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import tes3x_savepool as pool
from tes3x_patch import CERT_TITLE_ID, PatchError, _title_id


class SavePoolTests(unittest.TestCase):
    def test_id_from_name_is_stable_and_outside_licensed_titles(self):
        value = pool.pool_id("TR test")
        self.assertEqual(value >> 16, 0x5433)
        self.assertNotEqual(value & 0xFFFF, 0)
        self.assertEqual(pool.pool_id(" tr TEST "), value)
        self.assertNotEqual(pool.pool_id("Main"), value)

    def test_explicit_id_wins_but_not_the_shared_one(self):
        self.assertEqual(pool.pool_id("x", "5433ABCD"), 0x5433ABCD)
        self.assertEqual(pool.pool_id("x", "0x5433abcd"), 0x5433ABCD)
        for bad in ("42530005", "0", "123456789"):
            with self.assertRaises(ValueError):
                pool.pool_id("x", bad)

    def test_folder_files(self):
        self.assertEqual(pool.folder(0x5433ABCD), "UDATA/5433ABCD")
        files = pool.files("TR test", b"image")
        self.assertEqual(files["TitleMeta.xbx"],
                         "﻿TitleName=Morrowind: TR test\r\n".encode("utf-16-le"))
        self.assertEqual(files["TitleImage.xbx"], b"image")
        self.assertEqual(files[pool.MARKER], b"TR test")

    def test_title_id_patch_rewrites_the_certificate(self):
        class Image:
            data = bytearray(0x200)
        x = Image()
        struct.pack_into("<I", x.data, CERT_TITLE_ID, 0x42530005)
        edits = _title_id(x, "5433abcd", {})
        self.assertEqual(struct.unpack_from("<I", x.data, CERT_TITLE_ID)[0], 0x5433ABCD)
        self.assertEqual(edits[0][:2], (CERT_TITLE_ID, 4))
        with self.assertRaises(PatchError):
            _title_id(x, "pool", {})


if __name__ == "__main__":
    unittest.main()
