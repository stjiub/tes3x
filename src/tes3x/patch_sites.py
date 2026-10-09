"""Content-located engine addresses and signatures shared by patching and payload builds."""

import re
import struct
import tes3x.inject as tes3x_inject  # noqa: E402

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


def find_unique(data, needle, what):
    hits = [m.start() for m in re.finditer(re.escape(needle), data)]
    if len(hits) != 1:
        raise PatchError("%s: %d match(es) for %r, expected exactly 1" % (what, len(hits), needle))
    return hits[0]


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


# Content-located engine addresses, shared with the payload build so the two cannot disagree.
LOCATORS = {
    "run-function": find_run_function,
    "command-table": find_command_table,
    "script-decode": lambda image: find_script_decode_state(image)[0],
    "script-ip": lambda image: find_script_decode_state(image)[1],
    "script-opcode": lambda image: find_script_decode_state(image)[2],
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
    "mcp-125": find_mcp125_collision,
    "mcp-37": lambda image: find_mcp37_context(image)[0],
    "dxt5-size": find_dxt5_size,
    "mcp-146": find_mcp146,
    "mcp-146-input": lambda image: find_mcp146_context(image)[1],
    "mcp-146-game": lambda image: find_mcp146_context(image)[2],
    "mcp-146-resume": lambda image: find_mcp146_context(image)[3],
    "bow-view": find_bow_view,
    "hd-video": find_hd_video,
    "ref-index": lambda image: find_ref_index(image)[0],
    "ref-index-find": lambda image: find_ref_index(image)[1],
    "ref-index-scripts": lambda image: find_ref_index(image)[2],
    "lean-menu": find_lean_menu,
    "video-arena": find_arena_size,
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
