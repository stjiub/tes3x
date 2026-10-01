# Paged dialogue link names

While plugins load, every dialogue response (`INFO`) keeps three names, its own ID and those of
the responses before and after it, until the responses are linked into order. With the Tamriel
Rebuilt masters these tables hold about 12 MB of the engine heap. This patch moves them out of the
heap into memory that TES3X pages to disk, so they cost little resident memory.

See the [patch table](../docs/patches.md) for availability and selection.

## How it works

The patch uses TES3X's demand pager: a reserved region of address space whose pages are committed
when first touched and written out to `E:\tes3xpage.bin` when they go cold, under a resident
budget of 1 MB.

- The `INFO` constructor's two allocations, the 12-byte table and the three 32-byte names, go to a
  hook (`hooks/tes3xinfoarena.c`) that places both in one 112-byte block in the paged region. A
  new topic starts a new page, so each topic's blocks stay together.
- The two paths that free the names skip blocks in the region.
- The post-load cleanup still links every response, but clears a paged table's pointer without
  reading it, so the cleanup does not fault each cold page back in.
- After the load, the log records the number of blocks, topics, the region used and any fallbacks
  (`info.allocations`, `info.groups`, `info.arena_kb`, `info.fallbacks`).

If the pager cannot start, or the region is full, an allocation falls back to the engine heap as
before.

## Compatibility and limits

The region lives for the whole process, so paged tables are never freed; their cold pages cost
disk space instead of memory. The paging file on `E:` can grow to the region's 64 MB.
