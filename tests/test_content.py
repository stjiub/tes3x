import http.client
import hashlib
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
        delta = b"unchanged XBE recipe"
        delta_hash = hashlib.sha256(delta).hexdigest()
        (self.deploy.parent / "deltas").mkdir()
        (self.deploy.parent / "deltas" / f"{delta_hash}.zst").write_bytes(delta)
        self.xbe = [{"delta": {"sha256": delta_hash, "size": len(delta)}}]
        manifest.write(self.deploy, manifest.create(
            self.deploy, profile="Sample", source={}, folder="Sample", xbe=self.xbe))
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

    def ticket(self, entry="sample", build=None, revision=None,
               attempt="0123456789abcdef", key=None, latest=1):
        key = key or Ed25519PrivateKey.generate()
        public = key.public_key().public_bytes(serialization.Encoding.Raw,
                                               serialization.PublicFormat.Raw).hex()
        if build is None or revision is None:
            offered = self.signed(self.request("/v1/catalog")[1])[1]["entries"][0]
            build = build or offered["build"]
            revision = revision or offered["revision"]
        message = f"ticket:{entry}:{build}:{revision}:{attempt}:{latest}".encode()
        path = (f"/v1/ticket/{entry}?client={public}&build={build}&revision={revision}"
                f"&attempt={attempt}&latest={latest}"
                f"&sig={key.sign(message).hex()}")
        status, body = self.request(path)
        return status, self.signed(body)[1] if status == 200 else body, key

    def test_catalog_ticket_and_frozen_revision(self):
        status, body = self.request("/v1/catalog")
        self.assertEqual(status, 200)
        key, catalog = self.signed(body)
        entry = catalog["entries"][0]
        self.assertEqual((entry["id"], entry["name"]), ("sample", "Sample"))
        status, ticket, _ = self.ticket(build=entry["build"], revision=entry["revision"])
        self.assertEqual(status, 200)
        self.assertEqual(self.signed(self.request("/v1/catalog")[1])[0], key)
        self.assertEqual(ticket["build"], entry["build"])
        status, data = self.request(f"/{ticket['token']}/file/mod.esp")
        self.assertEqual((status, data), (200, b"first revision"))

        (self.deploy / "mod.esp").write_bytes(b"second revision")
        manifest.write(self.deploy, manifest.create(
            self.deploy, profile="Sample", source={}, folder="Sample", xbe=self.xbe))
        self.library.refresh()
        status, body = self.request("/v1/catalog")
        self.assertEqual(status, 200)
        offered = self.signed(body)[1]["entries"]
        self.assertEqual(len(offered), 2)
        newer = offered[0]
        self.assertNotEqual(newer["build"], entry["build"])
        self.assertEqual(offered[1]["revision"], entry["revision"])
        status, stale, _ = self.ticket(build=entry["build"], revision=entry["revision"],
                                       attempt="1111111111111111")
        self.assertEqual((status, stale["revision"]), (200, newer["revision"]))
        _, newer_ticket, _ = self.ticket(build=newer["build"],
                                         revision=newer["revision"],
                                         attempt="fedcba9876543210")
        self.assertEqual(newer_ticket["build"], newer["build"])
        status, data = self.request(f"/{newer_ticket['token']}/file/mod.esp")
        self.assertEqual((status, data), (200, b"second revision"))
        status, data = self.request(f"/{ticket['token']}/file/mod.esp")
        self.assertEqual((status, data), (200, b"first revision"))
        status, old_ticket, _ = self.ticket(build=entry["build"],
                                            revision=entry["revision"],
                                            attempt="abcdef0123456789", latest=0)
        self.assertEqual(status, 200)
        self.assertEqual(self.request(f"/{old_ticket['token']}/file/mod.esp"),
                         (200, b"first revision"))

    def test_unknown_ticket_and_unsafe_manifest(self):
        self.assertEqual(self.ticket(entry="missing")[0], 404)
        self.assertEqual(self.request(f"/{'0' * 32}/file/mod.esp")[0], 403)
        self.assertFalse(content.safe_relative("../escape"))
        self.assertFalse(content.safe_relative("a/../../escape"))

    def test_publication_metadata_withdrawal_and_restart(self):
        first = self.signed(self.request("/v1/catalog")[1])[1]["entries"][0]
        self.library.set_metadata("sample", "Library Sample", "The original build")
        listed = self.signed(self.request("/v1/catalog")[1])[1]["entries"]
        self.assertEqual((listed[0]["name"], listed[0]["description"]),
                         ("Library Sample", "The original build"))

        self.library.set_listed("sample", first["revision"], False)
        self.assertEqual(self.signed(self.request("/v1/catalog")[1])[1]["entries"], [])
        status, stale, _ = self.ticket(build=first["build"], revision=first["revision"])
        self.assertEqual((status, stale),
                         (200, {"format": 1, "build": first["build"],
                                "revision": first["revision"]}))

        self.library = content.Library(self.root / "state", self.root / "build", [], True,
                                       ("build",))
        self.server.library = self.library
        self.assertEqual(self.signed(self.request("/v1/catalog")[1])[1]["entries"], [])
        self.library.set_listed("sample", first["revision"], True)
        restored = self.signed(self.request("/v1/catalog")[1])[1]["entries"][0]
        self.assertEqual((restored["revision"], restored["name"]),
                         (first["revision"], "Library Sample"))

    def test_same_build_id_can_have_two_manifest_revisions(self):
        first = self.signed(self.request("/v1/catalog")[1])[1]["entries"][0]
        manifest.write(self.deploy, manifest.create(
            self.deploy, profile="Renamed", source={}, folder="Sample", xbe=self.xbe))
        self.library.refresh()
        offered = self.signed(self.request("/v1/catalog")[1])[1]["entries"]
        self.assertEqual(len(offered), 2)
        self.assertEqual(offered[0]["build"], first["build"])
        self.assertNotEqual(offered[0]["revision"], first["revision"])
        status, ticket, _ = self.ticket(build=first["build"], revision=first["revision"],
                                        latest=0)
        self.assertEqual(status, 200)
        status, body = self.request(f"/{ticket['token']}/manifest")
        self.assertEqual((status, json.loads(body)["profile"]), (200, "Sample"))

    def test_activity_requires_ticket_and_keeps_terminal_result(self):
        private = Ed25519PrivateKey.generate()
        _, ticket, _ = self.ticket(key=private)
        token = ticket["token"]

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

    def test_renewed_ticket_keeps_attempt_and_rejects_other_bindings(self):
        private = Ed25519PrivateKey.generate()
        status, first, _ = self.ticket(key=private)
        self.assertEqual(status, 200)
        attempt = first["attempt"]
        token = first["token"]
        self.assertEqual(self.request(f"/{token}/file/mod.esp")[0], 200)
        message = f"{token}:failed:0:0".encode()
        self.assertEqual(self.request(f"/v1/report/{token}?stage=failed&done=0&total=0"
                                      f"&sig={private.sign(message).hex()}")[0], 200)
        status, second, _ = self.ticket(key=private, attempt=attempt)
        self.assertEqual(status, 200)
        self.assertNotEqual(first["token"], second["token"])
        self.assertEqual(second["attempt"], attempt)
        row = self.server.activity_snapshot()[0]
        self.assertEqual((row["stage"], row["tickets"]), ("retrying", 2))
        self.assertEqual(row["wire"], len(b"first revision"))
        self.assertEqual(self.request(f"/v1/report/{token}?stage=failed&done=0&total=0"
                                      f"&sig={private.sign(message).hex()}")[0], 200)
        self.assertEqual(self.server.activity_snapshot()[0]["stage"], "retrying")
        self.assertEqual(self.ticket(attempt=attempt)[0], 403)
        self.assertEqual(len(self.server.activity_snapshot()), 1)


if __name__ == "__main__":
    unittest.main()
