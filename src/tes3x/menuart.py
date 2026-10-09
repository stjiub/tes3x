#!/usr/bin/env python3
"""Render the menu buttons (normal, highlighted, pressed) in one font, styled after the retail
ones: a gold fill lit from the upper left, a dark edge and an orange glow. The retail New, Load,
Save, Options and Exit are redrawn too, so they match the buttons multiplayer adds. The result is
committed under assets/menu, so a build needs neither Pillow nor the font.

    tes3x menuart [--out assets/menu] [--sheet preview.png]
"""

import argparse
import struct
import sys
from pathlib import Path

from tes3x.paths import checkout

ROOT = checkout()
FONT = ROOT / "assets" / "fonts" / "Fondamento-Regular.ttf"
OUT = ROOT / "assets" / "menu"
# texture name -> label, width; the retail buttons are 128x64
BUTTONS = {
    "menu_newgame": ("New", 128), "menu_loadgame": ("Load", 128),
    "menu_savegame": ("Save", 128), "menu_options": ("Options", 128),
    "menu_return": ("Return", 128), "menu_exitgame": ("Exit", 128),
    "menu_join": ("Join", 128), "menu_servers": ("Servers", 128),
    "menu_manager": ("Manager", 128),
    "menu_newserver": ("New Server", 256),
    "menu_leave": ("Leave", 128), "menu_saveserver": ("Save to Server", 256),
}
HEIGHT = 64
CAP = 21       # cap height, as the retail lettering
BASELINE = 35  # the retail lettering's baseline row
SCALE = 4      # drawn larger, then reduced
STATES = {
    "": dict(top=(245, 226, 160), bottom=(210, 168, 86), glow=(150, 80, 24), glow_a=0.7,
             reach=1.8, counters=0.0, dark=0.0),
    "_over": dict(top=(250, 232, 168), bottom=(220, 175, 90), glow=(190, 98, 26), glow_a=0.85,
                  reach=2.2, counters=0.45, dark=0.2),
    "_pressed": dict(top=(176, 160, 124), bottom=(118, 96, 62), glow=(180, 95, 30), glow_a=0.85,
                     reach=2.0, counters=0.5, dark=0.4),
}


def imports():
    try:
        import numpy
        from PIL import Image, ImageDraw, ImageFilter, ImageFont
    except ImportError:
        raise SystemExit("tes3x menuart needs numpy and Pillow")
    return numpy, Image, ImageDraw, ImageFilter, ImageFont


def coverage(label, width):
    np, Image, ImageDraw, _, ImageFont = imports()
    probe = ImageFont.truetype(str(FONT), 100)
    box = probe.getbbox("H")
    font = ImageFont.truetype(str(FONT), int(100 * CAP * SCALE / (box[3] - box[1])))
    img = Image.new("L", (width * SCALE, HEIGHT * SCALE), 0)
    draw = ImageDraw.Draw(img)
    text = draw.textbbox((0, 0), label, font=font)
    x = (width * SCALE - (text[2] - text[0])) // 2 - text[0]
    draw.text((x, BASELINE * SCALE - font.getbbox("H")[3]), label, font=font, fill=255)
    return np.asarray(img.resize((width, HEIGHT), Image.LANCZOS)).astype(float) / 255


def styled(m, state):
    np, Image, _, ImageFilter, _ = imports()
    s = STATES[state]
    mi = Image.fromarray((m * 255).astype(np.uint8))

    def filtered(*filters):
        img = mi
        for f in filters:
            img = img.filter(f)
        return np.asarray(img).astype(float) / 255

    glow = np.clip(filtered(ImageFilter.MaxFilter(5), ImageFilter.GaussianBlur(s["reach"]))
                   * 1.6, 0, 1) * s["glow_a"]
    near = filtered(ImageFilter.MaxFilter(7), ImageFilter.GaussianBlur(1.0))
    glow = np.maximum(glow, near * s["counters"])  # highlighted: the counters glow too
    gy, gx = np.gradient(filtered(ImageFilter.GaussianBlur(1.0)))
    light = np.clip((gx + gy) * 0.7 * 4, -1, 1)
    rows = np.linspace(0, 1, m.shape[0])[:, None, None]
    fill = np.array(s["top"], float) * (1 - rows) + np.array(s["bottom"], float) * rows
    fill = np.repeat(fill, m.shape[1], axis=1)
    fill = fill + np.random.default_rng(7).normal(0, 9, m.shape)[..., None] + light[..., None] * 40
    fill = fill * (1 - s["dark"] * filtered(ImageFilter.MinFilter(3))[..., None])
    edge = np.clip(filtered(ImageFilter.MaxFilter(3)) - m, 0, 1)
    out = np.zeros(m.shape + (4,))
    for rgb, a in ((s["glow"], glow), ((60, 32, 10), edge * 0.8), (np.clip(fill, 0, 255), m)):
        a = a[..., None]
        out[..., :3] = out[..., :3] * (1 - a) + np.asarray(rgb) * a
        out[..., 3:] = out[..., 3:] * (1 - a) + a
    return np.clip(out * [1, 1, 1, 255], 0, 255).astype(np.uint8)


def dds(rgba):
    """An uncompressed 32-bit DDS without mipmaps, as the retail menu textures are."""
    height, width = rgba.shape[:2]
    head = struct.pack("<4s7I44x", b"DDS ", 124, 0x100F, height, width, width * 4, 0, 0)
    head += struct.pack("<2I4s5I", 32, 0x41, b"\0\0\0\0", 32, 0xFF0000, 0xFF00, 0xFF, 0xFF000000)
    head += struct.pack("<I16x", 0x1000)
    return head + rgba[..., [2, 1, 0, 3]].tobytes()


def render(out=OUT, sheet=None):
    np, Image, _, _, _ = imports()
    out.mkdir(parents=True, exist_ok=True)
    tiles = []
    for name, (label, width) in BUTTONS.items():
        m = coverage(label, width)
        row = []
        for state in STATES:
            rgba = styled(m, state)
            (out / f"{name}{state}.dds").write_bytes(dds(rgba))
            row.append(rgba)
        tiles.append(row)
    if sheet:
        img = Image.new("RGBA", (3 * 260, len(tiles) * 68), (0, 0, 0, 255))
        for r, row in enumerate(tiles):
            for c, rgba in enumerate(row):
                img.alpha_composite(Image.fromarray(rgba), (c * 260, r * 68))
        img.save(sheet)
    return len(tiles) * len(STATES)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--out", type=Path, default=OUT)
    p.add_argument("--sheet", type=Path, help="also write a preview of every button")
    args = p.parse_args(argv)
    print(f"wrote {render(args.out, args.sheet)} textures to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
