# video-arena: video memory on 128 MB

A TES3X patch for consoles with 128 MB. Retail Morrowind never needs it; large mods such as
Tamriel Rebuilt do.

## The limit

Every texture and vertex buffer comes from one contiguous arena, allocated once at startup by the
constructor at `0x000150A0`. Its size, `0xF80000` bytes (15.5 MB), is an immediate at
`0x00013549`, whatever the console's memory. The busiest retail exteriors keep about 12.4 MB of it
live.

When the arena is full, the allocator at `0x00015100` gives each further block its own contiguous
physical allocation (`0x0001516E`) from general memory, rounded up to 4 KB pages, and tracks the
total at heap offset `+0x2C`. Small vertex buffers then cost a whole page each: overflow of 6 MB
has been measured holding 8.8 MB of pages. When that allocation also fails, the engine's
out-of-memory handler (`0x00092BA0`) purges what it can and relaunches the title with the "disc
may be dirty or damaged" error.

## What the patch changes

The `mov ebx, 0xF80000` at `0x00013549` becomes a call to `hooks/tes3xarena.c`, which returns the
arena size in `ebx` and leaves the heap object in `eax` untouched. When the kernel reports more
than 64 MB of physical memory, the size is `[Xbox] VideoMemoryKB` in `Morrowind.ini`, 22528
(22 MB) if the key is absent, clamped between retail's 15,872 and 49,152. With 64 MB the arena
stays at retail's size, whatever the key says. The log records the size chosen as `arena.kb`, or
`arena.kb_ini` when it came from the key.

## Choosing a size

The arena and general memory share the same RAM, so a larger arena only moves memory between the
two: everything the arena gains is gone from general memory from the start. It saves the page
rounding of overflow, not the memory the content itself needs. A size that leaves too little
general memory fails in the general allocator instead, through the same out-of-memory handler.
