"""Inject an XBE section and rebuild its packed header tail."""

import argparse
import re
import struct

from tes3x.paths import resource

ENTRY_XOR = {"retail": 0xA8FC57AB, "debug": 0x94859D4B}
THUNK_XOR = {"retail": 0x5B6D40B6, "debug": 0xEFB1F152}

SEC_WRITABLE = 0x01
SEC_PRELOAD = 0x02
SEC_EXECUTABLE = 0x04

HDR_SIZEOF_HEADERS = 0x108
HDR_SIZEOF_IMAGE = 0x10C
HDR_SECTION_COUNT = 0x11C
HDR_SECTION_TABLE = 0x120
HDR_ENTRY = 0x128
HDR_THUNK = 0x158
SECHDR_SIZE = 56
PAGE = 0x1000

DEFAULT_KRNL_DEF = str(resource("hooks", "xboxkrnl.exe.def"))


def _u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def _decode(raw, keys):
    for kind, key in keys.items():
        v = raw ^ key
        if 0x10000 <= v < 0x80000000:
            return v, kind
    raise ValueError("cannot decode 0x%08X" % raw)


class Section:
    __slots__ = ("flags", "va", "vsize", "raw", "rsize", "name",
                 "digest", "head_ref", "tail_ref")


class Xbe:
    def __init__(self, data):
        if data[:4] != b"XBEH":
            raise ValueError("not an XBE")
        self.data = bytearray(data)
        self.base = _u32(data, 0x104)
        self.sizeof_headers = _u32(data, HDR_SIZEOF_HEADERS)
        self.entry, self.kind = _decode(_u32(data, HDR_ENTRY), ENTRY_XOR)
        self.thunk, _ = _decode(_u32(data, HDR_THUNK), THUNK_XOR)
        self.table_off = _u32(data, HDR_SECTION_TABLE) - self.base
        self.sections = []
        for i in range(_u32(data, HDR_SECTION_COUNT)):
            o = self.table_off + i * SECHDR_SIZE
            s = Section()
            (s.flags, s.va, s.vsize, s.raw, s.rsize,
             nameva, _nref, s.head_ref, s.tail_ref) = struct.unpack_from("<9I", data, o)
            s.digest = bytes(data[o + 0x24:o + 0x38])
            end = data.index(b"\0", nameva - self.base)
            s.name = data[nameva - self.base:end].decode("ascii", "replace")
            self.sections.append(s)

    def next_va(self):
        va = max(s.va + s.vsize for s in self.sections)
        return (va + PAGE - 1) & ~(PAGE - 1)

    def thunk_ordinals(self):
        off = self.va_to_off(self.thunk)
        out = []
        while True:
            e = _u32(self.data, off)
            if e == 0:
                break
            out.append(e & 0x7FFFFFFF)
            off += 4
        return out

    def va_to_off(self, va):
        for s in self.sections:
            if s.va <= va < s.va + s.vsize:
                d = va - s.va
                return s.raw + d if d < s.rsize else None
        if va - self.base < self.sizeof_headers:
            return va - self.base
        return None

    def off_to_va(self, off):
        for s in self.sections:
            if s.raw <= off < s.raw + s.rsize:
                return s.va + (off - s.raw)
        if off < self.sizeof_headers:
            return self.base + off
        return None

    def add_section(self, name, payload, vsize, flags):
        va = self.next_va()
        raw = (len(self.data) + PAGE - 1) & ~(PAGE - 1)
        self.data.extend(b"\0" * (raw - len(self.data)))
        self.data.extend(payload)
        s = Section()
        s.flags, s.va, s.vsize, s.raw, s.rsize = flags, va, vsize, raw, len(payload)
        s.name, s.digest = name, b"\0" * 20
        # XeLoadSection requires both refcount pointers; sentinels request fresh words below.
        s.head_ref, s.tail_ref = -1, -2
        self.sections.append(s)
        return va

    def _read_cstr(self, va, wide=False):
        if not va:
            return None
        o = va - self.base
        if wide:
            e = o
            while self.data[e] or self.data[e + 1]:
                e += 2
            return bytes(self.data[o:e + 2])
        e = self.data.index(b"\0", o)
        return bytes(self.data[o:e + 1])

    def rebuild_headers(self):
        """Rebuild the section table and preserve the shared header-page tail."""
        d = self.data
        base = self.base
        n = len(self.sections)

        nlib = _u32(d, 0x160)
        libva = _u32(d, 0x164)
        libs = bytes(d[libva - base:libva - base + nlib * 16]) if nlib and libva else b""
        # the kernel/XAPI version pointers index into that same array
        krnl_off = _u32(d, 0x168) - libva if libva and _u32(d, 0x168) else None
        xapi_off = _u32(d, 0x16C) - libva if libva and _u32(d, 0x16C) else None

        logo_va, logo_sz = _u32(d, 0x170), _u32(d, 0x174)
        logo = bytes(d[logo_va - base:logo_va - base + logo_sz]) if logo_va and logo_sz else b""

        apath_va, afile_va, upath_va = _u32(d, 0x14C), _u32(d, 0x150), _u32(d, 0x154)
        apath = self._read_cstr(apath_va)
        upath = self._read_cstr(upath_va, wide=True)
        # the ansi filename points at the basename inside the pathname, not its own string
        afile_delta = afile_va - apath_va if apath_va and afile_va else None

        alloc = (self.table_off + n * SECHDR_SIZE + 3) & ~3

        def take(size, align=1):
            nonlocal alloc
            alloc = (alloc + align - 1) & ~(align - 1)
            at = base + alloc
            alloc += size
            return at

        # Preserve shared refcount words by their old address.
        ref_map = {}
        for s in self.sections:
            for old in (s.head_ref, s.tail_ref):
                if old and old not in ref_map:
                    ref_map[old] = take(2)
        names = {}
        for s in self.sections:
            if s.name not in names:
                names[s.name] = take(len(s.name) + 1)
        new_lib = take(len(libs), 4) if libs else 0
        new_apath = take(len(apath)) if apath else 0
        new_upath = take(len(upath), 2) if upath else 0
        new_logo = take(len(logo), 4) if logo else 0
        if alloc > PAGE:
            raise ValueError("header region overflow: need 0x%X, have 0x%X" % (alloc, PAGE))

        d[self.table_off:PAGE] = b"\0" * (PAGE - self.table_off)
        for nm, va in names.items():
            d[va - base:va - base + len(nm) + 1] = nm.encode("ascii") + b"\0"
        for blob, at in ((libs, new_lib), (apath, new_apath),
                         (upath, new_upath), (logo, new_logo)):
            if blob:
                d[at - base:at - base + len(blob)] = blob

        for i, s in enumerate(self.sections):
            o = self.table_off + i * SECHDR_SIZE
            struct.pack_into("<9I", d, o, s.flags, s.va, s.vsize, s.raw, s.rsize,
                             names[s.name], 1,
                             ref_map.get(s.head_ref, 0), ref_map.get(s.tail_ref, 0))
            d[o + 0x24:o + 0x38] = s.digest

        if libs:
            struct.pack_into("<I", d, 0x164, new_lib)
            if krnl_off is not None:
                struct.pack_into("<I", d, 0x168, new_lib + krnl_off)
            if xapi_off is not None:
                struct.pack_into("<I", d, 0x16C, new_lib + xapi_off)
        if logo:
            struct.pack_into("<I", d, 0x170, new_logo)
        if apath:
            struct.pack_into("<I", d, 0x14C, new_apath)
            if afile_delta is not None:
                struct.pack_into("<I", d, 0x150, new_apath + afile_delta)
        if upath:
            struct.pack_into("<I", d, 0x154, new_upath)

        struct.pack_into("<I", d, HDR_SECTION_COUNT, n)
        hi = max(s.va + s.vsize for s in self.sections)
        struct.pack_into("<I", d, HDR_SIZEOF_IMAGE, hi - base)
        if alloc > self.sizeof_headers:
            struct.pack_into("<I", d, HDR_SIZEOF_HEADERS, alloc)
            self.sizeof_headers = alloc

    def set_entry(self, va):
        struct.pack_into("<I", self.data, HDR_ENTRY, va ^ ENTRY_XOR[self.kind])

    def patch_call(self, va, target):
        """Retarget a 5-byte call and return its old target."""
        off = self.va_to_off(va)
        if off is None:
            raise ValueError("VA 0x%08X is not in any section" % va)
        if self.data[off] != 0xE8:
            raise ValueError("no call at 0x%08X (found opcode 0x%02X)" % (va, self.data[off]))
        was = va + 5 + struct.unpack_from("<i", self.data, off + 1)[0]
        struct.pack_into("<i", self.data, off + 1, target - (va + 5))
        return was, off


def _mask(buf, ranges):
    b = bytearray(buf)
    for off, n in ranges:
        b[off:off + n] = b"\0" * n
    return bytes(b)


def verify(orig, new, patched=()):
    """Verify unpatched bytes and non-section-table header structures."""
    a, b = Xbe(orig), Xbe(new)
    problems = []

    def blob(x, va, size):
        return bytes(x.data[va - x.base:va - x.base + size])

    def cstr(x, va, wide=False):
        o = va - x.base
        if wide:
            e = o
            while x.data[e] or x.data[e + 1]:
                e += 2
            return bytes(x.data[o:e + 2])
        return bytes(x.data[o:x.data.index(b"\0", o) + 1])

    nlib = _u32(a.data, 0x160)
    if nlib:
        la, lb = _u32(a.data, 0x164), _u32(b.data, 0x164)
        if blob(a, la, nlib * 16) != blob(b, lb, nlib * 16):
            problems.append("library versions changed")
        for off, label in ((0x168, "kernel"), (0x16C, "xapi")):
            if _u32(a.data, off) and (_u32(a.data, off) - la) != (_u32(b.data, off) - lb):
                problems.append("%s library version pointer moved within the array" % label)
    if _u32(a.data, 0x174):
        if (blob(a, _u32(a.data, 0x170), _u32(a.data, 0x174))
                != blob(b, _u32(b.data, 0x170), _u32(b.data, 0x174))):
            problems.append("logo bitmap changed")
    for off, label, wide in ((0x14C, "debug pathname", False),
                             (0x150, "debug filename", False),
                             (0x154, "debug unicode filename", True)):
        if _u32(a.data, off) and cstr(a, _u32(a.data, off), wide) != cstr(b, _u32(b.data, off), wide):
            problems.append("%s changed" % label)

    for i, s in enumerate(a.sections):
        t = b.sections[i]
        for f in ("flags", "va", "vsize", "raw", "rsize", "name", "digest"):
            if getattr(s, f) != getattr(t, f):
                problems.append("section %s: %s changed" % (s.name, f))
    share_a = {}
    share_b = {}
    for s, t in zip(a.sections, b.sections):
        share_a.setdefault(s.head_ref, []).append(s.name)
        share_b.setdefault(t.head_ref, []).append(t.name)
    if sorted(sorted(v) for v in share_a.values()) != sorted(sorted(v) for v in share_b.values()):
        problems.append("shared-page refcount grouping changed")
    # Deliberate patches are masked out; everything else must be untouched.
    ranges = list(patched)
    if _mask(orig, ranges)[0x1000:] != _mask(new[:len(orig)], ranges)[0x1000:]:
        problems.append("existing section data changed")
    return problems


def load_pe(path):
    """Flatten a linked PE into an image blob plus its entry RVA and image base."""
    d = open(path, "rb").read()
    pe = _u32(d, 0x3C)
    if d[pe:pe + 4] != b"PE\0\0":
        raise ValueError("not a PE")
    nsec = struct.unpack_from("<H", d, pe + 6)[0]
    optsz = struct.unpack_from("<H", d, pe + 20)[0]
    entry_rva = _u32(d, pe + 40)
    imgbase = _u32(d, pe + 52)
    sectab = pe + 24 + optsz
    end = 0
    parts = []
    for i in range(nsec):
        o = sectab + i * 40
        vsize, va, rsize, raw = struct.unpack_from("<IIII", d, o + 8)
        if raw:
            # rsize is rounded up to the PE file alignment and can exceed vsize; that
            # padding must not extend the section past its virtual size
            parts.append((va, d[raw:raw + min(rsize, vsize)]))
        end = max(end, va + vsize)
    blob = bytearray(end)
    for va, body in parts:
        blob[va:va + len(body)] = body
    assert len(blob) == end
    # Back bss with file zeroes instead of relying on the loader.
    return bytes(blob), end, entry_rva, imgbase


def write_thunks(x, path, kernel_def):
    names = {}
    for line in open(kernel_def, encoding="utf-8", errors="replace"):
        m = re.match(r"\s+(\w+)(?:@\d+)?\s+@\s+(\d+)", line)
        if m:
            names[int(m.group(2))] = m.group(1)
    ords = x.thunk_ordinals()
    with open(path, "w", newline="\n") as f:
        f.write("/* generated by tes3x_inject.py - kernel thunk slots in the host XBE */\n")
        f.write("#ifndef TES3X_THUNKS_H\n#define TES3X_THUNKS_H\n\n")
        f.write("#define TES3X_THUNK_BASE 0x%08Xu\n\n" % x.thunk)
        for i, o in enumerate(ords):
            f.write("#define THUNK_%s 0x%08Xu /* ordinal %d */\n"
                    % (names.get(o, "ord_%d" % o), x.thunk + i * 4, o))
        f.write("\n#endif\n")
    print("  wrote %s (%d thunks)" % (path, len(ords)))


def call_target(x, va):
    """The VA an existing `call rel32` at va reaches."""
    off = x.va_to_off(va)
    if off is None or x.data[off] != 0xE8:
        raise ValueError("no call rel32 at 0x%08X" % va)
    return va + 5 + struct.unpack_from("<i", x.data, off + 1)[0]


def inject(xbe, payload, out, name=".tes3xhk", hook_entry=False, patch_calls=()):
    """Add a linked payload as a new section of xbe and write the result to out."""
    raw = open(xbe, "rb").read()
    x = Xbe(raw)
    blob, vsize, entry_rva, imgbase = load_pe(payload)
    va = x.next_va()
    if imgbase != va:
        raise ValueError("payload linked at 0x%08X but the section lands at 0x%08X; "
                         "relink with /base:0x%X" % (imgbase, va, va))

    orig_entry = x.entry
    x.add_section(name, blob, vsize, SEC_PRELOAD | SEC_EXECUTABLE | SEC_WRITABLE)
    print("  + section %s VA 0x%08X vsize 0x%X raw 0x%X" % (name, va, vsize, len(blob)))

    if hook_entry:
        x.set_entry(imgbase + entry_rva)
        print("  entry 0x%08X -> 0x%08X (payload must tail-jump back)"
              % (orig_entry, imgbase + entry_rva))

    patched = []
    for site, target in patch_calls:
        was, off = x.patch_call(site, target)
        patched.append((off, 5))
        print("  call at 0x%08X: 0x%08X -> 0x%08X" % (site, was, target))

    x.rebuild_headers()
    blob_out = bytes(x.data)
    problems = verify(raw, blob_out, patched)
    if problems:
        for p in problems:
            print("  FAIL: %s" % p)
        raise ValueError("refusing to write a corrupted XBE")
    print("  verified: headers, section data and refcount sharing preserved")
    open(out, "wb").write(blob_out)
    print("  wrote %s (%d bytes)" % (out, len(blob_out)))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("xbe")
    ap.add_argument("--payload", help="linked PE to inject as a new section")
    ap.add_argument("--name", default=".tes3xhk")
    ap.add_argument("--out")
    ap.add_argument("--hook-entry", action="store_true",
                    help="retarget the XBE entry point at the payload")
    ap.add_argument("--dump-thunks", metavar="HEADER",
                    help="write a C header of kernel thunk slot addresses")
    ap.add_argument("--kernel-def", default=DEFAULT_KRNL_DEF,
                    help="kernel export names (default: hooks/xboxkrnl.exe.def)")
    ap.add_argument("--next-va", action="store_true",
                    help="print the VA a new section would land at, then exit")
    ap.add_argument("--patch-call", action="append", default=[], metavar="VA=TARGET",
                    help="retarget an existing `call rel32` at VA (repeatable)")
    ap.add_argument("--print-call", metavar="VA",
                    help="print the VA an existing `call rel32` reaches, then exit")
    a = ap.parse_args()

    x = Xbe(open(a.xbe, "rb").read())
    print("%s: base 0x%08X entry 0x%08X (%s) thunk 0x%08X sections %d"
          % (a.xbe, x.base, x.entry, x.kind, x.thunk, len(x.sections)))

    if a.next_va:
        print("0x%08X" % x.next_va())
        return
    if a.print_call:
        try:
            print("0x%08X" % call_target(x, int(a.print_call, 16)))
        except ValueError as exc:
            raise SystemExit(str(exc))
        return
    if a.dump_thunks:
        write_thunks(x, a.dump_thunks, a.kernel_def)
    if not a.payload:
        return

    calls = []
    for spec in a.patch_call:
        site, _, target = spec.partition("=")
        if not target:
            ap.error("--patch-call wants VA=TARGET, got %r" % spec)
        calls.append((int(site, 16), int(target, 16)))
    try:
        inject(a.xbe, a.payload, a.out or a.xbe, a.name, a.hook_entry, calls)
    except ValueError as exc:
        raise SystemExit(str(exc))


if __name__ == "__main__":
    main()
