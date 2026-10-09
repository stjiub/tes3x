"""Read MCP catalogue and patch records against the original PC executable."""

import argparse
import json
import os
import re
import shutil
import struct
import subprocess
import tempfile
import tomllib

from tes3x_paths import writable

STEAM_DEFAULT = (r"C:\Program Files (x86)\Steam\steamapps\common\Morrowind" if os.name == "nt"
                 else os.path.expanduser("~/.steam/steam/steamapps/common/Morrowind"))


def pc_morrowind():
    """TES3X_PC_MORROWIND, else [paths] pc_morrowind in tes3x.local.toml, else Steam's default."""
    if os.environ.get("TES3X_PC_MORROWIND"):
        return os.environ["TES3X_PC_MORROWIND"]
    for config in (os.path.join(os.getcwd(), "tes3x.local.toml"),
                   writable("tes3x.local.toml")):
        if os.path.isfile(config):
            with open(config, "rb") as f:
                path = tomllib.load(f).get("paths", {}).get("pc_morrowind")
            if path:
                return os.path.join(os.path.dirname(os.path.abspath(config)), path)
    return STEAM_DEFAULT


MW = pc_morrowind()
OBJCOPY = (os.environ.get("LLVM_OBJCOPY") or shutil.which("llvm-objcopy")
           or r"C:\msys64\mingw64\bin\llvm-objcopy.exe")
OBJDUMP = (os.environ.get("LLVM_OBJDUMP") or shutil.which("llvm-objdump")
           or r"C:\msys64\mingw64\bin\llvm-objdump.exe")


class Pe:
    """Just enough PE to map an RVA to file data."""

    def __init__(self, path):
        self.data = bytearray(open(path, "rb").read())
        pe = struct.unpack_from("<I", self.data, 0x3C)[0]
        if self.data[pe:pe + 4] != b"PE\0\0":
            raise SystemExit("%s is not a PE image" % path)
        nsec = struct.unpack_from("<H", self.data, pe + 6)[0]
        optsz = struct.unpack_from("<H", self.data, pe + 20)[0]
        self.base = struct.unpack_from("<I", self.data, pe + 24 + 28)[0]
        self.sections = []
        t = pe + 24 + optsz
        for i in range(nsec):
            h = t + i * 40
            name = bytes(self.data[h:h + 8]).rstrip(b"\0").decode("latin-1")
            vsize, va, rsize, raw = struct.unpack_from("<IIII", self.data, h + 8)
            self.sections.append((name, va, vsize, raw, rsize))

    def va_to_off(self, va):
        rva = va - self.base
        for name, sva, vsize, raw, rsize in self.sections:
            if sva <= rva < sva + max(vsize, rsize):
                d = rva - sva
                return raw + d if d < rsize else None
        return None

    def read(self, va, n):
        off = self.va_to_off(va)
        if off is None:
            return None
        return bytes(self.data[off:off + n])


def load_catalogue():
    path = os.path.join(MW, "mcpatch", "describe.json")
    cats = json.load(open(path, encoding="utf-8"))
    by_id = {}
    for c in cats:
        for p in c["patches"]:
            p = dict(p)
            p["category"] = c["category"]
            by_id[p["id"]] = p
    return cats, by_id


def load_records():
    root = os.path.join(MW, "mcpatch")
    cand = []
    for d in os.listdir(root):
        p = os.path.join(root, d, "patch")
        if os.path.isfile(p):
            cand.append(p)
    if not cand:
        raise SystemExit("no mcpatch/<sha256>/patch found under %s" % root)
    # every copy observed is byte-identical; take the largest
    blob = open(max(cand, key=os.path.getsize), "rb").read()
    groups = {}
    o = 0
    while o + 12 <= len(blob):
        g, rva, ln = struct.unpack_from("<III", blob, o)
        o += 12
        groups.setdefault(g, []).append((rva, blob[o:o + ln]))
        o += ln
    return groups


def installed_ids():
    path = os.path.join(MW, "mcpatch", "installed")
    if not os.path.isfile(path):
        return None
    blob = open(path, "rb").read()
    if blob[:4] != b"MCP2":
        return None
    return {struct.unpack_from("<I", blob, o)[0]
            for o in range(4, len(blob) - 3, 4)}


def disasm(blob, va):
    """Disassemble a byte window as if it were loaded at `va`."""
    tmp = tempfile.mkdtemp()
    raw, obj = os.path.join(tmp, "w.bin"), os.path.join(tmp, "w.o")
    open(raw, "wb").write(blob)
    subprocess.run([OBJCOPY, "-I", "binary", "-O", "elf32-i386",
                    "--rename-section=.data=.text,code", raw, obj], check=True)
    out = subprocess.run([OBJDUMP, "-d", "--no-show-raw-insn", obj],
                         capture_output=True, text=True, check=True).stdout
    lines = []
    for line in out.splitlines():
        m = re.match(r"^\s+([0-9a-f]+):\s+(.*)$", line)
        if not m:
            continue
        body = re.sub(r"0x([0-9a-f]+) <[^>]*>",
                      lambda b: "0x%08X" % ((va + int(b.group(1), 16)) & 0xFFFFFFFF), m.group(2))
        lines.append("0x%08X  %s" % (va + int(m.group(1), 16), body))
    return lines


def cmd_at(args):
    exe = "Morrowind.exe" if args.patched else "Morrowind.Original.exe"
    pe = Pe(os.path.join(MW, exe))
    va, n = int(args.at, 0), int(args.len, 0)
    blob = pe.read(va, n)
    if blob is None:
        raise SystemExit("VA 0x%08X is not backed by file data in %s" % (va, exe))
    print("%s  0x%08X..0x%08X" % (exe, va, va + n))
    for line in disasm(blob, va):
        print("  " + line)


def cmd_sections(args):
    for exe in ("Morrowind.Original.exe", "Morrowind.exe"):
        pe = Pe(os.path.join(MW, exe))
        print("%s  base 0x%08X" % (exe, pe.base))
        for name, sva, vsize, raw, rsize in pe.sections:
            print("  %-8s va 0x%08X  vsize 0x%06X  raw 0x%06X  rsize 0x%06X"
                  % (name, pe.base + sva, vsize, raw, rsize))
        print()


def cmd_list(args, cats, by_id, groups):
    inst = installed_ids() or set()
    for c in cats:
        if args.category and args.category.lower() not in c["category"].lower():
            continue
        print("\n== %s (%d)" % (c["category"], len(c["patches"])))
        for p in sorted(c["patches"], key=lambda p: p["id"]):
            recs = groups.get(p["id"], [])
            nbytes = sum(len(b) for _, b in recs)
            mark = "*" if p["id"] in inst else " "
            print("  %s%4d  %3d rec %6d B  %s"
                  % (mark, p["id"], len(recs), nbytes, p["brief"]))
    print("\n* = present in mcpatch/installed")


def cmd_show(args, cats, by_id, groups):
    p = by_id.get(args.show)
    if not p:
        raise SystemExit("no patch with id=%d" % args.show)
    print("id=%d  [%s]  %s" % (p["id"], p["category"], p["brief"]))
    print()
    print(p["description"])
    recs = groups.get(p["id"], [])
    print("\n%d records, %d bytes" % (recs and len(recs) or 0,
                                      sum(len(b) for _, b in recs)))
    for rva, b in sorted(recs):
        print("  0x%08X  %4d" % (0x400000 + rva, len(b)))


def cmd_dump(args, cats, by_id, groups):
    p = by_id.get(args.dump)
    if not p:
        raise SystemExit("no patch with id=%d" % args.dump)
    pe = Pe(os.path.join(MW, "Morrowind.Original.exe"))
    ctx = int(args.context, 0)
    print("id=%d  [%s]  %s\n" % (p["id"], p["category"], p["brief"]))
    for rva, new in sorted(groups.get(p["id"], [])):
        va = pe.base + rva
        lo, hi = va - ctx, va + len(new) + ctx
        old = pe.read(lo, hi - lo)
        if old is None:
            print("== 0x%08X  %d bytes  (not backed by file data)\n" % (rva, len(new)))
            continue
        merged = bytearray(old)
        merged[ctx:ctx + len(new)] = new
        print("== 0x%08X  %d bytes" % (va, len(new)))
        if args.bytes:
            print("   old %s" % old[ctx:ctx + len(new)].hex())
            print("   new %s" % new.hex())
        a, b = disasm(bytes(old), lo), disasm(bytes(merged), lo)
        w = max([len(x) for x in a] or [0])
        for i in range(max(len(a), len(b))):
            la = a[i] if i < len(a) else ""
            lb = b[i] if i < len(b) else ""
            sep = "   " if la == lb else " | "
            print("   %-*s%s%s" % (w, la, sep, lb))
        print()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--category")
    ap.add_argument("--show", type=int)
    ap.add_argument("--dump", type=int)
    ap.add_argument("--context", default="0", help="bytes of context each side (default 0)")
    ap.add_argument("--bytes", action="store_true", help="also print raw hex")
    ap.add_argument("--at", help="disassemble from this VA instead")
    ap.add_argument("--len", default="0x80", help="bytes for --at (default 0x80)")
    ap.add_argument("--patched", action="store_true",
                    help="read Morrowind.exe (MCP applied) rather than the original")
    ap.add_argument("--sections", action="store_true")
    args = ap.parse_args()

    if args.sections:
        return cmd_sections(args)
    if args.at:
        return cmd_at(args)

    cats, by_id = load_catalogue()
    groups = load_records()
    if args.show is not None:
        cmd_show(args, cats, by_id, groups)
    elif args.dump is not None:
        cmd_dump(args, cats, by_id, groups)
    else:
        cmd_list(args, cats, by_id, groups)


if __name__ == "__main__":
    main()
