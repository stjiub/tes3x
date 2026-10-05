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


def servable(relative, path, retail=None):
    """A server may hand this file out: not an XBE, which is rebuilt from the player's own
    retail copy, and not a byte copy of a retail file."""
    if relative.lower().endswith(".xbe"):
        return False
    if retail is None:
        return True
    original = Path(retail) / relative
    return not (original.is_file() and original.stat().st_size == path.stat().st_size
                and filecmp.cmp(path, original, shallow=False))


def file_entries(root, retail=None):
    """{path: {size, sha256, serve}} for a staged tree."""
    return {relative: {"size": path.stat().st_size,
                       "sha256": sha256_file(path),
                       "serve": servable(relative, path, retail)}
            for relative, path in tree_files(root).items()}


def create(root, *, profile, source, plugins=(), ini=(), xbe=(), save_pool=None,
           install_layout="full", retail=None, files=None):
    """The manifest for a staged tree, or with `files` given, for those entries."""
    return {
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
