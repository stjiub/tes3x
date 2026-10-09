#!/usr/bin/env python3
"""The build manifest every TES3X build folder carries, and the checks made against it."""

import argparse
import filecmp
import hashlib
import json
import sys
from pathlib import Path

NAME = "tes3xbuild.json"
FORMAT = 1
# The oldest console manager that understands this format.
MANAGER = 1


def _zstd():
    try:
        import zstandard
    except ImportError:
        raise RuntimeError("XBE deltas need the zstandard package: pip install zstandard") \
            from None
    return zstandard


def _window_log(reference, size):
    # The frame refers back across the whole reference, so the window has to span it too.
    return max(10, (len(reference) + size - 1).bit_length())


def make_delta(reference, output):
    """A zstd frame that rebuilds `output` from `reference`, as `zstd --patch-from` makes."""
    zstd = _zstd()
    prefix = zstd.ZstdCompressionDict(reference, dict_type=zstd.DICT_TYPE_RAWCONTENT)
    params = zstd.ZstdCompressionParameters.from_level(
        19, window_log=_window_log(reference, len(output)), source_size=len(output),
        enable_ldm=True)
    return zstd.ZstdCompressor(compression_params=params, dict_data=prefix).compress(output)


def apply_delta(reference, delta):
    zstd = _zstd()
    prefix = zstd.ZstdCompressionDict(reference, dict_type=zstd.DICT_TYPE_RAWCONTENT)
    size = zstd.frame_content_size(delta)
    return zstd.ZstdDecompressor(dict_data=prefix,
                                 max_window_size=1 << _window_log(reference, size)
                                 ).decompress(delta)


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def tree_files(root):
    """{relative path: absolute path} for every file but the manifest itself."""
    root = Path(root)
    return {path.relative_to(root).as_posix(): path
            for path in sorted(root.rglob("*")) if path.is_file()
            and path.relative_to(root).as_posix().lower() != NAME}


def origin(relative, path, retail=None):
    """Where a file comes from, for a server's policy on what it sends: "xbe" (rebuilt from the
    player's own retail copy by its delta), "retail" (a byte copy of a retail file) or "build"."""
    if relative.lower().endswith(".xbe"):
        return "xbe"
    # The INI is the build's configuration, and a staged base (xemu's) leaves it out.
    if retail is not None and relative.lower() != "morrowind.ini":
        original = Path(retail) / relative
        if (original.is_file() and original.stat().st_size == path.stat().st_size
                and filecmp.cmp(path, original, shallow=False)):
            return "retail"
    return "build"


def file_entries(root, retail=None):
    """{path: {size, sha256, origin}} for a staged tree."""
    return {relative: {"size": path.stat().st_size,
                       "sha256": sha256_file(path),
                       "origin": origin(relative, path, retail)}
            for relative, path in tree_files(root).items()}


def create(root, *, profile, source, plugins=(), ini=(), xbe=(), save_pool=None,
           install_layout="full", retail=None, files=None, folder=None):
    """The manifest for a staged tree, or with `files` given, for those entries. `folder` is the
    install folder's name, where the console manager puts the build."""
    manifest = {
        "format": FORMAT,
        "manager": MANAGER,
        "profile": profile,
        "source": source,
        "install_layout": install_layout,
        "save_pool": save_pool,
        "plugins": list(plugins),
        "ini": list(ini),
        "xbe": list(xbe),
        "files": file_entries(root, retail) if files is None else files,
    }
    if folder:
        manifest["folder"] = folder
    # first, so the game finds it in the file's head
    return {"format": FORMAT, "build": build_id(manifest), **manifest}


def build_id(manifest):
    """What a server compares to tell a stale console: the files and the load order, not what a
    deploy or an install adds."""
    content = {"files": {path.lower(): entry.get("sha256") or entry.get("sha1")
                         for path, entry in manifest["files"].items()},
               "plugins": manifest.get("plugins", [])}
    return hashlib.sha256(json.dumps(content, sort_keys=True, separators=(",", ":"))
                          .encode("utf-8")).hexdigest()


def write(root, manifest):
    path = Path(root) / NAME
    path.write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    return path


def load(root):
    """The manifest in a local tree, or None."""
    try:
        return parse(Path(root, NAME).read_bytes())
    except OSError:
        return None


def parse(data):
    manifest = json.loads(data)
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), dict):
        raise ValueError("not a TES3X build manifest")
    if manifest.get("format", 0) > FORMAT:
        raise ValueError(f"manifest format {manifest['format']} is newer than this tool's "
                         f"{FORMAT}")
    return manifest


def diff(manifest, root):
    """(missing, changed, extra) paths of a tree against its manifest."""
    present = tree_files(root)
    present_ci = {path.lower(): path for path in present}
    missing, changed = [], []
    for relative, entry in manifest["files"].items():
        local = present_ci.get(relative.lower())
        if local is None:
            missing.append(relative)
        elif (present[local].stat().st_size != entry["size"]
              or sha256_file(present[local]) != entry["sha256"]):
            changed.append(relative)
    listed = {path.lower() for path in manifest["files"]}
    extra = [path for path in present if path.lower() not in listed]
    return missing, changed, extra


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("tree", help="a build folder holding " + NAME)
    args = ap.parse_args()
    manifest = load(args.tree)
    if manifest is None:
        sys.exit(f"no {NAME} in {args.tree}")
    missing, changed, extra = diff(manifest, args.tree)
    size = sum(entry["size"] for entry in manifest["files"].values())
    print(f"{manifest.get('profile') or 'unnamed'}: {len(manifest['files'])} files, "
          f"{size / 1048576:.1f} MB, {len(manifest.get('plugins', []))} plugins")
    for label, paths in (("missing", missing), ("changed", changed), ("not listed", extra)):
        for path in paths:
            print(f"  {label}: {path}")
    if missing or changed or extra:
        sys.exit(1)
    print("  matches")


if __name__ == "__main__":
    main()
