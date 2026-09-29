import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import tes3x_patches as registry  # noqa: E402
from tes3x_test import GAME_TESTS, game_test_problems, read_toml  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
