import unittest

import tes3x.patches as registry  # noqa: E402
from tes3x.test import (GAME_TESTS, comparison_failures, game_test_problems,  # noqa: E402
                        read_toml, sequence_failures)


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


if __name__ == "__main__":
    unittest.main()
