import hashlib
import http.client
import os
from pathlib import Path
import socket
import struct
import tempfile
import unittest

from tes3x import manifest, netbuild


class BuildDownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.data = bytes(range(256)) * 8192
        (root / 'archive.bsa').write_bytes(self.data)
        manifest.write(root, manifest.create(root, profile='test', source={}))
        self.logs = []
        self.server = netbuild.BuildServer(netbuild.Build(root), '127.0.0.1', 0,
                                          log=self.logs.append)
        self.addCleanup(self.server.close)
        self.token = netbuild.BUILD_BODY.unpack(self.server.ticket())[-1].hex()

    def request(self, byte_range=None, token=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.port, timeout=5)
        self.addCleanup(conn.close)
        headers = {'Range': byte_range} if byte_range else {}
        conn.request('GET', f'/{token or self.token}/file/archive.bsa', headers=headers)
        return conn.getresponse()

    def test_resume_after_connection_reset(self):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.port, timeout=5)
        conn.request('GET', f'/{self.token}/file/archive.bsa')
        response = conn.getresponse()
        prefix = response.read(73001)
        conn.sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER,
                            struct.pack('hh' if os.name == 'nt' else 'ii', 1, 0))
        conn.close()
        response.close()
        resumed = self.request(f'bytes={len(prefix)}-')
        self.assertEqual(resumed.status, 206)
        self.assertEqual(resumed.getheader('Content-Range'),
                         f'bytes {len(prefix)}-{len(self.data) - 1}/{len(self.data)}')
        recovered = prefix + resumed.read()
        self.assertEqual(hashlib.sha256(recovered).digest(), hashlib.sha256(self.data).digest())
        self.assertEqual(self.request().read(), self.data)

    def test_ranges_and_ticket_policy(self):
        for offset in (0, 1, len(self.data) - 1):
            with self.subTest(offset=offset):
                response = self.request(f'bytes={offset}-')
                self.assertEqual(response.status, 206)
                self.assertEqual(int(response.getheader('Content-Length')), len(self.data) - offset)
                self.assertEqual(response.read(), self.data[offset:])
        for value in ('bytes=-1', 'bytes=1-2', 'bytes=2-,3-', 'items=0-',
                      f'bytes={len(self.data)}-', 'bytes=999999999999-'):
            with self.subTest(value=value):
                response = self.request(value)
                self.assertEqual(response.status, 416)
                self.assertEqual(response.read(), b'')
        response = self.request('bytes=1-', token='0' * 32)
        self.assertEqual(response.status, 403)
        response.read()

    def test_range_does_not_bypass_retail_policy(self):
        self.server.build.origins.clear()
        response = self.request('bytes=1-')
        self.assertEqual(response.status, 404)
        response.read()

    def test_overlay_advertises_separate_retail_dependency(self):
        root = Path(self.temp.name)
        retail = root / 'clean'
        (retail / 'Data Files').mkdir(parents=True)
        (retail / 'morrowind.xbe').write_bytes(b'retail executable')
        (retail / 'Data Files/Morrowind.esm').write_bytes(b'retail master')
        (retail / 'Data Files/Morrowind.bsa').write_bytes(self.data)
        (retail / 'release.nfo').write_bytes(b'exclude')
        (retail / 'Morrowind.ini').write_bytes(b'exclude')
        game = root / 'overlay'
        game.mkdir()
        (game / 'mod.esp').write_bytes(b'plugin')
        manifest.write(game, manifest.create(game, profile='test', source={},
                                             install_layout='overlay'))
        original_id = manifest.load(game)['build']
        build = netbuild.Build(game, origins=('build', 'retail', 'xbe'), retail_base=retail)
        served = manifest.parse(build.data)
        self.assertEqual(served['build'], original_id)
        self.assertEqual(served['manager'], 2)
        dependency = served['retail_base']
        self.assertEqual(dependency['install_layout'], 'retail-base')
        self.assertEqual(set(dependency['files']),
                         {'morrowind.xbe', 'Data Files/Morrowind.esm', 'Data Files/Morrowind.bsa'})
        self.assertEqual(build.retail_file('data files/morrowind.bsa'),
                         retail / 'Data Files/Morrowind.bsa')
        self.assertIsNone(build.file('Data Files/Morrowind.bsa'))
        self.assertIsNone(build.retail_file('../archive.bsa'))
        build.origins = {'build'}
        self.assertIsNone(build.retail_file('Data Files/Morrowind.bsa'))
        self.assertIsNone(build.retail_file('morrowind.xbe'))
