/* Put the model and icon paths and the display names that record loaders store in the
 * demand-paged region. They are read when a cell instances an object or a menu shows an item. The
 * loaders read a MODL, ITEX or FNAM subrecord and then store it through general::setStringSlot,
 * so the tag that TES3File::getNextSubrecord last returned picks the string's next allocation.
 * Frees of these blocks return at once through link-name-arena's guard at Memory_Heap::Free.
 */

#include "tes3x_thunks.h"
#include "tes3xlog.h"
#include "tes3xpager.h"

#ifndef TES3X_PATH_ALLOCATE
#error "define TES3X_PATH_ALLOCATE to Memory_Heap::Allocate"
#endif

#define ARENA_BYTES (3u * 1024u * 1024u)
#define REPORT_BYTES (512u * 1024u)
#define TAG_MODL 0x4C444F4Du
#define TAG_ITEX 0x58455449u
#define TAG_FNAM 0x4D414E46u

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

/* setStringSlot's allocation. Each tag picks one string, so a later unrelated one cannot follow.
 * Returns 0 to let the engine heap allocate. */
u32 __stdcall tes3x_path_take(u32 size)
{
    u32 tag = tes3x_path_tag, at, bytes = (size + 3) & ~3u;

    tes3x_path_tag = 0;
    if (!arena || (tag != TAG_MODL && tag != TAG_ITEX && tag != TAG_FNAM) || bytes > ARENA_BYTES)
        return 0;
    at = __sync_fetch_and_add(&used, bytes);
    if (at <= ARENA_BYTES - bytes) {
        if (at + bytes - reported >= REPORT_BYTES) {
            reported = at + bytes;
            tes3x_log("paths.arena_kb", reported / 1024);
        }
        return arena + at;
    }
    if (!fallbacks++)
        tes3x_log("paths.full_kb", ARENA_BYTES / 1024);
    return 0;
}

TES3X_OPTIONAL void tes3x_heap_alloc_hook(void);
void (*tes3x_path_census)(void) = tes3x_heap_alloc_hook;

/* Replaces the call to Memory_Heap::Allocate (thiscall, three stack arguments). A heap allocation
 * keeps setStringSlot's return address and goes through the heap census when it is linked, since
 * this call site is no longer the census's. */
__attribute__((naked)) void tes3x_path_arena_allocate(void)
{
    __asm__ volatile(
        "pushl %ecx\n\t"
        "pushl 8(%esp)\n\t"
        "call _tes3x_path_take@4\n\t"
        "popl %ecx\n\t"
        "testl %eax, %eax\n\t"
        "jz 1f\n\t"
        "retl $12\n\t"
        "1:\n\t"
        "movl _tes3x_path_census, %eax\n\t"
        "testl %eax, %eax\n\t"
        "jnz 2f\n\t"
        "movl $" TES3X_SYM(PATH_ALLOCATE) ", %eax\n\t"
        "2:\n\t"
        "jmp *%eax\n\t");
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
