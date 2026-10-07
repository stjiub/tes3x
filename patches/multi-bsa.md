# Multiple BSA loading

Patch key: `multi-bsa`

The Xbox engine opens exactly one archive, `Morrowind.bsa`; nothing else in `Data Files` can be
archived. This patch loads further archives from a list, so mod assets can ship in their own
archive instead of loose files or a rebuilt `Morrowind.bsa`.

See the [patch table](../docs/patches.md) for availability and selection.

## How it works

The archive path is pushed and opened at one place in the engine. The patch redirects that call to
a hook (`hooks/tes3xarch.c`), which first loads `Morrowind.bsa` as before and keeps its result.
It then reads `tes3xarch.txt` from the same folder and loads each archive it names, one per line,
through the engine's own `Archive::Load`. Blank lines and lines starting with `;` or `#` are
skipped.

A line is a path relative to that folder, or a full path with a drive letter, such as
`E:\TES3X\lib\mod.bsa`, so several builds can load one copy of an archive. A game sees only its
own drives, so the patch first makes `C:`, `E:`, `F:` or `G:` available when a line names it.

`Archive::Load` puts each new archive at the front of the search chain, so a later line wins over
an earlier one, and every listed archive wins over `Morrowind.bsa`.

## Using it

The pipeline writes `tes3xarch.txt` and applies the patch when it packs with `delta-bsa`, or when a
mod's archives are set to load; see [pipeline](../docs/pipeline.md).
