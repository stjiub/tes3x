"""Compile the bundled payload objects and their index from one supplied retail engine.

The engine only sets the element counts of array values in the generated header; the objects
themselves hold no engine values and serve every supported XBE.
"""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

from tes3x.payload import (HEADER_FORMAT, OBJECTS, OBJECTS_SCHEMA, compile_objects,
                           engine_bindings, production_sources, source_digest)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("xbe", type=Path,
                        help="the NTSC retail morrowind.xbe, where every patch binds")
    parser.add_argument("--out", type=Path, default=OBJECTS, help="object folder")
    parser.add_argument("--llvm", help="folder holding clang")
    args = parser.parse_args()
    sources = production_sources()
    layout = engine_bindings(args.xbe, sources).layout()
    args.out.mkdir(parents=True, exist_ok=True)
    for stale in args.out.glob("*.obj"):
        stale.unlink()
    with tempfile.TemporaryDirectory() as work:
        built = compile_objects(sources, Path(work), layout, llvm_dir=args.llvm)
        entries = {}
        for source, obj in built.items():
            target = args.out / obj.name
            shutil.copyfile(obj, target)
            entries[source] = {"digest": source_digest(source),
                               "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}
    index = {"schema": OBJECTS_SCHEMA, "header_format": HEADER_FORMAT, "layout": layout,
             "objects": entries}
    with (args.out / "index.json").open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(index, indent=2, sort_keys=True) + "\n")
    print(f"{len(entries)} objects, {sum(p.stat().st_size for p in args.out.glob('*.obj')):,} "
          f"bytes in {args.out}")


if __name__ == "__main__":
    main()
