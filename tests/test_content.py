import http.client
import json
import tempfile
import time
import unittest
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (Ed25519PrivateKey,
                                                                 Ed25519PublicKey)

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

    def test_activity_requires_ticket_and_keeps_terminal_result(self):
        private = Ed25519PrivateKey.generate()
        public = private.public_key().public_bytes(serialization.Encoding.Raw,
                                                   serialization.PublicFormat.Raw).hex()
        _, body = self.request(f"/v1/ticket/sample?client={public}")
        token = self.signed(body)[1]["token"]

        def report(stage, done=0, total=0, key=private):
            message = f"{token}:{stage}:{done}:{total}".encode()
            signature = key.sign(message).hex()
            return self.request(f"/v1/report/{token}?stage={stage}&done={done}"
                                f"&total={total}&sig={signature}")[0]

        self.assertEqual(self.server.activity_snapshot(), [])
        self.assertEqual(self.request(f"/v1/report/{'0' * 32}?stage=complete")[0], 403)
        self.assertEqual(report("bogus"), 400)
        self.assertEqual(report("downloading", 2, 5, Ed25519PrivateKey.generate()), 403)
        self.assertEqual(report("downloading", 2, 5), 200)
        self.assertEqual(self.request(f"/{token}/file/mod.esp")[0], 200)
        deadline = time.monotonic() + 2
        while self.server.activity_snapshot()[0]["wire"] == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        row = self.server.activity_snapshot()[0]
        self.assertEqual((row["stage"], row["done"], row["wire"], row["file"]),
                         ("downloading", 2, len(b"first revision"), "mod.esp"))
        self.assertNotIn(token, row.values())
        self.assertEqual(report("complete"), 200)
        self.assertEqual(report("failed"), 200)
        self.assertEqual(self.server.activity_snapshot()[0]["stage"], "complete")
        with self.server.lock:
            self.server.activity.clear()
        self.assertEqual(report("complete"), 200)
        self.assertEqual(self.server.activity_snapshot(), [])


if __name__ == "__main__":
    unittest.main()
