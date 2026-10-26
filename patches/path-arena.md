# Paged model and icon paths

Patch key: `path-arena`

Every object a plugin defines keeps the file path of its model, and every item the path of its
inventory icon, in the engine heap for the whole session. The game reads a model path when a cell
places the object and an icon path when a menu shows the item, so most of them sit unused. This
patch moves both into memory that TES3X pages to disk.

See the [patch table](../docs/patches.md) for availability and selection.

## How it works

The patch uses TES3X's demand pager, as [`link-name-arena`](link-name-arena.md) does, and takes
2 MB of its region.

- Record loaders read a `MODL` or `ITEX` subrecord and then store the path through the engine's
  string setter. The patch notes the tag each subrecord read returns, and when the setter next
  allocates after a `MODL` or `ITEX`, the string comes from the paged space instead of the engine
  heap (`hooks/tes3xpatharena.c`). Any other allocation clears the tag, so it chooses one string.
- It turns on `link-name-arena`, whose guard at `Memory_Heap::Free` leaves blocks in the paged
  region alone.
- The log records the space used at each 512 KB as `paths.arena_kb`, and `paths.full_kb` if it
  ever fills. A full arena, or a pager that cannot start, sends allocations back to the engine
  heap as before.

## Compatibility and limits

Space in the arena is never reused, so a path the game replaces, such as one a later plugin
overrides, stays on disk for the rest of the session.
