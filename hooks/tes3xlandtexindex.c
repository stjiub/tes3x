/* Resolve a LAND record's texture indices from an index, not by padding and walking lists.
 *
 * Land::loadObjectSpecific resolves each of a VTEX record's 256 texture indices through a
 * per-record cache list that is addressed by position. Storing a texture at index N appends N
 * placeholder nodes, and reading index N walks N nodes, so every LAND record allocates and walks
 * a list as long as its highest texture index. On full TR's boot load that is about 5 s of
 * 4,171 records. A cache miss then walks the landscape texture list for the first texture whose
 * index matches.
 *
 * The index maps a texture index to the first texture in list order that has it. It follows the
 * list by appending and rebuilds on any other change, and every hit is compared live. The hook
 * replaces the cache lookup and the walk, and leaves the engine's walk for the rare cases the
 * index cannot answer.
 */

#include "tes3x_thunks.h"
#include "tes3xnt.h"
#include "tes3xini.h"
#include "tes3xlog.h"

#ifndef TES3X_LANDTEX_STORE
#error "define TES3X_LANDTEX_STORE to where the loader continues after a texture is stored"
#endif
#ifndef TES3X_LANDTEX_WALK
#error "define TES3X_LANDTEX_WALK to where the loader walks the texture list itself"
#endif
#ifndef TES3X_LANDTEX_MISSING
#error "define TES3X_LANDTEX_MISSING to where the loader continues when no texture has the index"
#endif

#define MmAllocateSystemMemory KFN(THUNK_MmAllocateSystemMemory, fn_MmAllocateSystemMemory)
#define MmFreeSystemMemory KFN(THUNK_MmFreeSystemMemory, fn_MmFreeSystemMemory)

#define LIST_COUNT 4u
#define LIST_HEAD 8u
#define LIST_TAIL 0xCu
#define NODE_NEXT 4u
#define NODE_DATA 8u
#define TEX_KEY 0x13Cu
#define TEX_SLOT 0x138u
#define SLACK 1024u
#define MAX_KEY 0x10000u
#define FALLBACK 1u

#define AT(p, off) (*(u32 *)((u32)(p) + (off)))

enum { MODE_UNREAD, MODE_ON, MODE_VERIFY };

static u32 *lt_tab;
static u32 lt_cap, lt_bytes;
static u32 lt_built, lt_head, lt_last;
static u32 lt_builds, lt_checks, lt_mismatches;
static int lt_mode;
static volatile long lt_busy;

static u32 walk(u32 list, u32 key)
{
    u32 node;

    for (node = AT(list, LIST_HEAD); node; node = AT(node, NODE_NEXT)) {
        u32 tex = AT(node, NODE_DATA);

        if (tex && AT(tex, TEX_KEY) == key)
            return tex;
    }
    return 0;
}

/* Adds nodes after the last indexed one. Fails when a key does not fit the table. */
static int add(u32 node, u32 count)
{
    for (; count; count--, node = AT(node, NODE_NEXT)) {
        u32 tex = node ? AT(node, NODE_DATA) : 0;
        u32 key;

        if (!node)
            return 0;
        lt_last = node;
        lt_built++;
        if (!tex)
            continue;
        key = AT(tex, TEX_KEY);
        if (key >= MAX_KEY)
            continue;
        if (key >= lt_cap)
            return 0;
        if (!lt_tab[key])
            lt_tab[key] = tex;
    }
    return 1;
}

static int rebuild(u32 list)
{
    u32 node, max = 0, bytes, cap;
    u32 *fresh;

    for (node = AT(list, LIST_HEAD); node; node = AT(node, NODE_NEXT)) {
        u32 tex = AT(node, NODE_DATA);

        if (tex && AT(tex, TEX_KEY) < MAX_KEY && AT(tex, TEX_KEY) > max)
            max = AT(tex, TEX_KEY);
    }
    cap = max + 1 + SLACK;
    bytes = (cap * 4 + 0xFFF) & ~0xFFFu;
    cap = bytes / 4;
    fresh = MmAllocateSystemMemory(bytes, PAGE_READWRITE);
    if (!fresh)
        return 0;
    if (lt_tab)
        MmFreeSystemMemory(lt_tab, lt_bytes);
    lt_tab = fresh;
    lt_bytes = bytes;
    lt_cap = cap;
    lt_built = 0;
    lt_last = 0;
    lt_head = AT(list, LIST_HEAD);
    lt_builds++;
    return add(lt_head, AT(list, LIST_COUNT));
}

/* Brings the index level with the list; 0 when it could not. */
static int sync(u32 list)
{
    u32 count = AT(list, LIST_COUNT);
    u32 head = AT(list, LIST_HEAD);

    if (lt_tab && head == lt_head && count == lt_built && AT(list, LIST_TAIL) == lt_last)
        return 1;
    if (lt_tab && head == lt_head && count > lt_built && lt_last &&
        AT(list, LIST_TAIL) != lt_last) {
        u32 node = AT(lt_last, NODE_NEXT);
        u32 n = count - lt_built, check = node, tail = lt_last;

        while (check && n) {
            tail = check;
            check = AT(check, NODE_NEXT);
            n--;
        }
        if (!n && !check && tail == AT(list, LIST_TAIL) && add(node, count - lt_built))
            return 1;
    }
    return rebuild(list);
}

static void read_mode(void)
{
    char value[16];

    tes3x_ini_xbox("LandTexIndex", "on", value, sizeof(value));
    lt_mode = (value[0] | 0x20) == 'v' ? MODE_VERIFY : MODE_ON;
    tes3x_log("landtex.mode", lt_mode);
}

/* The texture with the key, 0 when none has it, FALLBACK to let the engine walk. */
u32 __stdcall tes3x_landtex_find(u32 list, u32 key)
{
    u32 tex;

    if (!list || key >= MAX_KEY || __sync_lock_test_and_set(&lt_busy, 1))
        return FALLBACK;
    if (lt_mode == MODE_UNREAD)
        read_mode();
    if (!sync(list)) {
        __sync_lock_release(&lt_busy);
        return FALLBACK;
    }
    tex = key < lt_cap ? lt_tab[key] : 0;
    if (tex && AT(tex, TEX_KEY) != key)
        tex = FALLBACK;
    if (lt_mode == MODE_VERIFY && tex != FALLBACK) {
        u32 engine = walk(list, key);

        lt_checks++;
        if (engine != tex) {
            lt_mismatches++;
            tes3x_log_hex3("landtex.mismatch", key, tex, engine);
            tex = engine;
        }
        if (!(lt_checks & 0x3FFF)) {
            tes3x_log("landtex.checks", lt_checks);
            tes3x_log("landtex.builds", lt_builds);
        }
    }
    __sync_lock_release(&lt_busy);
    return tex;
}

/* Entered by jmp at the cache check, with the loader's registers live: ESI holds the texture
 * index and the texture list is at 0x18(%esp). */
__attribute__((naked)) void tes3x_landtex_hook(void)
{
    __asm__ volatile(
        "pushl %esi\n\t"
        "pushl 0x1C(%esp)\n\t"
        "call _tes3x_landtex_find@8\n\t"
        "cmpl $1, %eax\n\t"
        "jbe 1f\n\t"
        "movl 0x138(%eax), %ecx\n\t"
        "leal (%ebx,%edi), %edx\n\t"
        "movw %cx, 0x1C(%ebp,%edx,2)\n\t"
        "pushl $" TES3X_SYM(LANDTEX_STORE) "\n\t"
        "ret\n"
        "1:\n\t"
        "je 2f\n\t"
        "pushl $" TES3X_SYM(LANDTEX_MISSING) "\n\t"
        "ret\n"
        "2:\n\t"
        "pushl $" TES3X_SYM(LANDTEX_WALK) "\n\t"
        "ret\n\t");
}
