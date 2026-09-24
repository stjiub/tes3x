# mem-census

Memory outside the engine heap, by caller: `--apply mem-census`, or the pipeline's
`--mem-census`. Snapshots append to `E:\tes3xmem.bin` at the first frame (needs `diagnostics`)
and on the console command `tes3xmem`.

At entry the payload replaces the import thunks of the kernel's allocators with wrappers:
`NtAllocateVirtualMemory`, `MmAllocateContiguousMemory(Ex)`, `MmAllocateSystemMemory`,
`ExAllocatePoolWithTag`, `MmClaimGpuInstanceMemory` and their frees. Every caller goes through
the thunks, including D3D and DSOUND. The patcher also redirects the direct calls to XAPI's
`RtlAllocateHeap` and `RtlFreeHeap`, the heap under CRT `malloc`, found from CRT `_heap_alloc`
and XAPI `HeapFree`.

Each allocation records its return address and the next return addresses on the stack that lie
in `.text` just after a call, so a caller is found through library functions that keep no frame
pointer. Other sections are skipped because they can be unloaded. Kernel allocations and heap
blocks of 64 KB or more are tracked exactly; smaller heap blocks for one address in four. Each
snapshot also walks the address space with `NtQueryVirtualMemory`, since only the kernel knows
how much of a reservation is committed.

Instrumentation only: the tables take about 5 MB.
