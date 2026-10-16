"""Scale Xbox bitmap font metrics while keeping their texture atlases."""

import math
from pathlib import Path
import struct


FONT_SIZE = 0x3928
GLYPH_START = 0x128
GLYPH_SIZE = 56
GLYPH_COUNT = 256
METRICS_START = 36


def scale_font(data: bytes, factor: float, width_factor: float | None = None) -> bytes:
    if len(data) != FONT_SIZE or not 1 <= struct.unpack_from("<i", data, 4)[0] <= 8:
        raise ValueError("unexpected Xbox bitmap font layout")
    scaled = bytearray(data)
    if width_factor is None:
        width_factor = factor
    offsets = [(0, factor)] + [
        (GLYPH_START + glyph * GLYPH_SIZE + METRICS_START + metric * 4,
         width_factor if metric in (0, 2, 3) else factor)
        for glyph in range(GLYPH_COUNT) for metric in range(5)]
    for offset, axis_factor in offsets:
        value = struct.unpack_from("<f", data, offset)[0]
        if not math.isfinite(value):
            raise ValueError(f"nonfinite Xbox font metric at {offset:#x}")
        struct.pack_into("<f", scaled, offset, value * axis_factor)
    return bytes(scaled)


def stage_scaled_fonts(data_files: Path, factor: float,
                       width_factor: float | None = None) -> list[Path]:
    folder = next((path for path in data_files.iterdir()
                   if path.is_dir() and path.name.casefold() == "fonts"), None)
    fonts = sorted(path for path in folder.iterdir() if path.suffix.casefold() == ".fnt") \
        if folder else []
    if not fonts:
        raise ValueError(f"no Xbox bitmap fonts in {data_files}")
    scaled_fonts = [(path, scale_font(path.read_bytes(), factor, width_factor))
                    for path in fonts]
    for path, scaled in scaled_fonts:
        path.unlink()  # staged retail files may be hard links
        path.write_bytes(scaled)
    return fonts
