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
    a = ap.parse_args()

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
