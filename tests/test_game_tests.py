from pathlib import Path
import tempfile
import unittest

from tes3x.game_tests import engines, first_failure, missing_mods
from tes3x.pipeline import PipelineError, validate_local_config

import tes3x.patches as registry  # noqa: E402
from tes3x.test import (GAME_TESTS, comparison_failures, game_test_problems,  # noqa: E402
                        game_test_profile, read_toml, sequence_failures, write_profile)


class GameTestFileTests(unittest.TestCase):
    """Game tests boot the game, so they never run here; their files are checked instead."""

    def test_every_game_test_is_well_formed(self):
        paths = sorted(GAME_TESTS.glob("*.toml"))
        self.assertIn("smoke.toml", [path.name for path in paths])
        for path in paths:
            with self.subTest(path.name):
                test = read_toml(path)
                self.assertEqual(game_test_problems(test, path.name), [])
                if path.stem == "smoke":
                    self.assertEqual(test["kind"], "single")
                    self.assertFalse({"enable", "apply", "save"} & set(test))
                    continue
                self.assertIn(path.stem, registry.BY_NAME, "a game test is named after its patch")
                for name in test.get("enable", []):
                    self.assertIn(name, registry.BY_NAME)
                if "apply" in test:
                    self.assertEqual(test["apply"].partition("=")[0], path.stem)

    def test_scripts_name_asserts_correctly(self):
        for path in sorted(GAME_TESTS.glob("*.toml")):
            for line in read_toml(path)["script"].splitlines():
                if line.strip().startswith("assert "):
                    with self.subTest(path.name, line=line):
                        self.assertIn(" == ", line)

    def test_ordered_expectations_do_not_reuse_an_earlier_line(self):
        log = "autosave.slot 1\nautosave.slot 2\nautosave.slot 1\n"
        self.assertEqual(sequence_failures(
            log, [r"slot 1", r"slot 2", r"slot 1"]), [])
        self.assertTrue(sequence_failures(
            log, [r"slot 1", r"slot 1", r"slot 2"]))

    def test_numeric_comparison_pairs_every_value(self):
        logs = {
            "control": "diag.free_kb 17000\ndiag.free_kb 16000\n",
            "test": "diag.free_kb 28000\ndiag.free_kb 27000\n",
        }
        comparison = [{"pattern": r"diag\.free_kb ([0-9]+)", "relation": ">"}]
        self.assertEqual(comparison_failures(logs, comparison), [])
        comparison[0]["relation"] = "<"
        self.assertTrue(comparison_failures(logs, comparison))

    def test_ui_scale_overlay_reaches_the_generated_profile(self):
        spec = read_toml(GAME_TESTS / "ui-scale.toml")
        profile = game_test_profile({"profile": {"name": "retail"}}, spec)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "profile.toml"
            write_profile(path, profile)
            self.assertEqual(read_toml(path)["ui"],
                             {"scale": 0.8, "widescreen_480p": True})


class GameTestRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for name in ("ntsc", "pal"):
            (self.root / name).mkdir()
            (self.root / name / "morrowind.xbe").write_bytes(b"XBEH" + name.encode())
        self.config = self.root / "tes3x.local.toml"
        self.config.write_text('[engines]\nntsc = "ntsc"\npal = "pal"\n', encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_engines_resolve_names_relative_to_the_config(self):
        self.assertEqual(engines(self.config, []), [("default", None)])
        self.assertEqual(engines(self.config, ["pal"]), [("pal", (self.root / "pal").resolve())])
        self.assertEqual([name for name, _ in engines(self.config, ["all"])], ["ntsc", "pal"])
        with self.assertRaises(SystemExit):
            engines(self.config, ["jp"])

    def test_required_mods_are_matched_by_name_or_id(self):
        profile = self.root / "p.toml"
        profile.write_text('[profile]\nname = "p"\n[[mods]]\nid = "mop"\n', encoding="utf-8")
        self.assertEqual(missing_mods({"required_mods": ["MOP"]}, profile), [])
        self.assertEqual(missing_mods({"required_mods": ["TR"]}, profile), ["tr"])

    def test_failure_reason_is_the_first_failed_check(self):
        output = "control: pass\ntest: FAIL\n  ok   x\n  FAIL mcp37 1\nscenario failed\n"
        self.assertEqual(first_failure(output), "test: FAIL mcp37 1")
        self.assertEqual(first_failure("KeyError: 'smoke'\n"), "KeyError: 'smoke'")

    def test_local_config_engines_are_folder_names(self):
        validate_local_config({"engines": {"pal": "D:/PAL"}})
        with self.assertRaises(PipelineError):
            validate_local_config({"engines": {"pal": 1}})


if __name__ == "__main__":
    unittest.main()
