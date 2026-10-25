# Paged NPC and inventory link names

Patch key: `link-name-arena`

While plugins load, every NPC keeps the names of its race, class, faction, head and hair, and
every item in an NPC's, creature's or container's inventory keeps its item ID, until the game
links each name to its record after the last plugin has loaded. With the Tamriel Rebuilt masters
these names hold about 4 MB of the engine heap at once, during the load where memory is shortest.
This patch moves them into memory that TES3X pages to disk, so they cost little resident memory.

See the [patch table](../docs/patches.md) for availability and selection.

## How it works

The patch uses TES3X's demand pager, as [`info-name-arena`](info-name-arena.md) does: a reserved
region whose pages are committed when first touched and written out to `E:\tes3xpage.bin` when they
go cold, under a resident budget of 1 MB. This patch takes 8 MB at the top of the region.

- The NPC constructor's 160-byte buffer of five names, and the item ID string of each inventory
  entry read by the NPC, creature and container loaders and their saved-game counterparts, come
  from that space instead of the engine heap (`hooks/tes3xlinknames.c`).
- `Memory_Heap::Free` returns at once for a block in the paged region, so every path that frees
  these names, including the link pass and destructors, leaves them alone.
- The log records the space used at each megabyte as `links.arena_kb`, and `links.full_kb` if it
  ever fills. A full arena, or a pager that cannot start, sends allocations back to the engine
  heap as before.

## Compatibility and limits

Space in the arena is never reused, so its pages stay on disk for the rest of the session. The
paging file on `E:` can grow to the region's 64 MB, shared with `info-name-arena`.
