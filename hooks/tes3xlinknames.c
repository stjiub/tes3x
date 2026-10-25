/* Put NPCs' five link names and the item IDs of NPC, creature and container inventories in the
 * demand-paged region. The engine keeps both only until it links records after loading, then
 * frees them; Memory_Heap::Free returns at once for a block in the region.
 */

#include "tes3x_thunks.h"
#include "tes3xlog.h"
#include "tes3xpager.h"

#ifndef TES3X_LINK_ZEROED
#error "define TES3X_LINK_ZEROED to Memory_Heap::AllocateZeroed"
#endif
#ifndef TES3X_LINK_TAGGED
#error "define TES3X_LINK_TAGGED to Memory_Heap::AllocateTagged"
#endif
#ifndef TES3X_LINK_FREE_RESUME
#error "define TES3X_LINK_FREE_RESUME to Memory_Heap::Free past its six-byte prologue"
#endif

#define ARENA_BYTES (8u * 1024u * 1024u)
#define REPORT_BYTES (1024u * 1024u)

typedef void *(__cdecl *fn_zeroed)(u32, u32, const char *, u32);
typedef void *(__cdecl *fn_tagged)(u32, const char *, u32);

extern u32 tes3x_pager_base, tes3x_pager_size;

static u32 arena, used, reported, fallbacks;

void tes3x_link_names_init(void)
{
    if (!tes3x_pager_init()) {
        tes3x_log("links.pager_failed", 0);
        return;
    }
    arena = (u32)tes3x_pager_take(ARENA_BYTES);
    if (!arena)
        tes3x_log("links.take_failed", ARENA_BYTES / 1024);
}

/* Fresh region pages read as zero, and nothing is reused, so every block starts zeroed. */
static void *take(u32 size)
{
    u32 at;

    size = (size + 3) & ~3u;
    if (!arena || size > ARENA_BYTES)
        return 0;
    at = __sync_fetch_and_add(&used, size);
    if (at > ARENA_BYTES - size) {
        if (!fallbacks++)
            tes3x_log("links.full_kb", ARENA_BYTES / 1024);
        return 0;
    }
    if (at + size - reported >= REPORT_BYTES) {
        reported = at + size;
        tes3x_log("links.arena_kb", reported / 1024);
    }
    return (void *)(arena + at);
}

void *__cdecl tes3x_link_names_zeroed(u32 count, u32 size, const char *file, u32 line)
{
    void *p = take(count * size);
    return p ? p : ((fn_zeroed)TES3X_LINK_ZEROED)(count, size, file, line);
}

void *__cdecl tes3x_link_names_tagged(u32 size, const char *file, u32 line)
{
    void *p = take(size);
    return p ? p : ((fn_tagged)TES3X_LINK_TAGGED)(size, file, line);
}

/* Replaces Memory_Heap::Free's `push ebp; mov ebp, esp; sub esp, 0x14` (thiscall, one argument). */
__attribute__((naked)) void tes3x_link_names_free(void)
{
    __asm__ volatile(
        "movl 4(%esp), %eax\n\t"
        "subl _tes3x_pager_base, %eax\n\t"
        "cmpl _tes3x_pager_size, %eax\n\t"
        "jb 1f\n\t"
        "pushl %ebp\n\t"
        "movl %esp, %ebp\n\t"
        "subl $0x14, %esp\n\t"
        "pushl $" TES3X_SYM(LINK_FREE_RESUME) "\n\t"
        "retl\n\t"
        "1:\n\t"
        "retl $4\n\t");
}
