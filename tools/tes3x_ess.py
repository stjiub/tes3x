"""Measure Morrowind save records, changed references, and inventories."""

import argparse
import collections
import os
import struct
import sys

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])
from tes3x_records import records, subrecords  # noqa: E402

HEADER = 16


def analyse(path):
    by_tag = collections.Counter()
    bytes_by_tag = collections.Counter()
    subs = collections.Counter()
    accounted = 0

    for tag, _flags, data in records(path):
        by_tag[tag] += 1
        bytes_by_tag[tag] += len(data) + HEADER
        accounted += len(data) + HEADER
        if tag in (b"CELL", b"NPCC", b"CREC", b"CNTC"):
            for stag, sdata in subrecords(data):
                subs[(tag, stag)] += 1

    size = os.path.getsize(path)
    return dict(path=path, size=size, accounted=accounted, records=sum(by_tag.values()),
                by_tag=by_tag, bytes_by_tag=bytes_by_tag, subs=subs,
                frmr=subs[(b"CELL", b"FRMR")],
                npco=subs[(b"NPCC", b"NPCO")] + subs[(b"CREC", b"NPCO")]
                + subs[(b"CNTC", b"NPCO")])


def bindings(path):
    """Return the MAST list and changed-reference binding histogram."""
    masters = []
    hist = collections.Counter()
    for tag, _flags, data in records(path):
        if tag == b"TES3":
            for stag, sdata in subrecords(data):
                if stag == b"MAST":
                    masters.append(sdata.rstrip(bytes(1)).decode("latin-1"))
        elif tag == b"CELL":
            for stag, sdata in subrecords(data):
                if stag == b"FRMR" and len(sdata) >= 4:
                    hist[struct.unpack("<I", sdata[:4])[0] >> 24] += 1
    return masters, hist


def poison_references(path, out, cell_name=None, from_index=1, to_index=0xFF):
    """Copy a save while making selected references fail master-index resolution."""
    if os.path.abspath(path) == os.path.abspath(out):
        raise ValueError("output must differ from the source save")
    if not 0 <= from_index <= 0xFF or not 0 <= to_index <= 0xFF:
        raise ValueError("reference indices must fit one byte")

    data = bytearray(open(path, "rb").read())
    record_off = 0
    cells = 0
    patched = 0
    while record_off < len(data):
        if record_off + HEADER > len(data):
            raise ValueError(f"truncated record header at {record_off}")
        tag, size, _unknown, _flags = struct.unpack_from("<4sIII", data, record_off)
        body = record_off + HEADER
        end = body + size
        if end > len(data):
            raise ValueError(f"{tag!r} exceeds file at {record_off}")
        if tag == b"CELL":
            cells += 1
            wanted = cell_name is None
            if cell_name is not None:
                for stag, sdata in subrecords(bytes(data[body:end])):
                    if (stag == b"NAME"
                            and sdata.rstrip(bytes(1)).decode("latin-1").casefold()
                            == cell_name.casefold()):
                        wanted = True
                        break
            if not wanted:
                record_off = end
                continue
            sub_off = body
            while sub_off < end:
                if sub_off + 8 > end:
                    raise ValueError(f"truncated CELL subrecord at {sub_off}")
                stag, ssize = struct.unpack_from("<4sI", data, sub_off)
                value_off = sub_off + 8
                sub_end = value_off + ssize
                if sub_end > end:
                    raise ValueError(f"{stag!r} exceeds CELL at {sub_off}")
                if stag == b"FRMR" and ssize >= 4:
                    value = struct.unpack_from("<I", data, value_off)[0]
                    if value >> 24 == from_index:
                        value = (value & 0x00FFFFFF) | (to_index << 24)
                        struct.pack_into("<I", data, value_off, value)
                        patched += 1
                        break
                sub_off = sub_end
        record_off = end

    if not patched:
        where = f" in CELL {cell_name!r}" if cell_name is not None else ""
        raise ValueError(f"no CELL reference used mod index {from_index}{where}")
    with open(out, "wb") as stream:
        stream.write(data)
    return cells, patched


def poison_first_per_cell(path, out, from_index=1, to_index=0xFF):
    return poison_references(path, out, from_index=from_index, to_index=to_index)


def poison_first_in_cell(path, out, cell_name, from_index=1, to_index=0xFF):
    return poison_references(path, out, cell_name=cell_name,
                             from_index=from_index, to_index=to_index)


def report_bindings(paths):
    total = collections.Counter()
    orders = {}
    for path in paths:
        masters, hist = bindings(path)
        total.update(hist)
        orders.setdefault(tuple(masters), []).append(path)
        n = sum(hist.values())
        print(f"  {os.path.basename(path)[:34]:<34} {len(masters):>4} MAST {n:>6} FRMR "
              f"{hist[0]:>6} idx0")

    print()
    print(f"  {len(orders)} distinct MAST order(s) across {len(paths)} save(s)")
    order = max(orders, key=lambda k: len(orders[k]))
    n = sum(total.values())
    print()
    print(f"  {'index':>5} {'count':>8} {'share':>7}  plugin")
    for idx, count in sorted(total.items(), key=lambda kv: -kv[1]):
        if idx == 0:
            who = "created at runtime (or orphaned - indistinguishable)"
        elif idx <= len(order):
            who = order[idx - 1]
        else:
            who = "OUT OF RANGE for this MAST list"
        print(f"  {idx:>5} {count:>8} {100 * count / n:>6.1f}%  {who}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("saves", nargs="+")
    ap.add_argument("--detail", action="store_true", help="per-record-type breakdown")
    ap.add_argument("--bindings", action="store_true",
                    help="MAST list and what changed refs are bound to")
    ap.add_argument("--poison-first-per-cell", metavar="OUT",
                    help="copy one save, changing its first index-1 CELL reference to index 255")
    ap.add_argument("--poison-first-in-cell", nargs=2, metavar=("CELL", "OUT"),
                    help="copy one save, poisoning one reference in the named CELL")
    a = ap.parse_args()

    if a.poison_first_per_cell or a.poison_first_in_cell:
        if len(a.saves) != 1:
            raise SystemExit("poisoning requires exactly one input save")
        try:
            if a.poison_first_in_cell:
                cell_name, out = a.poison_first_in_cell
                cells, patched = poison_first_in_cell(a.saves[0], out, cell_name)
            else:
                out = a.poison_first_per_cell
                cells, patched = poison_first_per_cell(a.saves[0], out)
        except ValueError as exc:
            raise SystemExit(str(exc))
        print(f"  {out}: {patched}/{cells} CELL records poisoned")
        return

    if a.bindings:
        return report_bindings(sorted(a.saves))

    rows = []
    for path in sorted(a.saves):
        try:
            rows.append(analyse(path))
        except ValueError as exc:
            print(f"  {os.path.basename(path)}: {exc}")

    print(f"  {'save':<28} {'bytes':>9} {'recs':>6} {'FRMR':>6} {'NPCO':>6} "
          f"{'cellKB':>7} {'npccKB':>7} {'ok':>5}")
    for r in rows:
        name = os.path.basename(r["path"])[:28]
        print(f"  {name:<28} {r['size']:>9} {r['records']:>6} {r['frmr']:>6} {r['npco']:>6} "
              f"{r['bytes_by_tag'][b'CELL'] // 1024:>7} {r['bytes_by_tag'][b'NPCC'] // 1024:>7} "
              f"{100 * r['accounted'] // r['size']:>4}%")

    if a.detail and rows:
        for r in rows:
            print(f"\n  {os.path.basename(r['path'])}")
            for tag, nbytes in r["bytes_by_tag"].most_common(12):
                print(f"    {tag.decode('latin-1'):<6} {r['by_tag'][tag]:>6} recs "
                      f"{nbytes:>9} bytes  {100 * nbytes / r['size']:>5.1f}%")


if __name__ == "__main__":
    main()
