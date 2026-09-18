#!/usr/bin/env python3
"""Parse an Xbox XBE: header, certificate, sections, strings."""

import argparse
import re
import struct

ENTRY_XOR = {"retail": 0xA8FC57AB, "debug": 0x94859D4B}
THUNK_XOR = {"retail": 0x5B6D40B6, "debug": 0xEFB1F152}
SECTION_FLAGS = [(0x1, "W"), (0x2, "PRELOAD"), (0x4, "X"), (0x8, "INSERTFILE")]


class Xbe:
    def __init__(self, data):
        if data[:4] != b"XBEH":
            raise ValueError("not an XBE")
        self.data = data
        (self.base, self.size_headers, self.size_image) = struct.unpack_from("<3I", data, 0x104)
        self.cert_addr, self.num_sections, self.sec_addr = struct.unpack_from("<3I", data, 0x118)
        self.raw_entry = struct.unpack_from("<I", data, 0x128)[0]
        self.raw_thunk = struct.unpack_from("<I", data, 0x158)[0]

    def off(self, va):
        return va - self.base

    def cstr(self, va):
        o = self.off(va)
        return self.data[o:self.data.index(b"\x00", o)].decode("latin-1")

    def decode(self, raw, table):
        for kind, key in table.items():
            val = raw ^ key
            if self.base <= val < self.base + self.size_image:
                return val, kind
        return raw, "unknown"

    def sections(self):
        out = []
        for i in range(self.num_sections):
            o = self.off(self.sec_addr) + i * 56
            flags, va, vsize, raw, rsize, name_addr = struct.unpack_from("<6I", self.data, o)
            out.append(dict(name=self.cstr(name_addr), flags=flags, va=va,
                            vsize=vsize, raw=raw, rsize=rsize))
        return out

    def cert(self):
        o = self.off(self.cert_addr)
        size, timedate, title_id = struct.unpack_from("<3I", self.data, o)
        title = self.data[o + 12:o + 92].decode("utf-16-le", "replace").split("\x00")[0]
        return dict(title=title, title_id=title_id)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("xbe")
    ap.add_argument("--strings", metavar="REGEX", help="search printable strings")
    args = ap.parse_args()

    data = open(args.xbe, "rb").read()
    x = Xbe(data)
    cert = x.cert()
    entry, ekind = x.decode(x.raw_entry, ENTRY_XOR)
    thunk, tkind = x.decode(x.raw_thunk, THUNK_XOR)

    print(f"\n{args.xbe}  ({len(data)} bytes)")
    print(f"  title        {cert['title']!r}  id=0x{cert['title_id']:08X}")
    print(f"  base         0x{x.base:08X}   image 0x{x.size_image:X} ({x.size_image/1048576:.1f} MB)")
    print(f"  entry        0x{entry:08X}  ({ekind})")
    print(f"  kernel thunk 0x{thunk:08X}  ({tkind})")
    print(f"\n  {'name':<10} {'VA':>10} {'vsize':>10} {'raw':>10} {'rsize':>10}  flags")
    for s in x.sections():
        f = "|".join(n for b, n in SECTION_FLAGS if s["flags"] & b) or "-"
        print(f"  {s['name']:<10} 0x{s['va']:08X} {s['vsize']:>10} 0x{s['raw']:08X} {s['rsize']:>10}  {f}")

    if args.strings:
        pat = re.compile(args.strings.encode(), re.I)
        print(f"\n  strings matching /{args.strings}/:")
        for m in re.finditer(rb"[ -~]{4,}", data):
            if pat.search(m.group()):
                print(f"    0x{m.start():06X}  {m.group().decode('latin-1')}")
    print()


if __name__ == "__main__":
    main()
