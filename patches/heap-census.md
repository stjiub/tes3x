# heap-census

Live engine heap by source, for finding what fills memory: `--apply heap-census`, or the
pipeline's `--heap-census`.

The patcher redirects every direct call to `Memory_Heap::Allocate` and `Memory_Heap::Free`, found
from the names they push for their lock trace. Allocations that pass a source file and line are
grouped by it. About 1,700 inlined `new` sites pass `"NA"` and line 0; those are grouped by call
site instead. Six forwarders would otherwise hide the real caller: four wrappers that forward
their own caller's file and line, the frameless global `operator new`, and the heap fallback in
`MemoryPool_Simple::Allocate`. Each gets its own stub that takes the caller from its frame.

Live blocks are kept in an 8 MB open-addressed table for one address in four, chosen by hash,
since a large mod list keeps millions of blocks live. Interrupts are masked while the table
changes. A snapshot also carries `MmQueryStatistics` and the first words of the heap object.

Instrumentation only: every allocation pays for the lookup, and the table needs 128 MB. See
[testing](../docs/testing.md#heap-census) for how snapshots are taken.
