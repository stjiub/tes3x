/* Test-only selection and state probe for NPC magic-item unequipping. */

#include "tes3xlog.h"

#if !defined(TES3X_GAME_PTR) || !defined(TES3X_PLAYER_MOBILE) || \
    !defined(TES3X_RECORDS_PTR) || !defined(TES3X_RESOLVE_OBJECT) || \
    !defined(TES3X_CLOSEST_REF)
#error "mcp-47 test needs console object and player locators"
#endif

#define MOBILE_REF 0x14
#define MOBILE_SPELL 0x378
#define MOBILE_SPELL_TYPE 0x37C
#define MOBILE_ITEM 0x380
#define REF_INSTANCE 0x28
#define REF_POSITION 0x38
#define REF_ATTACHMENTS 0x44
#define NPC_BASE 0x6C
#define NPC_SPELLS_HEAD 0xD0
#define SPELL_ID 0x28

typedef void *(__attribute__((thiscall)) *fn_player_mobile)(void *world);
typedef void *(__attribute__((thiscall)) *fn_resolve_object)(void *records, const char *id);
typedef void *(__attribute__((thiscall)) *fn_closest_ref)(void *records, void *object,
                                                         const float *position, int any_cell,
                                                         int unknown);

static int same(const char *a, const char *b)
{
    while (*a && *a == *b) {
        a++;
        b++;
    }
    return *a == *b;
}

static void *mobile_of(const u8 *ref)
{
    const u8 *node = *(const u8 *const *)(ref + REF_ATTACHMENTS);
    u32 n;
    for (n = 0; node && n < 32; n++, node = *(const u8 *const *)(node + 4))
        if (*(const u32 *)node == 8)
            return *(void *const *)(node + 8);
    return 0;
}

static void *known_spell(const u8 *player)
{
    const u8 *ref = *(const u8 *const *)(player + MOBILE_REF);
    const u8 *instance = ref ? *(const u8 *const *)(ref + REF_INSTANCE) : 0;
    const u8 *npc = instance ? *(const u8 *const *)(instance + NPC_BASE) : 0;
    const u8 *node = npc ? *(const u8 *const *)(npc + NPC_SPELLS_HEAD) : 0;
    u32 n;
    for (n = 0; node && n < 256; n++, node = *(const u8 *const *)(node + 4)) {
        const u8 *spell = *(const u8 *const *)(node + 8);
        const char *id = spell ? *(const char *const *)(spell + SPELL_ID) : 0;
        if (id && same(id, "fire bite"))
            return (void *)spell;
    }
    return 0;
}

int tes3x_mcp47_test_command(const char *text)
{
    void *world, *player, **handler, *records, *fargoth, *ring, *ref, *npc;
    const u8 *player_ref;
    const char *mode;

    if (!same(text, "tes3xmcp47 npc") && !same(text, "tes3xmcp47 player") &&
        !same(text, "tes3xmcp47 state"))
        return 0;
    mode = text + 11;
    world = *(void **)TES3X_GAME_PTR;
    handler = *(void ***)TES3X_RECORDS_PTR;
    records = handler ? *handler : 0;
    player = world ? ((fn_player_mobile)TES3X_PLAYER_MOBILE)(world) : 0;
    player_ref = player ? *(const u8 *const *)((const u8 *)player + MOBILE_REF) : 0;
    if (!records || !player_ref) {
        tes3x_log("mcp47.ready", 0);
        return 1;
    }
    fargoth = ((fn_resolve_object)TES3X_RESOLVE_OBJECT)(records, "fargoth");
    ring = ((fn_resolve_object)TES3X_RESOLVE_OBJECT)(records, "ring_keley");
    ref = fargoth ? ((fn_closest_ref)TES3X_CLOSEST_REF)(
        records, fargoth, (const float *)(player_ref + REF_POSITION), 0, -1) : 0;
    npc = ref ? mobile_of(ref) : 0;
    if (!ring || !npc) {
        tes3x_log("mcp47.ready", 0);
        return 1;
    }
    if (same(mode, "npc") || same(mode, "player")) {
        void *spell = known_spell(player);
        if (!spell) {
            tes3x_log("mcp47.spell_found", 0);
            return 1;
        }
        *(void **)((u8 *)player + MOBILE_SPELL) = spell;
        *(u32 *)((u8 *)player + MOBILE_SPELL_TYPE) = 1;
        if (same(mode, "npc"))
            *(void **)((u8 *)npc + MOBILE_ITEM) = ring;
        else
            *(void **)((u8 *)player + MOBILE_ITEM) = ring;
        tes3x_log("mcp47.spell_found", 1);
    }
    tes3x_log("mcp47.player_selected", *(void **)((u8 *)player + MOBILE_SPELL) != 0);
    tes3x_log("mcp47.npc_item_selected", *(void **)((u8 *)npc + MOBILE_ITEM) == ring);
    tes3x_log("mcp47.player_item_selected", *(void **)((u8 *)player + MOBILE_ITEM) == ring);
    return 1;
}
