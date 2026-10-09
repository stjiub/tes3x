"""Render the memory census the console writes to E:\\tes3xmem.bin.

Each snapshot accounts for physical memory outside Memory_Heap: committed virtual memory by the
reservation that holds it and who reserved it, contiguous and system memory and pool by caller,
and live XAPI heap blocks (CRT malloc) by caller. Callers are the return addresses found on the
stack, named from the symbol map; `--depth` sets how many make up one row.
"""

import argparse
import struct
from collections import defaultdict
from pathlib import Path

from tes3x.heap import Names, default_xbe  # noqa: E402

MAGIC = 0x4D583354  # "T3XM"
VERSION = 1
HEADER = '<4I2Q15I9I'
HEADER_SIZE = struct.calcsize(HEADER)
HEADER_FIELDS = ('magic', 'version', 'build_id', 'reason', 'session', 'dump_time', 'seq',
                 'sample_shift', 'frames', 'sites', 'site_record', 'reservations', 'regions',
                 'untracked', 'unknown_frees', 'exact_used', 'sampled_used', 'own_bytes',
                 'stack_ok', 'stack_skipped', 'reserved', 'mm_length', 'total_pages',
                 'available_pages', 'vm_committed', 'vm_reserved', 'cache_pages', 'pool_pages',
                 'stack_pages', 'image_pages')
KINDS = {1: 'virtual', 2: 'commit', 3: 'contiguous', 4: 'system', 5: 'pool', 6: 'gpu',
         7: 'heap'}
REASONS = {1: 'console', 2: 'first frame'}
MEM_COMMIT = 0x1000


def read_dumps(path):
    data = Path(path).read_bytes()
    dumps, off = [], 0
    while off + HEADER_SIZE <= len(data):
        hdr = dict(zip(HEADER_FIELDS, struct.unpack_from(HEADER, data, off)))
        if hdr['magic'] != MAGIC or hdr['version'] != VERSION:
            raise SystemExit('%s: not a version %d census at offset %d' % (path, VERSION, off))
        off += HEADER_SIZE
        n = hdr['frames']
        fmt = '<%dI' % (n + 2) + 'Q4I'
        sites = []
        for _ in range(hdr['sites']):
            v = struct.unpack_from(fmt, data, off)
            sites.append({'kind': KINDS.get(v[0], v[0]), 'frames': [f for f in v[1:1 + n] if f],
                          'allocs': v[n + 1], 'alloc_bytes': v[n + 2], 'live_bytes': v[n + 3],
                          'live_blocks': v[n + 4], 'peak_bytes': v[n + 5], 'failed': v[n + 6]})
            off += hdr['site_record']
        hdr['site_list'] = sites
        hdr['reservation_list'] = [struct.unpack_from('<3I', data, off + 12 * i)
                                   for i in range(hdr['reservations'])]
        off += 12 * hdr['reservations']
        hdr['region_list'] = [struct.unpack_from('<6I', data, off + 24 * i)
                              for i in range(hdr['regions'])]
        off += 24 * hdr['regions']
        dumps.append(hdr)
    return dumps


def kb(n):
    return '%9s' % format(round(n / 1024), ',')


class Labels:
    def __init__(self, names, depth, skip):
        self.names, self.depth, self.skip = names, depth, skip

    def __call__(self, frames):
        named = []
        for va in frames:
            fn = self.names.function(va)
            if fn in self.skip or (named and named[-1] == fn):
                continue
            named.append(fn)
            if len(named) == self.depth:
                break
        return ' < '.join(named) or '-'


def table(title, rows, total=None):
    print('\n  %s' % title)
    print('  %9s %9s %9s  %s' % ('KB', 'blocks', 'allocs', 'caller'))
    for key, (live, blocks, allocs) in sorted(rows.items(), key=lambda kv: -kv[1][0]):
        print('  %s %9s %9s  %s' % (kb(live), format(blocks, ','), format(allocs, ','), key))
    if total is not None:
        print('  %s  total' % kb(total))


def report(dump, labels, top):
    page = 4096
    sites = dump['site_list']
    print('dump %d  session %016X  %s  build 0x%08X' % (
        dump['seq'], dump['session'], REASONS.get(dump['reason'], dump['reason']),
        dump['build_id']))
    notes = [(k, dump[k]) for k in ('untracked', 'unknown_frees', 'stack_skipped') if dump[k]]
    if not dump['stack_ok']:
        notes.append(('stack base unusable at entry', 1))
    if notes:
        print('  ' + '   '.join('%s %s' % (k, format(v, ',')) for k, v in notes))

    live = defaultdict(int)
    for s in sites:
        live[s['kind']] += s['live_bytes']

    # Committed virtual memory, by the reservation that holds it.
    by_base = {base: (size, site) for base, size, site in dump['reservation_list']}
    committed = defaultdict(int)
    for base, alloc_base, size, state, _protect, _type in dump['region_list']:
        if state == MEM_COMMIT:
            committed[alloc_base] += size
    vm_rows = defaultdict(lambda: [0, 0, 0])
    for alloc_base, size in committed.items():
        if alloc_base in by_base:
            key = labels(sites[by_base[alloc_base][1]]['frames'])
        else:
            key = 'before the census, 0x%08X' % alloc_base
        row = vm_rows[key]
        row[0] += size
        row[1] += 1
    vm_total = sum(committed.values())

    used = (dump['total_pages'] - dump['available_pages']) * page
    print('\n  physical %s KB, used %s KB, free %s KB' % (
        format(dump['total_pages'] * 4, ','), format(used // 1024, ','),
        format(dump['available_pages'] * 4, ',')))
    # The kernel counts the image as committed virtual memory; the census tables are system
    # memory allocated before the thunks were wrapped.
    parts = [('virtual memory committed, image %s KB included' % format(
                  dump['image_pages'] * 4, ','), dump['vm_committed']),
             ('contiguous, tracked', live['contiguous']),
             ('system memory, tracked', live['system']),
             ('census tables', dump['own_bytes']),
             ('pool pages (kernel)', dump['pool_pages'] * page),
             ('stacks (kernel)', dump['stack_pages'] * page),
             ('file cache (kernel)', dump['cache_pages'] * page),
             ('GPU instance memory', live['gpu'])]
    for name, n in parts:
        print('  %s  %s' % (kb(n), name))
    rest = used - sum(n for _name, n in parts)
    print('  %s  rest: kernel, and allocations made before the census' % kb(rest))
    print('  %s  used without the census tables' % kb(used - dump['own_bytes']))
    print('  %s  committed in the address space walk (%d regions)' % (
        kb(vm_total), len(dump['region_list'])))

    def rows_of(kind):
        rows = defaultdict(lambda: [0, 0, 0])
        for s in sites:
            if s['kind'] == kind:
                row = rows[labels(s['frames'])]
                row[0] += s['live_bytes']
                row[1] += s['live_blocks']
                row[2] += s['allocs']
        return dict(sorted(rows.items(), key=lambda kv: -kv[1][0])[:top])

    table('committed virtual memory, by who reserved it', dict(
        sorted(vm_rows.items(), key=lambda kv: -kv[1][0])[:top]), vm_total)
    table('contiguous memory', rows_of('contiguous'), live['contiguous'])
    table('system memory', rows_of('system'), live['system'])
    table('pool', rows_of('pool'), live['pool'])
    table('XAPI heap, live (1 in %d below 64 KB sampled)' % (1 << dump['sample_shift']),
          rows_of('heap'), live['heap'])
    commits = defaultdict(lambda: [0, 0, 0])
    for s in sites:
        if s['kind'] == 'commit':
            row = commits[labels(s['frames'])]
            row[0] += s['alloc_bytes']
            row[2] += s['allocs']
    table('commits into existing reservations, cumulative', dict(
        sorted(commits.items(), key=lambda kv: -kv[1][0])[:top]))
    failed = [(labels(s['frames']), s['kind'], s['failed']) for s in sites if s['failed']]
    if failed:
        print('\n  failed allocations')
        for key, kind, n in failed:
            print('  %9s  %s %s' % (format(n, ','), kind, key))
    print()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('dump', help='tes3xmem.bin')
    ap.add_argument('--xbe', default=default_xbe(), help='retail morrowind.xbe')
    ap.add_argument('--depth', type=int, default=3, help='caller frames per row')
    ap.add_argument('--skip', action='append', default=[], metavar='NAME',
                    help='leave this function out of caller chains (repeatable)')
    ap.add_argument('--top', type=int, default=25)
    ap.add_argument('--last', action='store_true', help='only the last snapshot')
    args = ap.parse_args()

    labels = Labels(Names(args.xbe), args.depth, set(args.skip))
    dumps = read_dumps(args.dump)
    if not dumps:
        raise SystemExit('%s: no snapshots' % args.dump)
    for dump in dumps[-1:] if args.last else dumps:
        report(dump, labels, args.top)


if __name__ == '__main__':
    main()
