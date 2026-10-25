/* Freestanding XBE payload.
 * Kernel calls use host import thunks emitted by tes3x.inject.
 */

#include "tes3x_thunks.h"
#include "tes3xlog.h"
#include "tes3xdiag.h"
#include "tes3xprof.h"
#include "tes3xheap.h"
#include "tes3xmem.h"

/* Each feature's start is a weak reference: 0 unless its patch is linked in. */
TES3X_OPTIONAL extern volatile u32 tes3x_diag_installed;
TES3X_OPTIONAL void tes3x_diag_init(void);
TES3X_OPTIONAL void tes3x_prof_init(void);
TES3X_OPTIONAL void tes3x_heap_init(void);
TES3X_OPTIONAL void tes3x_mem_init(void);
TES3X_OPTIONAL void tes3x_info_arena_init(void);
TES3X_OPTIONAL void tes3x_link_names_init(void);
TES3X_OPTIONAL void tes3x_overlay_init(void);
TES3X_OPTIONAL void tes3x_frag_probe(void);
TES3X_OPTIONAL void tes3x_console_start(void);
TES3X_OPTIONAL void tes3x_net_entry(void);

/* A thunk slot holds the resolved function pointer once the kernel has fixed up imports. */
#define KFN(slot, type) (*(type *)(slot))

typedef u32(__cdecl *fn_DbgPrint)(const char *, ...);
typedef void(__stdcall *fn_KeQuerySystemTime)(u64 *);
typedef u32(__stdcall *fn_MmQueryStatistics)(void *);

#define DbgPrint KFN(THUNK_DbgPrint, fn_DbgPrint)
#define KeQuerySystemTime KFN(THUNK_KeQuerySystemTime, fn_KeQuerySystemTime)
#define MmQueryStatistics KFN(THUNK_MmQueryStatistics, fn_MmQueryStatistics)

/* MM_STATISTICS, XDK layout. Only the page counts matter here. */
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

u64 tes3x_boot_time;

static u32 tes3x_free_kb(void)
{
    MM_STATISTICS st;
    st.Length = sizeof(st);
    if (MmQueryStatistics(&st) != 0)
        return 0;
    return st.AvailablePages * 4;
}

/* Called with all registers saved; must not disturb the host's startup state. */
void tes3x_init(void)
{
    KeQuerySystemTime(&tes3x_boot_time);
    tes3x_log_prepare();
    DbgPrint("tes3x: hook alive, section at 0x%08x\n", (u32)&tes3x_init);
    tes3x_log("entry.free_kb", tes3x_free_kb());
    if (tes3x_overlay_init)
        tes3x_overlay_init();
    if (tes3x_mem_init)
        tes3x_mem_init();
    if (tes3x_heap_init)
        tes3x_heap_init();
    if (tes3x_info_arena_init)
        tes3x_info_arena_init();
    if (tes3x_link_names_init)
        tes3x_link_names_init();
    if (tes3x_prof_init)
        tes3x_prof_init();
    if (&tes3x_diag_installed && tes3x_diag_installed)
        tes3x_diag_init();
    if (tes3x_frag_probe)
        tes3x_frag_probe();
    if (tes3x_console_start)
        tes3x_console_start();
    if (tes3x_net_entry)
        tes3x_net_entry();
}

__attribute__((naked)) void tes3x_entry(void)
{
    __asm__ volatile(
        "pushal\n\t"
        "pushfl\n\t"
        "call _tes3x_init\n\t"  /* i386 COFF prefixes cdecl symbols with an underscore */
        "popfl\n\t"
        "popal\n\t"
        "pushl $" TES3X_SYM(ORIG_ENTRY) "\n\t"
        "ret\n\t");
}
