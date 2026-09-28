#!/usr/bin/env python3
"""Read and write hash-only Xbox Morrowind BSA archives."""

import os
import struct

VERSION = 0x100
MASK = 0xFFFFFFFF


def tes3_hash(path):
    """TES3 BSA name hash. Returns (low, high)."""
    path = path.lower().replace("/", "\\")
    chars = [ord(c) & 0xFF for c in path]
    mid = len(chars) >> 1

    low = 0
    for i in range(mid):
        low ^= chars[i] << ((i * 8) % 32)
    low &= MASK

    high = 0
    for i in range(mid, len(chars)):
        temp = (chars[i] << (((i - mid) * 8) % 32)) & MASK
        high = (high ^ temp) & MASK
        n = temp & 0x1F
        if n:
            high = ((high >> n) | (high << (32 - n))) & MASK
    return low, high


class Bsa:
    """An Xbox archive, which holds only name hashes, or a PC one, which also holds the names."""

    def __init__(self, path):
        self.path = path
        with open(path, "rb") as f:
            version, hash_off, count = struct.unpack("<3I", f.read(12))
            if version != VERSION:
                raise ValueError(f"unexpected BSA version 0x{version:X}")
            if hash_off < count * 8:
                raise ValueError("BSA hash table overlaps its file records")
            self.count = count
            recs = struct.unpack(f"<{count*2}I", f.read(count * 8))
            names = None
            if hash_off > count * 8:
                offsets = struct.unpack(f"<{count}I", f.read(count * 4))
                blob = f.read(hash_off - count * 12)
                names = [blob[o:blob.index(b"\0", o)].decode("cp1252") for o in offsets]
            f.seek(12 + hash_off)
            hashes = struct.unpack(f"<{count*2}I", f.read(count * 8))
        self.data_start = 12 + hash_off + count * 8
        self.entries = [dict(size=recs[i*2], offset=recs[i*2+1],
                             hash=(hashes[i*2], hashes[i*2+1])) for i in range(count)]
        if names is not None:
            for entry, name in zip(self.entries, names):
                entry["name"] = name
        self.named = names is not None
        self.by_hash = {e["hash"]: e for e in self.entries}

    def read(self, entry):
        with open(self.path, "rb") as f:
            f.seek(self.data_start + entry["offset"])
            return f.read(entry["size"])

    def contains(self, name):
        return tes3_hash(name) in self.by_hash


def extract_bsa(path, target):
    """Unpack a named archive into target; returns the file count."""
    archive = Bsa(path)
    if not archive.named:
        raise ValueError(f"{path} lists no file names, so it cannot be unpacked")
    with open(path, "rb") as source:
        for entry in archive.entries:
            parts = entry["name"].replace("\\", "/").split("/")
            if any(part in ("", ".", "..") for part in parts):
                raise ValueError(f"{path}: unsafe name {entry['name']!r}")
            destination = os.path.join(target, *parts)
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            source.seek(archive.data_start + entry["offset"])
            with open(destination, "wb") as stream:
                stream.write(source.read(entry["size"]))
    return archive.count


def write_bsa(out_path, files, base=None, progress=None, drop=()):
    """Build an Xbox BSA, optionally overriding entries from a base archive.
    Base entries whose hash is in `drop` are left out."""
    new = {}
    for name, src in files:
        new[tes3_hash(name)] = ("file", src, os.path.getsize(src))

    recs = []
    if base:
        for e in base.entries:
            if e["hash"] not in new and e["hash"] not in drop:
                recs.append((e["hash"], "base", e["offset"], e["size"]))
    for h, (_, src, size) in new.items():
        recs.append((h, "file", src, size))
    recs.sort(key=lambda r: r[0])

    count = len(recs)
    offset = 0
    table = []
    for h, kind, ref, size in recs:
        table += [size, offset]
        offset += size

    with open(out_path, "wb") as f:
        f.write(struct.pack("<3I", VERSION, count * 8, count))
        f.write(struct.pack(f"<{count*2}I", *table))
        for h, *_ in recs:
            f.write(struct.pack("<2I", *h))
        src_fh = open(base.path, "rb") if base else None
        for i, (h, kind, ref, size) in enumerate(recs):
            if kind == "base":
                src_fh.seek(base.data_start + ref)
                remaining = size
                while remaining:
                    chunk = src_fh.read(min(remaining, 1 << 20))
                    f.write(chunk)
                    remaining -= len(chunk)
            else:
                with open(ref, "rb") as s:
                    while True:
                        chunk = s.read(1 << 20)
                        if not chunk:
                            break
                        f.write(chunk)
            if progress and i % 2000 == 0:
                progress(i, count)
        if src_fh:
            src_fh.close()
    return count, offset


if __name__ == "__main__":
    import sys
    b = Bsa(sys.argv[1])
    print(f"{b.count} files, data at 0x{b.data_start:X}, "
          f"{sum(e['size'] for e in b.entries)/1048576:.1f} MB of content")
