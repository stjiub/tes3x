# Paged model paths, icon paths and display names

Patch key: `path-arena`

Every object a plugin defines keeps the file path of its model, every item the path of its
inventory icon, and most objects their display name, in the engine heap for the whole session.
The game reads a model path when a cell places the object, and an icon path or name when a menu
or tooltip shows it, so most of them sit unused. This patch moves all three into memory that
TES3X pages to disk.

See the [patch table](../docs/patches.md) for availability and selection.

## How it works

The patch uses TES3X's demand pager, as [`link-name-arena`](link-name-arena.md) does, and takes
3 MB of its region.

- Record loaders read a `MODL`, `ITEX` or `FNAM` subrecord and then store the string through the
  engine's string setter. The patch notes the tag each subrecord read returns, and when the setter
  next allocates after one of those tags, the string comes from the paged space instead of the
  engine heap (`hooks/tes3xpatharena.c`). Any other allocation clears the tag, so it chooses one
  string.
- It turns on `link-name-arena`, whose guard at `Memory_Heap::Free` leaves blocks in the paged
  region alone.
- The log records the space used at each 512 KB as `paths.arena_kb`, and `paths.full_kb` if it
  ever fills. A full arena, or a pager that cannot start, sends allocations back to the engine
  heap as before.

With the Tamriel Rebuilt masters the strings take 1.5-2 MB of the arena, and about 2.4 MB more
memory stays free in a new game.

## Compatibility and limits

Space in the arena is never reused, so a string the game replaces, such as a path a later plugin
overrides, stays on disk for the rest of the session.
