"""Emit a plugin containing cell references whose master indices cannot resolve."""

import argparse
import os
import struct
import sys

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])
from tes3x_records import records, subrecords  # noqa: E402

CELL_INTERIOR = 0x01
# Subrecords that describe the cell itself; everything after the first FRMR is a reference.
CELL_HEADER = (b"NAME", b"DATA", b"RGNN", b"WHGT", b"AMBI")

# Every named interior XboxChargenRevamped.esp can drop a new character into, so whichever
# start it rolls lands in a cell carrying the bad references.
DEFAULT_CELLS = (
    "Imperial Prison Ship",
    "Seyda Neen, Census and Excise Office",
    "Balmora, Guild of Mages",
    "Caldera, Shenk's Shovel",
    "Lonely Shipwreck, Upper Level",
    "Maar Gan, Andus Tradehouse",
    "Tharys Ancestral Tomb",
    "Vivec, Arena Pit",
    "Vivec, Hlaalu Prison Cells",
)


def sub(tag, body):
    return tag + struct.pack("<I", len(body)) + body


def zstr(text):
    return text.encode("latin-1") + b"\0"


def cell_headers(esm, cells):
    """Each target cell's own subrecords, so an override matches the cell exactly."""
    want, found = list(cells), {}
    for tag, _flags, data in records(esm):
        if tag != b"CELL":
            continue
        subs = list(subrecords(data))
        if subs[0][0] != b"NAME":
            continue
        name = subs[0][1].split(b"\0")[0].decode("latin-1")
        if name not in want or name in found:
            continue
        head = []
        for t, v in subs:
            if t == b"FRMR":
                break
            if t in CELL_HEADER:
                head.append((t, v))
        found[name] = b"".join(sub(t, v) for t, v in head)
        if len(found) == len(want):
            break
    missing = [c for c in want if c not in found]
    if missing:
        raise SystemExit("not in %s: %s" % (os.path.basename(esm), ", ".join(missing)))
    return [(c, found[c]) for c in want]


def reference(index, obj, at=(0.0, 0.0, 0.0)):
    return (sub(b"FRMR", struct.pack("<I", index))
            + sub(b"NAME", zstr(obj))
            + sub(b"DATA", struct.pack("<6f", at[0], at[1], at[2], 0.0, 0.0, 0.0)))


def gmst(name, value):
    """A visible override, so a run can tell "plugin not loaded" from "references not parsed"."""
    body = sub(b"NAME", zstr(name)) + sub(b"STRV", zstr(value))
    return b"GMST" + struct.pack("<III", len(body), 0, 0) + body


def build(out, esm, cells, obj, indices, marker, setting, master_name=None):
    if cells:
        targets = cell_headers(esm, cells)
    else:
        targets = [("tes3x orphan test",
                    sub(b"NAME", zstr("tes3x orphan test"))
                    + sub(b"DATA", struct.pack("<Iii", CELL_INTERIOR, 0, 0)))]

    records_out = []
    for n, (_name, head) in enumerate(targets):
        first = n * (len(indices) + 1) + 1
        # A reference the plugin itself creates: mod index 0 is the legitimate path, so it
        # survives the drop and is visible in game. Absent marker means the plugin never loaded.
        body = head + reference(first, obj, marker)
        body += b"".join(reference(i << 24 | first + k + 1, obj)
                         for k, i in enumerate(indices))
        records_out.append(b"CELL" + struct.pack("<III", len(body), 0, 0) + body)
    if setting:
        records_out.insert(0, gmst(*setting))
    record = b"".join(records_out)

    master = master_name or os.path.basename(esm)
    desc = ("tes3x orphan reproducer: mod indices %s against one master"
            % ", ".join(str(i) for i in indices)).encode("latin-1")
    hedr = (struct.pack("<fI", 1.2, 0) + b"tes3x".ljust(32, b"\0")
            + desc.ljust(256, b"\0") + struct.pack("<I", len(records_out)))
    header = (sub(b"HEDR", hedr) + sub(b"MAST", zstr(master))
              + sub(b"DATA", struct.pack("<Q", os.path.getsize(esm))))
    tes3 = b"TES3" + struct.pack("<III", len(header), 0, 0) + header

    with open(out, "wb") as f:
        f.write(tes3 + record)
    print("%s: %d bytes, %d cell(s), %d reference(s) each at mod index %s, one master (%s)"
          % (out, len(tes3) + len(record), len(targets), len(indices),
             ", ".join(str(i) for i in indices), master))
    for name, _ in targets:
        print("   %s" % name)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("out")
    ap.add_argument("--esm", required=True, help="the master to declare and clone the cell from")
    ap.add_argument("--master-name",
                    help="master filename to declare (default: basename of --esm)")
    ap.add_argument("--cell", action="append", metavar="NAME",
                    help="cell to override, repeatable; defaults to every alternate start")
    ap.add_argument("--no-cells", action="store_true",
                    help="add an unreachable test cell instead of overriding real ones")
    ap.add_argument("--object", default="misc_com_bottle_02")
    ap.add_argument("--gmst", default="",
                    help="optional NAME=VALUE GMST override; omitted by default")
    ap.add_argument("--marker", default="61,-135,40",
                    help="where to place the plugin's own visible reference (x,y,z)")
    ap.add_argument("--indices", default="2,3,4",
                    help="mod indices to stamp, all past the single master (default 2,3,4)")
    a = ap.parse_args()

    indices = [int(v, 0) for v in a.indices.split(",")]
    if any(not 1 <= i <= 0xFF for i in indices):
        raise SystemExit("mod indices must fit one byte and be nonzero")
    cells = [] if a.no_cells else (a.cell or list(DEFAULT_CELLS))
    marker = tuple(float(v) for v in a.marker.split(","))
    if len(marker) != 3:
        raise SystemExit("--marker wants x,y,z")
    setting = None
    if a.gmst:
        name, _, value = a.gmst.partition("=")
        setting = (name, value)
    build(a.out, a.esm, cells, a.object, indices, marker, setting, a.master_name)


if __name__ == "__main__":
    main()
