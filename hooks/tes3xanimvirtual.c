/* Actor-local playback of a KF interval through the stock Idle9 descriptor. */

#include "tes3xanimvirtual.h"

typedef void(__attribute__((thiscall)) *fn_play_group)(void *, int, int, int, int);

typedef struct {
    u8 group[0x2C];
    float times[4];
    u8 *animation;
    u8 *original;
    u8 *mobile;
    unsigned int had_scripted;
} virtual_group;

static virtual_group slots[16];

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

static int release_slot(virtual_group *slot, int restore)
{
    u8 *animation = slot->animation;
    int owned = 0;
    if (restore && animation && *(u8 **)(animation + 0x68 + 8 * 4) == slot->group) {
        *(u8 **)(animation + 0x68 + 8 * 4) = slot->original;
        if (slot->mobile && !slot->had_scripted)
            *(unsigned int *)(slot->mobile + 0x10) &= ~0x10000000u;
        owned = 1;
    }
    slot->animation = 0;
    slot->mobile = 0;
    slot->original = 0;
    return owned;
}

int tes3x_animation_virtual_restore(void *animation)
{
    unsigned int i;
    for (i = 0; i < 16; ++i)
        if (slots[i].animation == animation)
            return release_slot(&slots[i], 1);
    return 0;
}

void tes3x_animation_virtual_dispose(void *animation)
{
    unsigned int i;
    for (i = 0; i < 16; ++i) {
        if (slots[i].animation == animation) {
            release_slot(&slots[i], 0);
            tes3x_log("anim.virtual_disposed", i + 1);
        }
    }
}

void tes3x_animation_virtual_stock_request(void *animation, int group)
{
    if (group == 8 && tes3x_animation_virtual_restore(animation))
        tes3x_log("anim.virtual_stock_request", 1);
}

void tes3x_animation_virtual_maintain(void)
{
    unsigned int i;
    u8 *animation;
    for (i = 0; i < 16; ++i) {
        animation = slots[i].animation;
        if (!animation || animation[0x38] == 8 || animation[0x39] == 8 ||
            animation[0x3A] == 8)
            continue;
        release_slot(&slots[i], 1);
        tes3x_log("anim.virtual_auto_restore", i + 1);
    }
}

int tes3x_animation_virtual_play(const u8 *ref, u8 *animation, float start, float stop)
{
    virtual_group *slot = 0;
    u8 *group, *mobile;
    unsigned int i;
    if (!ref || !animation || !(start >= 0.0f && start < stop))
        return 0;
    mobile = mobile_of(ref);
    if (!mobile)
        return 0;
    for (i = 0; i < 16; ++i) {
        if (slots[i].animation == animation) {
            slot = &slots[i];
            break;
        }
        if (!slot && !slots[i].animation)
            slot = &slots[i];
    }
    if (!slot)
        return 0;
    if (slot->animation == animation && slot->mobile != mobile)
        return 0;
    group = *(u8 **)(animation + 0x68 + 8 * 4);
    if (slot->animation == animation && group != slot->group) {
        release_slot(slot, 0);
        return 0;
    }
    if (!group || *(unsigned int *)(group + 0x14) != 4)
        return 0;
    if (slot->animation != animation) {
        if (animation[0x38] == 8 || animation[0x39] == 8 || animation[0x3A] == 8)
            return 0;
        copy_bytes(slot->group, group, sizeof(slot->group));
        slot->original = group;
        slot->animation = animation;
        slot->mobile = mobile;
        slot->had_scripted = slot->mobile &&
            (*(unsigned int *)(slot->mobile + 0x10) & 0x10000000u);
        *(float **)(slot->group + 0x1C) = slot->times;
        *(u8 **)(animation + 0x68 + 8 * 4) = slot->group;
    }
    slot->times[0] = start;
    slot->times[1] = stop;
    slot->times[2] = start;
    slot->times[3] = stop;
    if (slot->mobile)
        *(unsigned int *)(slot->mobile + 0x10) |= 0x10000000u;
    for (i = 0; i < 3; ++i)
        ((fn_play_group)TES3X_ANIM_VIRTUAL_PLAY_GROUP)(animation, 8, i, 1, 1);
    return 1;
}
