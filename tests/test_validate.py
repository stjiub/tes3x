import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import tes3x_validate as validation

MCP102_BIT = 1 << 12


def log(mask, loaded):
    return (f'0 ms entry.free_kb 59800\n16 ms diag.session 0x1\n18 ms diag.build 0x12345678\n'
            f'20 ms diag.patches 0x{mask:08X}\n25570 ms mcp102.loaded {loaded}\n')


class ValidationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for name, value in (('ROOT', self.root), ('PATCH_DIRS', self.root / 'patches')):
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
            'command': ['python', 'tools/xemu_run.py', '<run>'],
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
        scenario = self.root / 'patches' / 'mcp-102' / 'scenario.toml'
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
        return self.root / 'patches' / 'mcp-102' / '2026-09-23-xemu.toml'

    def test_record_keeps_logs_masks_and_observed_lines(self):
        path = self.record(0x5, 0x5 | MCP102_BIT)
        record = validation.load_record(path)
        control, tested = record['run']
        self.assertEqual(tested['patches'], '0x00001005')
        self.assertEqual(tested['build'], '0x12345678')
        self.assertEqual(control['observed'], ['25570 ms mcp102.loaded 0'])
        self.assertTrue((path.parent / tested['log']).is_file())
        self.assertEqual(record['schema'], 3)
        provenance = json.loads((path.parent / record['provenance']).read_text())
        self.assertEqual(provenance['inputs']['profile'], 'validation')
        self.assertEqual(provenance['platform']['guest_ram_mb'], 64)

    def test_single_scenario_records_only_the_test_run(self):
        tested = self.run_folder('test', 0x5 | MCP102_BIT, 1,
                                 ['diagnostics', 'mcp-102'])
        scenario = self.root / 'patches' / 'mcp-102' / 'scenario.toml'
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
        path = self.root / 'patches' / 'mcp-102' / '2026-09-23-xemu.toml'
        record = validation.load_record(path)
        self.assertEqual(record['kind'], 'single')
        self.assertEqual([run['role'] for run in record['run']], ['test'])

    def test_passing_result_requires_scenario_expectations(self):
        tested = self.run_folder('test', 0x5 | MCP102_BIT, 0,
                                 ['diagnostics', 'mcp-102'])
        scenario = self.root / 'patches' / 'mcp-102' / 'scenario.toml'
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
        self.assertFalse([path for path in (self.root / 'patches').rglob('*.toml')
                          if path.name != 'scenario.toml'])

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


class RepositoryValidationTests(unittest.TestCase):
    def test_results_and_table_validation_agree(self):
        self.assertEqual(validation.check_all(), [])


if __name__ == '__main__':
    unittest.main()
