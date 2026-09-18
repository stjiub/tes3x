#!/usr/bin/env python3
"""Audit a Morrowind Xbox Data Files directory for load cost and FATX hazards."""

import argparse
import os
import sys
from collections import defaultdict

FATX_DIRENT = 64
FATX_NAME_MAX = 42
FATX_CLUSTER = 16384

PLUGIN_EXT = (".esm", ".esp")


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024


def scan(root):
    dirs = defaultdict(list)
    for dirpath, _, filenames in os.walk(root):
        rel = os.path.relpath(dirpath, root).replace("\\", "/")
        for name in filenames:
            try:
                size = os.path.getsize(os.path.join(dirpath, name))
            except OSError:
                size = 0
            dirs[rel].append((name, size))
    return dirs


def plugins(entries):
    masters, plugs = [], []
    for name, size in entries:
        ext = os.path.splitext(name)[1].lower()
        if ext == ".esm":
            masters.append((name, size))
        elif ext == ".esp":
            plugs.append((name, size))
    key = lambda e: e[0].lower()
    return sorted(masters, key=key, reverse=True), sorted(plugs, key=key, reverse=True)


def report_plugins(entries):
    masters, plugs = plugins(entries)
    total = sum(s for _, s in masters + plugs)
    print(f"== PLUGIN LOAD ({len(masters)} esm, {len(plugs)} esp, {human(total)}) ==")
    print("   enumeration order: masters first, then plugins, reverse-alphabetical\n")
    for label, group in (("ESM", masters), ("ESP", plugs)):
        for name, size in group:
            flag = "  <-- STUB" if size < 64 else ""
            print(f"  {label}  {human(size):>10}  {name}{flag}")
    print()
    return total


def report_junk(entries):
    junk = [(n, s) for n, s in entries
            if not n.lower().endswith(PLUGIN_EXT + (".bsa",))]
    if not junk:
        return
    junk.sort(key=lambda e: -e[1])
    total = sum(s for _, s in junk)
    print(f"== NON-GAME FILES IN ROOT ({len(junk)}, {human(total)}) ==")
    for name, size in junk[:20]:
        print(f"  {human(size):>10}  {name}")
    print()


def report_fanout(dirs):
    rows = []
    for rel, entries in dirs.items():
        n = len(entries)
        if n == 0:
            continue
        clusters = -(-(n * FATX_DIRENT) // FATX_CLUSTER)
        rows.append((n, clusters, rel))
    rows.sort(reverse=True)
    print("== FATX DIRECTORY FANOUT (lookup cost is linear in entries) ==")
    print(f"{'entries':>8} {'clusters/miss':>14}  path")
    for n, clusters, rel in rows[:15]:
        mark = "  <-- HOT" if n >= 1000 else ""
        print(f"{n:>8} {clusters:>14}  {rel}{mark}")
    print()


def report_fatx_names(dirs):
    bad = []
    for rel, entries in dirs.items():
        for name, _ in entries:
            if len(name) > FATX_NAME_MAX:
                bad.append(f"{rel}/{name}" if rel != "." else name)
    if bad:
        print(f"== FILENAMES EXCEEDING FATX {FATX_NAME_MAX}-CHAR LIMIT ({len(bad)}) ==")
        for path in bad[:20]:
            print(f"  {path}")
        print()


def report_dupes(entries):
    by_size = defaultdict(list)
    for name, size in entries:
        if size > 1024 * 1024:
            by_size[size].append(name)
    dupes = {s: n for s, n in by_size.items() if len(n) > 1}
    if dupes:
        print("== IDENTICAL-SIZE FILES IN ROOT (possible duplicates) ==")
        for size, names in sorted(dupes.items(), reverse=True):
            print(f"  {human(size):>10}  {', '.join(names)}")
        print()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("path", help="Data Files directory")
    args = ap.parse_args()

    if not os.path.isdir(args.path):
        sys.exit(f"not a directory: {args.path}")

    dirs = scan(args.path)
    root = dirs.get(".", [])
    total_files = sum(len(v) for v in dirs.values())
    total_bytes = sum(s for v in dirs.values() for _, s in v)

    print(f"\nData Files: {os.path.abspath(args.path)}")
    print(f"{total_files} files, {human(total_bytes)}, {len(dirs)} directories\n")

    report_plugins(root)
    report_junk(root)
    report_dupes(root)
    report_fanout(dirs)
    report_fatx_names(dirs)


if __name__ == "__main__":
    main()
