import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from tes3x.net import passwords
from tes3x.net.proto import admin_secret
from tes3x.net.server import load_admin_secret, load_password, load_server_key


class JoinPasswordTests(unittest.TestCase):
    def test_hash_line_accepts_only_the_password(self):
        line = passwords.join_hash(b"swordfish")
        self.assertTrue(line.startswith("scrypt$"))
        self.assertNotIn("swordfish", line)
        stored = passwords.JoinPassword(line)
        self.assertTrue(stored.check(b"swordfish"))
        self.assertFalse(stored.check(b"swordfisH"))
        self.assertFalse(stored.check(b""))
        self.assertFalse(stored.check(b"x" * 65))

    def test_each_hash_has_its_own_salt(self):
        self.assertNotEqual(passwords.join_hash(b"same"), passwords.join_hash(b"same"))

    def test_plain_password_still_works(self):
        stored = passwords.JoinPassword("swordfish")
        self.assertTrue(stored.check(b"swordfish"))
        self.assertFalse(stored.check(b"swordfis"))

    def test_malformed_hash_is_refused(self):
        with self.assertRaises(ValueError):
            passwords.JoinPassword("scrypt$zz$00")


class AdminSecretTests(unittest.TestCase):
    def test_hash_line_gives_the_same_key_as_the_password(self):
        line = passwords.admin_hash(b"correct horse")
        self.assertNotIn("correct horse", line)
        self.assertEqual(passwords.admin_secret_from(line), admin_secret(b"correct horse"))
        self.assertEqual(passwords.admin_secret_from("correct horse"),
                         admin_secret(b"correct horse"))


class FileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_loaders_read_plain_and_hashed_files(self):
        plain, hashed = self.dir / "plain.txt", self.dir / "hashed.txt"
        plain.write_text("swordfish\n", encoding="utf-8")
        hashed.write_text(passwords.join_hash(b"swordfish") + "\n", encoding="utf-8")
        for path in (plain, hashed):
            loaded = load_password(types.SimpleNamespace(password_file=str(path)))
            self.assertTrue(loaded.check(b"swordfish"), path.name)
        self.assertIsNone(load_password(types.SimpleNamespace(password_file=None)))
        admin = self.dir / "admin.txt"
        admin.write_text(passwords.admin_hash(b"correct horse") + "\n", encoding="utf-8")
        self.assertEqual(load_admin_secret(str(admin)), admin_secret(b"correct horse"))
        admin.write_text("correct horse\n", encoding="utf-8")
        self.assertEqual(load_admin_secret(str(admin)), admin_secret(b"correct horse"))

    def test_a_broken_hash_exits_with_a_message(self):
        bad = self.dir / "bad.txt"
        bad.write_text("scrypt$nothex$00\n", encoding="utf-8")
        with self.assertRaises(SystemExit):
            load_password(types.SimpleNamespace(password_file=str(bad)))

    @unittest.skipIf(os.name == "nt", "modes mean nothing on Windows")
    def test_new_key_is_private_and_readable_files_are_reported(self):
        key = self.dir / "server.key"
        load_server_key(types.SimpleNamespace(key=str(key), world=None))
        self.assertEqual(key.stat().st_mode & 0o077, 0)
        key.chmod(0o644)
        with mock.patch("sys.stderr") as err:
            passwords.warn_if_readable(str(key), "the server key")
        self.assertTrue(err.write.called)

    def test_write_private_replaces_content(self):
        path = self.dir / "p.txt"
        passwords.write_private(str(path), "one\n")
        passwords.write_private(str(path), "two\n")
        self.assertEqual(path.read_text(encoding="utf-8"), "two\n")


if __name__ == "__main__":
    unittest.main()
