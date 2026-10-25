# Expanded-memory video arena

Patch key: `video-arena`

On a console with 128 MB, this patch lets textures and vertex buffers use more memory than the
retail game's fixed arena. Retail Morrowind never needs it; large mods such as Tamriel Rebuilt do.

See the [patch table](../docs/patches.md) for availability and selection.

## How it works

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

The `mov ebx, 0xF80000` at `0x00013549` becomes a call to `hooks/tes3xarena.c`, which returns the
arena size in `ebx` and leaves the heap object in `eax` untouched. The size comes from the ini key
below. With 64 MB of physical memory the key can only shrink the arena below retail's size. The log
records the size chosen as `arena.kb`, or `arena.kb_ini` when it came from the key.

## Configuration

`[Xbox] VideoMemoryKB` in `Morrowind.ini` sets the arena size in KB, clamped between 1,024 and
49,152. Without the key it is 22528 (22 MB) with more than 64 MB, and retail's 15,872 on 64 MB,
where a larger value is also capped at 15,872.

The retail arena holds about 2.4 MB at the main menu and peaks near 2.5 MB through record loading,
in retail and with Tamriel Rebuilt's masters alike; busy cells need 10-18 MB. A smaller arena
leaves more general memory for loading records, but every block past it then costs whole pages.

The arena and general memory share the same RAM, so a larger arena only moves memory between the
two: everything the arena gains is gone from general memory from the start. It saves the page
rounding of overflow, not the memory the content itself needs. A size that leaves too little
general memory fails in the general allocator instead, through the same out-of-memory handler.
