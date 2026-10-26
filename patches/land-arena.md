# Paged land objects

Patch key: `land-arena`

Every exterior cell's land record is an object in memory for the whole session. A cell reads its
land only when it loads, so most of them sit unused, and a large exterior such as Tamriel
Rebuilt's has thousands. This patch moves the land objects the record loader creates into memory
that TES3X pages to disk.

See the [patch table](../docs/patches.md) for availability and selection.

## How it works

The patch uses TES3X's demand pager, as [`link-name-arena`](link-name-arena.md) does, and takes
4 MB of its region.

- The record loader takes each new land object from the engine's land pool, which overflows into
  the engine heap once full. The patch sends that allocation to the paged space instead
  (`hooks/tes3xlandarena.c`).
- Loading no longer fills the pool, so the patch shrinks it to 16 objects, which still serve land
  the game creates in play for a cell that has none.
- The loader frees the object it built when a record merges into an existing one; the patch
  reuses those blocks. It turns on `link-name-arena`, whose guard at `Memory_Heap::Free` hands
  them back.
- The log records the space used at each 1 MB as `lands.arena_kb`, and `lands.full_kb` if it ever
  fills. A full arena, or a pager that cannot start, sends allocations back to the pool as before.

With the Tamriel Rebuilt masters about 2.1 MB more memory stays free, with no change in load time.
