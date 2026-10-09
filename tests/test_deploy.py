import ftplib
import io
import os
import posixpath
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tes3x.agent import AgentError
from tes3x.deploy import (CLUSTER, AgentTarget, deployed_manifest, ensure_dirs, ftp_basename,
                          legacy_manifest, local_tree, merge_console_ini, on_disk, owner_conflicts, parse_drives,
                          pool_plan, read_manifest, remote_tree, remove_remote_folder, sync,
                          upload_file,
                          verify_uploads)
import tes3x.manifest as tes3x_manifest


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

    def delete(self, name):
        if "/" in name or ":" in name:
            raise ftplib.error_perm("550 relative names only")
        self.calls.append(("delete", self.current, name))
        self.entries[self.current] = [e for e in self.entries.get(self.current, [])
                                      if e[1] != name]

    def rmd(self, name):
        target = self.resolve(name)
        if "/" in name or self.entries.get(target):
            raise ftplib.error_perm("550 not empty")
        self.calls.append(("rmd", target))
        self.dirs.discard(target)
        parent = posixpath.dirname(target)
        self.entries[parent] = [e for e in self.entries.get(parent, [])
                                if e[1] != posixpath.basename(target)]

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

    def test_remove_remote_folder_empties_and_removes_it(self):
        ftp = FakeFtp()
        base = "/F/Games/Old"
        data = base + "/Data Files"
        ftp.dirs.update((base, data))
        ftp.entries["/F/Games"] = [("dir", "Old", 0), ("dir", "Keep", 0)]
        ftp.entries[base] = [("file", "default.xbe", 100), ("dir", "Data Files", 0)]
        ftp.entries[data] = [("file", "Morrowind.esm", 200), ("file", "Mod.esp", 3)]

        self.assertEqual(remove_remote_folder(ftp, "F:/Games/Old"), 3)
        self.assertNotIn(base, ftp.dirs)
        self.assertNotIn(data, ftp.dirs)
        self.assertEqual(ftp.entries["/F/Games"], [("dir", "Keep", 0)])

    def test_first_upload_creates_target_and_stores_relative(self):
        ftp = FakeFtp()
        path = "F:/Games/TES3XFTPProbe/Data Files/canary.txt"
        ensure_dirs(ftp, path, set())
        name = ftp_basename(ftp, path)
        ftp.storbinary("STOR " + name, io.BytesIO(b"probe"), blocksize=64 * 1024)

        self.assertEqual(ftp.current, "/F/Games/TES3XFTPProbe/Data Files")
        self.assertEqual(ftp.files[ftp.current + "/canary.txt"], b"probe")
        self.assertEqual(name, "canary.txt")

    def test_console_ini_keeps_the_consoles_other_keys(self):
        remote = "[Xbox]\r\nNetAgent=192.0.2.7#aa\r\nNetServer=x\r\n"
        merged = merge_console_ini(remote, "[Xbox]\r\nNetAgent=192.0.2.8#bb\r\nNetDns=1.1.1.1\r\n")
        self.assertEqual(merged, "[Xbox]\r\nNetAgent=192.0.2.8#bb\r\nNetServer=x\r\n"
                                 "NetDns=1.1.1.1\r\n")
        self.assertEqual(merge_console_ini("", "[Xbox]\r\nNetAddress=dhcp\r\n"),
                         "[Xbox]\r\nNetAddress=dhcp\r\n")

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
            with patch("tes3x.deploy.tes3x_ftp.connect", return_value=second), \
                    patch("tes3x.deploy.time.sleep"):
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
            previous = {"files": {"default.xbe": {"size": 3, "sha256": "old", "origin": "xbe"},
                                  "Morrowind.ini": {"size": 1, "sha1": "x"},
                                  "data files/A.esp": {"size": 1, "sha256": "stale"}}}
            out = deployed_manifest(built, {}, local, {"Data Files/a.esp": "new"}, previous)
            self.assertEqual(out["profile"], "main")
            self.assertEqual(out["plugins"], ["a.esp"])
            self.assertIn("deployed", out)
            self.assertEqual(out["files"], {
                "Data Files/a.esp": {"size": 6, "sha256": "new", "origin": "build"},
                "default.xbe": {"size": 3, "sha256": "old", "origin": "xbe"},
                "Morrowind.ini": {"size": 1, "sha1": "x"}})
            made = deployed_manifest(None, {"profile": "hand"}, local, {"Data Files/a.esp": "h"})
            self.assertEqual((made["profile"], made["source"], list(made["files"])),
                             ("hand", {"kind": "tree"}, ["Data Files/a.esp"]))
            # a server compares the id: the pipeline's stays, a hand-built tree's is its files
            self.assertEqual(out["build"], built["build"])
            self.assertEqual(made["build"], tes3x_manifest.build_id(made))
            self.assertNotEqual(made["build"], tes3x_manifest.build_id({"files": {}}))

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


class FakeManager:
    """The manager's file operations over a case-insensitive in-memory drive."""

    def __init__(self):
        self.host = "192.0.2.50"
        self.files, self.dirs, self.times, self.log = {}, {"f:", "f:/games"}, {}, []

    def key(self, path):
        return path.replace("\\", "/").rstrip("/").lower()

    def list(self, path):
        folder = self.key(path)
        if folder not in self.dirs:
            raise AgentError(f"list {path}: failed")
        names = {}
        for name in list(self.dirs) + list(self.files):
            if posixpath.dirname(name) == folder and name != folder:
                names[posixpath.basename(name)] = name
        return [(n, full in self.dirs, len(self.files.get(full, b""))) for n, full in names.items()]

    def fetch(self, path):
        if self.key(path) not in self.files:
            raise AgentError(f"{path}: failed")
        return self.files[self.key(path)]

    def mkdir(self, path):
        self.dirs.add(self.key(path))

    def put(self, path, data, window=16, progress=None):
        assert posixpath.dirname(self.key(path)) in self.dirs, path
        self.log.append(("put", posixpath.basename(path)))
        self.files[self.key(path)] = bytes(data)
        if progress:
            progress(len(data))

    def delete(self, path):
        if self.files.pop(self.key(path), None) is None:
            raise AgentError(f"delete {path}: failed")

    def rename(self, source, target):
        if self.key(target) in self.files:
            raise AgentError(f"rename {source}: failed")
        self.log.append(("rename", posixpath.basename(source), posixpath.basename(target)))
        self.files[self.key(target)] = self.files.pop(self.key(source))

    def set_time(self, path, mtime):
        self.times[self.key(path)] = mtime

    def space(self, path):
        return 1 << 30, 1 << 32


class AgentDeployTests(unittest.TestCase):
    def deploy(self, tree, manager, **options):
        target = AgentTarget.__new__(AgentTarget)
        target.client, target.dirs, target.listener = manager, set(), None
        args = SimpleNamespace(tree=str(tree), only=[], dry_run=False, replace=False,
                               require_current=False, verify="size", ignore_space=False,
                               plugin_delay=0, clear_cache=False, **options)
        local = local_tree(tree)
        with patch("sys.stdout", io.StringIO()):
            sync(args, target, "F:/Games/M", local, None)

    def test_an_empty_folder_is_written_in_place_then_updates_are_staged(self):
        manager = FakeManager()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Data Files").mkdir()
            (root / "Data Files" / "a.esp").write_bytes(b"plugin")
            (root / "default.xbe").write_bytes(b"xbe")
            os.utime(root / "Data Files" / "a.esp", (1_000_000_000, 1_000_000_000))
            self.deploy(root, manager)
            files = manager.files
            self.assertEqual(files["f:/games/m/data files/a.esp"], b"plugin")
            self.assertEqual(manager.times["f:/games/m/data files/a.esp"], 1_000_000_000)
            self.assertIn("f:/games/m/tes3xbuild.json", files)
            self.assertEqual([e[1] for e in manager.log if e[0] == "put"],
                             ["default.xbe", "a.esp", "~t3x.new"])

            (root / "default.xbe").write_bytes(b"new xbe")
            (root / "Data Files" / "a.esp").write_bytes(b"new plugin")
            os.utime(root / "Data Files" / "a.esp", (1_000_000_100, 1_000_000_100))
            files["f:/games/m/~t3x9.new"] = b"left by an interrupted deploy"
            manager.log.clear()
            self.deploy(root, manager)
            self.assertEqual(files["f:/games/m/default.xbe"], b"new xbe")
            self.assertEqual(manager.times["f:/games/m/data files/a.esp"], 1_000_000_100)
            self.assertFalse([p for p in files if "~t3x" in p])
            self.assertEqual([e[1] for e in manager.log if e[0] == "put"],
                             ["~t3x1.new", "~t3x2.new", "~t3x.new"])
            puts = [i for i, e in enumerate(manager.log) if e[0] == "put"]
            renames = [i for i, e in enumerate(manager.log) if e[0] == "rename"]
            self.assertLess(puts[1], renames[0])  # every file arrives before any is renamed



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
