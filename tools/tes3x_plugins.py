"""Sort plugins with mlox, on a copy staged with expanded Xbox stubs."""
import argparse
import hashlib
import importlib.metadata
import json
import logging
import os
import re
import shutil
import struct
import sys
import types
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


def collect(built, vanilla, stubs):
    files = {p.name.lower(): p for p in Path(built).iterdir() if p.suffix.lower() in {'.esm', '.esp'}}
    for name in ('Morrowind.esm', 'Tribunal.esm', 'Bloodmoon.esm'):
        if name.lower() in files:
            continue
        path = Path(vanilla) / name
        if not path.is_file():
            if name == 'Morrowind.esm':
                raise ValueError(f'missing retail master: {path}')
            # Retail Xbox ships no expansion masters; pack generates the same stub.
            path = Path(stubs) / name
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(b'TES3')
        files[name.lower()] = path
    return files


def stage(files, names, work):
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


def mlox_sort(work, rules):
    """Sort the staged plugins with the mlox package; return the order and mlox's messages."""
    # mlox.resources only locates the rules under the user's profile, and needs appdirs and
    # pkg_resources to do it. Supplying it here lets a --no-deps install work.
    resources = types.ModuleType('mlox.resources')
    resources.base_file, resources.user_file = str(rules), str(work / 'no-user-rules.txt')
    sys.modules['mlox.resources'] = resources
    try:
        from mlox import loadOrder
    except ImportError as exc:
        raise RuntimeError('mlox is not installed; run: python -m pip install mlox') from exc
    loadOrder.base_file, loadOrder.user_file = resources.base_file, resources.user_file
    logging.getLogger('mlox').setLevel(logging.ERROR)
    cwd = os.getcwd()
    os.chdir(work)  # mlox writes its .out files to the working directory
    try:
        order = loadOrder.loadorder()
        order.game_type = 'Morrowind'
        order.plugin_file = str(work / 'Morrowind.ini')
        order.datadir = str(work / 'Data Files')
        order.get_active_plugins()
        messages = order.update()
    finally:
        os.chdir(cwd)
    if messages is False:
        raise RuntimeError(f'mlox could not sort the plugins; check the rules file {rules}')
    return order.new_order, messages


def mlox_version():
    try:
        return importlib.metadata.version('mlox')
    except importlib.metadata.PackageNotFoundError:
        return 'unknown'


def warnings(messages):
    """mlox's message blocks other than plain notes, which are mostly PC advice."""
    blocks = re.split(r'(?m)^(?=\[[A-Z]+\])', messages)
    return [block.strip() for block in blocks if block.strip() and not block.startswith('[NOTE]')]


def run_order(built, vanilla, rules, work, output):
    work = Path(work).resolve()
    work.mkdir(parents=True, exist_ok=False)
    files = collect(built, vanilla, work / 'stubs')
    stage(files, dependency_order(files), work)
    names, messages = mlox_sort(work, Path(rules).resolve())
    names = validate_order(names, files)
    notes = Path(output).with_name('mlox-messages.txt')
    notes.write_text(messages, encoding='utf-8')
    payload = {'plugins': [files[n].name for n in names], 'mlox': mlox_version(),
               'rules_sha256': digest(rules),
               'input_sha256': {n: digest(files[n]) for n in sorted(files)}}
    Path(output).write_text(json.dumps(payload, indent=2), encoding='utf-8')
    for block in warnings(messages):
        print(block)
    print(f'mlox {mlox_version()}: sorted {len(names)} plugins; notes in {notes.name}')


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('action', choices=['order'])
    ap.add_argument('built')
    ap.add_argument('--vanilla', required=True)
    ap.add_argument('--rules', required=True, help="mlox_base.txt from the mlox-rules project")
    ap.add_argument('--work', required=True, help='new isolated working directory')
    ap.add_argument('--out', required=True)
    args = ap.parse_args()
    sys.stdout.reconfigure(errors='replace')
    run_order(args.built, args.vanilla, args.rules, args.work, args.out)
