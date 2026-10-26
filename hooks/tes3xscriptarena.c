/* Put script bytecode in the demand-paged region. Only the interpreter reads it, on the thread
 * running the script. Frees of these blocks return at once through link-name-arena's guard at
 * Memory_Heap::Free.
 */

#include "tes3x_thunks.h"
#include "tes3xlog.h"
#include "tes3xpager.h"

#ifndef TES3X_SCRIPT_ZEROED
#error "define TES3X_SCRIPT_ZEROED to Memory_Heap::AllocateZeroed"
#endif

#define ARENA_BYTES (4u * 1024u * 1024u)
#define REPORT_BYTES (1024u * 1024u)

typedef void *(__cdecl *fn_zeroed)(u32, u32, const char *, u32);

static u32 arena, used, reported, fallbacks;

void tes3x_script_arena_init(void)
{
    if (!tes3x_pager_init()) {
        tes3x_log("scripts.pager_failed", 0);
        return;
    }
    arena = (u32)tes3x_pager_take(ARENA_BYTES);
    if (!arena)
        tes3x_log("scripts.take_failed", ARENA_BYTES / 1024);
}

/* Fresh region pages read as zero, and nothing is reused, so every block starts zeroed. */
void *__cdecl tes3x_script_arena_zeroed(u32 count, u32 size, const char *file, u32 line)
{
    u32 at, bytes = (count * size + 3) & ~3u;

    if (arena && bytes <= ARENA_BYTES) {
        at = __sync_fetch_and_add(&used, bytes);
        if (at <= ARENA_BYTES - bytes) {
            if (at + bytes - reported >= REPORT_BYTES) {
                reported = at + bytes;
                tes3x_log("scripts.arena_kb", reported / 1024);
            }
            return (void *)(arena + at);
        }
        if (!fallbacks++)
            tes3x_log("scripts.full_kb", ARENA_BYTES / 1024);
    }
    return ((fn_zeroed)TES3X_SCRIPT_ZEROED)(count, size, file, line);
}
