import struct
import unittest

from tes3x.patch import PatchError, find_save_allowed_context


class Image:
    def __init__(self, data):
        self.data = bytearray(data)


class SavePatchTests(unittest.TestCase):
    def test_chargen_save_gate_fields_are_content_located(self):
        data = (b'\x90' * 20 + b'\x8b\x35' + struct.pack('<I', 0x12345678)
                + b'\x90' * 20 + b'\x8b\x8e' + struct.pack('<I', 0xBC)
                + b'\xd9\x41\x34\xe8\x00\x00\x00\x00\x83\xf8\xff\x75\x20'
                + b'\x90' * 20)
        self.assertEqual(find_save_allowed_context(Image(data)), (0x12345678, 0xBC, 0x34))

    def test_chargen_save_gate_must_be_unique(self):
        with self.assertRaisesRegex(PatchError, '0 matches'):
            find_save_allowed_context(Image(b'\x90' * 40))


if __name__ == '__main__':
    unittest.main()
