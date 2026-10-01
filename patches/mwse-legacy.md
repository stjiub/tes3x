# Legacy MWSE bytecode

Patch key: `mwse-legacy`

Some older PC mods were compiled with MWSE 0.9.4, which embeds its own stack-machine instructions
in a script's compiled bytecode. The Xbox engine does not know them: the script stops or goes out
of step. This patch interprets a subset of those instructions, so such mods can run.

See the [patch table](../docs/patches.md) for availability and selection.

## How it works

Legacy MWSE instructions use opcodes from `0x3800`. With [script opcode extensions](script-ext.md)
in place, those opcodes reach the payload (`hooks/tes3xmwse.c`), which keeps a per-script stack
and registers, as MWSE did.

When a script loads, the engine walks its bytecode once to fix up references. Legacy instructions
carry inline operands that walk would misread, so the patch redirects that walk's decoder call and
steps over each known instruction's operand.

The supported subset covers:

- the stack and registers: push, pop and their register forms;
- arithmetic, comparisons and jumps;
- local variables and references: `GetLocal`, `SetLocal`, `RefPCTarget`, `XGetPCTarget`, `SetRef`,
  `XRefType`;
- item values: `XGetValue`, `XSetValue`, and getting and setting weight, quality, condition,
  maximum condition, charge and maximum charge.

## Compatibility and limits

An unsupported instruction is skipped and logged as `mwse.unsupported_opcode`; the script
continues. MWSE 2.x mods are Lua and are not supported. The pipeline enables script opcode
extensions with this patch.
