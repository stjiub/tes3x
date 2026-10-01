# Expanded-memory heap region

This patch stops the engine heap holding memory it has not used, and lets it grow large enough for
mods such as Tamriel Rebuilt on a 128 MB console.

See the [patch table](../docs/patches.md) for availability and selection.

## How it works

The engine's global heap, `Memory_Heap` at `0x003EFC70`, is built by a static initializer at
`0x00265E90`. It takes one block of `0x1100000` bytes (17 MB) from CRT `malloc`, the immediate of
the `push` at `0x00265E93`, and hands out small allocations from it. Once that region is full,
every further block becomes its own CRT `malloc`, with that allocator's per-block overhead.
Retail uses about 12 MB of the region. The masters of Tamriel Rebuilt fill it and spill about
750,000 blocks.

The region is allocated with `malloc`, which commits all of it at once. Its whole size is gone
from general memory from the start, whether it is used or not: about 5 MB for retail on 64 MB.

The patch (`hooks/tes3xregion.c`) changes four places:

- The `push 0x1100000` becomes a call that leaves the region size on the stack where the push put
  it. The log records it as `region.kb`, or `region.kb_ini` when it came from the ini key.
- The constructor's `malloc` of the region becomes a reservation of address space.
- `Allocate` places a new block at the heap's high-water mark (`+0x0C`) once it has checked that
  the block fits the region. The patch retargets that check's `jbe` so that the pages under the
  block are committed first, 64 KB at a time. If the commit fails, the block goes to CRT
  `malloc` as it would once the region is full, and the log records `region.commit_failed_kb`.
- The destructor releases the reservation instead of freeing it.

Committed pages stay committed when the high-water mark falls again, as they did in retail.

## Configuration

`[Xbox] HeapRegionKB` in `Morrowind.ini` sets the region size: 98304 (96 MB) if the key is absent,
clamped between retail's 17,408 and 98,304.

Only committed pages use memory, so a region larger than the heap costs address space and
nothing else. The default fits the Tamriel Rebuilt masters, whose heap needs about 57-60 MB.
