# Paged script bytecode

Patch key: `script-arena`

Every script a plugin defines keeps its compiled bytecode in the engine heap for the whole
session, although only the scripts of loaded objects, and global scripts that are running, are
read in play. With the Tamriel Rebuilt masters that is about 2 MB. This patch moves the bytecode
into memory that TES3X pages to disk, so scripts that are not running cost little resident memory.

See the [patch table](../docs/patches.md) for availability and selection.

## How it works

The patch uses TES3X's demand pager, as [`link-name-arena`](link-name-arena.md) does, and takes
4 MB of its region.

- The script record loader's bytecode buffer (`SCDT`) comes from that space instead of the engine
  heap (`hooks/tes3xscriptarena.c`). Scripts compiled in the console stay in the heap.
- Local variable names stay in the heap: loading dialogue looks up every `Local` filter's
  variable in every script's names, which paging would turn into hundreds of thousands of disk
  reads.
- It turns on `link-name-arena`, whose guard at `Memory_Heap::Free` leaves blocks in the paged
  region alone.
- The log records the space used at each megabyte as `scripts.arena_kb`, and `scripts.full_kb` if
  it ever fills. A full arena, or a pager that cannot start, sends allocations back to the engine
  heap as before.

## Compatibility and limits

A running script whose bytecode has gone to disk waits for it to be read back the first time it
runs again. Space in the arena is never reused, so bytecode the game frees, such as a script a
later plugin replaces, stays on disk for the rest of the session.
