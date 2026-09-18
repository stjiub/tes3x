"""Break down what a Morrowind .ess save is actually made of.

The scene's avoidance ritual - close doors, do not open containers, dump loot on corpses - is a
theory about *what* grows a save. This attributes bytes to record types and counts the two
things that theory is about: changed references (FRMR inside CELL) and persisted actor
inventories (NPCO inside NPCC/CREC).
"""

import argparse
import collections
import os
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


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("saves", nargs="+")
    ap.add_argument("--detail", action="store_true", help="per-record-type breakdown")
    a = ap.parse_args()

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
