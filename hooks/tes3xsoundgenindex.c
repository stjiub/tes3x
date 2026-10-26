/* Find the creature behind an animation's sound keys from a list of creatures, not of every object.
 *
 * parseAnimationTextKeys resolves each SoundGen key (Left, Right, Moan, ...) by walking the whole
 * object list for the first creature whose name matches the animation's, once per key. The
 * creatures are kept here in list order and rebuilt whenever the list's count or tail changes;
 * names are read live, since a later record can replace a creature's strings.
 */

#include "tes3x_thunks.h"
#include "tes3xnt.h"
#include "tes3xlog.h"

#ifndef TES3X_SOUNDGEN_RESOLVE
#error "define TES3X_SOUNDGEN_RESOLVE to the sound generator lookup the walk ends in"
#endif
#ifndef TES3X_SOUNDGEN_STRICMP
#error "define TES3X_SOUNDGEN_STRICMP to the CRT _stricmp the walk calls"
#endif

#define MmAllocateSystemMemory KFN(THUNK_MmAllocateSystemMemory, fn_MmAllocateSystemMemory)
#define MmFreeSystemMemory KFN(THUNK_MmFreeSystemMemory, fn_MmFreeSystemMemory)

#define NDD_OBJECTS 0x18u /* NonDynamicData: {count, head, tail} of every object */
#define LIST_COUNT 0u
#define LIST_HEAD 4u
#define LIST_TAIL 8u
#define OBJ_TYPE 4u
#define OBJ_NEXT 0x20u
#define VT_NAME 0x48u
#define T_CREA 0x41455243u

typedef u32(__attribute__((thiscall)) *fn_resolve)(void *ndd, u32 creature, int key);
typedef const char *(__attribute__((thiscall)) *fn_name)(void *object);
typedef int(__cdecl *fn_stricmp)(const char *a, const char *b);

#define AT(p, off) (*(u32 *)((u32)(p) + (off)))

static u32 *sg_creatures, sg_count, sg_bytes;
static u32 sg_list, sg_list_count, sg_list_tail, sg_builds;
static volatile long sg_busy;

static const char *name_of(u32 object)
{
    return ((fn_name)(*(u32 *)(AT(object, 0) + VT_NAME)))((void *)object);
}

static int matches(u32 object, const char *name)
{
    const char *own = name_of(object);

    return own && !((fn_stricmp)TES3X_SOUNDGEN_STRICMP)(own, name);
}

static int build(u32 list)
{
    u32 object, n = 0, bytes;

    for (object = AT(list, LIST_HEAD); object; object = AT(object, OBJ_NEXT))
        n += AT(object, OBJ_TYPE) == T_CREA;
    bytes = (n * 4 + 0xFFFu) & ~0xFFFu;
    if (bytes > sg_bytes) {
        if (sg_creatures)
            MmFreeSystemMemory(sg_creatures, sg_bytes);
        sg_bytes = 0;
        sg_creatures = (u32 *)MmAllocateSystemMemory(bytes ? bytes : 0x1000u, PAGE_READWRITE);
        if (!sg_creatures)
            return 0;
        sg_bytes = bytes ? bytes : 0x1000u;
    }
    sg_count = 0;
    for (object = AT(list, LIST_HEAD); object; object = AT(object, OBJ_NEXT))
        if (AT(object, OBJ_TYPE) == T_CREA)
            sg_creatures[sg_count++] = object;
    sg_list = list;
    sg_list_count = AT(list, LIST_COUNT);
    sg_list_tail = AT(list, LIST_TAIL);
    if (!sg_builds++)
        tes3x_log("soundgen.creatures", sg_count);
    return 1;
}

/* The engine's walk (0x0010BE90), unchanged. */
static u32 walk(u32 list, const char *name)
{
    u32 object;

    for (object = AT(list, LIST_HEAD); object; object = AT(object, OBJ_NEXT))
        if (AT(object, OBJ_TYPE) == T_CREA && matches(object, name))
            return object;
    return 0;
}

static u32 find(u32 list, const char *name)
{
    u32 i;

    if (list != sg_list || AT(list, LIST_COUNT) != sg_list_count ||
        AT(list, LIST_TAIL) != sg_list_tail) {
        if (!build(list)) {
            sg_list = 0;
            return walk(list, name);
        }
    }
    for (i = 0; i < sg_count; i++)
        if (matches(sg_creatures[i], name))
            return sg_creatures[i];
    return 0;
}

u32 __stdcall tes3x_soundgen_find(void *ndd, const char *name, int key)
{
    u32 list = AT(ndd, NDD_OBJECTS), found;

    if (__sync_lock_test_and_set(&sg_busy, 1)) {
        found = walk(list, name);
    } else {
        found = find(list, name);
        __sync_lock_release(&sg_busy);
    }
    return ((fn_resolve)TES3X_SOUNDGEN_RESOLVE)(ndd, found, key);
}

/* Entered by jmp in place of the walk: __thiscall(NonDynamicData *this, name, key), ret 8. */
__attribute__((naked)) void tes3x_soundgen_find_hook(void)
{
    __asm__ volatile(
        "pushl 0x8(%esp)\n\t"
        "pushl 0x8(%esp)\n\t"
        "pushl %ecx\n\t"
        "call _tes3x_soundgen_find@12\n\t"
        "ret $0x8\n\t");
}
