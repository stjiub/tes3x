import ftplib
import io
import posixpath
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from tes3x_deploy import (CLUSTER, deployed_manifest, ensure_dirs, ftp_basename,
                          legacy_manifest, on_disk, owner_conflicts, parse_drives, pool_plan,
                          read_manifest, remote_tree, upload_file, verify_uploads)
import tes3x_manifest


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

    def storbinary(self, command, stream, blocksize=8192, callback=None):
        self.calls.append(("storbinary", self.current, command, blocksize))
        verb, name = command.split(" ", 1)
        if verb != "STOR" or "/" in name or ":" in name:
            raise ftplib.error_perm("550 storing in root not allowed")
        data = stream.read()
        self.files[posixpath.join(self.current, name)] = data
        if callback:
            callback(data)

    def retrbinary(self, command, callback, blocksize=8192):
        self.calls.append(("retrbinary", self.current, command, blocksize))
        verb, name = command.split(" ", 1)
        if verb != "RETR":
            raise AssertionError(command)
        path = posixpath.join(self.current, name)
        if path not in self.files:
            raise ftplib.error_perm("550 file not found")
        callback(self.files[path])

    def close(self):
        self.calls.append(("close",))


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

    def test_upload_reconnects_and_retries_the_named_file(self):
        class FlakyFtp(FakeFtp):
            def storbinary(self, command, stream, blocksize=8192, callback=None):
                raise ftplib.error_temp("426 connection closed")

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "asset.bin"
            source.write_bytes(b"content")
            first, second = FlakyFtp(), FakeFtp()
            first.dirs.add("/F/Games/Test")
            second.dirs.add("/F/Games/Test")
            progress = []
            with patch("tes3x_deploy.tes3x_ftp.connect", return_value=second), \
                    patch("tes3x_deploy.time.sleep"):
                result = upload_file(first, SimpleNamespace(), "F:/Games/Test/asset.bin",
                                     source, set(), lambda amount, reset:
                                     progress.append((amount, reset)), retries=1)
            self.assertIs(result, second)
            self.assertEqual(second.files["/F/Games/Test/asset.bin"], b"content")
            self.assertEqual(progress, [(0, True), (0, True), (7, False)])

    def test_owner_conflicts(self):
        base = "F:/Games/M"
        self.assertEqual(owner_conflicts(base, {}, None, "main"), [])
        untracked = owner_conflicts(base, {"default.xbe": 2048}, None, "main")
        self.assertIn("1 files (2.0 KB) that TES3X did not deploy", untracked[0])
        mine = {"profile": "main", "deployed": "d", "files": {}}
        self.assertEqual(owner_conflicts(base, {"default.xbe": 1}, mine, "main"), [])
        self.assertIn("profile 'main', deployed d",
                      owner_conflicts(base, {"default.xbe": 1}, mine, "tr")[0])
        unstamped = legacy_manifest({"default.xbe": [1, "x"]})
        self.assertEqual(owner_conflicts(base, {"default.xbe": 1}, unstamped, "tr"), [])

    def test_old_deploy_record_reads_as_a_manifest(self):
        ftp = FakeFtp()
        base = "/F/Games/M"
        ftp.dirs.add(base)
        ftp.files[base + "/tes3xdeploy.json"] = (
            b'{"default.xbe": [1, "ab"], ":build": {"profile": "main", "save_pool": "5433ABCD",'
            b' "deployed": "d"}}')
        manifest = read_manifest(ftp, "F:/Games/M")
        self.assertEqual(manifest["profile"], "main")
        self.assertEqual(manifest["save_pool"], {"id": "5433ABCD"})
        self.assertEqual(manifest["files"], {"default.xbe": {"size": 1, "sha1": "ab"}})
        ftp.files[base + "/tes3xbuild.json"] = b'{"format": 1, "profile": "new", "files": {}}'
        self.assertEqual(read_manifest(ftp, "F:/Games/M")["profile"], "new")
        self.assertIsNone(read_manifest(ftp, "F:/Games/Other"))

    def test_deployed_manifest_records_the_console(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Data Files").mkdir()
            (root / "Data Files" / "a.esp").write_bytes(b"plugin")
            (root / "default.xbe").write_bytes(b"xbe")
            built = tes3x_manifest.create(root, profile="main", source={"kind": "pipeline"},
                                          plugins=["a.esp"])
            local = {"Data Files/a.esp": (6, 0, str(root / "Data Files" / "a.esp"))}
            previous = {"files": {"default.xbe": {"size": 3, "sha256": "old", "serve": False},
                                  "Morrowind.ini": {"size": 1, "sha1": "x"},
                                  "data files/A.esp": {"size": 1, "sha256": "stale"}}}
            out = deployed_manifest(built, {}, local, {"Data Files/a.esp": "new"}, previous)
            self.assertEqual(out["profile"], "main")
            self.assertEqual(out["plugins"], ["a.esp"])
            self.assertIn("deployed", out)
            self.assertEqual(out["files"], {
                "Data Files/a.esp": {"size": 6, "sha256": "new", "serve": True},
                "default.xbe": {"size": 3, "sha256": "old", "serve": False},
                "Morrowind.ini": {"size": 1, "sha1": "x"}})
            made = deployed_manifest(None, {"profile": "hand"}, local, {"Data Files/a.esp": "h"})
            self.assertEqual((made["profile"], made["source"], list(made["files"])),
                             ("hand", {"kind": "tree"}, ["Data Files/a.esp"]))

    def test_pool_plan_tells_our_pool_from_another_title(self):
        pool = {"name": "TR", "id": "5433ABCD"}
        ftp = FakeFtp()
        ftp.dirs.update({"/E", "/E/UDATA"})
        self.assertEqual(pool_plan(ftp, pool), (
            "E:/UDATA/5433ABCD", [], ["TitleMeta.xbx", "TitleImage.xbx", "tes3xpool.txt"]))

        folder = "/E/UDATA/5433ABCD"
        ftp.dirs.add(folder)
        ftp.entries[folder] = [("file", "TitleMeta.xbx", 78)]
        conflicts = pool_plan(ftp, pool)[1]
        self.assertEqual(conflicts, ["E:/UDATA/5433ABCD holds 1 files of another title"])

        ftp.entries[folder].append(("file", "tes3xpool.txt", 2))
        ftp.files[folder + "/tes3xpool.txt"] = b"TR"
        self.assertEqual(pool_plan(ftp, pool), ("E:/UDATA/5433ABCD", [], ["TitleImage.xbx"]))
        ftp.files[folder + "/tes3xpool.txt"] = b"Main"
        self.assertEqual(pool_plan(ftp, pool)[1],
                         ["E:/UDATA/5433ABCD is save pool 'Main', not 'TR'"])



class DriveSpaceTests(unittest.TestCase):
    def test_the_agent_reply_gives_megabytes_per_drive(self):
        self.assertEqual(parse_drives("ok C=120/480 E=?/4882 F=9000/?"),
                         {"C": (120, 480), "E": (None, 4882), "F": (9000, None)})
        self.assertEqual(parse_drives("err unknown command: drives"), {})

    def test_files_take_whole_clusters(self):
        self.assertEqual([on_disk(n) for n in (0, 1, CLUSTER, CLUSTER + 1)],
                         [0, CLUSTER, CLUSTER, 2 * CLUSTER])


if __name__ == "__main__":
    unittest.main()
