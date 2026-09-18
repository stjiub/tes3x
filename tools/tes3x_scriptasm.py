"""Emit a plugin that calls an injected script opcode, by extending a script the engine runs.

The Xbox build ships no way to author a call to an opcode the retail command table does not
carry: the runtime compiler resolves names through that table and returns 0xFFFF on a miss. But
mod scripts reach the engine as compiled bytecode in a plugin's SCDT, which we can assemble.

A call has two encodings, both confirmed by decoding vanilla scripts against the command table.
As a *statement* it is the bare little-endian 16-bit opcode and nothing else - `Disable` in
ahnassiScript is `db 10`, and 0x10DB is exactly what the table gives for Disable. Inside an
*expression* it carries the token byte 'X' first, which is what the engine's instruction-length
scanners key on: `if ( MenuMode == 0 )` compiles its condition to `20 58 20 10 20 3d 3d 20 30`,
mostly ASCII with `58 20 10` for the call. Only the statement form is emitted here, because a
zero-argument command needs no expression context.

Rather than inventing a script and finding somewhere to start it, this borrows one the engine is
already running. A save stores its live global scripts as SCPT records holding only the name, the
local-variable counts and their values - no bytecode and no resume point - so the code comes back
from the masters on every load. Overriding one of those scripts in a plugin loaded last therefore
changes what it runs.

    python tools/tes3x_scriptasm.py --esm "<Data Files>/Morrowind.esm" \\
        --script Main --opcode 0x2001 --out build/tes3xtest.esp
"""

import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tes3x_records import records, subrecords  # noqa: E402

SCRIPT_END = struct.pack("<H", 0x0101)  # the `End` keyword every compiled script closes with
SCHD_NAME = 32  # name[32], then numShorts, numLongs, numFloats, dataSize, localSize


def call_bytes(opcode):
    """A zero-argument command as a statement: the opcode, little-endian, and nothing else."""
    if not 0 < opcode < 0x8000:
        raise SystemExit("opcode 0x%X is out of range; the engine's bound comparisons are signed"
                         % opcode)
    if opcode & 0xFF == 0 or opcode >> 8 == 0:
        raise SystemExit("opcode 0x%04X has a NUL half; keep both bytes non-zero" % opcode)
    return struct.pack("<H", opcode)


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


def build(esm, script, opcode, out, master="Morrowind.esm"):
    flags, subs = find_script(esm, script)
    head = dict(subs)[b"SCHD"]
    name = head[:SCHD_NAME].split(b"\0")[0].decode("latin-1")
    shorts, longs, floats, data_size, local_size = struct.unpack_from("<5I", head, SCHD_NAME)
    if b"SCDT" not in dict(subs):
        raise SystemExit("%s has no compiled data to extend" % name)

    call = call_bytes(opcode)
    rebuilt = []
    for tag, value in subs:
        if tag == b"SCHD":
            value = head[:SCHD_NAME + 12] + struct.pack("<II", data_size + len(call), local_size)
        elif tag == b"SCDT":
            # Appended just before `End`, so the borrowed script does all its own work first and
            # only the two bytes before the terminator are ours. If the engine were to mis-measure
            # a new opcode, the damage stops at the end of the script instead of running through
            # the middle of it. The local layout is untouched, so the save's values still line up.
            if not value.endswith(SCRIPT_END):
                raise SystemExit("%s does not end with `End`; refusing to guess where to append"
                                 % name)
            value = value[:-len(SCRIPT_END)] + call + SCRIPT_END
        elif tag == b"SCTX":
            value = value + ("\r\n; tes3x: opcode 0x%04X appended before End\r\n"
                             % opcode).encode("latin-1")
        rebuilt.append((tag, value))

    body = b"".join(t + struct.pack("<I", len(v)) + v for t, v in rebuilt)
    scpt = b"SCPT" + struct.pack("<III", len(body), 0, flags) + body

    desc = ("tes3x script-extension test: calls opcode 0x%04X from %s" % (opcode, name)
            ).encode("latin-1")
    hedr = (struct.pack("<fI", 1.2, 0) + b"tes3x".ljust(32, b"\0") + desc.ljust(256, b"\0")
            + struct.pack("<I", 1))
    header = (b"HEDR" + struct.pack("<I", len(hedr)) + hedr
              + b"MAST" + struct.pack("<I", len(master) + 1) + master.encode() + b"\0"
              + b"DATA" + struct.pack("<I", 8) + struct.pack("<Q", os.path.getsize(esm)))
    tes3 = b"TES3" + struct.pack("<III", len(header), 0, 0) + header

    with open(out, "wb") as f:
        f.write(tes3 + scpt)
    print("%s: %s, %d -> %d bytes of bytecode, statement %s before End"
          % (out, name, data_size, data_size + len(call), call.hex(" ")))
    print("  locals unchanged: %d short, %d long, %d float, %d bytes"
          % (shorts, longs, floats, local_size))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--esm", required=True, help="master holding the script to extend")
    ap.add_argument("--script", default="Main", help="script to append the call to")
    ap.add_argument("--opcode", default="0x2001", help="opcode to call")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    build(a.esm, a.script, int(a.opcode, 0), a.out)


if __name__ == "__main__":
    main()
