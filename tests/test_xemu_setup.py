import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from tes3x_xemu import xemu_config  # noqa: E402
from tes3x_xemu_setup import find_files, release_asset, resolve  # noqa: E402


class XemuSetupTests(unittest.TestCase):
    def folder(self, files):
        root = Path(tempfile.mkdtemp())
        for name, size in files.items():
            (root / name).write_bytes(b"\0" * size)
        return root

    def test_finds_each_file_by_name_and_size(self):
        root = self.folder({"xemu.exe": 10, "mcpx_1.0.bin": 512, "eeprom.bin": 256,
                            "bios_retail_4627.bin": 1 << 20, "bios_debug_4627.bin": 1 << 20,
                            "cerbios.bin": 256 << 10, "xbox_hdd.qcow2": 10})
        found = {key: path.name for key, path in find_files(root).items()}
        self.assertEqual(found, {"exe": "xemu.exe", "bootrom": "mcpx_1.0.bin",
                                 "eeprom": "eeprom.bin", "bios": "bios_retail_4627.bin",
                                 "bios_128mb": "cerbios.bin", "hdd": "xbox_hdd.qcow2"})

    def test_an_ambiguous_file_is_left_unset(self):
        root = self.folder({"a.bin": 1 << 20, "b.bin": 1 << 20})
        self.assertNotIn("bios", find_files(root))

    def test_set_keys_win_over_the_folder(self):
        root = self.folder({"xemu.exe": 10, "mcpx.bin": 512})
        config = resolve({"folder": str(root), "exe": "other/xemu.exe"}, root.parent)
        self.assertEqual(config["exe"], root.parent / "other" / "xemu.exe")
        self.assertEqual(config["bootrom"], root / "mcpx.bin")

    def test_picks_this_platforms_build(self):
        assets = [{"name": name} for name in (
            "xemu-0.8.136-dbg-windows-x86_64.zip", "xemu-0.8.136-windows-x86_64-pdb.zip",
            "xemu-0.8.136-windows-x86_64.zip", "xemu-0.8.136-x86_64.AppImage")]
        asset = release_asset(assets)
        if sys.platform == "win32":
            self.assertEqual(asset["name"], "xemu-0.8.136-windows-x86_64.zip")


class XemuConfigTests(unittest.TestCase):
    def config(self, **network):
        return xemu_config("boot.bin", "bios.bin", "eeprom.bin", "hdd.qcow2", "game.iso", 64,
                           **network)

    def test_network_backends(self):
        self.assertNotIn("[net]", self.config())
        tunnel = self.config(net_tunnel=(9370, 9369))
        self.assertIn("backend = 'udp'", tunnel)
        self.assertIn("bind_addr = '127.0.0.1:9370'", tunnel)
        nat = self.config(net_nat=True)
        self.assertIn("[net]\nenable = true\nbackend = 'nat'", nat)
        self.assertNotIn("[net.udp]", nat)


if __name__ == "__main__":
    unittest.main()
