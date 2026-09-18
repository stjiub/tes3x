"""Disassemble XBE virtual addresses and find code references.

String searches report file offsets; use --off2va before finding references.
"""

import argparse
import os
import re
import struct
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tes3x_inject import Xbe  # noqa: E402

OBJCOPY = os.environ.get("LLVM_OBJCOPY", r"C:\msys64\mingw64\bin\llvm-objcopy.exe")
OBJDUMP = os.environ.get("LLVM_OBJDUMP", r"C:\msys64\mingw64\bin\llvm-objdump.exe")


def off_to_va(x, off):
    for s in x.sections:
        if s.raw <= off < s.raw + s.rsize:
            return s.va + (off - s.raw)
    return None


def va_to_off(x, va):
    return x.va_to_off(va)


def disasm(x, va, length):
    off = va_to_off(x, va)
    if off is None:
        raise SystemExit("VA 0x%08X is not backed by file data" % va)
    blob = bytes(x.data[off:off + length])
    tmp = tempfile.mkdtemp()
    raw, obj = os.path.join(tmp, "w.bin"), os.path.join(tmp, "w.o")
    open(raw, "wb").write(blob)
    subprocess.run([OBJCOPY, "-I", "binary", "-O", "elf32-i386",
                    "--rename-section=.data=.text,code", raw, obj], check=True)
    out = subprocess.run([OBJDUMP, "-d", "--no-show-raw-insn", obj],
                         capture_output=True, text=True, check=True).stdout
    for line in out.splitlines():
        m = re.match(r"^\s+([0-9a-f]+):\s+(.*)$", line)
        if not m:
            continue
        addr = va + int(m.group(1), 16)
        body = m.group(2)
        # objdump's branch targets are window-relative; rewrite them to real VAs
        body = re.sub(r"0x([0-9a-f]+) <[^>]*>",
                      lambda b: "0x%08X" % (va + int(b.group(1), 16)), body)
        print("  0x%08X  %s" % (addr, body))


def xrefs(x, target_va, limit=40):
    pat = struct.pack("<I", target_va)
    found = []
    for s in x.sections:
        if not (s.flags & 0x04) or not s.rsize:
            continue
        blob = bytes(x.data[s.raw:s.raw + s.rsize])
        for m in re.finditer(re.escape(pat), blob):
            # the immediate usually follows a 1-byte opcode
            found.append((s.va + m.start() - 1, s.name))
            if len(found) >= limit:
                return found
    return found


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("xbe")
    ap.add_argument("--at", help="VA to disassemble from (hex)")
    ap.add_argument("--len", default="0x100", help="bytes to disassemble (default 0x100)")
    ap.add_argument("--xref", help="find code references to this VA (hex)")
    ap.add_argument("--off2va", help="convert a file offset from --strings into a VA (hex)")
    a = ap.parse_args()

    x = Xbe(open(a.xbe, "rb").read())
    if a.off2va:
        off = int(a.off2va, 16)
        va = off_to_va(x, off)
        print("file 0x%X -> VA 0x%08X" % (off, va) if va else "file 0x%X: unmapped" % off)
        if va and not a.xref:
            a.xref = "0x%X" % va
    if a.xref:
        target = int(a.xref, 16)
        hits = xrefs(x, target)
        print("%d reference(s) to 0x%08X:" % (len(hits), target))
        for va, sec in hits:
            print("  0x%08X  (%s)" % (va, sec))
    if a.at:
        disasm(x, int(a.at, 16), int(a.len, 16))


if __name__ == "__main__":
    main()
