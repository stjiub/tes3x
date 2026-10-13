"""Shared archive store: a large mod packed once into its own archive, named by its SHA-256, which
every build listing it reads from E:\\TES3X\\lib on the Xbox instead of carrying a copy."""

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from tes3x.bsa import tes3_hash, write_bsa

# The Xbox folder every build's tes3xarch.txt names store archives in.
XBOX_LIB = "E:\\TES3X\\lib"
TIERS = (256, 512, 1024)
# Changes whenever packing would produce different bytes for the same files and tier.
PACKING = 1
TEXTURE_EXT = (".dds", ".tga", ".bmp")


def tier_for(cap):
    """The fixed tier a texture cap falls in: the largest not above it."""
    fitting = [tier for tier in TIERS if tier <= cap]
    if not fitting:
        raise ValueError(f"no store tier fits a texture cap of {cap}")
    return fitting[-1]


def packable(key):
    """What the merged archive would pack (tes3x.pack.classify), so loose files stay loose."""
    from tes3x.pack import classify
    return classify(key) == "pack"


def xbox_name(digest):
    """The archive's file name on the Xbox: FATX names hold 42 characters, not a whole digest."""
    return digest[:32]


def xbox_path(digest):
    return f"{XBOX_LIB}\\{xbox_name(digest)}.bsa"


def pc_store(deploy):
    """The PC store a staged build's archives are in: the one its pipeline record names, else
    store/ in the build root two folders above it (as in a container, where the recorded path
    is the host's)."""
    deploy = Path(deploy).resolve()
    try:
        named = json.loads((deploy.parent / ".tes3x-pipeline.json").read_text(
            encoding="utf-8")).get("store")
        if named and Path(named).is_dir():
            return Path(named)
    except (OSError, ValueError):
        pass
    return deploy.parent.parent / "store"


def stamp(mod, tier):
    """What decides an archive's bytes, cheaply: each packable source's path, size and time."""
    digest = hashlib.sha256(f"packing {PACKING} tier {tier}\n".encode())
    for key in sorted(mod.files):
        if packable(key):
            stat = os.stat(mod.files[key])
            digest.update(f"{key}|{stat.st_size}|{stat.st_mtime_ns}\n".encode())
    return digest.hexdigest()


def read_entry(store, digest):
    """The sidecar of a store archive present and whole, or None."""
    try:
        entry = json.loads((Path(store) / f"{digest}.json").read_text(encoding="utf-8"))
        if (entry.get("sha256") == digest
                and (Path(store) / f"{digest}.bsa").stat().st_size == entry["size"]):
            return entry
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def pack_mod(mod, tier, store, cache=None):
    """The store entry {sha256, size, name, tier} for `mod` at `tier`, packing it unless an
    archive from the same sources is already in `store`."""
    from tes3x.build import texture_dims
    from tes3x.convert import convert_cached
    from tes3x.paths import data_dir

    if tier not in TIERS:
        raise ValueError(f"store tier must be one of {', '.join(map(str, TIERS))}")
    store = Path(store)
    store.mkdir(parents=True, exist_ok=True)
    index_path = store / "index.json"
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        index = {}
    key = stamp(mod, tier)
    known = index.get(key)
    if known and read_entry(store, known):
        # tiers can share bytes when no texture exceeds the lower one
        return {**read_entry(store, known), "tier": tier}
    cache = cache or data_dir() / "cache" / "tex"
    files, hashes = [], {}
    work = Path(tempfile.mkdtemp(prefix=".pack-", dir=store))
    try:
        for name in sorted(mod.files):
            if not packable(name):
                continue
            relative = mod.relative.get(name, name).replace("/", "\\")
            digest = tes3_hash(relative)
            if digest in hashes and hashes[digest] != name:
                raise ValueError(f"{mod.name}: {hashes[digest]} and {name} share a BSA hash; "
                                 "store this mod with store = false")
            hashes[digest] = name
            source = mod.files[name]
            dims = texture_dims(source) if name.endswith(TEXTURE_EXT) else None
            if dims and max(dims) > tier:
                data, _ = convert_cached(source, cache, tier)
                converted = work / f"{len(files)}.dds"
                converted.write_bytes(data)
                source = str(converted)
            files.append((relative, source))
        if not files:
            raise ValueError(f"{mod.name} has no packable assets to store")
        staged = work / "archive.bsa"
        write_bsa(staged, files)
        digest = hashlib.sha256(staged.read_bytes()).hexdigest()
        entry = {"sha256": digest, "size": staged.stat().st_size,
                 "name": re.sub(r"[^\x20-\x7e]", "?", mod.name)[:63], "tier": tier}
        target = store / f"{digest}.bsa"
        if read_entry(store, digest) is None:
            os.replace(staged, target)
            (store / f"{digest}.json").write_text(json.dumps(entry), encoding="utf-8")
    finally:
        for path in sorted(work.rglob("*"), reverse=True):
            path.unlink()
        work.rmdir()
    index[key] = digest
    index_path.write_text(json.dumps(index, indent=1), encoding="utf-8")
    return {**read_entry(store, digest), "tier": tier}


def required(manifest, store):
    """(entry, path or None) for each store archive a build manifest lists."""
    return [(entry, Path(store) / f"{entry['sha256']}.bsa"
             if read_entry(store, entry["sha256"]) else None)
            for entry in manifest.get("store", [])]


def check_entries(entries):
    if not isinstance(entries, list) or len(entries) > 64:
        raise ValueError("invalid store list")
    for entry in entries:
        if (not isinstance(entry, dict) or not re.fullmatch(r"[0-9a-f]{64}",
                                                             str(entry.get("sha256")))
                or not isinstance(entry.get("size"), int) or entry["size"] <= 0
                or not isinstance(entry.get("name"), str) or entry.get("tier") not in TIERS):
            raise ValueError("invalid store entry")
    return entries
