# Indexed cell lookup by name

Patch key: `cell-index`

The engine finds a cell by name, or an exterior cell by its grid position, by walking its whole
list of cells and comparing each one. It does this for every interior cell and path grid record
it loads and for every door's destination when it links the cells, so the work grows with the
square of the number of cells. Retail Morrowind has a few thousand cells; with the Tamriel
Rebuilt masters, about 9,000, these walks took about 92 s of the console's load to the main menu.

See the [patch table](../docs/patches.md) for availability and selection.

## How it works

The patch (`hooks/tes3xcellindex.c`) keeps two hash indexes over the cell list, one by name and
one by grid position, and answers both lookups from them. A cell's name, or its region's name
for an unnamed exterior, and its grid position can change only while one of its records loads, so
the patch hashes the cell being loaded and the newest cell again before each lookup, and compares
every candidate it finds with the cell's current name or position. When several cells match, it
returns the first in the engine's list, as the engine's own walk does. If the list changes in any
other way, the indexes are rebuilt; if the patch cannot get memory for them, it falls back to the
engine's walk.

The indexes take about 16 bytes per cell and per exterior cell, plus their hash tables: a few
hundred kilobytes with the Tamriel Rebuilt masters.

## Configuration

`[Xbox] CellIndex=verify` in `Morrowind.ini` runs the engine's walk beside every indexed lookup,
returns the engine's answer, and logs `cellindex.mismatch` with both cells when they differ.
`cellindex.checks` counts the lookups compared.
