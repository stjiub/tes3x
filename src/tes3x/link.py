"""Link i386 COFF payload objects into a PE32 image at a fixed address.

Covers what clang emits for the freestanding payload: DIR32 and REL32 relocations, COMDAT
sections, weak externals, common symbols and /INCLUDE directives. Unreferenced COMDAT sections
are dropped, as lld-link /opt:ref does. Symbols the objects leave undefined may be supplied as
absolute values or as 32-bit words the linker places in the image.
"""

from dataclasses import dataclass, field
import struct

REL_DIR32 = 0x06
REL_REL32 = 0x14

SCN_CNT_CODE = 0x00000020
SCN_CNT_UNINITIALIZED = 0x00000080
SCN_LNK_INFO = 0x00000200
SCN_LNK_REMOVE = 0x00000800
SCN_LNK_COMDAT = 0x00001000
SCN_LNK_NRELOC_OVFL = 0x01000000
SCN_MEM_WRITE = 0x80000000

CLASS_EXTERNAL = 2
CLASS_STATIC = 3
CLASS_LABEL = 6
CLASS_WEAK_EXTERNAL = 105

SYM_UNDEFINED = 0
SYM_ABSOLUTE = -1
SYM_DEBUG = -2

COMDAT_NODUPLICATES = 1
COMDAT_ASSOCIATIVE = 5
COMDAT_LARGEST = 6

# Output order: code, read-only data, data, then zero-filled data.
GROUPS = (".text", ".rdata", ".data", ".bss")
HEADER_SIZE = 0x200


class LinkError(RuntimeError):
    pass


@dataclass(eq=False)
class InputSection:
    obj: "ObjectFile"
    index: int
    name: str
    flags: int
    data: bytes
    size: int
    relocs: list
    comdat: int = 0
    associate: int = 0
    leader: str | None = None
    shared: bool = False
    associates: list = field(default_factory=list)
    live: bool = False
    discarded: bool = False
    rva: int = 0

    @property
    def align(self):
        shift = (self.flags >> 20) & 0xF
        return 1 << (shift - 1) if shift else 16

    @property
    def group(self):
        if self.flags & SCN_CNT_UNINITIALIZED:
            return ".bss"
        if self.flags & SCN_CNT_CODE:
            return ".text"
        if self.flags & SCN_MEM_WRITE:
            return ".data"
        return ".rdata"


@dataclass(eq=False)
class Symbol:
    name: str
    value: int
    section: int
    storage: int
    aux: bytes


@dataclass(eq=False)
class ObjectFile:
    name: str
    sections: list = field(default_factory=list)
    symbols: dict = field(default_factory=dict)
    includes: list = field(default_factory=list)


@dataclass(eq=False)
class Definition:
    """A resolved global: a place in an input section, or an absolute value."""
    name: str
    section: InputSection | None
    value: int
    obj: str


@dataclass
class Linked:
    base: int
    image: bytes
    vsize: int
    entry: int
    symbols: dict
    owners: dict
    sections: list


def _cstr(raw):
    return raw.split(b"\0", 1)[0].decode("utf-8", "replace")


def parse_object(name, data):
    """Read the sections, symbols and directives of an i386 COFF object."""
    if len(data) < 20:
        raise LinkError(f"{name}: truncated COFF header")
    machine, nsec, _stamp, symptr, nsym, optsz, _chars = struct.unpack_from("<HHIIIHH", data)
    if machine == 0 and nsec == 0xFFFF:
        raise LinkError(f"{name}: big-object COFF is not supported")
    if machine != 0x14C:
        raise LinkError(f"{name}: not an i386 COFF object (machine 0x{machine:04X})")
    strtab = symptr + nsym * 18

    def long_name(offset):
        return _cstr(data[strtab + offset:strtab + offset + 4096])

    obj = ObjectFile(name)
    for i in range(nsec):
        o = 20 + optsz + i * 40
        raw_name = data[o:o + 8]
        (_vsize, _va, rsize, rawptr, relptr, _lnptr, nrel, _nln,
         flags) = struct.unpack_from("<IIIIIIHHI", data, o + 8)
        sname = _cstr(raw_name)
        if sname.startswith("/"):
            sname = long_name(int(sname[1:]))
        relocs = []
        if flags & SCN_LNK_NRELOC_OVFL and nrel == 0xFFFF:
            nrel = struct.unpack_from("<I", data, relptr)[0]
            relptr += 10
            nrel -= 1
        for r in range(nrel):
            relocs.append(struct.unpack_from("<IIH", data, relptr + r * 10))
        body = b"" if flags & SCN_CNT_UNINITIALIZED else bytes(data[rawptr:rawptr + rsize])
        obj.sections.append(InputSection(obj, i + 1, sname, flags, body, rsize, relocs))
    i = 0
    while i < nsym:
        o = symptr + i * 18
        raw_name = data[o:o + 8]
        value, section, _type, storage, naux = struct.unpack_from("<IhHBB", data, o + 8)
        if raw_name[:4] == b"\0\0\0\0":
            sname = long_name(struct.unpack_from("<I", raw_name, 4)[0])
        else:
            sname = _cstr(raw_name)
        aux = bytes(data[o + 18:o + 18 + naux * 18])
        obj.symbols[i] = Symbol(sname, value, section, storage, aux)
        i += 1 + naux
    for symbol in obj.symbols.values():
        if symbol.section <= 0:
            continue
        section = obj.sections[symbol.section - 1]
        if (symbol.storage == CLASS_STATIC and symbol.aux and symbol.value == 0
                and section.flags & SCN_LNK_COMDAT and section.name == symbol.name):
            number, selection = struct.unpack_from("<HB", symbol.aux, 12)
            section.comdat = selection
            section.associate = number
        elif section.leader is None and symbol.storage in (CLASS_EXTERNAL, CLASS_STATIC):
            # The COMDAT symbol; only an external one makes copies in other objects the same
            section.leader = symbol.name
            section.shared = symbol.storage == CLASS_EXTERNAL
    for section in obj.sections:
        if section.comdat == COMDAT_ASSOCIATIVE:
            obj.sections[section.associate - 1].associates.append(section)
    for section in obj.sections:
        if section.flags & SCN_LNK_INFO and section.name == ".drectve":
            for word in section.data.decode("ascii", "replace").split():
                lower = word.lower()
                if lower.startswith(("/include:", "-include:")):
                    obj.includes.append(word.split(":", 1)[1])
                else:
                    raise LinkError(f"{name}: unsupported linker directive {word}")
    return obj


def _discardable(section):
    return bool(section.flags & (SCN_LNK_REMOVE | SCN_LNK_INFO)) or \
        section.name.startswith(".debug$")


def link(objects, base, entry, absolute=None, words=None, roots=()):
    """Link (name, bytes) objects at base. absolute maps undefined symbols to values; words
    maps them to 32-bit values the image holds. Returns a Linked image."""
    absolute = dict(absolute or {})
    words = dict(words or {})
    parsed = [parse_object(name, data) for name, data in objects]
    defined = {}
    comdats = {}
    commons = {}

    # COMDAT selection: the first copy of a group stays; later copies are dropped.
    for obj in parsed:
        for section in obj.sections:
            if _discardable(section) or not section.comdat or \
                    section.comdat == COMDAT_ASSOCIATIVE:
                continue
            key = section.leader
            if not section.shared:
                continue
            kept = comdats.get(key)
            if kept is None:
                comdats[key] = section
            elif section.comdat == COMDAT_NODUPLICATES:
                raise LinkError(f"duplicate COMDAT {key} in {kept.obj.name} and {obj.name}")
            elif section.comdat == COMDAT_LARGEST and section.size > kept.size:
                kept.discarded = True
                comdats[key] = section
            else:
                section.discarded = True
    for obj in parsed:
        for section in obj.sections:
            if section.comdat == COMDAT_ASSOCIATIVE and \
                    obj.sections[section.associate - 1].discarded:
                section.discarded = True

    for obj in parsed:
        for symbol in obj.symbols.values():
            if symbol.storage != CLASS_EXTERNAL:
                continue
            if symbol.section > 0:
                section = obj.sections[symbol.section - 1]
                if section.discarded:
                    continue
                if symbol.name in defined:
                    prior = defined[symbol.name]
                    raise LinkError(f"duplicate symbol {symbol.name} in {prior.obj} and {obj.name}")
                defined[symbol.name] = Definition(symbol.name, section, symbol.value, obj.name)
            elif symbol.section == SYM_ABSOLUTE:
                if symbol.name in defined:
                    raise LinkError(f"duplicate symbol {symbol.name} in {obj.name}")
                defined[symbol.name] = Definition(symbol.name, None, symbol.value, obj.name)
            elif symbol.section == SYM_UNDEFINED and symbol.value:
                commons[symbol.name] = max(commons.get(symbol.name, 0), symbol.value)

    synthetic = ObjectFile("<linker>")
    for name, size in commons.items():
        if name not in defined:
            section = InputSection(synthetic, len(synthetic.sections) + 1, ".bss",
                                   SCN_CNT_UNINITIALIZED | SCN_MEM_WRITE | 0x00500000, b"",
                                   size, [])
            synthetic.sections.append(section)
            defined[name] = Definition(name, section, 0, synthetic.name)
    word_section = None
    missing = set()

    def resolve_global(name):
        found = defined.get(name)
        if found:
            return found
        if name in absolute:
            found = defined[name] = Definition(name, None, absolute[name], "<absolute>")
            return found
        if name in words:
            nonlocal word_section
            if word_section is None:
                word_section = InputSection(synthetic, len(synthetic.sections) + 1, ".data",
                                            SCN_MEM_WRITE | 0x40 | 0x00300000, b"", 0, [])
                synthetic.sections.append(word_section)
            offset = len(word_section.data)
            word_section.data += struct.pack("<I", words[name] & 0xFFFFFFFF)
            word_section.size = len(word_section.data)
            found = defined[name] = Definition(name, word_section, offset, synthetic.name)
            return found
        return None

    def resolve(obj, index):
        """A relocation target: (section or None, value)."""
        symbol = obj.symbols[index]
        if symbol.storage == CLASS_WEAK_EXTERNAL:
            found = resolve_global(symbol.name)
            if found:
                return found.section, found.value
            tag = struct.unpack_from("<I", symbol.aux, 0)[0]
            return resolve(obj, tag)
        if symbol.storage == CLASS_EXTERNAL:
            found = resolve_global(symbol.name)
            if found is None:
                missing.add(symbol.name)
                return None, 0
            return found.section, found.value
        if symbol.section > 0:
            section = obj.sections[symbol.section - 1]
            if section.discarded:
                raise LinkError(f"{obj.name}: reference to discarded section {section.name} "
                                f"through {symbol.name}")
            return section, symbol.value
        if symbol.section == SYM_ABSOLUTE:
            return None, symbol.value
        raise LinkError(f"{obj.name}: unsupported symbol {symbol.name} "
                        f"(class {symbol.storage}, section {symbol.section})")

    # Liveness: every non-COMDAT section, then whatever live sections reach.
    pending = []

    def mark(section):
        if section is not None and not section.live and not section.discarded:
            section.live = True
            pending.append(section)

    for obj in parsed:
        for section in obj.sections:
            if not _discardable(section) and not section.discarded and not section.comdat:
                mark(section)
    root_names = [entry, *roots] + [name for obj in parsed for name in obj.includes]
    for name in root_names:
        found = resolve_global(name)
        if found is None:
            raise LinkError(f"undefined symbol {name}")
        mark(found.section)
    targets = {}
    while pending:
        section = pending.pop()
        for other in section.associates:
            mark(other)
        resolved = []
        for offset, index, kind in section.relocs:
            target, value = resolve(section.obj, index)
            resolved.append((offset, kind, target, value, section.obj.symbols[index].name))
            mark(target)
        targets[id(section)] = resolved
    if missing:
        raise LinkError("undefined symbol(s): " + ", ".join(sorted(missing)))

    live = [s for obj in parsed + [synthetic] for s in obj.sections if s.live]
    order = {group: i for i, group in enumerate(GROUPS)}
    live.sort(key=lambda s: (order[s.group], s.name.split("$", 1)[0], s.name))
    rva = HEADER_SIZE
    initialized_end = HEADER_SIZE
    for section in live:
        rva = (rva + section.align - 1) & -section.align
        section.rva = rva
        rva += section.size
        if section.group != ".bss":
            initialized_end = rva
    vsize = rva
    image = bytearray(initialized_end)
    for section in live:
        if section.group != ".bss":
            image[section.rva:section.rva + len(section.data)] = section.data
    for section in live:
        for offset, kind, target, value, name in targets.get(id(section), ()):
            at = section.rva + offset
            if at + 4 > len(image):
                raise LinkError(f"{section.obj.name}: relocation outside {section.name}")
            address = (base + target.rva if target is not None else 0) + value
            addend = struct.unpack_from("<I", image, at)[0]
            if kind == REL_DIR32:
                result = addend + address
            elif kind == REL_REL32:
                result = addend + address - (base + at + 4)
            else:
                raise LinkError(f"{section.obj.name}: unsupported relocation type 0x{kind:X} "
                                f"against {name}")
            struct.pack_into("<I", image, at, result & 0xFFFFFFFF)

    symbols, owners = {}, {}
    for name, found in defined.items():
        if found.section is None:
            if found.obj != "<absolute>" and not name.startswith(".weak."):
                symbols[name] = found.value
                owners[name] = found.obj
        elif found.section.live:
            symbols[name] = base + found.section.rva + found.value
            owners[name] = found.obj
    entry_va = symbols.get(entry)
    if entry_va is None:
        raise LinkError(f"entry {entry} is not defined")
    return Linked(base, bytes(image), vsize, entry_va, symbols, owners, live)


def write_pe(linked, path):
    """One RWX section at HEADER_SIZE; the image is only valid at its base."""
    raw_size = len(linked.image) - HEADER_SIZE
    raw_aligned = (raw_size + 0x1FF) & ~0x1FF
    image_size = (linked.vsize + 0x1FF) & ~0x1FF
    header = bytearray(HEADER_SIZE)
    header[0:2] = b"MZ"
    struct.pack_into("<I", header, 0x3C, 0x40)
    header[0x40:0x44] = b"PE\0\0"
    # i386, one section, no symbols; executable, 32-bit, relocations stripped
    struct.pack_into("<HHIIIHH", header, 0x44, 0x14C, 1, 0, 0, 0, 224, 0x0103)
    opt = 0x58
    struct.pack_into("<HBBIIIIIII", header, opt, 0x10B, 14, 0, raw_aligned, 0, 0,
                     linked.entry - linked.base, HEADER_SIZE, HEADER_SIZE, linked.base)
    struct.pack_into("<IIHHHHHHIIIIHHIIIIII", header, opt + 32, 0x200, 0x200, 4, 0, 0, 0, 4, 0,
                     0, image_size, HEADER_SIZE, 0, 1, 0, 0x100000, 0x1000, 0x100000, 0x1000,
                     0, 16)
    section = opt + 224
    header[section:section + 8] = b".text\0\0\0"
    struct.pack_into("<IIIIIIHHI", header, section + 8, linked.vsize - HEADER_SIZE, HEADER_SIZE,
                     raw_aligned, HEADER_SIZE, 0, 0, 0, 0, 0xE0000060)
    body = linked.image[HEADER_SIZE:] + bytes(raw_aligned - raw_size)
    with open(path, "wb") as stream:
        stream.write(bytes(header) + body)


def write_map(linked, path):
    """Symbols by address in lld-link's map layout: the name second, the VA second to last."""
    with open(path, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(" tes3xhook\n\n Preferred load address is %016x\n\n" % linked.base)
        stream.write("  Address         Publics by Value              Rva+Base       Lib:Object\n\n")
        for name, va in sorted(linked.symbols.items(), key=lambda item: (item[1], item[0])):
            stream.write(" 0001:%08x       %-26s %016x     %s\n"
                         % ((va - linked.base) & 0xFFFFFFFF, name, va, linked.owners[name]))
