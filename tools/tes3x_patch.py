"""Apply modular patches to a retail Morrowind XBE.

Every patch locates its targets by content - a unique literal, or a push/call pair keyed on the
address of a string - never by a bare file offset. A build that does not match fails loudly
instead of corrupting an image quietly, and the same patch keeps working if offsets move.

    tes3x_patch.py morrowind.xbe --out patched.xbe \\
        --apply drive-letters=T --apply boot-media \\
        --apply payload=build/archhook/tes3xhook.pe --apply multi-bsa
"""

import argparse
import json
import os
import re
import struct
import sys

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])
import tes3x_inject  # noqa: E402

TITLE_ID = 0x42530005

# Asset paths the engine opens by literal name. Retail splits them across two drives; a build
# needs them all pointing wherever Data Files actually lives.
ASSET_PATHS = [
    b"Data Files\\Fonts",
    b"Data Files\\",
    b"Data Files\\%s",
    b"Data Files\\Morrowind.bsa",
    b"Data Files\\Meshes",
    b"Data Files\\morrowind.esm.map",
]

# `mov dword ptr [esp+0xC], "Z:\"` - the drive prefix built inline in .text rather than
# referenced as a string, which is why the scene hex edit has a lone .text byte in it.
INLINE_DRIVE = bytes([0xC7, 0x44, 0x24, 0x0C]) + b"%c:\\\x00"

SAVE_STAGING = [b"tempsave.ess", b"vv.dat"]

CERT_ALLOWED_MEDIA = 0x220
CERT_GAME_REGION = 0x224
MEDIA_ANY = 0xC00001FF
REGION_ANY = 0x00000007

PATCHES = {}


class PatchError(Exception):
    pass


def patch(name, takes=None):
    def register(fn):
        PATCHES[name] = (fn, takes, (fn.__doc__ or "").strip().splitlines()[0])
        return fn
    return register


def find_unique(data, needle, what):
    hits = [m.start() for m in re.finditer(re.escape(needle), data)]
    if len(hits) != 1:
        raise PatchError("%s: %d match(es) for %r, expected exactly 1" % (what, len(hits), needle))
    return hits[0]


def drive_letter(value, what):
    if not value or len(value) != 1 or not value.isalpha():
        raise PatchError("%s: want a single drive letter, got %r" % (what, value))
    return value.upper()


@patch("drive-letters", takes="LETTER")
def _drive_letters(x, value, ctx):
    """Point every Data Files asset path at one drive."""
    letter = drive_letter(value, "drive-letters")
    edits = []
    # Retail splits these across Z: and D:, and some appear on both, so every occurrence is
    # rewritten. The NUL terminator in the pattern keeps a shorter path from matching inside a
    # longer one.
    for tail in ASSET_PATHS:
        hits = [m.start() for m in re.finditer(rb"[A-Za-z]:\\" + re.escape(tail) + rb"\x00", x.data)]
        if not hits:
            raise PatchError("drive-letters: no match for %r" % tail)
        for off in hits:
            if x.data[off] != ord(letter):
                edits.append((off, 1, "%c:\\%s -> %s:" % (x.data[off], tail.decode(), letter)))
                x.data[off] = ord(letter)

    hits = [m.start() for m in re.finditer(rb"\xC7\x44\x24\x0C[A-Za-z]:\\\x00", x.data)]
    if len(hits) != 1:
        raise PatchError("drive-letters: %d inline drive store(s), expected 1" % len(hits))
    off = hits[0] + 4
    if x.data[off] != ord(letter):
        edits.append((off, 1, "inline %s:\\" % letter))
        x.data[off] = ord(letter)
    return edits


@patch("save-staging", takes="LETTER")
def _save_staging(x, value, ctx):
    """Stage saves on one volume with UDATA so the commit renames instead of copying."""
    letter = drive_letter(value, "save-staging")
    edits = []

    def set_letter(off, label):
        want = letter if x.data[off] < 0x61 else letter.lower()
        if x.data[off] != ord(want):
            edits.append((off, 1, "%s -> %s" % (label, want)))
            x.data[off] = ord(want)

    # The .ess literal, the bare drive prefix the writer joins with a name, and the bare
    # filename all sit in one run; matching them together keeps the prefix unambiguous.
    hits = list(re.finditer(
        rb"[A-Za-z]:\\tempsave\.ess\x00[A-Za-z]:\\\x00tempsave\.ess\x00", x.data))
    if len(hits) != 1:
        raise PatchError("save-staging: %d tempsave block(s), expected 1" % len(hits))
    set_letter(hits[0].start(), "tempsave.ess path")
    set_letter(hits[0].start() + 16, "staging drive prefix")

    for tail in SAVE_STAGING[1:]:
        found = list(re.finditer(rb"[A-Za-z]:\\" + re.escape(tail) + rb"\x00", x.data))
        if not found:
            raise PatchError("save-staging: no match for %r" % tail)
        for m in found:
            set_letter(m.start(), tail.decode())
    return edits


@patch("boot-media")
def _boot_media(x, value, ctx):
    """Permit booting from any media and region, not just a retail DVD."""
    edits = []
    for off, want, label in ((CERT_ALLOWED_MEDIA, MEDIA_ANY, "dwAllowedMedia"),
                             (CERT_GAME_REGION, REGION_ANY, "dwGameRegion")):
        if struct.unpack_from("<I", x.data, off)[0] != want:
            struct.pack_into("<I", x.data, off, want)
            edits.append((off, 4, "%s = 0x%08X" % (label, want)))
    return edits


@patch("payload", takes="FILE.pe")
def _payload(x, value, ctx):
    """Inject a code section and run it from the entry point."""
    if not value:
        raise PatchError("payload: needs a linked PE")
    blob, vsize, entry_rva, imgbase = tes3x_inject.load_pe(value)
    va = x.next_va()
    if imgbase != va:
        raise PatchError("payload linked at 0x%08X but the section lands at 0x%08X; "
                         "relink with /base:0x%X" % (imgbase, va, va))
    x.add_section(ctx["section"], blob, vsize, 0x02 | 0x04 | 0x01)
    x.set_entry(imgbase + entry_rva)
    ctx["payload"] = value
    manifest = os.path.splitext(value)[0] + ".json"
    if os.path.exists(manifest):
        with open(manifest, encoding="utf-8") as f:
            ctx["hooks"] = json.load(f).get("hooks", {})
    return [(None, vsize, "section %s at 0x%08X, entry -> 0x%08X"
             % (ctx["section"], va, imgbase + entry_rva))]


@patch("multi-bsa")
def _multi_bsa(x, value, ctx):
    """Load every archive listed in tes3xarch.txt, not just Morrowind.bsa."""
    target = ctx.get("hooks", {}).get("archive_load")
    if not target:
        raise PatchError("multi-bsa: needs `payload` first, with an archive_load hook in its "
                         "manifest")
    target = int(str(target), 16)
    off = find_unique(x.data, b":\\Data Files\\Morrowind.bsa\x00", "archive path")
    string_va = x.off_to_va(off - 1)
    if string_va is None:
        raise PatchError("multi-bsa: archive path is outside any section")
    # `push <string>; call rel32` - the one place the hardcoded archive is opened
    sites = [m.start() for m in
             re.finditer(re.escape(b"\x68" + struct.pack("<I", string_va)) + rb"\xE8", x.data)]
    if len(sites) != 1:
        raise PatchError("multi-bsa: %d push/call site(s) for the archive path, expected 1"
                         % len(sites))
    site_va = x.off_to_va(sites[0]) + 5
    was, call_off = x.patch_call(site_va, target)
    return [(call_off, 5, "Archive::Load call 0x%08X: 0x%08X -> 0x%08X" % (site_va, was, target))]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("xbe", nargs="?")
    ap.add_argument("--out")
    ap.add_argument("--apply", action="append", default=[], metavar="NAME[=VALUE]")
    ap.add_argument("--section", default=".tes3xhk")
    ap.add_argument("--list", action="store_true", help="list available patches and exit")
    a = ap.parse_args()

    if a.list or not a.xbe:
        print("\n  available patches:\n")
        for name, (_fn, takes, help_text) in PATCHES.items():
            spec = "%s=%s" % (name, takes) if takes else name
            print("    %-26s %s" % (spec, help_text))
        print()
        return

    raw = open(a.xbe, "rb").read()
    x = tes3x_inject.Xbe(raw)
    cert = struct.unpack_from("<I", x.data, 0x118)[0]
    title = struct.unpack_from("<I", x.data, cert - x.base + 8)[0]
    print("%s: %d bytes, title 0x%08X, %d sections" % (a.xbe, len(raw), title, len(x.sections)))
    if title != TITLE_ID:
        raise SystemExit("not Morrowind (title 0x%08X, want 0x%08X)" % (title, TITLE_ID))
    if any(s.name == a.section for s in x.sections):
        raise SystemExit("already carries a %s section; patch a clean XBE instead" % a.section)

    ctx = {"section": a.section}
    touched = []
    for spec in a.apply:
        name, _, value = spec.partition("=")
        if name not in PATCHES:
            raise SystemExit("unknown patch %r; --list shows them" % name)
        fn, takes, _help = PATCHES[name]
        if takes and not value:
            raise SystemExit("%s needs a value: %s=%s" % (name, name, takes))
        print("\n  %s" % spec)
        try:
            for off, length, label in fn(x, value, ctx):
                print("    %s" % label)
                if off is not None:
                    touched.append((off, length))
        except PatchError as exc:
            raise SystemExit("  FAILED: %s" % exc)

    x.rebuild_headers()
    out = bytes(x.data)
    problems = tes3x_inject.verify(raw, out, touched)
    if problems:
        for p in problems:
            print("  FAIL: %s" % p)
        raise SystemExit("refusing to write a corrupted XBE")
    print("\n  verified: %d byte(s) changed in the original image, headers intact"
          % sum(n for _, n in touched))
    if a.out:
        open(a.out, "wb").write(out)
        print("  wrote %s (%d bytes)" % (a.out, len(out)))
    else:
        print("  (no --out; nothing written)")


if __name__ == "__main__":
    main()
