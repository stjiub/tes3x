# Unresolvable reference cleanup

When a save refers to an object from a mod that is no longer loaded, the retail loader keeps the
reference and restamps it as an object created at runtime. The result is a phantom object that
belongs to no mod and stays in the save for good. This patch drops such a reference instead.

See the [patch table](../docs/patches.md) for availability and selection.

## How it works

When the loader cannot resolve a changed reference through the save's own master list, three
failure paths fall through to `0x00129CE5`, which restamps the reference with the reading file's
index. For a savegame that index is 0, the encoding for "created at runtime".

- `0x00129CE5`: a six-byte `jmp` into `tes3x_ref_load_hook` (`hooks/tes3xrefs.c`). The failure
  paths are `rel8` branches that cannot reach a payload, but they share this landing instruction
  with the one legitimate path, and `bl` tells them apart. The legitimate path repeats the replaced
  instruction and resumes at `0x00129CEB`. A failed resolution drops the reference by rejoining
  the loader's own skip tail at `0x00129E2B`, and logs `refs.orphan`.
- `0x00129C97`: `sar eax,0x18` becomes `shr eax,0x18`, so a mod index of `0x80` or above no longer
  sign-extends negative and fails every lookup.

Both addresses come from the patcher's own signature searches (`--locate ref-load`,
`--locate ref-skip`).

## Configuration

`[Xbox] DropReplacedRefs=0` in `Morrowind.ini` restores the retail restamp without repatching.
The default is `1`.

## Compatibility and limits

Only the load half of the Morrowind Code Patch fix is ported. MCP's Keep Replaced Refs is a
save-side merge mode, not a load-time choice, and has no equivalent here.
