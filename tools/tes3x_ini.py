"""Recover Morrowind.ini sections, keys, and defaults from retail XBE call sites."""

import argparse
import collections
import os
import re
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tes3x_inject import Xbe  # noqa: E402

READERS = {0x001933E0: "str", 0x00193710: "int", 0x00193780: "flt"}
INI_NAME = "D:" + chr(92) + "Morrowind.ini"
BULK = 20


def read_string(x, va, limit=96):
    off = x.va_to_off(va)
    if off is None:
        return None
    raw = bytes(x.data[off:off + limit]).split(b"\0")[0]
    try:
        s = raw.decode("ascii")
    except UnicodeDecodeError:
        return None
    return s if s and all(32 <= ord(c) < 127 for c in s) else None


def scan(x, readers=READERS):
    text = next(s for s in x.sections if s.name == ".text")
    blob = bytes(x.data[text.raw:text.raw + text.rsize])
    base = text.va
    sections = collections.defaultdict(dict)
    computed = 0
    for m in re.finditer(rb"\xE8", blob):
        o = m.start()
        if o + 5 > len(blob):
            continue
        target = base + o + 5 + struct.unpack_from("<i", blob, o + 1)[0]
        kind = readers.get(target)
        if kind is None:
            continue
        pushes, k = [], o - 1
        while k >= 0 and k > o - 0x40 and len(pushes) < 3:
            if blob[k] == 0x68:
                pushes.append(read_string(x, struct.unpack_from("<I", blob, k + 1)[0]))
                k -= 5
                continue
            k -= 1
        if len(pushes) < 2 or pushes[0] in (None, INI_NAME) or pushes[1] is None:
            computed += 1
            continue
        default = pushes[2] if len(pushes) > 2 and pushes[2] not in (None, INI_NAME) else ""
        sections[pushes[0]].setdefault(pushes[1], (kind, default, base + o))
    return sections, computed


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("xbe")
    ap.add_argument("--section", help="only this section (case-insensitive)")
    ap.add_argument("--all", action="store_true", help="list the bulk sections too")
    a = ap.parse_args()

    x = Xbe(open(a.xbe, "rb").read())
    sections, computed = scan(x)
    total = sum(len(v) for v in sections.values())
    print("%d sections, %d keys; %d call sites compute their section or key"
          % (len(sections), total, computed))

    for name in sorted(sections, key=lambda s: (-len(sections[s]), s)):
        if a.section and a.section.lower() != name.lower():
            continue
        keys = sections[name]
        if not a.section and not a.all and len(keys) > BULK:
            print("\n[%s]  %d keys  (bulk; --all to list)" % (name, len(keys)))
            continue
        print("\n[%s]  %d" % (name, len(keys)))
        for key, (kind, default, va) in sorted(keys.items()):
            print("    %-34s %-4s %-18s read at 0x%08X"
                  % (key, kind, ("= " + default) if default else "", va))


if __name__ == "__main__":
    main()
