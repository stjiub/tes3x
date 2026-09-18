#!/usr/bin/env python3
"""Pack a built Data Files tree into a rebuilt Morrowind.bsa and stage a deploy tree.

Assets the engine resolves by exact path go into the archive. Anything it discovers by
directory glob (plugins, Splash, Music) must stay loose, since globs cannot see inside a BSA.
"""

import argparse
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
    """Retail reader uses fgets(260), then unconditionally removes the last byte.

    Write Data Files-relative paths, one per line, including the final newline.
    No comments or blank lines: the engine passes every line to archive lookup.
    """
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
    ap.add_argument("--archive-only", action="store_true",
                    help="set TryArchiveFirst=1 (skips loose lookups entirely)")
    ap.add_argument("--loose-asset", action="append", default=[], metavar="GLOB",
                    help="also stage matching packed assets loose and invalidate their archive entries")
    ap.add_argument("--load-order", help="tes3x_plugins order/patch JSON; stamp all shipped plugins")
    ap.add_argument("--remote-root", default=DEFAULT_REMOTE_ROOT, help="Xbox game folder for physical path checks")
    args = ap.parse_args()
    if args.archive_only and args.loose_asset:
        ap.error('--loose-asset requires TryArchiveFirst=0; archive-only fallback is not verified')
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

    # Expansion stubs: 4-byte TES3 files that satisfy Tribunal/Bloodmoon master references.
    # Without them the engine hunts for missing masters and the load crawls.
    for stub in ("Tribunal.esm", "Bloodmoon.esm"):
        src = os.path.join(args.vanilla, stub)
        if os.path.isfile(src) and not any(r.lower() == stub.lower() for r, _ in loose):
            loose.append((stub, src))
        elif not os.path.isfile(src):
            ap.error(f"required expansion stub missing: {src}")

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

    invalidated = []
    for pattern in args.loose_asset:
        matches = [(rel, src) for rel, src in pack
                   if fnmatch.fnmatchcase(rel.lower().replace('\\', '/'), pattern.lower().replace('\\', '/'))]
        if not matches:
            ap.error(f"--loose-asset matched nothing: {pattern}")
        for rel, src in matches:
            if (rel, src) not in invalidated:
                invalidated.append((rel, src))
    loose.extend(invalidated)

    # TES3Merge output hangs the Xbox loading screen indefinitely; keep it out of builds
    for rel, _src in loose:
        if os.path.basename(rel).lower() == 'merged objects.esp':
            ap.error('Merged Objects.esp hangs the Xbox loading screen; remove it from the tree')

    seen = {}
    collisions = []
    for rel, full in pack:
        h = tes3_hash(rel)
        if h in seen and seen[h][0].lower() != rel.lower():
            collisions.append((seen[h][0], rel))
        seen[h] = (rel, full)
    pack = list(seen.values())

    overrides = sum(1 for h in seen if h in base.by_hash)
    print(f"packing {len(pack)} assets ({overrides} override vanilla), {len(loose)} stay loose")
    if collisions:
        for a, b in collisions:
            print(f"    {a}")
            print(f"    {b}")
        ap.error(f"{len(collisions)} BSA hash collision(s); cannot silently discard an asset")

    paths = ['Data Files/' + rel.replace('\\', '/') for rel, _ in loose]
    paths += ['Data Files/Morrowind.bsa', 'Morrowind.ini']
    if invalidated:
        paths.append('ArchiveInvalidationList.txt')
    require_paths(paths, args.remote_root)
    os.makedirs(out_df, exist_ok=True)

    def prog(i, n):
        print(f"\r  writing {i}/{n}", end="", flush=True)

    out_bsa = os.path.join(out_df, "Morrowind.bsa")
    count, total = write_bsa(out_bsa, pack, base=base, progress=prog)
    print(f"\r  Morrowind.bsa: {count} entries, {total/1048576:.1f} MB content, "
          f"{os.path.getsize(out_bsa)/1048576:.1f} MB on disk")

    copied = 0
    for rel, full in loose:
        dst = os.path.join(out_df, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(full, dst)
        st = os.stat(full)
        os.utime(dst, (st.st_atime, st.st_mtime))
        copied += 1

    from pathlib import Path
    from tes3x_plugins import validate_order, STAMP_BASE, STAMP_STEP
    plugins = {Path(rel).name.lower(): Path(out_df) / rel for rel, _ in loose
               if rel.lower().endswith(('.esm', '.esp'))}
    if args.load_order:
        import json
        names = json.loads(Path(args.load_order).read_text(encoding='utf-8'))['plugins']
    else:
        base_names = ['morrowind.esm', 'tribunal.esm', 'bloodmoon.esm']
        names = base_names + sorted(set(plugins) - set(base_names), key=lambda n: (
            not n.endswith('.esm'), plugins[n].stat().st_mtime, n))
    names = validate_order(names, plugins)
    for index, name in enumerate(names):
        os.utime(plugins[name], (STAMP_BASE + index * STAMP_STEP,) * 2)
    if invalidated:
        write_invalidation(Path(args.out) / 'ArchiveInvalidationList.txt', [rel for rel, _ in invalidated])

    ini_src = args.ini or os.path.join(os.path.dirname(args.vanilla.rstrip("/\\")), "Morrowind.ini")
    if os.path.isfile(ini_src):
        text = open(ini_src, encoding="latin-1").read()
        if args.archive_only:
            text = set_ini_key(text, "General", "TryArchiveFirst", 1)
        elif invalidated:
            text = set_ini_key(text, "General", "TryArchiveFirst", 0)
        open(os.path.join(args.out, "Morrowind.ini"), "w", encoding="latin-1").write(text)
        print(f"  Morrowind.ini staged"
              f"{' with TryArchiveFirst=1' if args.archive_only else ''}")
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
