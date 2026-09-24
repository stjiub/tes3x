/* Memory census outside Memory_Heap: every kernel allocation the title makes, and the XAPI heap
 * under CRT malloc, by caller.
 *
 * Kernel allocators are wrapped by swapping their import thunk slots at entry, so every caller
 * is seen, D3D and DSOUND included. RtlAllocateHeap and RtlFreeHeap are redirected at their call
 * sites. Each allocation records its return address and the next return addresses found on the
 * stack in .text, which reach the engine caller through FPO library frames. Heap blocks below
 * 64 KB are tracked for a fixed quarter of addresses; everything else exactly. Each snapshot
 * also walks the address space, since committed bytes per reservation are the kernel's to know.
 * Snapshots append to E:\tes3xmem.bin.
 */

#include "tes3x_thunks.h"
#include "tes3xnt.h"
#include "tes3xlog.h"
#include "tes3xmem.h"

#ifndef TES3X_MEM_HEAP_ALLOC
#error "define TES3X_MEM_HEAP_ALLOC to RtlAllocateHeap"
#endif
#ifndef TES3X_MEM_HEAP_FREE
#error "define TES3X_MEM_HEAP_FREE to RtlFreeHeap"
#endif
#ifndef TES3X_MEM_CODE_START
#error "define TES3X_MEM_CODE_START and TES3X_MEM_CODE_END to the XBE's .text range"
#endif
#ifndef TES3X_BUILD_ID
#define TES3X_BUILD_ID 0
#endif

#define NtCreateFile KFN(THUNK_NtCreateFile, fn_NtCreateFile)
#define NtWriteFile KFN(THUNK_NtWriteFile, fn_NtWriteFile)
#define NtClose KFN(THUNK_NtClose, fn_NtClose)
#define NtQueryVirtualMemory KFN(THUNK_NtQueryVirtualMemory, fn_NtQueryVirtualMemory)
#define MmQueryStatistics KFN(THUNK_MmQueryStatistics, fn_MmQueryStatistics)
#define KeQuerySystemTime KFN(THUNK_KeQuerySystemTime, fn_KeQuerySystemTime)

typedef u32(__stdcall *fn_NtAllocateVirtualMemory)(void **, u32, u32 *, u32, u32);
typedef u32(__stdcall *fn_NtFreeVirtualMemory)(void **, u32 *, u32);
typedef u32(__stdcall *fn_NtQueryVirtualMemory)(void *, void *);
typedef void *(__stdcall *fn_MmAllocateContiguousMemory)(u32);
typedef void *(__stdcall *fn_MmAllocateContiguousMemoryEx)(u32, u32, u32, u32, u32);
typedef void(__stdcall *fn_MmFreeContiguousMemory)(void *);
typedef u32(__stdcall *fn_MmFreeSystemMemoryRet)(void *, u32);
typedef void *(__stdcall *fn_ExAllocatePoolWithTag)(u32, u32);
typedef void(__stdcall *fn_ExFreePool)(void *);
typedef void *(__stdcall *fn_MmClaimGpuInstanceMemory)(u32, u32 *);
typedef u32(__stdcall *fn_MmQueryStatistics)(void *);
typedef void *(__stdcall *fn_heap_alloc)(void *, u32, u32);
typedef u32(__stdcall *fn_heap_free)(void *, u32, void *);

typedef struct {
    u32 Length;
    u32 TotalPhysicalPages;
    u32 AvailablePages;
    u32 VirtualMemoryBytesCommitted;
    u32 VirtualMemoryBytesReserved;
    u32 CachePagesCommitted;
    u32 PoolPagesCommitted;
    u32 StackPagesCommitted;
    u32 ImagePagesCommitted;
} MM_STATISTICS;

typedef struct {
    u32 BaseAddress;
    u32 AllocationBase;
    u32 AllocationProtect;
    u32 RegionSize;
    u32 State;
    u32 Protect;
    u32 Type;
} MEMORY_BASIC_INFORMATION;

#define MEM_MAGIC 0x4D583354u /* "T3XM" */
#define MEM_VERSION 1
#define FRAMES 10
#define SCAN_WORDS 128
#define SITE_BITS 13
#define SITE_COUNT (1u << SITE_BITS)
#define EXACT_BITS 14
#define EXACT_SIZE (1u << EXACT_BITS)
#define EXACT_LIMIT (EXACT_SIZE / 4 * 3)
#define SAMPLE_BITS 19
#define SAMPLE_SIZE (1u << SAMPLE_BITS)
#define SAMPLE_LIMIT (SAMPLE_SIZE / 10 * 7)
#define SAMPLE_SHIFT 2
#define UNIT_BITS (32 - SITE_BITS)
#define UNIT_SHIFT 3
#define BIG_BYTES 0x10000u
#define RESERVATION_MAX 2048
#define REGION_MAX 4096

#define MEM_COMMIT 0x1000u
#define MEM_RESERVE 0x2000u
#define MEM_RELEASE 0x8000u
#define MEM_FREE 0x10000u
#define CR0_WP 0x10000u

#define MEM_DUMP_CONSOLE 1
#define MEM_DUMP_FIRST_FRAME 2

enum { KIND_VM = 1, KIND_VM_COMMIT, KIND_CONTIGUOUS, KIND_SYSTEM, KIND_POOL, KIND_GPU, KIND_HEAP };

typedef struct {
    u32 kind;
    u32 frames[FRAMES];
    u32 allocs;
    u64 alloc_bytes;
    u32 live_bytes; /* heap sites: scaled for sampling */
    u32 live_blocks;
    u32 peak_bytes;
    u32 failed;
} mem_site;

typedef struct {
    u32 magic;
    u32 version;
    u32 build_id;
    u32 reason;
    u64 session;
    u64 dump_time;
    u32 seq;
    u32 sample_shift;
    u32 frames;
    u32 sites;
    u32 site_record;
    u32 reservations;
    u32 regions;
    u32 untracked;
    u32 unknown_frees;
    u32 exact_used;
    u32 sampled_used;
    u32 own_bytes;
    u32 stack_ok;
    u32 stack_skipped;
    u32 reserved;
    MM_STATISTICS mm;
} mem_header;

typedef struct {
    u32 base;
    u32 size;
    u32 site;
} mem_reservation;

typedef struct {
    u32 base;
    u32 allocation_base;
    u32 size;
    u32 state;
    u32 protect;
    u32 type;
} mem_region;

extern u64 tes3x_boot_time;

static fn_NtAllocateVirtualMemory real_vm_alloc;
static fn_NtFreeVirtualMemory real_vm_free;
static fn_MmAllocateContiguousMemory real_contig;
static fn_MmAllocateContiguousMemoryEx real_contig_ex;
static fn_MmFreeContiguousMemory real_contig_free;
static fn_MmAllocateSystemMemory real_system;
static fn_MmFreeSystemMemoryRet real_system_free;
static fn_ExAllocatePoolWithTag real_pool;
static fn_ExFreePool real_pool_free;
static fn_MmClaimGpuInstanceMemory real_gpu;

static mem_site *sites, *site_copy;
static u32 site_count;
static u32 *exact_keys, *exact_sizes;
static unsigned short *exact_sites;
static u32 *sample_keys, *sample_vals;
static u32 exact_used, sampled_used;
static u32 untracked, unknown_frees, own_bytes, stack_ok, stack_skipped;
static mem_reservation *reservation_copy;
static mem_region *regions;
static u32 dump_seq, dumped_first_frame, ready;
static volatile u32 dump_lock;
static char mem_path[] = "\\Device\\Harddisk0\\Partition1\\tes3xmem.bin";

static inline u32 lock_irq(void)
{
    u32 flags;
    __asm__ volatile("pushfl\n\tpopl %0\n\tcli" : "=r"(flags) : : "memory");
    return flags;
}

static inline void unlock_irq(u32 flags)
{
    __asm__ volatile("pushl %0\n\tpopfl" : : "r"(flags) : "memory", "cc");
}

static inline u32 slot_of(u32 key, u32 bits)
{
    return ((key >> 3) * 0x9E3779B1u) >> (32 - bits);
}

static inline int sampled(u32 ptr)
{
    return ((ptr >> 3) * 0x85EBCA77u) >> (32 - SAMPLE_SHIFT) == 0;
}

static inline u32 page_round(u32 size)
{
    return (size + 0xFFFu) & ~0xFFFu;
}

/* A return address: in .text and just after a call. Other sections can be unloaded. */
static int after_call(u32 v)
{
    const u8 *b = (const u8 *)v;

    if (v < TES3X_MEM_CODE_START + 8 || v >= TES3X_MEM_CODE_END)
        return 0;
    if (b[-5] == 0xE8)
        return 1;
    if (b[-6] == 0xFF && (b[-5] & 0x38) == 0x10)
        return 1;
    if (b[-3] == 0xFF && (b[-2] & 0x38) == 0x10 && (b[-2] & 0xC0) == 0x40)
        return 1;
    return b[-2] == 0xFF && (b[-1] & 0x38) == 0x10 && ((b[-1] & 0xC0) == 0xC0 ||
                                                        (b[-1] & 0xC0) == 0);
}

/* The stack is scanned no further than the thread's stack base, from the kernel's TIB. */
static void capture(u32 *ret, u32 *frames)
{
    u32 *p, *limit, base, n = 1, i;

    frames[0] = *ret;
    for (i = 1; i < FRAMES; i++)
        frames[i] = 0;
    __asm__ volatile("movl %%fs:4, %0" : "=r"(base));
    if (base <= (u32)ret || base - (u32)ret > 0x100000u) {
        stack_skipped++;
        return;
    }
    limit = ret + SCAN_WORDS;
    if ((u32)limit > base)
        limit = (u32 *)base;
    for (p = ret + 1; p < limit && n < FRAMES; p++) {
        if (after_call(*p))
            frames[n++] = *p;
    }
}

static u32 site_hash(u32 kind, const u32 *frames)
{
    u32 h = kind * 0x9E3779B1u, i;
    for (i = 0; i < FRAMES; i++)
        h = (h ^ frames[i]) * 0x85EBCA77u;
    return h >> (32 - SITE_BITS);
}

/* Past three quarters full, new sites keep only their first three frames. */
static u32 site_index(u32 kind, u32 *frames)
{
    u32 i, j;

    if (site_count >= SITE_COUNT / 4 * 3) {
        for (j = 3; j < FRAMES; j++)
            frames[j] = 0;
    }
    for (i = site_hash(kind, frames);; i = (i + 1) & (SITE_COUNT - 1)) {
        mem_site *s = &sites[i];
        if (!s->kind) {
            if (site_count >= SITE_COUNT - 1)
                return SITE_COUNT;
            s->kind = kind;
            for (j = 0; j < FRAMES; j++)
                s->frames[j] = frames[j];
            site_count++;
            return i;
        }
        if (s->kind != kind)
            continue;
        for (j = 0; j < FRAMES && s->frames[j] == frames[j]; j++)
            ;
        if (j == FRAMES)
            return i;
    }
}

static void site_live(u32 site, u32 bytes, u32 blocks, int add)
{
    mem_site *s = &sites[site];
    if (add) {
        s->live_bytes += bytes;
        s->live_blocks += blocks;
        if (s->live_bytes > s->peak_bytes)
            s->peak_bytes = s->live_bytes;
    } else {
        s->live_bytes -= bytes;
        s->live_blocks -= blocks;
    }
}

/* Counts an allocation at its site; returns the site, or SITE_COUNT when the table is full. */
static u32 count(u32 kind, u32 *frames, u32 bytes)
{
    u32 site = site_index(kind, frames);
    if (site == SITE_COUNT) {
        untracked++;
        return site;
    }
    sites[site].allocs++;
    sites[site].alloc_bytes += bytes;
    return site;
}

static void fail(u32 kind, u32 *frames)
{
    u32 flags = lock_irq();
    u32 site = site_index(kind, frames);
    if (site != SITE_COUNT)
        sites[site].failed++;
    unlock_irq(flags);
}

static void exact_add(u32 kind, u32 *frames, u32 key, u32 size)
{
    u32 flags = lock_irq();
    u32 site = count(kind, frames, size), i;

    if (site == SITE_COUNT || exact_used >= EXACT_LIMIT) {
        if (site != SITE_COUNT)
            untracked++;
        unlock_irq(flags);
        return;
    }
    for (i = slot_of(key, EXACT_BITS); exact_keys[i] && exact_keys[i] != key;
         i = (i + 1) & (EXACT_SIZE - 1))
        ;
    if (exact_keys[i] == key) {
        site_live(exact_sites[i], exact_sizes[i], 1, 0);
        exact_used--;
    }
    exact_keys[i] = key;
    exact_sizes[i] = size;
    exact_sites[i] = (unsigned short)site;
    exact_used++;
    site_live(site, size, 1, 1);
    unlock_irq(flags);
}

/* Backward-shift deletion keeps probe chains intact without tombstones. */
static int exact_remove(u32 key, u32 kind)
{
    u32 flags = lock_irq();
    u32 i, j, home;

    for (i = slot_of(key, EXACT_BITS); exact_keys[i] != key; i = (i + 1) & (EXACT_SIZE - 1)) {
        if (!exact_keys[i]) {
            unlock_irq(flags);
            return 0;
        }
    }
    if (sites[exact_sites[i]].kind != kind) {
        unlock_irq(flags);
        return 0;
    }
    site_live(exact_sites[i], exact_sizes[i], 1, 0);
    exact_used--;
    for (j = i;;) {
        j = (j + 1) & (EXACT_SIZE - 1);
        if (!exact_keys[j])
            break;
        home = slot_of(exact_keys[j], EXACT_BITS);
        if (i <= j ? (i < home && home <= j) : (i < home || home <= j))
            continue;
        exact_keys[i] = exact_keys[j];
        exact_sizes[i] = exact_sizes[j];
        exact_sites[i] = exact_sites[j];
        i = j;
    }
    exact_keys[i] = 0;
    unlock_irq(flags);
    return 1;
}

static void sample_add(u32 *frames, u32 key, u32 size)
{
    u32 flags = lock_irq();
    u32 site = count(KIND_HEAP, frames, size), units, i;

    if (site == SITE_COUNT || !sampled(key) || sampled_used >= SAMPLE_LIMIT) {
        if (site != SITE_COUNT && sampled(key))
            untracked++;
        unlock_irq(flags);
        return;
    }
    units = (size + (1u << UNIT_SHIFT) - 1) >> UNIT_SHIFT;
    for (i = slot_of(key, SAMPLE_BITS); sample_keys[i] && sample_keys[i] != key;
         i = (i + 1) & (SAMPLE_SIZE - 1))
        ;
    if (sample_keys[i] == key) {
        u32 old = sample_vals[i];
        site_live(old >> UNIT_BITS, (old & ((1u << UNIT_BITS) - 1)) << (UNIT_SHIFT + SAMPLE_SHIFT),
                  1u << SAMPLE_SHIFT, 0);
        sampled_used--;
    }
    sample_keys[i] = key;
    sample_vals[i] = (site << UNIT_BITS) | units;
    sampled_used++;
    site_live(site, units << (UNIT_SHIFT + SAMPLE_SHIFT), 1u << SAMPLE_SHIFT, 1);
    unlock_irq(flags);
}

static void sample_remove(u32 key)
{
    u32 flags = lock_irq();
    u32 i, j, home, val;

    for (i = slot_of(key, SAMPLE_BITS); sample_keys[i] != key; i = (i + 1) & (SAMPLE_SIZE - 1)) {
        if (!sample_keys[i]) {
            unknown_frees++;
            unlock_irq(flags);
            return;
        }
    }
    val = sample_vals[i];
    site_live(val >> UNIT_BITS, (val & ((1u << UNIT_BITS) - 1)) << (UNIT_SHIFT + SAMPLE_SHIFT),
              1u << SAMPLE_SHIFT, 0);
    sampled_used--;
    for (j = i;;) {
        j = (j + 1) & (SAMPLE_SIZE - 1);
        if (!sample_keys[j])
            break;
        home = slot_of(sample_keys[j], SAMPLE_BITS);
        if (i <= j ? (i < home && home <= j) : (i < home || home <= j))
            continue;
        sample_keys[i] = sample_keys[j];
        sample_vals[i] = sample_vals[j];
        i = j;
    }
    sample_keys[i] = 0;
    unlock_irq(flags);
}

/* The first argument's address less one word is the return address slot. */
#define RET_SLOT(arg) ((u32 *)(void *)&(arg) - 1)

u32 __stdcall tes3x_mem_vm_alloc(void **base, u32 zero, u32 *size, u32 type, u32 protect)
{
    void *want = *base;
    u32 frames[FRAMES], status;

    capture(RET_SLOT(base), frames);
    status = real_vm_alloc(base, zero, size, type, protect);
    if (status) {
        fail(KIND_VM, frames);
    } else if (!want || (type & MEM_RESERVE)) {
        exact_add(KIND_VM, frames, (u32)*base, *size);
    } else {
        u32 flags = lock_irq();
        count(KIND_VM_COMMIT, frames, *size);
        unlock_irq(flags);
    }
    return status;
}

u32 __stdcall tes3x_mem_vm_free(void **base, u32 *size, u32 type)
{
    u32 at = (u32)*base, status = real_vm_free(base, size, type);
    if (!status && (type & MEM_RELEASE))
        exact_remove(at, KIND_VM);
    return status;
}

void *__stdcall tes3x_mem_contig(u32 size)
{
    u32 frames[FRAMES];
    void *ptr;

    capture(RET_SLOT(size), frames);
    ptr = real_contig(size);
    if (ptr)
        exact_add(KIND_CONTIGUOUS, frames, (u32)ptr, page_round(size));
    else
        fail(KIND_CONTIGUOUS, frames);
    return ptr;
}

void *__stdcall tes3x_mem_contig_ex(u32 size, u32 low, u32 high, u32 align, u32 protect)
{
    u32 frames[FRAMES];
    void *ptr;

    capture(RET_SLOT(size), frames);
    ptr = real_contig_ex(size, low, high, align, protect);
    if (ptr)
        exact_add(KIND_CONTIGUOUS, frames, (u32)ptr, page_round(size));
    else
        fail(KIND_CONTIGUOUS, frames);
    return ptr;
}

void __stdcall tes3x_mem_contig_free(void *ptr)
{
    exact_remove((u32)ptr, KIND_CONTIGUOUS);
    real_contig_free(ptr);
}

void *__stdcall tes3x_mem_system(u32 size, u32 protect)
{
    u32 frames[FRAMES];
    void *ptr;

    capture(RET_SLOT(size), frames);
    ptr = real_system(size, protect);
    if (ptr)
        exact_add(KIND_SYSTEM, frames, (u32)ptr, page_round(size));
    else
        fail(KIND_SYSTEM, frames);
    return ptr;
}

u32 __stdcall tes3x_mem_system_free(void *ptr, u32 size)
{
    exact_remove((u32)ptr, KIND_SYSTEM);
    return real_system_free(ptr, size);
}

void *__stdcall tes3x_mem_pool(u32 size, u32 tag)
{
    u32 frames[FRAMES];
    void *ptr;

    capture(RET_SLOT(size), frames);
    ptr = real_pool(size, tag);
    if (ptr)
        exact_add(KIND_POOL, frames, (u32)ptr, size);
    else
        fail(KIND_POOL, frames);
    return ptr;
}

void __stdcall tes3x_mem_pool_free(void *ptr)
{
    exact_remove((u32)ptr, KIND_POOL);
    real_pool_free(ptr);
}

/* The GPU's instance memory is claimed once from the top of RAM and never freed. */
void *__stdcall tes3x_mem_gpu(u32 size, u32 *padding)
{
    u32 frames[FRAMES], flags, site;
    void *ptr;

    capture(RET_SLOT(size), frames);
    ptr = real_gpu(size, padding);
    flags = lock_irq();
    site = count(KIND_GPU, frames, size);
    if (site != SITE_COUNT && ptr)
        site_live(site, size + (padding ? *padding : 0), 1, 1);
    unlock_irq(flags);
    return ptr;
}

void *__stdcall tes3x_mem_heap_alloc(void *heap, u32 flags, u32 size)
{
    void *ptr = ((fn_heap_alloc)TES3X_MEM_HEAP_ALLOC)(heap, flags, size);
    u32 frames[FRAMES];

    if (!ready)
        return ptr;
    capture(RET_SLOT(heap), frames);
    if (!ptr)
        fail(KIND_HEAP, frames);
    else if (size >= BIG_BYTES)
        exact_add(KIND_HEAP, frames, (u32)ptr, size);
    else
        sample_add(frames, (u32)ptr, size);
    return ptr;
}

/* RtlReAllocateHeap is not redirected: a move reaches the two functions above, an in-place
 * resize keeps the block's first size. */
u32 __stdcall tes3x_mem_heap_free(void *heap, u32 flags, void *ptr)
{
    if (ready && ptr && !exact_remove((u32)ptr, KIND_HEAP) && sampled((u32)ptr))
        sample_remove((u32)ptr);
    return ((fn_heap_free)TES3X_MEM_HEAP_FREE)(heap, flags, ptr);
}

static int mem_write(void *h, const void *buf, u32 len)
{
    IO_STATUS_BLOCK iosb;
    u64 append = FILE_WRITE_TO_END_OF_FILE;

    return NtWriteFile(h, 0, 0, 0, &iosb, buf, len, &append) == 0;
}

static u32 walk_regions(void)
{
    MEMORY_BASIC_INFORMATION mbi;
    u32 addr = 0x10000u, next, n = 0;

    while (addr < 0x80000000u && n < REGION_MAX) {
        if (NtQueryVirtualMemory((void *)addr, &mbi) != 0)
            break;
        if (mbi.State != MEM_FREE) {
            regions[n].base = mbi.BaseAddress;
            regions[n].allocation_base = mbi.AllocationBase;
            regions[n].size = mbi.RegionSize;
            regions[n].state = mbi.State;
            regions[n].protect = mbi.Protect;
            regions[n].type = mbi.Type;
            n++;
        }
        next = mbi.BaseAddress + mbi.RegionSize;
        if (next <= addr)
            break;
        addr = next;
    }
    return n;
}

static void mem_dump(u32 reason)
{
    static unsigned short remap[SITE_COUNT];
    ANSI_STRING name;
    OBJECT_ATTRIBUTES oa;
    IO_STATUS_BLOCK iosb;
    mem_header hdr;
    void *h = 0;
    u32 flags, i, n = 0, r = 0;

    if (!ready || __sync_lock_test_and_set(&dump_lock, 1))
        return;

    flags = lock_irq();
    for (i = 0; i < SITE_COUNT; i++) {
        if (sites[i].kind) {
            remap[i] = (unsigned short)n;
            site_copy[n++] = sites[i];
        }
    }
    for (i = 0; i < EXACT_SIZE && r < RESERVATION_MAX; i++) {
        if (exact_keys[i] && sites[exact_sites[i]].kind == KIND_VM) {
            reservation_copy[r].base = exact_keys[i];
            reservation_copy[r].size = exact_sizes[i];
            reservation_copy[r].site = remap[exact_sites[i]];
            r++;
        }
    }
    hdr.untracked = untracked;
    hdr.unknown_frees = unknown_frees;
    hdr.exact_used = exact_used;
    hdr.sampled_used = sampled_used;
    hdr.stack_ok = stack_ok;
    hdr.stack_skipped = stack_skipped;
    unlock_irq(flags);

    hdr.magic = MEM_MAGIC;
    hdr.version = MEM_VERSION;
    hdr.build_id = TES3X_BUILD_ID;
    hdr.reason = reason;
    hdr.session = tes3x_boot_time;
    KeQuerySystemTime(&hdr.dump_time);
    hdr.seq = dump_seq;
    hdr.sample_shift = SAMPLE_SHIFT;
    hdr.frames = FRAMES;
    hdr.sites = n;
    hdr.site_record = sizeof(mem_site);
    hdr.reservations = r;
    hdr.regions = walk_regions();
    hdr.own_bytes = own_bytes;
    hdr.reserved = 0;
    hdr.mm.Length = sizeof(hdr.mm);
    if (MmQueryStatistics(&hdr.mm) != 0)
        hdr.mm.Length = 0;

    tes3x_object_attributes(&oa, &name, mem_path);
    if (NtCreateFile(&h, GENERIC_WRITE | FILE_APPEND_DATA | SYNCHRONIZE, &oa, &iosb, 0,
                     FILE_ATTRIBUTE_NORMAL, FILE_SHARE_READ, FILE_OPEN_IF,
                     FILE_SYNCHRONOUS_IO_NONALERT) != 0) {
        tes3x_log("mem.dump_open_failed", dump_seq);
        dump_lock = 0;
        return;
    }
    if (mem_write(h, &hdr, sizeof(hdr)) && mem_write(h, site_copy, n * sizeof(mem_site)) &&
        mem_write(h, reservation_copy, r * sizeof(mem_reservation)) &&
        mem_write(h, regions, hdr.regions * sizeof(mem_region)))
        dump_seq++;
    NtClose(h);
    tes3x_log("mem.dump", dump_seq);
    tes3x_log("mem.sites", n);
    tes3x_log("mem.regions", hdr.regions);
    if (hdr.untracked)
        tes3x_log("mem.untracked", hdr.untracked);
    dump_lock = 0;
}

static void *own_alloc(u32 bytes)
{
    u32 *p = (u32 *)KFN(THUNK_MmAllocateSystemMemory, fn_MmAllocateSystemMemory)(
        bytes, PAGE_READWRITE);
    u32 i;

    if (p) {
        for (i = 0; i < bytes / 4; i++)
            p[i] = 0;
        own_bytes += bytes;
    }
    return p;
}

static void swap(u32 slot, void *hook, void *save)
{
    *(u32 *)save = *(u32 *)slot;
    *(u32 *)slot = (u32)hook;
}

void tes3x_mem_init(void)
{
    u32 cr0, flags, base, here;

    sites = own_alloc(SITE_COUNT * sizeof(mem_site));
    site_copy = own_alloc(SITE_COUNT * sizeof(mem_site));
    exact_keys = own_alloc(EXACT_SIZE * 4);
    exact_sizes = own_alloc(EXACT_SIZE * 4);
    exact_sites = own_alloc(EXACT_SIZE * 2);
    sample_keys = own_alloc(SAMPLE_SIZE * 4);
    sample_vals = own_alloc(SAMPLE_SIZE * 4);
    reservation_copy = own_alloc(RESERVATION_MAX * sizeof(mem_reservation));
    regions = own_alloc(REGION_MAX * sizeof(mem_region));
    if (!sites || !site_copy || !exact_keys || !exact_sizes || !exact_sites || !sample_keys ||
        !sample_vals || !reservation_copy || !regions) {
        tes3x_log("mem.table_failed", own_bytes / 1024);
        return;
    }

    /* The thunk table is in .rdata; write through the page protection. */
    flags = lock_irq();
    __asm__ volatile("movl %%cr0, %0" : "=r"(cr0));
    __asm__ volatile("movl %0, %%cr0" : : "r"(cr0 & ~CR0_WP) : "memory");
    swap(THUNK_NtAllocateVirtualMemory, tes3x_mem_vm_alloc, &real_vm_alloc);
    swap(THUNK_NtFreeVirtualMemory, tes3x_mem_vm_free, &real_vm_free);
    swap(THUNK_MmAllocateContiguousMemory, tes3x_mem_contig, &real_contig);
    swap(THUNK_MmAllocateContiguousMemoryEx, tes3x_mem_contig_ex, &real_contig_ex);
    swap(THUNK_MmFreeContiguousMemory, tes3x_mem_contig_free, &real_contig_free);
    swap(THUNK_MmAllocateSystemMemory, tes3x_mem_system, &real_system);
    swap(THUNK_MmFreeSystemMemory, tes3x_mem_system_free, &real_system_free);
    swap(THUNK_ExAllocatePoolWithTag, tes3x_mem_pool, &real_pool);
    swap(THUNK_ExFreePool, tes3x_mem_pool_free, &real_pool_free);
    swap(THUNK_MmClaimGpuInstanceMemory, tes3x_mem_gpu, &real_gpu);
    __asm__ volatile("movl %0, %%cr0" : : "r"(cr0) : "memory");
    ready = 1;
    unlock_irq(flags);

    __asm__ volatile("movl %%fs:4, %0\n\tmovl %%esp, %1" : "=r"(base), "=r"(here));
    stack_ok = base > here && base - here <= 0x100000u;
    tes3x_log("mem.table_kb", own_bytes / 1024);
    tes3x_log_hex("mem.stack_base", base);
    tes3x_log_hex("mem.stack_here", here);
}

void tes3x_mem_frame(void)
{
    if (!dumped_first_frame) {
        dumped_first_frame = 1;
        mem_dump(MEM_DUMP_FIRST_FRAME);
    }
}

static int mem_text_equal(const char *a, const char *b)
{
    for (;;) {
        char ca = *a++, cb = *b++;
        if (ca >= 'A' && ca <= 'Z')
            ca += 'a' - 'A';
        if (cb >= 'A' && cb <= 'Z')
            cb += 'a' - 'A';
        if (ca != cb)
            return 0;
        if (!ca)
            return 1;
    }
}

int tes3x_mem_command(const char *text)
{
    if (!mem_text_equal(text, "tes3xmem"))
        return 0;
    mem_dump(MEM_DUMP_CONSOLE);
    return 1;
}
