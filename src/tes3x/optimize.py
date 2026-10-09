"""Write equivalence-checked derived copies of a TES3 plugin load order.

The first pass removes superseded script definitions. Both the Xbox loader
(0x00110D8B-0x00110E17) and PC loader (0x004C0823-0x004C092B) remove and
destroy an earlier SCPT object before appending the later definition.
"""

import argparse
import hashlib
import json
import os
import shutil
import struct
import tempfile
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from tes3x.records import RECORD_HEADER, raw_records, subrecords


HEDR_COUNT = 296
DELETED = 0x20
MODEL = "xbox-goty-script-replacement-v1"


class OptimizeError(Exception):
    pass


@dataclass(frozen=True)
class Plugin:
    path: Path
    name: str
    size: int
    records: int


@dataclass(frozen=True)
class Script:
    key: str
    name: str
    owner: str
    unknown: int
    flags: int
    deleted: bool
    body: bytes


def hash_file(path):
    with open(path, "rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def script_name(data):
    for tag, value in subrecords(data):
        if tag == b"SCHD":
            raw = value[:32].split(b"\0", 1)[0]
            if not raw:
                break
            return raw.decode("cp1252"), any(t == b"DELE" for t, _v in subrecords(data))
    raise OptimizeError("SCPT record has no non-empty SCHD name")


def inspect_plugins(paths):
    plugins = []
    names = set()
    for value in paths:
        path = Path(value).resolve()
        if not path.is_file():
            raise OptimizeError(f"plugin does not exist: {path}")
        if path.suffix.casefold() not in {".esm", ".esp"}:
            raise OptimizeError(f"plugin must end in .esm or .esp: {path}")
        name = path.name
        folded = name.casefold()
        if folded in names:
            raise OptimizeError(f"duplicate plugin name: {name}")
        names.add(folded)
        if path.stat().st_size == 4 and path.read_bytes() == b"TES3":
            plugins.append(Plugin(path, name, 4, 0))
            continue
        count = 0
        for index, (_offset, tag, _unknown, _flags, _data) in enumerate(raw_records(path)):
            if index == 0 and tag != b"TES3":
                raise OptimizeError(f"{path}: first record is not TES3")
            if index and tag == b"TES3":
                raise OptimizeError(f"{path}: TES3 header is not unique")
            count += tag != b"TES3"
        plugins.append(Plugin(path, name, path.stat().st_size, count))
    return plugins


def plan_scripts(plugins):
    """Return removable record locations and a report.

    The loader propagates the deleted bit from an earlier script into its replacement. A record
    with any flags or a DELE subrecord is therefore retained until those fields are modelled fully.
    """
    previous = {}
    drops = set()
    found = duplicates = guarded = 0
    by_file = {plugin.name: {"records": 0, "bytes": 0} for plugin in plugins}
    for file_index, plugin in enumerate(plugins):
        for record_index, (_offset, tag, _unknown, flags, data) in enumerate(
                raw_records(plugin.path)):
            if tag != b"SCPT":
                continue
            found += 1
            name, has_dele = script_name(data)
            key = name.casefold()
            prior = previous.get(key)
            if prior is not None:
                duplicates += 1
                prior_file, prior_record, prior_flags, prior_dele, prior_size = prior
                if prior_flags == 0 and not prior_dele:
                    drops.add((prior_file, prior_record))
                    item = by_file[plugins[prior_file].name]
                    item["records"] += 1
                    item["bytes"] += prior_size
                else:
                    guarded += 1
            previous[key] = (file_index, record_index, flags, has_dele,
                             RECORD_HEADER.size + len(data))
    return drops, {
        "pass": "superseded-scripts",
        "scripts": found,
        "duplicate_definitions": duplicates,
        "removed_records": len(drops),
        "removed_bytes": sum(item["bytes"] for item in by_file.values()),
        "guarded_records": guarded,
        "files": {name: item for name, item in by_file.items() if item["records"]},
    }


def patch_header(data, record_count, output_sizes):
    data = bytearray(data)
    offset = 0
    pending_master = None
    found_hedr = False
    while offset < len(data):
        if len(data) - offset < 8:
            raise OptimizeError("truncated TES3 subrecord header")
        tag, size = struct.unpack_from("<4sI", data, offset)
        body = offset + 8
        if size > len(data) - body:
            raise OptimizeError(f"TES3 {tag!r} subrecord exceeds its record")
        if tag == b"HEDR":
            if size < 300:
                raise OptimizeError("TES3 HEDR is shorter than 300 bytes")
            struct.pack_into("<I", data, body + HEDR_COUNT, record_count)
            found_hedr = True
        elif tag == b"MAST":
            pending_master = bytes(data[body:body + size]).split(b"\0", 1)[0] \
                .decode("cp1252").casefold()
        elif tag == b"DATA" and pending_master is not None:
            if size != 8:
                raise OptimizeError("TES3 master DATA is not eight bytes")
            if pending_master in output_sizes:
                struct.pack_into("<Q", data, body, output_sizes[pending_master])
            pending_master = None
        offset = body + size
    if not found_hedr:
        raise OptimizeError("TES3 header has no HEDR subrecord")
    return bytes(data)


def write_plugins(plugins, output, drops):
    removed = {i: 0 for i in range(len(plugins))}
    for file_index, _record_index in drops:
        removed[file_index] += 1
    output_sizes = {
        plugin.name.casefold(): plugin.size - sum(
            RECORD_HEADER.size + len(data)
            for record_index, (_offset, _tag, _unknown, _flags, data)
            in enumerate(raw_records(plugin.path))
            if (file_index, record_index) in drops)
        for file_index, plugin in enumerate(plugins)
    }

    output = Path(output).resolve()
    sources = {plugin.path for plugin in plugins}
    if output in sources:
        raise OptimizeError("output must be a directory, not a source plugin")
    if output.exists():
        raise OptimizeError(f"output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=output.parent))
    try:
        for file_index, plugin in enumerate(plugins):
            target = stage / plugin.name
            if plugin.size == 4:
                target.write_bytes(b"TES3")
                continue
            with open(target, "wb") as stream:
                for record_index, (_offset, tag, unknown, flags, data) in enumerate(
                        raw_records(plugin.path)):
                    if (file_index, record_index) in drops:
                        continue
                    if tag == b"TES3":
                        data = patch_header(data, plugin.records - removed[file_index],
                                            output_sizes)
                    stream.write(RECORD_HEADER.pack(tag, len(data), unknown, flags))
                    stream.write(data)
            expected = output_sizes[plugin.name.casefold()]
            if target.stat().st_size != expected:
                raise OptimizeError(
                    f"{plugin.name}: wrote {target.stat().st_size} bytes, expected {expected}")
        os.replace(stage, output)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return [output / plugin.name for plugin in plugins]


def normalize_header(data):
    data = bytearray(data)
    offset = 0
    pending_master = False
    while offset < len(data):
        if len(data) - offset < 8:
            raise OptimizeError("truncated TES3 subrecord header")
        tag, size = struct.unpack_from("<4sI", data, offset)
        body = offset + 8
        if size > len(data) - body:
            raise OptimizeError(f"TES3 {tag!r} subrecord exceeds its record")
        if tag == b"HEDR":
            if size < 300:
                raise OptimizeError("TES3 HEDR is shorter than 300 bytes")
            data[body + HEDR_COUNT:body + HEDR_COUNT + 4] = bytes(4)
        elif tag == b"MAST":
            pending_master = True
        elif tag == b"DATA" and pending_master:
            data[body:body + size] = bytes(size)
            pending_master = False
        offset = body + size
    return bytes(data)


def state_signature(paths):
    """Canonical state for the implemented model plus exact untouched record streams."""
    records_hash = hashlib.sha256()
    scripts = OrderedDict()
    for path in paths:
        path = Path(path)
        owner = path.name
        records_hash.update(struct.pack("<I", len(owner.encode("utf-8"))))
        records_hash.update(owner.encode("utf-8"))
        if path.stat().st_size == 4 and path.read_bytes() == b"TES3":
            records_hash.update(b"TES3-STUB")
            continue
        for _offset, tag, unknown, flags, data in raw_records(path):
            if tag == b"SCPT":
                name, has_dele = script_name(data)
                key = name.casefold()
                prior = scripts.pop(key, None)
                scripts[key] = Script(key, name, owner, unknown, flags,
                                      bool(flags & DELETED) or has_dele
                                      or bool(prior and prior.deleted), data)
                continue
            body = normalize_header(data) if tag == b"TES3" else data
            records_hash.update(RECORD_HEADER.pack(tag, len(body), unknown, flags))
            records_hash.update(body)
    script_rows = []
    for item in scripts.values():
        script_rows.append({
            "key": item.key,
            "name": item.name,
            "owner": item.owner,
            "unknown": item.unknown,
            "flags": item.flags,
            "deleted": item.deleted,
            "body": hashlib.sha256(item.body).hexdigest(),
        })
    value = {"model": MODEL, "records": records_hash.hexdigest(), "scripts": script_rows}
    canonical = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    value["digest"] = hashlib.sha256(canonical).hexdigest()
    return value


def verify(original, derived):
    before = state_signature(original)
    after = state_signature(derived)
    if before["records"] != after["records"]:
        raise OptimizeError("derived load order changes an untouched record or plugin header")
    if before["scripts"] != after["scripts"]:
        raise OptimizeError("derived load order changes final script state")
    if before["digest"] != after["digest"]:
        raise OptimizeError("derived load order has a different state digest")
    return before["digest"]


def manifest(plugins, derived, report, digest):
    return {
        "schema": 1,
        "model": MODEL,
        "state_digest": digest,
        "passes": [report],
        "plugins": [
            {
                "name": plugin.name,
                "source": str(plugin.path),
                "source_size": plugin.size,
                "source_sha256": hash_file(plugin.path),
                "output_size": output.stat().st_size,
                "output_sha256": hash_file(output),
            }
            for plugin, output in zip(plugins, derived)
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plugin", nargs="+", help="plugins in engine load order")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--output", metavar="DIR",
                       help="write verified derived copies to this new directory")
    group.add_argument("--check", metavar="DIR",
                       help="check copies in DIR against the supplied load order")
    args = parser.parse_args()
    try:
        plugins = inspect_plugins(args.plugin)
        sources = [plugin.path for plugin in plugins]
        if args.check:
            derived = [Path(args.check) / plugin.name for plugin in plugins]
            digest = verify(sources, derived)
            print(f"equivalent: {digest}")
            return
        drops, report = plan_scripts(plugins)
        print(f"superseded-scripts: remove {report['removed_records']:,} of "
              f"{report['scripts']:,} SCPT records ({report['removed_bytes']:,} bytes); "
              f"guarded {report['guarded_records']:,}")
        for name, item in report["files"].items():
            print(f"  {name}: {item['records']:,} records, {item['bytes']:,} bytes")
        if not args.output:
            return
        derived = write_plugins(plugins, args.output, drops)
        digest = verify(sources, derived)
        result = manifest(plugins, derived, report, digest)
        manifest_path = Path(args.output) / "tes3x-optimize.json"
        manifest_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(f"equivalent: {digest}")
        print(f"wrote {len(derived)} derived plugins and {manifest_path}")
    except (OSError, ValueError, OptimizeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
