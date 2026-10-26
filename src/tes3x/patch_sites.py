"""Content-located engine addresses and signatures shared by patching and payload builds."""

import re
import struct
import tes3x.inject as tes3x_inject  # noqa: E402
from tes3x.console_signatures import SIGNATURES as CONSOLE_SIGNATURES, find_console_signature
from tes3x.multi_signatures import find_multi_signature

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


CERT_TITLE_ID = 0x18C


CERT_ALLOWED_MEDIA = 0x220


CERT_GAME_REGION = 0x224


# wszTitleName, 40 UTF-16 characters. This is the name a dashboard lists.
CERT_TITLE_NAME = 0x190


CERT_TITLE_CHARS = 40


MEDIA_ANY = 0xC00001FF


REGION_ANY = 0x00000007


class PatchError(Exception):
    pass


def find_mcp30_context(x):
    """Bow animation angles and player projectile matrix call on both retail engines."""
    angle_sites = []
    for match in re.finditer(rb"\xd8\x4e\x1c", x.data):
        start = match.end()
        mul = x.data.find(b"\xd8\x0d", start, start + 13)
        if mul < 0:
            continue
        scalar_va = struct.unpack_from("<I", x.data, mul + 2)[0]
        scalar_off = x.va_to_off(scalar_va)
        if scalar_off is None:
            continue
        angle_sites.append((x.off_to_va(mul + 2),
                            struct.unpack_from("<f", x.data, scalar_off)[0]))
    expected = (0.75, 0.25, 0.5, 0.5, 0.75, -0.25, 0.5, -0.5)
    if tuple(value for _, value in angle_sites) != expected:
        raise PatchError("mcp-30: bow animation angle signature differs")
    launch = list(re.finditer(rb"\xff\x50\x48\x8b\xc8(?P<call>\xe8....)\x50\x8d", x.data))
    if len(launch) != 1:
        raise PatchError(f"mcp-30: {len(launch)} player launch matrix calls, expected 1")
    site = x.off_to_va(launch[0].start("call"))
    if site is None:
        raise PatchError("mcp-30: player launch call is outside any section")
    return tuple(va for va, _ in angle_sites[:4]), site, tes3x_inject.call_target(x, site)


def find_mcp55_context(x):
    """Find the alchemy brew callback and its name reset in both retail engines."""
    callback_sig = re.compile(
        rb"\xe8....\xe8....\x8b\x0d....\xe8....\x8b\xc8\xe8....\xb0\x01\xc3",
        re.S,
    )
    callbacks = list(callback_sig.finditer(x.data))
    if len(callbacks) != 1:
        raise PatchError(f"mcp-55: {len(callbacks)} brew callbacks, expected 1")
    callback = x.off_to_va(callbacks[0].start())
    refresh_call = callback + 5
    refresh = tes3x_inject.call_target(x, refresh_call)
    reset_sig = re.compile(rb"\x85\xc0\x74\x0c\x68....\x8b\xc8\xe8....", re.S)
    start = x.va_to_off(refresh)
    resets = list(reset_sig.finditer(x.data, start, start + 0x900))
    if len(resets) != 1:
        raise PatchError(f"mcp-55: {len(resets)} name resets in refresh, expected 1")
    name_call = x.off_to_va(resets[0].start()) + 11
    return refresh_call, refresh, name_call, tes3x_inject.call_target(x, name_call)


def find_mcp118_drop_raycast(x):
    """Find the second ground raycast in the inventory drop placement path."""
    sig = re.compile(
        rb"\x6a\x00\x6a\x00\x8d\x44\x24\x68\x50\x56\x8b\xcf"
        rb"\xc7\x44\x24\x70\x00\x00\x00\x00"
        rb"\xc7\x44\x24\x74\x00\x00\x00\x00"
        rb"\xc7\x44\x24\x78\x00\x00\x80\xbf\xe8....",
        re.S,
    )
    hits = list(sig.finditer(x.data))
    if len(hits) != 1:
        raise PatchError(f"mcp-118: {len(hits)} drop raycasts, expected 1")
    site = x.off_to_va(hits[0].end() - 5)
    if site is None:
        raise PatchError("mcp-118: drop raycast is outside an XBE section")
    return site, tes3x_inject.call_target(x, site)


def find_mcp118_drop_finish(x):
    raycast, _ = find_mcp118_drop_raycast(x)
    site = raycast + 0xD9
    off = x.va_to_off(site)
    if off is None or not re.fullmatch(rb"\x8b\xcf\xe8....\x8b\x4f\x14\x85\xc9", x.data[off:off + 12], re.S):
        raise PatchError("mcp-118: drop placement cleanup changed")
    return site + 2, tes3x_inject.call_target(x, site + 2)


def find_mcp55_inventory_context(x):
    """Find the alchemy close tail and the engine's standard inventory icon rebuild."""
    close_sig = re.compile(
        rb"\xe8....\xe8....\x33\xc0\x66\xa1....\x50\xe8...."
        rb"\x83\xc4\x04\xe8....\xb0\x01\xc3", re.S,
    )
    closes = list(close_sig.finditer(x.data))
    if len(closes) != 1:
        raise PatchError(f"mcp-55: {len(closes)} alchemy close callbacks, expected 1")
    close_site = x.off_to_va(closes[0].start()) + 27
    update_tiles = tes3x_inject.call_target(x, close_site)
    rebuild_sig = re.compile(
        rb"\x8b\x0d(?P<world>....)\x8b\x89\xc4\x02\x00\x00\x6a\x02"
        rb"\xe8(?P<clear>....)\x8b\x0d(?P=world)\xe8(?P<player>....)"
        rb"\x8b\x90\x60\x05\x00\x00\xa1(?P=world)"
        rb"\x8b\x88\xc4\x02\x00\x00\x6a\x02\x83\xc2\x3c\x52"
        rb"\xe8(?P<add>....)\xe8(?P<update>....)", re.S,
    )
    rebuilds = list(rebuild_sig.finditer(x.data))
    if not rebuilds:
        raise PatchError("mcp-55: no inventory rebuild sequence")
    contexts = set()
    for match in rebuilds:
        base = x.off_to_va(match.start())
        contexts.add((struct.unpack("<I", match.group("world"))[0],
                      tes3x_inject.call_target(x, base + match.start("clear") - match.start() - 1),
                      tes3x_inject.call_target(x, base + match.start("player") - match.start() - 1),
                      tes3x_inject.call_target(x, base + match.start("add") - match.start() - 1),
                      tes3x_inject.call_target(x, base + match.start("update") - match.start() - 1)))
    if len(contexts) != 1:
        raise PatchError(f"mcp-55: {len(contexts)} inventory rebuild targets, expected 1")
    world, clear, player, add, update = contexts.pop()
    if update != update_tiles:
        raise PatchError("mcp-55: alchemy close uses another inventory tile update")
    return close_site, world, clear, player, add, update


def find_unique(data, needle, what):
    hits = [m.start() for m in re.finditer(re.escape(needle), data)]
    if len(hits) != 1:
        raise PatchError("%s: %d match(es) for %r, expected exactly 1" % (what, len(hits), needle))
    return hits[0]


def find_mcp127_map(x):
    """Locate the map panel texture and its fixed size from the texture path."""
    land = b"Textures\\_land_default.tga\0"
    icon = b"Textures\\menu_icon_none.tga\0"
    land_off = find_unique(x.data, land, "mcp-127 land texture")
    icon_off = find_unique(x.data, icon, "mcp-127 icon texture")
    land_va = x.off_to_va(land_off)
    icon_va = x.off_to_va(icon_off)
    if land_va is None or icon_va is None:
        raise PatchError("mcp-127: texture path is outside the image")
    sig = b"\x68" + struct.pack("<I", land_va) + b"\x8b\xce\x52\xe8"
    site = find_unique(x.data, sig, "mcp-127 map initializer")
    # The call displacement varies by engine; the scale store must still match.
    if bytes(x.data[site + 13:site + 29]) != (
            b"\xb9\x00\x00\x80\x40\x89\x88\x4c\x01\x00\x00\x89\x88\x50\x01\x00"):
        raise PatchError("mcp-127: map scale layout changed")
    return x.off_to_va(site), icon_va


def find_item_tooltip(x):
    """The object tooltip and its world-target call, anchored in player activation."""
    from tes3x.multi_signatures import find_multi_signature

    activation = find_multi_signature(x, "ACTIVATION_TARGET")
    start = x.va_to_off(activation)
    pattern = re.compile(rb"\xe8....\x68\x05\x81\xff\xff\xe8", re.S)
    hits = [m for m in pattern.finditer(bytes(x.data), start, start + 0x400)]
    if len(hits) != 1:
        raise PatchError("object tooltip: %d world calls, expected 1" % len(hits))
    world_call = x.off_to_va(hits[0].start())
    tooltip = tes3x_inject.call_target(x, world_call)
    calls = find_call_sites(x, tooltip)
    if len(calls) != 19 or world_call not in calls:
        raise PatchError("object tooltip: %d calls, expected 19 including world target"
                         % len(calls))
    head = bytes(x.data[x.va_to_off(tooltip):x.va_to_off(tooltip) + 0x85])
    if head[:8] != bytes.fromhex("83 ec 20 56 8b 74 24 28") or \
            head[0x54:0x56] != b"\x66\xa1" or head[0x80] != 0xe8:
        raise PatchError("object tooltip: unexpected builder entry")
    content_id = struct.unpack_from("<I", head, 0x56)[0]
    get_item_data = tes3x_inject.call_target(x, tooltip + 0x80)
    find_help = tes3x_inject.call_target(x, world_call + 10)
    return tooltip, calls, content_id, get_item_data, find_help


SLEEP_SIG = re.compile(rb"\x6a\x00\xff\x74\x24\x08\xe8....\xc2\x04\x00", re.S)


# inc counter; test its low bits; up to four bytes of interleaved store; jne +7; push 1; call
SLEEP_LOOP_SIG = re.compile(
    rb"(?:\xf6[\xc0-\xc7]|\xa8)[\x0f\x3f\x7f].{0,4}\x75\x07\x6a(?P<ms>\x01)\xe8(?P<rel>....)", re.S)


SLEEP_LOOPS = 13


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


# Position's case: fstp [destination+8]; mov [rotation], 0; mov [rotation+4], 0
SCRIPT_POSITION_SIG = re.compile(
    rb"\xD9\x1D(?P<z>....)\xC7\x05(?P<rx>....)\x00\x00\x00\x00\xC7\x05(?P<ry>....)\x00\x00\x00\x00",
    re.S)
# text[len] = 0 after each string operand copy: mov byte ptr [reg + text], 0
SCRIPT_TEXT_TERMINATOR = re.compile(rb"\xC6[\x80-\x87](....)\x00", re.S)
SCRIPT_DECODE_SPAN = 0x2000


def find_script_operand_globals(x):
    """Decode's destination[3], rotation[3] and text buffer, read from Decode's own stores."""
    decode = find_script_decode_state(x)[0]
    off = x.va_to_off(decode)
    body = bytes(x.data[off:off + SCRIPT_DECODE_SPAN])
    hits = [m for m in SCRIPT_POSITION_SIG.finditer(body)
            if struct.unpack("<I", m.group("ry"))[0] == struct.unpack("<I", m.group("rx"))[0] + 4]
    if len(hits) != 1:
        raise PatchError("Script::Decode: %d Position operand store(s), expected 1" % len(hits))
    destination = struct.unpack("<I", hits[0].group("z"))[0] - 8
    rotation = struct.unpack("<I", hits[0].group("rx"))[0]
    texts = {struct.unpack("<I", m.group(1))[0] for m in SCRIPT_TEXT_TERMINATOR.finditer(body)}
    if len(texts) != 1:
        raise PatchError("Script::Decode: %d text buffer candidate(s), expected 1" % len(texts))
    return destination, rotation, texts.pop()


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


def find_script_ip_restore_call(x, run_function, script_ip):
    """Find the RunFunction caller that restores the decoder cursor from ESI."""
    sites = []
    for site in find_call_sites(x, run_function):
        off = x.va_to_off(site)
        if (bytes(x.data[off + 7:off + 9]) == b"\x89\x35"
                and struct.unpack_from("<I", x.data, off + 9)[0] == script_ip):
            sites.append(site)
    if len(sites) != 1:
        raise PatchError("script cursor restore: %d call site(s), expected 1" % len(sites))
    return sites[0]


def find_script_ip_restore_site(x):
    """Locate the RunFunction call whose caller owns the live decoder cursor."""
    _decode, script_ip, _opcode = find_script_decode_state(x)
    return find_script_ip_restore_call(x, find_run_function(x), script_ip)


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


# When two container references share one animated object, this path temporarily adjusts the
# references' own counts as well as assigning the animation.  Those extra retain/release operations
# can destroy the animation during access.  Anchor both operations and the common return together.
MCP98_REFCOUNT_SIG = re.compile(
    rb"\x3b\xf8\x0f\x85...."
    rb"\x8b\x57\x18\x8b\x07(?P<retain>\x42)\x8b\xcf\x89\x57\x18\xff\x50\x2c"
    rb".{64,160}?"
    rb"(?P<release>\xff\x4e\x18)(?P<guard>\x75.)"
    rb"\x8b\x06\x8b\xce\xff\x50\x2c"
    rb".{16,96}?"
    rb"(?P<exit>\x5e\x5b\x5f\xc3)",
    re.S,
)


# The summon-effect removal path already retires spells cast by the actor. Its following virtual
# cleanup leaves magic targeting that actor alive, however. MCP replaces that call with the same
# MobileActor::retireMagic wrapper used by the normal actor-lifetime paths.
MCP92_UNSUMMON_SIG = re.compile(
    rb"\x8b\xce\xe8....\x8b\x16\x6a\x01\x8b\xce\x8b\xf8\xff\x52\x14"
    rb"\x8b\xce\xe8....\xa1....\x8b\x48\x6c\x56\xe8...."
    rb"(?P<site>\x8b\x17\x6a\x00\x8b\xcf\xff\x52\x70)"
    rb"\x6a\x01\x8b\xce\xe8....",
    re.S,
)


MCP92_RETIRE_MAGIC_SIG = re.compile(
    rb"\x8b\x41\x14\x8b\x0d....\x8b\x49\x6c\x50\xe8....\xc3"
)


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


# Physical damage calls the actor's equipped-armor count virtual, then skips the complete
# reduction calculation when the count is zero. The calculation itself handles an unarmored
# result; MCP makes it reachable for a fully unarmored actor.
MCP3_UNARMORED_SIG = re.compile(
    rb"\xff\x92\xe4\x00\x00\x00\x8b\x44\x24\x10\x83\xcb\xff\x85\xc0"
    rb"(?P<site>\x0f\x84....)\xd8\x44\x24\x1c\x8b\x0d....\x68\x60\x04\x00\x00",
    re.S,
)


# A repeated DIAL record merges into the existing topic, then copies the just-constructed
# replacement's empty INFO head over it (Dialogue::mergeRepeated). The PC keeps the chain.
DIALOGUE_MERGE_SIG = re.compile(
    rb"\x8a\x47\x14\x88\x46\x14\x8b\x4f\x18(?P<site>\x89\x4e\x18)\x5f\x5e\xc2\x04\x00"
)


# PlaceItem and PlaceItemCell share this call to Cell::addReference.  The reference has already
# been initialized; the missing operation is marking the destination cell changed before insertion.
MCP123_ADD_SIG = re.compile(
    rb"\xa0....\x84\xc0\x75.\x8b\x17\x6a\x01\x8b\xcf\xff\x52\x14"
    rb"\x8b\x4c\x24\x40\x57(?P<site>\xe8....)\x85\xf6",
    re.S,
)


MCP6_RESET_SIG = re.compile(
    rb"\x51\x57\x8b\xf9\x8a\x47\x04\x84\xc0\x0f\x85...."
    rb"(?P<site>\x8b\x4f\x0c\x8b\x01)\x3b\xc1\x56", re.S,
)
MCP6_EFFECT_SIG = re.compile(
    rb"\x53\x55\x8b\x6c\x24\x1c\x8b\x4d\x00(?P<site>\xe8....)"
    rb"\x6a\x00\x8d\x0c\x7f\x8d\x9c\x88\x54\x02\x00\x00"
    rb"\x8b\x4c\x24\x18\x6a(?P<kind>[\x02\x1c])", re.S,
)
MCP6_FORTIFY_SIG = re.compile(
    rb"\x55\x57\x8b\x7c\x24\x1c\x8b\x0f(?P<site>\xe8....)"
    rb"\x6a\x00\x8d\x0c\x76\x8d\xac\x88\x54\x02\x00\x00", re.S,
)
MCP6_DRAIN_POST_SIG = re.compile(
    rb"\xe8....(?P<site>\x8b\x4d\x00\x83\xc4\x28)\x88\x44\x24\x14", re.S,
)
MCP6_RESTORE_POST_SIG = re.compile(
    rb"\xe8....(?P<site>\x83\xc4\x28\x84\xc0)\x74.", re.S,
)


# RaiseRank and LowerRank mark the script context, then change the NPC reference's rank.
MCP173_RANK_SIG = re.compile(
    rb"(?P<site>\x8b\x01\x6a\x01\xff\x50\x14)\x8b\x0d...."
    rb"\x8b\x41\x6c\xfe[\x80\x88]\xaa\x00\x00\x00", re.S,
)


# Position and PositionCell share the reference-move helper. Their early exits skip the script
# attachment used by the third caller, and the helper removes actor collision when the destination
# is absent but never adds it when the destination is present.
MCP125_POSITION_A_SIG = re.compile(
    rb"(?P<site>\xa1(?P<angle>....)\x8b\x0d(?P<ref>....)\x50\x68(?P<coords>....)"
    rb"\x56\x51(?P<call>\xe8....)\x83\xc4\x10\xe9....)",
    re.S,
)


MCP125_POSITION_B_SIG = re.compile(
    rb"(?P<site>\x8b\x15(?P<angle>....)\xa1(?P<ref>....)\x52\x68(?P<coords>....)"
    rb"\x56\x50(?P<call>\xe8....)\x83\xc4\x10\xe9....)",
    re.S,
)


MCP125_ATTACH_SIG = re.compile(
    rb"\x8b\x0d....\x51\x68(?P<coords>....)\x57\x56(?P<call>\xe8....)\x83\xc4\x10"
    rb"(?P<resume>\x8b\xce\xe8....\x8b\x4e\x10)",
    re.S,
)


MCP125_COLLISION_SIG = re.compile(
    rb"(?P<site>\x8a\x44\x24\x3c\x84\xc0\x0f\x85....\xa1(?P<manager>....)\x8b\x48\x5c)"
    rb"\x57(?P<remove>\xe8....)\xe9....",
    re.S,
)


MCP125_ADD_MOB_SIG = re.compile(
    rb"\x8b\x0d(?P<manager>....)\x8b\x49\x5c\x57(?P<add>\xe8....)\x8b\xcf\xe8....",
    re.S,
)


# The exterior/interior cell-change path copies the player's position, tears down its current
# world state, then installs the destination. MCP inserts its stale-cast cleanup immediately
# after that teardown. Capture the repeated game singleton so the payload does not pin it.
MCP37_SITE_SIG = re.compile(
    rb"\x8b\x0d(?P<game>....)\xe8....\x8b\x40\x14\x8b\x48\x38\x83\xc0\x38"
    rb"\x89\x4c\x24\x04\x8b\x50\x04\x8b\x0d(?P=game)\x89\x54\x24\x08"
    rb"\x8b\x40\x08\x89\x44\x24\x0c\x8b\x89\x3c\x03\x00\x00\x85\xc9\x74."
    rb"\xe8....(?P<site>\x8b\x0d(?P=game))\x56\x8d\x94\x24\x84\x00\x00\x00",
    re.S,
)


# std::_Tree::iterator::operator++ is shared by the magic manager's trees. Its full body is
# unique; unlike the PC build, Xbox marks the per-tree nil node at node+0x15.
MCP37_TREE_NEXT_SIG = bytes.fromhex(
    "8b018a501584d2754d8b5008538a5a1584db751b8b028a581584db750e8d4900"
    "8bd08b028a581584db74f589115bc38b40048a501584d2751a8da42400000000"
    "8b113b5008750c89018b40048a501584d274ed89015bc3"
)


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


# CVBHeap::Allocate's overflow, XPhysicalAlloc(size + 0x10, -1, 0, PAGE_READWRITE |
# PAGE_WRITECOMBINE), and CVBHeap::Free's XPhysicalFree of an overflow block (negative size).
ARENA_OVERFLOW_ALLOC_SIG = re.compile(rb"\x68\x04\x02\x00\x00\x6a\x00\x6a\xff\x8d\x53\x10\x52\xe8")
ARENA_OVERFLOW_FREE_SIG = re.compile(
    rb"\x8b\x4e\xf0\x85\xc9\x8d\x46\xf0\x89\x44\x24\x14\x7d.\x8b\x55\x2c\x83\xc1\xf0\x03\xd1"
    rb"\x50\x89\x55\x2c\xe8", re.S)


def find_arena_overflow(x):
    """The overflow's XPhysicalAlloc and XPhysicalFree call sites in the video arena."""
    data = bytes(x.data)
    sites = []
    for what, rx in (("allocation", ARENA_OVERFLOW_ALLOC_SIG), ("free", ARENA_OVERFLOW_FREE_SIG)):
        hits = list(rx.finditer(data))
        if len(hits) != 1:
            raise PatchError("video-arena: %d overflow %s sites, expected 1" % (len(hits), what))
        sites.append(x.off_to_va(hits[0].end() - 1))
    return {"alloc": sites[0], "free": sites[1],
            "phys_alloc": tes3x_inject.call_target(x, sites[0]),
            "phys_free": tes3x_inject.call_target(x, sites[1])}


# The walk behind findFirstInstanceOfObjectId: every cell of NonDynamicData+0xB270, asking each
# Cell::findReferenceToObject(object, 0).
REF_INDEX_FIND_SIG = re.compile(
    rb"\x83\xec\x08\x53\x55\x56\x89\x4c\x24\x10\x8b\x89\x70\xb2\x00\x00\x57"
    rb"\xc7\x44\x24\x10\x00\x00\x00\x00\xe8....\x8b\xf8\x85\xff\x74.\x8b\x6c\x24\x1c"
    rb"\x8b\x37\x6a\x00\x55\x8b\xce(?P<find>\xe8....)",
    re.S,
)


# WorldController::startGlobalScripts: the scripts list from the data handler, a counter in ebx.
REF_INDEX_SCRIPTS_SIG = re.compile(
    rb"\xa1....\x53\x55\x8b\xe9\x8b\x08\x56\x8b\x71\x38\x57\x8b\xce\x33\xdb\xe8....\x85\xc0\x74.",
    re.S,
)


REF_INDEX_SCRIPT_CALLS = 4


def find_ref_index(x):
    """Return the cell walk, Cell::findReferenceToObject and startGlobalScripts."""
    data = bytes(x.data)
    found = []
    for signature, label in ((REF_INDEX_FIND_SIG, "cell walk"),
                             (REF_INDEX_SCRIPTS_SIG, "startGlobalScripts")):
        hits = list(signature.finditer(data))
        if len(hits) != 1:
            raise PatchError("ref-index: %d %s match(es), expected 1" % (len(hits), label))
        found.append(hits[0])
    walk = x.off_to_va(found[0].start())
    scripts = x.off_to_va(found[1].start())
    if walk is None or scripts is None:
        raise PatchError("ref-index: a match is outside any section")
    find_site = walk + found[0].start("find") - found[0].start()
    return walk, tes3x_inject.call_target(x, find_site), scripts


# The walk parseAnimationTextKeys makes per SoundGen key: every object, for the first creature
# whose name (vtable +0x48) matches, then the sound generator lookup.
SOUNDGEN_WALK_SIG = re.compile(
    rb"\x53\x56\x57\x8b\xf9\x8b\x4f\x18\xe8....\x8b\xf0\x85\xf6\x74.\x8b\x5c\x24\x10\x8d\x4e\x04"
    rb"\xe8....\x3d\x43\x52\x45\x41\x75.\x8b\x06\x8b\xce\xff\x50\x48\x85\xc0\x74.\x8b\x16\x53"
    rb"\x8b\xce\xff\x52\x48\x50(?P<stricmp>\xe8....)\x83\xc4\x08\x85\xc0\x74.\x8b\xce\xe8...."
    rb"\x8b\xf0\x85\xf6\x75.\x8b\x4c\x24\x14\x51\x6a\x00\x8b\xcf(?P<resolve>\xe8....)"
    rb"\x5f\x5e\x5b\xc2\x08\x00",
    re.S,
)


def find_soundgen_index(x):
    """Return the creature walk, its _stricmp and the sound generator lookup it ends in."""
    hits = list(SOUNDGEN_WALK_SIG.finditer(bytes(x.data)))
    if len(hits) != 1:
        raise PatchError("soundgen-index: %d creature walk(s), expected 1" % len(hits))
    walk = x.off_to_va(hits[0].start())
    if walk is None:
        raise PatchError("soundgen-index: the creature walk is outside any section")
    base = walk - hits[0].start()
    return (walk, tes3x_inject.call_target(x, base + hits[0].start("stricmp")),
            tes3x_inject.call_target(x, base + hits[0].start("resolve")))


def find_dxt5_size(x):
    """The texture-create call to the texture-size function."""
    hits = list(DXT5_SIZE_SIG.finditer(bytes(x.data)))
    if len(hits) != 1:
        raise PatchError("dxt5-size: %d texture size call(s), expected 1" % len(hits))
    va = x.off_to_va(hits[0].start("site"))
    if va is None:
        raise PatchError("dxt5-size: texture size call is outside any section")
    return va


# MobilePlayer's input loop checks its disabled flag, then suppresses normal action handlers while
# an attack or cast is active.  The following state check anchors the one call we replace.
MCP146_SIG = re.compile(
    rb"\x8a\x86\xb0\x05\x00\x00\x84\xc0\x0f\x85....\x8b\xce"
    rb"(?P<site>\xe8....)\x84\xc0\x0f\x85....\x80\xbe\xdd\x00\x00\x00\x01",
    re.S,
)


MCP146_READY_SIG = re.compile(
    rb"\x8b\x46\x10\xc1\xe8\x0d\xa8\x01\x0f\x84...."
    rb"\x8b\x0d(?P<game>....)\x8b\x49\x4c\x6a\x02\x6a\x06(?P<input>\xe8....)"
    rb"\x85\xc0\x75.\x8b\x15(?P=game)\x8b\x4a\x4c\x6a\x01\x6a\x06(?P<input2>\xe8....)",
    re.S,
)


MCP146_RESUME_SIG = re.compile(
    rb"\x8b\x8e\x44\x02\x00\x00\xc6\x44\x24\x13\x01\xe8...."
    rb"(?P<resume>\x33\xc0\x66\x8b\x46\x08)",
    re.S,
)


# Actor::unequipItem clears the actor's magic, then unconditionally deselects the player's
# magic menu. The nearby readied-weapon branch identifies the world pointer and player getter.
MCP47_SIG = re.compile(
    rb"\x6a\x00\x8b\xcb\xe8....(?P<site>\xe8....)"
    rb"\x39\xb3\x88\x03\x00\x00\x75.\x6a\x00\x8b\xcb\xe8...."
    rb"\x8b\x0d(?P<world>....)(?P<player>\xe8....)\x3b\xd8",
    re.S,
)


# MobilePlayer::updateScenegraph updates the first-person transform, then updates and traverses
# its root.  The call is narrow enough to move only the viewmodel after the engine positions it.
BOW_VIEW_SIG = re.compile(
    rb"\x8b\xce(?P<site>\xe8....)\x8b\x17\x8b\xcf\xff\x52\x08\x8b\xcf\xe8....\x8d\x44\x24\x14",
    re.S,
)


# Game::createRenderer passes its settings' width and height to NiXBoxRenderer::create, which
# passes its present parameters to Direct3D_CreateDevice; the renderer's mode list asks D3D for
# the mode count and each mode.
VIDEO_RENDERER_SIG = re.compile(
    rb"\x8b\x56\x08\x51\x8b\x4e\x0c\x50\x55\x51\x52(?P<site>\xe8....)\x83\xc4\x20\x8b\xf0\x6a\x05",
    re.S,
)


VIDEO_CREATE_SIG = re.compile(
    rb"\x8d\x4e\x70\x55\x89\x7e\x78\x89\xbe\x84\x02\x00\x00\x89\x54\x24\x14(?P<site>\xe8....)"
    rb"\x3b\xc7\x5d",
    re.S,
)


VIDEO_MODES_SIG = re.compile(
    rb"\x8b\x07\x89\x44\x24\x0c(?P<count>\xe8....)\x8d\xb7\xe0\x00\x00\x00.{80,100}?"
    rb"\x8d\x54\x24\x14\x52\x55(?P<enum>\xe8....)\x85\xc0\x75",
    re.S,
)


# The book and journal scale helper and ShowScrollMenu divide 640.0 by viewWidth (+0x74); the
# float after that 640.0 is 480.0.
MCP94_SIGS = (
    re.compile(rb"\x8b\x0d....(?P<view>\xdb\x41\x74)\x8b\x56\x38\x89\x54\x24\x10\x6a\x02"
               rb"\xd8\x0d....\x8b\xc8\xd8\x3d(?P<base>....)", re.S),
    re.compile(rb"\xa1....(?P<view>\xdb\x40\x74)\x8b\xbe\x90\x00\x00\x00\x8b\x4f\x30\x8b\x57\x38"
               rb"\xd8\x3d(?P<base>....)", re.S),
)


def find_mcp94(x):
    """The viewWidth reads and 640.0 operands of the book and scroll scales."""
    data = bytes(x.data)
    sites = []
    for sig, what in zip(MCP94_SIGS, ("book", "scroll")):
        hits = list(sig.finditer(data))
        if len(hits) != 1:
            raise PatchError("mcp-94: %d %s scale(s), expected 1" % (len(hits), what))
        base = struct.unpack("<I", hits[0].group("base"))[0]
        off = x.va_to_off(base)
        if off is None or struct.unpack_from("<ff", data, off) != (640.0, 480.0):
            raise PatchError("mcp-94: %s scale does not divide 640.0 beside 480.0" % what)
        sites.append((what, hits[0].start("view") + 2, hits[0].start("base"), base))
    return sites


# The call after NiXBoxRenderer::create in Game::createRenderer (push 5 and the flicker filter),
# and the renderer's display and clear methods.
BOOT_CLEAR_SIG = re.compile(
    rb"\x8b\xf0\x6a\x05\x89\xbe\xd0\x02\x00\x00(?P<site>\xe8....)\x6a\x00\xe8....\x8b\x4c\x24\x1c",
    re.S,
)
BOOT_CLEAR_DISPLAY_SIG = re.compile(
    rb"\x56\x8b\xf1\x8b\x46\x58\x8b\x48\x10\x85\xc9\x75\x0e\x6a\x00\xe8....\xc7\x46\x74"
    rb"\x00\x00\x00\x00\x5e\xc3",
    re.S,
)
BOOT_CLEAR_CLEAR_SIG = re.compile(
    rb"\x8b\x44\x24\x08\x53\x56\x8b\xf1\x8b\xc8\x80\xe1\x01\xf6\xd9\x57\x1b\xc9\x81\xe1"
    rb"\xf0\x00\x00\x00",
    re.S,
)


def find_boot_clear(x):
    """The call after NiXBoxRenderer::create in Game::createRenderer, then the renderer's clear
    and display methods."""
    data = bytes(x.data)
    tails = list(BOOT_CLEAR_SIG.finditer(data))
    clears = list(BOOT_CLEAR_CLEAR_SIG.finditer(data))
    displays = list(BOOT_CLEAR_DISPLAY_SIG.finditer(data))
    if len(tails) != 1 or len(clears) != 1 or len(displays) != 1:
        raise PatchError("boot-clear: found %d createRenderer tails, %d clear and %d display "
                         "methods, expected 1" % (len(tails), len(clears), len(displays)))
    sites = [x.off_to_va(m.start(g)) for m, g in ((tails[0], "site"), (clears[0], 0),
                                                  (displays[0], 0))]
    if None in sites:
        raise PatchError("boot-clear: a site is outside any section")
    return sites


# The three callers of LoadScreenManager::open: the main menu's logo movie, New Game and a load.
MENU_STILL_MENU_SIG = re.compile(
    rb"\x8b\x88\xf0\x00\x00\x00\x6a\x00\x6a\x00\x6a\x00\x6a\x00\x6a\x00\x6a\x01\x57(?P<site>\xe8....)",
    re.S,
)
MENU_STILL_NEW_SIG = re.compile(
    rb"\x8b\x88\xf0\x00\x00\x00\x6a\x01\x6a\x00\x6a\x00\x6a\x00\x6a\x00\x6a\x01\x56(?P<site>\xe8....)",
    re.S,
)
MENU_STILL_LOAD_SIG = re.compile(
    rb"\x6a\x01\x6a\x00\x84\xc0\x6a\x00\x0f\x95\xc0\x6a\x00\x6a\x00\x50\x68....(?P<site>\xe8....)",
    re.S,
)


# D3DDevice_Swap's fence wait: D3D_SetFence(0), then D3D_BlockOnTime(previous fence, 1).
MENU_STILL_FENCE_SIG = re.compile(
    rb"\x6a\x00(?P<fence>\xe8....)\x8b\x86\x78\x24\x00\x00\x48\x83\xe0\x01\x8b\x84\x86\x74\x19"
    rb"\x00\x00\x85\xc0\x74.\x6a\x01\x50(?P<block>\xe8....)",
    re.S,
)


# The call to MenuOptions::build in MenuOptions::open: the new menu in esi is its argument.
DASH_BUTTON_SIG = re.compile(
    rb"\x55\x56\x57\x51\xe8....\x8b\xf0\x56(?P<site>\xe8....)\xa0....\x83\xc4\x08\x84\xc0",
    re.S,
)


# The call in MenuOptions::open that sets the built menu's height: 250 before a game, else 300.
DASH_HEIGHT_SIG = re.compile(
    rb"\x83\xc3\xce\x53\x8b\xce(?P<site>\xe8....)\x8b\x4c\x24\x10\x51\x8b\xce\xe8....\x6a\x01\x8b\xce"
    rb"\xe8",
    re.S,
)

# UI::updateTopicsList: the call that filters the actor's topics, the call that makes each topic's
# widget (dialogue and click handler stacked beneath its name), and the call in the topic click
# that runs the response's script, once the response is shown (a topic's click and a hyperlink's).
# The filter is called from the first.
TOPIC_LIST_SIG = re.compile(
    rb"\xc7\x44\x24\x64\xff\xff\xff\xff(?P<site>\xe8....)\x8b\x7c\x24\x24\x83\xc4\x08\x55"
    rb"\x68\xba\x80\xff\xff",
    re.S,
)
TOPIC_RUN_SIG = re.compile(
    rb"\x57\x8b\xcb(?P<site>\xe8....)\xe8....\xe8....\x83\x7c\x24\x40\x10", re.S)
TOPIC_CLICK_SIG = re.compile(
    rb"\x84\xc9\x75.\x2b\xc2\x89\x44\x24\x14\x74.\x53\x8b\xce(?P<site>\xe8....)", re.S)
# UI::updateTopicsList's three callers: the click on a hyperlink, the click on a topic (a tail jump)
# and the response text builder.
TOPIC_UPDATE_SIGS = (
    re.compile(rb"\x57\x8b\xcb\xe8....\xe8....(?P<site>\xe8....)\x83\x7c\x24\x40\x10", re.S),
    re.compile(rb"\x5f\x5b\x5e\x83\xc4\x0c(?P<site>\xe9....)\x5e\x83\xc4\x0c", re.S),
    re.compile(rb"\x83\xfb\x05\x74.(?P<site>\xe8....)\x83\xc8\xff", re.S),
)
TOPIC_FILTER_SIG = re.compile(rb"\x6a\x00\x50\x50\x8b\xce(?P<site>\xe8....)\x85\xc0\x74", re.S)

TOPIC_STATE_CALL_SIG = re.compile(
    rb"\x56\x6a\x00\x6a\x00\x68\x32\x80\xff\xff\x56(?P<site>\xe8....)\x83\xc4\x14", re.S)
TOPIC_STATE_PUSH_SIG = re.compile(rb"\x6a\x20(?P<site>\x68....)\x68\x32\x80\xff\xff\x8b\xce", re.S)
TOPIC_STATE_TAIL_SIG = re.compile(
    rb"\x8b\xce(?P<paint>\xe8....)\x6a\x00\x8b\xce(?P<update>\xe8....)\xb0\x01\x5e\x83\xc4\x08\xc3",
    re.S)


HUD_SCALE_SIG = re.compile(
    rb"\x56(?P<site>\xe8....)\x83\xc4\x04\x6a\x08\x68....\x68\x47\x80\xff\xff\x8b\xce\xe8...."
    rb"\x6a\x20\x68....\x68\x40\x80\xff\xff\x8b\xce\xe8...."
    rb"\x6a\x20\x68....\x68\x41\x80\xff\xff\x8b\xce\xe8....", re.S,
)

OPTIONS_SCALE_SIG = re.compile(
    rb"\x8b\x4c\x24\x10\x51\x8b\xce\xe8....\x6a\x01\x8b\xce"
    rb"(?P<site>\xe8....)\x33\xd2\x66\x8b\x15", re.S,
)

LEVELUP_SCALE_SIG = re.compile(
    rb"\x85\xc0\x7e\x09\x8b\xcf\x6a\x01\xe8....\x6a\x01\x8b\xce"
    rb"(?P<site>\xe8....)\x6a\x01\x8b\xce\xe8....\x5f\x5e\x5d\x5b\xc3", re.S,
)

MESSAGE_SCALE_SIG = re.compile(
    rb"\x68....\x68\x86\x80\xff\xff\x8b\xce\xe8....\x6a\x01\x8b\xce"
    rb"(?P<site>\xe8....)", re.S,
)

NAME_SCALE_SIG = re.compile(
    rb"\x8b\xce\xe8....\x56\x6a\x00\x6a\x00\x6a\x00\x56\xe8...."
    rb"\x83\xc4\x14\x6a\x01\x8b\xce(?P<site>\xe8....)\x5e\xc3", re.S,
)

RACE_SCALE_SIG = re.compile(
    rb"\xbd\x01\x00\x00\x00\x55\x8b\xce(?P<site>\xe8....)\x53\x68....\x68\xdc\x00\x00\x00",
    re.S,
)

CLASS_CHOICE_SCALE_SIG = re.compile(
    rb"\x85\xf6\x74.\x8b\xce\xe8....\x6a\x01\x8b\xce(?P<site>\xe8....)\x33\xc9\x66\x8b\x0d",
    re.S,
)

BIRTHSIGN_SCALE_SIG = re.compile(
    rb"\x83\xc4\x14\x6a\x01\x8b\xce(?P<site>\xe8....)\x8d\x4c\x24\x20\xc7\x44\x24\x3c\xff\xff\xff\xff",
    re.S,
)


def find_hud_scale(x):
    """The one MenuMulti builder call, followed by its three handler registrations."""
    sec = text_section(x)
    body = bytes(x.data[sec.raw:sec.raw + sec.rsize])
    hits = list(HUD_SCALE_SIG.finditer(body))
    if len(hits) != 1:
        raise PatchError(f"ui-scale: {len(hits)} MenuMulti builder calls, expected 1")
    return sec.va + hits[0].start("site")


def find_options_scale(x):
    """MenuOptions::open's first layout, after final width and height are set."""
    sec = text_section(x)
    body = bytes(x.data[sec.raw:sec.raw + sec.rsize])
    hits = list(OPTIONS_SCALE_SIG.finditer(body))
    if len(hits) != 1:
        raise PatchError(f"ui-scale: {len(hits)} MenuOptions layout calls, expected 1")
    return sec.va + hits[0].start("site")


def find_levelup_scale(x):
    """MenuLevelUp's first layout, after content is built and updated."""
    sec = text_section(x)
    body = bytes(x.data[sec.raw:sec.raw + sec.rsize])
    hits = list(LEVELUP_SCALE_SIG.finditer(body))
    if len(hits) != 1:
        raise PatchError(f"ui-scale: {len(hits)} MenuLevelUp layout calls, expected 1")
    return sec.va + hits[0].start("site")


def find_message_scale(x):
    """Message box first layout, after its text and buttons are built."""
    sec = text_section(x)
    body = bytes(x.data[sec.raw:sec.raw + sec.rsize])
    hits = list(MESSAGE_SCALE_SIG.finditer(body))
    if len(hits) != 1:
        raise PatchError(f"ui-scale: {len(hits)} message layout calls, expected 1")
    return sec.va + hits[0].start("site")


def find_name_scale(x):
    """MenuName's final layout after its keyboard is populated."""
    sec = text_section(x)
    body = bytes(x.data[sec.raw:sec.raw + sec.rsize])
    hits = list(NAME_SCALE_SIG.finditer(body))
    if len(hits) != 1:
        raise PatchError(f"ui-scale: {len(hits)} name keyboard layout calls, expected 1")
    return sec.va + hits[0].start("site")


def find_chargen_scale(x):
    """Final layout calls of the race, class choice and birthsign menus."""
    sec = text_section(x)
    body = bytes(x.data[sec.raw:sec.raw + sec.rsize])
    sites = []
    for label, sig in (("race", RACE_SCALE_SIG), ("class choice", CLASS_CHOICE_SCALE_SIG),
                       ("birthsign", BIRTHSIGN_SCALE_SIG)):
        hits = list(sig.finditer(body))
        if len(hits) != 1:
            raise PatchError(f"ui-scale: {len(hits)} {label} layout calls, expected 1")
        sites.append(sec.va + hits[0].start("site"))
    return tuple(sites)


def find_menu_area_scale(x):
    """EnsureMenuMulti's cache writes and the epilogue after its final layout."""
    init = find_hud_scale(x) - 8
    if bytes(x.data[x.va_to_off(init):x.va_to_off(init) + 8]) != \
            b"\x51\x56\x8b\x74\x24\x0c\x57\x56":
        raise PatchError("ui-scale: MenuMulti initializer changed")
    calls = find_call_sites(x, init)
    if len(calls) != 1:
        raise PatchError(f"ui-scale: {len(calls)} EnsureMenuMulti initializer calls, expected 1")
    start = calls[0] - 0x40
    body = bytes(x.data[x.va_to_off(start):x.va_to_off(start) + 0x500])
    if body[:3] != b"\x51\x8b\x0d" or body[0x251] != 0xA3:
        raise PatchError("ui-scale: EnsureMenuMulti cache changed")
    area = struct.unpack_from("<I", body, 0x252)[0]
    if (body[0x277] != 0xA3 or body[0x282] != 0xA3 or
            body[0x28D:0x28F] != b"\x89\x0d" or
            any(struct.unpack_from("<I", body, offset)[0] != area + delta
                for offset, delta in ((0x278, 4), (0x283, 8), (0x28F, 12)))):
        raise PatchError("ui-scale: MenuMulti cache fields changed")
    tail = b"\x5f\x5e\x5b\x59\xc3"
    hits = [start + m.start() for m in re.finditer(re.escape(tail), body)]
    if len(hits) != 1 or hits[0] <= calls[0]:
        raise PatchError(f"ui-scale: {len(hits)} EnsureMenuMulti epilogues, expected 1")
    return hits[0], area


def find_dash_button(x):
    """The call that builds the engine's main and pause menu, then the call that sets the built
    menu's height."""
    data = bytes(x.data)
    hits = list(DASH_BUTTON_SIG.finditer(data))
    heights = list(DASH_HEIGHT_SIG.finditer(data))
    if len(hits) != 1 or len(heights) != 1:
        raise PatchError("dash-button: %d MenuOptions::build call(s) and %d height call(s), "
                         "expected 1 each" % (len(hits), len(heights)))
    sites = [x.off_to_va(hits[0].start("site")), x.off_to_va(heights[0].start("site"))]
    if None in sites:
        raise PatchError("dash-button: a call is outside any section")
    return sites


def find_topic_colors(x):
    """The topic list filter, the two response script calls, the engine's getFilteredInfo and
    the three callers of UI::updateTopicsList."""
    data = bytes(x.data)
    sites = []
    for what, sig in (("list", TOPIC_LIST_SIG), ("response", TOPIC_RUN_SIG),
                      ("filter", TOPIC_FILTER_SIG), ("click", TOPIC_CLICK_SIG)) + tuple(
                          ("update", sig) for sig in TOPIC_UPDATE_SIGS):
        hits = list(sig.finditer(data))
        if len(hits) != 1:
            raise PatchError("topic-colors: %d %s call(s), expected 1" % (len(hits), what))
        sites.append(x.off_to_va(hits[0].start("site")))
    if None in sites:
        raise PatchError("topic-colors: a call is outside any section")
    return sites


def find_topic_state(x):
    """Every call to and registration of the text button's state-color handler, and the two
    calls it ends with that push the color to the scene (repaint, update)."""
    data = bytes(x.data)
    calls = [x.off_to_va(m.start("site")) for m in TOPIC_STATE_CALL_SIG.finditer(data)]
    pushes = [x.off_to_va(m.start("site")) for m in TOPIC_STATE_PUSH_SIG.finditer(data)]
    tails = list(TOPIC_STATE_TAIL_SIG.finditer(data))
    targets = {tes3x_inject.call_target(x, c) for c in calls}
    ends = {(tes3x_inject.call_target(x, x.off_to_va(m.start("paint"))),
             tes3x_inject.call_target(x, x.off_to_va(m.start("update")))) for m in tails}
    if not calls or not pushes or None in calls + pushes or len(targets) != 1 or len(ends) != 1:
        raise PatchError("topic-colors: %d state call(s) to %d target(s), %d registration(s), "
                         "%d repaint pair(s)" % (len(calls), len(targets), len(pushes), len(ends)))
    handler = targets.pop()
    pushes = [p for p in pushes
              if struct.unpack_from("<I", data, x.va_to_off(p) + 1)[0] == handler]
    if not pushes:
        raise PatchError("topic-colors: the state handler is never registered")
    return calls, pushes, ends.pop()


def find_menu_still(x):
    """The three LoadScreenManager::open calls, the renderer's clear slot in its vtable, and the
    clear and display methods."""
    data = bytes(x.data)
    calls = []
    for what, sig in (("menu", MENU_STILL_MENU_SIG), ("new game", MENU_STILL_NEW_SIG),
                      ("load", MENU_STILL_LOAD_SIG)):
        hits = list(sig.finditer(data))
        if len(hits) != 1:
            raise PatchError("menu-still: %d %s open call(s), expected 1" % (len(hits), what))
        calls.append(x.off_to_va(hits[0].start("site")))
    _, clear, display = find_boot_clear(x)
    pair = struct.pack("<II", display, clear)
    hits = [m.start() for m in re.finditer(re.escape(pair), data)]
    if len(hits) != 1:
        raise PatchError("menu-still: %d renderer vtables, expected 1" % len(hits))
    slot = x.off_to_va(hits[0] + 4)
    waits = list(MENU_STILL_FENCE_SIG.finditer(data))
    if len(waits) != 1:
        raise PatchError("menu-still: %d swap fence waits, expected 1" % len(waits))
    fence = x.off_to_va(waits[0].start("fence"))
    block = x.off_to_va(waits[0].start("block"))
    if None in calls or None in (slot, fence, block):
        raise PatchError("menu-still: a site is outside any section")
    return calls + [slot, clear, display, fence, block]


def find_hd_video(x):
    """The renderer and CreateDevice calls, and the D3D mode count and enumeration functions."""
    data = bytes(x.data)
    renderer = list(VIDEO_RENDERER_SIG.finditer(data))
    create = list(VIDEO_CREATE_SIG.finditer(data))
    modes = list(VIDEO_MODES_SIG.finditer(data))
    if len(renderer) != 1 or len(create) != 1 or len(modes) != 1:
        raise PatchError("hd-video: found %d renderer calls, %d CreateDevice calls and %d mode "
                         "lists, expected 1" % (len(renderer), len(create), len(modes)))
    sites = [x.off_to_va(m.start(g)) for m, g in ((renderer[0], "site"), (create[0], "site"),
                                                  (modes[0], "count"), (modes[0], "enum"))]
    if None in sites:
        raise PatchError("hd-video: a call is outside any section")
    return (sites[0], sites[1], tes3x_inject.call_target(x, sites[2]),
            tes3x_inject.call_target(x, sites[3]))


def find_mcp146(x):
    """The attacking/casting guard in MobilePlayer's Xbox input loop."""
    hits = list(MCP146_SIG.finditer(bytes(x.data)))
    if len(hits) != 1:
        raise PatchError("mcp-146: %d input guard(s), expected 1" % len(hits))
    site = x.off_to_va(hits[0].start("site"))
    if site is None:
        raise PatchError("mcp-146: input guard is outside any section")
    return site


def find_mcp47_context(x):
    """Deselect call, original target, world pointer, and player getter in unequipItem."""
    hits = list(MCP47_SIG.finditer(bytes(x.data)))
    if len(hits) != 1:
        raise PatchError("mcp-47: %d unequip deselection paths, expected 1" % len(hits))
    hit = hits[0]
    site = x.off_to_va(hit.start("site"))
    player_call = x.off_to_va(hit.start("player"))
    if site is None or player_call is None:
        raise PatchError("mcp-47: call is outside any section")
    return (site, tes3x_inject.call_target(x, site),
            struct.unpack("<I", hit.group("world"))[0],
            tes3x_inject.call_target(x, player_call))


def find_mcp25_blind(x):
    """Find the inverted Blind magnitude passed to the player's effect handler."""
    sig = re.compile(
        rb"\x8b\x54\x24\x08\x8b\x0d....\x6a\x00(?P<neg>\xf7\xda)"
        rb"\x52\x6a\x15\xe8....\x8b\xc8\xe8....\x5f\x5e\x59\xc3",
        re.S,
    )
    hits = list(sig.finditer(x.data))
    if len(hits) != 1:
        raise PatchError(f"mcp-25: {len(hits)} Blind player paths, expected 1")
    site = x.off_to_va(hits[0].start("neg"))
    if site is None:
        raise PatchError("mcp-25: Blind player path is outside any section")
    return site


def find_mcp48_context(x):
    """The two level-up multiplier dispatch tables and the count-three label path."""
    data = bytes(x.data)
    dispatch = re.compile(rb"\x83\xf8\x09\x0f\x87....\xff\x24\x85(?P<table>....)", re.S)
    found = []
    for hit in dispatch.finditer(data):
        table = struct.unpack("<I", hit.group("table"))[0]
        table_off = x.va_to_off(table)
        site = x.off_to_va(hit.start())
        if site is None or table_off is None or table_off + 40 > len(data):
            continue
        entries = struct.unpack_from("<10I", data, table_off)
        if all(x.va_to_off(entry) is not None for entry in entries):
            found.append((site, table, entries))
    pairs = [(left, right) for left in found for right in found
             if 0 < right[0] - left[0] < 0x800 and right[1] == left[1] + 40
             and left[2][0] == right[2][0]]
    if len(pairs) != 1:
        raise PatchError(f"mcp-48: {len(pairs)} level-up dispatch pairs, expected 1")
    left, right = pairs[0]
    guard = left[0] - 0x14
    if data[x.va_to_off(guard):x.va_to_off(guard) + 2] != b"\x0f\x8d":
        raise PatchError("mcp-48: first attribute cap branch changed")
    case_three = right[2][3]
    case_four = right[2][4]
    case_data = data[x.va_to_off(case_three):x.va_to_off(case_four)]
    label = re.compile(rb"\x8b\x0d....\x68\x4f\x04\x00\x00(?P<call>\xe8....)\x50\x68....\x57")
    calls = list(label.finditer(case_data))
    if len(calls) != 1:
        raise PatchError("mcp-48: count-three label fallthrough changed")
    call = case_three + calls[0].start("call")
    case_one = right[2][1]
    case_two = right[2][2]
    one_data = data[x.va_to_off(case_one):x.va_to_off(case_two)]
    jumps = list(re.finditer(rb"\x68\x4d\x04\x00\x00(?P<jump>\xe9....)", one_data, re.S))
    if len(jumps) != 1:
        raise PatchError("mcp-48: count-one label jump changed")
    jump = case_one + jumps[0].start("jump")
    tail = jump + 5 + struct.unpack_from("<i", data, x.va_to_off(jump) + 1)[0]
    tail_off = x.va_to_off(tail)
    if tail_off is None or tail >= right[2][0] or data[tail_off:tail_off + 2] != b"\x8b\x0d":
        raise PatchError("mcp-48: shared multiplier label path changed")
    return guard, left[1], left[2], right[2], call, tail


def find_mcp132_context(x):
    """The script animation calls, humanoid bone test and player idle update."""
    play = find_multi_signature(x, "ANIM_PLAY_GROUP")
    destroy = find_multi_signature(x, "ANIM_DATA_DESTROY")
    update = find_multi_signature(x, "UPDATE_ALL_IDLES")
    ref_update = find_multi_signature(x, "ANIM_IDLE_UPDATE")
    run = find_run_function(x)
    script = [site for site in find_call_sites(x, play)
              if run <= site < run + 0x10000]
    if len(script) != 2 or any(bytes(x.data[x.va_to_off(site) - 2:x.va_to_off(site)])
                               != b"\x8b\xc8" for site in script):
        raise PatchError("mcp-132: expected two PlayGroup/LoopGroup script calls")
    bones = list(re.finditer(rb"\x52\x55\xe8....\x83\xc4\x08\x85\xc0\x74",
                            bytes(x.data), re.S))
    if len(bones) != 1:
        raise PatchError("mcp-132: expected one humanoid bone comparison")
    bone = x.off_to_va(bones[0].start() + 2)
    idle_calls = find_call_sites(x, update)
    if len(idle_calls) != 1:
        raise PatchError("mcp-132: expected one main-loop UpdateAllIdles call")
    destroy_calls = find_call_sites(x, destroy)
    if len(destroy_calls) != 2:
        raise PatchError("mcp-132: expected two AnimationData destructor calls")
    _site, _deselect, world, get_player = find_mcp47_context(x)
    return {"play": play, "script": script, "bone": bone, "update": update,
            "idle_call": idle_calls[0], "ref_update": ref_update,
            "destroy": destroy, "destroy_calls": destroy_calls,
            "world": world, "get_player": get_player}


def find_mcp146_context(x):
    """The original guard, input query, game pointer and post-Ready-Weapon continuation."""
    data = bytes(x.data)
    ready = list(MCP146_READY_SIG.finditer(data))
    resume = list(MCP146_RESUME_SIG.finditer(data))
    if len(ready) != 1 or len(resume) != 1:
        raise PatchError("mcp-146: found %d Ready Weapon handlers and %d continuations, expected 1"
                         % (len(ready), len(resume)))
    input1 = x.off_to_va(ready[0].start("input"))
    input2 = x.off_to_va(ready[0].start("input2"))
    if input1 is None or input2 is None:
        raise PatchError("mcp-146: an input call is outside any section")
    target = tes3x_inject.call_target(x, input1)
    if tes3x_inject.call_target(x, input2) != target:
        raise PatchError("mcp-146: Ready Weapon input calls have different targets")
    resume_va = x.off_to_va(resume[0].start("resume"))
    if resume_va is None:
        raise PatchError("mcp-146: continuation is outside any section")
    game = struct.unpack("<I", ready[0].group("game"))[0]
    return find_mcp146(x), target, game, resume_va


def find_bow_view(x):
    """The first-person transform call in MobilePlayer::updateScenegraph."""
    hits = list(BOW_VIEW_SIG.finditer(bytes(x.data)))
    if len(hits) != 1:
        raise PatchError("bow-view: %d first-person transform call(s), expected 1" % len(hits))
    site = x.off_to_va(hits[0].start("site"))
    if site is None:
        raise PatchError("bow-view: transform call is outside any section")
    return site


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


def find_mcp98_refcounts(x):
    """Find the animated-container retain, release and common return."""
    hits = list(MCP98_REFCOUNT_SIG.finditer(bytes(x.data)))
    if len(hits) != 1:
        raise PatchError("mcp-98: %d animated-container path(s), expected 1" % len(hits))
    match = hits[0]
    offsets = tuple(match.start(name) for name in ("retain", "release", "exit"))
    vas = tuple(x.off_to_va(off) for off in offsets)
    if any(va is None for va in vas):
        raise PatchError("mcp-98: animated-container path is outside any section")
    guard = match.start("guard")
    target = guard + 2 + struct.unpack("<b", match.group("guard")[1:2])[0]
    if target != offsets[2]:
        raise PatchError("mcp-98: release guard does not target the common return")
    return vas


def find_mcp92_unsummon(x):
    """Find the unsummon cleanup call and MobileActor::retireMagic wrapper."""
    sites = list(MCP92_UNSUMMON_SIG.finditer(bytes(x.data)))
    targets = list(MCP92_RETIRE_MAGIC_SIG.finditer(bytes(x.data)))
    if len(sites) != 1 or len(targets) != 1:
        raise PatchError("mcp-92: %d unsummon path(s), %d retire-magic wrapper(s), expected 1 each"
                         % (len(sites), len(targets)))
    site = x.off_to_va(sites[0].start("site"))
    target = x.off_to_va(targets[0].start())
    if site is None or target is None:
        raise PatchError("mcp-92: cleanup path is outside any section")
    return site, target


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


def find_mcp3_unarmored(x):
    """Find the fully-unarmored early exit in physical damage calculation."""
    hits = list(MCP3_UNARMORED_SIG.finditer(bytes(x.data)))
    if len(hits) != 1:
        raise PatchError("mcp-3: %d unarmored branch(es), expected 1" % len(hits))
    va = x.off_to_va(hits[0].start("site"))
    if va is None:
        raise PatchError("mcp-3: unarmored branch is outside any section")
    return va


def find_dialogue_merge(x):
    """Find the store that replaces a repeated topic's INFO head."""
    hits = list(DIALOGUE_MERGE_SIG.finditer(bytes(x.data)))
    if len(hits) != 1:
        raise PatchError("dialogue-merge: %d INFO head store(s), expected 1" % len(hits))
    va = x.off_to_va(hits[0].start("site"))
    if va is None:
        raise PatchError("dialogue-merge: INFO head store is outside any section")
    return va


def find_mcp123_add(x):
    """Find the shared PlaceItem call to Cell::addReference."""
    hits = list(MCP123_ADD_SIG.finditer(bytes(x.data)))
    if len(hits) != 1:
        raise PatchError("mcp-123: %d PlaceItem insertion call(s), expected 1" % len(hits))
    va = x.off_to_va(hits[0].start("site"))
    if va is None:
        raise PatchError("mcp-123: PlaceItem insertion call is outside any section")
    return va


MCP149_MENU_SIG = re.compile(
    rb"(?P<site>\x8a\x86\xdd\x00\x00\x00)\x3c\x13\x5e\x74\x04"
    rb"\x3c\x12\x75\x07\xc6\x05(?P<flag>....)\x01\xc3", re.S,
)


def find_mcp149_menu(x):
    """Find the contents menu's actor state gate and dispose flag."""
    hits = list(MCP149_MENU_SIG.finditer(bytes(x.data)))
    if len(hits) != 1:
        raise PatchError(f"mcp-149: {len(hits)} contents menu gates, expected 1")
    site = x.off_to_va(hits[0].start("site"))
    if site is None:
        raise PatchError("mcp-149: contents menu gate is outside an XBE section")
    return site, struct.unpack("<I", hits[0].group("flag"))[0]


def find_mcp6_context(x):
    """Find the attribute effect paths and their shared mobile lookup."""
    data = bytes(x.data)

    def one(pattern, label):
        hits = list(pattern.finditer(data))
        if len(hits) != 1:
            raise PatchError(f"mcp-6: {len(hits)} {label} matches, expected 1")
        va = x.off_to_va(hits[0].start("site"))
        if va is None:
            raise PatchError(f"mcp-6: {label} is outside an XBE section")
        return va

    effects = list(MCP6_EFFECT_SIG.finditer(data))
    if len(effects) != 2 or {m.group("kind") for m in effects} != {b"\x02", b"\x1c"}:
        raise PatchError("mcp-6: expected one Drain and one Restore attribute path")
    effect_sites = {m.group("kind")[0]: x.off_to_va(m.start("site")) for m in effects}
    reset = one(MCP6_RESET_SIG, "controller reset")
    fortify = one(MCP6_FORTIFY_SIG, "Fortify attribute")
    drain_post = one(MCP6_DRAIN_POST_SIG, "Drain cleanup")
    restore = effect_sites[0x1c]
    restore_posts = [x.off_to_va(m.start("site")) for m in MCP6_RESTORE_POST_SIG.finditer(data)]
    restore_posts = [site for site in restore_posts if site is not None and 0 < site - restore < 0x80]
    if len(restore_posts) != 1 or any(site is None for site in effect_sites.values()):
        raise PatchError("mcp-6: expected one Restore cleanup inside its attribute handler")
    lookups = [tes3x_inject.call_target(x, site)
               for site in (effect_sites[0x02], restore, fortify)]
    if len(set(lookups)) != 1 or not (effect_sites[0x02] < drain_post < effect_sites[0x02] + 0x80):
        raise PatchError("mcp-6: attribute handlers disagree on their mobile lookup")
    return {"reset": reset, "drain": effect_sites[0x02], "restore": restore,
            "fortify": fortify, "drain_post": drain_post,
            "restore_post": restore_posts[0], "get_mobile": lookups[0]}


# Wander and Flee read the cell's pathgrid, then pick a random node from it.
PATHGRID_FLEE_SIG = re.compile(
    rb"(?P<site>\xe8....)\x85\xc0\x0f\x84....\x8b\x68\x24\x8d\x78\x20", re.S,
)
PATHGRID_WANDER_SIG = re.compile(
    rb"\x3a\xc3\x74.\xe8....\x8b\xc8(?P<site>\xe8....)\x85\xc0\x89\x46\x54\x74", re.S,
)


def find_pathgrid_lookups(x):
    """Find the Flee and Wander calls to the cell pathgrid getter."""
    data = bytes(x.data)
    sites = []
    for label, pattern in (("Flee", PATHGRID_FLEE_SIG), ("Wander", PATHGRID_WANDER_SIG)):
        hits = list(pattern.finditer(data))
        if len(hits) != 1:
            raise PatchError("mwse-pathgrid-zero: %d %s lookups, expected 1" % (len(hits), label))
        site = x.off_to_va(hits[0].start("site"))
        target = tes3x_inject.call_target(x, site) if site is not None else None
        off = x.va_to_off(target) if target is not None else None
        if off is None or bytes(x.data[off:off + 4]) != b"\x8b\x41\x70\xc3":
            raise PatchError("mwse-pathgrid-zero: %s lookup is not the pathgrid getter" % label)
        sites.append(site)
    return sites


# Inventory removal of one item: takes the item's weight off the actor's encumbrance, then looks
# for the stack. The weight must come off only once the stack is found.
MCP160_SIG = re.compile(
    rb"(?P<load>\x8b\x6c\x24\x1c)"
    rb"(?P<weight>\x85\xed\x74\x3b\x8b\x03\x6a\x00\x6a\x00\x6a\x00\x8b\xcb"
    rb"\x8d\xb5\xcc\x02\x00\x00\xff\x90\x9c\x00\x00\x00\xd8\x0d....\x51\x8b\xce"
    rb"\xd9\x1c\x24\xe8(?P<mod>....)\x8b\x0d....\xe8(?P<player>....)"
    rb"\x3b\xe8\x75\x05\xe8(?P<update>....))"
    rb"(?P<loop>\x83\xc7\x04\x8b\xcf\xe8(?P<first>....)\x85\xc0\x0f\x84(?P<done>....)"
    rb"\x8b\x70\x08\x39\x5e\x04\x74\x10\x8b\xcf\xe8(?P<next>....)\x85\xc0\x75\xed\xe9(?P<exit>....))"
    rb"\x8b\x0e", re.S,
)
MCP160_RELOCS = ("mod", "player", "update", "first", "done", "next", "exit")


def find_mcp160_removal(x):
    """Find the item removal: its address, the bytes to reorder and their relative operands."""
    data = bytes(x.data)
    hits = list(MCP160_SIG.finditer(data))
    if len(hits) != 1:
        raise PatchError("mcp-160: %d item removals, expected 1" % len(hits))
    m = hits[0]
    start = x.off_to_va(m.start())
    if start is None:
        raise PatchError("mcp-160: item removal is outside an XBE section")
    relocs = {name: (m.start(name) - m.start(),
                     start + m.start(name) - m.start() + 4 + struct.unpack("<i", m.group(name))[0])
              for name in MCP160_RELOCS}
    return start, m.group("load"), m.group("weight"), m.group("loop"), relocs


# Enable and Disable set a flag in the running script's local variables, which a script without
# any does not have.
ENABLE_FLAG_SIG = re.compile(rb"(?P<site>\xa1(?P<locals>....)\x80\x60\x0c\xfe)", re.S)
DISABLE_FLAG_SIG = re.compile(rb"(?P<site>\xa1(?P<locals>....)\x80\x48\x0c\x01)", re.S)


def find_enable_disable_flags(x):
    """Find the Enable and Disable flag writes and the global holding the script's locals."""
    data = bytes(x.data)
    found = []
    for label, pattern in (("Enable", ENABLE_FLAG_SIG), ("Disable", DISABLE_FLAG_SIG)):
        hits = list(pattern.finditer(data))
        if len(hits) != 1:
            raise PatchError("mwse-enable-disable-vars: %d %s flag writes, expected 1"
                             % (len(hits), label))
        site = x.off_to_va(hits[0].start("site"))
        if site is None:
            raise PatchError("mwse-enable-disable-vars: %s is outside an XBE section" % label)
        found.append((site, struct.unpack("<I", hits[0].group("locals"))[0]))
    if found[0][1] != found[1][1]:
        raise PatchError("mwse-enable-disable-vars: Enable and Disable disagree on the locals")
    return found[0][0], found[1][0], found[0][1]


def find_mcp173_rank(x):
    """Find the save-flag calls before the two NPC rank writes."""
    hits = list(MCP173_RANK_SIG.finditer(bytes(x.data)))
    if len(hits) != 2:
        raise PatchError("mcp-173: %d NPC rank save calls, expected 2" % len(hits))
    sites = [x.off_to_va(m.start("site")) for m in hits]
    if any(site is None for site in sites):
        raise PatchError("mcp-173: rank save call is outside any section")
    return sites


def find_mcp125_context(x):
    """Find the two Position exits, shared attachment tail and collision add/remove methods."""
    data = bytes(x.data)

    def one(signature, label):
        hits = list(signature.finditer(data))
        if len(hits) != 1:
            raise PatchError("mcp-125: %d %s match(es), expected 1" % (len(hits), label))
        return hits[0]

    a = one(MCP125_POSITION_A_SIG, "first Position path")
    b = one(MCP125_POSITION_B_SIG, "second Position path")
    attach = one(MCP125_ATTACH_SIG, "script attachment tail")
    collision = one(MCP125_COLLISION_SIG, "collision branch")
    add = one(MCP125_ADD_MOB_SIG, "add-mob path")

    calls = []
    for match in (a, b, attach):
        va = x.off_to_va(match.start("call"))
        if va is None:
            raise PatchError("mcp-125: move call is outside any section")
        calls.append(tes3x_inject.call_target(x, va))
    if len(set(calls)) != 1:
        raise PatchError("mcp-125: Position paths do not share one move helper")
    if a.group("ref") != b.group("ref") or a.group("coords") != b.group("coords"):
        raise PatchError("mcp-125: Position paths do not share reference and coordinate globals")
    if a.group("coords") != attach.group("coords"):
        raise PatchError("mcp-125: attachment path uses a different coordinate global")
    if collision.group("manager") != add.group("manager"):
        raise PatchError("mcp-125: collision paths use different mob managers")

    vas = {
        "positions": [x.off_to_va(a.start("site")), x.off_to_va(b.start("site"))],
        "resume": x.off_to_va(attach.start("resume")),
        "collision": x.off_to_va(collision.start("site")),
        "move": calls[0],
        "remove": tes3x_inject.call_target(x, x.off_to_va(collision.start("remove"))),
        "add": tes3x_inject.call_target(x, x.off_to_va(add.start("add"))),
        "manager": struct.unpack("<I", collision.group("manager"))[0],
        "ref": struct.unpack("<I", a.group("ref"))[0],
        "coords": struct.unpack("<I", a.group("coords"))[0],
        "angles": [struct.unpack("<I", a.group("angle"))[0],
                   struct.unpack("<I", b.group("angle"))[0]],
    }
    if any(va is None for va in vas["positions"] + [vas["resume"], vas["collision"]]):
        raise PatchError("mcp-125: a patch site is outside any section")
    return vas


def find_mcp125_collision(x):
    """Find the moved-reference collision branch patched by mcp-125."""
    return find_mcp125_context(x)["collision"]


def find_mcp37_context(x):
    """Find the cell-change hook and engine state its stale-cast walk needs."""
    hits = list(MCP37_SITE_SIG.finditer(bytes(x.data)))
    if len(hits) != 1:
        raise PatchError("mcp-37: %d cell-change site(s), expected 1" % len(hits))
    match = hits[0]
    site = x.off_to_va(match.start("site"))
    player_call = x.off_to_va(match.start() + 6)
    if site is None or player_call is None:
        raise PatchError("mcp-37: cell-change site is outside any section")
    tree = find_unique(x.data, MCP37_TREE_NEXT_SIG, "mcp-37 tree iterator")
    tree = x.off_to_va(tree)
    if tree is None:
        raise PatchError("mcp-37: tree iterator is outside any section")
    game = struct.unpack("<I", match.group("game"))[0]
    return site, game, tes3x_inject.call_target(x, player_call), tree


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
    buffers = {struct.unpack("<I", match.group("buf"))[0] for match in init}
    if len(buffers) != 1:
        raise PatchError("rotating-autosaves: %d name buffers from %d initializers, expected 1"
                         % (len(buffers), len(init)))
    buf_va = buffers.pop()

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


# Quicksave is gated on the chargen state before the input flag can reach SaveGame. The state is
# reached through two fields rather than an absolute address: Game -> owner -> float state.
SAVE_ALLOWED_SIG = re.compile(
    rb"\x8b\x8e(?P<owner>....)\xd9\x41(?P<state>.)\xe8....\x83\xf8\xff\x75", re.S)


def find_save_allowed_context(x):
    """The owner global and fields used by retail's chargen save gate."""
    hits = list(SAVE_ALLOWED_SIG.finditer(bytes(x.data)))
    if len(hits) != 1:
        raise PatchError("save gate: %d matches, expected 1" % len(hits))
    start = max(0, hits[0].start() - 0x100)
    owners = {struct.unpack("<I", match.group(1))[0] for match in
              re.finditer(rb"\x8b\x35(....)", bytes(x.data[start:hits[0].start()]), re.S)}
    if len(owners) != 1:
        raise PatchError("save gate: %d owner globals, expected 1" % len(owners))
    return (owners.pop(), struct.unpack("<I", hits[0].group("owner"))[0],
            hits[0].group("state")[0])


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


# WorldController::getMobilePlayer: the mob controller at +0x5C, a null check, then a tail jump.
MOBILE_PLAYER_SIG = bytes([
    0x8B, 0x49, 0x5C,        # mov ecx,[ecx+0x5C]
    0x85, 0xC9,              # test ecx,ecx
    0x75, 0x03,              # jne +3
    0x33, 0xC0, 0xC3,        # xor eax,eax; ret
    0xE9,                    # jmp MobController::getMobilePlayer
])


def find_world_controller(x):
    """The WorldController pointer: the global most getMobilePlayer calls load into ecx."""
    off = find_unique(x.data, MOBILE_PLAYER_SIG, "WorldController::getMobilePlayer")
    sites = find_call_sites(x, x.off_to_va(off))
    loads = {}
    for site in sites:
        at = x.va_to_off(site)
        if at is not None and x.data[at - 6:at - 4] == b"\x8b\x0d":
            va = struct.unpack_from("<I", x.data, at - 4)[0]
            loads[va] = loads.get(va, 0) + 1
    if not loads:
        raise PatchError("WorldController: no getMobilePlayer call loads a global")
    va = max(loads, key=loads.get)
    if loads[va] * 2 < len(sites):
        raise PatchError("WorldController: 0x%08X feeds %d of %d getMobilePlayer calls"
                         % (va, loads[va], len(sites)))
    return va


MENU_GATE_RE = re.compile(rb"\x8a\x86\xd2\x00\x00\x00\x84\xc0\x0f\x85...."
                          rb"\x8b\x0d....\x6a\x00\x6a\x00", re.S)


def find_menu_mode_gate(x):
    """The 6-byte jne in mainLoopBeforeInput that skips the world update while a menu is open:
    `mov al,[esi+0xD2]` (WorldController menu mode), `test al,al`, `jne`, then the DataHandler
    update."""
    hits = [m.start() for m in MENU_GATE_RE.finditer(x.data)]
    if len(hits) != 1:
        raise PatchError("menu mode gate: %d match(es), expected exactly 1" % len(hits))
    return x.off_to_va(hits[0] + 8)


MOB_GATE_RE = re.compile(rb"\x8a\x81\xd2\x00\x00\x00\x84\xc0\x0f\x85....\x84\xdb\x0f\x85...."
                         rb"\x8b\x51\x2c\x8b\x49\x5c", re.S)


def find_mob_gate(x):
    """The 6-byte jne in Game::Update that skips MobManager::ProcessMobs, idles, cell loading and
    weather while a menu is open."""
    hits = [m.start() for m in MOB_GATE_RE.finditer(x.data)]
    if len(hits) != 1:
        raise PatchError("mob update gate: %d match(es), expected exactly 1" % len(hits))
    return x.off_to_va(hits[0] + 8)


WEATHER_ROLL_RE = re.compile(rb"\x8b\x79\x4c\x8b\xcf\xe8....\x85\xc0\x74.\x8d\x9b\x00\x00\x00\x00"
                             rb"\x8b\x48\x08\xe8....\x8b\xcf\xe8....\x85\xc0\x75", re.S)


def find_weather_roll(x):
    """The 5-byte call to Region::randomizeWeather in updateEnvironmentLightingWeather's loop over
    every region, which runs when the hours between weather changes have passed."""
    hits = [m.start() for m in WEATHER_ROLL_RE.finditer(x.data)]
    if len(hits) != 1:
        raise PatchError("weather roll: %d match(es), expected exactly 1" % len(hits))
    return x.off_to_va(hits[0] + 23)


BUTTON_PRESSED_RE = re.compile(rb"\xa1(....)\xc3(?:\x90|\xcc)*\xc7\x05\1\xff\xff\xff\xff\xc3", re.S)


def find_button_pressed(x):
    """The message box button GetButtonPressed reads, -1 until one is pressed: the global of the
    getter `mov eax,[X]; ret` that is followed by its reset `mov [X],-1; ret`."""
    hits = [m for m in BUTTON_PRESSED_RE.finditer(x.data)]
    if len(hits) != 1:
        raise PatchError("button pressed index: %d match(es), expected exactly 1" % len(hits))
    return struct.unpack("<I", hits[0].group(1))[0]


FIND_MARKER_RE = re.compile(rb"\x83\xec\x0c\x56\x57\x8b\x7c\x24\x18\x8b\xf1\x8b\x0d(....)\x33\xc0"
                            rb"\x3b\xf9\x74.\x3b\x3d(....)\x0f\x85", re.S)


def find_marker(x):
    """DataHandler's closest TempleMarker or DivineMarker reference to the player (the Intervention
    spells' search): the function, then the globals holding the two marker objects."""
    hits = list(FIND_MARKER_RE.finditer(x.data))
    if len(hits) != 1:
        raise PatchError("find marker: %d match(es), expected exactly 1" % len(hits))
    return (x.off_to_va(hits[0].start()), struct.unpack("<I", hits[0].group(1))[0],
            struct.unpack("<I", hits[0].group(2))[0])


PROFILE_LIST_SITES = 8


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
    # An earlier patch (link-name-arena) may have replaced the prologue with a jmp.
    start = max(data.rfind(b"\xcc\x55\x8b\xec", max(0, push - 0x400), push),
                data.rfind(b"\xcc\xe9", max(0, push - 0x400), push))
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


def find_pool_grow_sites(x):
    """Allocate's isExhausted call in its size-class pool loop, the `jmp` that leaves the loop on
    an exhausted pool, and MemoryPool_Simple's constructor from addSizeClassPool."""
    data = bytes(x.data)
    start, end = find_heap_function(x, "allocate")
    # mov eax, [ebp-0x10]; mov eax, [eax]; call isExhausted; movzx ecx, al; test ecx, ecx; jne rel8
    sig = re.compile(rb"\x8b\x45\xf0\x8b\x00\xe8....\x0f\xb6\xc8\x85\xc9\x75(.)", re.S)
    lo, hi = x.va_to_off(start), x.va_to_off(end)
    found = list(sig.finditer(data, lo, hi))
    if len(found) != 1:
        raise PatchError("pool-grow: %d pool exhaustion checks in Allocate, expected 1"
                         % len(found))
    call = found[0].start() + 5
    leave = found[0].end() + found[0].group(1)[0]
    if data[leave] != 0xEB or data[leave + 2:leave + 5] != b"\x8b\x45\xf0":
        raise PatchError("pool-grow: no loop exit after the exhaustion check")
    # push 0x90; call operator_new; add esp, 4 ... lea ecx, [ebp-x]; push ecx; mov ecx, [ebp-y];
    # call MemoryPool_Simple::ctor
    ctor_sig = re.compile(rb"\x68\x90\x00\x00\x00\xe8....\x83\xc4\x04.{8,48}?"
                          rb"\x8d\x8d....\x51\x8b\x8d....\xe8", re.S)
    ctors = list(ctor_sig.finditer(data))
    if len(ctors) != 1:
        raise PatchError("pool-grow: %d size-class pool constructions, expected 1" % len(ctors))
    ctor_call = x.off_to_va(ctors[0].end() - 1)
    return {"exhausted_call": x.off_to_va(call), "loop_exit": x.off_to_va(leave),
            "pool_ctor": tes3x_inject.call_target(x, ctor_call)}


def find_info_arena_sites(x):
    """INFO constructor allocations, its two name-free paths, and the call after the global
    dialogue cleanup loop. All are anchored by the INFO vtable and the global heap object."""
    data = bytes(x.data)
    heap = find_heap_object(x)
    heap_bytes = struct.pack("<I", heap)
    constructor_sig = re.compile(
        rb"\xc7\x06(?P<vtable>....)\xe8....\x68\xdb\x0e\x00\x00"
        rb"\x68(?P<names>....)\x6a\x20\x6a\x03\x89\x46\x10\xe8", re.S)
    constructors = list(constructor_sig.finditer(data))
    if len(constructors) != 1:
        raise PatchError("info-name-arena: %d INFO constructors, expected 1" % len(constructors))
    constructor = constructors[0]
    vtable = struct.unpack("<I", constructor.group("vtable"))[0]
    names = struct.unpack("<I", constructor.group("names"))[0]
    if names != vtable - 0x20 or x.va_to_off(vtable) is None:
        raise PatchError("info-name-arena: INFO table pointers changed")
    constructor_store = constructor.start()
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
    names_off = constructor.end() - 1
    if not table_off < names_off < constructor_end:
        raise PatchError("info-name-arena: INFO allocation order changed")
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


# Inventory item IDs kept until the post-load link: (source file, line) of each loader's
# AllocateTagged, for the NPC record, creature and container records and their instances.
LINK_NAME_STRINGS = (("TES3NPC.cpp", 0x27C), ("TES3Creature.cpp", 0x170),
                     ("TES3Creature.cpp", 0x807), ("TES3Data.cpp", 0x16AB),
                     ("TES3Data.cpp", 0x1A58))
# Of those, the base records' loaders: each allocates its 12-byte item stack just before the ID
# and appends it to the inventory list just after.
LINK_BASE_INVENTORIES = (0, 1, 3)
LINK_STACK_ALLOCATE = re.compile(rb"\x6a\x0c\xb9....\xe8", re.S)
ITERATED_LIST_APPEND = b"\x56\x6a\x00\x68"


def find_link_names_sites(x):
    """The NPC constructor's five-name allocation, the inventory item ID allocations, the base
    inventories' stack allocations and list appends, and Memory_Heap::Free's prologue."""
    data = bytes(x.data)

    def source(name):
        return x.off_to_va(find_unique(data, b".\\TES3 Shared\\" + name.encode() + b"\0",
                                       "link-name-arena: " + name))

    npc = struct.pack("<I", source("TES3NPC.cpp"))
    names = list(re.finditer(rb"\x6a\x3c\x68" + re.escape(npc) +
                             rb"\x6a\x20\x6a\x05\x89\x46\x78\xe8", data))
    if len(names) != 1:
        raise PatchError("link-name-arena: %d NPC link name allocations, expected 1" % len(names))
    names_site = x.off_to_va(names[0].end() - 1)
    zeroed = tes3x_inject.call_target(x, names_site)

    strings = []
    for name, line in LINK_NAME_STRINGS:
        rx = (rb"\x68" + re.escape(struct.pack("<I", line)) + rb"\x40\x68" +
              re.escape(struct.pack("<I", source(name))) + rb"\x50\xe8")
        hits = list(re.finditer(rx, data))
        if len(hits) != 1:
            raise PatchError("link-name-arena: %d item ID allocations at %s:%d, expected 1"
                             % (len(hits), name, line))
        strings.append(x.off_to_va(hits[0].end() - 1))
    tagged = {tes3x_inject.call_target(x, site) for site in strings}
    if len(tagged) != 1:
        raise PatchError("link-name-arena: item ID allocations call different functions")

    stacks, appends = [], []
    for index in LINK_BASE_INVENTORIES:
        off = x.va_to_off(strings[index])
        hits = list(LINK_STACK_ALLOCATE.finditer(data, off - 0x50, off))
        if not hits:
            raise PatchError("link-name-arena: no item stack allocation before 0x%08X"
                             % strings[index])
        stacks.append(x.off_to_va(hits[-1].end() - 1))
        call = data.find(b"\xe8", off + 5, off + 0x40)
        while call >= 0:
            target = x.va_to_off(tes3x_inject.call_target(x, x.off_to_va(call)))
            if target is not None and data[target:target + 4] == ITERATED_LIST_APPEND:
                break
            call = data.find(b"\xe8", call + 1, off + 0x40)
        if call < 0:
            raise PatchError("link-name-arena: no list append after 0x%08X" % strings[index])
        appends.append(x.off_to_va(call))
    append = {tes3x_inject.call_target(x, site) for site in appends}
    if len(append) != 1:
        raise PatchError("link-name-arena: inventory appends call different functions")

    free = find_heap_function(x, "free")[0]
    off = x.va_to_off(free)
    if data[off:off + 6] != b"\x55\x8b\xec\x83\xec\x14":
        raise PatchError("link-name-arena: Memory_Heap::Free prologue changed")
    return {"names": names_site, "zeroed": zeroed, "strings": strings,
            "tagged": tagged.pop(), "stacks": stacks,
            "allocate": find_heap_function(x, "allocate")[0], "appends": appends,
            "append": append.pop(), "free": free}


# Script::loadRecordSpecific's AllocateZeroed line for SCDT (bytecode). SCVR's variable names
# (0x1B4E) stay in the heap: the INFO loader scans every script's names for each Local filter.
SCRIPT_ARENA_LINES = (0x1B55,)


def find_script_arena_sites(x):
    """The script record loader's bytecode allocation."""
    data = bytes(x.data)
    source = x.off_to_va(find_unique(data, b".\\TES3 Shared\\TES3Script.cpp\0",
                                     "script-arena: TES3Script.cpp"))
    sites = []
    for line in SCRIPT_ARENA_LINES:
        rx = (rb"\x68" + re.escape(struct.pack("<I", line)) + rb"\x68" +
              re.escape(struct.pack("<I", source)) + rb"\x50\x6a\x01\xe8")
        hits = list(re.finditer(rx, data))
        if len(hits) != 1:
            raise PatchError("script-arena: %d allocations at TES3Script.cpp:%d, expected 1"
                             % (len(hits), line))
        sites.append(x.off_to_va(hits[0].end() - 1))
    zeroed = {tes3x_inject.call_target(x, site) for site in sites}
    if len(zeroed) != 1:
        raise PatchError("script-arena: script allocations call different functions")
    return {"sites": sites, "zeroed": zeroed.pop()}


LAND_SIZE = 0x224
LAND_ARENA_KEEP = 16  # elements left in the shrunk pool for land made in play


def find_land_arena_sites(x):
    """The LAND pool's capacity push, its operator new, and the record loader's call to it."""
    data = bytes(x.data)
    size = struct.pack("<I", LAND_SIZE)
    name = x.off_to_va(find_unique(data, b"\0TES3ObjectLAND\0", "land-arena: pool name") + 1)
    ctor = re.compile(rb"\x68(?P<cap>....)\x68" + re.escape(size) + rb"\x68" +
                      re.escape(struct.pack("<I", name)) + rb"\xb9(?P<pool>....)\xe8", re.S)
    hits = list(ctor.finditer(data))
    if len(hits) != 1:
        raise PatchError("land-arena: %d LAND pool constructions, expected 1" % len(hits))
    new = re.compile(rb"\x8b\x44\x24\x04\x6a\x00\x68....\x50\xb9" +
                     re.escape(hits[0].group("pool")) + rb"\xe8....\xc3", re.S)
    news = [x.off_to_va(m.start()) for m in new.finditer(data)]
    if len(news) != 1:
        raise PatchError("land-arena: %d LAND operator news, expected 1" % len(news))
    calls = [x.off_to_va(m.end()) for m in re.finditer(rb"\x68" + re.escape(size) + rb"(?=\xe8)", data)]
    calls = [va for va in calls if tes3x_inject.call_target(x, va) == news[0]]
    if len(calls) != 1:
        raise PatchError("land-arena: %d loader calls to the LAND new, expected 1" % len(calls))
    return {"capacity": x.off_to_va(hits[0].start("cap")),
            "bytes": struct.unpack("<I", hits[0].group("cap"))[0], "new": news[0], "call": calls[0]}


# TES3File::getNextSubrecord: the tag at +0x23C, read from the stream when empty.
NEXT_SUBRECORD = re.compile(
    rb"\x8b\x81\x3c\x02\x00\x00\x85\xc0\x56\x8d\xb1\x3c\x02\x00\x00\x75\x18\x8b\x49\x08\x8b\x01"
    rb"\x6a\x08\x56\xff\x50\x08\x85\xc0\x75\x09\x33\xc9\x89\x0e\x89\x4e\x04\x5e\xc3"
    rb"(?P<tail>\x8b\x06\x5e\xc3\xcc\xcc\xcc)", re.S)
# general::setStringSlot: free the old string, then allocate strlen + 1 and copy.
SET_STRING_SLOT = re.compile(
    rb"\x56\x8b\x74\x24\x0c\x57\x8b\x7c\x24\x0c\x8b\x07\x3b\xc6\x74.\x85\xc0\x74.\x50\xb9....\xe8"
    rb"....\x85\xf6\x74.\x80\x3e\x00\x74.\x8b\xc6\x8d\x50\x01.{0,4}?\x8a\x08\x40\x84\xc9\x75\xf9"
    rb"\x6a\x00\x2b\xc2\x68....\x40\x50\xb9....(?P<call>\xe8)....\x89\x07", re.S)


def find_path_arena_sites(x):
    """getNextSubrecord's non-empty return, and setStringSlot's allocation call."""
    data = bytes(x.data)
    found = {}
    for key, rx, group in (("tag", NEXT_SUBRECORD, "tail"), ("allocate", SET_STRING_SLOT, "call")):
        hits = list(rx.finditer(data))
        if len(hits) != 1:
            raise PatchError("path-arena: %d %s sites, expected 1" % (len(hits), key))
        found[key] = x.off_to_va(hits[0].start(group))
    found["heap_allocate"] = tes3x_inject.call_target(x, found["allocate"])
    if found["heap_allocate"] != find_heap_function(x, "allocate")[0]:
        raise PatchError("path-arena: setStringSlot does not call Memory_Heap::Allocate")
    return found


# DialogueInfo::findDialogue: walk NonDynamicData::dialogues (+0x48), then each topic's chain.
DIALOGUE_OWNER_FIND = re.compile(
    rb"\xa1(?P<handler>....)\x8b\x08\x56\x8b\x71\x48\x57\x8b\xce\xe8....\x85\xc0\x74.\x8b\x7c\x24\x0c"
    rb".{0,8}?\x8b\x40\x08\x8b\x48\x18\x85\xc9\x74.", re.S)
# The INFO destructor's free of its load-link names at +0x10; info-name-arena may retarget the calls.
DIALOGUE_OWNER_DTOR = re.compile(
    rb"\x8b\x47\x10\x3b\xc5\x89\x2d....\x74.\x8b\x08\x51\xe8....\x8b\x47\x10\x83\xc4\x04\x50"
    rb"\xb9....\xe8....\x89\x6f\x10", re.S)


def find_dialogue_owner_sites(x):
    """findDialogue's entry, the DataHandler it loads there, and the INFO destructor's +0x10 read."""
    found = {}
    for key, rx in (("find", DIALOGUE_OWNER_FIND), ("dtor", DIALOGUE_OWNER_DTOR)):
        hits = list(rx.finditer(bytes(x.data)))
        if len(hits) != 1:
            raise PatchError(f"dialogue-owner: {len(hits)} {key} sites, expected 1")
        found[key] = hits[0]
    return {
        "find": x.off_to_va(found["find"].start()),
        "data_handler": struct.unpack("<I", found["find"].group("handler"))[0],
        "dtor": x.off_to_va(found["dtor"].start()),
    }


# loadRecord's plugin INFO path: pool new, constructor, loadFromFile, then the type copy and the
# atTail test that chooses appendInfo(info, true) or findInfo.
LAZY_DIALOGUE_INFO = re.compile(
    rb"\x6a\x2c\xe8(?P<new>....)\x83\xc4\x04\x89\x44\x24\x10\x85\xc0\xc7\x84\x24\xf8\x01\x00\x00"
    rb".\x00\x00\x00\x74\x0b\x8b\xc8\xe8(?P<ctor>....)\x8b\xf8\xeb\x02\x33\xff\x8b\x0d(?P<topic>....)"
    rb"\x8b\x84\x24\x00\x02\x00\x00\x8a\x51\x14\x50\x57\x8b\xcd\xc7\x84\x24\x00\x02\x00\x00"
    rb"\xff\xff\xff\xff\x88\x57\x14\xe8(?P<load>....)"
    rb"(?P<site>\x8b\x0d(?P=topic)\x8a\x84\x24\x04\x02\x00\x00\x84\xc0\x8a\x51\x14\x88\x57\x14"
    rb"\x74\x12)(?P<tail>\x6a\x01\x57\xe8....\xb8\x01\x00\x00\x00\xe9(?P<done>....))", re.S)
# DialogueInfo::getText's master branch: the cached master file, its construction, reopen and
# seek, then a temporary file's teardown.
LAZY_DIALOGUE_GET_TEXT = re.compile(
    rb"\x39\x3d(?P<cached>....)\x75.\x68\x74\x05\x00\x00\xe8(?P<new>....)\x83\xc4\x04"
    rb"\x89\x44\x24\x10\x3b\xc7\x89\x7c\x24\x1c\x74.\x57\x8d\x4e\x0c\x51\x81\xc6\x10\x01\x00\x00"
    rb"\x56\x8b\xc8\xe8(?P<ctor>....)\xeb\x02\x33\xc0\x8b\x15(?P<data>....)\x8b\x0a"
    rb"\x8b\x91\x6c\xae\x00\x00\x57\x52\x8b\xc8\xc7\x44\x24\x24\xff\xff\xff\xff\xa3(?P=cached)"
    rb"\xe8(?P<collect>....)\x8b\x0d(?P=cached)\x57\x57\xe8(?P<reopen>....)\x84\xc0\x74."
    rb"\x8b\x35(?P=cached)\x8b\x55\x24\x52\x8b\xce\xe8(?P<seek>....)\x84\xc0\x8b\xce\x74."
    rb"\xe8....\x3d\x49\x4e\x46\x4f\x0f\x84....\x8b\xce\xe8(?P<close>....)\x3b\xdf\x74."
    rb"\x8b\xcb\xe8(?P<dtor>....)", re.S)
# loadAllFiles after the last plugin: the optional sortTopicsByNameLength, then the first topic
# for the freeLoadNames loop.
LAZY_DIALOGUE_LOADED = re.compile(
    rb"\x84\xdb\x75\x05\xe8....\x8b\x4e\x48(?P<call>\xe8....)\x85\xc0\x74.\x8b\x40\x08\x56"
    rb"\x8b\xc8\xe8", re.S)
# The first instructions of the per-topic readers that must see a built chain.
LAZY_DIALOGUE_READERS = {
    "filtered": rb"\x56\x57\x8b\xf9\x8b\x77\x18\x85\xf6\x0f\x84....\x8b\x0d....\x85\xc9\x74.",
    "at_index": rb"\x8b\x41\x18\x85\xc0\x74.\x8b\x4c\x24\x04\xeb\x03\x8d\x49\x00\x8b\xd1\x49",
    "journal": rb"\x53\x55\x8b\x6c\x24\x0c\x56\x57\x8b\xf9\x8b\x77\x18\x32\xdb\x85\xf6\x74."
               rb"\x0f\xbf\x46\x16\x3b\xc5",
}


def find_lazy_dialogue_sites(x):
    """The INFO load decision, the getText file plumbing materialization reuses, and the entry of
    each reader that needs a topic's responses."""
    data = bytes(x.data)
    found = {}
    for key, rx in (("info", LAZY_DIALOGUE_INFO), ("text", LAZY_DIALOGUE_GET_TEXT),
                    ("loaded", LAZY_DIALOGUE_LOADED)):
        hits = list(rx.finditer(data))
        if len(hits) != 1:
            raise PatchError(f"lazy-dialogue: {len(hits)} {key} sites, expected 1")
        found[key] = hits[0]
    info, text = found["info"], found["text"]

    def rel(match, group):
        end = match.end(group)
        return x.off_to_va(end) + struct.unpack("<i", match.group(group))[0]

    def word(match, group):
        return struct.unpack("<I", match.group(group))[0]

    site = x.off_to_va(info.start("site"))
    sites = {
        "info_site": site,
        "info_tail": x.off_to_va(info.start("tail")),
        "info_notail": x.off_to_va(info.start("tail")) + 0x12,
        "info_done": rel(info, "done"),
        "info_new": rel(info, "new"),
        "info_ctor": rel(info, "ctor"),
        "topic": word(info, "topic"),
        "load_from_file": rel(info, "load"),
        "cached_file": word(text, "cached"),
        "operator_new": rel(text, "new"),
        "file_ctor": rel(text, "ctor"),
        "data_handler": word(text, "data"),
        "file_collect": rel(text, "collect"),
        "file_reopen": rel(text, "reopen"),
        "file_seek": rel(text, "seek"),
        "file_close": rel(text, "close"),
        "file_dtor": rel(text, "dtor"),
        "loaded_call": x.off_to_va(found["loaded"].start("call")),
        "loaded_first": tes3x_inject.call_target(x, x.off_to_va(found["loaded"].start("call"))),
    }
    for key, sig in LAZY_DIALOGUE_READERS.items():
        hits = list(re.finditer(sig, data, re.S))
        if len(hits) != 1:
            raise PatchError(f"lazy-dialogue: {len(hits)} {key} reader entries, expected 1")
        sites["reader_" + key] = x.off_to_va(hits[0].start())
    return sites


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
# `cmp dword [esp+N], 0x1F`. A second keyboard has identical handlers; this one's key and space
# handlers read id globals 12 bytes apart.
VK_KEY_LIMIT_SIG = re.compile(
    re.escape(bytes.fromhex("668b0d")) + b".{4}" +
    re.escape(bytes.fromhex("b8010000005050518bcee8"))
    + b".{4}" + re.escape(bytes.fromhex("837c24241f")), re.S)


VK_SPACE_LIMIT_SIG = re.compile(
    re.escape(bytes.fromhex("668b15")) + b".{4}" + b".{67}"
    + re.escape(bytes.fromhex("837c242c1f")), re.S)


def find_vk_limit(x, sig, what):
    """The 5-byte length compare at the end of a keyboard-handler signature."""
    text = text_section(x)
    blob = bytes(x.data[text.raw:text.raw + text.rsize])
    partner = VK_SPACE_LIMIT_SIG if what == "key" else VK_KEY_LIMIT_SIG
    partner_ids = {struct.unpack_from("<I", blob, m.start() + 3)[0]
                   for m in partner.finditer(blob)}
    delta = -12 if what == "key" else 12
    hits = [m.end() - 5 for m in sig.finditer(blob)
            if struct.unpack_from("<I", blob, m.start() + 3)[0] + delta in partner_ids]
    if len(hits) != 1:
        raise PatchError("console: %d keyboard %s length check(s), expected 1" % (len(hits), what))
    return text.va + hits[0]


def find_vk_text_id(x):
    """The text field id read by the paired console keyboard space handler."""
    text = text_section(x)
    blob = bytes(x.data[text.raw:text.raw + text.rsize])
    key_ids = {struct.unpack_from("<I", blob, m.start() + 3)[0]
               for m in VK_KEY_LIMIT_SIG.finditer(blob)}
    values = {space_id for m in VK_SPACE_LIMIT_SIG.finditer(blob)
              if (space_id := struct.unpack_from("<I", blob, m.start() + 3)[0]) + 12 in key_ids}
    if len(values) != 1:
        raise PatchError("console: %d text field ids, expected 1" % len(values))
    return values.pop()


# The console keyboard's space-bar setup reads its id, pushes the button factory and then
# loads the game pointer. All three operands vary between the retail images.
VK_SPACE_SETUP_SIG = re.compile(
    rb"\x33\xc0\x66\xa1(?P<space>....)\x6a\x00\x68(?P<button>....)"
    rb"\x8b\xcb\x50\xe8....\x8b\x0d(?P<game>....)", re.S)


def find_vk_space_setup(x):
    text = text_section(x)
    blob = bytes(x.data[text.raw:text.raw + text.rsize])
    field = find_vk_text_id(x)
    # The keyboard's text and space ids are 0x8C apart in both retail UI id blocks.
    hits = [m for m in VK_SPACE_SETUP_SIG.finditer(blob)
            if struct.unpack("<I", m.group("space"))[0] == field + 0x8C]
    if len(hits) != 1:
        raise PatchError("console: %d keyboard space setups, expected 1" % len(hits))
    values = {name: struct.unpack("<I", hits[0].group(name))[0]
              for name in ("space", "button", "game")}
    for name, va in values.items():
        section = next((s for s in x.sections if s.va <= va < s.va + s.vsize), None)
        expected = ".text" if name == "button" else ".data"
        if section is None or section.name != expected:
            raise PatchError("console: %s operand is not in %s" % (name, expected))
    return values


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
    pattern = re.escape(CONSOLE_PRINT_SIG[:4]) + rb"...." + re.escape(CONSOLE_PRINT_SIG[8:])
    matches = list(re.finditer(pattern, x.data, re.S))
    if len(matches) != 1:
        raise PatchError("console printf: %d matches, expected exactly 1" % len(matches))
    return x.off_to_va(matches[0].start())


def find_console_menu_id(x):
    off = x.va_to_off(find_console_print(x))
    return struct.unpack_from("<I", x.data, off + 4)[0]


# Widget methods used by the console keyboard and multiplayer menus. The two retail compilers
# produce different entries for the property setter and factories.
CONSOLE_WIDGET_ENTRIES = {
    "SET_PROP": (
        bytes.fromhex("53 8b 5c 24 08 55 56 57 8b 7c 24 18 8d 44 24 1c 50 57 53 8b f1"),
        bytes.fromhex("53 8b 5c 24 0c 55 56 57 8b 7c 24 14 8d 44 24 1c 50 53 57 8b f1"),
    ),
    "SET_AUTO_WIDTH": (
        bytes.fromhex("8a 44 24 04 84 c0 88 81 1c 01 00 00 74 19 81 b9 fc 00 00 00"),
        bytes.fromhex("8a 44 24 04 84 c0 88 81 1c 01 00 00 74 29 8b 81 fc 00 00 00"),
    ),
    "SET_AUTO_HEIGHT": (
        bytes.fromhex("8a 44 24 04 84 c0 88 81 1d 01 00 00 74 19 81 b9 00 01 00 00"),
        bytes.fromhex("8a 44 24 04 84 c0 88 81 1d 01 00 00 74 2a 8b 81 00 01 00 00"),
    ),
    "CREATE_WIDGET": (
        bytes.fromhex("56 8b f1 80 3e 58 74 06 33 c0 5e c2 0c 00 8a 44 24 10 84 c0"),
        bytes.fromhex("53 55 56 8b f1 80 3e 58 57 74 09 33 c0 5f 5e 5d 5b c2 0c 00"),
    ),
    "CREATE_IMAGE": (
        bytes.fromhex("57 8b f9 80 3f 58 74 06 33 c0 5f c2 0c 00 8a 44 24 10 84 c0"),
        bytes.fromhex("53 55 56 57 8b f9 80 3f 58 74 09 33 c0 5f 5e 5d 5b c2 0c 00"),
    ),
    "CREATE_NIF": (
        bytes.fromhex("57 8b f9 80 3f 58 74 06 33 c0 5f c2 0c 00 8a 44 24 10 84 c0"),
        bytes.fromhex("53 55 56 57 8b f9 80 3f 58 74 09 33 c0 5f 5e 5d 5b c2 0c 00"),
    ),
    "CREATE_BLOCK": (
        bytes.fromhex("56 8b f1 80 3e 58 74 06 33 c0 5e c2 08 00 8a 44 24 0c"),
        bytes.fromhex("53 55 56 8b f1 80 3e 58 57 74 09 33 c0 5f 5e 5d 5b c2 08 00"),
    ),
    "CREATE_LABEL": (
        bytes.fromhex("56 8b f1 80 3e 58 74 06 33 c0 5e c2 10 00 8a 44 24 14"),
        bytes.fromhex("53 55 56 8b f1 80 3e 58 57 74 09 33 c0 5f 5e 5d 5b c2 10 00"),
    ),
}


def find_console_widget_entry(x, name):
    text = text_section(x)
    blob = bytes(x.data[text.raw:text.raw + text.rsize])
    hits = [m.start() for sig in CONSOLE_WIDGET_ENTRIES[name]
            for m in re.finditer(re.escape(sig), blob)]
    if name in ("CREATE_IMAGE", "CREATE_NIF"):
        # These factories begin alike; their class ids distinguish the constructors.
        class_id = b"\xB8\xB8\x80" if name == "CREATE_IMAGE" else b"\xB8\xB5\x80"
        hits = [off for off in hits if class_id in blob[off:off + 0xC0]]
    if len(hits) != 1:
        raise PatchError("console %s: %d entries, expected 1" % (name, len(hits)))
    return text.va + hits[0]


GET_FOCUS_PROP = bytes.fromhex("6a 00 6a 00 6a 08 68 9b 80 ff ff")


def find_get_focus(x):
    """Find the focus-property getter despite PAL inlining its parent traversal."""
    text = text_section(x)
    blob = bytes(x.data[text.raw:text.raw + text.rsize])
    hits = list(re.finditer(re.escape(GET_FOCUS_PROP), blob))
    if len(hits) != 1:
        raise PatchError("get focus: %d property reads, expected 1" % len(hits))
    at = hits[0].start()
    starts = [i for i in range(max(1, at - 0x60), at)
              if blob[i - 1] == 0xCC and blob[i:i + 1] != b"\xCC"]
    if len(starts) != 1 or blob[starts[0]] != 0x51:
        raise PatchError("get focus: no unique function entry above property read")
    return text.va + starts[0]


LIST_APPEND_BODY = re.compile(rb"\x19\x8b\x4e\x0c\x89\x46\x0c\x89\x41\x04\x89\x08", re.S)


def find_list_append(x):
    """Find the list append with the class-link write, then its entry prologue."""
    text = text_section(x)
    blob = bytes(x.data[text.raw:text.raw + text.rsize])
    hits = list(LIST_APPEND_BODY.finditer(blob))
    if len(hits) != 1:
        raise PatchError("list append: %d class-link writes, expected 1" % len(hits))
    at = hits[0].start()
    prologue = re.compile(rb"\x56\x6a\x00\x68....\x8b\xf1\x6a\x0c\xb9", re.S)
    starts = [m.start() for m in prologue.finditer(blob[max(0, at - 0x80):at])]
    if len(starts) != 1:
        raise PatchError("list append: %d entries above class-link write, expected 1"
                         % len(starts))
    return text.va + max(0, at - 0x80) + starts[0]


VISIBLE_STATE_SIG = bytes.fromhex("38 5e 7e 75 05 38 5e 7d")


def find_set_visible(x):
    """Find the menu visibility setter by its paired visibility-state checks."""
    text = text_section(x)
    blob = bytes(x.data[text.raw:text.raw + text.rsize])
    hits = [m.start() for m in re.finditer(re.escape(VISIBLE_STATE_SIG), blob)]
    if len(hits) != 2 or hits[1] - hits[0] not in (0x36, 0x38):
        raise PatchError("set visible: expected paired state checks")
    start = blob.rfind(b"\xC3\x53", max(0, hits[0] - 0x80), hits[0])
    if start < 0:
        raise PatchError("set visible: no function entry above state check")
    return text.va + start + 1


def find_ini_get_string(x):
    """Locate the engine's INI string reader by its stack frame and early null check."""
    sig = re.compile(rb"\x55\x8b\xec\x83\xe4\xf8\xb8\x04\x10\x00\x00\xe8...."
                     rb"\x53\x56\x57\x8b\x7d\x18\x85\xff\x0f\x84", re.S)
    matches = list(sig.finditer(bytes(x.data)))
    if len(matches) != 1:
        raise PatchError("INI reader: %d matches, expected 1" % len(matches))
    return x.off_to_va(matches[0].start())


# TES3File::readChunkData's prologue; the masked word is a global that moves between engines.
CHUNK_READ_SIG = re.compile(
    rb"\xcc\x83\xec\x10\x53\x56\x8b\xf1\x8b\x86\x50\x02\x00\x00\x85\xc0\x57\x76\x2a"
    rb"\x8b\x15....\x8b\xbe\x48\x02\x00\x00\x8b\x4e", re.S)


def find_chunk_read(x):
    hits = list(CHUNK_READ_SIG.finditer(bytes(x.data)))
    if len(hits) != 1:
        raise PatchError("TES3File::readChunkData: %d match(es), expected 1" % len(hits))
    va = x.off_to_va(hits[0].start() + 1)
    if va is None:
        raise PatchError("TES3File::readChunkData is outside any section")
    return va


def find_ini_path(x):
    return x.off_to_va(find_unique(bytes(x.data), b"D:\\Morrowind.ini\0", "INI path"))


DOOR_ICON = b"textures\\door_icon.tga\0"
# Door marker setup in MenuMap::updateMapRender, from the createImage call to the property
# writes: the marker flag and the door reference. Their ids live in globals.
DOOR_MARKER_SIG = re.compile(
    rb"\x89\x44\x24.\x52\xe8....\x8b\xf0\x6a\x03\x8b\xce\xe8....\x6a\x03\x8b\xce\xe8...."
    rb"\x6a\x10\xb8\xbd\x80\xff\xff\x50\x33\xc0\x66\xa1(....)\x8b\xce\x50\xe8...."
    rb"\x33\xc9\x66\x8b\x0d(....)\x6a\x08\x55\x51\x8b\xce\xe8....", re.S)
# After the door reference: the cell pointer is loaded and stored under the cell-data id.
MARKER_CELL_SIG = re.compile(
    rb"\x8b\x54\x24.\x8b\x42\x04\x8b\x4c\x24.\x8b\x44\x08\x04\x8b\x44\x18\x10"
    rb"\x33\xd2\x66\x8b\x15(....)\x6a\x08", re.S)
# The call that ends updateMapRender: updateLocalPlayer(menu, 0, 0, 0xFFFF80BD, menu).
LOCAL_PLAYER_SITE_SIG = re.compile(
    rb"\x6a\x01\x8b\xce\xe8....\x53\x55\x55\x68\xbd\x80\xff\xff\x53(\xe8....)", re.S)


def find_door_marker(x):
    """The marker flag and door reference id globals, from the door marker setup."""
    off = find_unique(bytes(x.data), DOOR_ICON, "door marker texture")
    push = b"\x68" + struct.pack("<I", x.off_to_va(off))
    at = find_unique(bytes(x.data), push, "door marker texture push")
    m = DOOR_MARKER_SIG.match(bytes(x.data), at + 5)
    if m is None:
        raise PatchError("door marker setup: layout not recognised")
    cell = MARKER_CELL_SIG.match(bytes(x.data), m.end())
    if cell is None:
        raise PatchError("door marker setup: cell data property not recognised")
    return {"marker": struct.unpack("<I", m.group(1))[0],
            "door-ref": struct.unpack("<I", m.group(2))[0],
            "cell": struct.unpack("<I", cell.group(1))[0]}


# updateMapRender's lookup of the rectangle that holds the door markers, by its id global.
MARKER_PARENT_SIG = re.compile(
    rb"\x33\xc9\x66\x8b\x0d(....)\xbf\x01\x00\x00\x00\xc7\x44\x24.\x00\x02\x00\x00"
    rb"\xc7\x44\x24.\x00\x00\x00\x40", re.S)
# The world-to-map conversion called for every door marker (scale 0x200 on the map, 0x100 on
# the HUD); it rewrites the three floats in place.
MAP_PIXELS_SIG = re.compile(
    rb"\x68\x00\x02\x00\x00\x8d\x44\x24.\x50\xeb.\x68\x00\x01\x00\x00\x8d\x4c\x24.\x51(\xe8....)",
    re.S)


# The door marker's setX(x - 2) and setY(y + 2), after its size and scroll fields are written.
MARKER_POSITION_SIG = re.compile(
    rb"\xb8\x00\x38\xff\xc6\x89\x86\x28\x01\x00\x00\x89\x86\x2c\x01\x00\x00\x8b\x44\x24."
    rb"\x83\xc0\xfe\x50\x8b\xce\xc6\x86\x87\x00\x00\x00\x00(\xe8....)\x8b\x4c\x24."
    rb"\x83\xc1\x02\x51\x8b\xce(\xe8....)", re.S)


def find_marker_position(x):
    """setX and setY, as the door marker setup calls them."""
    text = text_section(x)
    blob = bytes(x.data[text.raw:text.raw + text.rsize])
    hits = list(MARKER_POSITION_SIG.finditer(blob))
    if len(hits) != 1:
        raise PatchError("door marker position: %d match(es), expected 1" % len(hits))
    return [text.va + hits[0].start(g) + 5 + struct.unpack("<i", hits[0].group(g)[1:])[0]
            for g in (1, 2)]


# What follows setY: the call that marks the marker, then its "tagged" property cleared.
MARKER_TAGGED_SIG = re.compile(
    rb"\x68\xd1\x80\xff\xff\x8b\xce\xe8....\x6a\x10\x33\xd2\x66\x8b\x15(....)", re.S)


def find_marker_tagged_id(x):
    text = text_section(x)
    blob = bytes(x.data[text.raw:text.raw + text.rsize])
    hits = list(MARKER_TAGGED_SIG.finditer(blob))
    if len(hits) != 1:
        raise PatchError("door marker tagged id: %d match(es), expected 1" % len(hits))
    return struct.unpack("<I", hits[0].group(1))[0]


def find_marker_parent_id(x):
    text = text_section(x)
    blob = bytes(x.data[text.raw:text.raw + text.rsize])
    hits = list(MARKER_PARENT_SIG.finditer(blob))
    if len(hits) != 1:
        raise PatchError("door marker parent: %d match(es), expected 1" % len(hits))
    return struct.unpack("<I", hits[0].group(1))[0]


def find_map_pixels(x):
    text = text_section(x)
    blob = bytes(x.data[text.raw:text.raw + text.rsize])
    hits = list(MAP_PIXELS_SIG.finditer(blob))
    if len(hits) != 1:
        raise PatchError("map pixel conversion: %d match(es), expected 1" % len(hits))
    at = text.va + hits[0].start(1)
    return at + 5 + struct.unpack("<i", hits[0].group(1)[1:])[0]


def find_local_player_site(x):
    """The updateLocalPlayer call at the end of updateMapRender."""
    text = text_section(x)
    blob = bytes(x.data[text.raw:text.raw + text.rsize])
    hits = list(LOCAL_PLAYER_SITE_SIG.finditer(blob))
    if len(hits) != 1:
        raise PatchError("updateLocalPlayer call: %d match(es), expected 1" % len(hits))
    return text.va + hits[0].start(1)


# Content-located engine addresses, shared with the payload build so the two cannot disagree.
LOCATORS = {
    "map-marker-id": lambda image: find_door_marker(image)["marker"],
    "map-door-ref-id": lambda image: find_door_marker(image)["door-ref"],
    "map-local-player-site": find_local_player_site,
    "map-cell-id": lambda image: find_door_marker(image)["cell"],
    "map-parent-id": find_marker_parent_id,
    "map-pixels": find_map_pixels,
    "map-tagged-id": find_marker_tagged_id,
    "map-set-x": lambda image: find_marker_position(image)[0],
    "map-set-y": lambda image: find_marker_position(image)[1],
    "run-function": find_run_function,
    "chunk-read": find_chunk_read,
    "command-table": find_command_table,
    "script-decode": lambda image: find_script_decode_state(image)[0],
    "script-ip": lambda image: find_script_decode_state(image)[1],
    "script-opcode": lambda image: find_script_decode_state(image)[2],
    "script-destination": lambda image: find_script_operand_globals(image)[0],
    "script-rotation": lambda image: find_script_operand_globals(image)[1],
    "script-text": lambda image: find_script_operand_globals(image)[2],
    "script-ip-restore": find_script_ip_restore_site,
    "ref-load": find_ref_load,
    "ref-skip": find_ref_skip,
    "mcp-97-scan": find_mcp97_scan,
    "mcp-98": lambda image: find_mcp98_refcounts(image)[0],
    "mcp-92": lambda image: find_mcp92_unsummon(image)[0],
    "mcp-154-load": find_mcp154_load,
    "mcp-154-reload": find_mcp154_reload,
    "mcp-102-actn": find_mcp102_actn,
    "mcp-3": find_mcp3_unarmored,
    "dialogue-merge": find_dialogue_merge,
    "mcp-123": find_mcp123_add,
    "mcp-173": lambda image: find_mcp173_rank(image)[0],
    "mcp-125": find_mcp125_collision,
    "mcp-37": lambda image: find_mcp37_context(image)[0],
    "dxt5-size": find_dxt5_size,
    "mcp-146": find_mcp146,
    "mcp-47": lambda image: find_mcp47_context(image)[0],
    "mcp-48": lambda image: find_mcp48_context(image)[0],
    "mcp-25": find_mcp25_blind,
    "mcp-146-input": lambda image: find_mcp146_context(image)[1],
    "mcp-146-game": lambda image: find_mcp146_context(image)[2],
    "mcp-146-resume": lambda image: find_mcp146_context(image)[3],
    "bow-view": find_bow_view,
    "hd-video": find_hd_video,
    "boot-clear": find_boot_clear,
    "menu-still": find_menu_still,
    "dash-button": find_dash_button,
    "topic-colors": find_topic_colors,
    "ui-scale": find_hud_scale,
    "ref-index": lambda image: find_ref_index(image)[0],
    "ref-index-find": lambda image: find_ref_index(image)[1],
    "ref-index-scripts": lambda image: find_ref_index(image)[2],
    "soundgen-index": lambda image: find_soundgen_index(image)[0],
    "soundgen-index-stricmp": lambda image: find_soundgen_index(image)[1],
    "soundgen-index-resolve": lambda image: find_soundgen_index(image)[2],
    "lean-menu": find_lean_menu,
    "video-arena": find_arena_size,
    "video-arena-phys-alloc": lambda image: find_arena_overflow(image)["phys_alloc"],
    "video-arena-phys-free": lambda image: find_arena_overflow(image)["phys_free"],
    "save-game": lambda image: find_autosave_calls(image)[0],
    "save-this-ptr": find_save_this_ptr,
    "preferences-load": find_preferences_load,
    "controls-table": find_controls_table,
    "diagnostics-update": find_diagnostics_update,
    "game-instance": find_game_instance,
    "world-controller": find_world_controller,
    "menu-mode-gate": find_menu_mode_gate,
    "mob-update-gate": find_mob_gate,
    "weather-roll": find_weather_roll,
    "button-pressed": find_button_pressed,
    "find-marker": lambda image: find_marker(image)[0],
    "temple-marker": lambda image: find_marker(image)[1],
    "divine-marker": lambda image: find_marker(image)[2],
    "console-print": find_console_print,
    "console-menu-id": find_console_menu_id,
    "console-vk-text-id": find_vk_text_id,
    "console-vk-space-id": lambda image: find_vk_space_setup(image)["space"],
    "console-vk-button": lambda image: find_vk_space_setup(image)["button"],
    "console-game-ptr": lambda image: find_vk_space_setup(image)["game"],
    **{"console-" + name.lower().replace("_", "-"):
       (lambda image, key=name: find_console_signature(image, key))
       for name in CONSOLE_SIGNATURES},
    **{"console-" + name.lower().replace("_", "-"):
       (lambda image, key=name: find_console_widget_entry(image, key))
       for name in CONSOLE_WIDGET_ENTRIES},
    "multi-get-focus": find_get_focus,
    "multi-list-append": find_list_append,
    "multi-set-visible": find_set_visible,
    "ini-get-string": find_ini_get_string,
    "ini-path": find_ini_path,
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
    "link-zeroed": lambda image: find_link_names_sites(image)["zeroed"],
    "link-tagged": lambda image: find_link_names_sites(image)["tagged"],
    "link-free": lambda image: find_link_names_sites(image)["free"],
    "link-allocate": lambda image: find_link_names_sites(image)["allocate"],
    "link-append": lambda image: find_link_names_sites(image)["append"],
    "script-zeroed": lambda image: find_script_arena_sites(image)["zeroed"],
    "path-allocate": lambda image: find_path_arena_sites(image)["heap_allocate"],
    "land-new": lambda image: find_land_arena_sites(image)["new"],
    "dialogue-owner-find": lambda image: find_dialogue_owner_sites(image)["find"],
    "dialogue-owner-data-handler": lambda image: find_dialogue_owner_sites(image)["data_handler"],
    "xapi-heap-alloc": find_xapi_heap_alloc,
    "xapi-heap-free": find_xapi_heap_free,
}
