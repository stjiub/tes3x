import contextlib
import io
import struct
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tes3x import content, manifest
from tes3x.net import proto as p
from tes3x.net.server import Server


class Admitted(Exception):
    pass


class WorldSourceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        deploy = self.root / "build" / "net" / "deploy"
        deploy.mkdir(parents=True)
        (deploy / "mod.esp").write_bytes(b"plugin")
        manifest.write(deploy, manifest.create(deploy, profile="Net", source={}))
        self.state = self.root / "state"
        self.library = content.Library(self.state, self.root / "build", [deploy], False,
                                       ("build",))
        self.row = self.library.entries["net"]
        self.world = content.WorldSource.configure(
            content.delegation_identity(self.root / "world" / "content-delegation.key"),
            entry="net", state=self.state, label="World")

    def test_local_library_fills_the_source_and_trusts_the_world(self):
        self.assertEqual((self.world.host, self.world.port), ("", content.PORT))
        self.assertEqual((self.world.build, self.world.revision),
                         (self.row["build"], self.row["revision"]))
        trusted = content.read_access(self.state)["delegates"][self.world.delegate]
        self.assertEqual(trusted["revisions"], [["net", self.row["revision"]]])

    def test_block_authorizes_only_the_named_dash(self):
        dash = Ed25519PrivateKey.generate()
        dash_key = dash.public_key().public_bytes(serialization.Encoding.Raw,
                                                  serialization.PublicFormat.Raw)
        block = self.world.block(dash_key)
        self.assertEqual(block[:4], b"CSRC")
        self.assertEqual(struct.unpack_from("<H", block, 4)[0], len(block) - 6)
        body = block[6:]
        self.assertEqual(body[:32].hex(), self.world.service_key)
        self.assertEqual(struct.unpack_from("<H", body, 32)[0], content.PORT)
        host, entry, rest = body[34:].split(b"\0", 2)
        self.assertEqual((host, entry), (b"", b"net"))
        build, revision, world = rest[:32].hex(), rest[32:64].hex(), rest[64:96].hex()
        expires = struct.unpack_from("<Q", rest, 96)[0]
        signature = rest[104:168].hex()
        self.assertEqual(len(rest), 168)
        self.assertGreater(expires, time.time())
        access = content.Access(self.state, "private")
        self.assertTrue(access.delegated(dash_key.hex(), self.world.service_key, "net", build,
                                         revision, world, expires, signature))
        self.assertFalse(access.delegated(bytes(32).hex(), self.world.service_key, "net",
                                          build, revision, world, expires, signature))
        self.assertEqual(self.world.block(bytes(32))[-72:], bytes(72))


class RevisionEnforcementTests(unittest.TestCase):
    def setUp(self):
        self.server = object.__new__(Server)
        self.server.password = None
        self.server.bans = {kind: set() for kind in p.BAN_KINDS}
        self.server.refuse = Mock()
        self.server.handshake_client = Mock()
        self.server.dash_character = Mock(return_value='')
        self.server.build_server = None
        self.server.clients = {}
        self.server.pinned = None
        self.server.adopt_world = Mock(side_effect=Admitted)
        self.server.args = SimpleNamespace(name='Test', description='', max_players=8)
        self.server.send = Mock()
        self.build, self.revision = bytes(range(32)), bytes(range(32, 64))
        self.server.content_source = SimpleNamespace(
            build=self.build.hex(), revision=self.revision.hex(),
            block=lambda dash: b"CSRC" + dash)

    def hello(self, build_id, revision, selection=bytes(32), plugins=3):
        packet = bytes(p.T3MP.size) + p.HELLO_BODY.pack(bytes(6), 0, 0, plugins, *[0.] * 6,
                                                      build_id, revision, selection)
        self.server.pinned = None
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                self.server.hello(packet, ('192.0.2.1', 1000), (b'key', (bytes(32),) * 2),
                                  p.MANAGER_VERSION, 1, 0, 0, '', 100.)
            except Admitted:
                return True
        return False

    def test_exact_revision_and_no_local_selection(self):
        self.assertTrue(self.hello(self.build, self.revision))
        self.assertTrue(self.hello(self.build, bytes(32)))  # deployed without a revision
        self.assertTrue(self.hello(bytes(32), bytes(32)))  # no manifest: load order alone
        for build_id, revision, selection in ((bytes(32), self.revision, bytes(32)),
                                              (self.build, bytes(range(1, 33)), bytes(32)),
                                              (self.build, self.revision, b"\xff" * 32)):
            if not any(build_id):
                continue
            self.server.refuse.reset_mock()
            self.assertFalse(self.hello(build_id, revision, selection))
            self.assertEqual(self.server.refuse.call_args.args[-1], p.REFUSED_STALE)
        self.assertFalse(self.hello(bytes(range(1, 33)), self.revision))

    def test_dash_gets_the_source_for_its_identity(self):
        identity = bytes(range(64, 96))
        self.hello(bytes(32), identity, plugins=p.MANAGER)
        body = self.server.send.call_args.args[2]
        self.assertTrue(body.endswith(b"CSRC" + identity))


if __name__ == "__main__":
    unittest.main()
