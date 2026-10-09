import json
import os
import re
import shutil
import struct
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import tes3x_paths
from tes3x_paths import check_paths, local_config, require_paths, resource
from tes3x_pack import write_invalidation
from tes3x_build import materialize, plugin_masters
from tes3x_pipeline import (PipelineError, agent_ini, agent_setting, console_ini_text,
                            copy_retail_root, link_or_copy, resolve_patch_plan, split_console,
                            preference_flags, sanitized_command, stage_default_xbe, stage_retail_base,
                            strip_retail_files, validate_local_config, validate_output, validate_profile)
from tes3x_pipeline import main as pipeline_main
from tes3x_patch import (MCP37_TREE_NEXT_SIG, PatchError, _mcp_3, _mcp_37, _mcp_92, _mcp_97,
                         _mcp_98, _mcp_102, _mcp_123, _mcp_125, _mcp_154, _test_mcp3,
                         _test_mcp97, _test_mcp102)
from tes3x_plugins import (collect, dependency_order, fetch_rules, rules_file, run_arrange,
                           validate_order, warnings as mlox_warnings)
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

    def test_only_tes3x_paths_finds_the_repository(self):
        up = re.compile(r'__file__.*(parents\[1\]|parent\.parent|dirname\(os\.path\.dirname|"\.\.")')
        package = Path(__file__).resolve().parents[1] / 'src' / 'tes3x'
        found = [f'{path.name}:{number}' for path in sorted(package.glob('*.py'))
                 if path.name != 'paths.py'
                 for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1)
                 if up.search(line)]
        self.assertEqual(found, [])

    def test_local_config_order(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder)
        here, checkout, data = folder / 'here', folder / 'checkout', folder / 'data'
        here.mkdir()
        checkout.mkdir()
        name = 'tes3x.local.toml'
        with mock.patch.dict(os.environ, {'TES3X_DATA': str(data)}), \
                mock.patch.object(Path, 'cwd', return_value=here), \
                mock.patch.object(tes3x_paths, 'CHECKOUT', checkout):
            os.environ.pop('TES3X_CONFIG', None)
            self.assertEqual(local_config(), data / name)
            (checkout / name).write_text('', encoding='utf-8')
            self.assertEqual(local_config(), checkout / name)
            (here / name).write_text('', encoding='utf-8')
            self.assertEqual(local_config(), here / name)
            os.environ['TES3X_CONFIG'] = str(folder / 'chosen.toml')
            self.assertEqual(local_config(), folder / 'chosen.toml')
            self.assertEqual(local_config(folder / 'given.toml'), folder / 'given.toml')

    def test_each_old_tool_name_is_the_package_module(self):
        import importlib
        tools = Path(__file__).resolve().parents[1] / 'tools'
        for shim in sorted(tools.glob('tes3x_*.py')):
            with self.subTest(shim.name):
                try:
                    module = importlib.import_module(shim.stem)
                except (ImportError, SystemExit) as exc:  # an optional dependency, as capstone
                    self.skipTest(str(exc))
                self.assertIs(module, importlib.import_module('tes3x.' + shim.stem[6:]))

    def test_the_wheel_carries_every_named_resource(self):
        import ast
        root = Path(__file__).resolve().parents[1]
        setup = ast.parse((root / 'setup.py').read_text(encoding='utf-8'))
        data = next(ast.literal_eval(node.value) for node in setup.body
                    if isinstance(node, ast.Assign) and node.targets[0].id == 'DATA')
        portable = ('VERSION', 'externals/', 'manager/default.xbe', 'manager/launcher.xbe')
        named = {'/'.join(ast.literal_eval(arg) for arg in node.args)
                 for path in (root / 'src' / 'tes3x').glob('*.py')
                 for node in ast.walk(ast.parse(path.read_text(encoding='utf-8')))
                 if isinstance(node, ast.Call) and getattr(node.func, 'id', '') == 'resource'
                 and node.args and all(isinstance(a, ast.Constant) for a in node.args)}
        missing = sorted(name for name in named if not name.startswith(portable)
                         and not any(name == d or name.startswith(d + '/') for d in data))
        self.assertTrue(named)
        self.assertEqual(missing, [])

    def test_named_resources_exist(self):
        # VERSION and the manager's XBEs exist only in a packaged folder.
        for parts in (('patches.toml',), ('candidates.toml',), ('catalog.toml',),
                      ('hooks', 'xboxkrnl.exe.def'), ('symbols', 'curated.json'),
                      ('symbols', 'structs.json'), ('examples', 'profile.toml'),
                      ('examples', 'starts.toml'), ('assets', 'menu'),
                      ('addons', 'console', 'console.py'), ('tests', 'game'),
                      ('manager', 'mgr.h'), ('keys', 'release.pub')):
            self.assertTrue(resource(*parts).exists(), parts)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_output_holding_only_the_gui_check_is_usable(self):
        out = self.root / "out"
        out.mkdir()
        (out / ".tes3x-check.json").write_text("{}")
        validate_output(out)
        (out / "stray.txt").write_text("")
        with self.assertRaisesRegex(PipelineError, "not owned"):
            validate_output(out)

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

    def test_mlox_inputs_generate_missing_expansion_stubs(self):
        built, vanilla = self.root / 'built', self.root / 'vanilla'
        built.mkdir()
        vanilla.mkdir()
        (vanilla / 'Morrowind.esm').write_bytes(rec(b'TES3', b''))
        (built / 'mod.esp').write_bytes(rec(b'TES3', sub(b'MAST', b'Morrowind.esm\0')))
        files = collect(built, vanilla, self.root / 'stubs')
        self.assertEqual(sorted(files), ['bloodmoon.esm', 'mod.esp', 'morrowind.esm', 'tribunal.esm'])
        self.assertEqual(files['tribunal.esm'].read_bytes(), b'TES3')
        (vanilla / 'Morrowind.esm').unlink()
        with self.assertRaises(ValueError):
            collect(built, vanilla, self.root / 'stubs')

    def test_fetch_rules_saves_only_a_rules_file(self):
        good, bad = self.root / 'good.txt', self.root / 'bad.txt'
        good.write_bytes(b'[Order]\nMorrowind.esm\nTribunal.esm\n')
        bad.write_bytes(b'<html>not found</html>')
        target = self.root / 'mlox' / 'mlox_base.txt'
        self.assertEqual(fetch_rules(target, good.as_uri()), good.stat().st_size)
        self.assertEqual(target.read_bytes(), good.read_bytes())
        with self.assertRaises(ValueError):
            fetch_rules(target, bad.as_uri())
        self.assertEqual(target.read_bytes(), good.read_bytes())

    def test_rules_download_once_to_the_data_folder(self):
        source = self.root / 'rules.txt'
        source.write_bytes(b'[Order]\nMorrowind.esm\n')
        data = {'TES3X_DATA': str(self.root / 'data')}
        with mock.patch.dict(os.environ, data), \
                mock.patch('tes3x_plugins.RULES_URL', source.as_uri()):
            path = rules_file()
            self.assertEqual(path, self.root / 'data' / 'mlox' / 'mlox_base.txt')
            source.unlink()
            self.assertEqual(rules_file(), path)
            self.assertEqual(rules_file(self.root / 'mine.txt'), self.root / 'mine.txt')

    def test_mlox_notes_are_not_warnings(self):
        messages = ("[NOTE]\n > 'a.esp'\n |\tadvice\n"
                    "[CONFLICT]\n > 'a.esp'\n > 'b.esp'\n |\tdo not use both\n"
                    "[REQUIRES]\n > 'c.esp' Requires:\n > 'd.esm'\n")
        self.assertEqual([block.split('\n')[0] for block in mlox_warnings(messages)],
                         ['[CONFLICT]', '[REQUIRES]'])

    def test_loose_mode_needs_no_archive_hook(self):
        plan = resolve_patch_plan({'patches': {'preset': 'minimal'}, 'package': {'mode': 'loose'},
                                   'mods': [{'name': 'x'}]})
        self.assertEqual(plan['package_mode'], 'loose')
        self.assertNotIn('multi-bsa', plan['applied'])
        with self.assertRaises(PipelineError):
            validate_profile({'profile': {'name': 'p'},
                              'package': {'mode': 'loose', 'archive_only': True}})

    def test_plugin_order_accepts_mods_or_mlox(self):
        validate_profile({'profile': {'name': 'p'}, 'rules': {'plugin_order': 'mlox'}})
        with self.assertRaises(PipelineError):
            validate_profile({'profile': {'name': 'p'}, 'rules': {'plugin_order': 'loot'}})
        validate_profile({'profile': {'name': 'p'}, 'plugins': {'order': ['a.esp']}})
        with self.assertRaises(PipelineError):
            validate_profile({'profile': {'name': 'p'}, 'plugins': {'order': ['a.esp']},
                              'rules': {'plugin_order': 'mlox'}})

    def test_tes3merge_is_a_rule_with_a_local_tool_path(self):
        validate_profile({'profile': {'name': 'p'}, 'rules': {'tes3merge': True}})
        with self.assertRaises(PipelineError):
            validate_profile({'profile': {'name': 'p'}, 'rules': {'tes3merge': 'yes'}})
        validate_local_config({'paths': {'tes3merge': 'TES3Merge.exe'}})
        with self.assertRaises(PipelineError):
            validate_local_config({'paths': {'tes3merge': True}})

    def test_listed_order_keeps_masters_first_and_completes_the_list(self):
        built, vanilla = self.root / 'built', self.root / 'vanilla'
        built.mkdir()
        vanilla.mkdir()
        (vanilla / 'Morrowind.esm').write_bytes(rec(b'TES3', b''))
        (built / 'lib.esm').write_bytes(rec(b'TES3', sub(b'MAST', b'Morrowind.esm\0')))
        for name in ('a.esp', 'b.esp', 'c.esp'):
            (built / name).write_bytes(rec(b'TES3', sub(b'MAST', b'lib.esm\0')))
        files = collect(built, vanilla, self.root / 'stubs')
        self.assertEqual(dependency_order(files, mtime=False, preferred=['c.esp', 'a.esp'])[3:],
                         ['lib.esm', 'c.esp', 'a.esp', 'b.esp'])
        out = self.root / 'order.json'
        run_arrange(built, vanilla, ['b.esp', 'lib.esm'], self.root / 'work', out)
        self.assertEqual(json.loads(out.read_text())['plugins'][3:5], ['lib.esm', 'b.esp'])

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

    def test_complete_install_carries_retail_root_payload(self):
        vanilla = self.root / 'vanilla'
        staged = self.root / 'staged'
        (vanilla / 'Data Files').mkdir(parents=True)
        (vanilla / 'sound-cache').mkdir()
        staged.mkdir()
        (staged / 'Data Files').mkdir()
        (staged / 'Data Files' / 'generated.bsa').write_bytes(b'generated')
        for name in ('Default.xbe', 'morrowind.xbe', 'Morrowind.ini'):
            (vanilla / name).write_bytes(b'retail')
        (vanilla / 'Data Files' / 'retail.bsa').write_bytes(b'retail')
        (vanilla / 'sound-cache' / 'voice.wav').write_bytes(b'voice')
        (vanilla / 'FullMap').write_bytes(b'map')
        for name in ('disc.iso', 'release.nfo', 'release.rar', 'release.r00', 'release.r12',
                     'release.sfv'):
            (vanilla / name).write_bytes(b'artifact')

        count, size = copy_retail_root(vanilla, staged)

        self.assertEqual((count, size), (2, 8))
        self.assertEqual((staged / 'sound-cache' / 'voice.wav').read_bytes(), b'voice')
        self.assertEqual((staged / 'FullMap').read_bytes(), b'map')
        self.assertEqual((staged / 'Data Files' / 'generated.bsa').read_bytes(), b'generated')
        self.assertFalse((staged / 'Data Files' / 'retail.bsa').exists())
        for name in ('Default.xbe', 'morrowind.xbe', 'Morrowind.ini', 'disc.iso', 'release.r00'):
            self.assertFalse((staged / name).exists())

    def test_hardlinked_retail_files_share_the_source(self):
        vanilla = self.root / 'vanilla'
        staged = self.root / 'staged'
        (vanilla / 'sound-cache').mkdir(parents=True)
        (vanilla / 'sound-cache' / 'voice.wav').write_bytes(b'voice')
        staged.mkdir()
        copy_retail_root(vanilla, staged, link_or_copy)
        self.assertTrue((staged / 'sound-cache' / 'voice.wav').samefile(
            vanilla / 'sound-cache' / 'voice.wav'))

    def test_overlay_base_keeps_retail_xbes_and_strips_unchanged_files(self):
        vanilla = self.root / 'vanilla'
        staged = self.root / 'staged'
        (vanilla / 'Data Files').mkdir(parents=True)
        (vanilla / 'Data Files' / 'Morrowind.bsa').write_bytes(b'retail')
        for name in ('Default.xbe', 'morrowind.xbe', 'Morrowind.ini'):
            (vanilla / name).write_bytes(b'retail')
        (vanilla / 'movie.bik').write_bytes(b'movie')

        base = self.root / 'base'
        count, _size = stage_retail_base(vanilla, base)
        self.assertEqual(count, 4)
        self.assertTrue((base / 'Data Files' / 'Morrowind.bsa').is_file())
        self.assertTrue((base / 'movie.bik').is_file())
        self.assertTrue((base / 'morrowind.xbe').is_file())
        self.assertTrue((base / 'Default.xbe').is_file())
        self.assertFalse((base / 'Morrowind.ini').exists())

        (staged / 'Data Files').mkdir(parents=True)
        (staged / 'Data Files' / 'Morrowind.bsa').write_bytes(b'retail')
        (staged / 'Data Files' / 'mod.bsa').write_bytes(b'mod')
        (staged / 'Morrowind.ini').write_bytes(b'retail')
        removed, removed_bytes = strip_retail_files(staged, vanilla)
        self.assertEqual((removed, removed_bytes), (1, 6))
        self.assertFalse((staged / 'Data Files' / 'Morrowind.bsa').exists())
        self.assertTrue((staged / 'Data Files' / 'mod.bsa').is_file())
        self.assertTrue((staged / 'Morrowind.ini').is_file())

    def test_overlay_uses_patched_engine_as_dashboard_entry(self):
        launcher = self.root / 'launcher.xbe'
        engine = self.root / 'engine.xbe'
        output = self.root / 'Default.xbe'
        launcher.write_bytes(b'launcher')
        engine.write_bytes(b'engine')

        self.assertEqual(stage_default_xbe(launcher, engine, output, 'overlay'), engine)
        self.assertEqual(output.read_bytes(), b'engine')
        self.assertEqual(stage_default_xbe(launcher, engine, output, 'full'), launcher)
        self.assertEqual(output.read_bytes(), b'launcher')

    def test_patches_only_stages_retail_data_and_ini_keys(self):
        from tes3x_pipeline import stage_retail
        data = self.root / 'Data Files'
        data.mkdir()
        (data / 'Morrowind.bsa').write_bytes(b'retail')
        ini = self.root / 'Morrowind.ini'
        ini.write_text('[General]\nShow FPS=0\n', encoding='latin-1')
        staged = self.root / 'staged'
        staged.mkdir()
        stage_retail(data, ini, staged, ['General:Show FPS=1', 'Xbox:Diagnostics=1'])
        self.assertEqual((staged / 'Data Files' / 'Morrowind.bsa').read_bytes(), b'retail')
        text = (staged / 'Morrowind.ini').read_text(encoding='latin-1')
        self.assertIn('Show FPS=1', text)
        self.assertIn('[Xbox]', text)
        self.assertIn('Diagnostics=1', text)
        with self.assertRaises(PipelineError):
            stage_retail(data, ini, self.root / 'again', ['no-section'])

    def test_dashboard_xml_names_the_folder_and_escapes_the_title(self):
        from tes3x_pipeline import dashboard_xml
        text = dashboard_xml('Morrowind & Mods', 'MorrowindModded')
        self.assertIn('<title>Morrowind &amp; Mods</title>', text)
        self.assertIn('<foldername>MorrowindModded</foldername>', text)
        self.assertIn('<titleid>42530005</titleid>', text)
        self.assertIn('<titleid>5433ABCD</titleid>', dashboard_xml('t', 'f', 0x5433ABCD))

    def test_save_pool_keys(self):
        validate_profile({'profile': {'name': 'p', 'save_pool': 'TR'}})
        validate_profile({'profile': {'name': 'p', 'save_pool': 'TR', 'save_pool_id': '5433ABCD'}})
        for identity in ({'name': 'p', 'save_pool_id': '5433ABCD'},
                         {'name': 'p', 'save_pool': 'TR', 'save_pool_id': '42530005'},
                         {'name': 'p', 'save_pool': 'TR', 'save_pool_id': 'pool'}):
            with self.assertRaises(PipelineError):
                validate_profile({'profile': identity})

    def test_dashboard_files_are_chosen_per_profile(self):
        from tes3x_pipeline import dashboard_list, write_dashboard_files
        self.assertEqual(dashboard_list({}), ['xbmc4gamers'])
        self.assertEqual(dashboard_list({'profile': {'dashboards': []}}), [])
        with self.assertRaises(PipelineError):
            dashboard_list({'profile': {'dashboards': ['unleashx']}})
        write_dashboard_files(self.root, ['xbmc4gamers'], 'Name', 'Folder')
        self.assertIn(b'<title>Name</title>\r\n',
                      (self.root / '_resources' / 'default.xml').read_bytes())

    def test_deploy_never_deletes_the_dashboard_folder(self):
        from tes3x_deploy import orphans
        remote = {'_resources/default.xml': 1, '_Resources/artwork/x.jpg': 2, 'old.esp': 3,
                  'morrowind.xbe': 4}
        self.assertEqual(orphans(remote, {'morrowind.xbe'}), ['old.esp'])

    def test_link_falls_back_to_copy(self):
        from unittest.mock import patch
        source = self.root / 'a'
        source.write_bytes(b'x')
        with patch('os.link', side_effect=OSError('cross-device')):
            target = link_or_copy(source, self.root / 'b')
        self.assertFalse(target.samefile(source))
        self.assertEqual(target.read_bytes(), b'x')

    def test_recorded_command_removes_machine_paths(self):
        command = sanitized_command(
            ['--vanilla', 'D:/private/game', 'profiles/validation.toml',
             '--out=D:/private/build', '--enable', 'mcp-102'],
            'validation', 'profiles/validation.toml')
        self.assertEqual(command, [
            'python', 'tools/tes3x_pipeline.py', '--vanilla', '<local-path>',
            'profile:validation', '--out=<local-path>', '--enable', 'mcp-102',
        ])


class PipelinePlanTests(unittest.TestCase):
    def test_pipeline_derives_archive_hook_and_payload_sources(self):
        profile = {
            'patches': {'preset': 'minimal', 'enable': ['mcp-1', 'script-ext']},
            'package': {'mode': 'delta-bsa'},
            'mods': [{'name': 'Example'}],
        }
        plan = resolve_patch_plan(profile)
        self.assertEqual(plan['applied'], ['multi-bsa', 'script-ext', 'mcp-1'])
        self.assertEqual(plan['sources'], [
            'tes3xhook.c', 'tes3xlog.c', 'tes3xini.c', 'tes3xarch.c', 'tes3xscript.c', 'tes3xrefs.c'
        ])
        self.assertTrue(plan['needs_payload'])

    def test_legacy_mwse_adds_script_extension_dependency(self):
        profile = {'patches': {'preset': 'minimal', 'enable': ['mwse-legacy']}}
        plan = resolve_patch_plan(profile)
        self.assertEqual(plan['selected'], ['script-ext', 'mwse-legacy'])
        self.assertEqual(plan['sources'], [
            'tes3xhook.c', 'tes3xlog.c', 'tes3xini.c', 'tes3xscript.c', 'tes3xconsole.c', 'tes3xmwse.c'
        ])

    def test_legacy_mwse_dependency_cannot_be_disabled(self):
        profile = {'patches': {'preset': 'minimal', 'enable': ['mwse-legacy'],
                               'disable': ['script-ext']}}
        with self.assertRaisesRegex(PipelineError, 'requires script-ext'):
            resolve_patch_plan(profile)

    def test_overlay_layout_adds_its_patch_and_cannot_disable_it(self):
        profile = {'profile': {'install_layout': 'overlay'},
                   'patches': {'preset': 'minimal'}}
        plan = resolve_patch_plan(profile)
        self.assertEqual(plan['selected'], ['data-overlay'])
        self.assertIn('tes3xoverlay.c', plan['sources'])
        profile['patches']['disable'] = ['data-overlay']
        with self.assertRaisesRegex(PipelineError, 'requires data-overlay'):
            resolve_patch_plan(profile)

    def test_info_name_arena_adds_pager_dependency(self):
        profile = {'patches': {'preset': 'minimal', 'enable': ['info-name-arena']}}
        plan = resolve_patch_plan(profile)
        self.assertEqual(plan['selected'], ['info-name-arena'])
        self.assertEqual(plan['sources'], [
            'tes3xhook.c', 'tes3xlog.c', 'tes3xini.c', 'tes3xpager.c', 'tes3xinfoarena.c'
        ])

    def test_multiplayer_adds_network_foundation(self):
        profile = {'patches': {'preset': 'minimal', 'enable': ['multiplayer']}}
        plan = resolve_patch_plan(profile)
        self.assertEqual(plan['selected'], ['diagnostics', 'net', 'multiplayer'])
        self.assertEqual(plan['sources'], [
            'tes3xhook.c', 'tes3xlog.c', 'tes3xini.c', 'tes3xdiag.c', 'tes3xnet.c', 'tes3xmulti.c'
        ])

    def test_agent_adds_network_foundation(self):
        profile = {'patches': {'preset': 'minimal', 'enable': ['agent']}}
        plan = resolve_patch_plan(profile)
        self.assertEqual(plan['selected'], ['diagnostics', 'net', 'agent'])
        self.assertEqual(plan['sources'], [
            'tes3xhook.c', 'tes3xlog.c', 'tes3xini.c', 'tes3xdiag.c', 'tes3xnet.c', 'tes3xagent.c'
        ])

    def test_agent_ini_pairs_and_turns_the_network_on(self):
        with tempfile.TemporaryDirectory() as folder:
            base, xemu = Path(folder), {'kind': 'xemu'}
            items = agent_ini(['net', 'agent'], [], base, xemu)
            self.assertEqual(items, ['Xbox:NetAgent=' + agent_setting(base, xemu),
                                     'Xbox:NetAddress=dhcp'])
            self.assertEqual(agent_ini(['net', 'agent'], ['Xbox:NetAddress=192.0.2.9',
                                                         'Xbox:NetAgent=1.2.3.4#00'], base, xemu),
                             [])
            self.assertEqual(agent_ini(['net', 'multiplayer', 'agent'], [], base, xemu),
                             ['Xbox:NetAgent=' + agent_setting(base, xemu)])
            self.assertEqual(agent_ini(['net', 'multiplayer'], [], base, xemu), [])

    def test_console_keys_leave_the_build(self):
        build, console = split_console(['Xbox:NetAgent=1.2.3.4#00', 'Xbox:NetServer=a',
                                        'General:NetAddress=x', 'xbox:netaddress=dhcp',
                                        'Xbox:NetAddress=192.0.2.9/24'])
        self.assertEqual(build, ['Xbox:NetServer=a', 'General:NetAddress=x'])
        self.assertEqual(console_ini_text(console),
                         '[Xbox]\r\nNetAgent=1.2.3.4#00\r\nNetAddress=192.0.2.9/24\r\n')

    def test_agent_setting_uses_xemu_gateway_and_persistent_key(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            first = agent_setting(Path(folder), {'kind': 'xemu'})
            second = agent_setting(Path(folder), {'kind': 'xemu'})
            self.assertEqual(first, second)
            self.assertRegex(first, r'^10\.0\.2\.2#[0-9a-f]{32}$')

    def test_recommended_selects_only_release_defaults(self):
        plan = resolve_patch_plan({'patches': {'preset': 'recommended'},
                                   'package': {'mode': 'merged-bsa'}})
        self.assertEqual(plan['selected'], [])

    def test_testing_includes_preview_defaults_but_not_dev_patches(self):
        plan = resolve_patch_plan({'patches': {'preset': 'testing'},
                                   'package': {'mode': 'merged-bsa'}})
        self.assertTrue({'mcp-97', 'mcp-102', 'dxt5-size', 'diagnostics', 'console'}
                        <= set(plan['selected']))
        self.assertTrue({'mcp-1', 'mcp-154'}.isdisjoint(plan['selected']))

    def test_old_preset_names_remain_compatible(self):
        standard = resolve_patch_plan({'patches': {'preset': 'standard'}})
        dev = resolve_patch_plan({'patches': {'preset': 'dev'}})
        self.assertEqual(standard['preset'], 'recommended')
        self.assertEqual(dev['preset'], 'testing')

    def test_profile_without_mods_patches_only(self):
        plan = resolve_patch_plan({'patches': {'preset': 'standard'},
                                   'package': {'mode': 'delta-bsa'},
                                   'mods': [{'name': 'Off', 'enabled': False}]})
        self.assertEqual(plan['package_mode'], 'retail')
        self.assertNotIn('multi-bsa', plan['applied'])

    def test_check_needs_no_retail_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp) / 'p.toml'
            profile.write_text('[profile]\nname = "p"\n', encoding='utf-8')
            local = Path(tmp) / 'local.toml'
            local.write_text('[paths]\nvanilla_root = "missing"\n', encoding='utf-8')
            self.assertEqual(pipeline_main([str(profile), '--config', str(local), '--check']), 0)

    def test_check_describes_saved_and_build_time_plugin_order(self):
        import contextlib
        import io
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp) / 'p.toml'
            local = Path(tmp) / 'local.toml'
            local.write_text('[paths]\nmod_library = "mods"\n', encoding='utf-8')
            for setting, expected in [
                    ('[plugins]\norder = ["Example.esp"]', 'saved order'),
                    ('[rules]\nplugin_order = "mlox"', 'mlox at build time')]:
                profile.write_text(
                    '[profile]\nname = "p"\n[[mods]]\nname = "Example"\n' + setting + '\n',
                    encoding='utf-8')
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    self.assertEqual(
                        pipeline_main([str(profile), '--config', str(local), '--check']), 0)
                self.assertIn(f'plugin order: {expected}', out.getvalue())

    def test_profile_remote_root_wins_over_local_config(self):
        import contextlib
        import io
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp) / 'p.toml'
            profile.write_text('[profile]\nname = "p"\nremote_root = "F:/Games/Profile"\n',
                               encoding='utf-8')
            local = Path(tmp) / 'local.toml'
            local.write_text('[deploy]\nhost = "x"\nremote_root = "F:/Games/Local"\n',
                             encoding='utf-8')
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                pipeline_main([str(profile), '--config', str(local), '--check', '--dry-run'])
            self.assertIn('target: xbox x F:/Games/Profile', out.getvalue())

    def test_targets_resolve_install_folder_and_longest_path_root(self):
        from tes3x_targets import path_check_root, remote_root, resolve
        local = {
            'default_target': 'bench',
            'targets': {
                'bench': {'kind': 'xbox', 'host': 'a', 'games_root': 'F:/Games'},
                'long': {'kind': 'xbox', 'host': 'b',
                         'games_root': 'E:/A much longer games directory'},
                'emulator': {'kind': 'xemu', 'ram': 128},
            },
        }
        profile = {'profile': {'name': 'output-name', 'install_dir': 'MorrowindMods'}}
        self.assertEqual(remote_root(profile, resolve(local)), 'F:/Games/MorrowindMods')
        self.assertEqual(path_check_root(local, profile),
                         'E:/A much longer games directory/MorrowindMods')
        self.assertEqual(path_check_root(local, profile, 'bench'), 'F:/Games/MorrowindMods')

    def test_xemu_targets_hold_their_own_emulator_files(self):
        from tes3x_targets import resolve
        local = {
            'xemu': {'bootrom': 'legacy.bin'},
            'targets': {
                'stable': {'kind': 'xemu', 'exe': 'xemu-stable.exe', 'bios': 'retail.bin'},
                'new': {'kind': 'xemu', 'exe': 'xemu-new.exe', 'bios': 'debug.bin'},
            },
        }
        stable = resolve(local, 'stable', 'xemu')
        new = resolve(local, 'new', 'xemu')
        self.assertEqual((stable['exe'], stable['bios']), ('xemu-stable.exe', 'retail.bin'))
        self.assertEqual((new['exe'], new['bios']), ('xemu-new.exe', 'debug.bin'))
        self.assertEqual(stable['bootrom'], 'legacy.bin')

        legacy = resolve({'xemu': {'exe': 'old-xemu.exe', 'bios': 'old.bin'}}, kind='xemu')
        self.assertEqual((legacy['name'], legacy['exe']), ('xemu', 'old-xemu.exe'))

    def test_legacy_deploy_is_an_implicit_target(self):
        from tes3x_targets import remote_root, resolve
        local = {'deploy': {'host': 'x', 'remote_root': 'F:/Games/LegacyFolder'}}
        target = resolve(local)
        self.assertEqual((target['name'], target['kind']), ('xbox', 'xbox'))
        profile = {'profile': {'name': 'profile-name'}}
        self.assertEqual(remote_root(profile, target), 'F:/Games/LegacyFolder')
        profile['profile']['remote_root'] = 'G:/Old/ProfileFolder'
        self.assertEqual(remote_root(profile, target), 'F:/Games/ProfileFolder')
        root_target = resolve({'deploy': {'host': 'x', 'remote_root': 'F:/Games'}})
        self.assertEqual(remote_root({'profile': {'name': 'p'}}, root_target), 'F:/Games')

    def test_target_validation(self):
        validate_local_config({
            'default_target': 'xemu-128',
            'targets': {'xemu-128': {'kind': 'xemu', 'ram': 128,
                                      'exe': 'xemu.exe', 'bios': 'cerbios.bin'}},
        })
        validate_local_config({
            'default_target': 'bench',
            'targets': {'bench': {'kind': 'xbox', 'host': 'x', 'games_root': 'F:/Games',
                                   'agent_token': 'a' * 64, 'dashboard': 'C:'}},
        })
        with self.assertRaisesRegex(PipelineError, 'games_root is required'):
            validate_local_config({'targets': {'bench': {'kind': 'xbox', 'host': 'x'}}})
        with self.assertRaisesRegex(PipelineError, 'agent_token'):
            validate_local_config({'targets': {'bench': {
                'kind': 'xbox', 'host': 'x', 'games_root': 'F:/Games', 'agent_token': 'bad'}}})
        with self.assertRaisesRegex(PipelineError, 'default_target'):
            validate_local_config({'default_target': 'missing', 'targets': {}})

    def test_local_config_can_supply_mod_library(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp) / 'p.toml'
            profile.write_text('[profile]\nname = "p"\n[[mods]]\nname = "Example"\n',
                               encoding='utf-8')
            local = Path(tmp) / 'local.toml'
            local.write_text('[paths]\nmod_library = "mods"\n', encoding='utf-8')
            self.assertEqual(pipeline_main([str(profile), '--config', str(local), '--check']), 0)

    def test_profile_validation_rejects_unknown_and_ill_typed_fields(self):
        with self.assertRaisesRegex(PipelineError, 'unknown profile keys: nmae'):
            validate_profile({'profile': {'name': 'p', 'nmae': 'typo'}})
        with self.assertRaisesRegex(PipelineError, r'mods\[1\]\.order must be an integer'):
            validate_profile({'profile': {'name': 'p', 'library': 'mods'},
                              'mods': [{'name': 'm', 'order': 'first'}]})
        validate_profile({'profile': {'name': 'p'}, 'mods': [{'name': 'm'}]})
        with self.assertRaisesRegex(PipelineError, 'preferences.invert_look must be a boolean'):
            validate_profile({'profile': {'name': 'p'},
                              'preferences': {'invert_look': 'no'}})
        with self.assertRaisesRegex(PipelineError, 'profile.install_layout'):
            validate_profile({'profile': {'name': 'p', 'install_layout': 'thin'}})

    def test_local_validation_checks_public_tables_and_allows_extensions(self):
        validate_local_config({'paths': {'build_root': 'build'},
                               'deploy': {'port': 21, 'retail_root': 'F:/Games/Retail'},
                               'xemu': {'exe': 'private-extension'}})
        with self.assertRaisesRegex(PipelineError, 'unknown paths keys: build_rooot'):
            validate_local_config({'paths': {'build_rooot': 'build'}})
        with self.assertRaisesRegex(PipelineError, 'unknown local config sections: path'):
            validate_local_config({'path': {'build_root': 'build'}})

    def test_rotating_autosaves_adds_its_hook_source(self):
        plan = resolve_patch_plan({
            'patches': {'preset': 'minimal', 'enable': ['rotating-autosaves']},
            'package': {'mode': 'merged-bsa'},
        })
        self.assertEqual(plan['applied'], ['rotating-autosaves'])
        self.assertEqual(plan['sources'], ['tes3xhook.c', 'tes3xlog.c', 'tes3xini.c', 'tes3xsaves.c'])

    def test_transition_autosaves_reuses_save_hook_source(self):
        plan = resolve_patch_plan({
            'patches': {'preset': 'minimal', 'enable': ['transition-autosaves']},
            'package': {'mode': 'merged-bsa'},
        })
        self.assertEqual(plan['applied'], ['transition-autosaves'])
        self.assertEqual(plan['sources'], ['tes3xhook.c', 'tes3xlog.c', 'tes3xini.c', 'tes3xsaves.c'])

    def test_build_preferences_adds_specialized_hook_source(self):
        profile = {
            'profile': {'name': 'p'},
            'patches': {'preset': 'minimal'},
            'preferences': {'invert_look': False},
        }
        validate_profile(profile)
        plan = resolve_patch_plan(profile)
        self.assertEqual(plan['applied'], ['build-preferences'])
        self.assertEqual(plan['sources'], ['tes3xhook.c', 'tes3xlog.c', 'tes3xini.c', 'tes3xprefs.c'])
        self.assertEqual(preference_flags(profile), '-DTES3X_INVERT_LOOK=0')
        self.assertEqual(preference_flags({'profile': {'name': 'p'}}), '')

        control = resolve_patch_plan(profile, disable=['build-preferences'])
        self.assertNotIn('build-preferences', control['applied'])

    def test_mcp_97_adds_its_hook_source(self):
        plan = resolve_patch_plan({
            'patches': {'preset': 'minimal', 'enable': ['mcp-97']},
            'package': {'mode': 'merged-bsa'},
        })
        self.assertEqual(plan['applied'], ['mcp-97'])
        self.assertEqual(plan['sources'], ['tes3xhook.c', 'tes3xlog.c', 'tes3xini.c', 'tes3xmcp97.c'])

    def test_mcp_37_redirects_cell_change_to_its_hook(self):
        base = 0x100000
        game = 0x3CB5F4
        player = 0x180000
        block_offset = 32
        player_call = base + block_offset + 6
        block = bytearray(bytes.fromhex('8b0d') + struct.pack('<I', game))
        block += b'\xe8' + struct.pack('<i', player - (player_call + 5))
        block += bytes.fromhex(
            '8b40148b483883c038894c24048b50048b0d'
        ) + struct.pack('<I', game) + bytes.fromhex(
            '895424088b40088944240c8b893c03000085c97405e8112233448b0d'
        ) + struct.pack('<I', game) + bytes.fromhex(
            '568d942484000000'
        )

        class Image:
            def __init__(self):
                self.data = bytearray(b'\x90' * block_offset + block + b'\x90' * 16
                                      + MCP37_TREE_NEXT_SIG + b'\xcc' * 16)

            def off_to_va(self, offset):
                return base + offset

            def va_to_off(self, va):
                return va - base

        image = Image()
        target = 0x200000
        edits = _mcp_37(image, '', {'hooks': {'mcp37': hex(target)}})
        site = block_offset + block.rindex(bytes.fromhex('8b0d') + struct.pack('<I', game))
        self.assertEqual(image.data[site], 0xE8)
        self.assertEqual(image.data[site + 5], 0x90)
        rel = struct.unpack_from('<i', image.data, site + 1)[0]
        self.assertEqual(image.off_to_va(site) + 5 + rel, target)
        self.assertEqual([(offset, length) for offset, length, _label in edits], [(site, 6)])

    def test_mcp_37_adds_its_hook_source(self):
        plan = resolve_patch_plan({
            'patches': {'preset': 'minimal', 'enable': ['mcp-37']},
            'package': {'mode': 'merged-bsa'},
        })
        self.assertEqual(plan['applied'], ['mcp-37'])
        self.assertEqual(plan['sources'], ['tes3xhook.c', 'tes3xlog.c', 'tes3xini.c', 'tes3xmcp37.c'])

    def test_mcp_3_keeps_damage_reduction_for_no_equipped_armor(self):
        block = bytes.fromhex(
            'ff92e40000008b44241083cbff85c00f845b020000'
            'd844241c8b0df4b53c006860040000'
        )

        class Image:
            base = 0x100000

            def __init__(self):
                self.data = bytearray(b'\x90' * 32 + block + b'\xcc' * 16)

            def off_to_va(self, offset):
                return self.base + offset

            def va_to_off(self, va):
                return va - self.base

        image = Image()
        edits = _mcp_3(image, '', {})
        site = 32 + 15
        self.assertEqual(image.data[site:site + 6], bytes.fromhex('eb04cccccccc'))
        self.assertEqual([(offset, length) for offset, length, _label in edits], [(site, 6)])

    def test_mcp_3_needs_no_dedicated_hook_source(self):
        plan = resolve_patch_plan({
            'patches': {'preset': 'minimal', 'enable': ['mcp-3']},
            'package': {'mode': 'merged-bsa'},
        })
        self.assertEqual(plan['applied'], ['mcp-3'])
        self.assertEqual(plan['sources'], ['tes3xhook.c', 'tes3xlog.c', 'tes3xini.c'])

    def test_mcp_3_test_probe_requires_its_command(self):
        with self.assertRaisesRegex(PatchError, 'has no mcp-3 test command'):
            _test_mcp3(None, '', {'hooks': {}})
        self.assertEqual(
            _test_mcp3(None, '', {'hooks': {'mcp3_test': '0x200000'}}),
            [(None, 0, 'fully unarmored damage command installed')],
        )

    def test_mcp_3_test_probe_requires_console(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp) / 'p.toml'
            profile.write_text('[profile]\nname = "p"\n[patches]\npreset = "minimal"\n',
                               encoding='utf-8')
            config = Path(tmp) / 'tes3x.local.toml'  # none of this computer's settings
            config.write_text('', encoding='utf-8')
            with self.assertRaisesRegex(PipelineError, 'needs the console patch'):
                pipeline_main([str(profile), '--test-probe', 'mcp-3', '--check',
                               '--config', str(config)])
            self.assertEqual(pipeline_main([
                str(profile), '--test-probe', 'mcp-3', '--enable', 'console', '--check',
                '--config', str(config)
            ]), 0)

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

    def test_mcp_97_test_probe_replaces_the_shared_landing(self):
        class Image:
            base = 0x100000

            def __init__(self):
                self.data = bytearray(b'\x90' * 32 + bytes.fromhex('4185ed74c2') + b'\x90' * 16)

            def va_to_off(self, va):
                return va - self.base

        image = Image()
        site, target = image.base + 32, 0x200000
        edits = _test_mcp97(image, '', {'hooks': {
            'mcp97_test': hex(target), 'mcp97_test_site': hex(site)}})
        self.assertEqual(image.data[32], 0xE9)
        rel = struct.unpack_from('<i', image.data, 33)[0]
        self.assertEqual(site + 5 + rel, target)
        self.assertEqual([(offset, length) for offset, length, _label in edits], [(32, 5)])

    def test_mcp_98_removes_both_reference_count_changes(self):
        prefix = bytes.fromhex(
            '3bf80f85112233448b57188b07428bcf895718ff502c'
        )
        middle = b'\x90' * 64
        suffix = bytes.fromhex('ff4e18753a8b068bceff502c') + b'\x90' * 51 + bytes.fromhex('5e5b5fc3')

        class Image:
            base = 0x100000

            def __init__(self):
                self.data = bytearray(b'\x90' * 32 + prefix + middle + suffix + b'\xcc' * 16)

            def off_to_va(self, offset):
                return self.base + offset

            def va_to_off(self, va):
                return va - self.base

        image = Image()
        edits = _mcp_98(image, '', {})
        retain = 32 + prefix.index(b'\x42')
        release = 32 + len(prefix) + len(middle)
        exit_off = release + 63
        self.assertEqual(image.data[retain], 0x90)
        self.assertEqual(image.data[release:release + 3], bytes.fromhex('eb3d18'))
        self.assertEqual(release + 2 + struct.unpack('<b', image.data[release + 1:release + 2])[0],
                         exit_off)
        self.assertEqual([(offset, length) for offset, length, _label in edits],
                         [(retain, 1), (release, 2)])

    def test_mcp_98_needs_no_dedicated_hook_source(self):
        plan = resolve_patch_plan({
            'patches': {'preset': 'minimal', 'enable': ['mcp-98']},
            'package': {'mode': 'merged-bsa'},
        })
        self.assertEqual(plan['applied'], ['mcp-98'])
        self.assertEqual(plan['sources'], ['tes3xhook.c', 'tes3xlog.c', 'tes3xini.c'])

    def test_mcp_92_replaces_virtual_cleanup_with_retire_magic(self):
        unsummon = bytes.fromhex(
            '8bcee8112233448b166a018bce8bf8ff5214'
            '8bcee855667788a1112233448b486c56e899aabbcc'
            '8b176a008bcfff52706a018bcee8ddeeff00'
        )
        wrapper = bytes.fromhex('8b41148b0d556677888b496c50e811223344c3')

        class Image:
            base = 0x100000

            def __init__(self):
                self.data = bytearray(b'\x90' * 32 + unsummon + b'\x90' * 16 + wrapper
                                      + b'\xcc' * 16)

            def off_to_va(self, offset):
                return self.base + offset

            def va_to_off(self, va):
                return va - self.base

        image = Image()
        edits = _mcp_92(image, '', {})
        site = 32 + unsummon.index(bytes.fromhex('8b176a008bcfff5270'))
        target = 32 + len(unsummon) + 16
        self.assertEqual(image.data[site:site + 2], bytes.fromhex('8bcf'))
        self.assertEqual(image.data[site + 2], 0xe8)
        rel = struct.unpack_from('<i', image.data, site + 3)[0]
        self.assertEqual(image.off_to_va(site) + 7 + rel, image.off_to_va(target))
        self.assertEqual(image.data[site + 7:site + 9], b'\x90\x90')
        self.assertEqual([(offset, length) for offset, length, _label in edits], [(site, 9)])

    def test_mcp_92_needs_no_dedicated_hook_source(self):
        plan = resolve_patch_plan({
            'patches': {'preset': 'minimal', 'enable': ['mcp-92']},
            'package': {'mode': 'merged-bsa'},
        })
        self.assertEqual(plan['applied'], ['mcp-92'])
        self.assertEqual(plan['sources'], ['tes3xhook.c', 'tes3xlog.c', 'tes3xini.c'])

    def test_mcp_154_pads_both_script_data_allocations(self):
        load = bytes.fromhex(
            '2d41434454740583e81275288b874002000068551b00006864913700506a01e811223344'
        )
        reload = bytes.fromhex(
            '3d5343445475328b455885c08bbe40020000740b50e81122334483c40468551c0000'
        )

        class Image:
            base = 0x100000

            def __init__(self):
                self.data = bytearray(b'\x90' * 32 + load + b'\x90' * 16 + reload + b'\x90' * 32)

            def off_to_va(self, offset):
                return self.base + offset

            def va_to_off(self, va):
                return va - self.base

        image = Image()
        load_site = 32 + load.index(bytes.fromhex('8b8740020000'))
        reload_base = 32 + len(load) + 16
        reload_site = reload_base + reload.index(bytes.fromhex('8bbe40020000'))
        load_target, reload_target = 0x200000, 0x200020
        edits = _mcp_154(image, '', {'hooks': {
            'mcp154_load': hex(load_target), 'mcp154_reload': hex(reload_target),
        }})
        for site, target in ((load_site, load_target), (reload_site, reload_target)):
            self.assertEqual(image.data[site], 0xE9)
            self.assertEqual(image.data[site + 5], 0x90)
            rel = struct.unpack_from('<i', image.data, site + 1)[0]
            self.assertEqual(image.off_to_va(site) + 5 + rel, target)
        self.assertEqual([(offset, length) for offset, length, _label in edits],
                         [(load_site, 6), (reload_site, 6)])

    def test_mcp_154_adds_its_hook_source(self):
        plan = resolve_patch_plan({
            'patches': {'preset': 'minimal', 'enable': ['mcp-154']},
            'package': {'mode': 'merged-bsa'},
        })
        self.assertEqual(plan['applied'], ['mcp-154'])
        self.assertEqual(plan['sources'], ['tes3xhook.c', 'tes3xlog.c', 'tes3xini.c', 'tes3xmcp154.c'])

    def test_mcp_102_forces_active_bit_in_both_actn_paths(self):
        setter = bytes.fromhex(
            '8b414485c0740c83380974168b400485c075f4e808ffffff'
            '8b542404895008c204008b4c2404894808c20400'
        )

        class Image:
            base = 0x100000

            def __init__(self):
                self.data = bytearray(b'\x90' * 32 + setter + b'\xcc' * 16)

            def off_to_va(self, offset):
                return self.base + offset

            def va_to_off(self, va):
                return va - self.base

        image = Image()
        edits = _mcp_102(image, '', {})
        block = 32
        store = block + 24
        self.assertEqual(image.data[block + 11], 0x0c)
        self.assertEqual(
            image.data[store:store + 20],
            bytes.fromhex('8b54240483ca01895008c20400') + b'\x90' * 7,
        )
        self.assertEqual([(offset, length) for offset, length, _label in edits],
                         [(block + 11, 1), (store, 20)])

    def test_mcp_102_needs_no_dedicated_hook_source(self):
        plan = resolve_patch_plan({
            'patches': {'preset': 'minimal', 'enable': ['mcp-102']},
            'package': {'mode': 'merged-bsa'},
        })
        self.assertEqual(plan['applied'], ['mcp-102'])
        self.assertEqual(plan['sources'], ['tes3xhook.c', 'tes3xlog.c', 'tes3xini.c'])

    def test_mcp_102_test_probe_wraps_the_actn_load_call(self):
        class Image:
            base = 0x100000

            def __init__(self):
                self.data = bytearray(b'\x90' * 32 + b'\xe8\0\0\0\0' + b'\x90' * 16)

            def va_to_off(self, va):
                return va - self.base

            def patch_call(self, va, target):
                off = self.va_to_off(va)
                was = va + 5 + struct.unpack_from('<i', self.data, off + 1)[0]
                struct.pack_into('<i', self.data, off + 1, target - (va + 5))
                return was, off

        image = Image()
        site, target = image.base + 32, 0x200000
        edits = _test_mcp102(image, '', {'hooks': {
            'mcp102_test': hex(target), 'mcp102_test_site': hex(site)}})
        rel = struct.unpack_from('<i', image.data, 33)[0]
        self.assertEqual(site + 5 + rel, target)
        self.assertEqual([(offset, length) for offset, length, _label in edits], [(32, 5)])

    def test_mcp_123_redirects_the_shared_placeitem_insertion(self):
        prefix = bytes.fromhex(
            'a01122334484c075098b176a018bcfff52148b4c244057'
        )
        suffix = bytes.fromhex('85f60f8200000000')

        class Image:
            base = 0x100000

            def __init__(self):
                self.data = bytearray(b'\x90' * 32 + prefix + b'\xe8\x00\x00\x00\x00'
                                      + suffix + b'\xcc' * 16)
                site = self.base + 32 + len(prefix)
                struct.pack_into('<i', self.data, 32 + len(prefix) + 1,
                                 0x101000 - (site + 5))

            def off_to_va(self, offset):
                return self.base + offset

            def va_to_off(self, va):
                return va - self.base

            def patch_call(self, site, target):
                off = self.va_to_off(site)
                old = site + 5 + struct.unpack_from('<i', self.data, off + 1)[0]
                struct.pack_into('<i', self.data, off + 1, target - (site + 5))
                return old, off

        image = Image()
        target = 0x200000
        edits = _mcp_123(image, '', {'hooks': {'mcp123_add': hex(target)}})
        site = 32 + len(prefix)
        self.assertEqual(image.data[site], 0xe8)
        rel = struct.unpack_from('<i', image.data, site + 1)[0]
        self.assertEqual(image.off_to_va(site) + 5 + rel, target)
        self.assertIn('0x00101000', edits[0][2])
        self.assertEqual([(offset, length) for offset, length, _label in edits], [(site, 5)])

    def test_mcp_123_adds_its_hook_source(self):
        plan = resolve_patch_plan({
            'patches': {'preset': 'minimal', 'enable': ['mcp-123']},
            'package': {'mode': 'merged-bsa'},
        })
        self.assertEqual(plan['applied'], ['mcp-123'])
        self.assertEqual(plan['sources'], ['tes3xhook.c', 'tes3xlog.c', 'tes3xini.c', 'tes3xmcp123.c'])

    def test_mcp_125_attaches_scripts_and_selects_collision_registration(self):
        base = 0x100000
        move, remove, add, hook = 0x180000, 0x180100, 0x180200, 0x200000
        angle_a, angle_b, ref, coords, manager = (0x300001, 0x300002, 0x300010,
                                                   0x300020, 0x300030)

        def rel_call(site, target):
            return b'\xe8' + struct.pack('<i', target - (site + 5))

        class Image:
            def __init__(self):
                self.base = base
                self.data = bytearray(b'\x90' * 32)
                self.sites = []
                for prefix, angle, pushed in ((b'\xa1', angle_a, b'\x50'),
                                              (b'\x8b\x15', angle_b, b'\x52')):
                    site = self.base + len(self.data)
                    self.sites.append(site)
                    head = prefix + struct.pack('<I', angle)
                    ref_load = ((b'\x8b\x0d' if prefix == b'\xa1' else b'\xa1') +
                                struct.pack('<I', ref))
                    call_site = site + 19
                    self.data += (head + ref_load + pushed + b'\x68' + struct.pack('<I', coords) +
                                  b'\x56' + (b'\x51' if prefix == b'\xa1' else b'\x50') +
                                  rel_call(call_site, move) + b'\x83\xc4\x10\xe9\0\0\0\0')
                    self.data += b'\x90' * 16

                attach = self.base + len(self.data)
                call_site = attach + 14
                self.data += (b'\x8b\x0d' + struct.pack('<I', angle_a) + b'\x51\x68' +
                              struct.pack('<I', coords) + b'\x57\x56' + rel_call(call_site, move) +
                              b'\x83\xc4\x10\x8b\xce\xe8\0\0\0\0\x8b\x4e\x10')
                self.resume = attach + 22
                self.data += b'\x90' * 16

                self.collision = self.base + len(self.data)
                remove_site = self.collision + 21
                self.data += (b'\x8a\x44\x24\x3c\x84\xc0\x0f\x85\x97\0\0\0\xa1' +
                              struct.pack('<I', manager) + b'\x8b\x48\x5c\x57' +
                              rel_call(remove_site, remove) + b'\xe9\x84\0\0\0')
                self.data += b'\x90' * 16

                add_block = self.base + len(self.data)
                add_site = add_block + 10
                self.data += (b'\x8b\x0d' + struct.pack('<I', manager) + b'\x8b\x49\x5c\x57' +
                              rel_call(add_site, add) + b'\x8b\xcf\xe8\0\0\0\0')
                self.data += b'\xcc' * 16

            def off_to_va(self, offset):
                return self.base + offset

            def va_to_off(self, va):
                return va - self.base

        image = Image()
        edits = _mcp_125(image, '', {'hooks': {'mcp125_collision': hex(hook)}})
        for site in image.sites:
            off = image.va_to_off(site)
            self.assertEqual(image.data[off:off + 2], b'\xff\x35')
            self.assertEqual(image.data[off + 12:off + 14], b'\x8b\x35')
            call_rel = struct.unpack_from('<i', image.data, off + 20)[0]
            self.assertEqual(site + 24 + call_rel, move)
            jump_rel = struct.unpack_from('<i', image.data, off + 28)[0]
            self.assertEqual(site + 32 + jump_rel, image.resume)
        collision = image.va_to_off(image.collision)
        hook_rel = struct.unpack_from('<i', image.data, collision + 14)[0]
        self.assertEqual(image.collision + 18 + hook_rel, hook)
        self.assertEqual(image.data[collision + 18:collision + 20], b'\xeb\x06')
        self.assertEqual([(offset, length) for offset, length, _label in edits],
                         [(image.va_to_off(site), 32) for site in image.sites] + [(collision, 20)])

    def test_mcp_125_adds_its_hook_source(self):
        plan = resolve_patch_plan({
            'patches': {'preset': 'minimal', 'enable': ['mcp-125']},
            'package': {'mode': 'merged-bsa'},
        })
        self.assertEqual(plan['applied'], ['mcp-125'])
        self.assertEqual(plan['sources'], ['tes3xhook.c', 'tes3xlog.c', 'tes3xini.c', 'tes3xmcp125.c'])

    def test_testing_adds_tools_but_allows_overrides(self):
        profile = {'patches': {'preset': 'testing', 'disable': ['diagnostics']},
                   'package': {'mode': 'merged-bsa'}}
        plan = resolve_patch_plan(profile)
        self.assertEqual(plan['selected'],
                         ['mcp-3', 'dialogue-merge', 'mcp-97', 'mcp-102', 'dxt5-size',
                          'console'])

    def test_pipeline_rejects_unknown_categories_and_patches(self):
        with self.assertRaises(PipelineError):
            resolve_patch_plan({'patches': {'categories': ['external']}})
        with self.assertRaises(PipelineError):
            resolve_patch_plan({'patches': {'enable': ['not-a-patch']}})
        with self.assertRaises(PipelineError):
            resolve_patch_plan({'patches': {'enable': ['mcp-140']}})


if __name__ == '__main__':
    unittest.main()
