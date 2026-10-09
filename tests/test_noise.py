import ctypes
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import tes3x.net as tes3x_net

HOOKS = Path(__file__).resolve().parents[1] / 'hooks'

# Noise_XX_25519_ChaChaPoly_BLAKE2b from cacophony's test vectors (vectors/cacophony.txt).
PROLOGUE = bytes.fromhex('4a6f686e2047616c74')
INIT_STATIC = bytes.fromhex('e61ef9919cde45dd5f82166404bd08e38bceb5dfdfded0a34c8df7ed542214d1')
INIT_EPHEMERAL = bytes.fromhex('893e28b9dc6ca8d611ab664754b8ceb7bac5117349a4439a6b0569da977c464a')
RESP_STATIC = bytes.fromhex('4a3acbfdb163dec651dfa3194dece676d437029c62a408b4c5ea9114246e4893')
RESP_EPHEMERAL = bytes.fromhex('bbdb4cdbd309f1a1f2e1456967fe288cadd6f712d65dc7b7793d5e63da6b375b')
HANDSHAKE_HASH = bytes.fromhex(
    '8cf47d7b3cb5804c0109d48e8bcdbee2cbb65687d8ea2c92994ca361fb86151a'
    'd93627b98936cbb32de56e8abb21def3925011ac3e35db9cbeea73ab9a4392c2')
MESSAGES = [(bytes.fromhex(p), bytes.fromhex(c)) for p, c in (
    ('4c756477696720766f6e204d69736573',
     'ca35def5ae56cec33dc2036731ab14896bc4c75dbb07a61f879f8e3afa4c79444c756477696720766f6e204d69736573'),
    ('4d757272617920526f746862617264',
     '95ebc60d2b1fa672c1f46a8aa265ef51bfe38e7ccb39ec5be34069f1448088430505b6745ce64a5f33f0e8e3b83f11ce'
     '8802bca507f4f2d8b564dbe277e1966116e132faa2dfd70b8b077b9f94b913df5056ae1319469b824a98d54bbaa82c'
     '325595587064f978c4b6d104f7596e6f'),
    ('462e20412e20486179656b',
     '99579e1c1ee15e422a57ddd6b16d37087b17558e8369c18991b4b2ca3a824abf904cdcf5458b5431a75af034ca9e9b98'
     '2de039eaaf156775e2d580cd4e5ebae89c3f8cb2594b556d8a8169'),
    ('4361726c204d656e676572', 'fc56eea290b3f3a21aac0c70cd5787b5ee99be37d2f4d751329b55'),
    ('4a65616e2d426170746973746520536179',
     'bb31c9da10d5639a4cdb88a12f5c61de41bbc7df09bf75d94f8184fe4157f5c68f'),
)]


class PythonNoiseTests(unittest.TestCase):
    def test_matches_the_vector(self):
        init = tes3x_net.Noise(True, INIT_STATIC, INIT_EPHEMERAL, PROLOGUE)
        resp = tes3x_net.Noise(False, RESP_STATIC, RESP_EPHEMERAL, PROLOGUE)
        self.assertEqual(init.write1(MESSAGES[0][0]), MESSAGES[0][1])
        self.assertEqual(resp.read1(MESSAGES[0][1]), MESSAGES[0][0])
        self.assertEqual(resp.write2(MESSAGES[1][0]), MESSAGES[1][1])
        self.assertEqual(init.read2(MESSAGES[1][1]), MESSAGES[1][0])
        self.assertEqual(init.write3(MESSAGES[2][0]), MESSAGES[2][1])
        self.assertEqual(resp.read3(MESSAGES[2][1]), MESSAGES[2][0])
        self.assertEqual(init.h, HANDSHAKE_HASH)
        self.assertEqual(resp.rs, tes3x_net.x25519_public(INIT_STATIC))
        send, receive = init.split()
        self.assertEqual(resp.split(), (send, receive))
        self.assertEqual(tes3x_net.seal(receive, 0, b'', MESSAGES[3][0]), MESSAGES[3][1])
        self.assertEqual(tes3x_net.seal(send, 0, b'', MESSAGES[4][0]), MESSAGES[4][1])

    def test_tampered_message_is_refused(self):
        init = tes3x_net.Noise(True, INIT_STATIC, INIT_EPHEMERAL, PROLOGUE)
        init.write1(MESSAGES[0][0])
        bad = bytearray(MESSAGES[1][1])
        bad[40] ^= 1
        with self.assertRaises(ValueError):
            init.read2(bytes(bad))
        self.assertIsNone(tes3x_net.unseal(bytes(32), 0, b'', b'short'))


class CNoiseTests(unittest.TestCase):
    """tes3xnoise.c and Monocypher, built for the PC, against the same vector."""

    @classmethod
    def setUpClass(cls):
        try:
            from tes3x.payload import find_tool  # where the payload build finds clang
            clang = find_tool('clang')
        except Exception as error:
            raise unittest.SkipTest(f'clang not found: {error!r}')
        cls.folder = tempfile.TemporaryDirectory()
        dll = Path(cls.folder.name) / 'tes3xnoise.dll'
        # an MSVC-target clang exports nothing from a DLL unless asked; a mingw one exports all
        target = subprocess.run([clang, '-dumpmachine'], capture_output=True, text=True).stdout
        exports = [f'-Wl,/EXPORT:noise_{name}' for name in
                   ('start', 'write1', 'read2', 'write3', 'split', 'seal', 'open')
                   ] if 'msvc' in target else []
        built = subprocess.run([clang, '-shared', '-O2', '-o', str(dll), str(HOOKS / 'tes3xnoise.c'),
                                str(HOOKS / 'monocypher.c'), *exports], capture_output=True, text=True)
        if built.returncode:
            raise unittest.SkipTest('clang could not build a PC library: ' + built.stderr[-200:])
        cls.lib = ctypes.CDLL(str(dll))
        cls.lib.noise_read2.restype = ctypes.c_int

    @classmethod
    def tearDownClass(cls):
        handle = cls.lib._handle
        del cls.lib
        if os.name == 'nt':
            ctypes.windll.kernel32.FreeLibrary(ctypes.c_void_p(handle))
        cls.folder.cleanup()

    def test_initiator_matches_the_vector(self):
        lib, st, out = self.lib, ctypes.create_string_buffer(1024), ctypes.create_string_buffer(512)
        lib.noise_start(st, PROLOGUE, len(PROLOGUE), INIT_STATIC, INIT_EPHEMERAL)
        lib.noise_write1(st, out, MESSAGES[0][0], len(MESSAGES[0][0]))
        self.assertEqual(out.raw[:len(MESSAGES[0][1])], MESSAGES[0][1])
        n = lib.noise_read2(st, MESSAGES[1][1], len(MESSAGES[1][1]), out)
        self.assertEqual(out.raw[:n], MESSAGES[1][0])
        lib.noise_write3(st, out, MESSAGES[2][0], len(MESSAGES[2][0]))
        self.assertEqual(out.raw[:len(MESSAGES[2][1])], MESSAGES[2][1])
        send, receive = ctypes.create_string_buffer(32), ctypes.create_string_buffer(32)
        lib.noise_split(st, send, receive)
        plain = MESSAGES[4][0]
        lib.noise_seal(send.raw, ctypes.c_ulonglong(0), None, 0, plain, len(plain), out)
        self.assertEqual(out.raw[:len(plain) + 16], MESSAGES[4][1])
        cipher = MESSAGES[3][1]
        self.assertEqual(lib.noise_open(receive.raw, ctypes.c_ulonglong(0), None, 0, cipher,
                                        len(cipher), out), 0)
        self.assertEqual(out.raw[:len(cipher) - 16], MESSAGES[3][0])
        bad = bytes([cipher[0] ^ 1]) + cipher[1:]
        self.assertNotEqual(lib.noise_open(receive.raw, ctypes.c_ulonglong(0), None, 0, bad,
                                           len(bad), out), 0)

    def test_tampered_message_two_is_refused(self):
        lib, st, out = self.lib, ctypes.create_string_buffer(1024), ctypes.create_string_buffer(512)
        lib.noise_start(st, PROLOGUE, len(PROLOGUE), INIT_STATIC, INIT_EPHEMERAL)
        lib.noise_write1(st, out, MESSAGES[0][0], len(MESSAGES[0][0]))
        bad = bytearray(MESSAGES[1][1])
        bad[40] ^= 1
        self.assertEqual(lib.noise_read2(st, bytes(bad), len(bad), out), -1)
        self.assertEqual(lib.noise_read2(st, MESSAGES[1][1][:60], 60, out), -1)


if __name__ == '__main__':
    unittest.main()
