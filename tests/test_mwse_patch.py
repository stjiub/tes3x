import struct
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from tes3x_patch import (DIAGNOSTICS_UPDATE_SIG, PatchError, _mwse_legacy, find_game_instance,
                         find_script_decode_state, find_script_fixup_call)


class Image:
    base = 0x100000

    def __init__(self, data):
        self.data = bytearray(data)
        self.sections = [SimpleNamespace(name='.text', raw=0, rsize=len(data), va=self.base)]

    def off_to_va(self, offset):
        return self.base + offset

    def va_to_off(self, va):
        offset = va - self.base
        return offset if 0 <= offset < len(self.data) else None


class MwsePatchTests(unittest.TestCase):
    def test_decoder_state_and_fixup_call_are_content_located(self):
        ip = 0x100180
        opcode = 0x100184
        decoder = (
            bytes.fromhex('83ec245355568bd957b9') + struct.pack('<I', 0x100188)
            + b'\xe8\x00\x00\x00\x00\xb9' + struct.pack('<I', 0x100188)
            + bytes.fromhex('89442410e8000000008b2d') + struct.pack('<I', ip)
            + bytes.fromhex('8bd08b43580fbf042833f683c5023d26010000a3')
            + struct.pack('<I', opcode) + b'\x89\x2d' + struct.pack('<I', ip)
        )
        data = bytearray(b'\x90' * 0x20 + decoder + b'\x90' * 0x80)
        call = len(data)
        target = 0x100020
        data += b'\x6a\x01\x8b\xcb\xe8' + struct.pack('<i', target - (0x100000 + call + 9))
        data += b'\xa1' + struct.pack('<I', opcode) + b'\x90' * 0x100
        image = Image(data)

        self.assertEqual(find_script_decode_state(image), (target, ip, opcode))
        self.assertEqual(find_script_fixup_call(image, target, opcode), 0x100000 + call + 4)

    def test_game_instance_is_loaded_before_update_call(self):
        update_off = 0x80
        update = Image.base + update_off
        instance = 0x103000
        call_off = 0x30
        data = bytearray(b'\x90' * 0x120)
        data[update_off:update_off + len(DIAGNOSTICS_UPDATE_SIG)] = DIAGNOSTICS_UPDATE_SIG
        data[call_off - 6:call_off] = b'\x8b\x0d' + struct.pack('<I', instance)
        data[call_off:call_off + 5] = (b'\xe8' + struct.pack(
            '<i', update - (Image.base + call_off + 5)))

        self.assertEqual(find_game_instance(Image(data)), instance)

    def test_mwse_patch_requires_script_extension_first(self):
        with self.assertRaisesRegex(PatchError, 'requires script-ext'):
            _mwse_legacy(Image(b'\x90' * 0x100), '', {
                'hooks': {'mwse_fixup': '0x200000', 'script_dispatch': '0x200100'},
            })


if __name__ == '__main__':
    unittest.main()
