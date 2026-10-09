"""Pick profiler targets by name and symbolize the counters the console writes.

It reads the symbol map (docs/symbol-map.md). `targets` turns curated names into the address
list `tes3x patch --apply profile=` wants; `report` renders E:\\tes3xprof.bin.
"""

import argparse
import json
import sqlite3
import struct
import sys
from pathlib import Path

from tes3x.paths import resource

DB_PATH = Path.cwd() / 'build' / 'symbols.db'
CURATED = resource('symbols', 'curated.json')

MAGIC = 0x50583354  # "T3XP"
VERSION = 2
HEADER_V1 = '<4I6Q2I3Q8I10I'
HEADER_V2 = HEADER_V1 + '4I'
HEADER_BY_VERSION = {1: HEADER_V1, 2: HEADER_V2}
SLOT = '<2I4Q'
SLOT_SIZE = struct.calcsize(SLOT)
ACTIVE_V1 = '<4IQ'
ACTIVE_V2 = '<8IQ'
ACTIVE_V1_SIZE = struct.calcsize(ACTIVE_V1)
ACTIVE_V2_SIZE = struct.calcsize(ACTIVE_V2)
BUCKET_MS = (8, 12, 16, 20, 25, 33, 50)
DUMP_REASONS = {
    1: 'console', 2: 'frames', 3: 'interval', 4: 'mark', 5: 'pre-menu',
    6: 'first-return', 7: 'first-entry', 8: 'second-entry',
}


def curated():
    if not CURATED.exists():
        return []
    return json.loads(CURATED.read_text())['records']


def name_index():
    return {int(r['va'], 16): r for r in curated()}


def resolve(token, records):
    """A hex VA, an exact name, or an unambiguous case-insensitive substring."""
    try:
        return int(token, 0)
    except ValueError:
        pass
    hits = [r for r in records if r.get('name') == token]
    if not hits:
        low = token.lower()
        hits = [r for r in records if low in r.get('name', '').lower()]
    if not hits:
        raise SystemExit(f'no curated record matches {token!r}')
    if len(hits) > 1:
        names = ', '.join(sorted(r['name'] for r in hits)[:8])
        raise SystemExit(f'{token!r} matches {len(hits)} records: {names}')
    return int(hits[0]['va'], 16)


def cmd_targets(a):
    records = curated()
    db = sqlite3.connect(DB_PATH) if DB_PATH.exists() else None
    chosen = []
    for token in a.target:
        va = resolve(token, records)
        rec = next((r for r in records if int(r['va'], 16) == va), None)
        callers = 0
        if db:
            callers = db.execute('SELECT COUNT(*) FROM edge WHERE tag=? AND dst=?',
                                 ('xbe', va)).fetchone()[0]
        chosen.append((va, rec, callers))
    for va, rec, callers in chosen:
        label = f"{rec['name']} [{rec['confidence']}]" if rec else '(unnamed)'
        print(f'  0x{va:08X}  {label}  {callers} direct call site(s)')
    print()
    print('--apply profile=' + ','.join(f'0x{va:08X}' for va, _, _ in chosen))


def blocks(path):
    data = Path(path).read_bytes()
    at = 0
    while at + 8 <= len(data):
        magic, version = struct.unpack_from('<2I', data, at)
        if magic != MAGIC:
            raise SystemExit(f'{path}: bad magic 0x{magic:08X} at offset {at}')
        header = HEADER_BY_VERSION.get(version)
        if not header:
            raise SystemExit(f'{path}: block version {version}, this reader knows '
                             f'{", ".join(map(str, HEADER_BY_VERSION))}')
        header_size = struct.calcsize(header)
        if at + header_size > len(data):
            print(f'  (truncated header at offset {at}; {len(data) - at} bytes left)')
            return
        f = struct.unpack_from(header, data, at)
        slots, record = f[2], f[3]
        active_count = f[34] if version >= 2 else 0
        active_record = f[35] if version >= 2 else 0
        body = at + header_size
        active_at = body + 4 * slots + record * slots
        end = active_at + active_count * active_record
        if end > len(data):
            print(f'  (truncated block at offset {at}; {len(data) - at} bytes left)')
            return
        targets = struct.unpack_from(f'<{slots}I', data, body)
        rows = [struct.unpack_from(SLOT, data, body + 4 * slots + i * record)
                for i in range(slots)]
        active = []
        if active_count:
            if active_record < ACTIVE_V1_SIZE:
                raise SystemExit(f'{path}: active record is only {active_record} bytes')
            for i in range(active_count):
                pos = active_at + i * active_record
                if active_record >= ACTIVE_V2_SIZE:
                    active.append(struct.unpack_from(ACTIVE_V2, data, pos))
                else:
                    thread, depth, slot, target, elapsed = struct.unpack_from(ACTIVE_V1, data, pos)
                    active.append((thread, depth, slot, target, 0, 0, 0, 0, elapsed))
        yield f, targets, rows, active
        at = end


def ms(cycles, hz):
    return cycles / hz * 1000.0


def report_block(seq, f, targets, rows, active, names, hz_override):
    (_magic, _ver, slots, _record, session, tsc_init, dump_time, tsc_now,
     reset_time, reset_tsc, frames, buckets, frame_total, frame_lo, frame_hi) = f[:15]
    hist = f[15:23]
    (overhead, foreign, overflow, stale, underflow, depth_max, threads,
     mask, build, mask_hi) = f[23:33]
    mask |= mask_hi << 32

    wall_100ns = dump_time - session
    derived = None
    if wall_100ns > 0 and tsc_now > tsc_init:
        derived = (tsc_now - tsc_init) / (wall_100ns / 1e7)
    # 733 MHz nominal, only if the block is too short to measure the real rate.
    hz = hz_override or derived or 733e6
    # Everything below covers the window since the last reset, not the whole session.
    window = max(0, tsc_now - reset_tsc)

    version = f[1]
    free_kb = f[33] if version >= 2 else None
    reason = f[36] if version >= 2 else 0
    reason_text = DUMP_REASONS.get(reason, f'reason-{reason}') if reason else 'legacy'
    mask_width = 16 if mask >> 32 else 8
    print(f'\n=== block {seq}  uptime {wall_100ns / 1e7:.1f}s  '
          f'window {ms(window, hz) / 1000:.1f}s  '
          f'patches 0x{mask:0{mask_width}X}  build 0x{build:08X}  {reason_text} ===')
    if derived:
        print(f'  TSC {derived / 1e6:.2f} MHz measured over {wall_100ns / 1e7:.1f}s'
              + ('  (overridden)' if hz_override else ''))
    if free_kb is not None:
        print(f'  free memory {free_kb} KB')
    print(f'  instrument overhead {overhead} cycles per instrumented call '
          f'({ms(overhead, hz) * 1000:.2f} us)')

    if frames:
        mean = frame_total / frames
        print(f'\n  frames {frames}  mean {ms(mean, hz):.2f} ms ({hz / mean:.1f} fps)  '
              f'min {ms(frame_lo, hz):.2f}  max {ms(frame_hi, hz):.2f}')
        edges = BUCKET_MS[:buckets - 1]
        spans = ([f'<{edges[0]}'] + [f'{a}-{b}' for a, b in zip(edges, edges[1:])]
                 + [f'>={edges[-1]}'])
        for span, n in zip(spans, hist[:buckets]):
            if n:
                print(f'    {span:>7} ms  {n:7d}  {100.0 * n / frames:5.1f}%')

    print(f'\n  {"target":<38} {"calls":>9} {"incl ms":>10} {"excl ms":>10} '
          f'{"mean cyc":>10} {"min":>9} {"max":>11} {"foreign":>8}')
    total_excl = 0
    for i in range(slots - 1):
        calls, skipped, incl, excl, lo, hi = rows[i]
        if not calls and not targets[i]:
            continue
        rec = names.get(targets[i])
        label = rec['name'] if rec else f'0x{targets[i]:08X}'
        if not calls:
            print(f'  {label[:38]:<38} {0:>9}  (never reached on the timed thread, '
                  f'{skipped} elsewhere)')
            continue
        # The instrument's own in-window cost, charged once per call.
        bias = overhead * calls
        incl_net = max(0, incl - bias)
        excl_net = max(0, excl - bias)
        total_excl += excl_net
        print(f'  {label[:38]:<38} {calls:>9} {ms(incl_net, hz):>10.2f} '
              f'{ms(excl_net, hz):>10.2f} {incl_net // calls:>10} {lo:>9} {hi:>11} '
              f'{skipped:>8}')
    if window and total_excl:
        print(f'\n  instrumented exclusive time is {100.0 * total_excl / window:.1f}% '
              f'of the {ms(window, hz) / 1000:.1f}s window')

    calib = rows[slots - 1]
    notes = [f'{threads} thread(s) timed']
    if foreign:
        notes.append(f'{foreign} call(s) with no free shadow stack, untimed')
    if overflow:
        notes.append(f'{overflow} shadow-stack overflow(s)')
    if stale:
        notes.append(f'{stale} frame(s) reclaimed after a non-local exit')
    if underflow:
        notes.append(f'{underflow} UNDERFLOW - a return address was lost')
    notes.append(f'max nesting {depth_max}')
    notes.append(f'calibration {calib[0]} calls')
    print('\n  ' + '; '.join(notes))

    if active:
        print(f'\n  active instrumented calls at dump ({len(active)}):')
        for thread, depth, slot, target, caller, self, arg0, arg1, elapsed in active:
            rec = names.get(target)
            label = rec['name'] if rec else f'0x{target:08X}'
            print(f'    thread 0x{thread:08X} depth {depth:2d} slot {slot:2d}  '
                  f'{ms(elapsed, hz):10.2f} ms  {label}')
            if caller:
                print(f'      caller 0x{caller:08X}  self 0x{self:08X}  '
                      f'arg0 0x{arg0:08X}  arg1 0x{arg1:08X}')


def cmd_report(a):
    names = name_index()
    any_block = False
    for seq, (f, targets, rows, active) in enumerate(blocks(a.dump)):
        report_block(seq, f, targets, rows, active, names, a.hz)
        any_block = True
    if not any_block:
        raise SystemExit(f'{a.dump}: no complete block')


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)

    t = sub.add_parser('targets', help='resolve names to the --apply profile= list')
    t.add_argument('target', nargs='+', help='a curated name, a substring of one, or a VA')
    t.set_defaults(fn=cmd_targets)

    r = sub.add_parser('report', help='symbolize a tes3xprof.bin dump')
    r.add_argument('dump')
    r.add_argument('--hz', type=float, default=0.0,
                   help='override the TSC rate instead of deriving it from the block')
    r.set_defaults(fn=cmd_report)

    a = ap.parse_args()
    a.fn(a)


if __name__ == '__main__':
    main()
