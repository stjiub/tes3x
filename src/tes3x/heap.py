"""Render the heap census the console writes to E:\\tes3xheap.bin.

Each snapshot lists live Memory_Heap bytes by the source file and line the allocation passed,
or by call site where the engine passed none (`NA`). File names are string pointers into the
XBE, read back from the retail image; call sites are named from the symbol map when it has
them. Live figures are sampled and scaled; allocation counts are exact.
`--compare` subtracts a baseline census, such as retail's, from each snapshot.
"""

import argparse
import json
import sqlite3
import struct
import tomllib
from collections import defaultdict
from pathlib import Path

import tes3x.inject as tes3x_inject  # noqa: E402
from tes3x.paths import local_config, resource  # noqa: E402

DB_PATH = Path.cwd() / 'build' / 'symbols.db'
CURATED = resource('symbols', 'curated.json')
MAGIC = 0x48583354  # "T3XH"
VERSIONS = (2, 3)
HEADER = '<4I2Q12I9II24I'
HEADER_SIZE = struct.calcsize(HEADER)
HEADER_FIELDS = ('magic', 'version', 'build_id', 'reason', 'session', 'dump_time', 'seq',
                 'sample_shift', 'sites', 'record', 'live_blocks', 'live_bytes', 'peak_bytes',
                 'untracked_allocs', 'unknown_frees', 'failed_allocs', 'saturated',
                 'stale_blocks', 'mm_length', 'total_pages', 'available_pages',
                 'vm_committed', 'vm_reserved', 'cache_pages', 'pool_pages', 'stack_pages',
                 'image_pages', 'heap_object')
WALK_BUCKETS = 24
WALK_MB = 96
WALK = '<7I%dI%dI%dI' % (WALK_BUCKETS, WALK_BUCKETS, WALK_MB)
WALK_FIELDS = ('complete', 'used_blocks', 'used_bytes', 'free_blocks', 'free_bytes',
               'free_pages', 'largest_free')
SITE = '<8IQ'
SITE_FIELDS = ('file', 'line', 'caller', 'live_bytes', 'live_blocks', 'peak_bytes', 'allocs',
               'reserved', 'alloc_bytes')
REASONS = {1: 'console', 2: 'first frame'}


def default_xbe():
    local = local_config()
    if local.exists():
        root = tomllib.loads(local.read_text()).get('paths', {}).get('vanilla_root')
        if root:
            return Path(root) / 'morrowind.xbe'
    return None


def read_dumps(path):
    data = Path(path).read_bytes()
    dumps, off = [], 0
    while off + HEADER_SIZE <= len(data):
        values = struct.unpack_from(HEADER, data, off)
        hdr = dict(zip(HEADER_FIELDS, values))
        hdr['heap'] = values[len(HEADER_FIELDS):]
        if hdr['magic'] != MAGIC or hdr['version'] not in VERSIONS:
            raise SystemExit('%s: not a census of versions %s at offset %d' % (
                path, VERSIONS, off))
        off += HEADER_SIZE
        hdr['walk'] = None
        if hdr['version'] >= 3:
            values = struct.unpack_from(WALK, data, off)
            walk = dict(zip(WALK_FIELDS, values))
            n = len(WALK_FIELDS)
            walk['free_count'] = values[n:n + WALK_BUCKETS]
            walk['free_sum'] = values[n + WALK_BUCKETS:n + 2 * WALK_BUCKETS]
            walk['free_mb'] = values[n + 2 * WALK_BUCKETS:]
            hdr['walk'] = walk
            off += struct.calcsize(WALK)
        scale = 1 << hdr['sample_shift']
        sites = []
        for _ in range(hdr['sites']):
            site = dict(zip(SITE_FIELDS, struct.unpack_from(SITE, data, off)))
            site['live_bytes'] *= scale
            site['live_blocks'] *= scale
            sites.append(site)
            off += hdr['record']
        hdr['site_list'] = sites
        dumps.append(hdr)
    return dumps


class Names:
    def __init__(self, xbe):
        self.image = tes3x_inject.Xbe(Path(xbe).read_bytes()) if xbe else None
        self.files = {}
        self.db = sqlite3.connect(DB_PATH) if DB_PATH.exists() else None
        self.curated = {}
        if CURATED.exists():
            for rec in json.loads(CURATED.read_text())['records']:
                self.curated[int(rec['va'], 16)] = rec['name']

    def file(self, va):
        if va not in self.files:
            off = self.image.va_to_off(va) if self.image and va else None
            if off is None:
                self.files[va] = '0x%08X' % va
            else:
                end = self.image.data.index(0, off)
                text = bytes(self.image.data[off:end]).decode('latin-1')
                self.files[va] = text.replace('.\\', '').replace('\\', '/')
        return self.files[va]

    def function(self, va):
        """The function holding a call site, by curated name or start address."""
        if not self.db:
            return '0x%08X' % va
        row = self.db.execute('SELECT va FROM func WHERE tag=? AND va<=? AND end>?',
                              ('xbe', va, va)).fetchone()
        if not row:
            return '0x%08X' % va
        return self.curated.get(row[0], 'sub_%08X' % row[0])

    def site(self, s, per_file):
        name = self.file(s['file'])
        if s['line']:
            return name if per_file else '%s:%d' % (name, s['line'])
        if per_file:
            return '%s in %s' % (name, self.function(s['caller']))
        return '%s @0x%08X %s' % (name, s['caller'], self.function(s['caller']))


def by_key(dump, names, per_file):
    totals = defaultdict(lambda: [0, 0, 0])
    for s in dump['site_list']:
        t = totals[names.site(s, per_file)]
        t[0] += s['live_bytes']
        t[1] += s['live_blocks']
        t[2] += s['allocs']
    return totals


def kb(n):
    return '%10s' % format(round(n / 1024), ',')


def report_walk(dump):
    w = dump['walk']
    top = dump['heap'][3]
    print('  region walk%s: top %s KB = %s KB in %s used blocks + %s KB in %s free blocks '
          '+ %s KB headers' % (
              '' if w['complete'] else ' (incomplete)', format(top // 1024, ','),
              format(w['used_bytes'] // 1024, ','), format(w['used_blocks'], ','),
              format(w['free_bytes'] // 1024, ','), format(w['free_blocks'], ','),
              format((w['used_blocks'] + w['free_blocks']) * 8 // 1024, ',')))
    print('    whole free pages %s (%s KB); largest free block %s bytes' % (
        format(w['free_pages'], ','), format(w['free_pages'] * 4, ','),
        format(w['largest_free'], ',')))
    print('    %12s %10s %10s' % ('free size', 'blocks', 'KB'))
    for b, (count, total) in enumerate(zip(w['free_count'], w['free_sum'])):
        if count:
            print('    %12s %10s %10s' % ('%d-%d' % (1 << b, (2 << b) - 1), format(count, ','),
                                          format(total // 1024, ',')))
    last = max((i for i, v in enumerate(w['free_mb']) if v), default=-1)
    if last >= 0:
        print('    free KB per MB of region, from its base:')
        row = [format(v // 1024, '>5') for v in w['free_mb'][:last + 1]]
        for i in range(0, len(row), 16):
            print('    %3d  %s' % (i, ' '.join(row[i:i + 16])))


def report(dump, names, args, base=None):
    scale = 1 << dump['sample_shift']
    print('dump %d  session %016X  %s  build 0x%08X' % (
        dump['seq'], dump['session'], REASONS.get(dump['reason'], dump['reason']),
        dump['build_id']))
    print('  heap live ~%s KB in ~%s blocks (1 in %d sampled)' % (
        format(dump['live_bytes'] * scale // 1024, ','),
        format(dump['live_blocks'] * scale, ','), scale))
    if dump['mm_length']:
        pages = lambda n: format(n * 4, ',')  # noqa: E731
        print('  kernel: %s of %s KB free; committed %s KB virtual, pool %s, cache %s, '
              'stacks %s, image %s KB' % (
                  pages(dump['available_pages']), pages(dump['total_pages']),
                  format(dump['vm_committed'] // 1024, ','), pages(dump['pool_pages']),
                  pages(dump['cache_pages']), pages(dump['stack_pages']),
                  pages(dump['image_pages'])))
    if args.heap_words:
        words = dump['heap']
        print('  heap object 0x%08X:' % dump['heap_object'])
        for i in range(0, len(words), 8):
            print('    +0x%02X  %s' % (i * 4, ' '.join('%08X' % w for w in words[i:i + 8])))
    if dump['walk'] and args.walk:
        report_walk(dump)
    gaps = [(k, dump[k]) for k in ('untracked_allocs', 'unknown_frees', 'failed_allocs',
                                   'saturated', 'stale_blocks') if dump[k]]
    if gaps:
        print('  ' + '   '.join('%s %s' % (k, format(v, ',')) for k, v in gaps))

    rows = by_key(dump, names, args.by_file)
    if base is not None:
        before = by_key(base, names, args.by_file)
        for key in set(rows) | set(before):
            now, was = rows.get(key, [0, 0, 0]), before.get(key, [0, 0, 0])
            rows[key] = [now[0] - was[0], now[1] - was[1], now[2] - was[2]]
        print('\n  change against the baseline census')
    ordered = sorted(rows.items(), key=lambda kv: -abs(kv[1][0]))[:args.top]
    print('\n  %10s %10s %12s  %s' % ('KB', 'blocks', 'bytes/block',
                                      'file' if args.by_file else 'site'))
    for key, (live, blocks, _allocs) in ordered:
        per = '%12.0f' % (live / blocks) if blocks else '%12s' % '-'
        print('  %s %10s %s  %s' % (kb(live), format(blocks, ','), per, key))
    print()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('dump', help='tes3xheap.bin')
    ap.add_argument('--xbe', default=default_xbe(), help='retail morrowind.xbe, for file names')
    ap.add_argument('--by-file', action='store_true',
                    help='group by source file, and NA sites by function')
    ap.add_argument('--top', type=int, default=40)
    ap.add_argument('--compare', metavar='BASELINE.bin',
                    help='subtract the last snapshot of another census')
    ap.add_argument('--last', action='store_true', help='only the last snapshot')
    ap.add_argument('--walk', action='store_true',
                    help='print the region walk: free blocks by size and by address')
    ap.add_argument('--heap-words', action='store_true',
                    help='print the raw words of the Memory_Heap object')
    args = ap.parse_args()

    names = Names(args.xbe)
    dumps = read_dumps(args.dump)
    if not dumps:
        raise SystemExit('%s: no snapshots' % args.dump)
    base = read_dumps(args.compare)[-1] if args.compare else None
    for dump in dumps[-1:] if args.last else dumps:
        report(dump, names, args, base)


if __name__ == '__main__':
    main()
