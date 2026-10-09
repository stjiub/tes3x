"""Read the hook log or another file from an Xbox HDD image's E: partition (raw or qcow2)."""

import argparse
import mmap
import re
import sys

from tes3x.fatx import PARTITIONS, FatxReader  # noqa: E402
from tes3x.qcow2 import is_qcow2, open_image  # noqa: E402

LOG_START = b"0 ms entry.free_kb "
LOG_NAME = "tes3xlog.txt"


def read_file(image, name, part="E"):
    """Read one file from the root of an Xbox HDD partition."""
    off, size = PARTITIONS[part]
    with open_image(image) as img:
        fs = FatxReader(img, off).bind(size)
        hit = {e[0].lower(): e for e in fs.listdir(1)}.get(name.lower())
        return fs.read_chain(hit[2], hit[3]) if hit else None


def raw_logs(image, part):
    """Every hook-log session in the raw bytes, whether or not a directory entry covers it,
    in case the entry lags the data. An overlay holds every guest write, so its file is
    scanned whole; a raw image only across the partition."""
    off, size = PARTITIONS[part]
    found = []
    with open(image, "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as m:
        start, end = (0, len(m)) if is_qcow2(image) else (off, min(off + size, len(m)))
        i = m.find(LOG_START, start, end)
        while i != -1:
            j = i
            while j < end and m[j] not in (0, 0xFF):
                j += 1
            found.append(bytes(m[i:j]))
            i = m.find(LOG_START, j, end)
    return found


def session_of(text):
    hit = re.search(rb"diag\.session (0x[0-9A-F]{8})", text)
    return hit.group(1) if hit else None


def read_log(image, part="E"):
    """The fullest copy of the hook log: the directory entry's bytes, or the raw
    partition's copy of the same session when that is longer or the entry is missing."""
    body = read_file(image, LOG_NAME, part)
    raw = raw_logs(image, part)
    want = session_of(body) if body else None
    same = [r for r in raw if want and session_of(r) == want]
    best = max(same or ([] if body else raw), key=len, default=None)
    if best and (body is None or len(best) > len(body)):
        print("[log recovered from raw partition: %s]" % (
            "not in the directory" if body is None else
            "%d bytes past the directory entry" % (len(best) - len(body))), file=sys.stderr)
        body = best
    return body


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("image")
    ap.add_argument("--partition", default="E", choices=sorted(PARTITIONS))
    ap.add_argument("--name", default=LOG_NAME, help="file to read, or '-' to list the root")
    ap.add_argument("--out", help="write the bytes here instead of decoding to stdout")
    a = ap.parse_args()

    if a.name == "-" or a.name.lower() != LOG_NAME:
        off, size = PARTITIONS[a.partition]
        with open_image(a.image) as img:
            fs = FatxReader(img, off).bind(size)
            root = fs.listdir(1)
            if a.name == "-":
                for name, attrs, _first, sz in sorted(root):
                    print("  %-44s %10d %s" % (name, sz, "<dir>" if attrs & 0x10 else ""))
                return
            hit = {e[0].lower(): e for e in root}.get(a.name.lower())
            if not hit:
                print("%s not found in %s: of %s" % (a.name, a.partition, a.image))
                print("root holds: %s" % ", ".join(sorted(e[0] for e in root)[:20]))
                raise SystemExit(1)
            body = fs.read_chain(hit[2], hit[3])
    else:
        body = read_log(a.image, a.partition)
        if body is None:
            print("%s not found in %s: of %s, nor any log text in its raw bytes"
                  % (a.name, a.partition, a.image))
            raise SystemExit(1)

    if a.out:
        with open(a.out, "wb") as f:
            f.write(body)
        print("%s: %d bytes -> %s" % (a.name, len(body), a.out))
        return
    sys.stdout.write(body.decode("ascii", "replace"))


if __name__ == "__main__":
    main()
