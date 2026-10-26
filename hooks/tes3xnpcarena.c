/* Put the NPC objects that the record loader creates in the demand-paged region. Play reads the
 * NPCs of loaded cells; the global walks that read every NPC are replaced by ref-index and
 * soundgen-index. The NPC pool is shrunk at patch time, since loading no longer fills it. Blocks
 * the loader frees when a record merges are reused, fed through link-name-arena's guard at
 * Memory_Heap::Free.
 */

#include "tes3x_thunks.h"
#include "tes3xlog.h"
#include "tes3xpager.h"

#ifndef TES3X_NPC_NEW
#error "define TES3X_NPC_NEW to the NPC pool's operator new"
#endif

#define ARENA_BYTES (4u * 1024u * 1024u)
#define REPORT_BYTES (1024u * 1024u)

typedef void *(__cdecl *fn_new)(u32);

static u32 arena, used, reported, fallbacks;
static void *free_list;

void tes3x_npc_arena_init(void)
{
    if (!tes3x_pager_init()) {
        tes3x_log("npcs.pager_failed", 0);
        return;
    }
    arena = (u32)tes3x_pager_take(ARENA_BYTES);
    if (!arena)
        tes3x_log("npcs.take_failed", ARENA_BYTES / 1024);
}

/* The loader runs on one thread, and the frees of its objects come from it too. */
void *__cdecl tes3x_npc_arena_new(u32 size)
{
    u32 at, bytes = (size + 15) & ~15u;
    void *p = free_list;

    if (p) {
        free_list = *(void **)p;
        return p;
    }
    if (arena && bytes <= ARENA_BYTES) {
        at = used;
        if (at <= ARENA_BYTES - bytes) {
            used = at + bytes;
            if (used - reported >= REPORT_BYTES) {
                reported = used;
                tes3x_log("npcs.arena_kb", reported / 1024);
            }
            return (void *)(arena + at);
        }
        if (!fallbacks++)
            tes3x_log("npcs.full_kb", ARENA_BYTES / 1024);
    }
    return ((fn_new)TES3X_NPC_NEW)(size);
}

int tes3x_npc_arena_holds(const void *ptr)
{
    return arena && (u32)ptr - arena < used;
}

void __stdcall tes3x_npc_arena_free(void *ptr)
{
    if (!arena || (u32)ptr - arena >= ARENA_BYTES)
        return;
    *(void **)ptr = free_list;
    free_list = ptr;
}
