/* Put INFO's temporary ID/previous/next names in the demand-paged region.
 * The arena stays valid for the process lifetime while cold pages leave the resident set.
 */

#include "tes3x_thunks.h"
#include "tes3xlog.h"
#include "tes3xpager.h"

#ifndef TES3X_INFO_HEAP_ALLOCATE
#error "define TES3X_INFO_HEAP_ALLOCATE to Memory_Heap::Allocate"
#endif
#ifndef TES3X_INFO_HEAP_FREE
#error "define TES3X_INFO_HEAP_FREE to Memory_Heap::Free"
#endif
#ifndef TES3X_INFO_HEAP_OBJECT
#error "define TES3X_INFO_HEAP_OBJECT to the global Memory_Heap"
#endif
#ifndef TES3X_INFO_NAMES_ALLOCATE
#error "define TES3X_INFO_NAMES_ALLOCATE to the original 3 x 32-byte allocator"
#endif
#ifndef TES3X_INFO_NAMES_FREE
#error "define TES3X_INFO_NAMES_FREE to the original name-buffer free"
#endif
#ifndef TES3X_INFO_CURRENT_TOPIC
#error "define TES3X_INFO_CURRENT_TOPIC to the loader's current DIAL pointer"
#endif
#ifndef TES3X_INFO_LINK_ORIGINAL
#error "define TES3X_INFO_LINK_ORIGINAL to the INFO post-load linker"
#endif
#ifndef TES3X_INFO_FINISH_ORIGINAL
#error "define TES3X_INFO_FINISH_ORIGINAL to the displaced post-pass call"
#endif

#define STR_(x) #x
#define STR(x) STR_(x)
#define PAGE 4096u
#define BLOCK 112u /* owner, three pointers, and three 32-byte names */

typedef void *(__thiscall *fn_heap_allocate)(void *, u32, const char *, u32);
typedef void(__thiscall *fn_heap_free)(void *, void *);
typedef void *(__cdecl *fn_names_allocate)(u32, u32, const char *, u32);
typedef void(__cdecl *fn_names_free)(void *);
typedef void(__thiscall *fn_info_link)(void *, u32);

extern u32 tes3x_pager_base, tes3x_pager_size;

static u32 used, topic, group, allocations, fallbacks;

void tes3x_info_arena_init(void)
{
    if (!tes3x_pager_init())
        tes3x_log("info.pager_failed", 0);
}

static __attribute__((used, noinline)) void *__stdcall
table_allocate(void *owner, void *heap, u32 size, const char *file, u32 line)
{
    u32 current = *(u32 *)TES3X_INFO_CURRENT_TOPIC;
    u32 at;

    if (!tes3x_pager_base || size != 12)
        return ((fn_heap_allocate)TES3X_INFO_HEAP_ALLOCATE)(heap, size, file, line);
    if (current != topic) {
        used = (used + PAGE - 1) & ~(PAGE - 1);
        topic = current;
        group++;
    }
    at = tes3x_pager_base + used;
    if (group > 0xFFFFu || used > tes3x_pager_size - BLOCK ||
        !tes3x_pager_set_group((void *)at, BLOCK, group)) {
        fallbacks++;
        return ((fn_heap_allocate)TES3X_INFO_HEAP_ALLOCATE)(heap, size, file, line);
    }
    *(u32 *)at = (u32)owner;
    used += BLOCK;
    allocations++;
    return (void *)(at + 4);
}

__attribute__((naked)) void tes3x_info_table_allocate_hook(void)
{
    __asm__ volatile(
        "pushl 12(%esp)\n\t"
        "pushl 12(%esp)\n\t"
        "pushl 12(%esp)\n\t"
        "pushl %ecx\n\t"
        "pushl %esi\n\t"
        "call _table_allocate@20\n\t"
        "retl $12\n\t");
}

static __attribute__((used, noinline)) void *__stdcall names_from_table(void *table)
{
    if (!tes3x_pager_contains(table))
        return 0;
    return (char *)table + 12;
}

__attribute__((naked)) void tes3x_info_names_allocate_hook(void)
{
    __asm__ volatile(
        "pushl 16(%esi)\n\t"
        "call _names_from_table@4\n\t"
        "testl %eax, %eax\n\t"
        "jz 1f\n\t"
        "retl\n\t"
        "1:\n\t"
        "pushl $" STR(TES3X_INFO_NAMES_ALLOCATE) "\n\t"
        "retl\n\t");
}

void __cdecl tes3x_info_names_free(void *ptr)
{
    if (!tes3x_pager_contains(ptr))
        ((fn_names_free)TES3X_INFO_NAMES_FREE)(ptr);
}

void __stdcall tes3x_info_table_free(void *ptr)
{
    if (!tes3x_pager_contains(ptr))
        ((fn_heap_free)TES3X_INFO_HEAP_FREE)((void *)TES3X_INFO_HEAP_OBJECT, ptr);
}

static __attribute__((used, noinline)) void __stdcall cleanup_topic(void *topic)
{
    u32 info = *(u32 *)((char *)topic + 0x18);

    while (info) {
        u32 next = *(u32 *)(info + 0x1C);
        void *table;

        ((fn_info_link)TES3X_INFO_LINK_ORIGINAL)((void *)info, 1);
        table = *(void **)(info + 0x10);
        if (table) {
            if (!tes3x_pager_contains(table)) {
                ((fn_names_free)TES3X_INFO_NAMES_FREE)(*(void **)table);
                ((u32 *)table)[0] = 0;
                ((u32 *)table)[1] = 0;
                ((u32 *)table)[2] = 0;
                ((fn_heap_free)TES3X_INFO_HEAP_FREE)(
                    (void *)TES3X_INFO_HEAP_OBJECT, table);
            }
            *(u32 *)(info + 0x10) = 0;
        }
        info = next;
    }
}

__attribute__((naked)) void tes3x_info_cleanup_hook(void)
{
    __asm__ volatile(
        "pushl %ecx\n\t"
        "call _cleanup_topic@4\n\t"
        "retl $4\n\t");
}

static __attribute__((used, noinline)) void info_finish(void)
{
    tes3x_log("info.allocations", allocations);
    tes3x_log("info.groups", group);
    tes3x_log("info.arena_kb", (used + 1023) / 1024);
    tes3x_log("info.fallbacks", fallbacks);
}

__attribute__((naked)) void tes3x_info_finish_hook(void)
{
    __asm__ volatile(
        "pushfl\n\t"
        "pushal\n\t"
        "call _info_finish\n\t"
        "popal\n\t"
        "popfl\n\t"
        "pushl $" STR(TES3X_INFO_FINISH_ORIGINAL) "\n\t"
        "retl\n\t");
}
