import json
import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import tes3x.validate as validation

MCP102_BIT = 1 << 12


def log(mask, loaded):
    return (f'0 ms entry.free_kb 59800\n16 ms diag.session 0x1\n18 ms diag.build 0x12345678\n'
            f'20 ms diag.patches 0x{mask:08X}\n25570 ms mcp102.loaded {loaded}\n')


class ValidationTests(unittest.TestCase):
    def setUp(self):
        test_tmp = Path(__file__).resolve().parents[1] / 'build' / 'test-tmp'
        test_tmp.mkdir(parents=True, exist_ok=True)
        self.root = test_tmp / uuid.uuid4().hex
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.patches = self.root / 'validation' / 'patches'
        self.games = self.root / 'tests' / 'game'
        for name, value in (('VALIDATION', self.root / 'validation'),
                            ('PATCH_DIRS', self.patches), ('GAME_TESTS', self.games)):
            patcher = patch.object(validation, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_folder(self, role, mask, loaded, patches=None, ini='8' * 64):
        folder = self.root / role
        (folder / 'pipeline').mkdir(parents=True)
        (folder / 'tes3xlog.txt').write_text(log(mask, loaded))
        marker = {
            'schema': 2, 'profile': 'validation', 'profile_sha256': '1' * 64,
            'preset': 'minimal', 'patches': patches or ['diagnostics'],
            'package_mode': 'retail', 'command': ['python', 'tools/tes3x_pipeline.py'],
            'ini': {'base_sha256': '2' * 64, 'staged_sha256': ini, 'overrides': []},
            'mods': [], 'plugins': [], 'data_files_sha256': '3' * 64,
            'toolchain': {}, 'deploy_tree': 'deploy', 'tes3x': 'abcdef123456',
            'retail_xbe_sha1': '4' * 40,
            'morrowind_xbe_sha256': ('6' if role == 'control' else '7') * 64,
        }
        (folder / 'pipeline' / validation.MARKER).write_text(json.dumps(marker))
        run_marker = {
            'schema': 1, 'environment': 'xemu',
            'command': ['python', 'tools/tes3x_xemu.py', '<run>'],
            'platform': {'kind': 'xemu', 'version': 'test', 'bios': 'test.bin',
                         'guest_ram_mb': 64},
            'fixtures': {'script': {'name': 'validation.txt', 'size': 1, 'sha256': '5' * 64}},
            'pipeline': marker, 'morrowind_xbe_sha256': marker['morrowind_xbe_sha256'],
        }
        (folder / validation.RUN_MARKER).write_text(json.dumps(run_marker))
        return folder

    def record(self, control_mask, test_mask, control_patches=None, test_patches=None,
               control_ini='8' * 64, test_ini='8' * 64):
        control = self.run_folder('control', control_mask, 0, control_patches, control_ini)
        tested = self.run_folder('test', test_mask, 1,
                                 test_patches or ['diagnostics', 'mcp-102'], test_ini)
        scenario = self.games / 'mcp-102.toml'
        scenario.parent.mkdir(parents=True, exist_ok=True)
        scenario.write_text('''kind = "comparison"
purpose = "c"
procedure = "m"
watch = 'mcp102\\.loaded'
script = "exit"
[expect]
control = ['mcp102\\.loaded 0']
test = ['mcp102\\.loaded 1']
''')
        validation.main(['record', 'mcp-102', '--env', 'xemu',
                         '--scenario', str(scenario), '--control', str(control),
                         '--test', str(tested), '--date', '2026-09-23'])
        return self.patches / 'mcp-102' / '2026-09-23-xemu.toml'

    def test_record_keeps_logs_masks_and_observed_lines(self):
        path = self.record(0x5, 0x5 | MCP102_BIT)
        record = validation.load_record(path)
        control, tested = record['run']
        self.assertEqual(tested['patches'], '0x00001005')
        self.assertEqual(tested['build'], '0x12345678')
        self.assertEqual(control['observed'], ['25570 ms mcp102.loaded 0'])
        self.assertTrue((path.parent / tested['log']).is_file())
        self.assertEqual(record['schema'], 3)
        self.assertEqual(record['scenario'], 'tests/game/mcp-102.toml')
        self.assertIsInstance(record['recorded_at'], validation.datetime.datetime)
        provenance = json.loads((path.parent / record['provenance']).read_text())
        self.assertEqual(provenance['inputs']['profile'], 'validation')
        self.assertEqual(provenance['platform']['guest_ram_mb'], 64)

    def test_record_does_not_expose_local_paths(self):
        path = self.record(0x5, 0x5 | MCP102_BIT)
        for artifact in path.parent.iterdir():
            if artifact.suffix in {'.toml', '.json', '.log'}:
                self.assertNotIn(str(self.root), artifact.read_text(encoding='utf-8'))

    def test_single_scenario_records_only_the_test_run(self):
        tested = self.run_folder('test', 0x5 | MCP102_BIT, 1,
                                 ['diagnostics', 'mcp-102'])
        scenario = self.games / 'mcp-102.toml'
        scenario.parent.mkdir(parents=True, exist_ok=True)
        scenario.write_text('''kind = "single"
purpose = "exercise the feature"
procedure = "run it"
watch = 'mcp102\\.loaded'
script = "exit"
[expect]
test = ['mcp102\\.loaded 1']
''')
        validation.main(['record', 'mcp-102', '--env', 'xemu',
                         '--scenario', str(scenario), '--test', str(tested),
                         '--date', '2026-09-23'])
        path = self.patches / 'mcp-102' / '2026-09-23-xemu.toml'
        record = validation.load_record(path)
        self.assertEqual(record['kind'], 'single')
        self.assertEqual([run['role'] for run in record['run']], ['test'])

    def test_passing_result_requires_scenario_expectations(self):
        tested = self.run_folder('test', 0x5 | MCP102_BIT, 0,
                                 ['diagnostics', 'mcp-102'])
        scenario = self.games / 'mcp-102.toml'
        scenario.parent.mkdir(parents=True, exist_ok=True)
        scenario.write_text('''kind = "single"
purpose = "exercise the feature"
procedure = "run it"
watch = 'mcp102\\.loaded'
script = "exit"
[expect]
test = ['mcp102\\.loaded 1']
''')
        with self.assertRaises(SystemExit):
            validation.main(['record', 'mcp-102', '--env', 'xemu',
                             '--scenario', str(scenario), '--test', str(tested)])

    def test_test_run_must_carry_the_patch(self):
        with self.assertRaises(SystemExit):
            self.record(0x5, 0x5)
        self.assertFalse(list(self.patches.rglob('*.toml')))

    def test_runtime_masks_may_differ_only_by_the_patch(self):
        with self.assertRaises(SystemExit):
            self.record(0x5, 0x5 | MCP102_BIT | (1 << 3))

    def test_build_patch_lists_may_differ_only_by_the_patch(self):
        with self.assertRaises(SystemExit):
            self.record(0x5, 0x5 | MCP102_BIT,
                        test_patches=['diagnostics', 'console', 'mcp-102'])

    def test_valued_patch_spec_uses_its_patch_name(self):
        control = self.run_folder('control', 0x5, 0, ['diagnostics'])
        tested = self.run_folder('test', 0x5, 1,
                                 ['diagnostics', 'title=TES3X Validation'])
        watch = validation.re.compile('mcp102')
        runs = [('control', validation.read_run(control, watch)),
                ('test', validation.read_run(tested, watch))]
        provenance = validation.build_provenance('title', 'xemu', runs)
        self.assertEqual(provenance['patch'], 'title')

    def test_build_inputs_must_match(self):
        with self.assertRaises(SystemExit):
            self.record(0x5, 0x5 | MCP102_BIT, test_ini='9' * 64)

    def test_hardware_description_tracks_visible_ram_and_cpu(self):
        path = self.root / 'hardware.toml'
        path.write_text('''
[hardware]
unit = "xbox-a"
board_revision = "1.4"
installed_ram_mb = 128
title_ram_mb = 64
cpu = "stock"
cpu_mhz = 733
bios = "retail"
''')
        hardware = validation.load_hardware(path)
        self.assertEqual(hardware['title_ram_mb'], 64)
        self.assertEqual(hardware['cpu_mhz'], 733)

    def test_edited_provenance_breaks_the_record(self):
        path = self.record(0x5, 0x5 | MCP102_BIT)
        record = validation.load_record(path)
        (path.parent / record['provenance']).write_text('{}')
        with self.assertRaises(validation.ValidationError):
            validation.load_record(path)

    def test_edited_log_breaks_the_record(self):
        path = self.record(0x5, 0x5 | MCP102_BIT)
        (path.parent / '2026-09-23-xemu-test.log').write_text('edited')
        with self.assertRaises(validation.ValidationError):
            validation.load_record(path)


    def test_gate_follows_the_current_game_test(self):
        path = self.record(0x5, 0x5 | MCP102_BIT)
        records = {'r': validation.load_record(path)}
        self.assertEqual(validation.standing('mcp-102', records)[0], 'current')
        game_test = self.games / 'mcp-102.toml'
        game_test.write_text(game_test.read_text() + '# changed\n')
        self.assertEqual(validation.standing('mcp-102', records)[0], 'stale')
        self.assertEqual(validation.standing('mcp-97', records), ('none', None))
        legacy = {'patch': 'mcp-102', 'result': 'pass', 'date': records['r']['date']}
        self.assertEqual(validation.standing('mcp-102', {'l': legacy})[0], 'legacy')

    def test_latest_result_can_be_a_failure_after_a_pass(self):
        records = {
            '2026-09-29-xemu.toml': {
                'patch': 'mcp-102', 'result': 'pass', 'environment': 'xemu',
                'date': validation.datetime.date(2026, 9, 29),
            },
            '2026-09-30-xemu.toml': {
                'patch': 'mcp-102', 'result': 'fail', 'environment': 'xemu',
                'date': validation.datetime.date(2026, 9, 30),
            },
        }
        self.assertEqual(validation.latest_result('mcp-102', records)['result'], 'fail')

    def test_gate_fails_preview_patches_without_a_current_pass(self):
        entries = [{'name': 'mcp-102', 'channel': 'preview'},
                   {'name': 'mcp-97', 'channel': 'preview'},
                   {'name': 'mcp-37', 'channel': 'dev'}]
        path = self.record(0x5, 0x5 | MCP102_BIT)
        with patch.object(validation.registry, 'PATCHES', entries):
            failures, warnings = validation.gate({'r': validation.load_record(path)})
        self.assertEqual(failures, ['mcp-97 (preview): no passing result'])
        self.assertEqual(warnings, [])


class RepositoryValidationTests(unittest.TestCase):
    def test_results_and_table_validation_agree(self):
        self.assertEqual(validation.check_all(), [])


if __name__ == '__main__':
    unittest.main()
