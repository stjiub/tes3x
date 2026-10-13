import http.client
import json
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from tes3x import content, manifest


class ContentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.deploy = self.root / "build" / "sample" / "deploy"
        self.deploy.mkdir(parents=True)
        (self.deploy / "mod.esp").write_bytes(b"first revision")
        manifest.write(self.deploy, manifest.create(
            self.deploy, profile="Sample", source={}, folder="Sample"))
        self.library = content.Library(self.root / "state", self.root / "build", [], True,
                                       ("build",))
        self.server = content.ContentServer(self.library, "127.0.0.1", 0, log=lambda _: None)
        self.addCleanup(self.server.close)

    def request(self, path):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=5)
        self.addCleanup(conn.close)
        conn.request("GET", path)
        response = conn.getresponse()
        return response.status, response.read()

    def signed(self, body):
        public, signature, data = body[:32], body[32:96], body[96:]
        Ed25519PublicKey.from_public_bytes(public).verify(signature, data)
        return public, json.loads(data)

    def test_catalog_ticket_and_frozen_revision(self):
        status, body = self.request("/v1/catalog")
        self.assertEqual(status, 200)
        key, catalog = self.signed(body)
        entry = catalog["entries"][0]
        self.assertEqual((entry["id"], entry["name"]), ("sample", "Sample"))
        status, body = self.request("/v1/ticket/sample")
        self.assertEqual(status, 200)
        self.assertEqual(self.signed(body)[0], key)
        ticket = self.signed(body)[1]
        self.assertEqual(ticket["build"], entry["build"])
        status, data = self.request(f"/{ticket['token']}/file/mod.esp")
        self.assertEqual((status, data), (200, b"first revision"))

        (self.deploy / "mod.esp").write_bytes(b"second revision")
        manifest.write(self.deploy, manifest.create(
            self.deploy, profile="Sample", source={}, folder="Sample"))
        self.library.refresh()
        status, body = self.request("/v1/catalog")
        self.assertEqual(status, 200)
        newer = self.signed(body)[1]["entries"][0]
        self.assertNotEqual(newer["build"], entry["build"])
        status, data = self.request(f"/{ticket['token']}/file/mod.esp")
        self.assertEqual((status, data), (200, b"first revision"))

    def test_unknown_ticket_and_unsafe_manifest(self):
        self.assertEqual(self.request("/v1/ticket/missing")[0], 404)
        self.assertEqual(self.request(f"/{'0' * 32}/file/mod.esp")[0], 403)
        self.assertFalse(content.safe_relative("../escape"))
        self.assertFalse(content.safe_relative("a/../../escape"))


if __name__ == "__main__":
    unittest.main()
