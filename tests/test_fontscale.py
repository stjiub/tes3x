import os
import shutil
import struct
import tempfile
import unittest
from pathlib import Path

from tes3x.fontscale import FONT_SIZE, stage_scaled_fonts
from tes3x.pipeline import PipelineError, make_work_dir, validate_profile


class FontScaleTests(unittest.TestCase):
    def test_staged_font_scales_metrics_without_changing_atlas_or_source(self):
        data = bytearray(FONT_SIZE)
        struct.pack_into("<f", data, 0, 20.0)
        struct.pack_into("<i", data, 4, 1)
        data[8:17] = b"atlas.tex"
        struct.pack_into("<f", data, 0x128 + 4, 0.25)  # UV
        struct.pack_into("<f", data, 0x128 + 36, 10.0)  # glyph width
        struct.pack_into("<f", data, 0x128 + 40, 12.0)  # glyph height
        struct.pack_into("<f", data, 0x128 + 44, 2.0)  # left spacing
        struct.pack_into("<f", data, 0x128 + 48, 4.0)  # right spacing
        struct.pack_into("<f", data, 0x128 + 52, 6.0)  # ascent
        root = make_work_dir(Path(tempfile.gettempdir()), "tes3x-font-test-")
        try:
            source = root / "source.fnt"
            source.write_bytes(data)
            fonts = root / "Data Files" / "fonts"
            fonts.mkdir(parents=True)
            staged = fonts / "font.fnt"
            os.link(source, staged)
            stage_scaled_fonts(root / "Data Files", 0.8, 0.6)
            result = staged.read_bytes()
            self.assertEqual(source.read_bytes(), data)
            self.assertEqual(len(result), FONT_SIZE)
            self.assertEqual(result[8:17], b"atlas.tex")
            self.assertEqual(struct.unpack_from("<f", result, 0x128 + 4)[0], 0.25)
            self.assertEqual(struct.unpack_from("<f", result, 0)[0], 16.0)
            self.assertEqual([round(struct.unpack_from("<f", result, 0x128 + offset)[0], 4)
                              for offset in (36, 40, 44, 48, 52)],
                             [6.0, 9.6, 1.2, 2.4, 4.8])
        finally:
            shutil.rmtree(root)

    def test_profile_requires_finite_font_scale_in_range(self):
        for value in (0, 0.05, -0.5, 1.1, True, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(PipelineError):
                validate_profile({"profile": {"name": "p"}, "ui": {"font_scale": value}})
        validate_profile({"profile": {"name": "p"}, "ui": {"font_scale": 0.8}})


if __name__ == "__main__":
    unittest.main()
