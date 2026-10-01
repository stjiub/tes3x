# Saved-script initialization fix

While a save loads, `Script::ReplaceGlobalsInData` scans compiled script bytecode and skips over
operands. It advances the cursor wrongly for two operand forms, so it can land in the middle of a
string identifier and read it as opcodes: "Unable to find id" errors, or a load that never ends.
This patch corrects both advances.

See the [patch table](../docs/patches.md) for availability and selection.

## How it works

- The fixed-width operand advance at `0x00139C9A`: 3 bytes becomes 2.
- The length-prefixed advance at `0x00139C9F`: a hook (`hooks/tes3xmcp97.c`) advances by the
  length plus one.

MCP's third PC-side correction is already present in the Xbox build.

A length-prefixed operand is a type byte, a length byte, then that many name bytes. Retail
resumes on the name's last character; if that character is an ordinary letter, it counts as a
plain byte, advances one, and the scan resynchronises. That is why most saves load. The bug bites
only when that character is itself a token: a space or one of `A B D F L M R S T`. The
fixed-width form errs the other way and steps over the next token's first byte.
