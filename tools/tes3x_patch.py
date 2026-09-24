"""Apply content-located patches to a retail Morrowind XBE."""

import argparse
import json
import os
import re
import struct
import sys

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])
import tes3x_inject  # noqa: E402
import tes3x_patches as registry  # noqa: E402

TITLE_ID = 0x42530005

# Literal asset paths that must share the Data Files drive.
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

# Script::RunFunction's dispatch: `lea edx,[ecx-0x1000]; cmp edx,0x1BC`, then an indirect jump
# through a 445-entry table. Unique in the image, and it anchors the function's own address.
RUNFN_DISPATCH = bytes([0x8D, 0x91, 0x00, 0xF0, 0xFF, 0xFF, 0x81, 0xFA, 0xBC, 0x01, 0x00, 0x00])
RUNFN_PROLOGUE = bytes([0x55, 0x8B, 0xEC, 0x83, 0xE4, 0xF8])

# Six parsers infer instruction length from [0x1000, 0x11BD); unknown opcodes desync bytecode.
# Five compare 16-bit registers and one compares 32 bits.
OPCODE_LO = 0x1000
OPCODE_HI = 0x11BD
# Backreferences require both bounds to compare the same 16-bit register.
BOUND16 = re.compile(rb"(?P<cmp>\x66\x3d|\x66\x81[\xf8-\xff])\x00\x10.{0,12}?(?P=cmp)\xbd\x11",
                     re.S)
BOUND32 = re.compile(rb"\x3d\x00\x10\x00\x00.{0,12}?\x3d\xbd\x11\x00\x00", re.S)
BOUND16_COUNT = 5
BOUND32_COUNT = 1

CERT_ALLOWED_MEDIA = 0x220
CERT_GAME_REGION = 0x224
# wszTitleName, 40 UTF-16 characters. This is the name a dashboard lists.
CERT_TITLE_NAME = 0x190
CERT_TITLE_CHARS = 40
MEDIA_ANY = 0xC00001FF
REGION_ANY = 0x00000007

PATCHES = {}

PATCH_BITS = {entry["name"]: 1 << entry["bit"] for entry in registry.PATCHES if "bit" in entry}


class PatchError(Exception):
    pass


def patch(name):
    """Register fn as the patch patches.toml calls name, with its value and summary from there."""
    entry = registry.BY_NAME[name]

    def register(fn):
        PATCHES[name] = (fn, entry.get("takes"), entry["summary"])
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


@patch("drive-letters")
def _drive_letters(x, value, ctx):
    """Point every Data Files asset path at one drive."""
    letter = drive_letter(value, "drive-letters")
    edits = []
    # Rewrite every occurrence; the NUL keeps short paths from matching longer ones.
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


@patch("save-staging")
def _save_staging(x, value, ctx):
    """Stage saves on one volume with UDATA so the commit renames instead of copying."""
    letter = drive_letter(value, "save-staging")
    edits = []

    def set_letter(off, label):
        want = letter if x.data[off] < 0x61 else letter.lower()
        if x.data[off] != ord(want):
            edits.append((off, 1, "%s -> %s" % (label, want)))
            x.data[off] = ord(want)

    # Match the adjacent path, drive prefix, and filename as one unambiguous block.
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


@patch("title")
def _title(x, value, ctx):
    """Rename the image, so parallel installs are told apart in a dashboard."""
    name = value.strip()
    if not name:
        raise PatchError("title: needs a name")
    if len(name) > CERT_TITLE_CHARS - 1:
        raise PatchError("title: %r is %d characters, the certificate holds %d"
                         % (name, len(name), CERT_TITLE_CHARS - 1))
    size = CERT_TITLE_CHARS * 2
    was = bytes(x.data[CERT_TITLE_NAME:CERT_TITLE_NAME + size]).decode("utf-16-le")
    x.data[CERT_TITLE_NAME:CERT_TITLE_NAME + size] = name.encode("utf-16-le").ljust(size, b"\0")
    return [(CERT_TITLE_NAME, size, "title %r -> %r" % (was.split("\x00")[0], name))]


@patch("payload")
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


def text_section(x):
    for s in x.sections:
        if s.name == ".text":
            return s
    raise PatchError("no .text section")


def find_run_function(x):
    """Find RunFunction from its unique dispatch and checked prologue."""
    off = find_unique(x.data, RUNFN_DISPATCH, "RunFunction dispatch")
    i = off
    limit = max(0, off - 0x400)
    while i > limit and not (x.data[i - 1] == 0xCC and x.data[i - 2] == 0xCC):
        i -= 1
    if bytes(x.data[i:i + len(RUNFN_PROLOGUE)]) != RUNFN_PROLOGUE:
        raise PatchError("RunFunction: no prologue above the dispatch at 0x%08X"
                         % (x.off_to_va(off) or 0))
    va = x.off_to_va(i)
    if va is None:
        raise PatchError("RunFunction: entry is outside any section")
    return va


# The unique placeholder at the end anchors the command table.
COMMAND_SENTINEL = b"ADD NEW FUNCTIONS BEFORE THIS ONE!!!\x00"
COMMAND_STRIDE = 12  # const char *name; const char *shortName; u32 opcode
FIRST_OPCODE = 0x0100


def find_command_table(x):
    """Find the sentinel and walk back to the first command-table entry."""
    off = find_unique(x.data, COMMAND_SENTINEL, "command table sentinel")
    va = x.off_to_va(off)
    if va is None:
        raise PatchError("command table: the sentinel string is outside any section")
    # Require a valid short-name pointer and opcode to exclude code references.
    hits = [m.start() for m in re.finditer(re.escape(struct.pack("<I", va)), x.data)
            if x.va_to_off(struct.unpack_from("<I", x.data, m.start() + 4)[0]) is not None
            and FIRST_OPCODE <= struct.unpack_from("<I", x.data, m.start() + 8)[0] < 0x2000]
    if len(hits) != 1:
        raise PatchError("command table: %d sentinel entry candidate(s), expected 1" % len(hits))
    entry = hits[0]
    # Consecutive opcodes exclude the error-pointer array above the two command blocks.
    opcode = struct.unpack_from("<I", x.data, entry + 8)[0]
    while entry >= COMMAND_STRIDE:
        prev = entry - COMMAND_STRIDE
        name = struct.unpack_from("<I", x.data, prev)[0]
        prev_op = struct.unpack_from("<I", x.data, prev + 8)[0]
        if not name or x.va_to_off(name) is None:
            break
        if prev_op != opcode - 1 and not (opcode == 0x1000 and FIRST_OPCODE <= prev_op < 0x1000):
            break
        entry, opcode = prev, prev_op
    first = x.data[x.va_to_off(struct.unpack_from("<I", x.data, entry)[0]):][:6]
    if opcode != FIRST_OPCODE or not first.startswith(b"Begin\x00"):
        raise PatchError("command table: entry 0 is %r opcode 0x%04X, expected Begin 0x%04X"
                         % (bytes(first), opcode, FIRST_OPCODE))
    return x.off_to_va(entry)


def find_call_sites(x, target_va):
    """Every `call rel32` in .text that reaches target_va."""
    sec = text_section(x)
    body = bytes(x.data[sec.raw:sec.raw + sec.rsize])
    sites = []
    for m in re.finditer(b"\xE8", body):
        i = m.start()
        if i + 5 > len(body):
            continue
        site = sec.va + i
        if site + 5 + struct.unpack_from("<i", body, i + 1)[0] == target_va:
            sites.append(site)
    return sites


def widen_opcode_bounds(x, ceiling):
    """Raise the six instruction-length bounds from 0x11BD to `ceiling`."""
    sec = text_section(x)
    body = bytes(x.data[sec.raw:sec.raw + sec.rsize])
    found = []
    for rx, width, count in ((BOUND16, 2, BOUND16_COUNT), (BOUND32, 4, BOUND32_COUNT)):
        hits = list(rx.finditer(body))
        if len(hits) != count:
            raise PatchError("script-ext: %d of the %d-bit opcode bounds, expected %d"
                             % (len(hits), width * 8, count))
        for m in hits:
            found.append((sec.raw + m.end() - width, width))
    edits = []
    for off, width in sorted(found):
        struct.pack_into("<H" if width == 2 else "<I", x.data, off, ceiling)
        edits.append((off, width, "opcode bound 0x%08X: 0x%04X -> 0x%04X"
                      % (x.off_to_va(off), OPCODE_HI, ceiling)))
    return edits


@patch("script-ext")
def _script_ext(x, value, ctx):
    """Add script opcodes: widen the length bounds and hook Script::RunFunction."""
    target = ctx.get("hooks", {}).get("script_dispatch")
    if not target:
        raise PatchError("script-ext: needs `payload` first, with a script_dispatch hook in its "
                         "manifest")
    target = int(str(target), 16)
    ceiling = int(str(ctx.get("hooks", {}).get("opcode_ceil") or value or 0x4000), 0)
    if not OPCODE_HI < ceiling <= 0x7FFF:
        raise PatchError("script-ext: ceiling 0x%X must be above 0x%04X and below 0x8000 - the "
                         "bound comparisons are signed" % (ceiling, OPCODE_HI))

    edits = widen_opcode_bounds(x, ceiling)

    runfn = find_run_function(x)
    sites = find_call_sites(x, runfn)
    if len(sites) != 3:
        raise PatchError("script-ext: %d call site(s) for RunFunction 0x%08X, expected 3"
                         % (len(sites), runfn))
    for site in sites:
        was, off = x.patch_call(site, target)
        edits.append((off, 5, "RunFunction call 0x%08X: 0x%08X -> 0x%08X" % (site, was, target)))
    return edits


# `mov [esp+0x1c],bl` then the restamp fallback's landing instruction. `mov eax,[ebp+0x4DC]`
# occurs three times in the image; with the store above it the site is unique.
REF_LOAD_SIG = bytes([
    0x88, 0x5C, 0x24, 0x1C,              # mov [esp+0x1c], bl
    0x8B, 0x85, 0xDC, 0x04, 0x00, 0x00,  # mov eax, [ebp+0x4DC]   <- the six bytes replaced
    0x8B, 0x74, 0x24, 0x14,              # mov esi, [esp+0x14]
])
REF_LOAD_OFF = 4

# The loader's own skip tail, where a dropped reference rejoins: it consumes the reference's
# remaining subrecords and returns to the per-reference loop.
REF_SKIP_SIG = re.compile(rb"\x8a\x44\x24\x13\x84\xc0\x74.\x8d\x4c\x24\x2c\x51\x8b\xcf", re.S)

# `mov eax,[esp+0x14]; sar eax,0x18` - the mod index, extracted arithmetically, so 0x80 and above
# come out negative and never resolve. `sar eax,0x18` occurs once in the whole image.
REF_INDEX_SIG = bytes([0x8B, 0x44, 0x24, 0x14, 0xC1, 0xF8, 0x18, 0x85, 0xC0, 0x74])
REF_INDEX_OFF = 5  # the /7 sar modrm byte; /5 is shr

# Script::ReplaceGlobalsInData scans compiled bytecode before replacing identifiers. Two
# operand forms advance the scan cursor incorrectly: the fixed-width form skips one byte too
# many, while the length-prefixed form skips one too few. Absolute table addresses vary with
# the image, so leave them wildcarded and anchor the complete dispatch tail.
MCP97_SCAN_SIG = re.compile(
    rb"\x0f\xb6\x92....\xff\x24\x95....\x83\xc1\x03\xeb."
    rb"\x0f\xbe\x40\x01\x03\xc8\xeb.\x8b\xe8\x41\x85\xed",
    re.S,
)
MCP97_FIXED_IMM = 16
MCP97_LENGTH_CASE = 19
MCP97_LENGTH_REPLACED = 6

# Script data is allocated from the SCDT chunk length on both initial load and reload. The
# reader can touch one dword beyond that data, so MCP pads both allocations by four bytes.
MCP154_LOAD_SIG = re.compile(
    rb"\x2dACDT\x74.\x83\xe8\x12\x75.(?P<site>\x8b\x87\x40\x02\x00\x00)"
    rb"\x68....\x68....\x50\x6a\x01\xe8",
    re.S,
)
MCP154_RELOAD_SIG = re.compile(
    rb"\x3dSCDT\x75.\x8b\x45\x58\x85\xc0(?P<site>\x8b\xbe\x40\x02\x00\x00)"
    rb"\x74.\x50\xe8....\x83\xc4\x04\x68",
    re.S,
)
MCP154_REPLACED = 6

# The ACTN save subrecord is loaded through this sole setter. When the object has no action
# state yet it allocates one, then both paths store the serialized flags at +8. MCP keeps bit
# zero set so an object cannot remain inactive after the script which triggered it is removed.
MCP102_ACTN_SIG = re.compile(
    rb"\x8b\x41\x44\x85\xc0\x74\x0c\x83\x38\x09\x74\x16"
    rb"\x8b\x40\x04\x85\xc0\x75\xf4\xe8...."
    rb"(?P<missing>\x8b\x54\x24\x04\x89\x50\x08\xc2\x04\x00)"
    rb"\x8b\x4c\x24\x04\x89\x48\x08\xc2\x04\x00",
    re.S,
)
MCP102_FOUND_JUMP = 11
MCP102_STORE = bytes.fromhex("8b54240483ca01895008c20400")

# MenuLoading's progress callback updates the fill value, then calls the menu update and redraw
# routines whenever the float changes. MCP throttles those last two calls to one per 50 ms.
MCP140_REDRAW_SIG = re.compile(
    rb"\xd9\x44\x24\x18\x8b\xd8\xe8....\xd9\x03\x8b\xe8\xe8...."
    rb"\x3b\xe8\x5d\x5b\x74.\x8b\x4c\x24\x10\x33\xc0\x66\xa1...."
    rb"\x6a\x02\x8b\xd1\x52\x89\x4c\x24\x10\x8b\xcf\x50\xe8...."
    rb"\x8b\xce(?P<site>\xe8....)\x6a\x01\x8b\xce\xe8....\x5f\xb0\x01",
    re.S,
)
# The texture-create call to the size function, just before the pitch computation that
# special-cases DXT1 (0xC) and DXT3 (0xE).
DXT5_SIZE_SIG = re.compile(
    rb"\x8b\xf8\x8b\x44\x24\x20\x50\x8b\xc7\x8b\xce(?P<site>\xe8....)\x8b\xe8\x83\xc4\x04"
    rb"\x8b\xc7\xe8....\x8b\xd8\x0f\xaf\xde\xc1\xeb\x03\x83\xff\x0c",
    re.S,
)
MCP140_STATUS_SIG = re.compile(
    rb"\x85\xf6\x74.(?P<update>\x8b\xce)\xe8....\x33\xd2\x66\x8b\x15...."
    rb"\x8b\xce\x52\xe8....\x85\xc0\x74.\x8b\x4c\x24\x08\x51\x8b\xc8\xe8...."
    rb"(?P<mode>\x6a\x01)\x8b\xce\xe8",
    re.S,
)


def find_dxt5_size(x):
    """The texture-create call to the texture-size function."""
    hits = list(DXT5_SIZE_SIG.finditer(bytes(x.data)))
    if len(hits) != 1:
        raise PatchError("dxt5-size: %d texture size call(s), expected 1" % len(hits))
    va = x.off_to_va(hits[0].start("site"))
    if va is None:
        raise PatchError("dxt5-size: texture size call is outside any section")
    return va


def find_ref_load(x):
    """The restamp fallback the three failed resolutions share with the legitimate path."""
    off = find_unique(x.data, REF_LOAD_SIG, "restamp fallback") + REF_LOAD_OFF
    va = x.off_to_va(off)
    if va is None:
        raise PatchError("mcp-1: the restamp fallback is outside any section")
    return va


def find_ref_skip(x):
    """The skip tail a dropped reference rejoins."""
    hits = [m.start() for m in REF_SKIP_SIG.finditer(bytes(x.data))]
    if len(hits) != 1:
        raise PatchError("mcp-1: %d skip tail(s), expected 1" % len(hits))
    return x.off_to_va(hits[0])


def find_mcp97_scan(x):
    """Find the length-prefixed operand case in ReplaceGlobalsInData's bytecode scan."""
    hits = list(MCP97_SCAN_SIG.finditer(bytes(x.data)))
    if len(hits) != 1:
        raise PatchError("mcp-97: %d bytecode scan(s), expected 1" % len(hits))
    va = x.off_to_va(hits[0].start() + MCP97_LENGTH_CASE)
    if va is None:
        raise PatchError("mcp-97: bytecode scan is outside any section")
    return va


def _find_mcp154_site(x, signature, label):
    hits = list(signature.finditer(bytes(x.data)))
    if len(hits) != 1:
        raise PatchError("mcp-154: %d %s allocation site(s), expected 1" % (len(hits), label))
    va = x.off_to_va(hits[0].start("site"))
    if va is None:
        raise PatchError("mcp-154: %s allocation is outside any section" % label)
    return va


def find_mcp154_load(x):
    """Find the initial script-data allocation size load."""
    return _find_mcp154_site(x, MCP154_LOAD_SIG, "initial")


def find_mcp154_reload(x):
    """Find the reloaded script-data allocation size load."""
    return _find_mcp154_site(x, MCP154_RELOAD_SIG, "reload")


def find_mcp102_actn(x):
    """Find the ACTN flag setter used by the save-reference loader."""
    hits = list(MCP102_ACTN_SIG.finditer(bytes(x.data)))
    if len(hits) != 1:
        raise PatchError("mcp-102: %d ACTN setter(s), expected 1" % len(hits))
    va = x.off_to_va(hits[0].start())
    if va is None:
        raise PatchError("mcp-102: ACTN setter is outside any section")
    return va


def find_mcp140_redraw(x):
    """Find MenuLoading's update call immediately before its unconditional redraw."""
    hits = list(MCP140_REDRAW_SIG.finditer(bytes(x.data)))
    if len(hits) != 1:
        raise PatchError("mcp-140: %d loading redraw site(s), expected 1" % len(hits))
    va = x.off_to_va(hits[0].start("site"))
    if va is None:
        raise PatchError("mcp-140: loading redraw is outside any section")
    return va


def find_mcp140_status(x):
    """Find MenuLoading's status-label update and redraw mode."""
    hits = list(MCP140_STATUS_SIG.finditer(bytes(x.data)))
    if len(hits) != 1:
        raise PatchError("mcp-140: %d loading status site(s), expected 1" % len(hits))
    update = x.off_to_va(hits[0].start("update"))
    mode = x.off_to_va(hits[0].start("mode") + 1)
    if update is None or mode is None:
        raise PatchError("mcp-140: loading status is outside any section")
    return update, mode


AUTOSAVE_NAME_SIG = b"autosave\x00\x00\x00\x00%d %s %s%s"

PREFERENCES_LOAD_SIG = re.compile(
    rb"\x83\xec\x5c\xa1....\x53\x55\x56\x8b\xe9\x8b\x48\x4c\x57"
    rb"(?P<site>\xe8....)\x33\xc0\xb9\x0c\x00\x00\x00\x8d\x7c\x24\x3c\xf3\xab\xb0\xfa",
    re.S,
)
CONTROLS_COPY_SIG = re.compile(
    rb"\xb9\x13\x00\x00\x00\x8d\x74\x24\x30\xbf(?P<table>....)"
    rb"\xf3\xa5\x66\xa5\x5f\x5e\x83\xc4\x78\xc3",
    re.S,
)


def find_preferences_load(x):
    """The call that loads controls.dat before player options are applied."""
    hits = list(PREFERENCES_LOAD_SIG.finditer(bytes(x.data)))
    if len(hits) != 1:
        raise PatchError("build-preferences: %d controls load call(s), expected 1" % len(hits))
    va = x.off_to_va(hits[0].start("site"))
    if va is None:
        raise PatchError("build-preferences: controls load call is outside any section")
    return va


def find_controls_table(x):
    """The 39 two-byte default action bindings overwritten by controls.dat."""
    hits = list(CONTROLS_COPY_SIG.finditer(bytes(x.data)))
    if len(hits) != 1:
        raise PatchError("build-preferences: %d controls table copies, expected 1" % len(hits))
    return struct.unpack("<I", hits[0].group("table"))[0]


def find_autosave_calls(x):
    """Find the save routine and the three callers that pass the autosave name buffer."""
    name_off = find_unique(x.data, AUTOSAVE_NAME_SIG, "autosave name")
    name_va = x.off_to_va(name_off)
    if name_va is None:
        raise PatchError("rotating-autosaves: autosave name is outside any section")

    # The lazy [SaveNames] lookup passes one writable buffer and the same autosave string as
    # both key and default. Extract the buffer address rather than pinning its .bss VA.
    init_sig = re.compile(
        rb"\x68(?P<buf>....)\x68" + re.escape(struct.pack("<I", name_va))
        + rb"\x68" + re.escape(struct.pack("<I", name_va)) + rb"\x68....\xe8",
        re.S,
    )
    text = text_section(x)
    body = bytes(x.data[text.raw:text.raw + text.rsize])
    init = list(init_sig.finditer(body))
    if len(init) != 1:
        raise PatchError("rotating-autosaves: %d autosave name initializers, expected 1"
                         % len(init))
    buf_va = struct.unpack("<I", init[0].group("buf"))[0]

    call_sig = re.compile(
        re.escape(b"\x68" + struct.pack("<I", buf_va)) * 2 + rb"\xe8(?P<rel>....)", re.S)
    calls = []
    targets = set()
    for match in call_sig.finditer(body):
        site = text.va + match.start() + 10
        rel = struct.unpack("<i", match.group("rel"))[0]
        calls.append(site)
        targets.add(site + 5 + rel)
    if len(calls) != 3 or len(targets) != 1:
        raise PatchError("rotating-autosaves: found %d call(s) to %d save target(s), expected 3/1"
                         % (len(calls), len(targets)))
    return targets.pop(), calls


def find_save_this_ptr(x):
    """The global whose pointee owns SaveGame, loaded before each autosave call."""
    _save_game, calls = find_autosave_calls(x)
    pointers = set()
    for site in calls:
        off = x.va_to_off(site)
        prefix = bytes(x.data[off - 18:off - 10])
        if len(prefix) != 8 or prefix[:2] != b"\x8b\x0d" or prefix[6:] != b"\x8b\x09":
            raise PatchError("rotating-autosaves: unexpected save owner load at 0x%08X" % site)
        pointers.add(struct.unpack("<I", prefix[2:6])[0])
    if len(pointers) != 1:
        raise PatchError("rotating-autosaves: found %d save owner pointers, expected 1"
                         % len(pointers))
    return pointers.pop()


@patch("build-preferences")
def _build_preferences(x, value, ctx):
    """Apply profile-selected player preferences after stored Xbox options load."""
    target = ctx.get("hooks", {}).get("preferences_load")
    if not target:
        raise PatchError("build-preferences: needs `payload` first, with a preferences_load "
                         "hook in its manifest")
    target = int(str(target), 16)
    site = find_preferences_load(x)
    expected = tes3x_inject.call_target(x, site)
    was, off = x.patch_call(site, target)
    if was != expected:
        raise PatchError("build-preferences: call 0x%08X targets 0x%08X, expected 0x%08X"
                         % (site, was, expected))
    return [(off, 5, "controls load 0x%08X: 0x%08X -> 0x%08X"
             % (site, was, target))]


@patch("rotating-autosaves")
def _rotating_autosaves(x, value, ctx):
    """Rotate automatic saves through INI-configurable slots."""
    target = ctx.get("hooks", {}).get("autosave")
    if not target:
        raise PatchError("rotating-autosaves: needs `payload` first, with an autosave hook in "
                         "its manifest")
    target = int(str(target), 16)
    save_game, sites = find_autosave_calls(x)
    edits = []
    for site in sites:
        was, off = x.patch_call(site, target)
        if was != save_game:
            raise PatchError("rotating-autosaves: call 0x%08X targets 0x%08X, expected 0x%08X"
                             % (site, was, save_game))
        edits.append((off, 5, "autosave call 0x%08X: 0x%08X -> 0x%08X"
                      % (site, was, target)))
    return edits


@patch("mcp-1")
def _mcp_1(x, value, ctx):
    """Stop an unresolvable reference being restamped as created at runtime."""
    target = ctx.get("hooks", {}).get("ref_load")
    if not target:
        raise PatchError("mcp-1: needs `payload` first, with a ref_load hook in its manifest")
    target = int(str(target), 16)
    site = find_ref_load(x)
    off = x.va_to_off(site)
    # jmp rel32 plus one pad; the replaced instruction is six bytes and the payload repeats it.
    x.data[off:off + 6] = b"\xe9" + struct.pack("<i", target - (site + 5)) + b"\x90"
    edits = [(off, 6, "restamp fallback 0x%08X -> 0x%08X" % (site, target))]

    shift = find_unique(x.data, REF_INDEX_SIG, "mod index shift") + REF_INDEX_OFF
    x.data[shift] = 0xE8
    edits.append((shift, 1, "mod index 0x%08X: sar -> shr" % x.off_to_va(shift - 1)))
    return edits


@patch("mcp-97")
def _mcp_97(x, value, ctx):
    """Advance the script parser correctly while initializing saved data."""
    target = ctx.get("hooks", {}).get("mcp97_scan")
    if not target:
        raise PatchError("mcp-97: needs `payload` first, with an mcp97_scan hook in its "
                         "manifest")
    target = int(str(target), 16)
    site = find_mcp97_scan(x)
    off = x.va_to_off(site)

    fixed = off - MCP97_LENGTH_CASE + MCP97_FIXED_IMM
    if x.data[fixed] != 3:
        raise PatchError("mcp-97: fixed-width advance is %d, expected 3" % x.data[fixed])
    x.data[fixed] = 2

    expected = b"\x0f\xbe\x40\x01\x03\xc8"
    if bytes(x.data[off:off + MCP97_LENGTH_REPLACED]) != expected:
        raise PatchError("mcp-97: length-prefixed case does not match expected instructions")
    x.data[off:off + MCP97_LENGTH_REPLACED] = (
        b"\xe9" + struct.pack("<i", target - (site + 5)) + b"\x90"
    )
    return [
        (fixed, 1, "fixed-width cursor 0x%08X: 3 -> 2" % x.off_to_va(fixed)),
        (off, MCP97_LENGTH_REPLACED,
         "length-prefixed cursor 0x%08X -> 0x%08X" % (site, target)),
    ]


@patch("mcp-154")
def _mcp_154(x, value, ctx):
    """Pad compiled script-data allocations to keep dword reads in bounds."""
    hooks = ctx.get("hooks", {})
    targets = (hooks.get("mcp154_load"), hooks.get("mcp154_reload"))
    if not all(targets):
        raise PatchError("mcp-154: needs `payload` first, with mcp154_load and mcp154_reload "
                         "hooks in its manifest")
    sites = (find_mcp154_load(x), find_mcp154_reload(x))
    expected = (b"\x8b\x87\x40\x02\x00\x00", b"\x8b\xbe\x40\x02\x00\x00")
    labels = ("initial", "reload")
    edits = []
    for site, target, want, label in zip(sites, targets, expected, labels):
        target = int(str(target), 16)
        off = x.va_to_off(site)
        if bytes(x.data[off:off + MCP154_REPLACED]) != want:
            raise PatchError("mcp-154: %s allocation does not match expected instruction" % label)
        x.data[off:off + MCP154_REPLACED] = (
            b"\xe9" + struct.pack("<i", target - (site + 5)) + b"\x90"
        )
        edits.append((off, MCP154_REPLACED,
                      "%s allocation 0x%08X -> 0x%08X" % (label, site, target)))
    return edits


@patch("mcp-140")
def _mcp_140(x, value, ctx):
    """Throttle loading-screen redraws to one every 50 milliseconds."""
    target = ctx.get("hooks", {}).get("mcp140_redraw")
    if not target:
        raise PatchError("mcp-140: needs `payload` first, with an mcp140_redraw hook in its "
                         "manifest")
    target = int(str(target), 16)
    site = find_mcp140_redraw(x)
    status_update, status_mode = find_mcp140_status(x)
    off = x.va_to_off(site)
    status_off = x.va_to_off(status_update)
    mode_off = x.va_to_off(status_mode)
    # The hook owns both following calls and rejoins at the existing true/false cleanup tails.
    x.data[off:off + 5] = b"\xe9" + struct.pack("<i", target - (site + 5))
    # Match MCP's status-label path: skip its redundant update and use redraw mode zero.
    x.data[status_off:status_off + 2] = b"\xeb\x05"
    x.data[mode_off] = 0
    return [
        (status_off, 2, "loading status update 0x%08X: skipped" % status_update),
        (mode_off, 1, "loading status redraw mode 0x%08X: 1 -> 0" % status_mode),
        (off, 5, "loading progress redraw 0x%08X -> 0x%08X" % (site, target)),
    ]


@patch("dxt5-size")
def _dxt5_size(x, value, ctx):
    """Size DXT5 textures as DXT3 instead of with a negative length."""
    target = ctx.get("hooks", {}).get("dxt5_size")
    if not target:
        raise PatchError("dxt5-size: needs `payload` first, with a dxt5_size hook in its manifest")
    site = find_dxt5_size(x)
    was, off = x.patch_call(site, int(str(target), 16))
    return [(off, 5, "texture size call 0x%08X: 0x%08X -> %s" % (site, was, target))]


@patch("mcp-102")
def _mcp_102(x, value, ctx):
    """Reactivate script-triggered objects after their script mod is removed."""
    site = find_mcp102_actn(x)
    off = x.va_to_off(site)
    match = MCP102_ACTN_SIG.match(bytes(x.data), off)
    store = match.start("missing")

    # Both the existing-state path and the newly-allocated path now share one store of
    # `serialized_flags | 1`; seven trailing padding bytes keep the function boundary fixed.
    x.data[off + MCP102_FOUND_JUMP] = store - (off + MCP102_FOUND_JUMP + 1)
    replaced = match.end() - store
    x.data[store:match.end()] = MCP102_STORE + b"\x90" * (replaced - len(MCP102_STORE))
    return [
        (off + MCP102_FOUND_JUMP, 1,
         "ACTN existing-state path 0x%08X -> shared store" % (site + 10)),
        (store, replaced,
         "ACTN flags 0x%08X: force active bit" % x.off_to_va(store)),
    ]


CONSOLE_GATE_SIG = bytes([
    0x6A, 0x02,              # push 2            ; mode
    0x6A, 0x19,              # push 0x19         ; the console action
    0xE8,                    # call rel32        ; the action check this replaces
])

# Game::Update is the 43-call once-per-frame function containing the console gate.
DIAGNOSTICS_UPDATE_SIG = bytes([
    0x55, 0x8B, 0xEC,             # push ebp; mov ebp,esp
    0x83, 0xE4, 0xF8,             # and esp,-8
    0x83, 0xEC, 0x58,             # sub esp,0x58
    0x53, 0x55, 0x56, 0x57,       # save registers
    0x8B, 0xE9,                   # mov ebp,ecx
])


def find_diagnostics_update(x):
    """The once-per-frame update function, found by its checked prologue."""
    off = find_unique(x.data, DIAGNOSTICS_UPDATE_SIG, "Game::Update")
    va = x.off_to_va(off)
    if va is None:
        raise PatchError("diagnostics: Game::Update is outside any section")
    return va


@patch("diagnostics")
def _diagnostics(x, value, ctx):
    """Enable INI-controlled crash records, snapshots and a hang watchdog."""
    hooks = ctx.get("hooks", {})
    target = hooks.get("diagnostics_update")
    flag = hooks.get("diagnostics_flag")
    if not target or not flag:
        raise PatchError("diagnostics: needs `payload` first, with diagnostics_update and flag in "
                         "its manifest")
    target = int(str(target), 16)
    flag = int(str(flag), 16)
    flag_off = x.va_to_off(flag)
    if flag_off is None:
        raise PatchError("diagnostics: installed flag is outside the payload section")
    struct.pack_into("<I", x.data, flag_off, 1)
    update = find_diagnostics_update(x)
    sites = find_call_sites(x, update)
    if len(sites) != 1:
        raise PatchError("diagnostics: %d call site(s) for Game::Update 0x%08X, expected 1"
                         % (len(sites), update))
    site = sites[0]
    was, off = x.patch_call(site, target)
    return [(None, 4, "diagnostics installed flag at 0x%08X" % flag),
            (off, 5, "Game::Update call 0x%08X: 0x%08X -> 0x%08X"
             % (site, was, target))]


PROFILE_LIST_SITES = 8


@patch("profile")
def _profile(x, value, ctx):
    """Time listed functions with RDTSC at every direct call site."""
    hooks = ctx.get("hooks", {})
    table, stubs = hooks.get("prof_target"), hooks.get("prof_stubs")
    if not table or not stubs:
        raise PatchError("profile: needs `payload` first, built with tes3xprof.c in SRCS")
    table_off = x.va_to_off(int(str(table), 16))
    stubs_off = x.va_to_off(int(str(stubs), 16))
    if table_off is None or stubs_off is None:
        raise PatchError("profile: the payload tables are outside the injected section")
    count = int(str(hooks.get("prof_count", 0)), 0)

    targets = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        # `@name` names a payload hook, so an installed hook can be timed in its own right.
        if item.startswith("@"):
            if item[1:] not in hooks:
                raise PatchError("profile: no %r in the payload manifest" % item[1:])
            targets.append((item, int(str(hooks[item[1:]]), 16)))
        else:
            targets.append((item, int(item, 0)))
    if not targets:
        raise PatchError("profile: no targets")
    if len(targets) > count:
        raise PatchError("profile: %d target(s), the payload carries %d slot(s)"
                         % (len(targets), count))

    seen = set()
    edits = []
    for k, (item, va) in enumerate(targets):
        if va in seen:
            raise PatchError("profile: 0x%08X listed twice" % va)
        seen.add(va)
        # Virtual dispatch is invisible to call-site hooking; say so rather than time nothing.
        label = "%s " % item if item.startswith("@") else ""
        sites = find_call_sites(x, va)
        if not sites:
            raise PatchError("profile: no direct call site reaches %s0x%08X" % (label, va))
        stub = struct.unpack_from("<I", x.data, stubs_off + 4 * k)[0]
        struct.pack_into("<I", x.data, table_off + 4 * k, va)
        edits.append((None, 4, "slot %d = %s0x%08X, stub 0x%08X, %d call site(s)"
                      % (k, label, va, stub, len(sites))))
        for site in sites:
            was, off = x.patch_call(site, stub)
            edits.append((off, 5, "  call 0x%08X: 0x%08X -> slot %d" % (site, was, k)
                          if len(sites) <= PROFILE_LIST_SITES else None))
    return edits


def find_console_gate(x):
    """The one `call` that gates Console::Toggle, found by its push/push/call shape."""
    text = text_section(x)
    blob = bytes(x.data[text.raw:text.raw + text.rsize])
    hits = [m.start() for m in re.finditer(re.escape(CONSOLE_GATE_SIG), blob)]
    if len(hits) != 1:
        raise PatchError("console: %d site(s) matching `push 2; push 0x19; call`, expected 1"
                         % len(hits))
    return text.va + hits[0] + 4


# The on-screen keyboard's key and space handlers cap its text at 31 characters with a
# `cmp dword [esp+N], 0x1F`. A second keyboard has identical handlers, so each signature is
# anchored on the text-entry id global only this keyboard's handler reads beforehand.
VK_KEY_LIMIT_SIG = re.compile(
    re.escape(bytes.fromhex("668b0d68c73d00b801000000505051 8bce e8".replace(" ", "")))
    + b".{4}" + re.escape(bytes.fromhex("837c24241f")), re.S)
VK_SPACE_LIMIT_SIG = re.compile(
    re.escape(bytes.fromhex("668b155cc73d00")) + b".{67}"
    + re.escape(bytes.fromhex("837c242c1f")), re.S)


def find_vk_limit(x, sig, what):
    """The 5-byte length compare at the end of a keyboard-handler signature."""
    text = text_section(x)
    blob = bytes(x.data[text.raw:text.raw + text.rsize])
    hits = [m.end() - 5 for m in sig.finditer(blob)]
    if len(hits) != 1:
        raise PatchError("console: %d keyboard %s length check(s), expected 1" % (len(hits), what))
    return text.va + hits[0]


# The console's printf: find the console menu, vsprintf into a 0x104-byte buffer, append the line.
CONSOLE_PRINT_SIG = bytes([
    0x33, 0xC0,                                # xor eax,eax
    0x66, 0xA1,                                # mov ax, [console menu id]
]) + b"\x6C\x81\x3D\x00" + bytes([
    0x81, 0xEC, 0x04, 0x01, 0x00, 0x00,        # sub esp,0x104
    0x50,                                      # push eax
])
CONSOLE_PRINT_VSPRINTF = 0x30                  # offset of its `call vsprintf`


def find_console_print(x):
    off = find_unique(x.data, CONSOLE_PRINT_SIG, "console printf")
    return x.off_to_va(off)


@patch("console")
def _console(x, value, ctx):
    """Make the in-game console reachable, by replacing its input gate."""
    target = ctx.get("hooks", {}).get("console_gate")
    if not target:
        raise PatchError("console: needs `payload` first, with a console_gate hook in its "
                         "manifest")
    target = int(str(target), 16)
    site = find_console_gate(x)
    was, off = x.patch_call(site, target)
    edits = [(off, 5, "console gate 0x%08X: 0x%08X -> 0x%08X" % (site, was, target))]
    for what, sig, hook in (("key", VK_KEY_LIMIT_SIG, "console_vk_key"),
                            ("space", VK_SPACE_LIMIT_SIG, "console_vk_space")):
        stub = ctx["hooks"].get(hook)
        if not stub:
            continue
        stub = int(str(stub), 16)
        site = find_vk_limit(x, sig, what)
        off = x.va_to_off(site)
        x.data[off:off + 5] = b"\xe8" + struct.pack("<i", stub - (site + 5))
        edits.append((off, 5, "keyboard %s length check 0x%08X -> 0x%08X" % (what, site, stub)))
    printer = ctx["hooks"].get("console_print")
    if printer:
        printer = int(str(printer), 16)
        target = find_console_print(x)
        sites = find_call_sites(x, target)
        for site in sites:
            was, off = x.patch_call(site, printer)
            edits.append((off, 5, None))
        edits.append((None, 0, "console printf 0x%08X: %d call site(s) -> 0x%08X"
                      % (target, len(sites), printer)))
    return edits


# Content-located engine addresses, shared with the payload build so the two cannot disagree.
LOCATORS = {
    "run-function": find_run_function,
    "command-table": find_command_table,
    "ref-load": find_ref_load,
    "ref-skip": find_ref_skip,
    "mcp-97-scan": find_mcp97_scan,
    "mcp-154-load": find_mcp154_load,
    "mcp-154-reload": find_mcp154_reload,
    "mcp-140-redraw": find_mcp140_redraw,
    "mcp-102-actn": find_mcp102_actn,
    "dxt5-size": find_dxt5_size,
    "save-game": lambda image: find_autosave_calls(image)[0],
    "save-this-ptr": find_save_this_ptr,
    "preferences-load": find_preferences_load,
    "controls-table": find_controls_table,
    "diagnostics-update": find_diagnostics_update,
    "console-print": find_console_print,
}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("xbe", nargs="?")
    ap.add_argument("--out")
    ap.add_argument("--apply", action="append", default=[], metavar="NAME[=VALUE]")
    ap.add_argument("--section", default=".tes3xhk")
    ap.add_argument("--list", action="store_true", help="list available patches and exit")
    ap.add_argument("--locate", choices=list(LOCATORS),
                    help="print a content-located engine address and exit")
    a = ap.parse_args()

    if a.list or not a.xbe:
        print("\n  available patches:\n")
        for entry in registry.PATCHES:
            name = entry["name"]
            _fn, takes, help_text = PATCHES[name]
            spec = "%s=%s" % (name, takes) if takes else name
            print("    %-26s %s" % (spec, help_text))
        print()
        return

    raw = open(a.xbe, "rb").read()
    x = tes3x_inject.Xbe(raw)
    if a.locate:
        print("0x%08X" % LOCATORS[a.locate](x))
        return
    cert = struct.unpack_from("<I", x.data, 0x118)[0]
    title = struct.unpack_from("<I", x.data, cert - x.base + 8)[0]
    print("%s: %d bytes, title 0x%08X, %d sections" % (a.xbe, len(raw), title, len(x.sections)))
    if title != TITLE_ID:
        raise SystemExit("not Morrowind (title 0x%08X, want 0x%08X)" % (title, TITLE_ID))
    if any(s.name == a.section for s in x.sections):
        raise SystemExit("already carries a %s section; patch a clean XBE instead" % a.section)

    ctx = {"section": a.section}
    touched = []
    applied = []
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
                # A patch with hundreds of identical edits reports them as one line.
                if label:
                    print("    %s" % label)
                if off is not None:
                    touched.append((off, length))
            applied.append(name)
        except PatchError as exc:
            raise SystemExit("  FAILED: %s" % exc)

    mask_va = ctx.get("hooks", {}).get("patch_mask")
    if mask_va:
        mask_va = int(str(mask_va), 16)
        mask_off = x.va_to_off(mask_va)
        if mask_off is None:
            raise SystemExit("payload patch mask is outside the injected section")
        mask = 0
        for name in applied:
            mask |= PATCH_BITS.get(name, 0)
        struct.pack_into("<I", x.data, mask_off, mask)
        print("\n  payload patch mask 0x%08X at 0x%08X" % (mask, mask_va))

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
