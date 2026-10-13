"""Build the supported distributable payload set from one supplied NTSC engine."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil

from tes3x.payload import build_payload
from tes3x.pipeline import resolve_patch_plan


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("xbe", type=Path)
    parser.add_argument("assets", type=Path)
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    variants = []
    for name, patches, archives in (
        ("diagnostics", ["diagnostics"], False),
        ("console", ["diagnostics", "console"], False),
        ("multiplayer", ["diagnostics", "console", "net", "multiplayer"], False),
        ("archives", [], True),
        ("archives-diagnostics", ["diagnostics"], True),
        ("archives-console", ["diagnostics", "console"], True),
        ("archives-multiplayer", ["diagnostics", "console", "net", "multiplayer"], True),
    ):
        profile = {"patches": {"preset": "minimal", "enable": patches}}
        if archives:
            profile["mods"] = [{"name": "example"}]
        sources = resolve_patch_plan(profile)["sources"]
        built = args.work / name
        pe, manifest = build_payload(args.xbe, sources, out=built, portable_bindings=True)
        target_pe, target_manifest = (args.assets / f"{name}.pe",
                                      args.assets / f"{name}.json")
        shutil.copy2(pe, target_pe)
        shutil.copy2(manifest, target_manifest)
        variants.append({"name": name, "sources": sources,
                         "pe_sha256": hashlib.sha256(target_pe.read_bytes()).hexdigest(),
                         "manifest_sha256": hashlib.sha256(
                             target_manifest.read_bytes()).hexdigest()})
    with (args.assets / "index.json").open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps({"schema": 1, "variants": variants}, indent=2) + "\n")


if __name__ == "__main__":
    main()
