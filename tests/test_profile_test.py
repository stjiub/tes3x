import argparse
import tempfile
import tomllib
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"

from tes3x.test import (TestError, check_log, load_scenario, run_library,  # noqa: E402
                        write_profile)


class ProfileTestTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        # resolved, as the tools resolve it: Windows may name temp by its 8.3 short form
        self.root = Path(temp.name).resolve()
        self.scenario = self.root / "scenario.toml"
        self.scenario.write_text('''
kind = "single"
purpose = "Smoke"
procedure = "Run"
watch = 'exec|crash'
script = "exit"
[expect]
test = ['exec\\.exit', '!crash\\.']
''', encoding="utf-8")

    def test_scenario_and_log_pass(self):
        scenario = load_scenario(self.scenario)
        log = self.root / "log.txt"
        log.write_text("exec.exit 0\n", encoding="ascii")
        self.assertEqual(check_log(log, scenario), (True, [], ["exec.exit 0"]))

    def test_global_diagnostic_failure_fails(self):
        scenario = load_scenario(self.scenario)
        log = self.root / "log.txt"
        log.write_text("exec.exit 0\ncrash.eip 0x1\n", encoding="ascii")
        passed, failures, _ = check_log(log, scenario)
        self.assertFalse(passed)
        self.assertIn("!crash\\.", failures)

    def test_comparison_scenario_is_rejected(self):
        self.scenario.write_text(self.scenario.read_text().replace('"single"', '"comparison"')
                                 + "control = ['exec']\n")
        with self.assertRaisesRegex(TestError, "single"):
            load_scenario(self.scenario)

    def test_ephemeral_profile_writer_round_trips(self):
        profile = {
            "profile": {"name": "p", "library": "D:/mods"},
            "patches": {"preset": "minimal", "enable": ["console"]},
            "ini": {"General:Show FPS": 1},
            "mods": [{"id": "travel", "version": "1.0", "components": ["music"],
                      "order": 10}],
        }
        path = self.root / "profile.toml"
        write_profile(path, profile)
        with open(path, "rb") as stream:
            self.assertEqual(tomllib.load(stream), profile)

    def test_library_batch_scans_plain_library_into_profiles(self):
        library = self.root / "library"
        (library / "A Mod" / "textures").mkdir(parents=True)
        template = self.root / "template.toml"
        template.write_text('[profile]\nname = "template"\n[patches]\npreset = "minimal"\n')
        args = argparse.Namespace(
            profile=str(template), library=str(library), mod=[], all_versions=False,
            work_root=str(self.root / "work"), scenario=str(self.scenario), runner=None,
            pipeline_arg=[], record=False, results=str(self.root / "results"),
            keep_artifacts="never")
        from unittest.mock import patch
        with patch("tes3x.test.run_profile", return_value=0) as run:
            self.assertEqual(run_library(args), 0)
        generated = Path(run.call_args.args[0].profile)
        with open(generated, "rb") as stream:
            profile = tomllib.load(stream)
        self.assertEqual(profile["profile"]["library"], library.as_posix())
        self.assertEqual(profile["mods"][0]["id"], "a-mod")


if __name__ == "__main__":
    unittest.main()
