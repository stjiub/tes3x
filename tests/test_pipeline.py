import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from tes3x_paths import check_paths, require_paths
from tes3x_pack import write_invalidation
from tes3x_build import materialize, plugin_masters
from tes3x_pipeline import (PipelineError, copy_retail_root, link_or_copy, resolve_patch_plan,
                            preference_flags, sanitized_command, validate_local_config,
                            validate_profile)
from tes3x_pipeline import main as pipeline_main
from tes3x_patch import _mcp_97, _mcp_102, _mcp_154
from tes3x_plugins import (collect, dependency_order, fetch_rules, run_arrange, validate_order,
                           warnings as mlox_warnings)
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
            'tes3xhook.c', 'tes3xlog.c', 'tes3xarch.c', 'tes3xscript.c', 'tes3xrefs.c'
        ])
        self.assertTrue(plan['needs_payload'])

    def test_legacy_mwse_adds_script_extension_dependency(self):
        profile = {'patches': {'preset': 'minimal', 'enable': ['mwse-legacy']}}
        plan = resolve_patch_plan(profile)
        self.assertEqual(plan['selected'], ['script-ext', 'mwse-legacy'])
        self.assertEqual(plan['sources'], [
            'tes3xhook.c', 'tes3xlog.c', 'tes3xscript.c', 'tes3xmwse.c'
        ])

    def test_legacy_mwse_dependency_cannot_be_disabled(self):
        profile = {'patches': {'preset': 'minimal', 'enable': ['mwse-legacy'],
                               'disable': ['script-ext']}}
        with self.assertRaisesRegex(PipelineError, 'requires script-ext'):
            resolve_patch_plan(profile)

    def test_info_name_arena_adds_pager_dependency(self):
        profile = {'patches': {'preset': 'minimal', 'enable': ['info-name-arena']}}
        plan = resolve_patch_plan(profile)
        self.assertEqual(plan['selected'], ['info-name-arena'])
        self.assertEqual(plan['sources'], [
            'tes3xhook.c', 'tes3xlog.c', 'tes3xpager.c', 'tes3xinfoarena.c'
        ])

    def test_standard_selects_release_channel_fixes_and_console(self):
        plan = resolve_patch_plan({'patches': {'preset': 'standard'},
                                   'package': {'mode': 'merged-bsa'}})
        self.assertEqual(plan['selected'], ['console'])

    def test_development_includes_unreleased_default_fixes(self):
        plan = resolve_patch_plan({'patches': {'preset': 'development'},
                                   'package': {'mode': 'merged-bsa'}})
        self.assertTrue({'mcp-1', 'mcp-97', 'mcp-154', 'mcp-102', 'dxt5-size'}
                        <= set(plan['selected']))

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
            self.assertIn('target: x F:/Games/Profile', out.getvalue())

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

    def test_local_validation_checks_public_tables_and_allows_extensions(self):
        validate_local_config({'paths': {'build_root': 'build'},
                               'deploy': {'port': 21},
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
        self.assertEqual(plan['sources'], ['tes3xhook.c', 'tes3xlog.c', 'tes3xsaves.c'])

    def test_transition_autosaves_reuses_save_hook_source(self):
        plan = resolve_patch_plan({
            'patches': {'preset': 'minimal', 'enable': ['transition-autosaves']},
            'package': {'mode': 'merged-bsa'},
        })
        self.assertEqual(plan['applied'], ['transition-autosaves'])
        self.assertEqual(plan['sources'], ['tes3xhook.c', 'tes3xlog.c', 'tes3xsaves.c'])

    def test_build_preferences_adds_specialized_hook_source(self):
        profile = {
            'profile': {'name': 'p'},
            'patches': {'preset': 'minimal'},
            'preferences': {'invert_look': False},
        }
        validate_profile(profile)
        plan = resolve_patch_plan(profile)
        self.assertEqual(plan['applied'], ['build-preferences'])
        self.assertEqual(plan['sources'], ['tes3xhook.c', 'tes3xlog.c', 'tes3xprefs.c'])
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
        self.assertEqual(plan['sources'], ['tes3xhook.c', 'tes3xlog.c', 'tes3xmcp154.c'])

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
        self.assertEqual(plan['sources'], ['tes3xhook.c', 'tes3xlog.c'])

    def test_development_adds_tools_but_allows_overrides(self):
        profile = {'patches': {'preset': 'development', 'disable': ['diagnostics']},
                   'package': {'mode': 'merged-bsa'}}
        plan = resolve_patch_plan(profile)
        self.assertEqual(plan['selected'],
                         ['mcp-1', 'mcp-97', 'mcp-154', 'mcp-102', 'dxt5-size', 'console'])

    def test_pipeline_rejects_unknown_categories_and_patches(self):
        with self.assertRaises(PipelineError):
            resolve_patch_plan({'patches': {'categories': ['external']}})
        with self.assertRaises(PipelineError):
            resolve_patch_plan({'patches': {'enable': ['not-a-patch']}})
        with self.assertRaises(PipelineError):
            resolve_patch_plan({'patches': {'enable': ['mcp-140']}})


if __name__ == '__main__':
    unittest.main()
