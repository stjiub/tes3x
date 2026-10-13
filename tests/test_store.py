import contextlib
import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tes3x import manifest, store
from tes3x.build import Mod, resolve, store_mods


class StoreTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.big = self.mod("Big", {"meshes/a.nif": b"mesh a", "meshes/b.nif": b"mesh b",
                                    "icons/i.dds": b"icon", "Big.esp": b"plugin"})
        self.later = self.mod("Later", {"meshes/b.nif": b"later b"})

    def mod(self, name, files):
        folder = self.root / "library" / name
        for relative, data in files.items():
            path = folder / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return folder

    def test_two_stores_pack_identical_bytes_and_reuse_them(self):
        first = store.pack_mod(Mod("Big", str(self.big), 1), 512, self.root / "one")
        second = store.pack_mod(Mod("Big", str(self.big), 1), 512, self.root / "two")
        self.assertEqual(first["sha256"], second["sha256"])
        archive = (self.root / "one" / f"{first['sha256']}.bsa").read_bytes()
        self.assertEqual(hashlib.sha256(archive).hexdigest(), first["sha256"])
        self.assertEqual(archive, (self.root / "two" / f"{first['sha256']}.bsa").read_bytes())
        with patch("tes3x.store.write_bsa", side_effect=AssertionError("repacked")):
            again = store.pack_mod(Mod("Big", str(self.big), 1), 512, self.root / "one")
        self.assertEqual(again, first)
        self.assertEqual(store.xbox_path(first["sha256"]),
                         f"E:\\TES3X\\lib\\{first['sha256'][:32]}.bsa")
        self.assertLessEqual(len(store.xbox_name(first["sha256"]) + ".json"), 42)

    def test_tree_keeps_what_the_store_mod_loses(self):
        mods = [Mod("Big", str(self.big), 1), Mod("Later", str(self.later), 2)]
        mods[0].store = (True, None)
        filemap, _ = resolve(mods)
        args = SimpleNamespace(store_dir=self.root / "store")
        with contextlib.redirect_stdout(io.StringIO()):
            entries = store_mods(mods, filemap, {}, {"max_texture_size": 1024}, args)
        self.assertEqual([entry["tier"] for entry in entries], [1024])
        self.assertEqual(sorted(filemap), ["big.esp", "meshes/b.nif"])
        self.assertIs(filemap["meshes/b.nif"][0], mods[1])

    def test_threshold_selects_large_mods(self):
        mods = [Mod("Big", str(self.big), 1), Mod("Later", str(self.later), 2)]
        filemap, _ = resolve(mods)
        args = SimpleNamespace(store_dir=self.root / "store")
        with contextlib.redirect_stdout(io.StringIO()):
            entries = store_mods(mods, filemap, {"package": {"store_threshold_mb": 0}}, {}, args)
        self.assertEqual(entries, [])
        size = 2 ** 20
        (self.big / "meshes" / "large.nif").write_bytes(bytes(size))
        mods = [Mod("Big", str(self.big), 1), Mod("Later", str(self.later), 2)]
        filemap, _ = resolve(mods)
        with contextlib.redirect_stdout(io.StringIO()):
            entries = store_mods(mods, filemap, {"package": {"store_threshold_mb": 1}}, {},
                                 args)
        self.assertEqual([entry["name"] for entry in entries], ["Big"])

    def test_manifest_build_id_includes_store_only_when_present(self):
        tree = self.root / "tree"
        tree.mkdir()
        (tree / "a.esp").write_bytes(b"a")
        plain = manifest.create(tree, profile="p", source={})
        self.assertNotIn("store", plain)
        stored = manifest.create(tree, profile="p", source={}, store=[
            {"sha256": "ab" * 32, "size": 3, "name": "Big", "tier": 512}])
        self.assertNotEqual(plain["build"], stored["build"])
        self.assertEqual(stored["manager"], manifest.MANAGER_STORE)
        self.assertEqual(plain["build"], manifest.create(tree, profile="p", source={})["build"])


if __name__ == "__main__":
    unittest.main()
