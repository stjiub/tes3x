/* Skip the spells an NPC's spell auto-calculation would reject, from a compact table.
 *
 * The routine that picks an NPC's spells (the end of NPC link resolution) visits every spell in
 * the game. For an NPC, a spell is rejected before anything else is done if it is deleted, not a
 * plain spell, not flagged for NPCs, costs more than the NPC's magicka, or has an effect whose
 * skill or attribute is below the minimum. The walk reads about 2,000 spell objects scattered
 * over memory for each NPC: 5.6 s for 6,506 NPCs on full Tamriel Rebuilt's boot load.
 *
 * This keeps, in list order, a 24-byte record per candidate spell (cost, and the skill or
 * attribute slot of each effect) and hands the routine only the spells those tests accept. The
 * routine applies every other test itself. A walk for the player is not filtered. The table is
 * rebuilt when the list's head, tail or count changes; spell flags and effects do not change
 * while the game runs.
 */

#include "tes3x_thunks.h"
#include "tes3xnt.h"
#include "tes3xini.h"
#include "tes3xlog.h"

#ifndef TES3X_ACALC_EFFECTS
#error "define TES3X_ACALC_EFFECTS to the engine's effect flags table"
#endif

#define MmAllocateSystemMemory KFN(THUNK_MmAllocateSystemMemory, fn_MmAllocateSystemMemory)
#define MmFreeSystemMemory KFN(THUNK_MmFreeSystemMemory, fn_MmFreeSystemMemory)

#define LIST_HEAD 4u
#define LIST_TAIL 8u
#define LIST_COUNT 0xCu
#define NODE_NEXT 0x20u
#define OBJ_FLAGS 8u
#define SPELL_TYPE 0x30u
#define SPELL_COST 0x32u
#define SPELL_EFFECTS 0x34u
#define SPELL_FLAGS 0xF4u
#define EFFECT_SIZE 0x18u
#define EFFECT_COUNT 8u
#define VT_FOR_NPCS 0xE8u
#define NPC_ATTRIBUTES 0x7Eu
#define NPC_SKILLS 0x86u
#define NPC_MAGICKA 0xA4u
#define NPC_SPELLS 0xD0u
#define FRAME_PLAYER 0x1Bu
#define FRAME_MINIMUM 0x10u
#define FRAME_FACTOR 0x34u
#define SLACK 64u

/* Effect kinds in a table record. */
enum { K_END, K_NONE, K_ATTRIBUTE, K_SKILL };

typedef struct {
    u32 spell;
    unsigned short cost;
    u8 for_npcs, pad;
    u8 kind[EFFECT_COUNT];
    u8 index[EFFECT_COUNT];
} record;

typedef u32(__attribute__((thiscall)) *fn_flag)(void *spell);

#define AT(p, off) (*(u32 *)((u32)(p) + (off)))

enum { MODE_UNREAD, MODE_ON, MODE_VERIFY, MODE_OFF };

static record *ac_tab;
static u32 ac_cap, ac_bytes, ac_n, ac_cur;
static u32 ac_list, ac_head, ac_tail, ac_count;
static u32 ac_builds, ac_checks, ac_mismatches, ac_walks, ac_digest, ac_prev, ac_skipped;
static u32 ac_npc, ac_magicka;
static int ac_mode, ac_plain, ac_filter, ac_minimum, ac_factor;

static u32 effect_flags(int id)
{
    return ((u32 *)TES3X_ACALC_EFFECTS)[id];
}

/* The tests that make a spell a candidate for anyone. */
static int candidate(u32 spell)
{
    if ((AT(spell, OBJ_FLAGS) >> 5) & 1)
        return 0;
    if (*(u8 *)(spell + SPELL_TYPE))
        return 0;
    if (((fn_flag)AT(AT(spell, 0), VT_FOR_NPCS))((void *)spell) & 0xFF)
        return 1;
    return (*(u8 *)(spell + SPELL_FLAGS) >> 1) & 1;
}

static void fill(record *r, u32 spell)
{
    u32 k;

    for (k = 0; k < EFFECT_COUNT; k++) {
        r->kind[k] = K_END;
        r->index[k] = 0;
    }
    r->spell = spell;
    r->cost = *(unsigned short *)(spell + SPELL_COST);
    r->for_npcs = ((fn_flag)AT(AT(spell, 0), VT_FOR_NPCS))((void *)spell) & 1;
    for (k = 0; k < EFFECT_COUNT; k++) {
        u32 e = spell + SPELL_EFFECTS + k * EFFECT_SIZE;
        int id = (short)*(unsigned short *)e;
        u32 flags;

        if (id == -1)
            break;
        flags = effect_flags(id);
        if (flags & 2) {
            r->kind[k] = K_ATTRIBUTE;
            r->index[k] = *(u8 *)(e + 3);
        } else if (flags & 1) {
            r->kind[k] = K_SKILL;
            r->index[k] = *(u8 *)(e + 2);
        } else {
            r->kind[k] = K_NONE;
        }
    }
}

/* Whether the routine rejects the spell for the NPC before any other test. */
static int rejects(const record *r)
{
    u32 k;

    if (!r->for_npcs)
        return 1;
    if ((int)ac_magicka < (int)r->cost * ac_factor)
        return 1;
    for (k = 0; k < EFFECT_COUNT && r->kind[k] != K_END; k++) {
        u32 v;

        if (r->kind[k] == K_NONE)
            continue;
        if (r->kind[k] == K_ATTRIBUTE)
            v = *(u8 *)(ac_npc + NPC_ATTRIBUTES + (int)(signed char)r->index[k]);
        else
            v = *(u8 *)(ac_npc + NPC_SKILLS + (int)(signed char)r->index[k]);
        if ((int)v < ac_minimum)
            return 1;
    }
    return 0;
}

static int build(u32 list)
{
    u32 node, n = 0, bytes, cap, i;
    record *fresh;

    for (node = AT(list, LIST_HEAD); node; node = AT(node, NODE_NEXT))
        n += candidate(node);
    cap = n + SLACK;
    bytes = (cap * sizeof(record) + 0xFFF) & ~0xFFFu;
    cap = bytes / sizeof(record);
    fresh = MmAllocateSystemMemory(bytes, PAGE_READWRITE);
    if (!fresh)
        return 0;
    for (i = 0; i < bytes / 4; i++)
        ((u32 *)fresh)[i] = 0;
    for (i = 0, node = AT(list, LIST_HEAD); node && i < cap; node = AT(node, NODE_NEXT)) {
        if (candidate(node))
            fill(&fresh[i++], node);
    }
    if (ac_tab)
        MmFreeSystemMemory(ac_tab, ac_bytes);
    ac_tab = fresh;
    ac_bytes = bytes;
    ac_cap = cap;
    ac_n = i;
    ac_list = list;
    ac_head = AT(list, LIST_HEAD);
    ac_tail = AT(list, LIST_TAIL);
    ac_count = AT(list, LIST_COUNT);
    ac_builds++;
    tes3x_log("acalc.cached", ac_n);
    return 1;
}

static void read_mode(void)
{
    char value[16];

    tes3x_ini_xbox("AutoCalcIndex", "on", value, sizeof(value));
    switch (value[0] | 0x20) {
    case 'v':
        ac_mode = MODE_VERIFY;
        break;
    case 'o':
        ac_mode = value[1] == 'f' || value[1] == 'F' ? MODE_OFF : MODE_ON;
        break;
    default:
        ac_mode = MODE_ON;
    }
    tes3x_log("acalc.mode", ac_mode);
}

static int current(u32 list)
{
    return ac_tab && list == ac_list && AT(list, LIST_HEAD) == ac_head &&
           AT(list, LIST_TAIL) == ac_tail && AT(list, LIST_COUNT) == ac_count;
}

/* The next accepted table index at or after ac_cur, or ac_n. */
static u32 settle(void)
{
    if (ac_filter) {
        while (ac_cur < ac_n && rejects(&ac_tab[ac_cur])) {
            ac_skipped++;
            ac_cur++;
        }
    }
    return ac_cur < ac_n ? ac_tab[ac_cur].spell : 0;
}

static int differ(const record *a, const record *b)
{
    u32 k;

    for (k = 0; k < EFFECT_COUNT; k++) {
        if (a->kind[k] != b->kind[k] || a->index[k] != b->index[k])
            return 1;
    }
    return 0;
}

/* Recomputes a spell's rejection from the live objects and compares it with the table. */
static void check_live(const record *r)
{
    record live;

    fill(&live, r->spell);
    ac_checks++;
    if (live.cost != r->cost || live.for_npcs != r->for_npcs ||
        differ(&live, r)) {
        ac_mismatches++;
        tes3x_log_hex3("acalc.mismatch", r->spell, live.cost, r->cost);
    }
    if (!(ac_checks & 0x3FFF)) {
        tes3x_log("acalc.checks", ac_checks);
        tes3x_log("acalc.skipped", ac_skipped);
    }
}

/* Folds the spells the previous NPC ended up with into a digest, for comparing runs: the
 * contents of each spell, which do not move between runs. */
static void digest_previous(void)
{
    u32 node;

    if (!ac_prev)
        return;
    ac_digest = ac_digest * 31u + ac_walks;
    for (node = AT(ac_prev, NPC_SPELLS); node; node = AT(node, 4))
        {
        u32 spell = AT(node, 8), k;

        ac_digest = ac_digest * 31u + AT(spell, SPELL_COST - 2);
        for (k = 0; k < EFFECT_COUNT * EFFECT_SIZE; k += 4)
            ac_digest = ac_digest * 31u + AT(spell, SPELL_EFFECTS + k);
    }
}

/* Replaces the call that fetches the list's first spell. LIST is in ECX, NPC in EBP and FRAME is
 * the routine's stack pointer. */
u32 __stdcall tes3x_acalc_first(u32 list, u32 npc, u32 frame)
{
    if (ac_mode == MODE_UNREAD)
        read_mode();
    digest_previous();
    ac_prev = npc;
    ac_walks++;
    if (!(ac_walks & 0xFF)) {
        tes3x_log_hex3("acalc.digest", ac_walks, ac_digest, ac_skipped);
    }
    ac_plain = ac_mode == MODE_OFF;
    if (!ac_plain && !current(list) && !build(list))
        ac_plain = 1;
    if (ac_plain)
        return AT(list, LIST_HEAD);
    ac_npc = npc;
    ac_filter = !*(u8 *)(frame + FRAME_PLAYER);
    ac_minimum = *(int *)(frame + FRAME_MINIMUM);
    ac_factor = *(int *)(frame + FRAME_FACTOR);
    ac_magicka = *(unsigned short *)(npc + NPC_MAGICKA);
    ac_cur = 0;
    return settle();
}

/* Replaces the call that fetches the spell after NODE. */
u32 __stdcall tes3x_acalc_next(u32 list, u32 node)
{
    u32 i, from;

    (void)list;
    if (ac_plain)
        return AT(node, NODE_NEXT);
    if (ac_cur >= ac_n || ac_tab[ac_cur].spell != node) {
        for (i = 0; i < ac_n && ac_tab[i].spell != node; i++)
            ;
        if (i == ac_n) {
            ac_plain = 1;
            return AT(node, NODE_NEXT);
        }
        ac_cur = i;
    }
    from = ++ac_cur;
    if (ac_mode == MODE_VERIFY) {
        for (i = from; i < ac_n; i++) {
            check_live(&ac_tab[i]);
            if (!ac_filter || !rejects(&ac_tab[i]))
                break;
        }
    }
    return settle();
}

__attribute__((naked)) void tes3x_acalc_first_hook(void)
{
    __asm__ volatile("leal 4(%esp), %eax\n\t"
                     "pushl %eax\n\t"
                     "pushl %ebp\n\t"
                     "pushl %ecx\n\t"
                     "call _tes3x_acalc_first@12\n\t"
                     "ret\n\t");
}

__attribute__((naked)) void tes3x_acalc_next_hook(void)
{
    __asm__ volatile("movl 4(%esp), %eax\n\t"
                     "pushl %eax\n\t"
                     "pushl %ecx\n\t"
                     "call _tes3x_acalc_next@8\n\t"
                     "ret $4\n\t");
}
