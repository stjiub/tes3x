# Empty pathgrid fix

Patch key: `mwse-pathgrid-zero`

Actors that wander or flee choose a random node from their cell's pathgrid. A pathgrid record
with no nodes makes that choice divide by zero and crash the game. The patch treats a pathgrid
without nodes as no pathgrid, so the actor stays put instead.

## How it works

Wander and Flee both read the cell's pathgrid through one small getter. The patch sends those two
calls to a replacement that returns nothing when the grid has no nodes. Both callers already handle
a missing pathgrid. This is the fix MWSE ships as a patch of the same name. See the
[patch table](../docs/patches.md) for availability and selection.
