#!/usr/bin/env python3
"""Create and populate fixed-offset FATX partitions in a raw Xbox HDD image."""

import argparse
import os
import struct
import time

SECTOR = 512
FATX_MAGIC = b"FATX"
SUPERBLOCK = 4096
DIRENT = 64
NAME_MAX = 42
END_OF_DIR = 0xFF

# byte offset, size - the standard retail layout
PARTITIONS = {
    "X": (0x00080000, 0x2EE00000),
    "Y": (0x2EE80000, 0x2EE00000),
    "Z": (0x5DC80000, 0x2EE00000),
    "C": (0x8CA80000, 0x1F400000),
    "E": (0xABE80000, 0x1312D6000),
}

ATTR_DIRECTORY = 0x10


def fat_time(ts):
    t = time.localtime(ts)
    date = ((t.tm_year - 2000) << 9) | (t.tm_mon << 5) | t.tm_mday
    tm = (t.tm_hour << 11) | (t.tm_min << 5) | (t.tm_sec // 2)
    return (date << 16) | tm


class Fatx:
    def __init__(self, img, offset, size, cluster_size=16384):
        self.img = img
        self.offset = offset
        self.size = size
        self.cluster_size = cluster_size
        self.cluster_count = size // cluster_size
        self.fat32 = self.cluster_count >= 0xFFF0
        self.fat_entry = 4 if self.fat32 else 2
        fat_bytes = self.cluster_count * self.fat_entry
        self.fat_offset = offset + SUPERBLOCK
        self.fat_size = (fat_bytes + 4095) // 4096 * 4096
        self.data_offset = self.fat_offset + self.fat_size
        self.next_free = 1
        self.fat = bytearray(self.fat_size)

    @classmethod
    def mount(cls, img, offset, size):
        """Attach to an already-formatted partition instead of reformatting it."""
        img.seek(offset)
        sb = img.read(SUPERBLOCK)
        if sb[:4] != FATX_MAGIC:
            raise ValueError(f"no FATX magic at 0x{offset:X}")
        _vid, cluster_sectors, _copies = struct.unpack_from("<IIH", sb, 4)
        fs = cls(img, offset, size, cluster_sectors * SECTOR)
        img.seek(fs.fat_offset)
        fs.fat = bytearray(img.read(fs.fat_size))
        fs.next_free = 2
        return fs

    def is_free(self, cluster):
        o = cluster * self.fat_entry
        v = (struct.unpack_from("<I", self.fat, o)[0] if self.fat32
             else struct.unpack_from("<H", self.fat, o)[0])
        return v == 0

    def find_free(self, n):
        """First run of n consecutive free clusters."""
        c = max(self.next_free, 2)
        while c + n <= self.cluster_count:
            if all(self.is_free(c + i) for i in range(n)):
                return c
            c += 1
        raise RuntimeError("partition full")

    def read_dir(self, cluster):
        """Existing directory entries as (name, attrs, first, size, stamp)."""
        blob = bytearray()
        c = cluster
        seen = set()
        while c is not None and c not in seen:
            seen.add(c)
            self.img.seek(self.cluster_pos(c))
            blob += self.img.read(self.cluster_size)
            o = c * self.fat_entry
            v = (struct.unpack_from("<I", self.fat, o)[0] if self.fat32
                 else struct.unpack_from("<H", self.fat, o)[0])
            end = 0xFFFFFFF8 if self.fat32 else 0xFFF8
            c = None if v >= end or v == 0 else v
        out = []
        for i in range(0, len(blob), DIRENT):
            e = blob[i:i+DIRENT]
            if not e or e[0] in (END_OF_DIR, 0x00):
                break
            if e[0] == 0xE5:
                continue
            nm = e[2:2+e[0]].decode("latin-1")
            first, sz, stamp = struct.unpack_from("<III", e, 0x2C)
            out.append((nm, e[1], first, sz, stamp))
        return out

    def format(self, volume_id=0x0BADF00D):
        sb = bytearray(SUPERBLOCK)
        sb[0:4] = FATX_MAGIC
        struct.pack_into("<IIH", sb, 4, volume_id, self.cluster_size // SECTOR, 1)
        for i in range(4 + 10, SUPERBLOCK):
            sb[i] = 0xFF
        self.img.seek(self.offset)
        self.img.write(sb)
        # cluster 0 and 1 are reserved; 1 marks the root directory chain end
        self.set_fat(0, 0xFFFFFFF8 if self.fat32 else 0xFFF8)
        self.set_fat(1, 0xFFFFFFFF if self.fat32 else 0xFFFF)
        self.next_free = 2

    def set_fat(self, cluster, value):
        o = cluster * self.fat_entry
        if self.fat32:
            struct.pack_into("<I", self.fat, o, value & 0xFFFFFFFF)
        else:
            struct.pack_into("<H", self.fat, o, value & 0xFFFF)

    def flush_fat(self):
        self.img.seek(self.fat_offset)
        self.img.write(self.fat)

    def cluster_pos(self, cluster):
        return self.data_offset + (cluster - 1) * self.cluster_size

    def alloc_chain(self, nbytes):
        n = max(1, (nbytes + self.cluster_size - 1) // self.cluster_size)
        first = self.find_free(n)
        for i in range(n):
            c = first + i
            last = (i == n - 1)
            self.set_fat(c, (0xFFFFFFFF if self.fat32 else 0xFFFF) if last else c + 1)
        self.next_free = first + n
        return first, n

    def write_file(self, src):
        size = os.path.getsize(src)
        first, n = self.alloc_chain(size)
        self.img.seek(self.cluster_pos(first))
        with open(src, "rb") as f:
            remaining = size
            while remaining:
                chunk = f.read(min(remaining, 1 << 20))
                self.img.write(chunk)
                remaining -= len(chunk)
        pad = n * self.cluster_size - size
        if pad:
            self.img.write(b"\x00" * pad)
        return first, size

    def dir_blob(self, entries):
        blob = bytearray()
        for name, attrs, cluster, size, stamp in entries:
            if len(name) > NAME_MAX:
                raise ValueError(f"name exceeds FATX limit: {name}")
            e = bytearray(DIRENT)
            e[0] = len(name)
            e[1] = attrs
            e[2:2 + len(name)] = name.encode("latin-1")
            for i in range(2 + len(name), 2 + NAME_MAX):
                e[i] = 0xFF
            struct.pack_into("<IIIII", e, 0x2C, cluster, size, stamp, stamp, stamp)
            blob += e
        blob += bytes([END_OF_DIR]) + b"\xFF" * (DIRENT - 1)
        return bytes(blob)

    def write_at(self, first, n, blob):
        self.img.seek(self.cluster_pos(first))
        self.img.write(blob)
        pad = n * self.cluster_size - len(blob)
        if pad > 0:
            self.img.write(bytes([255]) * pad)

    def write_dir(self, entries):
        blob = self.dir_blob(entries)
        first, n = self.alloc_chain(len(blob))
        self.write_at(first, n, blob)
        return first

    def write_dir_at(self, cluster, entries):
        """Write a directory into an already-reserved cluster; the root lives at cluster 1."""
        blob = self.dir_blob(entries)
        n = max(1, (len(blob) + self.cluster_size - 1) // self.cluster_size)
        if n > 1:
            extra, _ = self.alloc_chain((n - 1) * self.cluster_size)
            chain = [cluster] + [extra + i for i in range(n - 1)]
            end = 0xFFFFFFFF if self.fat32 else 0xFFFF
            for i, c in enumerate(chain):
                self.set_fat(c, end if i == len(chain) - 1 else chain[i + 1])
        self.write_at(cluster, n, blob)

        first, n = self.alloc_chain(len(blob))
        self.img.seek(self.cluster_pos(first))
        self.img.write(blob)
        self.img.write(b"\xFF" * (n * self.cluster_size - len(blob)))
        return first

    def build_entries(self, src):
        """Write a host directory's contents recursively; returns its directory entries."""
        entries = []
        for name in sorted(os.listdir(src)):
            full = os.path.join(src, name)
            st = os.stat(full)
            if os.path.isdir(full):
                c = self.write_dir(self.build_entries(full))
                entries.append((name, ATTR_DIRECTORY, c, 0, fat_time(st.st_mtime)))
            else:
                c, size = self.write_file(full)
                entries.append((name, 0, c, size, fat_time(st.st_mtime)))
        return entries


class FatxReader:
    def __init__(self, img, offset):
        self.img = img
        self.offset = offset
        img.seek(offset)
        sb = img.read(SUPERBLOCK)
        if sb[:4] != FATX_MAGIC:
            raise ValueError("no FATX magic at that offset")
        self.volume_id, cluster_sectors, self.fat_copies = struct.unpack_from("<IIH", sb, 4)
        self.cluster_size = cluster_sectors * SECTOR

    def bind(self, size):
        self.cluster_count = size // self.cluster_size
        self.fat32 = self.cluster_count >= 0xFFF0
        self.fat_entry = 4 if self.fat32 else 2
        fat_bytes = self.cluster_count * self.fat_entry
        self.fat_offset = self.offset + SUPERBLOCK
        self.fat_size = (fat_bytes + 4095) // 4096 * 4096
        self.data_offset = self.fat_offset + self.fat_size
        self.img.seek(self.fat_offset)
        self.fat = self.img.read(self.fat_size)
        return self

    def next_cluster(self, c):
        o = c * self.fat_entry
        v = (struct.unpack_from("<I", self.fat, o)[0] if self.fat32
             else struct.unpack_from("<H", self.fat, o)[0])
        end = 0xFFFFFFF8 if self.fat32 else 0xFFF8
        return None if v >= end else v

    def chain(self, first):
        out, c, seen = [], first, set()
        while c is not None and c not in seen:
            seen.add(c)
            out.append(c)
            c = self.next_cluster(c)
        return out

    def read_chain(self, first, size=None):
        data = bytearray()
        for c in self.chain(first):
            self.img.seek(self.data_offset + (c - 1) * self.cluster_size)
            data += self.img.read(self.cluster_size)
            if size is not None and len(data) >= size:
                break
        return bytes(data[:size]) if size is not None else bytes(data)

    def listdir(self, cluster=1):
        out = []
        for e in range(0, len(self.read_chain(cluster)), DIRENT):
            raw = self.read_chain(cluster)[e:e + DIRENT]
            if not raw or raw[0] in (END_OF_DIR, 0x00):
                break
            if raw[0] == 0xE5:
                continue
            nlen = raw[0]
            name = raw[2:2 + nlen].decode("latin-1")
            attrs = raw[1]
            first, size = struct.unpack_from("<II", raw, 0x2C)
            out.append((name, attrs, first, size))
        return out

    def walk(self, cluster=1, prefix=""):
        for name, attrs, first, size in self.listdir(cluster):
            path = prefix + "/" + name if prefix else name
            if attrs & ATTR_DIRECTORY:
                yield from self.walk(first, path)
            else:
                yield path, first, size


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("image", help="raw .img to create or update")
    ap.add_argument("--partition", default="E", choices=sorted(PARTITIONS))
    ap.add_argument("--tree", required=True, help="host directory to write as the partition root")
    ap.add_argument("--size", type=float, default=8.0, help="image size in GB (new images only)")
    args = ap.parse_args()

    new = not os.path.exists(args.image)
    if new:
        with open(args.image, "wb") as f:
            f.truncate(int(args.size * 2**30))
        print(f"created sparse image {args.image} ({args.size} GB)")

    with open(args.image, "r+b") as img:
        # every partition must be a valid FATX volume or the BIOS reports error 09
        for name in ("C", "X", "Y", "Z", "E"):
            off, size = PARTITIONS[name]
            fs = Fatx(img, off, size)
            fs.format()
            if name == args.partition:
                entries = fs.build_entries(args.tree)
                fs.write_dir_at(1, entries)
                used = (fs.next_free - 1) * fs.cluster_size
                note = f"  <- {len(entries)} root entries, {used/2**20:.1f} MB"
            else:
                fs.write_dir_at(1, [])
                note = "  (empty)"
            fs.flush_fat()
            kind = "FAT32" if fs.fat32 else "FAT16"
            print(f"  {name}: 0x{off:09X}  {size/2**30:6.2f} GB  {fs.cluster_count:>7} clusters  "
                  f"{kind}{note}")


if __name__ == "__main__":
    main()
