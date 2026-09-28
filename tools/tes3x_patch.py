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

# Script::Decode fetches a word from Script::SCDT using the global instruction pointer.
# The captured globals are also the state a legacy-MWSE fixup shim must advance.
SCRIPT_DECODE_SIG = re.compile(
    rb"\x83\xec\x24\x53\x55\x56\x8b\xd9\x57\xb9....\xe8...."
    rb"\xb9....\x89\x44\x24\x10\xe8...."
    rb"\x8b\x2d(?P<ip>....)\x8b\xd0\x8b\x43\x58\x0f\xbf\x04\x28"
    rb"\x33\xf6\x83\xc5\x02\x3d\x26\x01\x00\x00"
    rb"\xa3(?P<opcode>....)\x89\x2d(?P=ip)",
    re.S,
)

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


def find_script_decode_state(x):
    """Find Script::Decode and its instruction-pointer/opcode globals."""
    hits = list(SCRIPT_DECODE_SIG.finditer(bytes(x.data)))
    if len(hits) != 1:
        raise PatchError("Script::Decode: %d signature match(es), expected 1" % len(hits))
    match = hits[0]
    va = x.off_to_va(match.start())
    if va is None:
        raise PatchError("Script::Decode: entry is outside any section")
    ip = struct.unpack("<I", match.group("ip"))[0]
    opcode = struct.unpack("<I", match.group("opcode"))[0]
    def in_image(address):
        return any(s.va <= address < s.va + max(getattr(s, "vsize", 0), s.rsize)
                   for s in x.sections)
    if not in_image(ip) or not in_image(opcode):
        raise PatchError("Script::Decode: captured state is outside the image")
    return va, ip, opcode


def find_script_fixup_call(x, decode_va, opcode_va):
    """Find the fixup-only call to Script::Decode."""
    sites = []
    for site in find_call_sites(x, decode_va):
        off = x.va_to_off(site)
        if (bytes(x.data[off - 4:off]) == b"\x6a\x01\x8b\xcb"
                and bytes(x.data[off + 5:off + 10])
                == b"\xa1" + struct.pack("<I", opcode_va)):
            sites.append(site)
    if len(sites) != 1:
        raise PatchError("mwse-legacy: %d fixup decoder call(s), expected 1" % len(sites))
    return sites[0]


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


@patch("mwse-legacy")
def _mwse_legacy(x, value, ctx):
    """Interpret legacy MWSE 0.9.4 bytecode embedded in compiled scripts."""
    hooks = ctx.get("hooks", {})
    target, dispatch = hooks.get("mwse_fixup"), hooks.get("script_dispatch")
    if not target or not dispatch:
        raise PatchError("mwse-legacy: needs `payload` first, with mwse_fixup and "
                         "script_dispatch hooks in its manifest")
    target = int(str(target), 16)
    dispatch = int(str(dispatch), 16)
    if len(find_call_sites(x, dispatch)) != 3:
        raise PatchError("mwse-legacy: requires script-ext to be applied first")
    decode, _ip, opcode = find_script_decode_state(x)
    site = find_script_fixup_call(x, decode, opcode)
    was, off = x.patch_call(site, target)
    return [(off, 5, "script fixup decoder 0x%08X: 0x%08X -> 0x%08X" % (site, was, target))]


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

# The texture-create call to the size function, just before the pitch computation that
# special-cases DXT1 (0xC) and DXT3 (0xE).
DXT5_SIZE_SIG = re.compile(
    rb"\x8b\xf8\x8b\x44\x24\x20\x50\x8b\xc7\x8b\xce(?P<site>\xe8....)\x8b\xe8\x83\xc4\x04"
    rb"\x8b\xc7\xe8....\x8b\xd8\x0f\xaf\xde\xc1\xeb\x03\x83\xff\x0c",
    re.S,
)


# The arena constructor's size argument, `mov ebx, 0xF80000; mov esi, eax; call`, behind the
# null check of the heap object just allocated.
ARENA_SIZE_SIG = b"\x85\xc0\x74\x0e\xbb\x00\x00\xf8\x00\x8b\xf0\xe8"
ARENA_SIZE_OFF = 4


def find_arena_size(x):
    """The `mov ebx, 0xF80000` that sizes the video-memory arena."""
    off = find_unique(bytes(x.data), ARENA_SIZE_SIG, "video-arena") + ARENA_SIZE_OFF
    va = x.off_to_va(off)
    if va is None:
        raise PatchError("video-arena: the arena size is outside any section")
    return va


def find_dxt5_size(x):
    """The texture-create call to the texture-size function."""
    hits = list(DXT5_SIZE_SIG.finditer(bytes(x.data)))
    if len(hits) != 1:
        raise PatchError("dxt5-size: %d texture size call(s), expected 1" % len(hits))
    va = x.off_to_va(hits[0].start("site"))
    if va is None:
        raise PatchError("dxt5-size: texture size call is outside any section")
    return va


# The per-file loop's load-one-record call, whose failure sets the result to 0.
LEAN_RECORD_SIG = re.compile(rb"\x50\x51\x53\x8b\xcd(?P<site>\xe8....)\x85\xc0\x75\x04\x89\x44\x24\x1c",
                             re.S)
# The load-one-record function reads the record tag first.
LEAN_TAG_SIG = re.compile(rb"\x8b\xcf\x8b\xf0(?P<call>\xe8....)\x8b\xd8\x81\xfb", re.S)
# Startup stores the launch-info buffer's address after logging "Getting Launch Info".
LEAN_LAUNCH_SIG = re.compile(rb"\xc7\x84\x24\x14\x0d\x00\x00\xff\xff\xff\xff\xa3(?P<ptr>....)",
                             re.S)
# Startup stores the two [Debug] No Reboot flags in adjacent bytes.
LEAN_NO_REBOOT_SIG = re.compile(rb"\x0f\x95\xc1\x68....\x68....\x88\x0d(?P<new>....)\xe8....\x8b\x0d"
                                rb"....\x83\xc4\x20\x85\xc0\x0f\x95\xc2\x88\x15(?P<load>....)", re.S)
# The [PreLoad] Cell 0 lookup, whose result is stored and tested before the main menu.
LEAN_PRELOAD_SIG = re.compile(rb"\x68....\x68....\xe8....\x8b\x15....\x8b\x0a\x83\xc4\x18\x56"
                              rb"(?P<site>\xe8....)\x8b\x0d....\x89\x81\x08\x03\x00\x00", re.S)
# New Game's handler creates the player when there is none, before it decides to relaunch.
LEAN_PLAYER_SIG = re.compile(rb"\x85\xc0\x75\x0b\x8b\x0d....(?P<site>\xe8....)\x8b\x0d....\xd9\x05",
                             re.S)


def find_lean_menu(x):
    """The record-load call, the tag getter, the launch-info pointer, the No Reboot flags, the
    PreLoad cell lookup and New Game's create-player call."""
    data = bytes(x.data)
    found = {}
    for what, sig in (("record call", LEAN_RECORD_SIG),
                      ("tag read", LEAN_TAG_SIG),
                      ("launch info", LEAN_LAUNCH_SIG),
                      ("no-reboot flags", LEAN_NO_REBOOT_SIG),
                      ("preload lookup", LEAN_PRELOAD_SIG),
                      ("create player", LEAN_PLAYER_SIG)):
        hits = list(sig.finditer(data))
        if len(hits) != 1:
            raise PatchError("lean-menu: %d %s match(es), expected 1" % (len(hits), what))
        found[what] = hits[0]
    site = x.off_to_va(found["record call"].start("site"))
    load = tes3x_inject.call_target(x, site)
    tag_call = x.off_to_va(found["tag read"].start("call"))
    preload = x.off_to_va(found["preload lookup"].start("site"))
    player = x.off_to_va(found["create player"].start("site"))
    if None in (site, tag_call, preload, player):
        raise PatchError("lean-menu: a call site is outside any section")
    if not load <= tag_call < load + 0x80:
        raise PatchError("lean-menu: the tag read is not at the head of the record loader")
    launch = struct.unpack("<I", found["launch info"].group("ptr"))[0]
    no_reboot = struct.unpack("<I", found["no-reboot flags"].group("new"))[0]
    if struct.unpack("<I", found["no-reboot flags"].group("load"))[0] != no_reboot + 1:
        raise PatchError("lean-menu: the No Reboot flags are not adjacent")
    return site, tes3x_inject.call_target(x, tag_call), launch, no_reboot, preload, player


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

TRANSITION_CALL_SIGS = {
    "cell": [
        re.compile(rb"\x8b\x50\x38\x83\xec\x0c\x8b\xcc\x89\x11\x8b\x50\x3c"
                   rb"\x89\x51\x04\x8b\x40\x40\x89\x41\x08(?P<site>\xe8....)\x83\xc4\x20", re.S),
        re.compile(rb"\x8b\x54\x24\x28\x8b\xc4\x89\x10\x8b\x4c\x24\x2c\x89\x48\x04"
                   rb"\x8b\x54\x24\x30\x89\x50\x08(?P<site>\xe8....)\x83\xc4\x20", re.S),
        re.compile(rb"\x89\x64\x24\x38\x89\x48\x04\x89\x50\x08"
                   rb"(?P<site>\xe8....)\x83\xc4\x20\xe9", re.S),
    ],
    "cell_companions": [
        re.compile(rb"\x89\x64\x24\x34\x89\x48\x04\x89\x50\x08"
                   rb"(?P<site>\xe8....)\x83\xc4\x1c\xe9", re.S),
    ],
    "teleport": [
        re.compile(rb"\x8b\x4c\x24\x18\x83\xec\x0c\x8b\xc4\x89\x08\x8b\x4c\x24\x2c"
                   rb"\x89\x50\x04\x89\x48\x08(?P<site>\xe8....)\xa1", re.S),
        re.compile(rb"\x89\x64\x24\x34\x68....\xe8...."
                   rb"(?P<site>\xe8....)\x83\xc4\x20\xe9", re.S),
        re.compile(rb"\x8b\x0d....\x89\x48\x04\x8b\x15....\x89\x64\x24\x38\x89\x50\x08"
                   rb"(?P<site>\xe8....)\x83\xc4\x20\xe9", re.S),
        re.compile(rb"\x8b\x10\x83\xec\x0c\x8b\xcc\x89\x11\x8b\x50\x04\x89\x51\x04"
                   rb"\x8b\x40\x08\x89\x41\x08(?P<site>\xe8....)\x83\xc4\x20\xb0\x01", re.S),
    ],
    "travel": [
        re.compile(rb"\x8b\x56\x08\x83\xec\x0c\x8b\xc4\x89\x10\x8b\x4e\x0c\x89\x48\x04"
                   rb"\x8b\x56\x10\x89\x50\x08(?P<site>\xe8....)\x83\xc4\x20\xa1", re.S),
    ],
}


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


def find_transition_calls(x):
    """Player-triggered cell changes, excluding load restore and exterior streaming."""
    found = {}
    data = bytes(x.data)
    for kind, signatures in TRANSITION_CALL_SIGS.items():
        sites = []
        for signature in signatures:
            hits = list(signature.finditer(data))
            if len(hits) != 1:
                raise PatchError("transition-autosaves: %s signature has %d matches, expected 1"
                                 % (kind, len(hits)))
            site = x.off_to_va(hits[0].start("site"))
            if site is None:
                raise PatchError("transition-autosaves: call is outside any section")
            sites.append(site)
        found[kind] = sites
    targets = {kind: {tes3x_inject.call_target(x, site) for site in sites}
               for kind, sites in found.items()}
    if len(targets["cell"] | targets["teleport"] | targets["travel"]) != 1 \
            or len(targets["cell_companions"]) != 1:
        raise PatchError("transition-autosaves: unexpected cell-change targets")
    return found, next(iter(targets["cell"])), next(iter(targets["cell_companions"]))


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


@patch("transition-autosaves")
def _transition_autosaves(x, value, ctx):
    """Save before player-triggered doors, teleports and paid travel."""
    hooks = ctx.get("hooks", {})
    wanted = {
        "cell": "transition_cell",
        "cell_companions": "transition_cell_companions",
        "teleport": "transition_teleport",
        "travel": "transition_travel",
    }
    if any(not hooks.get(name) for name in wanted.values()):
        raise PatchError("transition-autosaves: needs `payload` first, with transition hooks "
                         "in its manifest")
    calls, cell_change, companions = find_transition_calls(x)
    edits = []
    for kind, sites in calls.items():
        target = int(str(hooks[wanted[kind]]), 16)
        expected = companions if kind == "cell_companions" else cell_change
        for site in sites:
            was, off = x.patch_call(site, target)
            if was != expected:
                raise PatchError("transition-autosaves: call 0x%08X targets 0x%08X, expected 0x%08X"
                                 % (site, was, expected))
            edits.append((off, 5, "%s transition 0x%08X: 0x%08X -> 0x%08X"
                          % (kind, site, was, target)))
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


@patch("dxt5-size")
def _dxt5_size(x, value, ctx):
    """Size DXT5 textures as DXT3 instead of with a negative length."""
    target = ctx.get("hooks", {}).get("dxt5_size")
    if not target:
        raise PatchError("dxt5-size: needs `payload` first, with a dxt5_size hook in its manifest")
    site = find_dxt5_size(x)
    was, off = x.patch_call(site, int(str(target), 16))
    return [(off, 5, "texture size call 0x%08X: 0x%08X -> %s" % (site, was, target))]


@patch("lean-menu")
def _lean_menu(x, value, ctx):
    """Load only game settings for the main menu; the relaunch loads the rest."""
    hooks = ctx.get("hooks", {})
    if not all(hooks.get(k) for k in ("lean_record", "lean_preload", "lean_player")):
        raise PatchError("lean-menu: needs `payload` first, with lean_record, lean_preload and "
                         "lean_player hooks in its manifest")
    found = find_lean_menu(x)
    edits = []
    for site, key, what in ((found[0], "lean_record", "record load"),
                            (found[4], "lean_preload", "PreLoad cell lookup"),
                            (found[5], "lean_player", "New Game create-player")):
        was, off = x.patch_call(site, int(str(hooks[key]), 16))
        edits.append((off, 5, "%s call 0x%08X: 0x%08X -> %s" % (what, site, was, hooks[key])))
    return edits


@patch("video-arena")
def _video_arena(x, value, ctx):
    """Size the video-memory arena from [Xbox] VideoMemoryKB when there is more than 64 MB."""
    target = ctx.get("hooks", {}).get("arena_size")
    if not target:
        raise PatchError("video-arena: needs `payload` first, with an arena_size hook in its "
                         "manifest")
    site = find_arena_size(x)
    off = x.va_to_off(site)
    target = int(str(target), 16)
    x.data[off:off + 5] = b"\xe8" + struct.pack("<i", target - (site + 5))
    return [(off, 5, "arena size 0x%08X: mov ebx, 0xF80000 -> call 0x%08X" % (site, target))]


@patch("heap-region")
def _heap_region(x, value, ctx):
    """Reserve the engine heap's region, commit it as the heap grows, size it from the ini."""
    hooks = ctx.get("hooks", {})
    names = ("region_size", "region_reserve", "region_carve", "region_release")
    missing = [name for name in names if not hooks.get(name)]
    if missing:
        raise PatchError("heap-region: needs `payload` first, with %s in its manifest"
                         % ", ".join(missing))
    size, reserve, carve, release = (int(str(hooks[name]), 16) for name in names)
    site = find_heap_region(x)
    malloc_site, fit, free_site = find_heap_region_sites(x)
    off = x.va_to_off(site)
    x.data[off:off + 5] = b"\xe8" + struct.pack("<i", size - (site + 5))
    malloc, malloc_off = x.patch_call(malloc_site, reserve)
    free, free_off = x.patch_call(free_site, release)
    fit_off = x.va_to_off(fit)
    struct.pack_into("<i", x.data, fit_off + 2, carve - (fit + 6))
    return [
        (off, 5, "heap region 0x%08X: push 0x1100000 -> call 0x%08X" % (site, size)),
        (malloc_off, 5, "region malloc 0x%08X: 0x%08X -> 0x%08X" % (malloc_site, malloc, reserve)),
        (fit_off, 6, "region carve 0x%08X: jbe -> 0x%08X" % (fit, carve)),
        (free_off, 5, "region free 0x%08X: 0x%08X -> 0x%08X" % (free_site, free, release)),
    ]


@patch("info-name-arena")
def _info_name_arena(x, value, ctx):
    """Put INFO's temporary three-name tables in the demand-paged arena."""
    hooks = ctx.get("hooks", {})
    names = ("info_table_allocate", "info_names_allocate", "info_names_free",
             "info_table_free", "info_cleanup", "info_finish")
    missing = [name for name in names if not hooks.get(name)]
    if missing:
        raise PatchError("info-name-arena: needs `payload` first, with %s in its manifest"
                         % ", ".join(missing))
    sites = find_info_arena_sites(x)
    targets = {name: int(str(hooks[name]), 16) for name in names}
    edits = []
    for key, site in (("info_table_allocate", sites["table_allocate"]),
                      ("info_names_allocate", sites["names_allocate"]),
                      ("info_names_free", sites["destructor_names_free"]),
                      ("info_table_free", sites["destructor_table_free"]),
                      ("info_cleanup", sites["cleanup_call"]),
                      ("info_finish", sites["finish"])):
        was, off = x.patch_call(site, targets[key])
        edits.append((off, 5, "%s 0x%08X: 0x%08X -> 0x%08X"
                      % (key.replace("_", " "), site, was, targets[key])))
    return edits


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


def find_game_instance(x):
    """Global owner passed to the sole Game::Update call."""
    update = find_diagnostics_update(x)
    sites = find_call_sites(x, update)
    if len(sites) != 1:
        raise PatchError("Game instance: %d call site(s) for Game::Update, expected 1"
                         % len(sites))
    off = x.va_to_off(sites[0])
    if off is None or off < 6 or x.data[off - 6:off - 4] != b"\x8b\x0d":
        raise PatchError("Game instance: Game::Update call is not preceded by mov ecx,[global]")
    return struct.unpack_from("<I", x.data, off - 4)[0]


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


# Memory_Heap's entry points push their own names for a lock trace; each ends in `ret imm16`
# and int3 padding, which bounds the function so its recursive retry call can be left alone.
HEAP_FUNCTIONS = {
    "allocate": (b"Memory_Heap::Allocate\x00", b"\xc2\x0c\x00\xcc"),
    "free": (b"Memory_Heap::Free\x00", b"\xc2\x04\x00\xcc"),
}


def find_heap_function(x, which):
    """Memory_Heap::Allocate or ::Free as (start, end), from its name string."""
    name, tail = HEAP_FUNCTIONS[which]
    data = bytes(x.data)
    label = name[:-1].decode()
    string_va = x.off_to_va(find_unique(data, name, label))
    push = find_unique(data, b"\x68" + struct.pack("<I", string_va), label + " reference")
    start = data.rfind(b"\xcc\x55\x8b\xec", max(0, push - 0x400), push)
    if start < 0 or b"\xcc\xcc" in data[start + 1:push]:
        raise PatchError("%s: no prologue before its name reference" % label)
    end = data.find(tail, push, push + 0x800)
    if end < 0:
        raise PatchError("%s: no ret before the function padding" % label)
    return x.off_to_va(start + 1), x.off_to_va(end + len(tail) - 1)


# A wrapper forwards its own caller's file and line: two loads from [ebp+0xC..0x14] and no
# immediate push before the call.
HEAP_FORWARD = re.compile(rb"\x8b[\x45\x4d\x55\x5d\x75\x7d][\x0c\x10\x14]")
HEAP_WRAPPERS = 4


def heap_call_sites(x, which):
    """Call sites of a Memory_Heap entry point, outside the function itself."""
    start, end = find_heap_function(x, which)
    return [site for site in find_call_sites(x, start) if not start <= site < end]


def heap_wrapper_sites(x, sites):
    wrapped = []
    for site in sites:
        off = x.va_to_off(site)
        before = bytes(x.data[off - 24:off])
        if b"\x68" not in before[-16:] and len(HEAP_FORWARD.findall(before)) >= 2:
            wrapped.append(site)
    if len(wrapped) != HEAP_WRAPPERS:
        raise PatchError("heap-census: %d allocation wrapper(s), expected %d"
                         % (len(wrapped), HEAP_WRAPPERS))
    return wrapped


# operator new(size): `mov eax,[esp+4]; push 0; push "NA"; push eax; mov ecx,heap; call; ret`.
HEAP_NEW = re.compile(rb"\x8b\x44\x24\x04\x6a\x00\x68....\x50\xb9....(?=\xe8....\xc3)", re.S)
# MemoryPool_Simple::Allocate saves four registers, then falls back to the heap when full.
POOL_NAME = b"MemoryPool_Simple::Allocate\x00"
POOL_PROLOGUE = b"\x53\x55\x56\x8b\xf1\x57"


def heap_new_site(x, sites):
    """The call inside the frameless global operator new."""
    found = [site for site in sites
             if HEAP_NEW.match(bytes(x.data), x.va_to_off(site) - 17)]
    if len(found) != 1:
        raise PatchError("heap-census: %d operator new site(s), expected 1" % len(found))
    return found[0]


def heap_pool_site(x, sites):
    """The heap fallback inside MemoryPool_Simple::Allocate."""
    data = bytes(x.data)
    string_va = x.off_to_va(find_unique(data, POOL_NAME, "MemoryPool_Simple::Allocate"))
    push = find_unique(data, b"\x68" + struct.pack("<I", string_va),
                       "MemoryPool_Simple::Allocate reference")
    start = data.rfind(b"\xcc", max(0, push - 0x40), push) + 1
    if data[start:start + len(POOL_PROLOGUE)] != POOL_PROLOGUE:
        raise PatchError("heap-census: MemoryPool_Simple::Allocate prologue changed")
    end = data.find(b"\xcc\xcc", push)
    found = [site for site in sites if start <= x.va_to_off(site) < end]
    if len(found) != 1:
        raise PatchError("heap-census: %d pool fallback site(s), expected 1" % len(found))
    return found[0]


def find_heap_object(x):
    """The global Memory_Heap, loaded into ecx before most Allocate calls."""
    counts = {}
    for site in heap_call_sites(x, "allocate"):
        off = x.va_to_off(site)
        if x.data[off - 5] == 0xB9:
            va = struct.unpack_from("<I", x.data, off - 4)[0]
            counts[va] = counts.get(va, 0) + 1
    if not counts:
        raise PatchError("heap-census: no Allocate call loads the heap object")
    return max(counts, key=counts.get)


def find_heap_region(x):
    """The `push 0x1100000` in the static initializer that constructs the global heap."""
    heap = struct.pack("<I", find_heap_object(x))
    sig = b"\x55\x8b\xec\x68\x00\x00\x10\x01\xb9" + heap + b"\xe8"
    return x.off_to_va(find_unique(bytes(x.data), sig, "heap-region") + 3)


def _find_in(data, start, length, sig, what):
    at = data.find(sig, start, start + length)
    if at < 0 or data.find(sig, at + 1, start + length) >= 0:
        raise PatchError("heap-region: expected one %s" % what)
    return at


def find_heap_region_sites(x):
    """The constructor's malloc of the region, Allocate's `jbe` taken when a block fits the
    region, and the destructor's free of the region."""
    data = bytes(x.data)
    init = find_heap_region(x)
    ctor = x.va_to_off(tes3x_inject.call_target(x, init + 10))
    # call malloc; mov edx, [ebp-0x34]; mov [edx+0x14], eax
    store = _find_in(data, ctor, 0x100, b"\x8b\x55\xcc\x89\x42\x14", "region base store")
    if data[store - 5] != 0xE8:
        raise PatchError("heap-region: no call before the region base store")
    start, end = find_heap_function(x, "allocate")
    # cmp eax, [ecx+8]; jbe rel32
    fit = _find_in(data, x.va_to_off(start), end - start, b"\x3b\x41\x08\x0f\x86",
                   "region fit check") + 3
    # The initializer registers the destructor's atexit stub: push stub.
    off = x.va_to_off(init + 15)
    if data[off] != 0x68:
        raise PatchError("heap-region: no atexit push after the heap constructor")
    stub = struct.unpack_from("<I", data, off + 1)[0]
    dtor = x.va_to_off(tes3x_inject.call_target(x, stub + 8))
    # mov edx, [ecx+0x14]; push edx; mov ecx, [ebp-x]; call free
    free = _find_in(data, dtor, 0x100, b"\x8b\x51\x14\x52\x8b\x4d", "region free") + 7
    if data[free] != 0xE8:
        raise PatchError("heap-region: no call after the region free argument")
    return x.off_to_va(store - 5), x.off_to_va(fit), x.off_to_va(free)


def find_info_arena_sites(x):
    """INFO constructor allocations, its two name-free paths, and the call after the global
    dialogue cleanup loop. All are anchored by the INFO vtable and the global heap object."""
    data = bytes(x.data)
    heap = find_heap_object(x)
    heap_bytes = struct.pack("<I", heap)
    constructor_store = find_unique(data, b"\xc7\x06\x88\x80\x36\x00",
                                    "INFO constructor vtable")
    constructor_start = data.rfind(b"\x6a\xff", constructor_store - 0x60, constructor_store)
    if constructor_start < 0:
        raise PatchError("info-name-arena: INFO constructor start not found")
    constructor_va = x.off_to_va(constructor_start)
    constructor_end = constructor_store + 0xD0
    heap_allocate = find_heap_function(x, "allocate")[0]
    table_calls = [site for site in find_call_sites(x, heap_allocate)
                   if constructor_va <= site < x.off_to_va(constructor_end)]
    if len(table_calls) != 1:
        raise PatchError("info-name-arena: expected one INFO table allocation")
    table_allocate = table_calls[0]
    table_off = x.va_to_off(table_allocate)
    names_sig = (b"\x68\xdb\x0e\x00\x00\x68\x68\x80\x36\x00\x6a\x20\x6a\x03"
                 b"\x89\x46\x10\xe8")
    names_off = table_off + 5 + find_unique(
        data[table_off + 5:constructor_end], names_sig,
        "INFO name allocation sequence") + len(names_sig) - 1
    names_allocate = x.off_to_va(names_off)

    constructor_calls = find_call_sites(x, constructor_va)
    if len(constructor_calls) != 1:
        raise PatchError("info-name-arena: expected one INFO constructor call")
    load_off = x.va_to_off(constructor_calls[0] + 5)
    topic_match = re.search(rb"\x8b\x0d(?P<topic>....)", data[load_off:load_off + 0x20], re.S)
    if not topic_match:
        raise PatchError("info-name-arena: current topic load not found")
    current_topic = struct.unpack("<I", topic_match.group("topic"))[0]

    cleanup_sig = b"\x53\x56\x8b\x71\x18\x33\xdb\x3b\xf3\x74\x4c\x57"
    cleanup_off = find_unique(data, cleanup_sig, "INFO post-load cleanup")
    cleanup = x.off_to_va(cleanup_off)
    cleanup_names_free = cleanup + 0x26
    cleanup_table_free = cleanup + 0x48
    cleanup_link = cleanup + 0x17
    if data[x.va_to_off(cleanup_link)] != 0xE8 or \
            data[x.va_to_off(cleanup_names_free)] != 0xE8 or \
            data[x.va_to_off(cleanup_table_free)] != 0xE8:
        raise PatchError("info-name-arena: cleanup calls changed")

    dtor_free = re.compile(
        rb"\x8b\x47\x10\x3b\xc5.{0,16}?\x74.\x8b\x08\x51"
        rb"(?P<names>\xe8....)\x8b\x47\x10\x83\xc4\x04\x50\xb9" +
        re.escape(heap_bytes) + rb"(?P<table>\xe8....)\x89\x6f\x10", re.S)
    matches = list(dtor_free.finditer(data))
    if len(matches) != 1:
        raise PatchError("info-name-arena: %d destructor free sequences, expected 1"
                         % len(matches))
    destructor_names_free = x.off_to_va(matches[0].start("names"))
    destructor_table_free = x.off_to_va(matches[0].start("table"))

    if tes3x_inject.call_target(x, cleanup_names_free) != \
            tes3x_inject.call_target(x, destructor_names_free):
        raise PatchError("info-name-arena: name free targets differ")
    heap_free = find_heap_function(x, "free")[0]
    if any(tes3x_inject.call_target(x, site) != heap_free
           for site in (cleanup_table_free, destructor_table_free)):
        raise PatchError("info-name-arena: table free target changed")

    callers = find_call_sites(x, cleanup)
    if len(callers) != 1:
        raise PatchError("info-name-arena: expected one cleanup caller")
    cleanup_call = callers[0]
    finish = cleanup_call + 0x14
    if data[x.va_to_off(finish)] != 0xE8:
        raise PatchError("info-name-arena: post-cleanup call changed")
    return {
        "table_allocate": table_allocate,
        "names_allocate": names_allocate,
        "names_free": tes3x_inject.call_target(x, cleanup_names_free),
        "cleanup_names_free": cleanup_names_free,
        "cleanup_table_free": cleanup_table_free,
        "destructor_names_free": destructor_names_free,
        "destructor_table_free": destructor_table_free,
        "cleanup_call": cleanup_call,
        "link_original": tes3x_inject.call_target(x, cleanup_link),
        "current_topic": current_topic,
        "finish": finish,
        "finish_original": tes3x_inject.call_target(x, finish),
    }


@patch("heap-census")
def _heap_census(x, value, ctx):
    """Redirect every direct Memory_Heap::Allocate and ::Free call to the census."""
    hooks = ctx.get("hooks", {})
    names = ("heap_allocate", "heap_allocate_wrapped", "heap_allocate_new",
             "heap_allocate_pool", "heap_free")
    if not all(hooks.get(name) for name in names):
        raise PatchError("heap-census: needs `payload` first, built with tes3xheap.c")
    direct, wrapped, new, pool, free = (int(str(hooks[name]), 16) for name in names)
    allocs = heap_call_sites(x, "allocate")
    targets = dict.fromkeys(heap_wrapper_sites(x, allocs), wrapped)
    targets[heap_new_site(x, allocs)] = new
    targets[heap_pool_site(x, allocs)] = pool
    frees = heap_call_sites(x, "free")
    edits = [(None, 0, "Memory_Heap::Allocate: %d call site(s), %d forwarding"
              % (len(allocs), len(targets))),
             (None, 0, "Memory_Heap::Free: %d call site(s)" % len(frees))]
    for site in allocs:
        _was, off = x.patch_call(site, targets.get(site, direct))
        edits.append((off, 5, None))
    for site in frees:
        _was, off = x.patch_call(site, free)
        edits.append((off, 5, None))
    return edits


# CRT _heap_alloc: round the size, then RtlAllocateHeap(GetProcessHeap(), 0, size).
CRT_HEAP_ALLOC = re.compile(rb"\x8b\x44\x24\x04\x85\xc0\x75\x01\x40\x83\x3d....\x01\x74\x06"
                            rb"\x83\xc0\x0f\x83\xe0\xf0\x50\x6a\x00\xe8....\x50(?=\xe8....\xc3)",
                            re.S)
# XAPI HeapFree: RtlFreeHeap(heap, flags, ptr), its BOOLEAN widened.
XAPI_HEAP_FREE = re.compile(rb"(?:\xff\x74\x24\x0c){3}(?=\xe8....\x0f\xb6\xc0\xc2\x0c\x00)", re.S)


def _unique_call(x, rx, what):
    hits = list(rx.finditer(bytes(x.data)))
    if len(hits) != 1:
        raise PatchError("mem-census: %d %s signature(s), expected 1" % (len(hits), what))
    return tes3x_inject.call_target(x, x.off_to_va(hits[0].end()))


def find_xapi_heap_alloc(x):
    """RtlAllocateHeap, from the CRT allocator that calls it."""
    return _unique_call(x, CRT_HEAP_ALLOC, "CRT _heap_alloc")


def find_xapi_heap_free(x):
    """RtlFreeHeap, from XAPI's HeapFree."""
    return _unique_call(x, XAPI_HEAP_FREE, "HeapFree")


@patch("mem-census")
def _mem_census(x, value, ctx):
    """Redirect every direct RtlAllocateHeap and RtlFreeHeap call to the memory census."""
    hooks = ctx.get("hooks", {})
    if not (hooks.get("mem_heap_alloc") and hooks.get("mem_heap_free")):
        raise PatchError("mem-census: needs `payload` first, built with tes3xmem.c")
    edits = []
    for name, target, key in (("RtlAllocateHeap", find_xapi_heap_alloc(x), "mem_heap_alloc"),
                              ("RtlFreeHeap", find_xapi_heap_free(x), "mem_heap_free")):
        hook = int(str(hooks[key]), 16)
        sites = find_call_sites(x, target)
        edits.append((None, 0, "%s 0x%08X: %d call site(s)" % (name, target, len(sites))))
        for site in sites:
            _was, off = x.patch_call(site, hook)
            edits.append((off, 5, None))
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
    "script-decode": lambda image: find_script_decode_state(image)[0],
    "script-ip": lambda image: find_script_decode_state(image)[1],
    "script-opcode": lambda image: find_script_decode_state(image)[2],
    "ref-load": find_ref_load,
    "ref-skip": find_ref_skip,
    "mcp-97-scan": find_mcp97_scan,
    "mcp-154-load": find_mcp154_load,
    "mcp-154-reload": find_mcp154_reload,
    "mcp-102-actn": find_mcp102_actn,
    "dxt5-size": find_dxt5_size,
    "lean-menu": find_lean_menu,
    "video-arena": find_arena_size,
    "save-game": lambda image: find_autosave_calls(image)[0],
    "save-this-ptr": find_save_this_ptr,
    "preferences-load": find_preferences_load,
    "controls-table": find_controls_table,
    "diagnostics-update": find_diagnostics_update,
    "game-instance": find_game_instance,
    "console-print": find_console_print,
    "heap-allocate": lambda image: find_heap_function(image, "allocate")[0],
    "heap-free": lambda image: find_heap_function(image, "free")[0],
    "heap-object": find_heap_object,
    "heap-region": find_heap_region,
    "heap-region-malloc": lambda image: find_heap_region_sites(image)[0],
    "heap-region-fit": lambda image: find_heap_region_sites(image)[1],
    "heap-region-free": lambda image: find_heap_region_sites(image)[2],
    "info-names-allocate": lambda image: find_info_arena_sites(image)["names_allocate"],
    "info-names-free": lambda image: find_info_arena_sites(image)["names_free"],
    "info-current-topic": lambda image: find_info_arena_sites(image)["current_topic"],
    "info-link-original": lambda image: find_info_arena_sites(image)["link_original"],
    "info-finish-original": lambda image: find_info_arena_sites(image)["finish_original"],
    "xapi-heap-alloc": find_xapi_heap_alloc,
    "xapi-heap-free": find_xapi_heap_free,
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
