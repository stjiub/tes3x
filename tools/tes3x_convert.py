#!/usr/bin/env python3
"""Texture conversion for Xbox: power-of-two, size cap, DXT, mipmap chain."""

import hashlib
import io
import os
import struct

from PIL import Image

DDS_MAGIC = b"DDS "
DDSD_CAPS, DDSD_HEIGHT, DDSD_WIDTH = 0x1, 0x2, 0x4
DDSD_PIXELFORMAT, DDSD_MIPMAPCOUNT, DDSD_LINEARSIZE = 0x1000, 0x20000, 0x80000
DDPF_FOURCC = 0x4
DDSCAPS_COMPLEX, DDSCAPS_MIPMAP, DDSCAPS_TEXTURE = 0x8, 0x400000, 0x1000


def pot(n):
    p = 1
    while p * 2 <= n:
        p *= 2
    return max(p, 4)


def target_size(w, h, cap):
    """Scale proportionally so the longest edge fits cap, then snap to power of two."""
    scale = min(1.0, cap / max(w, h))
    return pot(round(w * scale)), pot(round(h * scale))


def has_alpha(img):
    if img.mode != "RGBA":
        return False
    return img.getchannel("A").getextrema()[0] < 255


def encode_level(img, fourcc):
    buf = io.BytesIO()
    img.save(buf, format="DDS", pixel_format=fourcc)
    return buf.getvalue()[128:]


def dds_header(w, h, fourcc, mips, linear_size):
    flags = DDSD_CAPS | DDSD_HEIGHT | DDSD_WIDTH | DDSD_PIXELFORMAT | DDSD_LINEARSIZE
    caps = DDSCAPS_TEXTURE
    if mips > 1:
        flags |= DDSD_MIPMAPCOUNT
        caps |= DDSCAPS_MIPMAP | DDSCAPS_COMPLEX
    hdr = bytearray(128)
    hdr[0:4] = DDS_MAGIC
    struct.pack_into("<7I", hdr, 4, 124, flags, h, w, linear_size, 0, mips)
    struct.pack_into("<2I4s5I", hdr, 76, 32, DDPF_FOURCC, fourcc.encode(), 0, 0, 0, 0, 0)
    struct.pack_into("<4I", hdr, 108, caps, 0, 0, 0)
    return bytes(hdr)


def convert_texture(src, max_size=512, force_fourcc=None):
    """Returns (dds_bytes, note) or (None, reason) if unchanged/unsupported."""
    img = Image.open(src)
    img.load()
    if img.mode != "RGBA":
        img = img.convert("RGBA")

    w, h = target_size(img.width, img.height, max_size)
    fourcc = force_fourcc or ("DXT5" if has_alpha(img) else "DXT1")

    if (w, h) != img.size:
        img = img.resize((w, h), Image.LANCZOS)

    levels, lw, lh = [], w, h
    while True:
        level = img if (lw, lh) == (w, h) else img.resize((lw, lh), Image.LANCZOS)
        levels.append(encode_level(level, fourcc))
        if lw == 4 and lh == 4:
            break
        lw, lh = max(4, lw // 2), max(4, lh // 2)

    header = dds_header(w, h, fourcc, len(levels), len(levels[0]))
    return header + b"".join(levels), f"{w}x{h} {fourcc} {len(levels)} mips"


def cache_key(src, max_size, fourcc):
    h = hashlib.blake2b(digest_size=16)
    with open(src, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    h.update(f"{max_size}:{fourcc}".encode())
    return h.hexdigest()


def convert_cached(src, cache_dir, max_size=512, force_fourcc=None):
    key = cache_key(src, max_size, force_fourcc)
    path = os.path.join(cache_dir, key[:2], key + ".dds")
    if os.path.exists(path):
        with open(path, "rb") as f:
            return f.read(), "cached"
    data, note = convert_texture(src, max_size, force_fourcc)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return data, note


if __name__ == "__main__":
    import sys
    for src in sys.argv[1:]:
        before = os.path.getsize(src)
        data, note = convert_texture(src)
        img = Image.open(src)
        print(f"{os.path.basename(src):<40} {img.size[0]}x{img.size[1]} {before:>8}B -> {note:<22} {len(data):>8}B")
        Image.open(io.BytesIO(data)).load()
