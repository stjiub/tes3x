/* Put the model and icon paths that record loaders store in the demand-paged region. They are read
 * when a cell instances an object or a menu shows an icon. The loaders read a MODL or ITEX
 * subrecord and then store it through general::setStringSlot, so the tag that
 * TES3File::getNextSubrecord last returned picks the string's next allocation. Frees of these
 * blocks return at once through link-name-arena's guard at Memory_Heap::Free.
 */

#include "tes3x_thunks.h"
#include "tes3xlog.h"
#include "tes3xpager.h"

#ifndef TES3X_PATH_ALLOCATE
#error "define TES3X_PATH_ALLOCATE to Memory_Heap::Allocate"
#endif

#define ARENA_BYTES (2u * 1024u * 1024u)
#define REPORT_BYTES (512u * 1024u)
#define TAG_MODL 0x4C444F4Du
#define TAG_ITEX 0x58455449u

typedef void *(__thiscall *fn_allocate)(void *, u32, const char *, u32);

static u32 arena, used, reported, fallbacks;
u32 tes3x_path_tag;

void tes3x_path_arena_init(void)
{
    if (!tes3x_pager_init()) {
        tes3x_log("paths.pager_failed", 0);
        return;
    }
    arena = (u32)tes3x_pager_take(ARENA_BYTES);
    if (!arena)
        tes3x_log("paths.take_failed", ARENA_BYTES / 1024);
}

/* setStringSlot's allocation. Each tag picks one string, so a later unrelated one cannot follow. */
void *__stdcall tes3x_path_allocate(void *heap, u32 size, const char *file, u32 line)
{
    u32 tag = tes3x_path_tag, at, bytes = (size + 3) & ~3u;

    tes3x_path_tag = 0;
    if (arena && (tag == TAG_MODL || tag == TAG_ITEX) && bytes <= ARENA_BYTES) {
        at = __sync_fetch_and_add(&used, bytes);
        if (at <= ARENA_BYTES - bytes) {
            if (at + bytes - reported >= REPORT_BYTES) {
                reported = at + bytes;
                tes3x_log("paths.arena_kb", reported / 1024);
            }
            return (void *)(arena + at);
        }
        if (!fallbacks++)
            tes3x_log("paths.full_kb", ARENA_BYTES / 1024);
    }
    return ((fn_allocate)TES3X_PATH_ALLOCATE)(heap, size, file, line);
}

__attribute__((naked)) void tes3x_path_arena_allocate(void)
{
    __asm__ volatile(
        "popl %eax\n\t"
        "pushl %ecx\n\t"
        "pushl %eax\n\t"
        "jmp _tes3x_path_allocate@16\n\t");
}

/* Replaces getNextSubrecord's `mov eax, [esi]; pop esi; ret` (esi = &file->tag). */
__attribute__((naked)) void tes3x_path_arena_tag(void)
{
    __asm__ volatile(
        "movl (%esi), %eax\n\t"
        "movl %eax, _tes3x_path_tag\n\t"
        "popl %esi\n\t"
        "retl\n\t");
}
