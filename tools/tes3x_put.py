"""Add one file to a raw Xbox HDD image in place."""

import argparse
import os
import struct
import sys
import time

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])
from tes3x_fatx import (ATTR_DIRECTORY, DIRENT, END_OF_DIR, NAME_MAX, PARTITIONS,  # noqa: E402
                      Fatx, FatxReader, fat_time)

DELETED = 0xE5


def resolve_dir(fs, parts):
    cluster = 1
    for i, part in enumerate(parts):
        for name, attrs, first, _size in fs.listdir(cluster):
            if name.lower() == part.lower() and attrs & ATTR_DIRECTORY:
                cluster = first
                break
        else:
            raise SystemExit("no such directory: %s" % "/".join(parts[:i + 1]))
    return cluster


def free_slot(fs, cluster):
    """Image offset of a reusable directory entry, preferring a deleted one."""
    end = None
    for c in fs.chain(cluster):
        base = fs.data_offset + (c - 1) * fs.cluster_size
        fs.img.seek(base)
        blob = fs.img.read(fs.cluster_size)
        for off in range(0, len(blob), DIRENT):
            tag = blob[off]
            if tag == DELETED:
                return base + off, False
            if tag in (END_OF_DIR, 0x00) and end is None:
                end = base + off
    if end is None:
        raise SystemExit("directory is full and extending it is not implemented")
    return end, True


def add_entry(img, reader, cluster, name, attrs, first, nbytes):
    """Write a directory entry for an allocated chain into directory `cluster`."""
    if len(name) > NAME_MAX:
        raise SystemExit("name exceeds the %d-character FATX limit: %s" % (NAME_MAX, name))
    for existing, _attrs, _first, _sz in reader.listdir(cluster):
        if existing.lower() == name.lower():
            raise SystemExit("%s already exists; remove it first" % name)
    slot, was_end = free_slot(reader, cluster)
    ent = bytearray(DIRENT)
    ent[0] = len(name)
    ent[1] = attrs
    ent[2:2 + len(name)] = name.encode("latin-1")
    for i in range(2 + len(name), 2 + NAME_MAX):
        ent[i] = 0xFF
    stamp = fat_time(time.time())
    struct.pack_into("<IIIII", ent, 0x2C, first, nbytes, stamp, stamp, stamp)
    img.seek(slot)
    img.write(bytes(ent))
    if was_end:
        img.write(bytes([END_OF_DIR]) + b"\xFF" * (DIRENT - 1))


def make_dirs(img, dest, partition="E"):
    """Create each missing directory of dest; returns its cluster."""
    off, size = PARTITIONS[partition]
    cluster = 1
    for part in [p for p in dest.replace("\\", "/").split("/") if p]:
        reader = FatxReader(img, off).bind(size)
        hit = [e for e in reader.listdir(cluster)
               if e[0].lower() == part.lower() and e[1] & ATTR_DIRECTORY]
        if hit:
            cluster = hit[0][2]
            continue
        fs = Fatx.mount(img, off, size)
        first, _n = fs.alloc_chain(fs.cluster_size)
        img.seek(fs.cluster_pos(first))
        img.write(b"\xFF" * fs.cluster_size)
        fs.flush_fat()
        add_entry(img, FatxReader(img, off).bind(size), cluster, part, ATTR_DIRECTORY, first, 0)
        cluster = first
    return cluster


def put_file(img, src, dest, name, partition="E"):
    """Write host file src into directory dest of a partition on an open, writable image."""
    off, size = PARTITIONS[partition]
    reader = FatxReader(img, off).bind(size)
    parts = [p for p in dest.replace("\\", "/").split("/") if p]
    cluster = resolve_dir(reader, parts)
    if any(e[0].lower() == name.lower() for e in reader.listdir(cluster)):
        raise SystemExit("%s already exists in %s; remove it first" % (name, dest or "/"))

    fs = Fatx.mount(img, off, size)
    first, nbytes = fs.write_file(src)
    fs.flush_fat()
    add_entry(img, FatxReader(img, off).bind(size), cluster, name, 0, first, nbytes)
    return first, nbytes


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image")
    ap.add_argument("src", help="host file to add")
    ap.add_argument("dest", help="directory within the partition, e.g. 'TDATA/42530005/Data Files'")
    ap.add_argument("--partition", default="E", choices=sorted(PARTITIONS))
    ap.add_argument("--name", help="name on the image (default: the source basename)")
    a = ap.parse_args()

    name = a.name or os.path.basename(a.src)
    with open(a.image, "r+b") as img:
        first, nbytes = put_file(img, a.src, a.dest, name, a.partition)
    print("  %s -> %s/%s  %d bytes, cluster %d" % (a.src, a.dest, name, nbytes, first))


if __name__ == "__main__":
    main()
