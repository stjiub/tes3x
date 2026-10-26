/* Find the creature behind an animation's sound keys from a list of creatures, not of every object.
 *
 * parseAnimationTextKeys resolves each SoundGen key (Left, Right, Moan, ...) by walking the whole
 * object list for the first creature whose name matches the animation's, once per key. The
 * creatures are kept here in list order, extended when objects were only appended and rebuilt
 * after any other change; names are read live, since a later record can replace a creature's
 * strings.
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
#define OBJ_PREV 0x1Cu
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

static int add(u32 object)
{
    u32 bytes, *grown, i;

    if (AT(object, OBJ_TYPE) != T_CREA)
        return 1;
    if ((sg_count + 1) * 4 > sg_bytes) {
        bytes = ((sg_count + 1) * 8 + 0xFFFu) & ~0xFFFu;
        grown = (u32 *)MmAllocateSystemMemory(bytes, PAGE_READWRITE);
        if (!grown)
            return 0;
        for (i = 0; i < sg_count; i++)
            grown[i] = sg_creatures[i];
        if (sg_creatures)
            MmFreeSystemMemory(sg_creatures, sg_bytes);
        sg_creatures = grown;
        sg_bytes = bytes;
    }
    sg_creatures[sg_count++] = object;
    return 1;
}

static void keep(u32 list)
{
    sg_list = list;
    sg_list_count = AT(list, LIST_COUNT);
    sg_list_tail = AT(list, LIST_TAIL);
}

/* Every object's links are inside it, so a full walk reads every object, paged ones included. */
static int build(u32 list)
{
    u32 object;

    sg_count = 0;
    for (object = AT(list, LIST_HEAD); object; object = AT(object, OBJ_NEXT))
        if (!add(object))
            return 0;
    keep(list);
    sg_builds++;
    if (!(sg_builds & (sg_builds - 1)))
        tes3x_log("soundgen.builds", sg_builds);
    if (sg_builds == 1)
        tes3x_log("soundgen.creatures", sg_count);
    return 1;
}

/* Objects only appended since the last look: stepping back by the count's growth from the tail
 * lands on the old tail, compared by value, so a removed old tail is never read. Only the new
 * objects are read. */
static int extend(u32 list)
{
    u32 grown, object = AT(list, LIST_TAIL), i;

    if (list != sg_list || AT(list, LIST_COUNT) <= sg_list_count)
        return 0;
    grown = AT(list, LIST_COUNT) - sg_list_count;
    for (i = 0; i < grown; i++) {
        if (!object)
            return 0;
        object = AT(object, OBJ_PREV);
    }
    if (object != sg_list_tail)
        return 0;
    for (object = object ? AT(object, OBJ_NEXT) : AT(list, LIST_HEAD); object;
         object = AT(object, OBJ_NEXT))
        if (!add(object))
            return 0;
    keep(list);
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
        if (!extend(list) && !build(list)) {
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
