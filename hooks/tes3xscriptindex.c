/* Find a script by ID from a hash index, not by walking every script.
 *
 * NonDynamicData::findScriptByName walks the whole script list with _stricmp for the first script
 * whose ID matches. Record loading asks once per SCPT record and once per global script, and
 * linking once per script reference, so the cost grows with the square of the script count: 18 s
 * of full TR's boot load on hardware.
 *
 * A script's ID is set while its record loads, before the script joins the list. The list is
 * appended to, cleared, or has an earlier script removed when a later plugin replaces it. Every
 * script is kept under the hash of its ID in list order and each candidate is compared live, so
 * the first live match is the engine's first match. Any change other than appending rebuilds the
 * index. The engine's walk leaves the list's cursor on the match, or null; so does this.
 */

#include "tes3x_thunks.h"
#include "tes3xnt.h"
#include "tes3xini.h"
#include "tes3xlog.h"

#ifndef TES3X_SCRIPTINDEX_LIST
#error "define TES3X_SCRIPTINDEX_LIST to NonDynamicData's script list offset"
#endif
#ifndef TES3X_SCRIPTINDEX_STRICMP
#error "define TES3X_SCRIPTINDEX_STRICMP to the CRT _stricmp the walk calls"
#endif

#define MmAllocateSystemMemory KFN(THUNK_MmAllocateSystemMemory, fn_MmAllocateSystemMemory)
#define MmFreeSystemMemory KFN(THUNK_MmFreeSystemMemory, fn_MmFreeSystemMemory)

#define LIST_COUNT 4u
#define LIST_HEAD 8u
#define LIST_TAIL 0xCu
#define LIST_CURSOR 0x10u
#define NODE_NEXT 4u
#define NODE_PREV 0u
#define NODE_DATA 8u
#define VT_ID 0x20u
#define NONE 0xFFFFFFFFu

typedef const char *(__attribute__((thiscall)) *fn_id)(void *script);
typedef int(__cdecl *fn_stricmp)(const char *a, const char *b);

#define AT(p, off) (*(u32 *)((u32)(p) + (off)))

typedef struct {
    u32 node, hash, next;
} entry;

enum { MODE_UNREAD, MODE_ON, MODE_VERIFY };

static entry *si_e;
static u32 si_used, si_cap, si_bytes;
static u32 *si_buckets, *si_tails, si_mask, si_bucket_bytes;
static u32 si_list, si_list_count, si_list_tail;
static u32 si_builds, si_checks, si_mismatches;
static int si_mode;
static volatile long si_busy;

static const char *id_of(u32 script)
{
    return ((fn_id)AT(AT(script, 0), VT_ID))((void *)script);
}

static int same(const char *id, const char *name)
{
    return id && !((fn_stricmp)TES3X_SCRIPTINDEX_STRICMP)(id, name);
}

/* FNV-1a over ASCII folded to lower case. Other bytes are left out, so the hash never separates
 * two names _stricmp could call equal. */
static u32 name_hash(const char *s)
{
    u32 h = 2166136261u;
    u8 c;

    if (!s)
        return h;
    while ((c = (u8)*s++)) {
        if (c >= 0x80)
            continue;
        if (c >= 'A' && c <= 'Z')
            c += 'a' - 'A';
        h = (h ^ c) * 16777619u;
    }
    return h;
}

static void *alloc(u32 bytes)
{
    return MmAllocateSystemMemory(bytes, PAGE_READWRITE);
}

/* Entries arrive in list order, so each chain stays in list order by appending. */
static void link(u32 i)
{
    u32 bucket = si_e[i].hash & si_mask;

    si_e[i].next = NONE;
    if (si_tails[bucket] == NONE)
        si_buckets[bucket] = i;
    else
        si_e[si_tails[bucket]].next = i;
    si_tails[bucket] = i;
}

static void reset_buckets(void)
{
    u32 i;

    for (i = 0; i <= si_mask; i++) {
        si_buckets[i] = NONE;
        si_tails[i] = NONE;
    }
}

static int rehash(u32 buckets)
{
    u32 bytes = (buckets * 8 + 0xFFFu) & ~0xFFFu, *fresh, i;

    fresh = (u32 *)alloc(bytes);
    if (!fresh)
        return 0;
    if (si_buckets)
        MmFreeSystemMemory(si_buckets, si_bucket_bytes);
    si_buckets = fresh;
    si_tails = fresh + buckets;
    si_bucket_bytes = bytes;
    si_mask = buckets - 1;
    reset_buckets();
    for (i = 0; i < si_used; i++)
        link(i);
    return 1;
}

static int reserve(void)
{
    u32 bytes, i;
    entry *grown;

    if (si_used == si_cap) {
        bytes = ((si_cap ? si_cap + si_cap / 2 : 1024) * sizeof(entry) + 0xFFFu) & ~0xFFFu;
        grown = (entry *)alloc(bytes);
        if (!grown)
            return 0;
        for (i = 0; i < si_used; i++)
            grown[i] = si_e[i];
        if (si_e)
            MmFreeSystemMemory(si_e, si_bytes);
        si_e = grown;
        si_bytes = bytes;
        si_cap = bytes / sizeof(entry);
    }
    if (!si_buckets || si_used >= (si_mask + 1) * 2)
        return rehash(si_buckets ? (si_mask + 1) * 2 : 2048);
    return 1;
}

static int add(u32 node)
{
    u32 script = AT(node, NODE_DATA);

    if (!reserve())
        return 0;
    si_e[si_used].node = node;
    si_e[si_used].hash = script ? name_hash(id_of(script)) : 0;
    link(si_used++);
    return 1;
}

static void keep(u32 list)
{
    si_list = list;
    si_list_count = AT(list, LIST_COUNT);
    si_list_tail = AT(list, LIST_TAIL);
}

static int add_from(u32 node)
{
    for (; node; node = AT(node, NODE_NEXT))
        if (!add(node))
            return 0;
    return 1;
}

static int build(u32 list)
{
    si_used = 0;
    if (si_buckets)
        reset_buckets();
    if (!add_from(AT(list, LIST_HEAD)))
        return 0;
    keep(list);
    si_builds++;
    if (!(si_builds & (si_builds - 1)))
        tes3x_log("scriptindex.builds", si_builds);
    return 1;
}

/* Scripts only appended: stepping back by the count's growth from the tail lands on the old tail,
 * compared by value, so a cleared list's freed nodes are never read. */
static int extend(u32 list)
{
    u32 grown, node = AT(list, LIST_TAIL), i;

    if (list != si_list || AT(list, LIST_COUNT) <= si_list_count)
        return 0;
    grown = AT(list, LIST_COUNT) - si_list_count;
    for (i = 0; i < grown; i++) {
        if (!node)
            return 0;
        node = AT(node, NODE_PREV);
    }
    if (node != si_list_tail)
        return 0;
    if (!add_from(node ? AT(node, NODE_NEXT) : AT(list, LIST_HEAD)))
        return 0;
    keep(list);
    return 1;
}

static int sync(u32 list)
{
    if (list == si_list && AT(list, LIST_COUNT) == si_list_count &&
        AT(list, LIST_TAIL) == si_list_tail)
        return 1;
    if (extend(list) || build(list))
        return 1;
    si_list = 0;
    return 0;
}

/* The engine's walk, unchanged, ending with the list cursor where its iteration leaves it. */
static u32 walk(u32 list, const char *name)
{
    u32 node;

    for (node = AT(list, LIST_HEAD); node; node = AT(node, NODE_NEXT))
        if (same(id_of(AT(node, NODE_DATA)), name))
            break;
    AT(list, LIST_CURSOR) = node;
    return node;
}

static u32 find(u32 list, const char *name)
{
    u32 hash = name_hash(name), i, node = 0;

    if (!sync(list))
        return walk(list, name);
    if (si_buckets)
        for (i = si_buckets[hash & si_mask]; i != NONE; i = si_e[i].next)
            if (si_e[i].hash == hash && same(id_of(AT(si_e[i].node, NODE_DATA)), name)) {
                node = si_e[i].node;
                break;
            }
    AT(list, LIST_CURSOR) = node;
    return node;
}

static void read_mode(void)
{
    char value[16];

    tes3x_ini_xbox("ScriptIndex", "on", value, sizeof(value));
    si_mode = (value[0] | 0x20) == 'v' ? MODE_VERIFY : MODE_ON;
    tes3x_log("scriptindex.mode", si_mode);
}

static u32 checked(u32 found, u32 engine, u32 detail)
{
    si_checks++;
    if (!(si_checks & (si_checks - 1)) && si_checks >= 1024)
        tes3x_log("scriptindex.checks", si_checks);
    if (engine == found)
        return found;
    si_mismatches++;
    if (si_mismatches <= 8)
        tes3x_log_hex3("scriptindex.mismatch", engine, found, detail);
    if (!(si_mismatches & (si_mismatches - 1)))
        tes3x_log("scriptindex.mismatches", si_mismatches);
    return engine;
}

u32 __stdcall tes3x_scriptindex_find(void *ndd, const char *name)
{
    u32 list = AT(ndd, TES3X_SCRIPTINDEX_LIST), node;

    if (!list)
        return 0;
    if (__sync_lock_test_and_set(&si_busy, 1))
        node = walk(list, name);
    else {
        if (si_mode == MODE_UNREAD)
            read_mode();
        node = find(list, name);
        if (si_mode == MODE_VERIFY)
            node = checked(node, walk(list, name), name_hash(name));
        __sync_lock_release(&si_busy);
    }
    return node ? AT(node, NODE_DATA) : 0;
}

/* Entered by jmp in place of findScriptByName: __thiscall(NonDynamicData *this, name), ret 4. */
__attribute__((naked)) void tes3x_scriptindex_find_hook(void)
{
    __asm__ volatile(
        "pushl 0x4(%esp)\n\t"
        "pushl %ecx\n\t"
        "call _tes3x_scriptindex_find@8\n\t"
        "ret $0x4\n\t");
}
