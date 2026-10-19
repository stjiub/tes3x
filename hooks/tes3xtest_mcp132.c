/* Read an NPC's three current animation groups after a scripted PlayGroup. */

#include "tes3xanimvirtual.h"

typedef u8 *(__attribute__((thiscall)) *fn_find_reference)(void *, const char *);
typedef u8 *(__attribute__((thiscall)) *fn_ref_animation)(const u8 *);
typedef u8 *(__attribute__((thiscall)) *fn_get_player)(void *);
static float dodge_start = 348.4341f;
static float dodge_stop = 348.9341f;

static int same(const char *a, const char *b)
{
    while (*a && *a == *b) {
        ++a;
        ++b;
    }
    return *a == *b;
}

static const char *after_prefix(const char *text, const char *prefix)
{
    while (*prefix && *text == *prefix) {
        ++text;
        ++prefix;
    }
    return *prefix ? 0 : text;
}

static const char *decimal(const char *text, float *value)
{
    float whole = 0.0f, fraction = 0.0f, scale = 0.1f;
    int digits = 0;
    while (*text >= '0' && *text <= '9' && digits < 5) {
        whole = whole * 10.0f + (float)(*text++ - '0');
        ++digits;
    }
    if (!digits || (*text >= '0' && *text <= '9'))
        return 0;
    if (*text == '.') {
        ++text;
        while (*text >= '0' && *text <= '9' && digits < 11) {
            fraction += (float)(*text++ - '0') * scale;
            scale *= 0.1f;
            ++digits;
        }
        if (*text >= '0' && *text <= '9')
            return 0;
    }
    *value = whole + fraction;
    return text;
}

int tes3x_mcp132_test_command(const char *text)
{
    const u8 *handler, *ref, *animation, *player;
    void *records;
    int is_player = same(text, "tes3xmcp132 player");
    int dodge_fargoth = same(text, "tes3xmcp132 dodge_fargoth");
    int dodge_vodunius = same(text, "tes3xmcp132 dodge_vodunius");
    int restore_fargoth = same(text, "tes3xmcp132 restore_fargoth");
    int restore_vodunius = same(text, "tes3xmcp132 restore_vodunius");
    const char *range = after_prefix(text, "tes3xmcp132 range ");

    if (range) {
        float start, stop;
        range = decimal(range, &start);
        if (!range || *range++ != ' ')
            goto bad_range;
        range = decimal(range, &stop);
        if (!range || *range || start >= stop || stop > 10000.0f)
            goto bad_range;
        dodge_start = start;
        dodge_stop = stop;
        tes3x_log("mcp132.virtual_range", (unsigned int)(start * 1000.0f));
        return 1;
    bad_range:
        tes3x_log("mcp132.virtual_bad_range", 1);
        return 1;
    }
    if (!is_player && !same(text, "tes3xmcp132") && !dodge_fargoth &&
        !dodge_vodunius && !restore_fargoth && !restore_vodunius)
        return 0;
    if (is_player) {
        void *world = *(void **)TES3X_MCP132_TEST_WORLD;
        player = world ? ((fn_get_player)TES3X_MCP132_TEST_GET_PLAYER)(world) : 0;
        ref = player ? *(const u8 *const *)(player + 0x14) : 0;
    } else {
        handler = *(const u8 **)TES3X_MCP132_TEST_RECORDS;
        records = handler ? *(void **)handler : 0;
        ref = records ? ((fn_find_reference)TES3X_MCP132_TEST_FIND_REF)(
            records, (dodge_vodunius || restore_vodunius) ? "vodunius nuccius" : "fargoth") : 0;
    }
    animation = ref ? ((fn_ref_animation)TES3X_MCP132_TEST_ANIMATION)(ref) : 0;
    if (!animation) {
        tes3x_log(is_player ? "mcp132.player_ready" : "mcp132.ready", 0);
        return 1;
    }
    if (restore_fargoth || restore_vodunius) {
        tes3x_log("mcp132.virtual_restored",
                  tes3x_animation_virtual_restore((void *)animation));
        return 1;
    }
    if (dodge_fargoth || dodge_vodunius) {
        int played = tes3x_animation_virtual_play(ref, (u8 *)animation,
                                                   dodge_start, dodge_stop);
        tes3x_log("mcp132.virtual_ready", played);
        if (played)
            tes3x_log_hex("mcp132.virtual_layers",
                          animation[0x38] | (animation[0x39] << 8) |
                          (animation[0x3A] << 16));
        return 1;
    }
    tes3x_log(is_player ? "mcp132.player_ready" : "mcp132.ready", 1);
    tes3x_log_hex(is_player ? "mcp132.player_groups" : "mcp132.groups",
                  animation[0x38] | (animation[0x39] << 8) |
                  (animation[0x3A] << 16));
    return 1;
}
