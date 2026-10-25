# Growing small-block pools

Patch key: `pool-grow`

This patch stops small allocations from paying a header each once the engine's fixed pools for
them are full. Retail never fills them; large mods such as Tamriel Rebuilt fill them during
loading and spill hundreds of thousands of blocks into the heap.

See the [patch table](../docs/patches.md) for availability and selection.

## How it works

`Memory_Heap::Allocate` (`0x00011840`) serves requests of 8 to 176 bytes from eleven size-class
pools created at startup by `addSizeClassPool` (`0x000124B0`), sized for retail. A pool block has
no header. Once a class's pool is full, every further block of that size comes from the heap's
region with an 8-byte header and the region's block bookkeeping. With the Tamriel Rebuilt
masters on a 128 MB console, the patch adds 78 pools (16.2 MB) during loading and leaves 4.4 MB
more memory free after it.

The patch (`hooks/tes3xpoolgrow.c`) changes two places in `Allocate`'s pool loop:

- The call to `MemoryPool_Simple::isExhausted` becomes a call to the payload. When the newest pool
  of a class is full, it allocates another pool object from the heap, constructs it with the
  engine's own constructor and the same element size, and links it into the loop's list right
  after the full one. Chunks start at 64 KB and double up to 256 KB per class. The object must
  come from the heap: before relaunching on a fatal error, `0x000D2E20` releases the 12- and
  176-byte pools with `Memory_Heap::Free`.
- The `jmp` that leaves the loop on a full pool becomes a step to the next pool, so the new pool,
  or an older one that has had blocks freed, is found.

`Memory_Heap::Free` and `sizeOfAllocation` already look up any block outside the region in the
global pool list, which the pool constructor joins, so freeing and reallocating need no change.
Each new pool logs `pool.grow` with its element size and `pool.grown_kb` with the total.

A pool's memory is never returned, like the region's own high-water mark. At most 128 pools are
added; after that, blocks spill into the region as before.
