import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import tes3x_manager
import tes3x_release
from tes3x_release import ReleaseError, check_release, make_manager_release


def xbe(title_id, tail=b''):
    data = bytearray(b'XBEH' + bytes(0x300))
    struct.pack_into('<I', data, 0x104, 0x10000)
    struct.pack_into('<I', data, 0x118, 0x10180)
    struct.pack_into('<I', data, 0x188, title_id)
    return bytes(data) + tail


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.key = self.dir / 'release.pem'
        public = tes3x_release.keygen(self.key, 'correct horse')
        self.public = self.dir / 'release.pub'
        self.public.write_text(public.hex() + '\n', encoding='ascii')
        (self.dir / 'm.xbe').write_bytes(xbe(1, b'TES3X-MANAGER-VERSION 0.12.3\0'))
        (self.dir / 'l.xbe').write_bytes(xbe(1))

    def tearDown(self):
        self.tmp.cleanup()

    def release(self):
        out = self.dir / 'out'
        version = make_manager_release(out, self.key, self.dir / 'm.xbe', self.dir / 'l.xbe',
                                       'correct horse')
        return out, version

    def test_a_release_carries_the_managers_own_version_and_checks(self):
        out, version = self.release()
        self.assertEqual(version, '0.12.3')
        release = check_release(out, self.public)
        self.assertEqual(release['product'], 'tes3x-manager')
        self.assertEqual(set(release['files']), {'manager.xbe', 'launcher.xbe'})

    def test_a_changed_file_or_release_is_refused(self):
        out, _ = self.release()
        (out / 'launcher.xbe').write_bytes(xbe(2))
        with self.assertRaisesRegex(ReleaseError, 'launcher.xbe'):
            check_release(out, self.public)
        out, _ = self.release()
        text = (out / 'release.json').read_bytes().replace(b'0.12.3', b'9.0.0')
        (out / 'release.json').write_bytes(text)
        with self.assertRaisesRegex(ReleaseError, 'signature'):
            check_release(out, self.public)

    def test_another_key_is_refused(self):
        out, _ = self.release()
        other = tes3x_release.keygen(self.dir / 'other.pem', '')
        with self.assertRaisesRegex(ReleaseError, 'signature'):
            check_release(out, other.hex())

    def test_xbes_must_be_a_manager_and_a_launcher(self):
        with self.assertRaisesRegex(ReleaseError, 'no manager version'):
            make_manager_release(self.dir / 'x', self.key, self.dir / 'l.xbe',
                                 self.dir / 'l.xbe', 'correct horse')
        with self.assertRaisesRegex(ReleaseError, 'is a manager'):
            make_manager_release(self.dir / 'x', self.key, self.dir / 'm.xbe',
                                 self.dir / 'm.xbe', 'correct horse')

    def test_passphrase_is_required_and_can_change(self):
        with self.assertRaises(SystemExit):
            tes3x_release.load_private(self.key, 'wrong')
        with mock.patch.dict('os.environ', {tes3x_release.PASSPHRASE_ENV: 'correct horse'}):
            private = tes3x_release.load_private(self.key)
        before = tes3x_release.public_bytes(private)
        self.assertEqual(tes3x_release.protect(self.key, private, 'new one'), before)
        self.assertEqual(tes3x_release.public_bytes(
            tes3x_release.load_private(self.key, 'new one')), before)
        tes3x_release.protect(self.key, private, '')
        self.assertEqual(tes3x_release.public_bytes(tes3x_release.load_private(self.key)), before)


class StageTests(unittest.TestCase):
    def test_the_launcher_is_default_xbe_and_the_manager_slot_a(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / 'm.xbe').write_bytes(xbe(0xFFFF0002, b'manager'))
            (tmp / 'l.xbe').write_bytes(xbe(0xFFFF0002, b'launcher'))
            tree = tes3x_manager.stage(tmp / 'out', tmp / 'm.xbe', tmp / 'l.xbe',
                                       'F:/Games/TES3XManager')
            self.assertTrue((tree / 'default.xbe').read_bytes().endswith(b'launcher'))
            self.assertTrue((tree / 'a' / 'default.xbe').read_bytes().endswith(b'manager'))
            manifest = json.loads((tree / 'tes3xbuild.json').read_text(encoding='utf-8'))
            self.assertEqual({manifest['files'][n]['origin'] for n in
                              ('default.xbe', 'a/default.xbe')}, {'build'})
            self.assertIn('Manager=F:\\Games\\TES3XManager\\default.xbe',
                          (tmp / 'out' / 'console.ini').read_text(encoding='latin-1'))


if __name__ == '__main__':
    unittest.main()
