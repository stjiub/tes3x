import struct
import unittest
from types import SimpleNamespace

from tes3x.patch import PatchError, find_save_allowed_context
from tes3x.patch_sites import (AUTOSAVE_NAME_SIG, CONSOLE_PRINT_SIG,
                              CONSOLE_WIDGET_ENTRIES, find_autosave_calls,
                              find_console_menu_id, find_console_print,
                              find_console_widget_entry)


class Image:
    def __init__(self, data):
        self.data = bytearray(data)
        self.sections = [SimpleNamespace(name='.text', va=0x10000, raw=0, rsize=len(data))]

    def off_to_va(self, off):
        return 0x10000 + off

    def va_to_off(self, va):
        return va - 0x10000


class SavePatchTests(unittest.TestCase):
    def autosaves(self, buffers):
        body = bytearray(512)
        for i, buffer in enumerate(buffers):
            init = (b'\x68' + struct.pack('<I', buffer)
                    + (b'\x68' + struct.pack('<I', 0x10200)) * 2
                    + b'\x68\x00\x00\x00\x00\xe8')
            body[i * 24:i * 24 + len(init)] = init
        sites = []
        for i in range(3):
            at = 256 + i * 24
            site = 0x10000 + at + 10
            call = (b'\x68' + struct.pack('<I', buffers[0])) * 2
            call += b'\xe8' + struct.pack('<i', 0x20000 - site - 5)
            body[at:at + len(call)] = call
            sites.append(site)
        body += AUTOSAVE_NAME_SIG
        return Image(body), sites

    def test_localized_autosave_initializers_share_one_buffer(self):
        for count in (1, 3):
            image, sites = self.autosaves([0x30000] * count)
            self.assertEqual(find_autosave_calls(image), (0x20000, sites))

    def test_autosave_initializers_cannot_disagree_on_the_buffer(self):
        image, _ = self.autosaves([0x30000, 0x30010])
        with self.assertRaisesRegex(PatchError, '2 name buffers'):
            find_autosave_calls(image)

    def test_console_print_locates_a_moved_menu_id(self):
        signature = CONSOLE_PRINT_SIG[:4] + struct.pack('<I', 0x3D940C) + CONSOLE_PRINT_SIG[8:]
        self.assertEqual(find_console_print(Image(b'\x90' * 8 + signature)), 0x10008)
        self.assertEqual(find_console_menu_id(Image(b'\x90' * 8 + signature)), 0x3D940C)
        with self.assertRaisesRegex(PatchError, '2 matches'):
            find_console_print(Image(signature * 2))

    def test_image_factory_is_distinguished_from_nif_factory(self):
        entry = CONSOLE_WIDGET_ENTRIES['CREATE_IMAGE'][1]
        nif = entry + b'\x90' * 60 + b'\xb8\xb5\x80\xff\xff'
        image = entry + b'\x90' * 60 + b'\xb8\xb8\x80\xff\xff'
        data = nif.ljust(0x100, b'\x90') + image
        self.assertEqual(find_console_widget_entry(Image(data), 'CREATE_IMAGE'), 0x10100)

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
