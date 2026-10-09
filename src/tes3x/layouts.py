"""C++ struct layouts from MWSE's headers, as data types for the Ghidra project.

MWSE describes the PC engine's structs in C++ headers. Compiling them for 32-bit MSVC gives the
exact layout the PC build uses, which the Xbox build shares except where its source differed;
`symbols/structs.json` records the Xbox's own corrections and additions. Needs libclang
(`python -m pip install libclang`), clang, and MSVC and Windows SDK headers.

    tes3x layouts MWSE_TREE [--out build/ghidra/types-mwse.json]
"""
import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

from tes3x.paths import local_config

try:
    import clang.cindex as ci
except ImportError:
    ci = None

OUT = Path.cwd() / 'build' / 'ghidra' / 'types-mwse.json'
HEADER = re.compile(r'(TES3|NI)\w*\.h$')
DEFINES = ['WIN32', 'NOMINMAX', '_CRT_SECURE_NO_WARNINGS', 'SE_IS_MWSE=1', 'SE_TARGETS_MW=1',
           'SE_USE_LUA=0', 'DIRECTINPUT_VERSION=0x0800',
           '_ALLOW_COMPILER_AND_STL_VERSION_MISMATCH']
PRELUDE = """
#include <algorithm>
#include <bit>
#include <functional>
#include <list>
#include <map>
#include <memory>
#include <mutex>
#include <optional>
#include <set>
#include <span>
#include <string>
#include <string_view>
#include <tuple>
#include <unordered_map>
#include <unordered_set>
#include <vector>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <intrin.h>
#include <windows.h>
#include <d3d8.h>
#include <dinput.h>
#include <dsound.h>
#define FMT_HEADER_ONLY
#include <fmt/format.h>
#undef far
#undef near
#undef PlaySound
#define SE_MEMORY_FNADDR_NEW 0x727692
#define SE_MEMORY_FNADDR_DELETE 0x727530
#define SE_MEMORY_FNADDR_MALLOC 0x727738
#define SE_MEMORY_FNADDR_FREE 0x727732
#define SE_MEMORY_FNADDR_REALLOC 0x746288
"""
PRIMS = {} if ci is None else {
    ci.TypeKind.BOOL: 'bool', ci.TypeKind.CHAR_S: 'char', ci.TypeKind.SCHAR: 'char',
    ci.TypeKind.CHAR_U: 'uchar', ci.TypeKind.UCHAR: 'uchar', ci.TypeKind.SHORT: 'short',
    ci.TypeKind.USHORT: 'ushort', ci.TypeKind.INT: 'int', ci.TypeKind.UINT: 'uint',
    ci.TypeKind.LONG: 'int', ci.TypeKind.ULONG: 'uint', ci.TypeKind.LONGLONG: 'longlong',
    ci.TypeKind.ULONGLONG: 'ulonglong', ci.TypeKind.FLOAT: 'float',
    ci.TypeKind.DOUBLE: 'double', ci.TypeKind.LONGDOUBLE: 'double',
    ci.TypeKind.WCHAR: 'wchar16', ci.TypeKind.CHAR16: 'wchar16', ci.TypeKind.VOID: 'void',
}
KEYWORDS = re.compile(r'\b(const|volatile|struct|class|union|enum)\s+')


def local_paths():
    f = local_config()
    return tomllib.loads(f.read_text(encoding='utf-8')).get('paths', {}) if f.exists() else {}


def msvc_includes():
    vc = sorted(glob.glob('C:/Program Files*/Microsoft Visual Studio/*/*/VC/Tools/MSVC/*/include'))
    sdk = sorted(glob.glob('C:/Program Files (x86)/Windows Kits/10/Include/10.*'))
    if not vc or not sdk:
        raise SystemExit('MSVC and Windows SDK headers not found (Visual Studio C++ workload)')
    return [vc[-1]] + [f'{sdk[-1]}/{d}' for d in ('ucrt', 'um', 'shared')]


def clang_exe():
    llvm = local_paths().get('llvm')
    exe = Path(llvm) / ('clang++.exe' if os.name == 'nt' else 'clang++') if llvm \
        else shutil.which('clang++')
    if not exe or not Path(exe).exists():
        raise SystemExit('clang++ not found: set [paths] llvm in tes3x.local.toml')
    return str(exe)


def split_top(s):
    """Split on '::' outside template brackets."""
    parts, depth, cur, i = [], 0, '', 0
    while i < len(s):
        c = s[i]
        if c == '<':
            depth += 1
        elif c == '>':
            depth -= 1
        if depth == 0 and s.startswith('::', i):
            parts.append(cur)
            cur, i = '', i + 2
            continue
        cur += c
        i += 1
    return parts + [cur]


def ident(s):
    return re.sub(r'_+', '_', re.sub(r'[^A-Za-z0-9_]', '_', s.replace('::', '_'))).strip('_')


def type_path(spelling):
    """'NI::IteratedList<TES3::Foo *>' -> '/NI/IteratedList_TES3_Foo'."""
    parts = split_top(KEYWORDS.sub('', spelling).strip())
    return '/' + '/'.join(ident(p) for p in parts)


class Collector:
    def __init__(self):
        self.records, self.enums, self.paths, self.queue = {}, {}, {}, []

    def path_of(self, t, hint):
        key = t.spelling
        if key not in self.paths:
            anon = '(unnamed' in key or '(anonymous' in key
            self.paths[key] = hint if anon else type_path(key)
            self.queue.append(t)
        return self.paths[key]

    def tstr(self, t, hint):
        t = t.get_canonical()
        k = t.kind
        if k in PRIMS:
            return PRIMS[k]
        if k in (ci.TypeKind.POINTER, ci.TypeKind.LVALUEREFERENCE, ci.TypeKind.RVALUEREFERENCE):
            p = t.get_pointee().get_canonical()
            if p.kind in (ci.TypeKind.FUNCTIONPROTO, ci.TypeKind.FUNCTIONNOPROTO):
                return 'void*'
            return self.tstr(p, hint) + '*'
        if k == ci.TypeKind.CONSTANTARRAY:
            return self.tstr(t.element_type, hint) + f'[{t.element_count}]'
        if k == ci.TypeKind.INCOMPLETEARRAY:
            return self.tstr(t.element_type, hint) + '[0]'
        if k in (ci.TypeKind.RECORD, ci.TypeKind.ENUM):
            return self.path_of(t, hint)
        size = t.get_size()
        return f'undefined{size}' if size > 0 else 'void'

    def drain(self):
        while self.queue:
            t = self.queue.pop()
            path = self.paths[t.spelling]
            size = t.get_size()
            if size < 0:
                continue
            if t.kind == ci.TypeKind.ENUM:
                decl = t.get_declaration()
                self.enums[path] = {'size': size, 'values': {
                    c.spelling: c.enum_value for c in decl.get_children()
                    if c.kind == ci.CursorKind.ENUM_CONSTANT_DECL}}
                continue
            decl = t.get_declaration()
            for c in decl.get_children():
                if c.kind == ci.CursorKind.CXX_BASE_SPECIFIER and c.type.get_size() > 0:
                    self.path_of(c.type.get_canonical(), type_path(c.type.spelling))
            fields = []
            for i, f in enumerate(t.get_fields()):
                if f.is_bitfield():
                    continue
                off = f.get_field_offsetof()
                if off < 0:
                    continue
                name = f.spelling or f'anon_{i}'
                fields.append([off // 8, name, self.tstr(f.type, f'{path}_anon_{name}')])
            self.records[path] = {
                'kind': 'union' if decl.kind == ci.CursorKind.UNION_DECL else 'struct',
                'size': size, 'spelling': KEYWORDS.sub('', t.spelling), 'fields': fields}


def roots(tu, tree):
    dirs = tuple(str(tree / d).replace('\\', '/').lower() + '/' for d in ('MWSE', 'SharedSE'))
    stack = [tu.cursor]
    while stack:
        c = stack.pop()
        for ch in c.get_children():
            if ch.kind in (ci.CursorKind.NAMESPACE, ci.CursorKind.STRUCT_DECL,
                           ci.CursorKind.CLASS_DECL, ci.CursorKind.UNION_DECL,
                           ci.CursorKind.ENUM_DECL):
                f = ch.location.file
                if not f or not str(f.name).replace('\\', '/').lower().startswith(dirs):
                    continue
                if ch.kind != ci.CursorKind.NAMESPACE and ch.is_definition() \
                        and not ch.is_anonymous():
                    yield ch.type
                stack.append(ch)


def failed_asserts(tu):
    """MWSE checks its own sizes and offsets; a failure means a layout here is wrong."""
    return [str(d) for d in tu.diagnostics
            if d.severity >= 3 and 'static assertion failed' in d.spelling
            and 'STL' not in d.spelling]


def dump_bases(args, tu_path, records):
    """Base-class and vtable offsets, which libclang does not expose, from clang's dump."""
    probe = tu_path.with_name('probe.cpp')
    lines = [f'#include "{tu_path.name}"']
    for r in records.values():
        if '(unnamed' not in r['spelling'] and '(anonymous' not in r['spelling']:
            lines.append(f'static_assert(sizeof({r["spelling"]}) > 0 || true);')
    probe.write_text('\n'.join(lines) + '\n')
    out = subprocess.run([clang_exe(), '-fsyntax-only', '-ferror-limit=0', '-Xclang',
                          '-fdump-record-layouts', *args, str(probe)],
                         capture_output=True, text=True, errors='replace').stdout
    bases, cur = {}, None
    for line in out.splitlines():
        if '|' not in line:
            continue
        off, rest = line.split('|', 1)
        rest = rest[1:] if rest.startswith(' ') else rest
        depth = (len(rest) - len(rest.lstrip(' '))) // 2
        rest = rest.strip()
        if not off.strip():
            continue
        if depth == 0:
            cur = bases.setdefault(KEYWORDS.sub('', rest.replace(' (empty)', '')), [])
        elif depth == 1 and cur is not None:
            m = re.match(r'(.*) \((primary |virtual )?base\)( \(empty\))?$', rest)
            if m:
                cur.append([int(off), KEYWORDS.sub('', m.group(1))])
            elif rest.endswith('vftable pointer)') or rest.endswith('vbtable pointer)'):
                cur.append([int(off), '*vptr'])
    return bases


def generate(tree, out):
    if ci is None:
        raise SystemExit('needs libclang; run `python -m pip install libclang`')
    tree = Path(tree).resolve()
    work = out.parent / 'mwse-layout'
    work.mkdir(parents=True, exist_ok=True)
    headers = sorted(p for d in ('MWSE', 'SharedSE') for p in (tree / d).glob('*.h')
                     if HEADER.match(p.name))
    tu_path = work / 'all.cpp'
    tu_path.write_text(PRELUDE + ''.join(f'#include "{p.as_posix()}"\n' for p in headers))
    args = ['--target=i686-pc-windows-msvc', '-fms-compatibility-version=19.44', '-std=c++20',
            '-fms-extensions', '-fms-compatibility', '-nostdinc', '-ferror-limit=0']
    for inc in msvc_includes():
        args += ['-isystem', inc]
    for inc in ('MWSE', 'SharedSE', 'deps/fmt/include', 'deps/DirectX8/include'):
        args += ['-I', str(tree / inc)]
    args += [f'-D{d}' for d in DEFINES]
    tu = ci.Index.create().parse(str(tu_path), args=args,
                                 options=ci.TranslationUnit.PARSE_SKIP_FUNCTION_BODIES)
    col = Collector()
    for t in roots(tu, tree):
        col.path_of(t, type_path(t.spelling))
    col.drain()
    bases = dump_bases(args, tu_path, col.records)
    for r in col.records.values():
        for off, name in bases.get(r['spelling'], []):
            if name == '*vptr':
                r['fields'].insert(0, [off, 'vtable', 'void*'])
            elif type_path(name) in col.records:
                r.setdefault('bases', []).append([off, type_path(name)])
    commit = subprocess.run(['git', '-C', str(tree), 'log', '-1', '--format=%h'],
                            capture_output=True, text=True).stdout.strip()
    data = {'source': f'MWSE {commit}', 'records': col.records, 'enums': col.enums}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=0, sort_keys=True))
    with_bases = sum(1 for r in col.records.values() if r.get('bases'))
    print(f'{len(col.records)} records ({with_bases} with bases), {len(col.enums)} enums -> {out}')
    failed = failed_asserts(tu)
    for f in failed:
        print(f'  {f}')
    print(f'MWSE size and offset checks failed: {len(failed)}')


ALIASES = {
    'unsigned char': 'uchar', 'uint8_t': 'uchar', 'BYTE': 'uchar', 'signed char': 'char',
    'int8_t': 'char', 'unsigned short': 'ushort', 'uint16_t': 'ushort', 'WORD': 'ushort',
    'int16_t': 'short', 'unsigned int': 'uint', 'unsigned': 'uint', 'uint32_t': 'uint',
    'DWORD': 'uint', 'unsigned long': 'uint', 'long': 'int', 'int32_t': 'int', 'BOOL': 'int',
    'int64_t': 'longlong', 'uint64_t': 'ulonglong', 'wchar_t': 'wchar16',
}
BASIC = {'bool': 1, 'char': 1, 'uchar': 1, 'short': 2, 'ushort': 2, 'int': 4, 'uint': 4,
         'longlong': 8, 'ulonglong': 8, 'float': 4, 'double': 8, 'wchar16': 2, 'void': 0}
SUFFIX = re.compile(r'\*|\[(\d+)\]')


def record_path(name, types):
    """A C++ name to a type path, trying TES3 and NI for an unqualified one."""
    path = type_path(name)
    if '::' in name or path in types['records'] or path in types['enums']:
        return path
    for ns in ('TES3', 'NI', 'TES3/UI'):
        cand = f'/{ns}{path}'
        if cand in types['records'] or cand in types['enums']:
            return cand
    return path


def internal_type(c, types):
    """'TES3::Statistic *', 'unsigned char[4]' -> '/TES3/Statistic*', 'uchar[4]'."""
    m = re.match(r'^(.*?)\s*((?:\s*(?:\*|\[\d+\]))*)\s*$', c.strip())
    base, suffix = m.group(1).strip(), re.sub(r'\s', '', m.group(2))
    base = ALIASES.get(base, base)
    if base not in BASIC and not base.startswith('undefined'):
        base = record_path(base, types)
    return base + suffix


def type_size(t, types):
    m = re.match(r'^([^*\[]*)(.*)$', t)
    size = None
    base = m.group(1)
    if base in BASIC:
        size = BASIC[base]
    elif base.startswith('undefined'):
        size = int(base[9:])
    else:
        r = types['records'].get(base) or types['enums'].get(base)
        size = r['size'] if r else 0
    for s in SUFFIX.finditer(m.group(2)):
        size = 4 if s.group(0) == '*' else size * int(s.group(1))
    return size


def merge(types, curated):
    """Apply the curated Xbox overlay to MWSE's PC layouts."""
    recs = {k: dict(v, fields=list(v['fields']), bases=list(v.get('bases', [])))
            for k, v in types['records'].items()}
    out = {'records': recs, 'enums': types['enums']}
    for s in curated.get('structs', []):
        path = record_path(s['name'], out)
        rec = recs.setdefault(path, {'kind': 'struct', 'size': 0, 'fields': [], 'bases': [],
                                     'spelling': s['name']})
        if 'size' in s:
            rec['size'] = int(s['size'], 0)
        if 'pc_valid_until' in s:
            lim = int(s['pc_valid_until'], 0)
            rec['fields'] = [f for f in rec['fields'] if f[0] + type_size(f[2], out) <= lim]
            rec['bases'] = [b for b in rec['bases']
                            if b[0] + recs.get(b[1], {}).get('size', 0) <= lim]
        for f in s.get('fields', []):
            off, t = int(f['offset'], 0), internal_type(f['type'], out)
            end = off + max(type_size(t, out), 1)
            rec['fields'] = [g for g in rec['fields']
                             if g[0] >= end or g[0] + type_size(g[2], out) <= off]
            rec['bases'] = [b for b in rec['bases']
                            if b[0] >= end or b[0] + recs.get(b[1], {}).get('size', 0) <= off]
            rec['fields'].append([off, f['name'], t])
        rec['fields'].sort(key=lambda f: f[0])
        if s.get('note'):
            rec['note'] = s['note']
    return out


CONVENTIONS = re.compile(r'\b(__thiscall|__cdecl|__stdcall|__fastcall)\b\s*')


def ghidra_signature(sig):
    """Curated C prototype -> (Ghidra signature text, calling convention or None)."""
    conv = CONVENTIONS.search(sig)
    text = CONVENTIONS.sub('', sig)
    # Type names stay qualified (TES3::Object and NI::Object are different); Ghidra resolves them.
    text = re.sub(r'\b(?:\w+::)+(\w+)(?=\s*\()', r'\1', text)
    # Ghidra signatures have no const and its parser rejects the word.
    text = re.sub(r'\bconst\s+', '', text)
    return text.strip(), conv.group(1) if conv else None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('mwse', help='MWSE source tree (github.com/MWSE/MWSE)')
    ap.add_argument('--out', type=Path, default=OUT)
    a = ap.parse_args()
    generate(a.mwse, a.out)


if __name__ == '__main__':
    main()
