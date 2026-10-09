"""Append zero-argument opcode statements to a compiled plugin script."""

import argparse
import os
import struct

from tes3x.records import records, subrecords  # noqa: E402

SCRIPT_END = struct.pack("<H", 0x0101)  # the `End` keyword every compiled script closes with
SCHD_NAME = 32  # name[32], then numShorts, numLongs, numFloats, dataSize, localSize


def call_bytes(opcodes):
    """Zero-argument commands as statements: each opcode, little-endian, and nothing else."""
    out = b""
    for opcode in opcodes:
        if not 0 < opcode < 0x8000:
            raise SystemExit("opcode 0x%X is out of range; the engine's bound comparisons are "
                             "signed" % opcode)
        if opcode & 0xFF == 0 or opcode >> 8 == 0:
            raise SystemExit("opcode 0x%04X has a NUL half; keep both bytes non-zero" % opcode)
        out += struct.pack("<H", opcode)
    return out


def find_script(esm, name):
    want = name.lower().encode("latin-1")
    for tag, flags, body in records(esm):
        if tag != b"SCPT":
            continue
        subs = list(subrecords(body))
        head = dict(subs).get(b"SCHD")
        if head and head[:SCHD_NAME].split(b"\0")[0].lower() == want:
            return flags, subs
    raise SystemExit("no SCPT named %r in %s" % (name, esm))


def build(esm, script, opcodes, out, master="Morrowind.esm"):
    flags, subs = find_script(esm, script)
    head = dict(subs)[b"SCHD"]
    name = head[:SCHD_NAME].split(b"\0")[0].decode("latin-1")
    shorts, longs, floats, data_size, local_size = struct.unpack_from("<5I", head, SCHD_NAME)
    if b"SCDT" not in dict(subs):
        raise SystemExit("%s has no compiled data to extend" % name)

    call = call_bytes(opcodes)
    names = ", ".join("0x%04X" % o for o in opcodes)
    rebuilt = []
    for tag, value in subs:
        if tag == b"SCHD":
            value = head[:SCHD_NAME + 12] + struct.pack("<II", data_size + len(call), local_size)
        elif tag == b"SCDT":
            # Append before End to preserve the script's work and saved local layout.
            if not value.endswith(SCRIPT_END):
                raise SystemExit("%s does not end with `End`; refusing to guess where to append"
                                 % name)
            value = value[:-len(SCRIPT_END)] + call + SCRIPT_END
        elif tag == b"SCTX":
            value = value + ("\r\n; tes3x: %s appended before End\r\n"
                             % names).encode("latin-1")
        rebuilt.append((tag, value))

    body = b"".join(t + struct.pack("<I", len(v)) + v for t, v in rebuilt)
    scpt = b"SCPT" + struct.pack("<III", len(body), 0, flags) + body

    desc = ("tes3x script-extension test: calls %s from %s" % (names, name)).encode("latin-1")
    hedr = (struct.pack("<fI", 1.2, 0) + b"tes3x".ljust(32, b"\0") + desc.ljust(256, b"\0")
            + struct.pack("<I", 1))
    header = (b"HEDR" + struct.pack("<I", len(hedr)) + hedr
              + b"MAST" + struct.pack("<I", len(master) + 1) + master.encode() + b"\0"
              + b"DATA" + struct.pack("<I", 8) + struct.pack("<Q", os.path.getsize(esm)))
    tes3 = b"TES3" + struct.pack("<III", len(header), 0, 0) + header

    with open(out, "wb") as f:
        f.write(tes3 + scpt)
    print("%s: %s, %d -> %d bytes of bytecode, %s as %s before End"
          % (out, name, data_size, data_size + len(call), names, call.hex(" ")))
    print("  locals unchanged: %d short, %d long, %d float, %d bytes"
          % (shorts, longs, floats, local_size))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--esm", required=True, help="master holding the script to extend")
    ap.add_argument("--script", default="Main", help="script to append the call to")
    ap.add_argument("--opcode", action="append", default=[],
                    help="opcode to call; repeat for several, in order")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    build(a.esm, a.script, [int(o, 0) for o in a.opcode or ["0x2001"]], a.out)


if __name__ == "__main__":
    main()
