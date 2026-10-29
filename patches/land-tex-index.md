# Indexed land texture lookup

Patch key: `land-tex-index`

A landscape record lists the textures of its 16 by 16 squares by index. The engine resolves each
index through a list it builds for that record, which has one node for every index up to the
highest one used, then walks the game's texture list on a miss. With the Tamriel Rebuilt masters
that is about 5 s of the console's load to the main menu, 4,171 records at over a millisecond
each. The index answers each lookup at once.

See the [patch table](../docs/patches.md) for availability and selection.

## How it works

The patch (`hooks/tes3xlandtexindex.c`) keeps a table from texture index to the first texture in
list order that has it. It adds textures as they are appended and rebuilds when the list changes
in any other way. Every hit is compared with the texture's current index. When the table cannot
answer, it leaves the lookup to the engine's own walk.

## Configuration

`[Xbox] LandTexIndex=verify` in `Morrowind.ini` runs the engine's walk beside every indexed lookup,
returns the engine's answer, and logs `landtex.mismatch` when they differ. `landtex.checks` counts
the lookups compared.
