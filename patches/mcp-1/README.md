# mcp-1: savegame corruption fix

Port of Morrowind Code Patch fix 1, load half only.

When the loader cannot resolve a changed reference through the save's own master list, three
failure paths fall through to `0x00129CE5`, which restamps the reference with the reading file's
index. For a savegame that index is 0, the encoding for "created at runtime", so a reference from
a removed mod turns into a phantom runtime object.

## What the patch changes

- `0x00129CE5`: a six-byte `jmp` into `tes3x_ref_load_hook` (`hooks/tes3xrefs.c`). The failure
  paths are `rel8` branches that cannot reach a payload, but they share this landing instruction
  with the one legitimate path, and `bl` tells them apart. The legitimate path repeats the replaced
  instruction and resumes at `0x00129CEB`. A failed resolution drops the reference by rejoining
  the loader's own skip tail at `0x00129E2B`.
- `0x00129C97`: `sar eax,0x18` becomes `shr eax,0x18`, so a mod index of `0x80` or above no longer
  sign-extends negative and fails every lookup.

`[Xbox] DropReplacedRefs=0` in `Morrowind.ini` restores the vanilla restamp. Both addresses come
from the patcher's own signature searches (`--locate ref-load`, `--locate ref-skip`).

MCP's Keep Replaced Refs is a save-side merge mode, not a load-time choice, and is not ported.

## What remains

Builds boot and play a full vanilla intro without regression, but the drop branch has never
executed in a test: no run has logged `refs.orphan`. A proof needs one controlled failed
resolution showing:

1. with `DropReplacedRefs=1`, `refs.orphan` in the log, the reference dropped, and the cell
   loading through the skip tail;
2. with `DropReplacedRefs=0`, the same input taking vanilla's restamp path, and the cell still
   loading.
