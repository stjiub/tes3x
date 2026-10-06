/* Size the video-memory arena from physical memory. Every texture and vertex buffer comes from one
 * contiguous arena of a hardcoded 0xF80000 bytes; once it is full, each further block becomes its
 * own page-rounded physical allocation from general memory. With more than 64 MB, take the size
 * from [Xbox] VideoMemoryKB instead; on 64 MB the arena stays retail's size.
 */

#include "tes3x_thunks.h"
#include "tes3xnt.h"
#include "tes3xlog.h"
#include "tes3xini.h"

#ifndef TES3X_INI_GET_STRING
#error "define TES3X_INI_GET_STRING to the VA of the ini string reader"
#endif
#ifndef TES3X_INI_PATH
#error "define TES3X_INI_PATH to the VA of the engine's ini filename string"
#endif

#define MmQueryStatistics KFN(THUNK_MmQueryStatistics, fn_MmQueryStatistics)

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

#define RETAIL_BYTES 0xF80000u
#define DEFAULT_KB (22u * 1024u)
#define MAX_KB (48u * 1024u)
#define PAGES_64MB (64u * 1024u * 1024u / 4096u)

u32 tes3x_arena_size(void)
{
    MM_STATISTICS st;
    char buf[16];
    u32 kb = 0;
    int i, set = 0;

    st.Length = sizeof(st);
    if (MmQueryStatistics(&st) != 0 || st.TotalPhysicalPages <= PAGES_64MB) {
        tes3x_log("arena.kb", RETAIL_BYTES / 1024);
        return RETAIL_BYTES;
    }
    tes3x_ini_xbox("VideoMemoryKB", "", buf, sizeof(buf));
    for (i = 0; buf[i] >= '0' && buf[i] <= '9'; i++, set = 1)
        kb = kb * 10 + (u32)(buf[i] - '0');
    if (!set)
        kb = DEFAULT_KB;
    if (kb * 1024 < RETAIL_BYTES)
        kb = RETAIL_BYTES / 1024;
    if (kb > MAX_KB)
        kb = MAX_KB;
    tes3x_log(set ? "arena.kb_ini" : "arena.kb", kb);
    return kb * 1024;
}

/* Replaces `mov ebx, 0xF80000` just before the arena constructor: eax holds the new heap object
 * and must survive; the size goes in ebx. */
__attribute__((naked)) void tes3x_arena_size_hook(void)
{
    __asm__ volatile(
        "pushl %eax\n\t"
        "pushl %ecx\n\t"
        "pushl %edx\n\t"
        "call _tes3x_arena_size\n\t"
        "movl %eax, %ebx\n\t"
        "popl %edx\n\t"
        "popl %ecx\n\t"
        "popl %eax\n\t"
        "ret\n\t");
}
