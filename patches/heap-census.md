# Engine heap census

A tool for finding what fills memory: it counts the engine heap's live bytes by the source file
and line that allocated them, or by call site where the engine passes no source.

See the [patch table](../docs/patches.md) for availability and selection.

## How it works

The patcher redirects every direct call to `Memory_Heap::Allocate` and `Memory_Heap::Free`, found
from the names they push for their lock trace. Allocations that pass a source file and line are
grouped by it. About 1,700 inlined `new` sites pass `"NA"` and line 0; those are grouped by call
site instead. Six forwarders would otherwise hide the real caller: four wrappers that forward
their own caller's file and line, the frameless global `operator new`, and the heap fallback in
`MemoryPool_Simple::Allocate`. Each gets its own stub that takes the caller from its frame.

Live blocks are kept in an 8 MB open-addressed table for one address in four, chosen by hash,
since a large mod list keeps millions of blocks live. Interrupts are masked while the table
changes. A snapshot also carries `MmQueryStatistics` and the first words of the heap object.

## Using it

Build with `--apply heap-census`, or the pipeline's `--heap-census`. See
[testing](../docs/testing.md#heap-census) for how snapshots are taken and read.

## Compatibility and limits

Instrumentation only: every allocation pays for the lookup, and the table needs 128 MB.
