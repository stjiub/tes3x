# Same-volume save staging

Patch key: `save-staging=LETTER`

The game writes each save in two steps: it first writes the files to a staging location, then moves
them into the save folder. Retail stages on `Z:`, the cache partition, while saves live under
`E:\UDATA`, so the move cannot be a rename: every save is written twice, copied across partitions
and deleted. This patch stages on the drive that holds the save folder instead, so the move can be
a rename.

See the [patch table](../docs/patches.md) for availability and selection.

## How it works

The save writer serialises the game to `Z:\tempsave.ess` and a companion file `Z:\vv.dat`, with the
screenshot on `T:`, then commits all three with `MoveFileEx`. XAPI's `MoveFileEx` renames, and
falls back to a copy and a delete only when source and destination are on different volumes. `T:`
is `E:\TDATA\<title ID>`, on the same volume as the save folder, which is why only the screenshot
is renamed in retail.

The patch rewrites the drive letter of the staging paths: `tempsave.ess`, the staging drive
prefix beside it, and both spellings of `vv.dat`. The engine's own `Z:\` for `Data Files` is a
separate string and is left to the [drive redirect](drive-letters.md).

## Configuration

`--apply save-staging=T`, or the pipeline's `--save-staging T`. `T` is the title's own folder on
the save volume.
