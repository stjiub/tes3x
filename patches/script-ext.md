# Script opcode extensions

Morrowind scripts compile to bytecode, and the engine knows a fixed set of opcodes. This patch lets
the payload add new ones, so a plugin's compiled script can call code TES3X provides. It is also
the base that [legacy MWSE bytecode](mwse-legacy.md) runs on.

See the [patch table](../docs/patches.md) for availability and selection.

## How it works

- Six parsers infer an instruction's length from its opcode, and treat anything outside
  `0x1000`-`0x11BD` as unknown, which throws the rest of the script out of step. The patch raises
  all six upper bounds to `0x4000`. The engine compares them signed, so no ceiling can reach
  `0x8000`.
- The three calls to `Script::RunFunction` go through a hook (`hooks/tes3xscript.c`). Retail
  opcodes continue to the original function; opcodes from `0x2000` up to the ceiling run in the
  payload.
- The hook also counts calls per retail opcode.

The payload currently provides:

| opcode | command | returns |
|---|---|---|
| `0x2001` | `tes3xGetVersion` | the payload's script interface version, 1 |
| `0x2002` | `tes3xDumpProfile` | writes the per-opcode call counts to the log |

## Using it

`tools/tes3x_scriptasm.py` writes a plugin that calls an opcode by extending a script the engine
already runs. Script compilers do not know the new commands' names, so a script cannot name them
in source.

## Compatibility and limits

The new commands take no arguments. An opcode with a zero byte in either half cannot be encoded.
