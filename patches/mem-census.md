# Kernel memory census

Patch key: `mem-census`

A tool for finding what uses memory outside the engine heap: kernel allocations and the XAPI heap
under CRT `malloc`, counted by caller, with a map of the address space.

See the [patch table](../docs/patches.md) for availability and selection.

## How it works

At entry the payload (`hooks/tes3xmem.c`) replaces the import thunks of the kernel's allocators
with wrappers: `NtAllocateVirtualMemory`, `MmAllocateContiguousMemory(Ex)`,
`MmAllocateSystemMemory`, `ExAllocatePoolWithTag`, `MmClaimGpuInstanceMemory` and their frees.
Every caller goes through the thunks, including D3D and DSOUND. The patcher also redirects the
direct calls to XAPI's `RtlAllocateHeap` and `RtlFreeHeap`, the heap under CRT `malloc`, found
from CRT `_heap_alloc` and XAPI `HeapFree`.

Each allocation records its return address and the next return addresses on the stack that lie
in `.text` just after a call, so a caller is found through library functions that keep no frame
pointer. Other sections are skipped because they can be unloaded. Kernel allocations and heap
blocks of 64 KB or more are tracked exactly; smaller heap blocks for one address in four. Each
snapshot also walks the address space with `NtQueryVirtualMemory`, since only the kernel knows
how much of a reservation is committed.

## Using it

Build with `--apply mem-census`, or the pipeline's `--mem-census`. Snapshots append to
`E:\tes3xmem.bin` at the first frame (which needs `diagnostics`) and on the console command
`tes3xmem`; `tes3x mem` renders them.

## Compatibility and limits

Instrumentation only: the tables take about 5 MB.
