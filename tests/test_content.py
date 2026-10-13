import contextlib
import http.client
import hashlib
import io
import json
import ssl
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
            self.deploy, profile="Sample", source={}, folder="Sample", xbe=self.xbe,
            sources=[{"kind": "profile", "name": "Sample", "version": "v1"}]))
        self.library = content.Library(self.root / "state", self.root / "build", [], True,
                                       ("build",))
        self.server = content.ContentServer(self.library, "127.0.0.1", 0, log=lambda _: None)
        self.addCleanup(self.server.close)

    def request(self, path):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        conn = http.client.HTTPSConnection("127.0.0.1", self.server.port, timeout=5,
                                           context=context)
        try:
            conn.request("GET", path)
            response = conn.getresponse()
            self.peer = hashlib.sha256(conn.sock.getpeercert(binary_form=True)).hexdigest()
            return response.status, response.read()
        finally:
            conn.close()

    def signed(self, body):
        public, signature, data = body[:32], body[32:96], body[96:]
        Ed25519PublicKey.from_public_bytes(public).verify(signature, data)
        return public, json.loads(data)

    def ticket(self, entry="sample", build=None, revision=None,
               attempt="0123456789abcdef", key=None, latest=1, world=""):
        key = key or Ed25519PrivateKey.generate()
        public = key.public_key().public_bytes(serialization.Encoding.Raw,
                                               serialization.PublicFormat.Raw).hex()
        if build is None or revision is None:
            offered = self.signed(self.request("/v1/catalog")[1])[1]["entries"][0]
            build = build or offered["build"]
            revision = revision or offered["revision"]
        message = (f"ticket:{entry}:{build}:{revision}:{attempt}:{latest}:"
                   f"{self.library.tls}").encode()
        path = (f"/v1/ticket/{entry}?client={public}&build={build}&revision={revision}"
                f"&attempt={attempt}&latest={latest}"
                f"&sig={key.sign(message).hex()}{world}")
        status, body = self.request(path)
        return status, self.signed(body)[1] if status == 200 else body, key

    def test_catalog_ticket_and_frozen_revision(self):
        status, body = self.request("/v1/catalog")
        self.assertEqual(status, 200)
        key, catalog = self.signed(body)
        self.assertEqual((catalog["tls"], catalog["access"]), (self.peer, "public"))
        entry = catalog["entries"][0]
        self.assertEqual((entry["id"], entry["name"]), ("sample", "Sample"))
        self.assertEqual(entry["sources"],
                         [{"kind": "profile", "name": "Sample", "version": "v1"}])
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
                         (200, {"format": 1, "tls": self.library.tls, "build": first["build"],
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
            self.deploy, profile="Sample", source={}, folder="Sample", xbe=self.xbe,
            sources=[{"kind": "profile", "name": "Sample", "version": "v2"}]))
        self.library.refresh()
        offered = self.signed(self.request("/v1/catalog")[1])[1]["entries"]
        self.assertEqual(len(offered), 2)
        self.assertEqual(offered[0]["build"], first["build"])
        self.assertNotEqual(offered[0]["revision"], first["revision"])
        self.assertEqual(offered[0]["sources"][0]["version"], "v2")
        status, ticket, _ = self.ticket(build=first["build"], revision=first["revision"],
                                        latest=0)
        self.assertEqual(status, 200)
        status, body = self.request(f"/{ticket['token']}/manifest")
        self.assertEqual((status, json.loads(body)["sources"][0]["version"]), (200, "v1"))

    def test_legacy_revision_has_no_sources(self):
        manifest.write(self.deploy, manifest.create(
            self.deploy, profile="Sample", source={}, folder="Sample", xbe=self.xbe))
        self.library.refresh()
        self.library = content.Library(self.root / "state", self.root / "build", [], True,
                                       ("build",))
        self.server.library = self.library
        offered = self.signed(self.request("/v1/catalog")[1])[1]["entries"]
        self.assertEqual(offered[0]["sources"], [])

    def test_invalid_source_metadata_is_not_published(self):
        manifest.write(self.deploy, manifest.create(
            self.deploy, profile="Sample", source={}, folder="Sample", xbe=self.xbe,
            sources=[{"kind": "profile", "name": "Sample", "version": ""}]))
        with self.assertRaisesRegex(ValueError, "revision source"):
            self.library.refresh()

    def test_key_command_reports_existing_public_identity(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            content.main(["key", "--state", str(self.root / "state")])
        public = content.public_key(self.library.key)
        self.assertEqual(output.getvalue().splitlines(),
                         [f"public key {public.hex()}",
                          f"fingerprint {hashlib.blake2b(public, digest_size=16).hexdigest()}"])

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

    def private(self):
        self.server.access = content.Access(self.root / "state", "private")

    def catalog_as(self, key):
        public = key.public_key().public_bytes(serialization.Encoding.Raw,
                                               serialization.PublicFormat.Raw).hex()
        signature = key.sign(f"catalog:{self.library.tls}".encode()).hex()
        status, body = self.request(f"/v1/catalog?client={public}&sig={signature}")
        return status, self.signed(body)[1] if status == 200 else body

    def test_private_library_admits_approved_dashes(self):
        self.private()
        offered = self.signed(self.request("/v1/catalog")[1])[1]
        self.assertEqual((offered["access"], offered["entries"]), ("private", []))
        dash = Ed25519PrivateKey.generate()
        status, waiting = self.catalog_as(dash)
        self.assertEqual((status, waiting["access"], waiting["entries"]), (200, "pending", []))
        build, revision = (self.library.entries["sample"][key] for key in ("build", "revision"))
        self.assertEqual(self.ticket(key=dash, build=build, revision=revision)[0], 403)
        self.assertEqual(content.approve(self.root / "state", waiting["client"][:10], "mine"),
                         dash.public_key().public_bytes(serialization.Encoding.Raw,
                                                        serialization.PublicFormat.Raw).hex())
        status, approved = self.catalog_as(dash)
        self.assertEqual((approved["access"], len(approved["entries"])), ("approved", 1))
        self.assertEqual(self.ticket(key=dash, build=build, revision=revision)[0], 200)
        content.revoke(self.root / "state", waiting["client"])
        self.assertEqual(self.ticket(key=dash, build=build, revision=revision,
                                     attempt="2222222222222222")[0], 403)

    def test_ticket_signature_names_this_certificate(self):
        key = Ed25519PrivateKey.generate()
        public = key.public_key().public_bytes(serialization.Encoding.Raw,
                                               serialization.PublicFormat.Raw).hex()
        entry = self.library.entries["sample"]
        message = (f"ticket:sample:{entry['build']}:{entry['revision']}:0123456789abcdef:1:"
                   f"{'0' * 64}").encode()
        status, _ = self.request(f"/v1/ticket/sample?client={public}&build={entry['build']}"
                                 f"&revision={entry['revision']}&attempt=0123456789abcdef"
                                 f"&latest=1&sig={key.sign(message).hex()}")
        self.assertEqual(status, 403)

    def test_world_authorization_reaches_an_exact_withdrawn_revision(self):
        self.private()
        world = Ed25519PrivateKey.generate()
        world_hex = world.public_key().public_bytes(serialization.Encoding.Raw,
                                                    serialization.PublicFormat.Raw).hex()
        dash = Ed25519PrivateKey.generate()
        client = dash.public_key().public_bytes(serialization.Encoding.Raw,
                                                serialization.PublicFormat.Raw).hex()
        service = content.public_key(self.library.key).hex()
        entry = self.library.entries["sample"]
        build, revision = entry["build"], entry["revision"]

        def authorized(expires, revision=revision, signer=world):
            message = content.delegation_message(client, service, "sample", build, revision,
                                                 expires)
            return (f"&world={world_hex}&expires={expires}"
                    f"&wsig={signer.sign(message).hex()}")

        soon = int(time.time()) + 600
        self.assertEqual(self.ticket(key=dash, build=build, revision=revision,
                                     world=authorized(soon))[0], 403)
        content.delegate(self.root / "state", world_hex, "sample", revision, "World")
        self.library.set_listed("sample", revision, False)
        status, ticket, _ = self.ticket(key=dash, build=build, revision=revision, latest=0,
                                        world=authorized(soon))
        self.assertEqual((status, ticket["revision"]), (200, revision))
        self.assertEqual(self.request(f"/{ticket['token']}/file/mod.esp"),
                         (200, b"first revision"))
        for bad in (authorized(int(time.time()) - 1),
                    authorized(int(time.time()) + 3 * 86400),
                    authorized(soon, signer=Ed25519PrivateKey.generate())):
            self.assertEqual(self.ticket(key=dash, build=build, revision=revision, latest=0,
                                         attempt="3333333333333333", world=bad)[0], 403)
        self.assertEqual(self.library.prune(), [])
        content.undelegate(self.root / "state", world_hex)
        self.assertEqual(self.library.prune(), [revision])
        self.assertFalse((self.root / "state" / "revisions" / revision).exists())
        self.assertNotIn("sample", self.library.history)

    def test_other_process_edits_reach_the_running_catalog(self):
        other = content.Library(self.root / "state", self.root / "state", [], False, ("build",))
        other.set_metadata("sample", "Edited elsewhere")
        offered = self.signed(self.request("/v1/catalog")[1])[1]["entries"]
        self.assertEqual(offered[0]["name"], "Edited elsewhere")
        other.set_listed("sample", offered[0]["revision"], False)
        self.assertEqual(self.signed(self.request("/v1/catalog")[1])[1]["entries"], [])

    def test_catalog_requests_are_rate_limited(self):
        statuses = [self.request("/v1/catalog")[0] for _ in range(content.CATALOG_RATE[0] + 2)]
        self.assertEqual(statuses[0], 200)
        self.assertEqual(statuses[-1], 429)

    def test_bandwidth_cap_is_shared_by_an_address(self):
        self.server.client_rate = 1_000_000
        started = time.monotonic()
        self.server.pace("192.0.2.1", 100_000)
        self.server.pace("192.0.2.1", 100_000)
        self.assertGreaterEqual(time.monotonic() - started, 0.15)
        started = time.monotonic()
        self.server.pace("192.0.2.2", 10_000)
        self.assertLess(time.monotonic() - started, 0.05)

    def test_store_archives_are_shared_served_and_pruned(self):
        archive = b"store archive bytes"
        digest = hashlib.sha256(archive).hexdigest()
        (self.root / "build" / "store").mkdir()
        (self.root / "build" / "store" / f"{digest}.bsa").write_bytes(archive)
        entry = {"sha256": digest, "size": len(archive), "name": "Big", "tier": 512}
        manifest.write(self.deploy, manifest.create(
            self.deploy, profile="Sample", source={}, folder="Sample", xbe=self.xbe,
            store=[entry]))
        self.library.refresh()
        shared = self.root / "state" / "store" / f"{digest}.bsa"
        self.assertEqual(shared.read_bytes(), archive)
        stored = self.library.entries["sample"]
        status, ticket, _ = self.ticket(build=stored["build"], revision=stored["revision"])
        self.assertEqual(status, 200)
        self.assertEqual(self.request(f"/{ticket['token']}/store/{digest}"), (200, archive))
        self.assertEqual(self.request(f"/{ticket['token']}/store/{'0' * 64}")[0], 404)
        self.library.set_listed("sample", stored["revision"], False)
        self.assertEqual(self.library.prune(), [stored["revision"]])
        self.assertFalse(shared.exists())

    def test_one_server_per_state(self):
        held = content.serving_lock(self.root / "state")
        self.addCleanup(held.close)
        self.assertIsNone(content.serving_lock(self.root / "state"))


if __name__ == "__main__":
    unittest.main()
