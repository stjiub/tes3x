# heap-region: engine heap on 128 MB

A TES3X patch for consoles with 128 MB. Retail Morrowind never needs it; large mods such as
Tamriel Rebuilt do.

## The limit

The engine's global heap, `Memory_Heap` at `0x003EFC70`, is built by a static initializer at
`0x00265E90`. It takes one block of `0x1100000` bytes (17 MB) from CRT `malloc`, the immediate of
the `push` at `0x00265E93`, and hands out small allocations from it. Once that region is full,
every further block becomes its own CRT `malloc`, with that allocator's per-block overhead.
Retail uses about 12 MB of the region. The masters of Tamriel Rebuilt fill it and spill about
750,000 blocks.

The region is committed when it is allocated, so its whole size is gone from general memory from
the start, whether it is used or not.

## What the patch changes

The `push 0x1100000` becomes a call to `hooks/tes3xregion.c`, which leaves the region size on
the stack where the push put it. When the kernel reports more than 64 MB of physical memory, the
size is `[Xbox] HeapRegionKB` in `Morrowind.ini`, 57344 (56 MB) if the key is absent, clamped
between retail's 17,408 and 98,304. With 64 MB the region stays at retail's size, whatever the
key says. The log records the size chosen as `region.kb`, or `region.kb_ini` when it came from
the key.

## Choosing a size

A region larger than the heap's live data only holds memory idle. One smaller than it leaves the
rest to CRT `malloc`, which costs more per block. The best size is about what the load order keeps
live in the heap, and it grows with the load order.
