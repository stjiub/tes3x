/* Read an NPC's three current animation groups after a scripted PlayGroup. */

#include "tes3xlog.h"

typedef u8 *(__attribute__((thiscall)) *fn_find_reference)(void *, const char *);
typedef u8 *(__attribute__((thiscall)) *fn_ref_animation)(const u8 *);
typedef u8 *(__attribute__((thiscall)) *fn_get_player)(void *);

static int same(const char *a, const char *b)
{
    while (*a && *a == *b) {
        ++a;
        ++b;
    }
    return *a == *b;
}

int tes3x_mcp132_test_command(const char *text)
{
    const u8 *handler, *ref, *animation, *player;
    void *records;
    int is_player = same(text, "tes3xmcp132 player");

    if (!is_player && !same(text, "tes3xmcp132"))
        return 0;
    if (is_player) {
        void *world = *(void **)TES3X_MCP132_TEST_WORLD;
        player = world ? ((fn_get_player)TES3X_MCP132_TEST_GET_PLAYER)(world) : 0;
        ref = player ? *(const u8 *const *)(player + 0x14) : 0;
    } else {
        handler = *(const u8 **)TES3X_MCP132_TEST_RECORDS;
        records = handler ? *(void **)handler : 0;
        ref = records ? ((fn_find_reference)TES3X_MCP132_TEST_FIND_REF)(records, "fargoth") : 0;
    }
    animation = ref ? ((fn_ref_animation)TES3X_MCP132_TEST_ANIMATION)(ref) : 0;
    if (!animation) {
        tes3x_log(is_player ? "mcp132.player_ready" : "mcp132.ready", 0);
        return 1;
    }
    tes3x_log(is_player ? "mcp132.player_ready" : "mcp132.ready", 1);
    tes3x_log_hex(is_player ? "mcp132.player_groups" : "mcp132.groups",
                  animation[0x38] | (animation[0x39] << 8) |
                  (animation[0x3A] << 16));
    return 1;
}
