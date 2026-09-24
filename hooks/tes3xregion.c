/* Size the engine heap's region from physical memory. The global Memory_Heap takes one fixed
 * 17 MB block at startup and commits all of it; beyond it every block is a separate CRT malloc
 * with its own overhead. With more than 64 MB, take the size from [Xbox] HeapRegionKB instead;
 * on 64 MB the region stays retail's size.
 */

#include "tes3x_thunks.h"
#include "tes3xnt.h"
#include "tes3xlog.h"

#ifndef TES3X_INI_GET_STRING
#error "define TES3X_INI_GET_STRING to the VA of the ini string reader"
#endif
#ifndef TES3X_INI_PATH
#error "define TES3X_INI_PATH to the VA of the engine's ini filename string"
#endif

#define MmQueryStatistics KFN(THUNK_MmQueryStatistics, fn_MmQueryStatistics)

typedef u32(__stdcall *fn_MmQueryStatistics)(void *);
typedef int(__cdecl *fn_ini_get_string)(const char *, const char *, const char *,
                                        char *, int, const char *);

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

#define RETAIL_BYTES 0x1100000u
#define DEFAULT_KB (56u * 1024u)
#define MAX_KB (96u * 1024u)
#define PAGES_64MB (64u * 1024u * 1024u / 4096u)

u32 tes3x_region_size(void)
{
    fn_ini_get_string get = (fn_ini_get_string)TES3X_INI_GET_STRING;
    MM_STATISTICS st;
    char buf[16];
    u32 kb = 0;
    int i, set = 0;

    st.Length = sizeof(st);
    if (MmQueryStatistics(&st) != 0 || st.TotalPhysicalPages <= PAGES_64MB) {
        tes3x_log("region.kb", RETAIL_BYTES / 1024);
        return RETAIL_BYTES;
    }
    for (i = 0; i < (int)sizeof(buf); i++)
        buf[i] = 0;
    get("Xbox", "HeapRegionKB", "", buf, (int)sizeof(buf) - 1, (const char *)TES3X_INI_PATH);
    for (i = 0; buf[i] >= '0' && buf[i] <= '9'; i++, set = 1)
        kb = kb * 10 + (u32)(buf[i] - '0');
    if (!set)
        kb = DEFAULT_KB;
    if (kb > MAX_KB)
        kb = MAX_KB;
    if (kb < RETAIL_BYTES / 1024)
        kb = RETAIL_BYTES / 1024;
    tes3x_log(set ? "region.kb_ini" : "region.kb", kb);
    return kb * 1024;
}

/* Replaces the heap initializer's `push 0x1100000`: leaves the size where the push would have. */
__attribute__((naked)) void tes3x_region_size_hook(void)
{
    __asm__ volatile(
        "call _tes3x_region_size\n\t"
        "xchgl %eax, (%esp)\n\t"
        "jmpl *%eax\n\t");
}
