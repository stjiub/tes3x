import contextlib
import io
import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tes3x.progress as progress  # noqa: E402

try:
    from PySide6.QtWidgets import QApplication, QTextEdit
    from tes3x.gui import logtext
    from tes3x.gui.common import StatusBar
except ImportError:
    QApplication = None


def captured(function, *args, enabled=True):
    out = io.StringIO()
    env = {progress.ENV: "1"} if enabled else {}
    with mock.patch.dict(os.environ, env, clear=False), contextlib.redirect_stdout(out):
        if not enabled:
            os.environ.pop(progress.ENV, None)
        function(*args)
    return out.getvalue().splitlines()


class ProgressTests(unittest.TestCase):
    def test_emit_and_parse_round_trip(self):
        (line,) = captured(progress.emit, "copy", 3, 10, "files", "meshes/a b.nif")
        self.assertEqual(progress.parse(line), ("copy", 3, 10, "files", "meshes/a b.nif"))
        self.assertIsNone(progress.parse("== not progress"))
        self.assertIsNone(progress.parse("@progress copy x 10 files"))

    def test_steps_report_the_phase_about_to_start(self):
        def run():
            steps = progress.Steps("build", 3)
            for name in ("one", "two", "three"):
                steps.begin(name)
        lines = [progress.parse(line) for line in captured(run)]
        self.assertEqual(lines, [("build", 0, 3, "steps", "one"), ("build", 1, 3, "steps", "two"),
                                 ("build", 2, 3, "steps", "three")])

    def test_steps_are_silent_unless_a_front_end_asked(self):
        def run():
            progress.Steps("build", 1).begin("one")
        self.assertEqual(captured(run, enabled=False), [])

    def test_counter_throttles_but_always_reports_the_last_item(self):
        def run():
            counter = progress.Counter("copy", 50)
            for index in range(50):
                counter.step(f"f{index}")
        lines = [progress.parse(line) for line in captured(run)]
        self.assertLess(len(lines), 50)
        self.assertEqual(lines[0][1], 1)
        self.assertEqual(lines[-1], ("copy", 50, 50, "files", "f49"))

    def test_counter_is_silent_unless_a_front_end_asked(self):
        def run():
            counter = progress.Counter("copy", 2)
            counter.step()
            counter.step()
        self.assertEqual(captured(run, enabled=False), [])


@unittest.skipIf(QApplication is None, "optional PySide6 dependency is not installed")
class DisplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_classify_picks_out_headers_warnings_and_errors(self):
        self.assertEqual(logtext.classify("== payload: a.c"), "header")
        self.assertEqual(logtext.classify("WARNING: dev patches"), "warning")
        self.assertEqual(logtext.classify("build failed; partial output kept"), "error")
        self.assertEqual(logtext.classify("Traceback (most recent call last):"), "error")
        self.assertEqual(logtext.classify("ValueError: bad"), "error")
        self.assertIsNone(logtext.classify("  scaled 4 UI fonts"))
        self.assertIsNone(logtext.classify("0 errors found"))

    def test_append_keeps_the_text_and_formats_each_line(self):
        log = QTextEdit()
        logtext.append(log, "== header\nplain\nerror: x\n")
        logtext.append(log, "more\n")
        self.assertEqual(log.toPlainText(), "== header\nplain\nerror: x\nmore\n")
        block = log.document().firstBlock()
        self.assertEqual(block.begin().fragment().charFormat().fontWeight(), 700)
        error = block.next().next()
        self.assertEqual(error.begin().fragment().charFormat().foreground().color().name(),
                         "#e0463c")

    def test_status_bar_shows_steps_without_speed(self):
        bar = StatusBar()
        bar.show_progress("build", 2, 8, "steps", "packing mods")
        self.assertEqual(bar.message.text(), "Step 3 of 8 · Packing mods")
        self.assertEqual(bar.fill, 0.25)
        bar.clear_progress()
        self.assertIsNone(bar.fill)

    def test_status_bar_counts_files_with_separators(self):
        bar = StatusBar()
        bar.show_progress("copy", 1200, 12000, "files", "a.nif")
        self.assertEqual(bar.message.text(), "Copying 1,200 of 12,000 · a.nif")


if __name__ == "__main__":
    unittest.main()
