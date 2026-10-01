/* Answer "first reference to this object in any cell" from an index while global scripts start.
 *
 * Global script startup resolves every object its scripts name through a walk of every cell, one
 * walk per name: 2,559 walks of 2,895 cells on a 34-master load list.  While startGlobalScripts
 * runs, the references cannot change, so one pass over the cells records each object's answer.
 * The answer is the walk's: in cell order, then list order, the first reference that is not
 * deleted, or failing that the first reference.
 */

#include "tes3x_thunks.h"
#include "tes3xnt.h"
#include "tes3xlog.h"

#ifndef TES3X_REFINDEX_FIND_IN_CELL
#error "define TES3X_REFINDEX_FIND_IN_CELL to Cell::findReferenceToObject"
#endif
#ifndef TES3X_REFINDEX_START_SCRIPTS
#error "define TES3X_REFINDEX_START_SCRIPTS to WorldController::startGlobalScripts"
#endif

#define TES3X_STR_(x) #x
#define TES3X_STR(x) TES3X_STR_(x)

#define MmAllocateSystemMemory KFN(THUNK_MmAllocateSystemMemory, fn_MmAllocateSystemMemory)
#define MmFreeSystemMemory KFN(THUNK_MmFreeSystemMemory, fn_MmFreeSystemMemory)

#define CELL_LIST 0xB270u   /* NonDynamicData: list of every cell */
#define CELL_ACTORS 0x2Cu   /* creature and NPC references */
#define CELL_OBJECTS 0x3Cu  /* everything else */
#define CELL_EXTRA 0x10u    /* optional holder of a second object list at +8 */
#define LIST_HEAD 4u
#define NODE_NEXT 8u
#define REF_FLAGS 8u
#define REF_NEXT 0x20u
#define REF_BASE 0x28u
#define REF_DELETED 0x20u
#define OBJ_TYPE 4u
#define OBJ_FLAGS 0x34u
#define VT_ORIGINAL 0x154u

#define T_CREA 0x41455243u
#define T_NPC 0x5F43504Eu
#define T_CREC 0x43455243u
#define T_NPCC 0x4343504Eu
#define T_CONT 0x544E4F43u
#define T_CNTC 0x43544E43u

#define LIVE 1u /* tag on a stored reference: it is not deleted */

typedef void *(__attribute__((thiscall)) *fn_find_in_cell)(void *cell, void *object, int live);
typedef void *(__attribute__((thiscall)) *fn_original)(void *object);
typedef void(__attribute__((thiscall)) *fn_start_scripts)(void *world);

#define AT(p, off) (*(u32 *)((u32)(p) + (off)))

static int ri_active;
static u32 *ri_table; /* key, value pairs; value is a reference, tagged LIVE */
static u32 ri_mask, ri_shift, ri_bytes, ri_keys, ri_lookups, ri_failed;
#ifdef TES3X_REFINDEX_VERIFY
static u32 ri_mismatch;
#endif

static int is_actor(u32 type)
{
    return type == T_CREA || type == T_NPC || type == T_CREC || type == T_NPCC;
}

/* The engine's own test (0x00131320) for a base object that stands in for another. */
static int is_instance(void *base)
{
    u32 type = AT(base, OBJ_TYPE);

    if (type == T_CNTC || type == T_NPCC || type == T_CREC)
        return 1;
    if (type == T_CREA || type == T_CONT || type == T_NPC)
        return !((*(unsigned char *)((u32)base + OBJ_FLAGS) >> 3) & 1);
    return 0;
}

static u32 *slot(u32 key)
{
    /* The product's high bits: its low bits repeat for pointers allocated at a fixed stride. */
    u32 i = (key * 2654435761u) >> ri_shift;

    while (ri_table[i * 2] && ri_table[i * 2] != key)
        i = (i + 1) & ri_mask;
    return &ri_table[i * 2];
}

static void record(u32 key, u32 ref, int actors)
{
    u32 *s;
    u32 live = (AT(ref, REF_FLAGS) & REF_DELETED) ? 0 : LIVE;

    if (!key || is_actor(AT(key, OBJ_TYPE)) != actors)
        return;
    s = slot(key);
    if (!s[0]) {
        s[0] = key;
        s[1] = ref | live;
        ri_keys++;
    } else if (!(s[1] & LIVE) && live) {
        s[1] = ref | live;
    }
}

static void index_list(u32 list, int actors)
{
    u32 ref, base;

    if (!list)
        return;
    for (ref = AT(list, LIST_HEAD); ref; ref = AT(ref, REF_NEXT)) {
        base = AT(ref, REF_BASE);
        if (!base)
            continue;
        record(base, ref, actors);
        if (is_instance((void *)base))
            record((u32)((fn_original)(*(u32 *)(AT(base, 0) + VT_ORIGINAL)))((void *)base), ref,
                   actors);
    }
}

/* An upper bound on the keys a list adds: its base objects and the originals they stand in for. */
static u32 count_list(u32 list)
{
    u32 ref, base, n = 0;

    if (list)
        for (ref = AT(list, LIST_HEAD); ref; ref = AT(ref, REF_NEXT)) {
            base = AT(ref, REF_BASE);
            if (base)
                n += 1 + is_instance((void *)base);
        }
    return n;
}

static void build(void *ndd)
{
    u32 node, cell, extra, want = 0, cap = 1024, shift = 22;

    for (node = AT(AT(ndd, CELL_LIST), LIST_HEAD); node; node = AT(node, NODE_NEXT)) {
        cell = AT(node, 0);
        extra = AT(cell, CELL_EXTRA);
        want += count_list(cell + CELL_ACTORS) + count_list(cell + CELL_OBJECTS) +
                (extra ? count_list(extra + 8) : 0);
    }
    while (cap < want + want / 2 + 1) {
        cap <<= 1;
        shift--;
    }
    ri_bytes = cap * 8;
    tes3x_log("refindex.bytes", ri_bytes);
    ri_table = (u32 *)MmAllocateSystemMemory(ri_bytes, PAGE_READWRITE);
    if (!ri_table) {
        ri_failed = 1;
        return;
    }
    for (node = 0; node < cap * 2; node++)
        ri_table[node] = 0; /* the kernel does not clear the pages it hands out */
    ri_mask = cap - 1;
    ri_shift = shift;
    tes3x_log("refindex.allocated", 1);
    for (node = AT(AT(ndd, CELL_LIST), LIST_HEAD); node; node = AT(node, NODE_NEXT)) {
        cell = AT(node, 0);
        extra = AT(cell, CELL_EXTRA);
        index_list(cell + CELL_ACTORS, 1);
        index_list(cell + CELL_OBJECTS, 0);
        if (extra)
            index_list(extra + 8, 0);
    }
}

/* The engine's walk (0x00104E10), unchanged. */
static u32 walk(void *ndd, void *object)
{
    fn_find_in_cell find = (fn_find_in_cell)TES3X_REFINDEX_FIND_IN_CELL;
    u32 node, any = 0, ref, live;

    for (node = AT(AT(ndd, CELL_LIST), LIST_HEAD); node; node = AT(node, NODE_NEXT)) {
        ref = (u32)find((void *)AT(node, 0), object, 0);
        if (!ref)
            continue;
        if (!(AT(ref, REF_FLAGS) & REF_DELETED))
            return ref;
        live = (u32)find((void *)AT(node, 0), object, 1);
        if (live)
            return live;
        if (!any)
            any = ref;
    }
    return any;
}

u32 tes3x_refindex_find(void *ndd, void *object)
{
    u32 *s, found;

    if (!ri_active || !object || ri_failed)
        return walk(ndd, object);
    if (!ri_table)
        build(ndd);
    if (!ri_table)
        return walk(ndd, object);
    ri_lookups++;
    s = slot((u32)object);
    found = s[0] ? s[1] & ~LIVE : 0;
#ifdef TES3X_REFINDEX_VERIFY
    if (found != walk(ndd, object))
        ri_mismatch++;
#endif
    return found;
}

void tes3x_refindex_begin(void)
{
    ri_active = 1;
    ri_table = 0;
    ri_failed = 0;
    ri_keys = ri_lookups = 0;
}

void tes3x_refindex_end(void)
{
    ri_active = 0;
    tes3x_log("refindex.lookups", ri_lookups);
    tes3x_log("refindex.keys", ri_keys);
    if (ri_failed)
        tes3x_log("refindex.alloc_failed", 1);
#ifdef TES3X_REFINDEX_VERIFY
    tes3x_log("refindex.mismatch", ri_mismatch);
#endif
    if (ri_table)
        MmFreeSystemMemory(ri_table, ri_bytes);
    ri_table = 0;
}

/* Entered by jmp in place of 0x00104E10: __thiscall(NonDynamicData *this, object), ret 4. */
__attribute__((naked)) void tes3x_refindex_find_hook(void)
{
    __asm__ volatile(
        "pushl 0x4(%esp)\n\t"
        "pushl %ecx\n\t"
        "call _tes3x_refindex_find\n\t"
        "addl $0x8, %esp\n\t"
        "ret $0x4\n\t");
}

/* Called in place of startGlobalScripts: __thiscall(WorldController *this), no arguments. */
__attribute__((naked)) void tes3x_refindex_scripts_hook(void)
{
    __asm__ volatile(
        "pushl %ecx\n\t"
        "call _tes3x_refindex_begin\n\t"
        "popl %ecx\n\t"
        "movl $" TES3X_STR(TES3X_REFINDEX_START_SCRIPTS) ", %eax\n\t"
        "call *%eax\n\t"
        "jmp _tes3x_refindex_end\n\t");
}
