# Paged NPC objects

Patch key: `npc-arena`

Every NPC record is an object in memory for the whole session, and a large load order such as
Tamriel Rebuilt's has more than twelve thousand of them. Play reads only the NPCs of the cells
around the player, so this patch moves the NPC objects the record loader creates into memory
that TES3X pages to disk.

See the [patch table](../docs/patches.md) for availability and selection.

## How it works

The patch uses TES3X's demand pager, as [`land-arena`](land-arena.md) does, and takes 4 MB of
its region.

- The record loader takes each new NPC object from the engine's NPC pool, which overflows into
  the engine heap once full. The patch sends that allocation to the paged space instead
  (`hooks/tes3xnpcarena.c`), and shrinks the pool to 16 objects.
- The loader frees the object it built when a record merges into an existing one; the patch
  reuses those blocks through `link-name-arena`'s guard at `Memory_Heap::Free`.
- Two engine searches read every NPC: the reference lookups of global script startup and the
  creature lookup behind animation sound keys. With the NPCs paged each would read them all from
  disk again for every lookup, so the patch turns on [`ref-index`](ref-index.md) and
  [`soundgen-index`](soundgen-index.md), which replace both.
- The log records the space used at each 1 MB as `npcs.arena_kb`, and `npcs.full_kb` if it ever
  fills. A full arena, or a pager that cannot start, sends allocations back to the pool as before.

A console or script command that finds a reference by ID outside global script startup still
searches every cell, and so pages in every NPC that has a reference.
