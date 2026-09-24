/* Freestanding XBE payload.
 * Kernel calls use host import thunks emitted by tes3x_inject.py.
 */

#include "tes3x_thunks.h"
#include "tes3xlog.h"
#ifdef TES3X_DIAGNOSTICS
#include "tes3xdiag.h"
#endif
#ifdef TES3X_PROFILE
#include "tes3xprof.h"
#endif
#ifdef TES3X_HEAP
#include "tes3xheap.h"
#endif
#ifdef TES3X_MEM
#include "tes3xmem.h"
#endif

#ifndef TES3X_ORIG_ENTRY
#error "define TES3X_ORIG_ENTRY to the XBE's original entry point"
#endif

#define TES3X_STR_(x) #x
#define TES3X_STR(x) TES3X_STR_(x)

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

#ifdef TES3X_FRAG_PROBE
void tes3x_frag_probe(void);
#endif
#ifdef TES3X_CONSOLE
void tes3x_console_start(void);
#endif

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
#ifdef TES3X_MEM
    tes3x_mem_init();
#endif
#ifdef TES3X_HEAP
    tes3x_heap_init();
#endif
#ifdef TES3X_PROFILE
    tes3x_prof_init();
#endif
#ifdef TES3X_DIAGNOSTICS
    if (tes3x_diag_installed)
        tes3x_diag_init();
#endif
#ifdef TES3X_FRAG_PROBE
    tes3x_frag_probe();
#endif
#ifdef TES3X_CONSOLE
    tes3x_console_start();
#endif
}

__attribute__((naked)) void tes3x_entry(void)
{
    __asm__ volatile(
        "pushal\n\t"
        "pushfl\n\t"
        "call _tes3x_init\n\t"  /* i386 COFF prefixes cdecl symbols with an underscore */
        "popfl\n\t"
        "popal\n\t"
        "pushl $" TES3X_STR(TES3X_ORIG_ENTRY) "\n\t"
        "ret\n\t");
}
