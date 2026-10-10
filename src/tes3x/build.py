#!/usr/bin/env python3
"""Compile a Morrowind Xbox Data Files tree from a mod library and profile."""

import argparse
import os
import shutil
import fnmatch
import hashlib
import struct
import sys
import tomllib
from collections import defaultdict
from tes3x.library import LibraryError, load_library, resolve_selection
from tes3x.paths import CONFIG_HELP, DEFAULT_REMOTE_ROOT, data_dir, mod_library, require_paths

FATX_NAME_MAX = 42
# Retail includes a 1024x512 texture; on 64 MB hardware, total texture size is the useful limit.
RETAIL_TEXTURE_MB = 40.1
TEXTURE_SUSPECT = 1024
PLUGIN_EXT = (".esm", ".esp")
MTIME_STEP = 4
EXPANSION_STUBS = ("Tribunal.esm", "Bloodmoon.esm")
VANILLA_MASTERS = ("Morrowind.esm",)
DEFAULT_EXCLUDE = ["docs/*", "*.txt", "*.md", "*.rtf", "*.doc", "*.bak", "*.psd",
                   "readme*", "*thumbs.db", "*.lnk", "*.url", "*.ini"]
ASSET_DIRS = ("meshes", "textures", "icons", "sound", "bookart", "splash", "fonts", "video", "music")


def find_data_root(path):
    """Descend until a directory looks like a Data Files tree."""
    for _ in range(4):
        entries = os.listdir(path)
        lower = {e.lower() for e in entries}
        if lower & set(ASSET_DIRS) or any(e.lower().endswith(PLUGIN_EXT) for e in entries):
            return path
        subdirs = [e for e in entries if os.path.isdir(os.path.join(path, e))]
        if len(subdirs) != 1:
            return path
        path = os.path.join(path, subdirs[0])
    return path


def plugin_masters(path):
    """Master files a plugin depends on, from its TES3 header."""
    from tes3x.records import records, subrecords
    header = next(records(path), None)
    if header is None:  # Four-byte retail expansion stub.
        return []
    if header[0] != b'TES3':
        raise ValueError(f'{path}: missing TES3 header')
    return [value.rstrip(b'\0').decode('cp1252') for tag, value in subrecords(header[2]) if tag == b'MAST']


def texture_dims(path):
    try:
        with open(path, "rb") as f:
            head = f.read(32)
    except OSError:
        return None
    if head[:4] == b"DDS " and len(head) >= 20:
        h, w = struct.unpack_from("<II", head, 12)
        return w, h
    if path.lower().endswith(".tga") and len(head) >= 16:
        w, h = struct.unpack_from("<HH", head, 12)
        return w, h
    if head[:2] == b"BM" and len(head) >= 26:
        with open(path, "rb") as f:
            f.seek(18)
            w, h = struct.unpack("<ii", f.read(8))
        return abs(w), abs(h)
    return None


class Mod:
    def __init__(self, name, path, order, plugins=None, exclude=(), unpack=None):
        self.name = name
        paths = list(path) if isinstance(path, (list, tuple)) else [path]
        layers = [(str(item["path"]), [str(value) for value in item.get("exclude", [])])
                  if isinstance(item, dict) else (str(item), []) for item in paths]
        self.single = len(layers) == 1 and os.path.isfile(layers[0][0])
        self.roots = [os.path.dirname(source) if os.path.isfile(source)
                      else find_data_root(source) for source, _blocked in layers]
        self.root = self.roots[0]
        self.order = order
        self.want = {p.lower() for p in plugins} if plugins is not None else None
        self.skipped_plugins = []
        self.excluded = 0
        self.files = {}
        self.relative = {}
        for (source, blocked), root in zip(layers, self.roots):
            if os.path.isfile(source):
                name = os.path.basename(source)
                self.files[name.lower()] = source
                self.relative[name.lower()] = name
                continue
            blocked = [os.path.normcase(os.path.abspath(path)) for path in blocked]

            def is_blocked(path):
                path = os.path.normcase(os.path.abspath(path))
                return any(path == value or path.startswith(value + os.sep) for value in blocked)

            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [name for name in dirnames
                               if not is_blocked(os.path.join(dirpath, name))]
                rel_dir = os.path.relpath(dirpath, root)
                for fn in filenames:
                    if is_blocked(os.path.join(dirpath, fn)):
                        continue
                    rel = os.path.normpath(os.path.join(rel_dir, fn)).replace("\\", "/")
                    rel = rel[2:] if rel.startswith("./") else rel
                    key = rel.lower()
                    if any(fnmatch.fnmatch(key, pat) or fnmatch.fnmatch(os.path.basename(key), pat)
                           for pat in exclude):
                        self.excluded += 1
                        continue
                    if key.endswith(PLUGIN_EXT) and self.want is not None:
                        if os.path.basename(key) not in self.want:
                            self.skipped_plugins.append(os.path.basename(key))
                            continue
                    self.files[key] = os.path.join(dirpath, fn)
                    self.relative[key] = rel
        self.unpacked = []
        if unpack is not None:
            self.unpack_archives(unpack, exclude)

    def unpack_archives(self, cache, exclude):
        """Replace the mod's archives with their files; its loose files still win."""
        from tes3x.bsa import extract_bsa
        archives = sorted(key for key in self.files if key.endswith(".bsa"))
        for key in reversed(archives):
            source = self.files.pop(key)
            self.relative.pop(key)
            stat = os.stat(source)
            stamp = f"{os.path.abspath(source)}|{stat.st_size}|{stat.st_mtime_ns}"
            target = os.path.join(cache, hashlib.sha1(stamp.encode()).hexdigest()[:16])
            if not os.path.isdir(target):
                partial = target + ".part"
                shutil.rmtree(partial, ignore_errors=True)
                try:
                    extract_bsa(source, partial)
                except ValueError as exc:
                    raise ValueError(f"{self.name}: {exc}; set archives = \"load\" for this "
                                     "mod to have the multi-bsa patch open it instead") from exc
                os.replace(partial, target)
            for dirpath, _dirnames, filenames in os.walk(target):
                for fn in filenames:
                    rel = os.path.relpath(os.path.join(dirpath, fn), target).replace("\\", "/")
                    lower = rel.lower()
                    if lower in self.files or any(
                            fnmatch.fnmatch(lower, pat) or fnmatch.fnmatch(os.path.basename(lower), pat)
                            for pat in exclude):
                        continue
                    self.files[lower] = os.path.join(dirpath, fn)
                    self.relative[lower] = rel
            self.unpacked.append(os.path.basename(source))


def load_profile(path):
    with open(path, "rb") as f:
        return tomllib.load(f)


def resolve(mods):
    """Later order wins. Returns (filemap, conflicts)."""
    owners = defaultdict(list)
    for mod in sorted(mods, key=lambda m: m.order):
        for key, src in mod.files.items():
            owners[key].append((mod, src))
    filemap = {k: v[-1] for k, v in owners.items()}
    conflicts = {k: v for k, v in owners.items() if len(v) > 1}
    return filemap, conflicts


def check_masters(filemap, extra_present=()):
    """Every master a plugin names must exist, or the engine stalls hunting for it."""
    present = {os.path.basename(k).lower() for k in filemap if k.endswith(PLUGIN_EXT)}
    present |= {m.lower() for m in extra_present}
    missing = defaultdict(list)
    for key, (mod, src) in filemap.items():
        if not key.endswith(PLUGIN_EXT):
            continue
        for m in plugin_masters(src):
            if m.lower() not in present:
                missing[m].append(os.path.basename(key))
    return missing


def check(filemap):
    problems = defaultdict(list)
    for key, (mod, src) in filemap.items():
        name = os.path.basename(key)
        if len(name) > FATX_NAME_MAX:
            problems["filename over 42 chars"].append((key, mod.name, len(name)))
        if key.startswith("textures/") or key.endswith((".dds", ".tga", ".bmp")):
            dims = texture_dims(src)
            if dims and max(dims) > TEXTURE_SUSPECT:
                problems["texture larger than any retail ships"].append(
                    (key, mod.name, f"{dims[0]}x{dims[1]}"))
    return problems


def texture_budget(filemap):
    """Total texture bytes and a histogram by longest side."""
    total = 0
    hist = defaultdict(int)
    for key, (_mod, src) in filemap.items():
        if not (key.startswith("textures/") or key.endswith((".dds", ".tga", ".bmp"))):
            continue
        dims = texture_dims(src)
        if not dims:
            continue
        try:
            total += os.path.getsize(src)
        except OSError:
            continue
        hist[max(dims)] += 1
    return total, hist


def report(mods, filemap, conflicts, problems):
    plugins = sorted((k for k in filemap if k.endswith(PLUGIN_EXT)))
    total = sum(os.path.getsize(s) for _, s in filemap.values() if os.path.exists(s))

    print(f"\n{len(mods)} mods -> {len(filemap)} files, {total/1048576:.1f} MB")
    print(f"{len(plugins)} plugins, {len(conflicts)} conflicting paths\n")

    print("== LOAD ORDER (mtime ascending; later wins) ==")
    for mod in sorted(mods, key=lambda m: m.order):
        n_plug = sum(1 for k in mod.files if k.endswith(PLUGIN_EXT))
        print(f"  {mod.order:>4}  {mod.name:<34} {len(mod.files):>6} files  {n_plug} plugin(s)")

    tex_bytes, hist = texture_budget(filemap)
    if tex_bytes:
        mb = tex_bytes / 1048576
        print("\n== TEXTURE BUDGET (source bytes, before conversion) ==")
        print(f"  {sum(hist.values())} textures, {mb:.1f} MB "
              f"({mb / RETAIL_TEXTURE_MB:.1f}x retail's {RETAIL_TEXTURE_MB} MB) on a 64 MB console")
        print("  conversion shrinks this; the packed archive is the figure that counts")
        print("  by longest side: "
              + "  ".join(f"{k}:{hist[k]}" for k in sorted(hist)))

    if conflicts:
        print(f"\n== CONFLICTS ({len(conflicts)}) ==")
        by_pair = defaultdict(int)
        for key, chain in conflicts.items():
            losers = ", ".join(m.name for m, _ in chain[:-1])
            by_pair[(losers, chain[-1][0].name)] += 1
        for (losers, winner), n in sorted(by_pair.items(), key=lambda e: -e[1]):
            print(f"  {n:>5} files  {winner} overrides {losers}")

    if problems:
        print()
        for kind, items in problems.items():
            print(f"== {kind.upper()} ({len(items)}) ==")
            for key, mod, detail in items[:15]:
                print(f"  {detail:>12}  {key}  [{mod}]")
            if len(items) > 15:
                print(f"  ... {len(items)-15} more")
            print()

    missing = check_masters(filemap, EXPANSION_STUBS + VANILLA_MASTERS)
    if missing:
        print()
        print(f"== MISSING MASTERS ({len(missing)}) - plugins will stall the loader ==")
        for m, users in sorted(missing.items(), key=lambda e: -len(e[1])):
            print(f"  {m}  required by {len(users)}: {', '.join(sorted(users)[:4])}"
                  + (" ..." if len(users) > 4 else ""))

    fanout = defaultdict(int)
    for key in filemap:
        fanout[os.path.dirname(key) or "."] += 1
    hot = sorted(fanout.items(), key=lambda e: -e[1])[:8]
    unselected = [(m.name, m.skipped_plugins) for m in mods if m.skipped_plugins]
    if unselected:
        print()
        print(f"== PLUGINS NOT SELECTED ({sum(len(p) for _, p in unselected)}) ==")
        for name, skipped in unselected:
            print(f"  {name}: {', '.join(sorted(skipped))}")

    dropped = sum(m.excluded for m in mods)
    if dropped:
        print()
        print(f"{dropped} files excluded by pattern (docs, readmes, editor junk)")

    print()
    print("== OUTPUT FANOUT (FATX lookup cost) ==")
    for d, n in hot:
        print(f"  {n:>6}  {d}")
    print()


def materialize(filemap, mods, out, rules, cache=None, load_order=None, sox=None, sound_rate=None):
    """Write the resolved tree, converting textures and stamping load order."""
    from tes3x.convert import convert_cached
    cache = cache if cache is not None else data_dir() / "cache" / "tex"

    # This is a budget target; retail proves larger textures are valid.
    cap = rules.get("max_texture_size", 512)
    name_max = rules.get("max_filename", FATX_NAME_MAX)
    if not 4 <= cap <= TEXTURE_SUSPECT or not 1 <= name_max <= FATX_NAME_MAX:
        raise ValueError('profile constraints must fit Xbox texture and filename limits')
    convert_all = rules.get("convert_all_textures", False)
    order_of = {m.name: m.order for m in mods}

    stats = defaultdict(int)
    renames = {}
    plugins = []

    for key, (mod, src) in sorted(filemap.items()):
        rel = getattr(mod, "relative", {}).get(
            key, os.path.relpath(src, mod.root).replace("\\", "/"))
        if any(len(part) > name_max for part in rel.split('/')):
            raise ValueError(f'FATX name too long: {rel}; renaming requires rewriting asset references')

        dst = os.path.join(out, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)

        if sound_rate and key.startswith('sound/') and key.endswith('.wav'):
            from tes3x.assets import convert_wav
            if convert_wav(src, dst, sox, sound_rate):
                stats['sound_converted'] += 1
                stats['sound_bytes_saved'] += os.path.getsize(src) - os.path.getsize(dst)
                continue

        is_tex = key.endswith((".dds", ".tga", ".bmp"))
        dims = texture_dims(src) if is_tex else None
        if is_tex and dims and (convert_all or max(dims) > cap):
            try:
                data, _ = convert_cached(src, cache, cap)
                with open(dst, "wb") as f:
                    f.write(data)
                stats["converted"] += 1
                stats["bytes_saved"] += os.path.getsize(src) - len(data)
                continue
            except Exception as exc:
                raise ValueError(f'texture conversion failed for {src}; refusing unsafe output') from exc

        shutil.copyfile(src, dst)
        stats["copied"] += 1
        if key.endswith(PLUGIN_EXT):
            plugins.append((order_of[mod.name], dst))

    # Each plugin needs its own FATX timestamp, even when several belong to
    # the same mod. A fixed epoch also makes repeated builds reproducible.
    base = 978307200
    ordered = sorted(plugins, key=lambda item: (not item[1].lower().endswith('.esm'),
                                               item[0], os.path.basename(item[1]).lower()))
    rank = {name.lower(): i for i, name in enumerate(load_order or [])}
    for index, (_, dst) in enumerate(ordered):
        stamp = base + rank.get(os.path.basename(dst).lower(), index + 3) * MTIME_STEP
        os.utime(dst, (stamp, stamp))

    return stats, renames


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("profile")
    ap.add_argument("--config", help=CONFIG_HELP)
    ap.add_argument("--library", help="override profile.library (normally from local config)")
    ap.add_argument("--json", help="write manifest to this path")
    ap.add_argument("--out", help="materialize the resolved tree into this directory")
    ap.add_argument("--prune", action="store_true", help="prune unreachable mod assets (requires --vanilla)")
    ap.add_argument("--vanilla", help="clean Xbox Data Files, for reachability roots and archive replacements")
    ap.add_argument("--reachability-json", help="write pruning decisions and missing references")
    ap.add_argument("--load-order", help="validated tes3x_plugins order JSON; requires --vanilla")
    ap.add_argument("--archive-list", help="write the mod archives shipped, in load order, as JSON")
    ap.add_argument("--max-texture-size", type=int, metavar="N",
                    help="downscale target for mod textures (profile: max_texture_size)")
    ap.add_argument("--convert-all-textures", dest="convert_all", action="store_true",
                    default=None,
                    help="convert every mod texture, not only oversized ones; vanilla is "
                         "untouched either way, it is merged later by tes3x_pack")
    ap.add_argument("--no-convert-all-textures", dest="convert_all", action="store_false",
                    help="only convert mod textures above the size cap")
    ap.add_argument("--sox", help="SoX executable for opt-in sound conversion")
    ap.add_argument("--sound-rate", type=int, help="cap mod WAV sample rate; requires --sox")
    ap.add_argument("--remote-root", help=f"Xbox game folder (default: {DEFAULT_REMOTE_ROOT})")
    args = ap.parse_args()
    if args.sound_rate and not args.sox:
        ap.error("--sound-rate requires --sox")

    prof = load_profile(args.profile)
    try:
        library = mod_library(prof, args.library, args.config)
    except (ValueError, OSError) as exc:
        sys.exit(str(exc))

    rules = dict(prof.get("rules", {}))
    if args.max_texture_size is not None:
        rules["max_texture_size"] = args.max_texture_size
    if args.convert_all is not None:
        rules["convert_all_textures"] = args.convert_all
    exclude = rules.get("exclude", DEFAULT_EXCLUDE)

    mods, missing = [], []
    managed = any("id" in entry for entry in prof.get("mods", []))
    try:
        catalog = load_library(library) if managed else None
    except LibraryError as exc:
        sys.exit(str(exc))
    selected_ids = {entry.get("id") for entry in prof.get("mods", [])
                    if entry.get("enabled", True) and entry.get("id")}
    for entry in prof.get("mods", []):
        if not entry.get("enabled", True):
            continue
        try:
            selection = resolve_selection(entry, library, catalog)
        except (LibraryError, KeyError) as exc:
            if entry.get("optional", False):
                print(f"  optional mod skipped: {exc}", file=sys.stderr)
                continue
            missing.append(str(exc))
            continue
        absent = [str(path) for path in selection["roots"] if not path.exists()]
        if absent:
            if entry.get("optional", False):
                print(f"  optional mod not found, skipped: {selection['name']}", file=sys.stderr)
            else:
                missing.extend(absent)
            continue
        absent_dependencies = set(selection.get("dependencies", [])) - selected_ids
        if absent_dependencies:
            missing.append(f"{selection['id']} needs profile mod ids "
                           + ", ".join(sorted(absent_dependencies)))
            continue
        label = selection["name"] + (f" {selection['version']}" if selection["version"] else "")
        try:
            mods.append(Mod(label, selection.get("layers", selection["roots"]),
                            entry.get("order", 0), entry.get("plugins"), exclude,
                            None if entry.get("archives") == "load"
                            else data_dir() / "cache" / "bsa"))
        except ValueError as exc:
            sys.exit(str(exc))
    if missing:
        # A deploy mirrors the build, so a silently omitted mod would be deleted from the Xbox.
        sys.exit(f"mods not found in {library}: {', '.join(missing)}\n"
                 "fix the name, set enabled = false, or mark the mod optional = true")

    if not mods:
        sys.exit("no enabled mods resolved")

    filemap, conflicts = resolve(mods)
    load_order = None
    if args.load_order:
        if not args.vanilla:
            ap.error("--load-order requires --vanilla")
        import json
        from pathlib import Path
        from tes3x.plugins import validate_order, digest
        payload = json.loads(Path(args.load_order).read_text(encoding="utf-8"))
        inputs = {os.path.basename(k): Path(s) for k, (_, s) in filemap.items() if k.endswith(PLUGIN_EXT)}
        for name in ("Morrowind.esm",) + EXPANSION_STUBS:
            inputs.setdefault(name.lower(), Path(args.vanilla) / name)
        load_order = validate_order(payload['plugins'], inputs)
        if payload.get('input_sha256') != {n: digest(p) for n, p in sorted(inputs.items())}:
            ap.error("plugin contents differ from the mlox inputs; regenerate the order")
    if args.prune:
        if not args.vanilla:
            ap.error("--prune requires --vanilla")
        if args.out and os.path.isdir(args.out) and os.listdir(args.out):
            ap.error("pruned output must be a new or empty directory (prevents stale assets)")
        from tes3x.reach import prune
        filemap, reach = prune(filemap, args.vanilla, rules.get("keep_assets", []))
        print(f"Reachability: removed {reach['removed_files']} files, "
              f"{reach['removed_bytes']/1048576:.1f} MB; "
              f"{len(reach['missing_references'])} unresolved references")
        for warning in reach['warnings']:
            print(f"  WARNING: {warning}")
        if args.reachability_json:
            import json
            with open(args.reachability_json, "w", encoding="utf-8") as stream:
                json.dump(reach, stream, indent=2)
    problems = check(filemap)
    report(mods, filemap, conflicts, problems)
    remote_root = args.remote_root or prof['profile'].get('remote_root', DEFAULT_REMOTE_ROOT)
    require_paths(filemap, remote_root, 'Data Files')

    if args.out:
        stats, renames = materialize(filemap, mods, args.out, rules, load_order=load_order,
                                    sox=args.sox, sound_rate=args.sound_rate)
        print(f"== MATERIALIZED -> {args.out} ==")
        print(f"  {stats['copied']} copied, {stats['converted']} converted, "
              f"{stats['renamed']} renamed, {stats['convert_failed']} failed")
        if stats["bytes_saved"]:
            print(f"  texture conversion saved {stats['bytes_saved']/1048576:.1f} MB")
        if stats['sound_converted']:
            print(f"  SoX: {stats['sound_converted']} WAVs converted, {stats['sound_bytes_saved']} bytes saved")
        print(f"  load order stamped across {len([k for k in filemap if k.endswith(PLUGIN_EXT)])} plugins")
        print()

    if args.archive_list:
        import json
        archives = sorted((mod.order, key) for key, (mod, _src) in filemap.items()
                          if key.endswith(".bsa") and "/" not in key)
        with open(args.archive_list, "w", encoding="utf-8") as stream:
            json.dump([filemap[key][0].relative.get(key, key) for _order, key in archives],
                      stream)

    if args.json:
        import json
        manifest = {k: {"mod": m.name, "src": s} for k, (m, s) in sorted(filemap.items())}
        with open(args.json, "w") as f:
            json.dump(manifest, f, indent=1)
        print(f"manifest -> {args.json}\n")


if __name__ == "__main__":
    main()
