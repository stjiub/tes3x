import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from tes3x_paths import check_paths, require_paths
from tes3x_pack import write_invalidation
from tes3x_build import materialize, plugin_masters
from tes3x_pipeline import PipelineError, resolve_patch_plan
from tes3x_patch import _mcp_97
from tes3x_plugins import validate_order
from test_reach import rec, sub


class PathTests(unittest.TestCase):
    def test_default_and_alternate_install_root(self):
        a = check_paths(['textures/a.dds'], prefix='Data Files')
        b = check_paths(['textures/a.dds'], 'G:\\Games\\RPG\\Morrowind', 'Data Files')
        self.assertEqual(a['longest_path'], 'F:/Games/Morrowind/Data Files/textures/a.dds')
        self.assertEqual(b['longest_length'] - a['longest_length'], 4)
        self.assertFalse(a['problems'])

    def test_component_boundaries_include_directories_and_install_root(self):
        self.assertFalse(check_paths(['a' * 42])['problems'])
        self.assertTrue(check_paths(['a' * 43 + '/x'])['problems'])
        self.assertTrue(check_paths(['x'], 'F:/' + 'a' * 43)['problems'])

    def test_total_limit_excludes_drive_but_includes_leading_separator(self):
        # Use <=42 character components while controlling total path length.
        path = '/'.join(['a' * 40] * 5 + ['b' * 40, 'ccc'])
        self.assertEqual(len('/' + path), 250)
        self.assertFalse(check_paths([path], 'F:/')['problems'])
        self.assertTrue(check_paths([path + 'd'], 'F:/')['problems'])

    def test_reject_escape_and_relative_root(self):
        with self.assertRaises(ValueError):
            require_paths(['x'], 'Games/Morrowind')
        with self.assertRaises(ValueError):
            require_paths(['../outside'], 'F:/Games/Morrowind')
        with self.assertRaises(ValueError):
            require_paths(['x'], 'E:/', '../outside')
        self.assertFalse(check_paths([], 'E:/')['problems'])


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_stub_and_dependency_order(self):
        base = self.root / 'Morrowind.esm'
        stub = self.root / 'Tribunal.esm'
        plug = self.root / 'mod.esp'
        base.write_bytes(rec(b'TES3', b''))
        stub.write_bytes(b'TES3')
        plug.write_bytes(rec(b'TES3', sub(b'MAST', b'Morrowind.esm\0')))
        files = {p.name.lower(): p for p in (base, stub, plug)}
        self.assertEqual(plugin_masters(stub), [])
        self.assertEqual(validate_order(['Morrowind.esm', 'Tribunal.esm', 'mod.esp'], files), list(files))
        for order in (['mod.esp', 'Morrowind.esm', 'Tribunal.esm'], ['Morrowind.esm', 'mod.esp'],
                      ['Morrowind.esm', 'mod.esp', 'mod.esp']):
            with self.assertRaises(ValueError):
                validate_order(order, files)

    def test_invalidation_newline_and_path_safety(self):
        target = self.root / 'ArchiveInvalidationList.txt'
        write_invalidation(target, ['Textures/A.dds', 'textures/a.dds'])
        self.assertEqual(target.read_bytes(), b'textures\\a.dds\r\n')
        for name in ('', '../outside', 'F:/bad', 'a\nb'):
            with self.assertRaises(ValueError):
                write_invalidation(target, [name])

    def test_unique_deterministic_mtimes_with_same_mod(self):
        from types import SimpleNamespace
        mod = SimpleNamespace(name='mod', order=10, root=str(self.root))
        files = {}
        for name in ('a.esp', 'b.esp', 'c.esm'):
            source = self.root / name
            source.write_bytes(rec(b'TES3', b''))
            files[name] = (mod, str(source))
        output = self.root / 'out'
        materialize(files, [mod], output, {})
        stamps = {p.name: p.stat().st_mtime for p in output.iterdir()}
        self.assertEqual(len(set(stamps.values())), 3)
        self.assertLess(stamps['c.esm'], stamps['a.esp'])
        self.assertEqual(stamps['b.esp'] - stamps['a.esp'], 4)
        materialize(files, [mod], output, {})
        self.assertEqual(stamps, {p.name: p.stat().st_mtime for p in output.iterdir()})

    def test_deploy_checks_actual_destination_before_ftp(self):
        from unittest.mock import patch
        from tes3x_deploy import main
        (self.root / 'file.txt').write_text('test')
        args = ['tes3x_deploy', str(self.root), '--host', 'invalid.example',
                '--remote', 'F:/' + 'x' * 43, '--dry-run']
        with patch.object(sys, 'argv', args), patch('tes3x_deploy.ftplib.FTP') as ftp:
            with self.assertRaises(ValueError):
                main()
            ftp.assert_not_called()


class PipelinePlanTests(unittest.TestCase):
    def test_pipeline_derives_archive_hook_and_payload_sources(self):
        profile = {
            'patches': {'preset': 'minimal', 'enable': ['mcp-1', 'script-ext']},
            'package': {'mode': 'delta-bsa'},
        }
        plan = resolve_patch_plan(profile)
        self.assertEqual(plan['applied'], ['multi-bsa', 'script-ext', 'mcp-1'])
        self.assertEqual(plan['sources'], [
            'tes3xhook.c', 'tes3xlog.c', 'tes3xarch.c', 'tes3xscript.c', 'tes3xrefs.c'
        ])
        self.assertTrue(plan['needs_payload'])

    def test_standard_only_selects_verified_default_fixes(self):
        plan = resolve_patch_plan({'patches': {'preset': 'standard'},
                                   'package': {'mode': 'merged-bsa'}})
        # mcp-1 remains explicit until its failed-resolution branch is verified.
        self.assertEqual(plan['selected'], [])
        self.assertEqual(plan['applied'], [])
        self.assertFalse(plan['needs_payload'])

    def test_rotating_autosaves_adds_its_hook_source(self):
        plan = resolve_patch_plan({
            'patches': {'preset': 'minimal', 'enable': ['rotating-autosaves']},
            'package': {'mode': 'merged-bsa'},
        })
        self.assertEqual(plan['applied'], ['rotating-autosaves'])
        self.assertEqual(plan['sources'], ['tes3xhook.c', 'tes3xlog.c', 'tes3xsaves.c'])

    def test_mcp_97_adds_its_hook_source(self):
        plan = resolve_patch_plan({
            'patches': {'preset': 'minimal', 'enable': ['mcp-97']},
            'package': {'mode': 'merged-bsa'},
        })
        self.assertEqual(plan['applied'], ['mcp-97'])
        self.assertEqual(plan['sources'], ['tes3xhook.c', 'tes3xlog.c', 'tes3xmcp97.c'])

    def test_mcp_97_patches_both_cursor_advances(self):
        scan = bytes.fromhex(
            '0fb69200100000ff24950020000083c103eb0a0fbe400103c8eb028be84185ed'
        )

        class Image:
            base = 0x100000

            def __init__(self):
                self.data = bytearray(b'\x90' * 32 + scan + b'\x90' * 32)

            def off_to_va(self, offset):
                return self.base + offset

            def va_to_off(self, va):
                return va - self.base

        image = Image()
        target = 0x200000
        edits = _mcp_97(image, '', {'hooks': {'mcp97_scan': hex(target)}})
        block = 32
        site = block + 19
        self.assertEqual(image.data[block + 16], 2)
        self.assertEqual(image.data[site], 0xE9)
        self.assertEqual(image.data[site + 5], 0x90)
        rel = struct.unpack_from('<i', image.data, site + 1)[0]
        self.assertEqual(image.off_to_va(site) + 5 + rel, target)
        self.assertEqual([(offset, length) for offset, length, _label in edits],
                         [(block + 16, 1), (site, 6)])

    def test_development_adds_tools_but_allows_overrides(self):
        profile = {'patches': {'preset': 'development', 'disable': ['diagnostics']},
                   'package': {'mode': 'merged-bsa'}}
        plan = resolve_patch_plan(profile)
        self.assertEqual(plan['selected'], ['console'])

    def test_pipeline_rejects_unknown_categories_and_patches(self):
        with self.assertRaises(PipelineError):
            resolve_patch_plan({'patches': {'categories': ['external']}})
        with self.assertRaises(PipelineError):
            resolve_patch_plan({'patches': {'enable': ['not-a-patch']}})


if __name__ == '__main__':
    unittest.main()
