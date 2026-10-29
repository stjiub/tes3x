/* Resolve a dialogue filter's script variable from a hash index, not by walking every script.
 *
 * DialogueInfo::loadObjectSpecific resolves each Local or NotLocal filter by calling
 * Script::getLocalVarIndexAndType on every script in turn until one declares the variable, a
 * walk of the whole script list per filter: 43.7 million calls and about 30 s of full TR's boot
 * load on hardware. Only whether some script declares the name matters; the script and index it
 * reports are not used.
 *
 * Every declared name of every script is kept under the hash of its lower-cased name, and each
 * candidate is compared live. The index follows the script list the way the script index does:
 * appended scripts are added, any other change rebuilds it. The hook replaces the engine's walk
 * and leaves the list's cursor on the first script, as the walk's first step does.
 */

#include "tes3x_thunks.h"
#include "tes3xnt.h"
#include "tes3xini.h"
#include "tes3xlog.h"

#ifndef TES3X_LOCALVAR_GLOBAL
#error "define TES3X_LOCALVAR_GLOBAL to the address of the pointer to NonDynamicData's holder"
#endif
#ifndef TES3X_LOCALVAR_STRICMP
#error "define TES3X_LOCALVAR_STRICMP to the CRT _stricmp"
#endif
#ifndef TES3X_LOCALVAR_WALK
#error "define TES3X_LOCALVAR_WALK to Script::getLocalVarIndexAndType"
#endif
#ifndef TES3X_LOCALVAR_FOUND
#error "define TES3X_LOCALVAR_FOUND to where the loader continues after a match"
#endif
#ifndef TES3X_LOCALVAR_MISSING
#error "define TES3X_LOCALVAR_MISSING to where the loader continues after no match"
#endif

#define MmAllocateSystemMemory KFN(THUNK_MmAllocateSystemMemory, fn_MmAllocateSystemMemory)
#define MmFreeSystemMemory KFN(THUNK_MmFreeSystemMemory, fn_MmFreeSystemMemory)

#define SCRIPT_LIST 0x38u
#define LIST_COUNT 4u
#define LIST_HEAD 8u
#define LIST_TAIL 0xCu
#define LIST_CURSOR 0x10u
#define NODE_NEXT 4u
#define NODE_PREV 0u
#define NODE_DATA 8u
#define NONE 0xFFFFFFFFu

/* Script: variable counts (short, long, float) and the name pointer arrays. */
#define SCRIPT_COUNTS 0x30u
#define SCRIPT_NAMES 0x44u

typedef int(__cdecl *fn_stricmp)(const char *a, const char *b);
typedef u32(__attribute__((thiscall)) *fn_walk)(void *script, const char *name, int *index);

#define AT(p, off) (*(u32 *)((u32)(p) + (off)))

typedef struct {
    const char *name;
    u32 hash, next;
} entry;

enum { MODE_UNREAD, MODE_ON, MODE_VERIFY };

static entry *lv_e;
static u32 lv_used, lv_cap, lv_bytes;
static u32 *lv_buckets, *lv_tails, lv_mask, lv_bucket_bytes;
static u32 lv_list, lv_list_count, lv_list_tail;
static u32 lv_builds, lv_checks, lv_mismatches;
static int lv_mode;
static volatile long lv_busy;

#define MmAllocate(bytes) MmAllocateSystemMemory(bytes, PAGE_READWRITE)

/* FNV-1a over ASCII folded to lower case. Other bytes are left out, so the hash never separates
 * two names _stricmp could call equal. */
static u32 name_hash(const char *s)
{
    u32 h = 2166136261u;
    u8 c;

    while ((c = (u8)*s++)) {
        if (c >= 0x80)
            continue;
        if (c >= 'A' && c <= 'Z')
            c += 'a' - 'A';
        h = (h ^ c) * 16777619u;
    }
    return h;
}

static void link(u32 i)
{
    u32 bucket = lv_e[i].hash & lv_mask;

    lv_e[i].next = NONE;
    if (lv_tails[bucket] == NONE)
        lv_buckets[bucket] = i;
    else
        lv_e[lv_tails[bucket]].next = i;
    lv_tails[bucket] = i;
}

static void reset_buckets(void)
{
    u32 i;

    for (i = 0; i <= lv_mask; i++) {
        lv_buckets[i] = NONE;
        lv_tails[i] = NONE;
    }
}

static int rehash(u32 buckets)
{
    u32 bytes = (buckets * 8 + 0xFFFu) & ~0xFFFu, *fresh, i;

    fresh = (u32 *)MmAllocate(bytes);
    if (!fresh)
        return 0;
    if (lv_buckets)
        MmFreeSystemMemory(lv_buckets, lv_bucket_bytes);
    lv_buckets = fresh;
    lv_tails = fresh + buckets;
    lv_bucket_bytes = bytes;
    lv_mask = buckets - 1;
    reset_buckets();
    for (i = 0; i < lv_used; i++)
        link(i);
    return 1;
}

static int reserve(void)
{
    u32 bytes, i;
    entry *grown;

    if (lv_used == lv_cap) {
        bytes = ((lv_cap ? lv_cap + lv_cap / 2 : 4096) * sizeof(entry) + 0xFFFu) & ~0xFFFu;
        grown = (entry *)MmAllocate(bytes);
        if (!grown)
            return 0;
        for (i = 0; i < lv_used; i++)
            grown[i] = lv_e[i];
        if (lv_e)
            MmFreeSystemMemory(lv_e, lv_bytes);
        lv_e = grown;
        lv_bytes = bytes;
        lv_cap = bytes / sizeof(entry);
    }
    if (!lv_buckets || lv_used >= (lv_mask + 1) * 2)
        return rehash(lv_buckets ? (lv_mask + 1) * 2 : 4096);
    return 1;
}

static int add_name(const char *name)
{
    if (!name)
        return 1;
    if (!reserve())
        return 0;
    lv_e[lv_used].name = name;
    lv_e[lv_used].hash = name_hash(name);
    link(lv_used++);
    return 1;
}

static int add_script(u32 script)
{
    u32 kind, i;

    if (!script)
        return 1;
    for (kind = 0; kind < 3; kind++) {
        u32 count = AT(script, SCRIPT_COUNTS + kind * 4);
        u32 names = AT(script, SCRIPT_NAMES + kind * 4);

        if (!count)
            continue;
        if (!names)
            return 0;
        for (i = 0; i < count; i++)
            if (!add_name(((const char **)names)[i]))
                return 0;
    }
    return 1;
}

static int add_from(u32 node)
{
    for (; node; node = AT(node, NODE_NEXT))
        if (!add_script(AT(node, NODE_DATA)))
            return 0;
    return 1;
}

static void keep(u32 list)
{
    lv_list = list;
    lv_list_count = AT(list, LIST_COUNT);
    lv_list_tail = AT(list, LIST_TAIL);
}

static int build(u32 list)
{
    lv_used = 0;
    if (lv_buckets)
        reset_buckets();
    if (!add_from(AT(list, LIST_HEAD)))
        return 0;
    keep(list);
    lv_builds++;
    if (!(lv_builds & (lv_builds - 1)))
        tes3x_log("localvar.builds", lv_builds);
    return 1;
}

/* Scripts only appended: stepping back by the count's growth from the tail lands on the old tail,
 * compared by value, so a cleared list's freed nodes are never read. */
static int extend(u32 list)
{
    u32 grown, node = AT(list, LIST_TAIL), i;

    if (list != lv_list || AT(list, LIST_COUNT) <= lv_list_count)
        return 0;
    grown = AT(list, LIST_COUNT) - lv_list_count;
    for (i = 0; i < grown; i++) {
        if (!node)
            return 0;
        node = AT(node, NODE_PREV);
    }
    if (node != lv_list_tail)
        return 0;
    if (!add_from(node ? AT(node, NODE_NEXT) : AT(list, LIST_HEAD)))
        return 0;
    keep(list);
    return 1;
}

static int sync(u32 list)
{
    if (list == lv_list && AT(list, LIST_COUNT) == lv_list_count &&
        AT(list, LIST_TAIL) == lv_list_tail)
        return 1;
    if (extend(list) || build(list))
        return 1;
    lv_list = 0;
    return 0;
}

/* The engine's walk: whether any script declares the name. */
static int walk(u32 list, const char *name)
{
    u32 node;
    int index;

    for (node = AT(list, LIST_HEAD); node; node = AT(node, NODE_NEXT)) {
        u32 script = AT(node, NODE_DATA);

        if (script && (u8)((fn_walk)TES3X_LOCALVAR_WALK)((void *)script, name, &index))
            return 1;
    }
    return 0;
}

static int find(u32 list, const char *name)
{
    u32 hash = name_hash(name), i;

    if (!sync(list))
        return walk(list, name);
    if (lv_buckets)
        for (i = lv_buckets[hash & lv_mask]; i != NONE; i = lv_e[i].next)
            if (lv_e[i].hash == hash && !((fn_stricmp)TES3X_LOCALVAR_STRICMP)(lv_e[i].name, name))
                return 1;
    return 0;
}

static void read_mode(void)
{
    char value[16];

    tes3x_ini_xbox("LocalVarIndex", "on", value, sizeof(value));
    lv_mode = (value[0] | 0x20) == 'v' ? MODE_VERIFY : MODE_ON;
    tes3x_log("localvar.mode", lv_mode);
}

static int checked(int found, int engine, const char *name)
{
    lv_checks++;
    if (!(lv_checks & (lv_checks - 1)) && lv_checks >= 1024)
        tes3x_log("localvar.checks", lv_checks);
    if (engine == found)
        return found;
    lv_mismatches++;
    if (lv_mismatches <= 8)
        tes3x_log_hex3("localvar.mismatch", engine, found, name_hash(name));
    if (!(lv_mismatches & (lv_mismatches - 1)))
        tes3x_log("localvar.mismatches", lv_mismatches);
    return engine;
}

u32 __stdcall tes3x_localvar_has(const char *name)
{
    u32 ndd = AT(AT(TES3X_LOCALVAR_GLOBAL, 0), 0);
    u32 list = ndd ? AT(ndd, SCRIPT_LIST) : 0;
    int found;

    if (!list)
        return 0;
    AT(list, LIST_CURSOR) = AT(list, LIST_HEAD);
    if (__sync_lock_test_and_set(&lv_busy, 1))
        return walk(list, name);
    if (lv_mode == MODE_UNREAD)
        read_mode();
    found = find(list, name);
    if (lv_mode == MODE_VERIFY)
        found = checked(found, walk(list, name), name);
    __sync_lock_release(&lv_busy);
    return found;
}

/* Entered by jmp in place of the loader's script walk, with its registers live: EBX holds the
 * variable name. Continues at the loader's match or no-match code. */
__attribute__((naked)) void tes3x_localvar_hook(void)
{
    __asm__ volatile(
        "pushal\n\t"
        "pushl %ebx\n\t"
        "call _tes3x_localvar_has@4\n\t"
        "testl %eax, %eax\n\t"
        "popal\n\t"
        "jnz 1f\n\t"
        "pushl $" TES3X_SYM(LOCALVAR_MISSING) "\n\t"
        "ret\n"
        "1:\n\t"
        "pushl $" TES3X_SYM(LOCALVAR_FOUND) "\n\t"
        "ret\n\t");
}
