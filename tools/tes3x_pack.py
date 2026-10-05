#!/usr/bin/env python3
"""Pack exact-path assets into BSA and stage globbed files loose for deployment."""

import argparse
import json
import os
import re
import shutil
import sys
import fnmatch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tes3x_bsa import Bsa, write_bsa, tes3_hash
from tes3x_paths import DEFAULT_REMOTE_ROOT, require_paths

PACKABLE = ("meshes", "textures", "icons", "sound", "bookart")
LOOSE_DIRS = ("splash", "video", "music", "fonts")
LOOSE_EXT = (".esm", ".esp", ".bsa", ".map", ".ini", ".txt")


EXPANSION_STUBS = ("Tribunal.esm", "Bloodmoon.esm")
TES3_STUB = b"TES3"


def classify(rel):
    low = rel.lower().replace("\\", "/")
    top = low.split("/")[0]
    if low.endswith(LOOSE_EXT) or top in LOOSE_DIRS:
        return "loose"
    if top in PACKABLE:
        return "pack"
    return "loose"


def set_ini_key(text, section, key, value):
    out, in_section, done = [], False, False
    for line in text.split("\n"):
        s = line.strip()
        if s.startswith("["):
            if in_section and not done:
                out.append(f"{key}={value}")
                done = True
            in_section = s.lower() == f"[{section.lower()}]"
        elif in_section and re.match(rf"\s*{re.escape(key)}\s*=", line, re.I):
            line = f"{key}={value}"
            done = True
        out.append(line)
    if in_section and not done:
        out.append(f"{key}={value}")
    elif not done:
        out.extend([f'[{section}]', f'{key}={value}'])
    return "\n".join(out)


def write_invalidation(path, names):
    """Write validated relative paths with the final newline retail requires."""
    paths = sorted({name.lower().replace('/', '\\') for name in names})
    for name in paths:
        if (not name or name.startswith('\\') or ':' in name or
                '..' in name.split('\\') or any(c in name for c in '\r\n\0')):
            raise ValueError(f'invalid archive-invalidation path: {name!r}')
        if len(name.encode('cp1252')) > 258:
            raise ValueError('archive-invalidation path exceeds fgets line buffer')
    with open(path, 'w', encoding='cp1252', newline='\r\n') as stream:
        stream.write(''.join(name + '\n' for name in paths))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("built", help="materialized mod tree (tes3x_build --out)")
    ap.add_argument("--vanilla", required=True, help="clean Data Files with Morrowind.bsa/.esm/.map")
    ap.add_argument("--out", required=True, help="deploy tree to stage")
    ap.add_argument("--ini", help="Morrowind.ini to adapt (default: alongside vanilla)")
    ap.add_argument("--delta-archive", metavar="NAME", nargs="?", const="tes3xmods.bsa",
                    help="leave vanilla Morrowind.bsa untouched and put mod assets in their own "
                         "archive, listed in tes3xarch.txt (needs the multi-BSA hook)")
    ap.add_argument("--mod-archives", metavar="JSON",
                    help="mod archives in the tree, in load order, for tes3xarch.txt "
                         "(needs the multi-BSA hook)")
    ap.add_argument("--no-archive", action="store_true",
                    help="stage every mod asset loose and leave vanilla Morrowind.bsa untouched")
    ap.add_argument("--stubs", choices=("auto", "require", "force"), default="auto",
                    help="expansion master placeholders: use vanilla's and generate what is "
                         "missing (auto), fail if absent (require), or always generate (force)")
    ap.add_argument("--archive-only", action="store_true",
                    help="set TryArchiveFirst=1 (skips loose lookups entirely)")
    ap.add_argument("--ini-set", action="append", default=[], metavar="SECTION:KEY=VALUE",
                    help="set a key in the staged Morrowind.ini (repeatable). "
                         "tools/tes3x_ini.py lists every key the engine actually reads")
    ap.add_argument("--quickstart", nargs="?", const=True, metavar="CELL",
                    help="[General] QuickStart=1, with Starting Cell=CELL if given - boots "
                         "straight into a game instead of the menu, for unattended test cycles")
    ap.add_argument("--show-fps", action="store_true",
                    help="[General] Show FPS=1 - the engine's own frame counter, no hook needed")
    ap.add_argument("--loose-asset", action="append", default=[], metavar="GLOB",
                    help="stage matching assets loose instead of packing them (repeatable)")
    ap.add_argument("--loose-mod", action="append", default=[], metavar="NAME",
                    help="stage every asset this mod won loose instead of packing it (repeatable); "
                         "needs --manifest")
    ap.add_argument("--manifest", help="tes3x_build --json output, mapping assets to mods")
    ap.add_argument("--load-order", help="tes3x_plugins order/patch JSON; stamp all shipped plugins")
    ap.add_argument("--remote-root", default=DEFAULT_REMOTE_ROOT, help="Xbox game folder for physical path checks")
    args = ap.parse_args()
    if args.archive_only and (args.loose_asset or args.loose_mod or args.no_archive):
        ap.error('loose assets require TryArchiveFirst=0; archive-only skips loose lookups')
    if args.no_archive and args.delta_archive:
        ap.error('--no-archive and --delta-archive are alternatives')
    if args.loose_mod and not args.manifest:
        ap.error('--loose-mod needs --manifest')
    if os.path.isdir(args.out) and os.listdir(args.out):
        ap.error("output must be new or empty; stale files would invalidate the deployment")

    base_bsa = os.path.join(args.vanilla, "Morrowind.bsa")
    base = Bsa(base_bsa)
    out_df = os.path.join(args.out, "Data Files")

    pack, loose = [], []
    for dp, _, fns in os.walk(args.built):
        for fn in fns:
            full = os.path.join(dp, fn)
            rel = os.path.relpath(full, args.built).replace("/", "\\")
            (pack if classify(rel) == "pack" else loose).append((rel, full))

    # Four-byte TES3 stubs satisfy expansion masters; Xbox merges their content into Morrowind.esm.
    for stub in EXPANSION_STUBS:
        if any(r.lower() == stub.lower() for r, _ in loose):
            continue
        src = os.path.join(args.vanilla, stub)
        if args.stubs != "force" and os.path.isfile(src):
            loose.append((stub, src))
        elif args.stubs == "require":
            ap.error(f"required expansion stub missing: {src}")
        else:
            loose.append((stub, None))
            print(f"  generating placeholder {stub}")

    # vanilla loose files the engine globs or opens directly
    for name in os.listdir(args.vanilla):
        full = os.path.join(args.vanilla, name)
        if os.path.isfile(full) and name.lower().endswith((".esm", ".map")):
            if not any(r.lower() == name.lower() for r, _ in loose):
                loose.append((name, full))
    for d in LOOSE_DIRS:
        vsrc = os.path.join(args.vanilla, d)
        if not os.path.isdir(vsrc):
            continue
        for dp, _, fns in os.walk(vsrc):
            for fn in fns:
                full = os.path.join(dp, fn)
                rel = os.path.relpath(full, args.vanilla).replace("/", "\\")
                if not any(r.lower() == rel.lower() for r, _ in loose):
                    loose.append((rel, full))

    # Loose assets leave the archives entirely. A retail entry they replace is dropped from a
    # merged archive, or named in ArchiveInvalidationList.txt when retail ships unchanged: the
    # engine invalidates a listed name only in the first chained archive that holds it.
    owner = {}
    if args.manifest:
        with open(args.manifest, encoding="utf-8") as stream:
            owner = {k.lower(): v["mod"].lower() for k, v in json.load(stream).items()}
    for mod in args.loose_mod:
        if mod.lower() not in owner.values():
            ap.error(f"--loose-mod {mod}: no asset in the manifest belongs to it")
    loose_mods = {m.lower() for m in args.loose_mod}
    globs = [p.lower().replace('\\', '/') for p in args.loose_asset]

    def key(rel):
        return rel.lower().replace('\\', '/')

    for pattern in globs:
        if not any(fnmatch.fnmatchcase(key(rel), pattern) for rel, _ in pack):
            ap.error(f"--loose-asset matched nothing: {pattern}")
    staged_loose = [(rel, src) for rel, src in pack
                    if args.no_archive or owner.get(key(rel)) in loose_mods
                    or any(fnmatch.fnmatchcase(key(rel), p) for p in globs)]
    moved = {rel for rel, _ in staged_loose}
    pack = [(rel, src) for rel, src in pack if rel not in moved]

    # A BSA identifies entries only by hash, so neither member of a collision can remain in the
    # archive. Loose lookup uses the full path and preserves both assets.
    by_hash = {}
    for item in pack:
        by_hash.setdefault(tes3_hash(item[0]), []).append(item)
    collision_groups = [items for items in by_hash.values()
                        if len({key(rel) for rel, _ in items}) > 1]
    collision_paths = {key(rel) for items in collision_groups for rel, _ in items}
    if collision_groups and args.archive_only:
        ap.error('BSA hash collisions require loose assets; remove --archive-only')
    collision_loose = [(rel, src) for rel, src in pack if key(rel) in collision_paths]
    if collision_loose:
        pack = [(rel, src) for rel, src in pack if key(rel) not in collision_paths]
        staged_loose.extend(collision_loose)
        for items in collision_groups:
            print('  BSA hash collision; staging loose:')
            for rel, _ in items:
                print(f'    {rel}')
        print(f'  {len(collision_loose)} assets in {len(collision_groups)} collision group(s) '
              'staged loose')

    loose.extend(staged_loose)
    invalidated = [(rel, src) for rel, src in staged_loose if tes3_hash(rel) in base.by_hash]
    if staged_loose:
        print(f"  {len(staged_loose)} assets staged loose, {len(invalidated)} replace retail entries")

    from tes3x_plugins import xbox_renames, rename_masters
    is_plugin = lambda rel: rel.lower().endswith(('.esm', '.esp'))
    try:
        renames = xbox_renames([os.path.basename(rel) for rel, _ in loose if is_plugin(rel)])
    except ValueError as error:
        ap.error(str(error))
    for old, new in renames.items():
        print(f"  renaming plugin {old} -> {new}: the Xbox skips a name with two dots")
    loose = [(os.path.join(os.path.dirname(rel), renames[os.path.basename(rel).lower()])
              if is_plugin(rel) and os.path.basename(rel).lower() in renames else rel, full)
             for rel, full in loose]

    seen = {}
    for rel, full in pack:
        h = tes3_hash(rel)
        seen[h] = (rel, full)
    pack = list(seen.values())

    overrides = sum(1 for h in seen if h in base.by_hash)
    print(f"packing {len(pack)} assets ({overrides} override vanilla), {len(loose)} stay loose")

    paths = ['Data Files/' + rel.replace('\\', '/') for rel, _ in loose]
    paths += ['Data Files/Morrowind.bsa', 'Morrowind.ini']
    extra_archives = (json.loads(open(args.mod_archives, encoding="utf-8").read())
                      if args.mod_archives else [])
    # Loose files beat every archive, so the delta archive, which stands in for them, loads last.
    listed = extra_archives + ([args.delta_archive] if args.delta_archive else [])
    if args.delta_archive:
        paths.append('Data Files/' + args.delta_archive)
    if listed:
        paths.append('Data Files/tes3xarch.txt')
    if invalidated and (args.delta_archive or args.no_archive):
        paths.append('ArchiveInvalidationList.txt')
    require_paths(paths, args.remote_root)
    os.makedirs(out_df, exist_ok=True)

    def prog(i, n):
        print(f"\r  writing {i}/{n}", end="", flush=True)

    out_bsa = os.path.join(out_df, "Morrowind.bsa")
    if args.no_archive:
        shutil.copyfile(base_bsa, out_bsa)
        print(f"  Morrowind.bsa: vanilla unchanged, {os.path.getsize(out_bsa)/1048576:.1f} MB")
    elif args.delta_archive:
        # The hook loads the mod archive second; prepending makes its entries override vanilla.
        shutil.copyfile(base_bsa, out_bsa)
        delta_path = os.path.join(out_df, args.delta_archive)
        count, total = write_bsa(delta_path, pack, progress=prog)
        print(f"\r  Morrowind.bsa: vanilla unchanged, "
              f"{os.path.getsize(out_bsa)/1048576:.1f} MB")
        print(f"  {args.delta_archive}: {count} entries, {total/1048576:.1f} MB content, "
              f"{os.path.getsize(delta_path)/1048576:.1f} MB on disk")
    else:
        count, total = write_bsa(out_bsa, pack, base=base, progress=prog,
                                 drop={tes3_hash(rel) for rel, _ in invalidated})
        print(f"\r  Morrowind.bsa: {count} entries, {total/1048576:.1f} MB content, "
              f"{os.path.getsize(out_bsa)/1048576:.1f} MB on disk")

    if listed:
        with open(os.path.join(out_df, "tes3xarch.txt"), "w", newline="\r\n") as f:
            f.write("# extra archives, loaded in order; later lines win\n")
            f.writelines(name + "\n" for name in listed)
        print("  tes3xarch.txt: " + ", ".join(listed))

    copied = 0
    for rel, full in loose:
        dst = os.path.join(out_df, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if full is None:
            with open(dst, "wb") as f:
                f.write(TES3_STUB)
            continue
        shutil.copyfile(full, dst)
        st = os.stat(full)
        os.utime(dst, (st.st_atime, st.st_mtime))
        copied += 1

    from pathlib import Path
    from tes3x_plugins import validate_order, STAMP_BASE, STAMP_STEP
    plugins = {Path(rel).name.lower(): Path(out_df) / rel for rel, _ in loose if is_plugin(rel)}
    if renames:
        for path in plugins.values():
            for old in rename_masters(path, renames):
                print(f"    {path.name}: master {old} -> {renames[old.lower()]}")
    if args.load_order:
        names = json.loads(Path(args.load_order).read_text(encoding='utf-8'))['plugins']
        names = [renames.get(name.lower(), name) for name in names]
    else:
        from tes3x_plugins import dependency_order
        names = dependency_order(plugins)
    names = validate_order(names, plugins)
    for index, name in enumerate(names):
        os.utime(plugins[name], (STAMP_BASE + index * STAMP_STEP,) * 2)
    if invalidated and (args.delta_archive or args.no_archive):
        write_invalidation(Path(args.out) / 'ArchiveInvalidationList.txt', [rel for rel, _ in invalidated])

    ini_src = args.ini or os.path.join(os.path.dirname(args.vanilla.rstrip("/\\")), "Morrowind.ini")
    if os.path.isfile(ini_src):
        text = open(ini_src, encoding="latin-1").read()
        if args.archive_only:
            text = set_ini_key(text, "General", "TryArchiveFirst", 1)
        elif staged_loose:
            text = set_ini_key(text, "General", "TryArchiveFirst", 0)
        edits = []
        if args.quickstart:
            text = set_ini_key(text, "General", "QuickStart", 1)
            edits.append("QuickStart=1")
            if args.quickstart is not True:
                text = set_ini_key(text, "General", "Starting Cell", args.quickstart)
                edits.append(f"Starting Cell={args.quickstart}")
        if args.show_fps:
            text = set_ini_key(text, "General", "Show FPS", 1)
            edits.append("Show FPS=1")
        for item in args.ini_set:
            section, _, rest = item.partition(":")
            key, eq, value = rest.partition("=")
            if not section or not key or not eq:
                raise SystemExit(f"--ini-set wants SECTION:KEY=VALUE, got {item!r}")
            text = set_ini_key(text, section.strip(), key.strip(), value)
            edits.append(f"[{section.strip()}] {key.strip()}={value}")
        open(os.path.join(args.out, "Morrowind.ini"), "w", encoding="latin-1").write(text)
        print(f"  Morrowind.ini staged"
              f"{' with TryArchiveFirst=1' if args.archive_only else ''}")
        for e in edits:
            print(f"    {e}")
    else:
        print(f"  WARNING: no Morrowind.ini found at {ini_src}")

    print(f"  {copied} loose files copied")
    fanout = {}
    for rel, _ in loose:
        fanout[os.path.dirname(rel) or "."] = fanout.get(os.path.dirname(rel) or ".", 0) + 1
    print("\n  loose fanout (FATX cost):")
    for d, n in sorted(fanout.items(), key=lambda e: -e[1])[:6]:
        print(f"    {n:>5}  {d or 'Data Files'}")


if __name__ == "__main__":
    main()
