import ftplib
import io
import posixpath
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from tes3x_deploy import ensure_dirs, ftp_basename, remote_tree, verify_uploads


class FakeFtp:
    def __init__(self):
        self.current = "/"
        self.dirs = {"/", "/F", "/F/Games"}
        self.entries = {}
        self.files = {}
        self.calls = []

    def resolve(self, path):
        if len(path) >= 2 and path[1] == ":":
            path = "/" + path[0] + path[2:]
        elif not path.startswith("/"):
            path = posixpath.join(self.current, path)
        return posixpath.normpath(path)

    def cwd(self, path):
        target = self.resolve(path)
        self.calls.append(("cwd", target))
        if target not in self.dirs:
            raise ftplib.error_perm("550 directory not found")
        self.current = target

    def retrlines(self, command, callback):
        self.calls.append(("retrlines", self.current, command))
        if command != "LIST":
            raise AssertionError(command)
        for kind, name, size in self.entries.get(self.current, []):
            mode = "d" if kind == "dir" else "-"
            callback(f"{mode}rwxr-xr-x 1 xbox xbox {size} Jan 01 00:00 {name}")

    def mkd(self, path):
        target = self.resolve(path)
        self.calls.append(("mkd", target))
        if target in self.dirs:
            raise ftplib.error_temp("450 already exists")
        if posixpath.dirname(target) not in self.dirs:
            raise ftplib.error_perm("550 parent not found")
        self.dirs.add(target)

    def storbinary(self, command, stream, blocksize=8192):
        self.calls.append(("storbinary", self.current, command, blocksize))
        verb, name = command.split(" ", 1)
        if verb != "STOR" or "/" in name or ":" in name:
            raise ftplib.error_perm("550 storing in root not allowed")
        self.files[posixpath.join(self.current, name)] = stream.read()

    def retrbinary(self, command, callback, blocksize=8192):
        self.calls.append(("retrbinary", self.current, command, blocksize))
        verb, name = command.split(" ", 1)
        if verb != "RETR":
            raise AssertionError(command)
        callback(self.files[posixpath.join(self.current, name)])


class DeployFtpTests(unittest.TestCase):
    def test_remote_tree_uses_cwd_and_restores_parent(self):
        ftp = FakeFtp()
        base = "/F/Games/Test"
        data = base + "/Data Files"
        ftp.dirs.update((base, data))
        ftp.entries[base] = [("file", "default.xbe", 100), ("dir", "Data Files", 0)]
        ftp.entries[data] = [("file", "Morrowind.esm", 200)]

        self.assertEqual(remote_tree(ftp, "F:/Games/Test"), {
            "default.xbe": 100,
            "Data Files/Morrowind.esm": 200,
        })
        self.assertEqual(ftp.current, base)
        self.assertTrue(all(call[-1] == "LIST" for call in ftp.calls
                            if call[0] == "retrlines"))

    def test_first_upload_creates_target_and_stores_relative(self):
        ftp = FakeFtp()
        path = "F:/Games/TES3XFTPProbe/Data Files/canary.txt"
        ensure_dirs(ftp, path, set())
        name = ftp_basename(ftp, path)
        ftp.storbinary("STOR " + name, io.BytesIO(b"probe"), blocksize=64 * 1024)

        self.assertEqual(ftp.current, "/F/Games/TES3XFTPProbe/Data Files")
        self.assertEqual(ftp.files[ftp.current + "/canary.txt"], b"probe")
        self.assertEqual(name, "canary.txt")

    def test_missing_remote_tree_is_empty(self):
        self.assertEqual(remote_tree(FakeFtp(), "F:/Games/NewTarget"), {})

    def test_hash_verification_reads_remote_upload(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "a.bin"
            source.write_bytes(b"content")
            ftp = FakeFtp()
            base = "/F/Games/Test"
            ftp.dirs.add(base)
            ftp.entries[base] = [("file", "a.bin", 7)]
            ftp.files[base + "/a.bin"] = b"content"
            verify_uploads(ftp, base, {"a.bin": (7, 0, str(source))}, ["a.bin"], "hash")
            ftp.files[base + "/a.bin"] = b"corrupt"
            with self.assertRaisesRegex(RuntimeError, "hash verification failed"):
                verify_uploads(ftp, base, {"a.bin": (7, 0, str(source))}, ["a.bin"], "hash")


if __name__ == "__main__":
    unittest.main()
