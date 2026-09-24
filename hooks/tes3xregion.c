/* Reserve the engine heap's region and commit it as the heap grows. The global Memory_Heap
 * takes one block at startup, 17 MB in retail, and carves small allocations from it upwards;
 * beyond it every block is a separate CRT malloc with its own overhead. Retail mallocs the
 * region, which commits all of it at once. Reserving it instead costs only address space, so
 * the region can be sized for the largest load order: [Xbox] HeapRegionKB, 96 MB by default.
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
#ifndef TES3X_REGION_MALLOC
#error "define TES3X_REGION_MALLOC to the heap's CRT malloc wrapper"
#endif
#ifndef TES3X_REGION_FREE
#error "define TES3X_REGION_FREE to the heap's CRT free wrapper"
#endif
#ifndef TES3X_REGION_CARVE
#error "define TES3X_REGION_CARVE to where Allocate carves a block from the region"
#endif
#ifndef TES3X_REGION_SPILL
#error "define TES3X_REGION_SPILL to where Allocate falls back to CRT malloc"
#endif

#define TES3X_STR_(x) #x
#define TES3X_STR(x) TES3X_STR_(x)

#define NtAllocateVirtualMemory KFN(THUNK_NtAllocateVirtualMemory, fn_NtAllocateVirtualMemory)
#define NtFreeVirtualMemory KFN(THUNK_NtFreeVirtualMemory, fn_NtFreeVirtualMemory)

typedef u32(__stdcall *fn_NtAllocateVirtualMemory)(void **, u32, u32 *, u32, u32);
typedef u32(__stdcall *fn_NtFreeVirtualMemory)(void **, u32 *, u32);
typedef int(__cdecl *fn_ini_get_string)(const char *, const char *, const char *,
                                        char *, int, const char *);
typedef void *(__thiscall *fn_heap_malloc)(void *, u32);
typedef void(__thiscall *fn_heap_free)(void *, void *);

#define MEM_COMMIT 0x1000u
#define MEM_RESERVE 0x2000u
#define MEM_RELEASE 0x8000u

#define RETAIL_BYTES 0x1100000u
#define DEFAULT_KB (96u * 1024u)
#define MAX_KB (96u * 1024u)
#define COMMIT_STEP 0x10000u

/* Only touched under the heap's own lock, or before any thread can allocate. */
static char *region;
static u32 reserved, committed, commit_failed;

u32 tes3x_region_size(void)
{
    fn_ini_get_string get = (fn_ini_get_string)TES3X_INI_GET_STRING;
    char buf[16];
    u32 kb = 0;
    int i, set = 0;

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

/* Replaces the constructor's malloc of the region; __fastcall with an unused edx is __thiscall. */
void *__fastcall tes3x_region_reserve(void *heap, void *unused, u32 size)
{
    void *base = 0;
    u32 len = size;

    (void)unused;
    if (NtAllocateVirtualMemory(&base, 0, &len, MEM_RESERVE, PAGE_READWRITE) != 0) {
        tes3x_log("region.reserve_failed", size);
        return ((fn_heap_malloc)TES3X_REGION_MALLOC)(heap, size);
    }
    region = (char *)base;
    reserved = size;
    return base;
}

/* Commits the region up to `end` bytes from its start; zero when memory is exhausted. */
int __stdcall tes3x_region_commit(u32 end)
{
    void *base;
    u32 len, top;

    if (!region || end <= committed)
        return 1;
    top = (end + COMMIT_STEP - 1) & ~(COMMIT_STEP - 1);
    if (top > reserved)
        top = reserved;
    base = region + committed;
    len = top - committed;
    if (NtAllocateVirtualMemory(&base, 0, &len, MEM_COMMIT, PAGE_READWRITE) != 0) {
        if (!commit_failed++)
            tes3x_log("region.commit_failed_kb", committed / 1024);
        return 0;
    }
    committed = top;
    return 1;
}

/* Target of Allocate's `jbe` once a block fits the region: eax holds the block's end offset.
 * Both continuations reload everything from the frame. */
__attribute__((naked)) void tes3x_region_carve_hook(void)
{
    __asm__ volatile(
        "pushl %eax\n\t"
        "call _tes3x_region_commit@4\n\t"
        "testl %eax, %eax\n\t"
        "jz 1f\n\t"
        "pushl $" TES3X_STR(TES3X_REGION_CARVE) "\n\t"
        "ret\n"
        "1:\n\t"
        "pushl $" TES3X_STR(TES3X_REGION_SPILL) "\n\t"
        "ret\n\t");
}

/* Replaces the destructor's free of the region. */
void __fastcall tes3x_region_release(void *heap, void *unused, void *ptr)
{
    u32 len = 0;

    (void)unused;
    if (!region || ptr != region) {
        ((fn_heap_free)TES3X_REGION_FREE)(heap, ptr);
        return;
    }
    NtFreeVirtualMemory(&ptr, &len, MEM_RELEASE);
    region = 0;
    reserved = committed = 0;
}
