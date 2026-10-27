/* Find a cell by name or by exterior grid from hash indexes, not by walking every cell.
 *
 * NonDynamicData::findCellByName walks the whole cell list with _stricmp for the first cell whose
 * ID matches, and the grid lookup behind getCellByGrid walks it comparing coordinates. Record
 * loading asks once per CELL and PGRD record and linking once per door, so the cost grows with
 * the square of the cell count: 92 s of full TR's boot load on hardware.
 *
 * The cell list is only appended to or cleared. A cell's ID (its name, or its region's name for an
 * unnamed exterior) and its grid change only while one of its records loads. So every cell is kept
 * under the hash of its ID and of its grid in list order, the cell being loaded and the list's
 * tail are hashed again before each lookup, and each candidate is compared live, so an entry left
 * under an old key never matches. Any other change to the list rebuilds the indexes.
 */

#include "tes3x_thunks.h"
#include "tes3xnt.h"
#include "tes3xini.h"
#include "tes3xlog.h"

#ifndef TES3X_CELLINDEX_LIST
#error "define TES3X_CELLINDEX_LIST to NonDynamicData's cell list offset"
#endif
#ifndef TES3X_CELLINDEX_STRICMP
#error "define TES3X_CELLINDEX_STRICMP to the CRT _stricmp the walk calls"
#endif
#ifndef TES3X_CELLINDEX_FLAGS
#error "define TES3X_CELLINDEX_FLAGS to the Cell flags offset (bit 0: interior)"
#endif
#ifndef TES3X_CELLINDEX_GRID_X
#error "define TES3X_CELLINDEX_GRID_X and TES3X_CELLINDEX_GRID_Y to the exterior grid offsets"
#endif

#define MmAllocateSystemMemory KFN(THUNK_MmAllocateSystemMemory, fn_MmAllocateSystemMemory)
#define MmFreeSystemMemory KFN(THUNK_MmFreeSystemMemory, fn_MmFreeSystemMemory)

#define LIST_COUNT 0u
#define LIST_HEAD 4u
#define LIST_TAIL 8u
#define NODE_DATA 0u
#define NODE_PREV 4u
#define NODE_NEXT 8u
#define VT_ID 0x20u
#define NONE 0xFFFFFFFFu

typedef const char *(__attribute__((thiscall)) *fn_id)(void *cell);
typedef int(__cdecl *fn_stricmp)(const char *a, const char *b);

#define AT(p, off) (*(u32 *)((u32)(p) + (off)))

typedef struct {
    u32 node, hash, pos, next;
} entry;

/* Each chain stays in list order, so the first live match is the engine's first match. */
typedef struct {
    entry *e;
    u32 used, cap, bytes;
    u32 *buckets, mask, bucket_bytes;
} index;

enum { MODE_UNREAD, MODE_ON, MODE_VERIFY };

static index ci_names, ci_grids;
static u32 ci_list, ci_list_count, ci_list_tail;
static u32 ci_loading, ci_loading_pos = NONE;
static u32 ci_builds, ci_checks, ci_mismatches;
static int ci_mode;
static volatile long ci_busy;

static const char *id_of(u32 cell)
{
    return ((fn_id)AT(AT(cell, 0), VT_ID))((void *)cell);
}

static int same(const char *id, const char *name)
{
    return id && !((fn_stricmp)TES3X_CELLINDEX_STRICMP)(id, name);
}

static int exterior(u32 cell)
{
    return !(*(u8 *)(cell + TES3X_CELLINDEX_FLAGS) & 1);
}

static int at_grid(u32 cell, int x, int y)
{
    return exterior(cell) && (int)AT(cell, TES3X_CELLINDEX_GRID_X) == x &&
           (int)AT(cell, TES3X_CELLINDEX_GRID_Y) == y;
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

static u32 grid_hash(int x, int y)
{
    return ((u32)x * 73856093u) ^ ((u32)y * 19349663u);
}

static void *alloc(u32 bytes)
{
    return MmAllocateSystemMemory(bytes, PAGE_READWRITE);
}

static u32 first(index *ix, u32 hash)
{
    return ix->buckets ? ix->buckets[hash & ix->mask] : NONE;
}

static void link(index *ix, u32 i)
{
    u32 *at = &ix->buckets[ix->e[i].hash & ix->mask];

    while (*at != NONE && ix->e[*at].pos <= ix->e[i].pos)
        at = &ix->e[*at].next;
    ix->e[i].next = *at;
    *at = i;
}

static void clear(index *ix)
{
    u32 i;

    ix->used = 0;
    if (ix->buckets)
        for (i = 0; i <= ix->mask; i++)
            ix->buckets[i] = NONE;
}

static int rehash(index *ix, u32 buckets)
{
    u32 bytes = (buckets * 4 + 0xFFFu) & ~0xFFFu, *fresh, i;

    fresh = (u32 *)alloc(bytes);
    if (!fresh)
        return 0;
    if (ix->buckets)
        MmFreeSystemMemory(ix->buckets, ix->bucket_bytes);
    ix->buckets = fresh;
    ix->bucket_bytes = bytes;
    ix->mask = buckets - 1;
    for (i = 0; i <= ix->mask; i++)
        ix->buckets[i] = NONE;
    for (i = 0; i < ix->used; i++)
        link(ix, i);
    return 1;
}

static int reserve(index *ix)
{
    u32 bytes, i;
    entry *grown;

    if (ix->used == ix->cap) {
        bytes = ((ix->cap ? ix->cap + ix->cap / 2 : 1024) * sizeof(entry) + 0xFFFu) & ~0xFFFu;
        grown = (entry *)alloc(bytes);
        if (!grown)
            return 0;
        for (i = 0; i < ix->used; i++)
            grown[i] = ix->e[i];
        if (ix->e)
            MmFreeSystemMemory(ix->e, ix->bytes);
        ix->e = grown;
        ix->bytes = bytes;
        ix->cap = bytes / sizeof(entry);
    }
    if (!ix->buckets || ix->used >= (ix->mask + 1) * 2)
        return rehash(ix, ix->buckets ? (ix->mask + 1) * 2 : 2048);
    return 1;
}

/* File the node under `hash` unless it is already there. */
static int file(index *ix, u32 node, u32 hash, u32 pos)
{
    u32 i;

    for (i = first(ix, hash); i != NONE; i = ix->e[i].next)
        if (ix->e[i].node == node && ix->e[i].hash == hash)
            return 1;
    if (!reserve(ix))
        return 0;
    ix->e[ix->used].node = node;
    ix->e[ix->used].hash = hash;
    ix->e[ix->used].pos = pos;
    link(ix, ix->used++);
    return 1;
}

static int key(u32 node, u32 pos)
{
    u32 cell = AT(node, NODE_DATA);

    if (!cell)
        return 1;
    if (node == ci_loading)
        ci_loading_pos = pos;
    if (!file(&ci_names, node, name_hash(id_of(cell)), pos))
        return 0;
    return !exterior(cell) ||
           file(&ci_grids, node, grid_hash((int)AT(cell, TES3X_CELLINDEX_GRID_X),
                                           (int)AT(cell, TES3X_CELLINDEX_GRID_Y)), pos);
}

static void keep(u32 list)
{
    ci_list = list;
    ci_list_count = AT(list, LIST_COUNT);
    ci_list_tail = AT(list, LIST_TAIL);
}

static int add_from(u32 node, u32 pos)
{
    for (; node; node = AT(node, NODE_NEXT), pos++)
        if (!key(node, pos))
            return 0;
    return 1;
}

static int build(u32 list)
{
    clear(&ci_names);
    clear(&ci_grids);
    ci_loading_pos = NONE;
    if (!add_from(AT(list, LIST_HEAD), 0))
        return 0;
    if (ci_loading_pos == NONE)
        ci_loading = 0;
    keep(list);
    ci_builds++;
    if (!(ci_builds & (ci_builds - 1)))
        tes3x_log("cellindex.builds", ci_builds);
    return 1;
}

/* Cells only appended: stepping back by the count's growth from the tail lands on the old tail,
 * compared by value, so a cleared list's freed nodes are never read. */
static int extend(u32 list)
{
    u32 grown, node = AT(list, LIST_TAIL), i;

    if (list != ci_list || AT(list, LIST_COUNT) <= ci_list_count)
        return 0;
    grown = AT(list, LIST_COUNT) - ci_list_count;
    for (i = 0; i < grown; i++) {
        if (!node)
            return 0;
        node = AT(node, NODE_PREV);
    }
    if (node != ci_list_tail)
        return 0;
    if (node && !key(node, ci_list_count - 1))
        return 0;
    if (!add_from(node ? AT(node, NODE_NEXT) : AT(list, LIST_HEAD), ci_list_count))
        return 0;
    keep(list);
    return 1;
}

static int sync(u32 list)
{
    if (list != ci_list || AT(list, LIST_COUNT) != ci_list_count ||
        AT(list, LIST_TAIL) != ci_list_tail) {
        if (!extend(list) && !build(list)) {
            ci_list = 0;
            return 0;
        }
    }
    if (ci_list_tail && !key(ci_list_tail, ci_list_count - 1))
        return 0;
    return !ci_loading || key(ci_loading, ci_loading_pos);
}

/* The engine's walks, unchanged. */
static u32 walk_name(u32 list, const char *name)
{
    u32 node;

    for (node = AT(list, LIST_HEAD); node; node = AT(node, NODE_NEXT))
        if (same(id_of(AT(node, NODE_DATA)), name))
            return AT(node, NODE_DATA);
    return 0;
}

static u32 walk_grid(u32 list, int x, int y)
{
    u32 node;

    for (node = AT(list, LIST_HEAD); node; node = AT(node, NODE_NEXT))
        if (at_grid(AT(node, NODE_DATA), x, y))
            return node;
    return 0;
}

static u32 find_name(u32 list, const char *name)
{
    u32 hash = name_hash(name), i;
    entry *e;

    if (!sync(list))
        return walk_name(list, name);
    for (i = first(&ci_names, hash); i != NONE; i = e->next) {
        e = &ci_names.e[i];
        if (e->hash == hash && same(id_of(AT(e->node, NODE_DATA)), name))
            return AT(e->node, NODE_DATA);
    }
    return 0;
}

static u32 find_grid(u32 list, int x, int y)
{
    u32 hash = grid_hash(x, y), i;
    entry *e;

    if (!sync(list))
        return walk_grid(list, x, y);
    for (i = first(&ci_grids, hash); i != NONE; i = e->next) {
        e = &ci_grids.e[i];
        if (e->hash == hash && at_grid(AT(e->node, NODE_DATA), x, y))
            return e->node;
    }
    return 0;
}

static void read_mode(void)
{
    char value[16];

    tes3x_ini_xbox("CellIndex", "on", value, sizeof(value));
    ci_mode = (value[0] | 0x20) == 'v' ? MODE_VERIFY : MODE_ON;
    tes3x_log("cellindex.mode", ci_mode);
}

static u32 checked(u32 found, u32 engine, u32 detail)
{
    ci_checks++;
    if (!(ci_checks & (ci_checks - 1)) && ci_checks >= 1024)
        tes3x_log("cellindex.checks", ci_checks);
    if (engine == found)
        return found;
    ci_mismatches++;
    if (ci_mismatches <= 8)
        tes3x_log_hex3("cellindex.mismatch", engine, found, detail);
    if (!(ci_mismatches & (ci_mismatches - 1)))
        tes3x_log("cellindex.mismatches", ci_mismatches);
    return engine;
}

u32 __stdcall tes3x_cellindex_find(void *ndd, const char *name)
{
    u32 list = AT(ndd, TES3X_CELLINDEX_LIST), found;

    if (__sync_lock_test_and_set(&ci_busy, 1))
        return walk_name(list, name);
    if (ci_mode == MODE_UNREAD)
        read_mode();
    found = find_name(list, name);
    if (ci_mode == MODE_VERIFY)
        found = checked(found, walk_name(list, name), name_hash(name));
    __sync_lock_release(&ci_busy);
    return found;
}

u32 __stdcall tes3x_cellindex_grid(void *ndd, int x, int y)
{
    u32 list = AT(ndd, TES3X_CELLINDEX_LIST), found;

    if (__sync_lock_test_and_set(&ci_busy, 1))
        return walk_grid(list, x, y);
    if (ci_mode == MODE_UNREAD)
        read_mode();
    found = find_grid(list, x, y);
    if (ci_mode == MODE_VERIFY)
        found = checked(found, walk_grid(list, x, y), ((u32)x << 16) | ((u32)y & 0xFFFFu));
    __sync_lock_release(&ci_busy);
    return found;
}

/* The record about to load into `cell` may change its ID or grid: find its place while the
 * indexes still file it under the old keys, and key it again at every lookup until the next cell
 * loads. */
void __stdcall tes3x_cellindex_mark(void *ndd, u32 cell)
{
    u32 list = AT(ndd, TES3X_CELLINDEX_LIST), hash, i, node = 0, pos = NONE;

    if (__sync_lock_test_and_set(&ci_busy, 1))
        return;
    if (sync(list)) {
        hash = name_hash(id_of(cell));
        for (i = first(&ci_names, hash); i != NONE; i = ci_names.e[i].next)
            if (AT(ci_names.e[i].node, NODE_DATA) == cell) {
                node = ci_names.e[i].node;
                pos = ci_names.e[i].pos;
                break;
            }
        if (!node)
            for (node = AT(list, LIST_HEAD), pos = 0; node; node = AT(node, NODE_NEXT), pos++)
                if (AT(node, NODE_DATA) == cell)
                    break;
    }
    ci_loading = node;
    ci_loading_pos = node ? pos : NONE;
    __sync_lock_release(&ci_busy);
}

/* Called in place of loadRecord's thiscall to 0x00122990 on the cell a CELL record loads into,
 * new or found; ebp holds NonDynamicData there. */
__attribute__((naked)) void tes3x_cellindex_load_hook(void)
{
    __asm__ volatile(
        "pushl %ecx\n\t"
        "pushl %ecx\n\t"
        "pushl %ebp\n\t"
        "call _tes3x_cellindex_mark@8\n\t"
        "popl %ecx\n\t"
        "movl $" TES3X_SYM(CELLINDEX_LOAD) ", %eax\n\t"
        "jmp *%eax\n\t");
}

/* Entered by jmp in place of findCellByName: __thiscall(NonDynamicData *this, name), ret 4. */
__attribute__((naked)) void tes3x_cellindex_find_hook(void)
{
    __asm__ volatile(
        "pushl 0x4(%esp)\n\t"
        "pushl %ecx\n\t"
        "call _tes3x_cellindex_find@8\n\t"
        "ret $0x4\n\t");
}

/* Entered by jmp in place of the grid walk: __thiscall(NonDynamicData *this, x, y), ret 8;
 * returns the list node. */
__attribute__((naked)) void tes3x_cellindex_grid_hook(void)
{
    __asm__ volatile(
        "pushl 0x8(%esp)\n\t"
        "pushl 0x8(%esp)\n\t"
        "pushl %ecx\n\t"
        "call _tes3x_cellindex_grid@12\n\t"
        "ret $0x8\n\t");
}
