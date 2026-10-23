"""Script command names and opcodes: the retail command table plus TES3X's injected opcodes.

`symbols/script-commands.json` is generated from a retail XBE (`tes3x sym commands`) and read
by tools that compile against command names.
"""

import json
import re
import struct

from tes3x.paths import resource

COMMANDS = resource('symbols', 'script-commands.json')
SCRIPT_HOOK = resource('hooks', 'tes3xscript.c')

# `TES3X_OP_VERSION = 0x2001, /* tes3xGetVersion  -> ... */`
INJECTED = re.compile(r'TES3X_OP_\w+\s*=\s*(0x[0-9A-Fa-f]+),\s*/\*\s*(\w+)')


def read_table(x):
    """Every command-table entry before the sentinel, as (opcode, name, short name)."""
    from tes3x.patch_sites import COMMAND_SENTINEL, COMMAND_STRIDE, find_command_table

    def cstr(va):
        if not va:
            return ''
        off = x.va_to_off(va)
        return bytes(x.data[off:x.data.index(b'\0', off)]).decode('latin-1')

    off = x.va_to_off(find_command_table(x))
    sentinel = COMMAND_SENTINEL.rstrip(b'\0').decode()
    out = []
    while True:
        name, short, opcode = struct.unpack_from('<3I', x.data, off)
        name = cstr(name)
        if name == sentinel:
            return out
        out.append((opcode, name, cstr(short)))
        off += COMMAND_STRIDE


def injected():
    return [(int(op, 16), name)
            for op, name in INJECTED.findall(SCRIPT_HOOK.read_text(encoding='utf-8'))]


def generate(x):
    retail = []
    for opcode, name, short in read_table(x):
        entry = {'opcode': f'0x{opcode:04X}', 'name': name}
        if short:
            entry['short'] = short
        retail.append(entry)
    return {
        'retail': retail,
        'injected': [{'opcode': f'0x{op:04X}', 'name': name, 'patch': 'script-ext'}
                     for op, name in injected()],
    }


def dumps(data):
    """One entry per line, so a regenerated table diffs by command."""
    blocks = []
    for key, entries in data.items():
        rows = ',\n'.join('  ' + json.dumps(e) for e in entries)
        blocks.append(f' "{key}": [\n{rows}\n ]')
    return '{\n' + ',\n'.join(blocks) + '\n}\n'


def load():
    """Name (lower case, long and short) -> opcode."""
    data = json.loads(COMMANDS.read_text(encoding='utf-8'))
    entries = data['retail'] + data['injected']
    out = {}
    # A long name wins over another command's short name.
    for field in ('name', 'short'):
        for entry in entries:
            if entry.get(field):
                out.setdefault(entry[field].lower(), int(entry['opcode'], 16))
    return out


def opcode(name):
    table = load()
    try:
        return table[name.lower()]
    except KeyError:
        raise SystemExit(f'no script command named {name!r} in {COMMANDS.name}') from None
