"""Run mlox with expanded Xbox stubs in an isolated workspace."""
import argparse
import hashlib
import json
import os
import shutil
import struct
import subprocess
from pathlib import Path

from tes3x_build import plugin_masters
from tes3x_records import records, subrecords

STAMP_BASE = 978307200  # 2001-01-01 UTC, representable on FATX
STAMP_STEP = 4


def digest(path):
    with open(path, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


BASE_MASTERS = ('morrowind.esm', 'tribunal.esm', 'bloodmoon.esm')


def dependency_order(files, mtime=True):
    """Stable dependency order with retail masters first and mtime/name tiebreaks."""
    names = list(files)
    known = set(names)
    masters = {n: {m.lower() for m in plugin_masters(str(files[n]))} & known - {n}
               for n in names}

    def rank(n):
        base = BASE_MASTERS.index(n) if n in BASE_MASTERS else len(BASE_MASTERS)
        stamp = files[n].stat().st_mtime if mtime else 0
        return (base, not n.endswith('.esm'), stamp, n)

    ordered, placed, remaining = [], set(), set(names)
    while remaining:
        ready = [n for n in remaining if masters[n] <= placed]
        if not ready:
            raise ValueError('circular master references among: %s'
                             % ', '.join(sorted(remaining)))
        pick = min(ready, key=rank)
        ordered.append(pick)
        placed.add(pick)
        remaining.discard(pick)
    return ordered


def validate_order(names, files):
    names = [n.lower() for n in names]
    if len(names) != len(set(names)) or set(names) != set(files):
        raise ValueError('load order must contain every input plugin exactly once')
    seen = set()
    had_plugin = False
    for name in names:
        if name.endswith('.esm') and had_plugin:
            raise ValueError('Xbox loads all masters before ESPs')
        had_plugin |= name.endswith('.esp')
        for master in plugin_masters(str(files[name])):
            if master.lower() not in seen:
                raise ValueError(f'{name}: master {master} is missing or loads later')
        seen.add(name)
    return names


def collect(built, vanilla):
    files = {p.name.lower(): p for p in Path(built).iterdir() if p.suffix.lower() in {'.esm', '.esp'}}
    for name in ('Morrowind.esm', 'Tribunal.esm', 'Bloodmoon.esm'):
        if name.lower() not in files:
            path = Path(vanilla) / name
            if not path.is_file():
                raise ValueError(f'missing required retail master/stub: {path}')
            files[name.lower()] = path
    return files


def stage(files, names, work):
    work = Path(work).resolve()
    work.mkdir(parents=True, exist_ok=False)
    data = work / 'Data Files'
    data.mkdir()
    for i, name in enumerate(names):
        source = files[name]
        target = data / source.name
        if source.read_bytes() == b'TES3':
            # Tools expect a complete HEDR even though Xbox accepts four bytes.
            hedr = struct.pack('<fI', 1.3, 1) + bytes(32 + 256) + struct.pack('<I', 0)
            payload = b'HEDR' + struct.pack('<I', len(hedr)) + hedr
            target.write_bytes(b'TES3' + struct.pack('<III', len(payload), 0, 0) + payload)
        else:
            shutil.copyfile(source, target)
        os.utime(target, (STAMP_BASE + i * STAMP_STEP,) * 2)
    (work / 'Morrowind.ini').write_text('[Game Files]\n' + ''.join(
        f'GameFile{i}={files[name].name}\n' for i, name in enumerate(names)), encoding='cp1252')
    return work


def run_order(built, vanilla, executable, rules, work, output):
    files = collect(built, vanilla)
    work = stage(files, dependency_order(files), work)
    # Legacy mlox identifies a Morrowind directory solely by this filename.
    # This zero-byte marker is never executable and never leaves the workspace.
    (work / 'Morrowind.exe').write_bytes(b'')
    exe = Path(executable).resolve()
    for src in exe.parent.iterdir():
        if src.suffix.lower() in {'.exe', '.msg', '.dll', '.gif', '.ico'}:
            shutil.copyfile(src, work / src.name)
    shutil.copyfile(rules, work / 'mlox_base.txt')
    result = subprocess.run([str(work / exe.name), '-n', '-c'], cwd=work,
                            capture_output=True, timeout=180)
    (work / 'process.log').write_bytes(result.stdout + result.stderr)
    result.check_returncode()
    candidates = [work / 'mlox_new_loadorder.out', work / 'mlox_loadorder.out']
    order_file = next((p for p in candidates if p.is_file()), None)
    if order_file is None:
        raise RuntimeError(f'mlox produced no load order; inspect {work} logs')
    names = [line.strip() for line in order_file.read_text(encoding='cp1252').splitlines() if line.strip()]
    names = validate_order(names, files)
    payload = {'plugins': [files[n].name for n in names], 'rules_sha256': digest(rules),
               'tool_sha256': digest(exe), 'input_sha256': {n: digest(files[n]) for n in sorted(files)}}
    Path(output).write_text(json.dumps(payload, indent=2), encoding='utf-8')
    print(f'mlox: validated {len(names)} plugins -> {output}')


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('action', choices=['order'])
    ap.add_argument('built')
    ap.add_argument('--vanilla', required=True)
    ap.add_argument('--tool', required=True)
    ap.add_argument('--work', required=True, help='new isolated working directory')
    ap.add_argument('--out', required=True)
    ap.add_argument('--rules')
    args = ap.parse_args()
    if not args.rules:
        ap.error('order requires --rules')
    run_order(args.built, args.vanilla, args.tool, args.rules, args.work, args.out)
