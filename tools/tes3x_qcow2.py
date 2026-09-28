#!/usr/bin/env python3
"""qcow2 access for Xbox HDD images: convert to raw, read through a backing chain, and
create a copy-on-write overlay so a test run never copies the whole disk."""

import os
import struct

MAGIC = b"QFI\xfb"
L2_OFFSET_MASK = 0x00FFFFFFFFFFFE00
L2_ZERO = 1
OVERLAY_CLUSTER_BITS = 16
EXT_BACKING_FORMAT = 0xE2792ACA


def is_qcow2(path):
    with open(path, "rb") as f:
        return f.read(4) == MAGIC


COPIED = 1 << 63


def image_size(path):
    """The guest disk size of a raw or qcow2 image."""
    if not is_qcow2(path):
        return os.path.getsize(path)
    with Qcow2(path) as q:
        return q.size


def create_overlay(path, backing, clusters=None):
    """qcow2 v3 whose unwritten clusters read from `backing`, a raw or qcow2 image. `clusters` maps
    a guest cluster index to its full contents, as CowView.changed() returns them."""
    cs = 1 << OVERLAY_CLUSTER_BITS
    per_l2 = cs // 8
    size = image_size(backing)
    fmt = b"qcow2" if is_qcow2(backing) else b"raw"
    l1_size = -(-size // (cs * per_l2))
    clusters = clusters or {}
    tables = sorted({c // per_l2 for c in clusters})
    # Clusters: 0 header, 1 L1 table, 2 refcount table, 3 refcount block, then L2 tables and data.
    host = 4 + len(tables) + len(clusters)
    if l1_size * 8 > cs or host > cs // 2:
        raise ValueError("overlay layout does not fit one L1 table and one refcount block")
    name = os.path.abspath(backing).replace("\\", "/").encode()
    ext = struct.pack(">II", EXT_BACKING_FORMAT, len(fmt)) + fmt.ljust(8, b"\0") + struct.pack(">II", 0, 0)
    name_off = 104 + len(ext)
    header = struct.pack(">4sIQIIQIIQQIIQQQQII", MAGIC, 3, name_off, len(name), OVERLAY_CLUSTER_BITS,
                         size, 0, l1_size, cs, 2 * cs, 1, 0, 0, 0, 0, 0, 4, 104)
    image = bytearray(host * cs)
    image[:len(header)] = header
    image[104:104 + len(ext)] = ext
    image[name_off:name_off + len(name)] = name
    struct.pack_into(">Q", image, 2 * cs, 3 * cs)
    for c in range(host):
        struct.pack_into(">H", image, 3 * cs + 2 * c, 1)
    l2_host = {}
    for i, index in enumerate(tables):
        l2_host[index] = (4 + i) * cs
        struct.pack_into(">Q", image, cs + 8 * index, l2_host[index] | COPIED)
    for i, (guest, data) in enumerate(sorted(clusters.items())):
        if len(data) != cs:
            raise ValueError("cluster %d is %d bytes, expected %d" % (guest, len(data), cs))
        at = (4 + len(tables) + i) * cs
        image[at:at + cs] = data
        struct.pack_into(">Q", image, l2_host[guest // per_l2] + 8 * (guest % per_l2), at | COPIED)
    with open(path, "wb") as f:
        f.write(image)


class CowView:
    """Seekable read/write view of a raw or qcow2 image that keeps writes in memory by overlay
    cluster, so a file can be added to a run's disk without touching the clean image."""

    def __init__(self, backing):
        self.f = open_image(backing)
        self.size = image_size(backing)
        self.cs = 1 << OVERLAY_CLUSTER_BITS
        self.pos = 0
        self.dirty = {}

    def _cluster(self, c):
        if c not in self.dirty:
            self.f.seek(c * self.cs)
            self.dirty[c] = bytearray(self.f.read(self.cs).ljust(self.cs, b"\0"))
        return self.dirty[c]

    def seek(self, pos, whence=0):
        self.pos = pos if whence == 0 else (self.pos + pos if whence == 1 else self.size + pos)
        return self.pos

    def tell(self):
        return self.pos

    def read(self, n=-1):
        if n < 0:
            n = self.size - self.pos
        out = bytearray()
        while n > 0 and self.pos < self.size:
            c, within = divmod(self.pos, self.cs)
            take = min(n, self.cs - within)
            if c in self.dirty:
                out += self.dirty[c][within:within + take]
            else:
                self.f.seek(self.pos)
                out += self.f.read(take)
            self.pos += take
            n -= take
        return bytes(out)

    def write(self, data):
        data = memoryview(bytes(data))
        while data:
            c, within = divmod(self.pos, self.cs)
            take = min(len(data), self.cs - within)
            self._cluster(c)[within:within + take] = data[:take]
            self.pos += take
            data = data[take:]
        return len(data)

    def changed(self):
        """Written clusters that differ from the backing image."""
        out = {}
        for c, data in self.dirty.items():
            self.f.seek(c * self.cs)
            if self.f.read(self.cs).ljust(self.cs, b"\0") != data:
                out[c] = bytes(data)
        return out

    def close(self):
        self.f.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class Qcow2:
    def __init__(self, path):
        self.f = open(path, "rb")
        h = self.f.read(72)
        if h[:4] != MAGIC:
            raise ValueError("not a qcow2 image")
        self.version = struct.unpack_from(">I", h, 4)[0]
        backing_off = struct.unpack_from(">Q", h, 8)[0]
        backing_len = struct.unpack_from(">I", h, 16)[0]
        self.cluster_bits = struct.unpack_from(">I", h, 20)[0]
        self.size = struct.unpack_from(">Q", h, 24)[0]
        self.l1_size = struct.unpack_from(">I", h, 36)[0]
        self.l1_offset = struct.unpack_from(">Q", h, 40)[0]
        self.cluster_size = 1 << self.cluster_bits
        self.l2_entries = self.cluster_size // 8
        self.f.seek(self.l1_offset)
        raw = self.f.read(self.l1_size * 8)
        self.l1 = struct.unpack(f">{self.l1_size}Q", raw)
        self._l2_cache = {}
        self.backing = None
        if backing_off:
            self.f.seek(backing_off)
            self.backing = self.f.read(backing_len).decode()

    def close(self):
        self.f.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _l2(self, l2_offset):
        if l2_offset not in self._l2_cache:
            self.f.seek(l2_offset)
            raw = self.f.read(self.cluster_size)
            self._l2_cache[l2_offset] = struct.unpack(f">{self.l2_entries}Q", raw)
            if len(self._l2_cache) > 64:
                self._l2_cache.pop(next(iter(self._l2_cache)))
        return self._l2_cache[l2_offset]

    def cluster_host_offset(self, guest_cluster):
        l1_idx = guest_cluster // self.l2_entries
        if l1_idx >= self.l1_size:
            return None
        l2_off = self.l1[l1_idx] & L2_OFFSET_MASK
        if not l2_off:
            return None
        entry = self._l2(l2_off)[guest_cluster % self.l2_entries]
        if entry & (1 << 62):
            raise NotImplementedError("compressed cluster")
        host = entry & L2_OFFSET_MASK
        if not host and entry & L2_ZERO:
            return 0
        return host or None

    def read_cluster(self, guest_cluster):
        host = self.cluster_host_offset(guest_cluster)
        if host is None:
            return None
        if host == 0:
            return bytes(self.cluster_size)
        self.f.seek(host)
        return self.f.read(self.cluster_size)

    def to_raw(self, out_path, progress=None):
        total = self.size // self.cluster_size
        written = 0
        with open(out_path, "wb") as out:
            out.truncate(self.size)
            for c in range(total):
                data = self.read_cluster(c)
                if data is None:
                    continue
                out.seek(c * self.cluster_size)
                out.write(data)
                written += 1
                if progress and written % 256 == 0:
                    progress(c, total, written)
        return written, total


class ImageFile:
    """Seekable read-only view of a qcow2 image as its guest disk, falling back to its backing
    image, raw or qcow2, for clusters the overlay has not written."""

    def __init__(self, path):
        self.q = Qcow2(path)
        backing = self.q.backing
        if backing and not os.path.isabs(backing):
            # QEMU resolves a relative backing name against the overlay's folder.
            backing = os.path.join(os.path.dirname(os.path.abspath(path)), backing)
        self.base = open_image(backing) if backing else None
        self.pos = 0

    def seek(self, pos, whence=0):
        self.pos = pos if whence == 0 else (self.pos + pos if whence == 1 else self.q.size + pos)
        return self.pos

    def tell(self):
        return self.pos

    def read(self, n=-1):
        if n < 0:
            n = self.q.size - self.pos
        out = bytearray()
        cs = self.q.cluster_size
        while n > 0 and self.pos < self.q.size:
            c, within = divmod(self.pos, cs)
            take = min(n, cs - within)
            host = self.q.cluster_host_offset(c)
            if host:
                self.q.f.seek(host + within)
                out += self.q.f.read(take)
            elif host is None and self.base:
                self.base.seek(self.pos)
                out += self.base.read(take)
            else:
                out += bytes(take)
            self.pos += take
            n -= take
        return bytes(out)

    def close(self):
        self.q.f.close()
        if self.base:
            self.base.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def open_image(path):
    """A raw image as a plain file, or a qcow2 image through its backing chain."""
    return ImageFile(path) if is_qcow2(path) else open(path, "rb")


if __name__ == "__main__":
    import sys
    q = Qcow2(sys.argv[1])
    print(f"qcow2 v{q.version}  virtual {q.size/2**30:.1f} GB  "
          f"cluster {q.cluster_size}  L1 entries {q.l1_size}")
    if len(sys.argv) > 2:
        def prog(c, t, w):
            print(f"\r  {100*c//t}%  {w} clusters", end="", flush=True)
        w, t = q.to_raw(sys.argv[2], prog)
        print(f"\r  wrote {w} allocated clusters of {t}")
