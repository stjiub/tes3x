"""Symbol map: function inventory and cross-image matching for morrowind.xbe.

Derived data lives in a regenerable SQLite database, build/symbols.db under the working
folder; names live in the committed symbols/curated.json. See docs/symbol-map.md.
"""
import argparse
import bisect
import json
import os
import re
import sqlite3
import struct
import sys
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tes3x_inject import Xbe  # noqa: E402

try:
    from capstone import Cs, CS_ARCH_X86, CS_MODE_32, CS_OP_IMM, CS_OP_MEM
    from capstone.x86 import X86_OP_MEM
except ImportError as exc:
    raise SystemExit('the symbol map needs capstone; run `python -m pip install capstone`') \
        from exc

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = Path.cwd() / 'build' / 'symbols.db'
CURATED = ROOT / 'symbols' / 'curated.json'

MAX_FUNC = 0x10000
BODY_GAP = 64
STOP = {'ret', 'retf', 'iret', 'iretd', 'hlt', 'ud2', 'int3'}


def u16(b, o):
    return struct.unpack_from('<H', b, o)[0]


def u32(b, o):
    return struct.unpack_from('<I', b, o)[0]


class Image:
    """Uniform view over an XBE or a PE32 executable."""

    def __init__(self, path, tag):
        self.path, self.tag = str(path), tag
        self.data = open(path, 'rb').read()
        if self.data[:4] == b'XBEH':
            self._load_xbe()
        elif self.data[:2] == b'MZ':
            self._load_pe()
        else:
            raise SystemExit(f'{path}: not an XBE or PE image')

    def _load_xbe(self):
        x = Xbe(self.data)
        self.base, self.entry = x.base, x.entry
        self.data = bytes(x.data)
        self.sections = [(s.name, s.va, s.vsize, s.raw, s.rsize,
                          bool(s.flags & 0x04)) for s in x.sections]

    def _load_pe(self):
        d = self.data
        pe = u32(d, 0x3C)
        if d[pe:pe + 4] != b'PE\0\0':
            raise SystemExit('bad PE signature')
        nsec, optsz = u16(d, pe + 6), u16(d, pe + 0x14)
        opt = pe + 0x18
        self.base = u32(d, opt + 0x1C)
        self.entry = self.base + u32(d, opt + 0x10)
        self.sections = []
        for i in range(nsec):
            h = opt + optsz + i * 40
            name = d[h:h + 8].rstrip(b'\0').decode('ascii', 'replace')
            self.sections.append((name, self.base + u32(d, h + 12), u32(d, h + 8),
                                  u32(d, h + 20), u32(d, h + 16),
                                  bool(u32(d, h + 36) & 0x20000020)))

    def va_to_off(self, va):
        for _, sva, vsize, raw, rsize, _ in self.sections:
            if sva <= va < sva + vsize:
                off = raw + (va - sva)
                return off if off < raw + rsize else None
        return None

    def read(self, va, n):
        off = self.va_to_off(va)
        return self.data[off:off + n] if off is not None else b''

    def analysis_range(self, want):
        """(va, size, bytes) for the section holding game code."""
        for name, sva, vsize, raw, rsize, _ in self.sections:
            if name == want:
                return sva, min(vsize, rsize), self.data[raw:raw + min(vsize, rsize)]
        raise SystemExit(f'{self.tag}: no section named {want}')


def schema(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS image(tag TEXT PRIMARY KEY, path TEXT, base INT,
        entry INT, section TEXT, code_va INT, code_size INT);
    CREATE TABLE IF NOT EXISTS func(tag TEXT, va INT, end INT, insns INT,
        blocks INT, source TEXT, PRIMARY KEY(tag, va));
    CREATE TABLE IF NOT EXISTS edge(tag TEXT, src INT, dst INT, site INT, kind TEXT);
    CREATE TABLE IF NOT EXISTS str(tag TEXT, va INT, text TEXT, PRIMARY KEY(tag, va));
    CREATE TABLE IF NOT EXISTS strref(tag TEXT, str_va INT, site INT, func INT);
    CREATE TABLE IF NOT EXISTS match(xbe INT PRIMARY KEY, pc INT, score REAL,
        method TEXT, evidence TEXT);
    CREATE TABLE IF NOT EXISTS vtab(tag TEXT, va INT, count INT, PRIMARY KEY(tag, va));
    CREATE TABLE IF NOT EXISTS vtab_entry(tag TEXT, vtab INT, idx INT, target INT);
    CREATE TABLE IF NOT EXISTS vmatch(xbe INT PRIMARY KEY, pc INT, score REAL);
    CREATE INDEX IF NOT EXISTS vte_t ON vtab_entry(tag, target);
    CREATE INDEX IF NOT EXISTS vte_v ON vtab_entry(tag, vtab);
    CREATE INDEX IF NOT EXISTS edge_dst ON edge(tag, dst);
    CREATE INDEX IF NOT EXISTS edge_src ON edge(tag, src);
    CREATE INDEX IF NOT EXISTS strref_fn ON strref(tag, func);
    CREATE INDEX IF NOT EXISTS strref_sv ON strref(tag, str_va);
    """)


def scan_targets(code, code_va, lo, hi):
    """Every rel32 call/jmp site and target, by byte scan.

    Descent reaches about 78% of the section, so its call edges are incomplete -
    it found one of RunFunction's three call sites. This scan is the ground truth
    the edge table is built from; false positives are filtered later by requiring
    both ends to be known functions.
    """
    calls, jumps = [], []
    n = len(code)
    for i in range(n - 4):
        op = code[i]
        if op != 0xE8 and op != 0xE9:
            continue
        disp = struct.unpack_from('<i', code, i + 1)[0]
        site = code_va + i
        target = site + 5 + disp
        if lo <= target < hi:
            (calls if op == 0xE8 else jumps).append((site, target))
    return calls, jumps


def scan_pointers(img, lo, hi):
    """Aligned code pointers in non-code sections: vtables and dispatch tables."""
    out = set()
    for name, sva, vsize, raw, rsize, is_code in img.sections:
        if is_code and name not in ('.rdata', '.data'):
            continue
        blob = img.data[raw:raw + min(vsize, rsize)]
        for i in range(0, len(blob) - 4, 4):
            v = struct.unpack_from('<I', blob, i)[0]
            if lo <= v < hi:
                out.add(v)
    return out


def scan_vtables(img, lo, hi, minrun=3):
    """Runs of consecutive aligned code pointers. Most are C++ vtables.

    Direct calls reach only a fraction of the image; almost everything else is a
    virtual method, reachable only through one of these.
    """
    out = []
    for name, sva, vsize, raw, rsize, is_code in img.sections:
        if is_code and name not in ('.rdata', '.data'):
            continue
        blob = img.data[raw:raw + min(vsize, rsize)]
        run, start = [], 0
        for i in range(0, len(blob) - 4, 4):
            v = struct.unpack_from('<I', blob, i)[0]
            if lo <= v < hi:
                if not run:
                    start = sva + i
                run.append(v)
            else:
                if len(run) >= minrun:
                    out.append((start, run))
                run = []
        if len(run) >= minrun:
            out.append((start, run))
    return out


def preceded_ok(code, code_va, va):
    """True when va follows a terminator or padding, as a function start should.

    98.1% of direct-call targets pass, so a pointer that fails is most likely an
    integer that happened to land in the code range.
    """
    i = va - code_va
    if i < 1:
        return True
    if code[i - 1] in (0xC3, 0xCB, 0xCC, 0x90):
        return True
    if i >= 3 and code[i - 3] == 0xC2:
        return True
    if i >= 5 and code[i - 5] == 0xE9:
        return True
    return i >= 2 and code[i - 2] == 0xEB


def descend(md, code, code_va, start, lo, hi, entries):
    """Walk one function. Returns (end, insns, blocks, edges) or None."""
    work, seen, edges = [start], {}, []
    end, blocks = start, 0
    while work:
        va = work.pop()
        if va in seen or not (lo <= va < hi):
            continue
        blocks += 1
        off = va - code_va
        for ins in md.disasm(code[off:off + 4096], va):
            if ins.address in seen:
                break
            seen[ins.address] = ins.size
            end = max(end, ins.address + ins.size)
            if end - start > MAX_FUNC:
                return None
            m = ins.mnemonic
            if m in STOP:
                break
            if m == 'call':
                op = ins.operands[0] if ins.operands else None
                if op is not None and op.type == CS_OP_IMM:
                    edges.append((ins.address, op.imm, 'call'))
                break_after = False
            elif m == 'jmp':
                op = ins.operands[0] if ins.operands else None
                if op is not None and op.type == CS_OP_IMM:
                    t = op.imm
                    # A jump into another function's entry is a tail call, not flow.
                    if t in entries and t != start:
                        edges.append((ins.address, t, 'tail'))
                    elif lo <= t < hi:
                        work.append(t)
                break
            elif m.startswith('j'):
                op = ins.operands[0] if ins.operands else None
                if op is not None and op.type == CS_OP_IMM and lo <= op.imm < hi:
                    work.append(op.imm)
            else:
                continue
        else:
            continue
    # A conditional jump can land far outside the body, so the highest address
    # reached is not the end. Take the contiguous extent instead, tolerating the
    # padding and jump tables that sit between blocks.
    end = start
    for va in sorted(seen):
        if va > end + BODY_GAP:
            break
        end = max(end, va + seen[va])
    return end, len(seen), blocks, edges


def find_strings(img, minlen=6):
    out = {}
    pat = re.compile(rb'[\x20-\x7e]{%d,}' % minlen)
    for name, sva, vsize, raw, rsize, _ in img.sections:
        blob = img.data[raw:raw + min(vsize, rsize)]
        for m in pat.finditer(blob):
            out[sva + m.start()] = m.group().decode('ascii')
    return out


def cmd_build(a):
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    schema(db)
    img = Image(a.image, a.tag)
    code_va, code_size, code = img.analysis_range(a.section)
    lo, hi = code_va, code_va + code_size
    print(f'{a.tag}: {a.section} {code_size} bytes at 0x{code_va:08X}')

    calls, jumps = scan_targets(code, code_va, lo, hi)
    call_targets = {t for _, t in calls}
    ptrs = scan_pointers(img, lo, hi)
    kept = {v for v in ptrs if v in call_targets or preceded_ok(code, code_va, v)}
    print(f'  call sites {len(calls)} to {len(call_targets)} targets  '
          f'jmp sites {len(jumps)}  code pointers {len(ptrs)} -> {len(kept)} valid')

    entries = call_targets | kept
    if lo <= img.entry < hi:
        entries.add(img.entry)
    source = {}
    for va in entries:
        source[va] = 'call' if va in call_targets else 'ptr'
    source[img.entry] = 'entry' if lo <= img.entry < hi else source.get(img.entry, 'ptr')

    md = Cs(CS_ARCH_X86, CS_MODE_32)
    md.detail = True
    funcs, edges = [], []
    for i, va in enumerate(sorted(entries)):
        r = descend(md, code, code_va, va, lo, hi, entries)
        if r is None:
            continue
        end, insns, blocks, fedges = r
        if insns == 0:
            continue
        funcs.append((a.tag, va, end, insns, blocks, source.get(va, 'ptr')))
        for site, dst, kind in fedges:
            if kind == 'tail':
                edges.append((a.tag, va, dst, site, kind))
        if a.progress and i % 2000 == 0:
            print(f'  ...{i}/{len(entries)}')

    # Descent follows jumps past the function it started in. An entry point is the
    # hard bound: no function extends into the next one.
    order = sorted(entries)
    funcs = [(t, va, min(end, order[bisect.bisect_right(order, va)])
              if bisect.bisect_right(order, va) < len(order) else end,
              insns, blocks, src) for t, va, end, insns, blocks, src in funcs]
    strings = find_strings(img)
    bounds = sorted((f[1], f[2]) for f in funcs)
    starts = [b[0] for b in bounds]

    # Two different notions of extent. `end` is the contiguous body, which is what
    # a size means. Attribution uses the region from one entry to the next: a call
    # site or string reference past the contiguous end still belongs to that
    # function, and requiring containment drops it instead.
    def owner(va):
        i = bisect.bisect_right(starts, va) - 1
        return bounds[i][0] if i >= 0 else None

    # Call edges come from the scan, not from descent, so that a call site in a
    # region descent never reached is still recorded. Both ends must be known
    # functions, which discards 0xE8 bytes that were never an opcode.
    known = {f[1] for f in funcs}
    seen_call = set()
    for site, target in calls:
        src = owner(site)
        if src is None or target not in known or site in seen_call:
            continue
        seen_call.add(site)
        edges.append((a.tag, src, target, site, 'call'))
    print(f'  functions {len(funcs)}  edges {len(edges)}')

    srefs = []
    for sva in strings:
        needle = struct.pack('<I', sva)
        pos = code.find(needle)
        while pos != -1:
            site = code_va + pos
            srefs.append((a.tag, sva, site, owner(site)))
            pos = code.find(needle, pos + 1)
    print(f'  strings {len(strings)}  references {len(srefs)}')

    vtabs = scan_vtables(img, lo, hi)
    print(f'  vtables {len(vtabs)}, {sum(len(v) for _, v in vtabs)} entries')

    for t in ('func', 'edge', 'str', 'strref', 'vtab', 'vtab_entry'):
        db.execute(f'DELETE FROM {t} WHERE tag=?', (a.tag,))
    db.executemany('INSERT INTO vtab VALUES(?,?,?)',
                   [(a.tag, va, len(v)) for va, v in vtabs])
    db.executemany('INSERT INTO vtab_entry VALUES(?,?,?,?)',
                   [(a.tag, va, i, t) for va, v in vtabs for i, t in enumerate(v)])
    db.execute('INSERT OR REPLACE INTO image VALUES(?,?,?,?,?,?,?)',
               (a.tag, img.path, img.base, img.entry, a.section, code_va, code_size))
    db.executemany('INSERT INTO func VALUES(?,?,?,?,?,?)', funcs)
    db.executemany('INSERT INTO edge VALUES(?,?,?,?,?)', edges)
    db.executemany('INSERT INTO str VALUES(?,?,?)',
                   [(a.tag, va, s) for va, s in strings.items()])
    db.executemany('INSERT INTO strref VALUES(?,?,?,?)', srefs)
    db.commit()


def cmd_match(a):
    db = sqlite3.connect(DB_PATH)
    schema(db)
    # A string is usable as an anchor when it is unique in both images and
    # referenced from exactly one function on each side.
    def index(tag):
        by_text = {}
        for va, text in db.execute('SELECT va, text FROM str WHERE tag=?', (tag,)):
            by_text.setdefault(text, []).append(va)
        refs = {}
        for sva, fn in db.execute(
                'SELECT str_va, func FROM strref WHERE tag=? AND func IS NOT NULL', (tag,)):
            refs.setdefault(sva, set()).add(fn)
        return by_text, refs

    xt, xr = index('xbe')
    pt, pr = index('pc')
    pairs, votes = {}, {}
    for text, xvas in xt.items():
        pvas = pt.get(text)
        if not pvas or len(xvas) != 1 or len(pvas) != 1:
            continue
        xf, pf = xr.get(xvas[0]), pr.get(pvas[0])
        if not xf or not pf:
            continue
        for x in xf:
            for p in pf:
                votes.setdefault(x, {}).setdefault(p, []).append(text)
    for x, cands in votes.items():
        best = max(cands.items(), key=lambda kv: len(kv[1]))
        total = sum(len(v) for v in cands.values())
        score = len(best[1]) / total
        if score >= a.min_score:
            pairs[x] = (best[0], score, best[1][:3])
    # Refuse a pc function claimed by several xbe functions unless it wins clearly.
    claim = {}
    for x, (p, score, ev) in pairs.items():
        claim.setdefault(p, []).append((score, x))
    kept = []
    for p, lst in claim.items():
        lst.sort(reverse=True)
        if len(lst) == 1 or lst[0][0] > lst[1][0]:
            x = lst[0][1]
            kept.append((x, p, pairs[x][1], 'string', json.dumps(pairs[x][2])))
    # Seeding restarts the whole match, so propagated rows must not survive it.
    db.execute('DELETE FROM match')
    db.execute('DELETE FROM vmatch')
    db.executemany('INSERT OR REPLACE INTO match VALUES(?,?,?,?,?)', kept)
    db.commit()
    nx = db.execute("SELECT COUNT(*) FROM func WHERE tag='xbe'").fetchone()[0]
    print(f'string anchors: {len(kept)} pairs of {nx} xbe functions '
          f'({100.0 * len(kept) / nx:.1f}%)')


def align_calls(a_, b_, pairs):
    """Pair unmatched callees between two call sequences.

    Already-matched callees anchor the alignment; an equal-length gap between two
    anchors pairs positionally. Unequal gaps mean one build inlined something, and
    those are skipped rather than guessed through.
    """
    left = [pairs[c] if c in pairs else ~i for i, c in enumerate(a_)]
    blocks = SequenceMatcher(None, left, b_, autojunk=False).get_matching_blocks()
    out = []
    pi = pj = 0
    for i, j, n in blocks:
        if i - pi == j - pj:
            for k in range(i - pi):
                out.append((a_[pi + k], b_[pj + k]))
        pi, pj = i + n, j + n
    return out


def cmd_propagate(a):
    """Extend the seed matches through the call graph until it stops growing."""
    db = sqlite3.connect(DB_PATH)
    schema(db)

    def sites(tag):
        d = {}
        for src, dst, site in db.execute(
                'SELECT src, dst, site FROM edge WHERE tag=? AND kind=? ORDER BY site',
                (tag, 'call')):
            d.setdefault(src, []).append(dst)
        return d

    def callers(tag):
        d = {}
        for src, dst in db.execute(
                'SELECT DISTINCT src, dst FROM edge WHERE tag=? AND kind=?', (tag, 'call')):
            d.setdefault(dst, set()).add(src)
        return d

    def vtables(tag):
        by_va, member = {}, {}
        for va, idx, target in db.execute(
                'SELECT vtab, idx, target FROM vtab_entry WHERE tag=? ORDER BY vtab, idx',
                (tag,)):
            by_va.setdefault(va, []).append(target)
            member.setdefault(target, []).append((va, idx))
        return by_va, member

    xs, ps = sites('xbe'), sites('pc')
    xcal, pcal = callers('xbe'), callers('pc')
    xvt, xmem = vtables('xbe')
    pvt, pmem = vtables('pc')
    # Call and vtable targets include addresses descent rejected; they are not
    # functions and must not enter the match.
    known = {t: {va for (va,) in db.execute('SELECT va FROM func WHERE tag=?', (t,))}
             for t in ('xbe', 'pc')}
    pairs = {x: p for x, p in db.execute('SELECT xbe, pc FROM match')}
    claimed = set(pairs.values())
    seeds = len(pairs)
    vpairs = {}
    gen = 0
    while True:
        gen += 1
        votes = {}
        for x, p in pairs.items():
            a_, b_ = xs.get(x, []), ps.get(p, [])
            if a_ and b_:
                for cx, cp in align_calls(a_, b_, pairs):
                    if cx not in pairs and cp not in claimed:
                        votes.setdefault(cx, {}).setdefault(cp, 0)
                        votes[cx][cp] += 1
            ca, cb = xcal.get(x, set()), pcal.get(p, set())
            if len(ca) == 1 and len(cb) == 1:
                cx, cp = next(iter(ca)), next(iter(cb))
                if cx not in pairs and cp not in claimed:
                    votes.setdefault(cx, {}).setdefault(cp, 0)
                    votes[cx][cp] += 1
        # Adjacent vtables in .rdata run together, so table boundaries differ between
        # the images. Align on the offset between matched slots instead: two anchors
        # agreeing on one shift fixes the whole overlap.
        shifts = {}
        for x, p in pairs.items():
            for vx, ix in xmem.get(x, []):
                for vp, ip in pmem.get(p, []):
                    shifts.setdefault((vx, vp), {}).setdefault(ip - ix, 0)
                    shifts[(vx, vp)][ip - ix] += 1
        for (vx, vp), cands in shifts.items():
            shift, n = max(cands.items(), key=lambda kv: kv[1])
            if n < 2 or n < sum(cands.values()) * 0.8:
                continue
            vpairs[(vx, vp)] = shift
            a_, b_ = xvt[vx], pvt[vp]
            for ix, cx in enumerate(a_):
                ip = ix + shift
                if not (0 <= ip < len(b_)):
                    continue
                cp = b_[ip]
                if cx not in pairs and cp not in claimed:
                    votes.setdefault(cx, {}).setdefault(cp, 0)
                    votes[cx][cp] += 2

        fresh = {}
        for cx, cands in votes.items():
            if len(cands) != 1 or cx not in known['xbe']:
                continue
            cp, n = next(iter(cands.items()))
            if cp in known['pc']:
                fresh[cx] = (cp, n)
        # Reject any pc function two xbe functions both want.
        back = {}
        for cx, (cp, n) in fresh.items():
            back.setdefault(cp, []).append(cx)
        added = 0
        rows = []
        for cp, lst in back.items():
            if len(lst) != 1:
                continue
            cx = lst[0]
            pairs[cx] = cp
            claimed.add(cp)
            rows.append((cx, cp, min(1.0, fresh[cx][1] / 2.0), f'propagate:{gen}', ''))
            added += 1
        db.executemany('INSERT OR REPLACE INTO match VALUES(?,?,?,?,?)', rows)
        db.commit()
        print(f'  generation {gen}: +{added} (total {len(pairs)}, '
              f'{len(vpairs)} vtables)')
        if added == 0 or gen >= a.max_gen:
            break
    db.execute('DELETE FROM vmatch')
    db.executemany('INSERT OR REPLACE INTO vmatch VALUES(?,?,?)',
                   [(vx, vp, float(s)) for (vx, vp), s in vpairs.items()])
    db.commit()
    nx = db.execute("SELECT COUNT(*) FROM func WHERE tag='xbe'").fetchone()[0]
    print(f'matched {len(pairs)} of {nx} xbe functions ({100.0 * len(pairs) / nx:.1f}%) '
          f'from {seeds} seeds')


def load_curated():
    if CURATED.exists():
        return json.loads(CURATED.read_text())
    return {'records': []}


def save_curated(cur):
    # Committed file: keep LF so a run on Windows does not rewrite every line.
    CURATED.parent.mkdir(parents=True, exist_ok=True)
    CURATED.write_text(json.dumps(cur, indent=2) + '\n', newline='\n')


SEED_NOTES = {
    'find_run_function': ('Script::RunFunction', 'function',
                          'three call rel32 sites redirected by script-ext'),
    'find_command_table': ('Script::CommandTable', 'data',
                           'six opcode bounds widened by script-ext'),
    'find_script_ip_restore_site': ('Script::RunFunction.restoreIpCall', 'site',
                                    'caller restores Script::Decode.ip from ESI'),
    'find_ref_load': ('Reference::Load.restamp', 'site',
                      'mcp-1 landing instruction; restamps with the reading index'),
    'find_ref_skip': ('Reference::Load.skip', 'site', 'mcp-1 drop branch'),
    'find_mcp97_scan': ('ScriptData::InitScan.lengthCase', 'site',
                        'mcp-97 length-prefixed operand advance'),
    'find_mcp154_load': ('ScriptData::Load.alloc', 'site', 'mcp-154 SCDT allocation'),
    'find_mcp154_reload': ('ScriptData::Reload.alloc', 'site', 'mcp-154 SCDT allocation'),
    'find_mcp102_actn': ('Reference::SetActionFlags', 'function',
                         'mcp-102 restores the default active bit'),
    'find_mcp123_add': ('PlaceItem.addReference', 'site',
                        'mcp-123 marks the destination cell changed'),
    'find_mcp125_collision': ('Position.collisionRegistration', 'site',
                              'mcp-125 registers collision after a scripted move'),
    'find_diagnostics_update': ('Diagnostics::Update', 'function',
                                'holds the console input gate'),
    'find_console_gate': ('Diagnostics::ConsoleGate', 'site',
                          'input gate replaced by --apply console'),
}

SEED_MANUAL = [
    ('0x001933E0', 'Ini::ReadKey', 'function', 'verified',
     'engine ini reader used by ConsoleCombo; __cdecl'),
    ('0x0012A5F0', 'Reference::GetActionFlags', 'function', 'verified',
     'paired with SetActionFlags in the mcp-102 probe'),
    ('0x00195F70', 'Input::Action', 'function', 'verified', 'console payload calls it'),
    ('0x0014B3C0', 'Script::CompileAndRun', 'function', 'verified',
     'runs a typed console command'),
    ('0x00139C70', 'ScriptData::InitScan.loop', 'site', 'verified',
     'mcp-97 scan loop head'),
    ('0x00139CA9', 'ScriptData::InitScan.landing', 'site', 'verified',
     'shared landing site spliced by the mcp-97 probe'),
    ('0x0019D850', 'Console::FieldRepaint?', 'function', 'matched',
     'candidate for the history-seeded repaint defect; two attempts reverted'),
    ('0x00161A20', 'Console::FieldDraw?', 'site', 'matched',
     'candidate for the history-seeded repaint defect'),
]


def cmd_seed(a):
    """Import addresses the patcher resolves by signature, plus documented sites."""
    import tes3x_patch as P
    x = Xbe(open(a.image, 'rb').read())
    cur = load_curated()
    by_va = {int(r['va'], 16): r for r in cur['records']}

    def put(va, name, kind, confidence, provenance):
        rec = by_va.setdefault(va, {'va': f'0x{va:08X}'})
        rec.update(name=name, kind=kind, confidence=confidence, provenance=provenance)

    for fn_name, (name, kind, why) in SEED_NOTES.items():
        fn = getattr(P, fn_name, None)
        if fn is None:
            continue
        try:
            r = fn(x)
        except Exception as e:
            print(f'  {fn_name}: unresolved ({e})')
            continue
        for va in (r if isinstance(r, (list, tuple)) else [r]):
            if isinstance(va, int):
                put(va, name, kind, 'verified', f'{fn_name}; {why}')
    try:
        decode, script_ip, script_opcode = P.find_script_decode_state(x)
        put(decode, 'Script::Decode', 'function', 'verified',
            'find_script_decode_state; mwse-legacy redirects its fixup caller')
        put(script_ip, 'Script::Decode.ip', 'data', 'verified',
            'find_script_decode_state; live decoder cursor used by mwse-legacy')
        put(script_opcode, 'Script::Decode.opcode', 'data', 'verified',
            'find_script_decode_state; live decoded opcode used by mwse-legacy')
    except Exception as e:
        print(f'  script decode state: unresolved ({e})')
    for va, name, kind, conf, why in SEED_MANUAL:
        put(int(va, 16), name, kind, conf, why)

    cur['records'] = sorted(by_va.values(), key=lambda r: int(r['va'], 16))
    CURATED.parent.mkdir(parents=True, exist_ok=True)
    save_curated(cur)
    print(f'curated records: {len(cur["records"])}')


MWSE_PATTERNS = [
    r'\b(?:const|static)?\s*auto\s+([A-Za-z_]\w*)\s*=\s*reinterpret_cast<[^;]*?>'
    r'\s*\(\s*(0x[0-9A-Fa-f]{5,8})\s*\)',
    r'#define\s+([A-Za-z_]\w*)\s+(0x[0-9A-Fa-f]{5,8})\b',
    r'\b(?:const|static)\s+(?:DWORD|UINT|uint32_t|unsigned\s+int)\s+([A-Za-z_]\w*)'
    r'\s*=\s*(0x[0-9A-Fa-f]{5,8})',
]


def cmd_names(a):
    """Carry MWSE's names for Morrowind.exe 1.6.1820 across to matched xbe functions.

    These are another project's reverse engineering of a different binary, reached
    through an inferred match, so they land at `matched` and never overwrite a
    verified name.
    """
    db = sqlite3.connect(DB_PATH)
    schema(db)
    row = db.execute("SELECT code_va, code_size FROM image WHERE tag='pc'").fetchone()
    if not row:
        raise SystemExit('build the pc image first')
    lo, hi = row[0], row[0] + row[1]

    pats = [re.compile(p) for p in MWSE_PATTERNS]
    found = {}
    files = 0
    for dp, _, fns in os.walk(a.source):
        for fn in fns:
            if not fn.endswith(('.h', '.hpp', '.cpp')):
                continue
            files += 1
            text = open(os.path.join(dp, fn), encoding='utf-8', errors='ignore').read()
            for p in pats:
                for m in p.finditer(text):
                    va = int(m.group(2), 16)
                    if lo <= va < hi:
                        found.setdefault(va, set()).add(m.group(1))
    # The declaration patterns also catch local variables and version-guarded
    # macros, which show up as one name on several addresses. Those are ambiguous,
    # so drop the name rather than pick an address for it.
    seen = {}
    for va, raw in found.items():
        for n in raw:
            seen.setdefault(n, set()).add(va)
    ambiguous = {n for n, vas in seen.items() if len(vas) > 1}
    found = {va: {n for n in raw if n not in ambiguous} for va, raw in found.items()}
    found = {va: raw for va, raw in found.items() if raw}
    print(f'{files} files, {len(found)} named addresses in pc .text '
          f'({len(ambiguous)} ambiguous names dropped)')

    starts = {va for (va,) in db.execute("SELECT va FROM func WHERE tag='pc'")}
    match = {p: x for x, p in db.execute('SELECT xbe, pc FROM match')}
    cur = load_curated()
    # Re-importing must not leave names behind from a previous match run.
    cur['records'] = [r for r in cur['records']
                      if not str(r.get('provenance', '')).startswith('mwse:')]
    by_va = {int(r['va'], 16): r for r in cur['records']}
    added = kept = skipped = 0
    for pva, raw in sorted(found.items()):
        if pva not in starts or pva not in match:
            skipped += 1
            continue
        xva = match[pva]
        full = sorted(raw)[0]
        name = full[5:] if full.startswith('TES3_') else full
        rec = by_va.get(xva)
        if rec and rec.get('confidence') == 'verified':
            # Disagreement here is worth seeing, so record it beside the name.
            rec['mwse'] = full
            kept += 1
            continue
        if rec is None:
            rec = {'va': f'0x{xva:08X}'}
            by_va[xva] = rec
        rec.update(name=name.replace('_', '::', 1), kind='function',
                   confidence='matched', provenance=f'mwse:{full}',
                   pc_va=f'0x{pva:08X}')
        added += 1
    cur['records'] = sorted(by_va.values(), key=lambda r: int(r['va'], 16))
    save_curated(cur)
    print(f'named {added} xbe functions, {kept} verified records annotated, '
          f'{skipped} not a matched function start')
    print(f'curated records: {len(cur["records"])}')


def cmd_annotate(a):
    cur = load_curated()
    va = int(a.va, 16)
    rec = next((r for r in cur['records'] if int(r['va'], 16) == va), None)
    if rec is None:
        rec = {'va': f'0x{va:08X}'}
        cur['records'].append(rec)
    rec['name'] = a.name
    rec['confidence'] = a.confidence
    if a.provenance:
        rec['provenance'] = a.provenance
    if a.note:
        rec['note'] = a.note
    if a.pc_va:
        rec['pc_va'] = f'0x{int(a.pc_va, 16):08X}'
    cur['records'].sort(key=lambda r: int(r['va'], 16))
    save_curated(cur)
    print(f"0x{va:08X} = {a.name} ({a.confidence})")


def cmd_lookup(a):
    db = sqlite3.connect(DB_PATH)
    schema(db)
    names = {int(r['va'], 16): r for r in load_curated()['records']}
    va = int(a.va, 16)
    row = db.execute('SELECT va, end, insns, blocks, source FROM func '
                     'WHERE tag=? AND va<=? AND end>? ', (a.tag, va, va)).fetchone()
    if not row:
        print(f'0x{va:08X}: no function')
        return
    fva, end, insns, blocks, source = row
    rec = names.get(fva)
    label = f"{rec['name']} [{rec['confidence']}]" if rec else '(unnamed)'
    print(f'0x{fva:08X}-0x{end:08X}  {label}')
    print(f'  {insns} insns, {blocks} blocks, found by {source}, '
          f'offset +0x{va - fva:X}')
    if rec:
        for k in ('provenance', 'pc_va', 'note'):
            if rec.get(k):
                print(f'  {k}: {rec[k]}')
    m = db.execute('SELECT pc, score, evidence FROM match WHERE xbe=?', (fva,)).fetchone()
    if m:
        print(f'  matches pc 0x{m[0]:08X} (score {m[1]:.2f}) via {m[2]}')
    callers = db.execute('SELECT COUNT(DISTINCT src) FROM edge WHERE tag=? AND dst=?',
                         (a.tag, fva)).fetchone()[0]
    callees = db.execute('SELECT COUNT(DISTINCT dst) FROM edge WHERE tag=? AND src=?',
                         (a.tag, fva)).fetchone()[0]
    print(f'  {callers} callers, {callees} callees')
    for sva, text in db.execute(
            'SELECT s.va, s.text FROM strref r JOIN str s ON s.tag=r.tag AND s.va=r.str_va '
            'WHERE r.tag=? AND r.func=? LIMIT 8', (a.tag, fva)):
        print(f'  string 0x{sva:08X} {text[:60]!r}')


def cmd_disasm(a):
    """Disassemble a range, annotating call targets and string references."""
    db = sqlite3.connect(DB_PATH)
    schema(db)
    row = db.execute('SELECT path, section FROM image WHERE tag=?', (a.tag,)).fetchone()
    if not row:
        raise SystemExit(f'build the {a.tag} image first')
    img = Image(row[0], a.tag)
    code_va, _, code = img.analysis_range(row[1])
    names = {int(r['va'], 16): r['name'] for r in load_curated()['records']}
    strings = {va: t for va, t in db.execute('SELECT va, text FROM str WHERE tag=?', (a.tag,))}

    va = int(a.at, 16)
    n = int(a.len, 16) if a.len.startswith('0x') else int(a.len)
    off = va - code_va
    if not 0 <= off < len(code):
        raise SystemExit(f'0x{va:08X} is outside {row[1]}')
    md = Cs(CS_ARCH_X86, CS_MODE_32)
    md.detail = True
    for ins in md.disasm(code[off:off + n], va):
        note = ''
        for op in ins.operands:
            t = op.imm if op.type == CS_OP_IMM else (
                op.mem.disp if op.type == X86_OP_MEM and op.mem.base == 0 else None)
            if t is None:
                continue
            if t in names:
                note = f'  ; {names[t]}'
            elif t in strings:
                note = f'  ; {strings[t][:56]!r}'
            elif ins.mnemonic == 'call':
                note = '  ; sub_%08X' % t
        print(f'  0x{ins.address:08X}  {ins.mnemonic:<7} {ins.op_str}{note}')


def cmd_callers(a):
    db = sqlite3.connect(DB_PATH)
    schema(db)
    names = {int(r['va'], 16): r for r in load_curated()['records']}
    va = int(a.va, 16)
    col, other = ('src', 'dst') if a.callees else ('dst', 'src')
    rows = db.execute(f'SELECT DISTINCT {other}, kind FROM edge WHERE tag=? AND {col}=?',
                      (a.tag, va))
    for target, kind in sorted(rows):
        rec = names.get(target)
        print(f'  0x{target:08X} {kind:5} {rec["name"] if rec else ""}')


def ghidra_names(db, tag):
    """Curated names for one image, and a stamp that changes with them."""
    recs = load_curated()['records']
    if tag == 'xbe':
        names = {r['va']: r['name'] for r in recs if r.get('kind', 'function') != 'site'}
    else:
        by_xbe = {int(r['va'], 16): r['name'] for r in recs
                  if r.get('kind', 'function') == 'function'}
        names = {f'0x{pc:08X}': by_xbe[x] for x, pc in db.execute('SELECT xbe, pc FROM match')
                 if x in by_xbe}
        names.update({r['pc_va']: r['name'] for r in recs if r.get('pc_va')})
    return str(CURATED.stat().st_mtime_ns), names


def ghidra_client(db, tags):
    import tes3x_ghidra
    cl = tes3x_ghidra.Client()
    for tag in tags:
        cl.sync_names(tag, *ghidra_names(db, tag))
    return cl


def pc_counterpart(db, va):
    m = db.execute('SELECT pc FROM match WHERE xbe=?', (va,)).fetchone()
    if m:
        return m[0]
    rec = next((r for r in load_curated()['records'] if int(r['va'], 16) == va), None)
    return int(rec['pc_va'], 16) if rec and rec.get('pc_va') else None


def cmd_ghidra_setup(a):
    import tes3x_ghidra
    db = sqlite3.connect(DB_PATH)
    schema(db)
    rows = db.execute('SELECT tag, path FROM image WHERE tag IN (%s)' %
                      ','.join('?' * len(a.tag)), a.tag).fetchall()
    if not rows:
        raise SystemExit('build the images first (`tes3x_sym.py build IMAGE --tag ...`)')
    tes3x_ghidra.setup(rows)


def cmd_ghidra_stop(a):
    import tes3x_ghidra
    print('stopped' if tes3x_ghidra.stop() else 'not running')


def cmd_decompile(a):
    db = sqlite3.connect(DB_PATH)
    schema(db)
    cl = ghidra_client(db, [a.tag] + (['pc'] if a.pc else []))
    for v in a.va:
        res = cl.call('decompile', tag=a.tag, va=v, timeout=a.timeout)
        print(f"// {a.tag} 0x{res['entry']:08X} {res['name']}")
        print(res['c'].strip() + '\n')
        if a.pc and a.tag == 'xbe':
            pc = pc_counterpart(db, res['entry'])
            if pc is None:
                print('// pc: no matched counterpart\n')
                continue
            res = cl.call('decompile', tag='pc', va=f'0x{pc:08X}', timeout=a.timeout)
            print(f"// pc 0x{res['entry']:08X} {res['name']}")
            print(res['c'].strip() + '\n')
    cl.close()


def cmd_refs(a):
    db = sqlite3.connect(DB_PATH)
    schema(db)
    cl = ghidra_client(db, [a.tag])
    refs = cl.call('refs', tag=a.tag, va=a.va)['refs']
    cl.close()
    for r in sorted(refs, key=lambda r: r['from']):
        fn = f"0x{r['func']:08X} {r['name']}" if 'func' in r else ''
        print(f"  0x{r['from']:08X} {r['type']:<16} {r.get('insn', ''):<36} {fn}")


def cmd_stats(a):
    db = sqlite3.connect(DB_PATH)
    schema(db)
    for tag, path, base, entry, sec, cva, csize in db.execute('SELECT * FROM image'):
        n = db.execute('SELECT COUNT(*) FROM func WHERE tag=?', (tag,)).fetchone()[0]
        cov = db.execute('SELECT SUM(end-va) FROM func WHERE tag=?', (tag,)).fetchone()[0] or 0
        e = db.execute('SELECT COUNT(*) FROM edge WHERE tag=?', (tag,)).fetchone()[0]
        s = db.execute('SELECT COUNT(*) FROM str WHERE tag=?', (tag,)).fetchone()[0]
        print(f'{tag:4} {os.path.basename(path)}')
        print(f'     {sec} 0x{cva:08X} +0x{csize:X}   {n} functions, {e} edges, {s} strings')
        print(f'     span {cov} bytes ({100.0 * cov / csize:.1f}% of section)')
    m = db.execute('SELECT COUNT(*) FROM match').fetchone()[0]
    named = len(load_curated()['records'])
    print(f'matched {m}, curated names {named}')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest='cmd', required=True)

    b = sub.add_parser('build', help='inventory one image')
    b.add_argument('image')
    b.add_argument('--tag', required=True, choices=['xbe', 'pc'])
    b.add_argument('--section', default='.text')
    b.add_argument('--progress', action='store_true')
    b.set_defaults(fn=cmd_build)

    m = sub.add_parser('match', help='pair functions by shared string references')
    m.add_argument('--min-score', type=float, default=0.6)
    m.set_defaults(fn=cmd_match)

    sd = sub.add_parser('seed', help='import addresses the patcher resolves by signature')
    sd.add_argument('image')
    sd.set_defaults(fn=cmd_seed)

    pr = sub.add_parser('propagate', help='extend matches through the call graph')
    pr.add_argument('--max-gen', type=int, default=40)
    pr.set_defaults(fn=cmd_propagate)

    nm = sub.add_parser('names', help='import MWSE names onto matched functions')
    nm.add_argument('source', help='MWSE source tree')
    nm.set_defaults(fn=cmd_names)

    a_ = sub.add_parser('annotate', help='record a name in the curated layer')
    a_.add_argument('va')
    a_.add_argument('name')
    a_.add_argument('--confidence', default='matched',
                    choices=['verified', 'matched', 'guess'])
    a_.add_argument('--provenance', default='')
    a_.add_argument('--note', default='')
    a_.add_argument('--pc-va', default='')
    a_.set_defaults(fn=cmd_annotate)

    lk = sub.add_parser('lookup', help='describe the function containing a VA')
    lk.add_argument('va')
    lk.add_argument('--tag', default='xbe')
    lk.set_defaults(fn=cmd_lookup)

    ds = sub.add_parser('disasm', help='annotated disassembly of a VA range')
    ds.add_argument('at')
    ds.add_argument('--len', default='0x80')
    ds.add_argument('--tag', default='xbe')
    ds.set_defaults(fn=cmd_disasm)

    c = sub.add_parser('callers', help='list callers, or callees with --callees')
    c.add_argument('va')
    c.add_argument('--tag', default='xbe')
    c.add_argument('--callees', action='store_true')
    c.set_defaults(fn=cmd_callers)

    st = sub.add_parser('stats', help='database summary')
    st.set_defaults(fn=cmd_stats)

    gs = sub.add_parser('ghidra-setup', help='import and analyse the images in Ghidra (slow, once)')
    gs.add_argument('--tag', nargs='+', default=['xbe', 'pc'], choices=['xbe', 'pc'])
    gs.set_defaults(fn=cmd_ghidra_setup)

    dc = sub.add_parser('decompile', help='Ghidra pseudo-C of the functions containing VAs')
    dc.add_argument('va', nargs='+')
    dc.add_argument('--tag', default='xbe', choices=['xbe', 'pc'])
    dc.add_argument('--pc', action='store_true', help='also the matched PC function')
    dc.add_argument('--timeout', type=int, default=60)
    dc.set_defaults(fn=cmd_decompile)

    rf = sub.add_parser('refs', help="Ghidra's code and data references to an address")
    rf.add_argument('va')
    rf.add_argument('--tag', default='xbe', choices=['xbe', 'pc'])
    rf.set_defaults(fn=cmd_refs)

    gx = sub.add_parser('ghidra-stop', help='stop the background Ghidra process')
    gx.set_defaults(fn=cmd_ghidra_stop)

    args = ap.parse_args()
    args.fn(args)


if __name__ == '__main__':
    main()
