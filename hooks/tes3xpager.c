/* Demand pager prototype: a reserved region whose pages are committed on first touch, filled
 * from a backing file, and evicted to it under a resident budget.
 *
 * Our #PF gate handles faults inside the region and chains to the kernel's for everything else.
 * It does not wait in the trap: it rewrites the trap frame so that the faulting thread resumes
 * in tes3x_pager_resume, which pages in at the thread's own IRQL, blocking as any caller of
 * NtReadFile may, and returns to the faulting instruction. A fault at raised IRQL or with
 * interrupts off cannot do that; it is counted and chained, so it ends as an access violation.
 * Before either path, the trap records the fault address, saved EIP and allocation group in a
 * fixed ring. `tes3xfaults` drains the ring to the normal log outside the trap.
 *
 * The console command `tes3xpager [runs] [cache] [faults] [reboot]` runs a synthetic workload
 * over the region from two threads of its own, and logs the pager's counters and page-in times
 * in TSC cycles for each run. `cache` uses the title's Z: partition, `faults` drains the fault
 * ring on completion, and `reboot` returns to the dashboard afterwards.
 * Title clients can initialize the pager at entry and allocate inside tes3x_pager_base.
 */

#include "tes3x_thunks.h"
#include "tes3xnt.h"
#include "tes3xlog.h"
#include "tes3xpager.h"

#define NtAllocateVirtualMemory KFN(THUNK_NtAllocateVirtualMemory, fn_NtAllocateVirtualMemory)
#define NtFreeVirtualMemory KFN(THUNK_NtFreeVirtualMemory, fn_NtFreeVirtualMemory)
#define NtCreateFile KFN(THUNK_NtCreateFile, fn_NtCreateFile)
#define NtReadFile KFN(THUNK_NtReadFile, fn_NtReadFile)
#define NtWriteFile KFN(THUNK_NtWriteFile, fn_NtWriteFile)
#define NtSetInformationFile KFN(THUNK_NtSetInformationFile, fn_NtSetInformationFile)
#define NtClose KFN(THUNK_NtClose, fn_NtClose)
#define NtCreateMutant KFN(THUNK_NtCreateMutant, fn_NtCreateMutant)
#define NtReleaseMutant KFN(THUNK_NtReleaseMutant, fn_NtReleaseMutant)
#define NtWaitForSingleObject KFN(THUNK_NtWaitForSingleObject, fn_NtWaitForSingleObject)
#define PsCreateSystemThreadEx KFN(THUNK_PsCreateSystemThreadEx, fn_PsCreateSystemThreadEx)
#define PsTerminateSystemThread KFN(THUNK_PsTerminateSystemThread, fn_PsTerminateSystemThread)
#define KeDelayExecutionThread KFN(THUNK_KeDelayExecutionThread, fn_KeDelayExecutionThread)
#define HalReturnToFirmware KFN(THUNK_HalReturnToFirmware, fn_HalReturnToFirmware)

typedef u32(__stdcall *fn_NtAllocateVirtualMemory)(void **, u32, u32 *, u32, u32);
typedef u32(__stdcall *fn_NtFreeVirtualMemory)(void **, u32 *, u32);
typedef u32(__stdcall *fn_NtCreateMutant)(void **, OBJECT_ATTRIBUTES *, unsigned char);
typedef u32(__stdcall *fn_NtReleaseMutant)(void *, long *);
typedef u32(__stdcall *fn_NtWaitForSingleObject)(void *, unsigned char, long long *);
typedef u32(__stdcall *fn_PsCreateSystemThreadEx)(void **, u32, u32, u32, void **,
                                                  void(__stdcall *)(void *), void *,
                                                  unsigned char, unsigned char, void *);
typedef void(__stdcall *fn_PsTerminateSystemThread)(u32);
typedef u32(__stdcall *fn_KeDelayExecutionThread)(u32, unsigned char, long long *);
typedef void(__stdcall *fn_HalReturnToFirmware)(u32);

#define MEM_COMMIT 0x1000u
#define MEM_RESERVE 0x2000u
#define MEM_DECOMMIT 0x4000u
#define STATUS_TIMEOUT 0x102u
#define HAL_REBOOT_ROUTINE 1u
#define FILE_NO_INTERMEDIATE_BUFFERING 0x08u

#define PAGE 4096u
#define REGION_PAGES 16384u /* 64 MB, larger than the test drive's cache */
#define BUDGET_PAGES 256u  /* 1 MB resident */
#define FAULT_RING_ENTRIES 256u

/* Page tables are self-mapped at 0xC0000000, as on x86 NT. */
#define PTE(va) ((volatile u32 *)(0xC0000000u + (((u32)(va) >> 12) << 2)))
#define PTE_RW 0x002u
#define PTE_ACCESSED 0x020u
#define PTE_DIRTY 0x040u

#define CR0_WP 0x10000u

/* Read by the trap handler. */
u32 tes3x_pager_base, tes3x_pager_size, tes3x_pager_chain, tes3x_pager_broken;
volatile u32 tes3x_pager_violations;

static void *lock_handle, *file;
static u8 resident[REGION_PAGES], stored[REGION_PAGES];
static unsigned short ring[BUDGET_PAGES];
static unsigned short page_group[REGION_PAGES];
static u32 ring_used, hand;
static u32 bounce[PAGE / 4] __attribute__((aligned(4096)));
static u32 n_faults, n_waits, n_zero, n_reads, n_writes, n_clean, n_evicted, n_errors;
static u64 t_pagein, t_read, t_write;
static u32 t_pagein_max;
static u32 installed, runs_wanted, dump_faults_after, reboot_after;
static volatile u32 running;

typedef struct {
    u32 addr, eip, group;
} fault_record;

static fault_record fault_ring[FAULT_RING_ENTRIES], fault_snapshot[FAULT_RING_ENTRIES];
static volatile u32 fault_head, fault_tail, fault_count, fault_dropped, fault_wraps;

void __stdcall tes3x_pager_fault(u32 addr);
void __stdcall tes3x_pager_trace_fault(u32 addr, u32 eip);
static void fault_drain(void);

/* IDT vector 14. The frame on entry is the error code, EIP, CS, EFLAGS: kernel mode, no stack
 * switch. For a fault this pager serves, the frame is rewritten so that iret lands in
 * tes3x_pager_resume with the fault address on top of the stack and the faulting EIP as its
 * return address. */
__attribute__((naked)) void tes3x_pager_trap(void)
{
    __asm__ volatile(
        "pushl %eax\n\t"
        "pushl %ecx\n\t"
        "pushl %edx\n\t"
        "movl %cr2, %eax\n\t"
        "movl %eax, %ecx\n\t"
        "subl _tes3x_pager_base, %ecx\n\t"
        "cmpl _tes3x_pager_size, %ecx\n\t"
        "jae 2f\n\t"
        "cmpl $0, _tes3x_pager_broken\n\t"
        "jne 2f\n\t"
        /* 0 edx, 4 ecx, 8 eax, 12 error, 16 eip, 20 cs, 24 eflags */
        "pushl 16(%esp)\n\t"
        "pushl %eax\n\t"
        "call _tes3x_pager_trace_fault@8\n\t"
        "movl %cr2, %eax\n\t"
        "testl $0x200, 24(%esp)\n\t"
        "jz 1f\n\t"
        "cmpb $0, %fs:0x24\n\t" /* KPCR.Irql */
        "jne 1f\n\t"
        "movl 16(%esp), %ecx\n\t"
        "movl 20(%esp), %edx\n\t"
        "movl %edx, 12(%esp)\n\t"
        "movl 24(%esp), %edx\n\t"
        "movl %edx, 16(%esp)\n\t"
        "movl %eax, 20(%esp)\n\t"
        "movl %ecx, 24(%esp)\n\t"
        "popl %edx\n\t"
        "popl %ecx\n\t"
        "movl (%esp), %eax\n\t"
        "movl $_tes3x_pager_resume, (%esp)\n\t"
        "iretl\n"
        "1:\n\t"
        "lock incl _tes3x_pager_violations\n"
        "2:\n\t"
        "popl %edx\n\t"
        "popl %ecx\n\t"
        "popl %eax\n\t"
        "jmpl *_tes3x_pager_chain\n\t");
}

/* Runs in the faulting thread. The interrupted code may hold live SSE state, so it is saved
 * around the C path. */
__attribute__((naked)) void tes3x_pager_resume(void)
{
    __asm__ volatile(
        "pushfl\n\t"
        "pushal\n\t"
        "movl %esp, %ebx\n\t"
        "subl $512, %esp\n\t"
        "andl $-16, %esp\n\t"
        "fxsave (%esp)\n\t"
        "cld\n\t"
        "pushl 36(%ebx)\n\t"
        "call _tes3x_pager_fault@4\n\t"
        "fxrstor (%esp)\n\t"
        "movl %ebx, %esp\n\t"
        "popal\n\t"
        "popfl\n\t"
        "leal 4(%esp), %esp\n\t"
        "ret\n\t");
}

static inline u64 rdtsc(void)
{
    u32 lo, hi;
    __asm__ volatile("rdtsc" : "=a"(lo), "=d"(hi));
    return (u64)hi << 32 | lo;
}

/* The interrupt gate keeps this single-core machine from observing a partial entry. */
void __stdcall tes3x_pager_trace_fault(u32 addr, u32 eip)
{
    u32 slot = fault_head;
    fault_record *record = &fault_ring[slot];

    record->addr = addr;
    record->eip = eip;
    record->group = page_group[(addr - tes3x_pager_base) / PAGE];
    slot++;
    if (slot == FAULT_RING_ENTRIES) {
        slot = 0;
        fault_wraps++;
    }
    fault_head = slot;
    if (fault_count == FAULT_RING_ENTRIES) {
        fault_tail = (fault_tail + 1) % FAULT_RING_ENTRIES;
        fault_dropped++;
    } else {
        fault_count++;
    }
}

int tes3x_pager_set_group(void *base, u32 size, u32 group)
{
    u32 addr = (u32)base, first, last, page;

    if (!size || group > 0xFFFFu || addr < tes3x_pager_base ||
        addr - tes3x_pager_base >= tes3x_pager_size ||
        size - 1 > tes3x_pager_size - 1 - (addr - tes3x_pager_base))
        return 0;
    first = (addr - tes3x_pager_base) / PAGE;
    last = (addr - tes3x_pager_base + size - 1) / PAGE;
    for (page = first; page <= last; page++)
        page_group[page] = (unsigned short)group;
    return 1;
}

static void copy_page(void *dst, const void *src)
{
    u32 n = PAGE / 4;
    __asm__ volatile("rep movsl" : "+D"(dst), "+S"(src), "+c"(n) : : "memory");
}

static void zero_page(void *dst)
{
    u32 n = PAGE / 4;
    __asm__ volatile("rep stosl" : "+D"(dst), "+c"(n) : "a"(0) : "memory");
}

static void flush_tlb(const void *va)
{
    __asm__ volatile("invlpg (%0)" : : "r"(va) : "memory");
}

static char *page_va(u32 page)
{
    return (char *)(tes3x_pager_base + page * PAGE);
}

static int file_io(int write, u32 page)
{
    IO_STATUS_BLOCK iosb;
    u64 offset = (u64)page * PAGE, t = rdtsc();
    u32 status;

    if (write) {
        status = NtWriteFile(file, 0, 0, 0, &iosb, bounce, PAGE, &offset);
        t_write += rdtsc() - t;
    } else {
        status = NtReadFile(file, 0, 0, 0, &iosb, bounce, PAGE, &offset);
        t_read += rdtsc() - t;
    }
    return status == 0 && iosb.Information == PAGE;
}

static void fail(const char *tag, u32 value)
{
    n_errors++;
    tes3x_pager_broken = 1;
    tes3x_log(tag, value);
}

/* Clock over the resident ring. A page is written back only if its PTE is dirty; it is made
 * read-only first, so a thread that writes it meanwhile faults and waits on the lock. */
static int evict_one(u32 *slot)
{
    u32 page, pte, size;
    void *va;

    for (;;) {
        page = ring[hand];
        va = page_va(page);
        pte = *PTE(va);
        if (!(pte & PTE_ACCESSED))
            break;
        *PTE(va) = pte & ~PTE_ACCESSED;
        flush_tlb(va);
        hand = (hand + 1) % BUDGET_PAGES;
    }
    if (pte & PTE_DIRTY) {
        *PTE(va) = pte & ~PTE_RW;
        flush_tlb(va);
        copy_page(bounce, va);
        if (!file_io(1, page)) {
            fail("pager.write_failed", page);
            return 0;
        }
        stored[page] = 1;
        n_writes++;
    } else {
        n_clean++;
    }
    size = PAGE;
    if (NtFreeVirtualMemory(&va, &size, MEM_DECOMMIT) != 0) {
        fail("pager.decommit_failed", page);
        return 0;
    }
    resident[page] = 0;
    n_evicted++;
    *slot = hand;
    hand = (hand + 1) % BUDGET_PAGES;
    return 1;
}

static void page_in(u32 page)
{
    void *va = page_va(page);
    u32 size = PAGE, slot;

    if (ring_used < BUDGET_PAGES)
        slot = ring_used++;
    else if (!evict_one(&slot))
        return;
    if (NtAllocateVirtualMemory(&va, 0, &size, MEM_COMMIT, PAGE_READWRITE) != 0) {
        fail("pager.commit_failed", page);
        return;
    }
    if (stored[page]) {
        if (!file_io(0, page)) {
            fail("pager.read_failed", page);
            return;
        }
        copy_page(va, bounce);
        n_reads++;
    } else {
        zero_page(va);
        n_zero++;
    }
    *PTE(va) &= ~(PTE_ACCESSED | PTE_DIRTY);
    flush_tlb(va);
    ring[slot] = (unsigned short)page;
    resident[page] = 1;
}

void __stdcall tes3x_pager_fault(u32 addr)
{
    long long now = 0;
    u32 page = (addr - tes3x_pager_base) / PAGE;

    if (NtWaitForSingleObject(lock_handle, 0, &now) == STATUS_TIMEOUT) {
        n_waits++;
        NtWaitForSingleObject(lock_handle, 0, 0);
    }
    n_faults++;
    if (!resident[page] && !tes3x_pager_broken) {
        u64 t = rdtsc();
        u32 dt;

        page_in(page);
        dt = (u32)(rdtsc() - t);
        t_pagein += dt;
        if (dt > t_pagein_max)
            t_pagein_max = dt;
    }
    NtReleaseMutant(lock_handle, 0);
}

static int install(u32 cache_partition)
{
    static char e_path[] = "\\Device\\Harddisk0\\Partition1\\tes3xpage.bin";
    static char z_path[] = "\\Device\\Harddisk0\\Partition5\\tes3xpage.bin";
    char *path = cache_partition ? z_path : e_path;
    ANSI_STRING name;
    OBJECT_ATTRIBUTES oa;
    IO_STATUS_BLOCK iosb;
    struct {
        unsigned short limit;
        u32 base;
    } __attribute__((packed)) idtr;
    u32 size = REGION_PAGES * PAGE, flags, cr0, handler, page;
    u64 eof = (u64)REGION_PAGES * PAGE;
    void *base = 0;
    u8 *gate;

    if (NtCreateMutant(&lock_handle, 0, 0) != 0) {
        tes3x_log("pager.mutant_failed", 0);
        return 0;
    }
    tes3x_object_attributes(&oa, &name, path);
    if (NtCreateFile(&file, GENERIC_READ | GENERIC_WRITE | SYNCHRONIZE, &oa, &iosb, 0,
                     FILE_ATTRIBUTE_NORMAL, 0, FILE_OVERWRITE_IF,
                     FILE_NO_INTERMEDIATE_BUFFERING | FILE_SYNCHRONOUS_IO_NONALERT) != 0) {
        tes3x_log("pager.file_failed", 0);
        return 0;
    }
    if (NtSetInformationFile(file, &iosb, &eof, sizeof(eof), FileEndOfFileInformation) != 0) {
        tes3x_log("pager.file_size_failed", 0);
        return 0;
    }
    if (NtAllocateVirtualMemory(&base, 0, &size, MEM_RESERVE, PAGE_READWRITE) != 0) {
        tes3x_log("pager.reserve_failed", size);
        return 0;
    }
    tes3x_pager_base = (u32)base;
    tes3x_pager_size = size;
    for (page = 0; page < REGION_PAGES; page++)
        page_group[page] = (unsigned short)(page / BUDGET_PAGES + 1);

    /* The IDT lives in kernel data mapped read-only to us; write through WP. */
    __asm__ volatile("sidt %0" : "=m"(idtr));
    gate = (u8 *)(idtr.base + 14 * 8);
    handler = (u32)tes3x_pager_trap;
    __asm__ volatile("pushfl\n\tpopl %0\n\tcli" : "=r"(flags) : : "memory");
    __asm__ volatile("movl %%cr0, %0" : "=r"(cr0));
    __asm__ volatile("movl %0, %%cr0" : : "r"(cr0 & ~CR0_WP) : "memory");
    tes3x_pager_chain = *(unsigned short *)gate | (u32)*(unsigned short *)(gate + 6) << 16;
    *(unsigned short *)gate = (unsigned short)handler;
    *(unsigned short *)(gate + 6) = (unsigned short)(handler >> 16);
    __asm__ volatile("movl %0, %%cr0" : : "r"(cr0) : "memory");
    __asm__ volatile("pushl %0\n\tpopfl" : : "r"(flags) : "memory", "cc");

    tes3x_log_hex("pager.base", tes3x_pager_base);
    tes3x_log("pager.region_kb", size / 1024);
    tes3x_log("pager.budget_kb", BUDGET_PAGES * PAGE / 1024);
    tes3x_log("pager.partition", cache_partition ? 5 : 1);
    tes3x_log_hex("pager.idt", idtr.base);
    tes3x_log_hex("pager.kernel_pf", tes3x_pager_chain);
    tes3x_log_hex("pager.gate_type", gate[5]);
    return 1;
}

int tes3x_pager_init(void)
{
    if (installed)
        return !tes3x_pager_broken;
    if (!install(0))
        return 0;
    installed = 1;
    return 1;
}

int tes3x_pager_contains(const void *ptr)
{
    u32 addr = (u32)ptr;
    return tes3x_pager_base && addr >= tes3x_pager_base &&
           addr - tes3x_pager_base < tes3x_pager_size;
}

/* Synthetic workload. */

static u32 pattern(u32 page, u32 word, u32 pass)
{
    return (page * 0x9E3779B1u) ^ (word * 0x85EBCA77u) ^ (pass * 0xC2B2AE3Du) ^ 0x5A5A5A5Au;
}

static void fill(u32 page, u32 pass)
{
    volatile u32 *p = (volatile u32 *)page_va(page);
    u32 w;

    for (w = 0; w < PAGE / 4; w++)
        p[w] = pattern(page, w, pass);
}

static u32 check(u32 page, u32 pass)
{
    volatile u32 *p = (volatile u32 *)page_va(page);
    u32 w, bad = 0;

    for (w = 0; w < PAGE / 4; w++)
        bad += p[w] != pattern(page, w, pass);
    return bad;
}

typedef struct {
    u32 parity, seed, bad;
    volatile u32 done;
} worker;

/* Rewrite this thread's half of the pages, then read random pages of it back. */
static void run_half(worker *w)
{
    u32 i, x = w->seed, page;

    for (page = w->parity; page < REGION_PAGES; page += 2)
        fill(page, 2);
    for (i = 0; i < 8192; i++) {
        x = x * 1664525u + 1013904223u;
        page = ((x >> 8) % (REGION_PAGES / 2)) * 2 + w->parity;
        w->bad += check(page, 2);
    }
}

static void __stdcall worker_start(void *context)
{
    worker *w = (worker *)context;

    run_half(w);
    w->done = 1;
}

static void __stdcall worker_system(void(__stdcall *start)(void *), void *context)
{
    start(context);
    PsTerminateSystemThread(0);
}

static void log_counters(const char *phase, u32 bad)
{
    tes3x_log(phase, bad);
    tes3x_log("pager.faults", n_faults);
    tes3x_log("pager.waits", n_waits);
    tes3x_log("pager.zero", n_zero);
    tes3x_log("pager.reads", n_reads);
    tes3x_log("pager.writes", n_writes);
    tes3x_log("pager.clean", n_clean);
    tes3x_log("pager.evicted", n_evicted);
    tes3x_log("pager.errors", n_errors);
    tes3x_log("pager.violations", tes3x_pager_violations);
    tes3x_log("pager.pagein_kcycles", (u32)(t_pagein >> 10));
    tes3x_log("pager.pagein_max_kcycles", t_pagein_max >> 10);
    tes3x_log("pager.read_kcycles", (u32)(t_read >> 10));
    tes3x_log("pager.write_kcycles", (u32)(t_write >> 10));
}

/* The log's millisecond stamps on pager.start and pager.test_kcycles give the TSC rate. */
static int run_test(u32 run)
{
    worker a = {0, 12345u, 0, 0}, b = {1, 67890u, 0, 0};
    long long tick = -10 * 10000;
    void *h = 0;
    u32 page, bad = 0;
    u64 start;

    n_faults = n_waits = n_zero = n_reads = n_writes = n_clean = n_evicted = 0;
    t_pagein = t_read = t_write = 0;
    t_pagein_max = 0;
    a.seed += run;
    b.seed += run;
    tes3x_log("pager.start", run);
    start = rdtsc();
    for (page = 0; page < REGION_PAGES; page++)
        fill(page, 1);
    log_counters("pager.fill", 0);

    for (page = 0; page < REGION_PAGES; page++)
        bad += check(page, 1);
    log_counters("pager.verify_bad", bad);

    if (PsCreateSystemThreadEx(&h, 0, 0x4000, 0, 0, worker_start, &b, 0, 0,
                               (void *)worker_system) != 0) {
        tes3x_log("pager.thread_failed", 0);
        return 0;
    }
    NtClose(h);
    run_half(&a);
    while (!b.done)
        KeDelayExecutionThread(0, 0, &tick);
    log_counters("pager.threads_bad", a.bad + b.bad);

    bad = 0;
    for (page = 0; page < REGION_PAGES; page++)
        bad += check(page, 2);
    log_counters("pager.final_bad", bad);
    tes3x_log("pager.test_kcycles", (u32)((rdtsc() - start) >> 10));
    return !tes3x_pager_broken;
}

static void __stdcall run_tests(void *unused)
{
    u32 run;

    (void)unused;
    for (run = 1; run <= runs_wanted && run_test(run); run++)
        ;
    tes3x_log("pager.done", run - 1);
    if (dump_faults_after)
        fault_drain();
    if (reboot_after)
        HalReturnToFirmware(HAL_REBOOT_ROUTINE);
    running = 0;
}

/* Matches a lower-case word at text, case-insensitively; returns the text after it, or 0. */
static const char *word(const char *text, const char *w)
{
    for (; *w; text++, w++) {
        char c = *text;
        if (c >= 'A' && c <= 'Z')
            c += 'a' - 'A';
        if (c != *w)
            return 0;
    }
    return *text == ' ' || !*text ? text : 0;
}

static u32 lock_irq(void)
{
    u32 flags;
    __asm__ volatile("pushfl\n\tpopl %0\n\tcli" : "=r"(flags) : : "memory");
    return flags;
}

static void unlock_irq(u32 flags)
{
    __asm__ volatile("pushl %0\n\tpopfl" : : "r"(flags) : "memory", "cc");
}

static void fault_reset(void)
{
    u32 flags = lock_irq();

    fault_head = fault_tail = fault_count = fault_dropped = fault_wraps = 0;
    unlock_irq(flags);
}

static void fault_drain(void)
{
    u32 flags = lock_irq();
    u32 count = fault_count, dropped = fault_dropped, wraps = fault_wraps;
    u32 i, slot = fault_tail;

    for (i = 0; i < count; i++) {
        fault_snapshot[i] = fault_ring[slot];
        slot = (slot + 1) % FAULT_RING_ENTRIES;
    }
    fault_tail = fault_head;
    fault_count = fault_dropped = fault_wraps = 0;
    unlock_irq(flags);

    tes3x_log("pager.fault_entries", count);
    tes3x_log("pager.fault_dropped", dropped);
    tes3x_log("pager.fault_wraps", wraps);
    for (i = 0; i < count; i++)
        tes3x_log_hex3("pager.fault", fault_snapshot[i].addr,
                       fault_snapshot[i].eip, fault_snapshot[i].group);
    tes3x_log("pager.fault_end", count);
}

int tes3x_pager_command(const char *text)
{
    const char *rest;
    void *h = 0;
    u32 runs = 0, cache_partition = 0, dump_faults = 0, reboot = 0;

    if ((rest = word(text, "tes3xfaults"))) {
        text = rest;
        while (*text == ' ')
            text++;
        if (!*text) {
            fault_drain();
        } else if ((rest = word(text, "reset")) && !*rest) {
            fault_reset();
            tes3x_log("pager.fault_reset", 0);
        } else {
            tes3x_log("pager.fault_usage", 0);
        }
        return 1;
    }
    if (!(text = word(text, "tes3xpager")))
        return 0;
    for (;;) {
        while (*text == ' ')
            text++;
        if (!*text)
            break;
        if ((rest = word(text, "reboot"))) {
            reboot = 1;
            text = rest;
            continue;
        }
        if ((rest = word(text, "cache"))) {
            cache_partition = 1;
            text = rest;
            continue;
        }
        if ((rest = word(text, "faults"))) {
            dump_faults = 1;
            text = rest;
            continue;
        }
        if (*text < '0' || *text > '9') {
            tes3x_log("pager.usage", 0);
            return 1;
        }
        while (*text >= '0' && *text <= '9')
            runs = runs * 10 + (u32)(*text++ - '0');
    }
    if (running) {
        tes3x_log("pager.busy", 0);
        return 1;
    }
    if (!installed) {
        if (!install(cache_partition))
            return 1;
        installed = 1;
    }
    if (tes3x_pager_broken) {
        tes3x_log("pager.broken", n_errors);
        return 1;
    }
    runs_wanted = runs ? runs : 1;
    dump_faults_after = dump_faults;
    reboot_after = reboot;
    running = 1;
    if (PsCreateSystemThreadEx(&h, 0, 0x4000, 0, 0, run_tests, 0, 0, 0,
                               (void *)worker_system) != 0) {
        tes3x_log("pager.thread_failed", 0);
        running = 0;
        return 1;
    }
    NtClose(h);
    return 1;
}
