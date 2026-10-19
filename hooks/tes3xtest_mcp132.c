/* Read an NPC's three current animation groups after a scripted PlayGroup. */

#include "tes3xlog.h"

typedef u8 *(__attribute__((thiscall)) *fn_find_reference)(void *, const char *);
typedef u8 *(__attribute__((thiscall)) *fn_ref_animation)(const u8 *);
typedef u8 *(__attribute__((thiscall)) *fn_get_player)(void *);
typedef void(__attribute__((thiscall)) *fn_play_group)(void *, int, int, int, int);

typedef struct {
    u8 group[0x2C];
    float times[4];
    u8 *animation;
    u8 *original;
    u8 *mobile;
    unsigned int had_scripted;
} virtual_group;

static virtual_group dodge_test[2];
static float dodge_start = 348.4341f;
static float dodge_stop = 348.9341f;

static void release_virtual(virtual_group *slot, int restore)
{
    u8 *animation = slot->animation;
    if (restore && animation) {
        if (*(u8 **)(animation + 0x68 + 8 * 4) == slot->group) {
            *(u8 **)(animation + 0x68 + 8 * 4) = slot->original;
            if (slot->mobile && !slot->had_scripted)
                *(unsigned int *)(slot->mobile + 0x10) &= ~0x10000000u;
        }
    }
    slot->animation = 0;
    slot->mobile = 0;
    slot->original = 0;
}

void tes3x_animation_virtual_dispose(void *animation)
{
    unsigned int i;
    for (i = 0; i < 2; ++i) {
        if (dodge_test[i].animation == animation) {
            release_virtual(&dodge_test[i], 0);
            tes3x_log("mcp132.virtual_disposed", i + 1);
        }
    }
}

void tes3x_animation_virtual_stock_request(void *animation, int group)
{
    unsigned int i;
    if (group != 8)
        return;
    for (i = 0; i < 2; ++i) {
        if (dodge_test[i].animation == animation) {
            release_virtual(&dodge_test[i], 1);
            tes3x_log("mcp132.virtual_stock_request", i + 1);
        }
    }
}

void tes3x_animation_virtual_maintain(void)
{
    unsigned int i;
    virtual_group *slot;
    u8 *animation;
    for (i = 0; i < 2; ++i) {
        slot = &dodge_test[i];
        animation = slot->animation;
        if (!animation || animation[0x38] == 8 || animation[0x39] == 8 ||
            animation[0x3A] == 8)
            continue;
        release_virtual(slot, 1);
        tes3x_log("mcp132.virtual_auto_restore", i + 1);
    }
}

static void copy_bytes(u8 *dst, const u8 *src, unsigned int n)
{
    unsigned int i;
    for (i = 0; i < n; ++i)
        dst[i] = src[i];
}

static u8 *mobile_of(const u8 *ref)
{
    const u8 *attachment = *(const u8 *const *)(ref + 0x44);
    unsigned int i;
    for (i = 0; attachment && i != 32; ++i) {
        if (*(const unsigned int *)attachment == 8)
            return *(u8 *const *)(attachment + 8);
        attachment = *(const u8 *const *)(attachment + 4);
    }
    return 0;
}

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
    virtual_group *slot = (dodge_vodunius || restore_vodunius) ? &dodge_test[1] : &dodge_test[0];
    u8 *group;
    unsigned int i;

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
        if (slot->animation == animation &&
            *(u8 *const *)(animation + 0x68 + 8 * 4) == slot->group) {
            release_virtual(slot, 1);
            tes3x_log("mcp132.virtual_restored", 1);
        } else {
            tes3x_log("mcp132.virtual_restored", 0);
        }
        return 1;
    }
    if (dodge_fargoth || dodge_vodunius) {
        group = *(u8 **)(animation + 0x68 + 8 * 4);
        if (!group || (slot->animation && slot->animation != animation) ||
            *(unsigned int *)(group + 0x14) != 4) {
            tes3x_log("mcp132.virtual_ready", 0);
            return 1;
        }
        if (group != slot->group) {
            copy_bytes(slot->group, group, sizeof(slot->group));
            slot->original = group;
            slot->animation = (u8 *)animation;
            slot->mobile = mobile_of(ref);
            slot->had_scripted = slot->mobile &&
                (*(unsigned int *)(slot->mobile + 0x10) & 0x10000000u);
            *(float **)(slot->group + 0x1C) = slot->times;
            *(u8 **)(animation + 0x68 + 8 * 4) = slot->group;
        }
        slot->times[0] = dodge_start;
        slot->times[1] = dodge_stop;
        slot->times[2] = dodge_start;
        slot->times[3] = dodge_stop;
        if (slot->mobile)
            *(unsigned int *)(slot->mobile + 0x10) |= 0x10000000u;
        for (i = 0; i != 3; ++i)
            ((fn_play_group)TES3X_MCP132_TEST_PLAY_GROUP)((void *)animation, 8, i, 1, 1);
        tes3x_log("mcp132.virtual_ready", 1);
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
