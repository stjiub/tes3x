/* Heap census: live Memory_Heap bytes by the source file and line each allocation passes, or by
 * call site where the engine passes none.
 *
 * Every direct call to Memory_Heap::Allocate and ::Free is redirected here. Allocation counts
 * are exact; live blocks are tracked for a fixed quarter of addresses, chosen by hash, so the
 * table fits beside full TR on 128 MB. Snapshots append to E:\tes3xheap.bin and are symbolized
 * on PC against the XBE, which holds the file name strings.
 */

#include "tes3x_thunks.h"
#include "tes3xnt.h"
#include "tes3xlog.h"
#include "tes3xheap.h"

#ifndef TES3X_HEAP_ALLOCATE
#error "define TES3X_HEAP_ALLOCATE to Memory_Heap::Allocate"
#endif
#ifndef TES3X_HEAP_FREE
#error "define TES3X_HEAP_FREE to Memory_Heap::Free"
#endif
#ifndef TES3X_HEAP_OBJECT
#error "define TES3X_HEAP_OBJECT to the global Memory_Heap"
#endif
#ifndef TES3X_BUILD_ID
#define TES3X_BUILD_ID 0
#endif

#define NtCreateFile KFN(THUNK_NtCreateFile, fn_NtCreateFile)
#define NtWriteFile KFN(THUNK_NtWriteFile, fn_NtWriteFile)
#define NtClose KFN(THUNK_NtClose, fn_NtClose)
#define MmAllocateSystemMemory KFN(THUNK_MmAllocateSystemMemory, fn_MmAllocateSystemMemory)
#define MmQueryStatistics KFN(THUNK_MmQueryStatistics, fn_MmQueryStatistics)
#define KeQuerySystemTime KFN(THUNK_KeQuerySystemTime, fn_KeQuerySystemTime)

typedef u32(__stdcall *fn_MmQueryStatistics)(void *);

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

#define HEAP_MAGIC 0x48583354u /* "T3XH" */
#define HEAP_VERSION 2
#define TABLE_BITS 20
#define TABLE_SIZE (1u << TABLE_BITS)
#define TABLE_LIMIT (TABLE_SIZE / 10 * 9)
#define SAMPLE_SHIFT 2
#define SITE_BITS 13
#define SITE_COUNT (1u << SITE_BITS)
#define SIZE_BITS (32 - SITE_BITS)
#define SIZE_MAX_UNITS ((1u << SIZE_BITS) - 1)
#define SIZE_UNIT_SHIFT 3 /* the heap rounds every request up to at least 8 bytes */
#define HEAP_WORDS 24

#define HEAP_DUMP_CONSOLE 1
#define HEAP_DUMP_FIRST_FRAME 2

typedef struct {
    u32 file;
    u32 line;
    u32 caller; /* call site, when the engine passed no line */
    u32 live_bytes;
    u32 live_blocks;
    u32 peak_bytes;
    u32 allocs;
    u32 reserved;
    u64 alloc_bytes;
} heap_site;

typedef struct {
    u32 magic;
    u32 version;
    u32 build_id;
    u32 reason;
    u64 session;
    u64 dump_time;
    u32 seq;
    u32 sample_shift;
    u32 sites;
    u32 record;
    u32 live_blocks; /* sampled */
    u32 live_bytes;  /* sampled */
    u32 peak_bytes;  /* sampled */
    u32 untracked_allocs; /* table or site table full */
    u32 unknown_frees;    /* sampled blocks freed without a record */
    u32 failed_allocs;
    u32 saturated;        /* blocks whose size did not fit the table */
    u32 stale_blocks;     /* reallocated while still live: a free the census missed */
    MM_STATISTICS mm;
    u32 heap_object;
    u32 heap[HEAP_WORDS];
} heap_header;

typedef void *(__thiscall *fn_allocate)(void *, u32, const char *, u32);
typedef void(__thiscall *fn_free)(void *, void *);

extern u64 tes3x_boot_time;

static u32 *table_keys;
static u32 *table_vals;
static heap_site sites[SITE_COUNT];
static u32 site_count;
static u32 live_blocks, live_bytes, peak_bytes;
static u32 untracked_allocs, unknown_frees, failed_allocs, saturated, stale_blocks;
static u32 dump_seq, dumped_first_frame;
static volatile u32 dump_lock;
static char heap_path[] = "\\Device\\Harddisk0\\Partition1\\tes3xheap.bin";

/* Uniprocessor ring 0: masking interrupts is the whole lock, and it cannot invert priority. */
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

static inline u32 slot_of(u32 key)
{
    return ((key >> 3) * 0x9E3779B1u) >> (32 - TABLE_BITS);
}

/* Independent of slot_of, so the sampled quarter spreads over the whole table. */
static inline int sampled(u32 ptr)
{
    return ((ptr >> 3) * 0x85EBCA77u) >> (32 - SAMPLE_SHIFT) == 0;
}

static u32 site_index(u32 file, u32 line, u32 caller)
{
    u32 i = ((file * 0x9E3779B1u) ^ (line * 0x85EBCA77u) ^ (caller * 0xC2B2AE3Du)) >>
            (32 - SITE_BITS);

    /* A site exists from its first allocation on, so allocs == 0 marks an empty slot. */
    for (;;) {
        heap_site *s = &sites[i];
        if (s->allocs && s->file == file && s->line == line && s->caller == caller)
            return i;
        if (!s->allocs) {
            if (site_count >= SITE_COUNT - 1)
                return SITE_COUNT;
            s->file = file;
            s->line = line;
            s->caller = caller;
            site_count++;
            return i;
        }
        i = (i + 1) & (SITE_COUNT - 1);
    }
}

static void forget(u32 val)
{
    heap_site *s = &sites[val >> SIZE_BITS];
    u32 bytes = (val & SIZE_MAX_UNITS) << SIZE_UNIT_SHIFT;

    s->live_blocks--;
    s->live_bytes -= bytes;
    live_blocks--;
    live_bytes -= bytes;
}

static void record_alloc(u32 ptr, u32 size, u32 file, u32 line, u32 caller)
{
    u32 flags = lock_irq();
    u32 units = (size + (1u << SIZE_UNIT_SHIFT) - 1) >> SIZE_UNIT_SHIFT;
    u32 bytes, site, i;
    heap_site *s;

    if (units > SIZE_MAX_UNITS) {
        units = SIZE_MAX_UNITS;
        saturated++;
    }
    bytes = units << SIZE_UNIT_SHIFT;
    site = site_index(file, line, line ? 0 : caller);
    if (site == SITE_COUNT) {
        untracked_allocs++;
        unlock_irq(flags);
        return;
    }
    s = &sites[site];
    s->allocs++;
    s->alloc_bytes += bytes;
    if (!sampled(ptr) || live_blocks >= TABLE_LIMIT) {
        if (sampled(ptr))
            untracked_allocs++;
        unlock_irq(flags);
        return;
    }

    for (i = slot_of(ptr); table_keys[i]; i = (i + 1) & (TABLE_SIZE - 1)) {
        if (table_keys[i] == ptr)
            break;
    }
    /* Still live means its free never reached us; the address now belongs to this block. */
    if (table_keys[i] == ptr) {
        forget(table_vals[i]);
        stale_blocks++;
    }
    s->live_blocks++;
    s->live_bytes += bytes;
    if (s->live_bytes > s->peak_bytes)
        s->peak_bytes = s->live_bytes;
    live_blocks++;
    live_bytes += bytes;
    if (live_bytes > peak_bytes)
        peak_bytes = live_bytes;
    table_keys[i] = ptr;
    table_vals[i] = (site << SIZE_BITS) | units;
    unlock_irq(flags);
}

/* Linear probing with backward-shift deletion, so lookups never need tombstones. */
static void record_free(u32 ptr)
{
    u32 flags = lock_irq();
    u32 i, j, home;

    for (i = slot_of(ptr); table_keys[i] != ptr; i = (i + 1) & (TABLE_SIZE - 1)) {
        if (!table_keys[i]) {
            unknown_frees++;
            unlock_irq(flags);
            return;
        }
    }
    forget(table_vals[i]);

    for (j = i;;) {
        j = (j + 1) & (TABLE_SIZE - 1);
        if (!table_keys[j])
            break;
        home = slot_of(table_keys[j]);
        if (i <= j ? (i < home && home <= j) : (i < home || home <= j))
            continue;
        table_keys[i] = table_keys[j];
        table_vals[i] = table_vals[j];
        i = j;
    }
    table_keys[i] = 0;
    unlock_irq(flags);
}

void *__stdcall tes3x_heap_alloc(u32 caller, void *heap, u32 size, const char *file, u32 line)
{
    void *ptr = ((fn_allocate)TES3X_HEAP_ALLOCATE)(heap, size, file, line);

    if (!table_keys)
        return ptr;
    if (!ptr) {
        failed_allocs++;
        return ptr;
    }
    /* Record after the block is ours, so a concurrent free of the same address cannot
     * interleave with the insert. */
    record_alloc((u32)ptr, size, (u32)file, line, caller);
    return ptr;
}

void __stdcall tes3x_heap_free(void *heap, void *ptr)
{
    /* Forget the block before the heap can hand it out again. */
    if (table_keys && ptr && sampled((u32)ptr))
        record_free((u32)ptr);
    ((fn_free)TES3X_HEAP_FREE)(heap, ptr);
}

/* __thiscall shims: `this` arrives in ecx and the callee pops its arguments. The caller goes
 * first so the original arguments can stay where they are. */
__attribute__((naked)) void tes3x_heap_alloc_hook(void)
{
    __asm__ volatile(
        "popl %eax\n\t"  /* return address, which is also the call site */
        "pushl %ecx\n\t"
        "pushl %eax\n\t"
        "pushl %eax\n\t"
        "jmp _tes3x_heap_alloc@20\n\t");
}

/* In a wrapper that forwards its caller's file and line, the site is one frame up. */
__attribute__((naked)) void tes3x_heap_alloc_wrapped_hook(void)
{
    __asm__ volatile(
        "popl %eax\n\t"
        "pushl %ecx\n\t"
        "pushl 4(%ebp)\n\t"
        "pushl %eax\n\t"
        "jmp _tes3x_heap_alloc@20\n\t");
}

/* Frameless forwarders leave their own return address a fixed distance up the stack:
 * operator new above the three arguments, MemoryPool_Simple::Allocate above four saved
 * registers as well. The offsets are from the stack after `this` is pushed. */
__attribute__((naked)) void tes3x_heap_alloc_new_hook(void)
{
    __asm__ volatile(
        "popl %eax\n\t"
        "pushl %ecx\n\t"
        "pushl 16(%esp)\n\t"
        "pushl %eax\n\t"
        "jmp _tes3x_heap_alloc@20\n\t");
}

__attribute__((naked)) void tes3x_heap_alloc_pool_hook(void)
{
    __asm__ volatile(
        "popl %eax\n\t"
        "pushl %ecx\n\t"
        "pushl 32(%esp)\n\t"
        "pushl %eax\n\t"
        "jmp _tes3x_heap_alloc@20\n\t");
}

__attribute__((naked)) void tes3x_heap_free_hook(void)
{
    __asm__ volatile(
        "popl %eax\n\t"
        "pushl %ecx\n\t"
        "pushl %eax\n\t"
        "jmp _tes3x_heap_free@8\n\t");
}

static int heap_write(void *h, const void *buf, u32 len)
{
    IO_STATUS_BLOCK iosb;
    u64 append = FILE_WRITE_TO_END_OF_FILE;

    return NtWriteFile(h, 0, 0, 0, &iosb, buf, len, &append) == 0;
}

static void heap_dump(u32 reason)
{
    static heap_site copy[SITE_COUNT];
    ANSI_STRING name;
    OBJECT_ATTRIBUTES oa;
    IO_STATUS_BLOCK iosb;
    heap_header hdr;
    void *h = 0;
    u32 flags, i, n = 0;

    if (!table_keys || __sync_lock_test_and_set(&dump_lock, 1))
        return;

    /* Copy under the lock so the snapshot is one consistent instant. */
    flags = lock_irq();
    for (i = 0; i < SITE_COUNT; i++) {
        if (sites[i].allocs)
            copy[n++] = sites[i];
    }
    hdr.live_blocks = live_blocks;
    hdr.live_bytes = live_bytes;
    hdr.peak_bytes = peak_bytes;
    hdr.untracked_allocs = untracked_allocs;
    hdr.unknown_frees = unknown_frees;
    hdr.failed_allocs = failed_allocs;
    hdr.saturated = saturated;
    hdr.stale_blocks = stale_blocks;
    for (i = 0; i < HEAP_WORDS; i++)
        hdr.heap[i] = ((u32 *)TES3X_HEAP_OBJECT)[i];
    unlock_irq(flags);

    hdr.magic = HEAP_MAGIC;
    hdr.version = HEAP_VERSION;
    hdr.build_id = TES3X_BUILD_ID;
    hdr.reason = reason;
    hdr.session = tes3x_boot_time;
    KeQuerySystemTime(&hdr.dump_time);
    hdr.seq = dump_seq;
    hdr.sample_shift = SAMPLE_SHIFT;
    hdr.sites = n;
    hdr.record = sizeof(heap_site);
    hdr.heap_object = TES3X_HEAP_OBJECT;
    hdr.mm.Length = sizeof(hdr.mm);
    if (MmQueryStatistics(&hdr.mm) != 0)
        hdr.mm.Length = 0;

    /* Always append: a title relaunch starts a new session in the same file. */
    tes3x_object_attributes(&oa, &name, heap_path);
    if (NtCreateFile(&h, GENERIC_WRITE | FILE_APPEND_DATA | SYNCHRONIZE, &oa, &iosb, 0,
                     FILE_ATTRIBUTE_NORMAL, FILE_SHARE_READ, FILE_OPEN_IF,
                     FILE_SYNCHRONOUS_IO_NONALERT) != 0) {
        tes3x_log("heap.dump_open_failed", dump_seq);
        dump_lock = 0;
        return;
    }
    if (heap_write(h, &hdr, sizeof(hdr)) && heap_write(h, copy, n * sizeof(heap_site)))
        dump_seq++;
    NtClose(h);
    tes3x_log("heap.dump", dump_seq);
    tes3x_log("heap.live_kb", (hdr.live_bytes >> 10) << SAMPLE_SHIFT);
    tes3x_log("heap.sites", n);
    if (hdr.untracked_allocs)
        tes3x_log("heap.untracked", hdr.untracked_allocs);
    dump_lock = 0;
}

void tes3x_heap_init(void)
{
    table_keys = (u32 *)MmAllocateSystemMemory(TABLE_SIZE * 4, PAGE_READWRITE);
    table_vals = table_keys ? (u32 *)MmAllocateSystemMemory(TABLE_SIZE * 4, PAGE_READWRITE) : 0;
    if (!table_vals) {
        table_keys = 0;
        tes3x_log("heap.table_failed", TABLE_SIZE);
        return;
    }
    tes3x_log("heap.table_kb", TABLE_SIZE * 8 / 1024);
}

void tes3x_heap_frame(void)
{
    if (!dumped_first_frame) {
        dumped_first_frame = 1;
        heap_dump(HEAP_DUMP_FIRST_FRAME);
    }
}

static int heap_text_equal(const char *a, const char *b)
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

int tes3x_heap_command(const char *text)
{
    if (!heap_text_equal(text, "tes3xheap"))
        return 0;
    heap_dump(HEAP_DUMP_CONSOLE);
    return 1;
}
