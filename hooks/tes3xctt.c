/* Can't Touch This: play a dodge when a melee attack misses a calm NPC. */

#include "tes3xanimvirtual.h"

typedef u8 *(__attribute__((thiscall)) *fn_ref_animation)(const u8 *);

#define CTT_RANGE(n) \
    extern const unsigned long tes3x_word_CTT_START_##n; \
    extern const unsigned long tes3x_word_CTT_STOP_##n;
CTT_RANGE(0) CTT_RANGE(1) CTT_RANGE(2) CTT_RANGE(3) CTT_RANGE(4) CTT_RANGE(5)
CTT_RANGE(6) CTT_RANGE(7) CTT_RANGE(8) CTT_RANGE(9) CTT_RANGE(10)
#undef CTT_RANGE
#define CTT_PAIR(n) {&tes3x_word_CTT_START_##n, &tes3x_word_CTT_STOP_##n}
static const unsigned long *const clip_bits[11][2] = {
    CTT_PAIR(0), CTT_PAIR(1), CTT_PAIR(2), CTT_PAIR(3), CTT_PAIR(4), CTT_PAIR(5),
    CTT_PAIR(6), CTT_PAIR(7), CTT_PAIR(8), CTT_PAIR(9), CTT_PAIR(10)
};
#undef CTT_PAIR

static unsigned int seed;
static unsigned int misses, dodges;

static float clip_time(unsigned int clip, unsigned int end)
{
    union { unsigned long bits; float value; } time;
    time.bits = *clip_bits[clip][end];
    return time.value;
}

int tes3x_ctt_clip_range(unsigned int clip, float *start, float *stop)
{
    if (clip >= 11)
        return 0;
    *start = clip_time(clip, 0);
    *stop = clip_time(clip, 1);
    return *start >= 0.0f && *start < *stop;
}

static unsigned int next_random(void)
{
    if (!seed) {
        unsigned int low, high;
        __asm__ volatile("rdtsc" : "=a"(low), "=d"(high));
        seed = (low ^ high) | 1;
    }
    seed ^= seed << 13;
    seed ^= seed >> 17;
    seed ^= seed << 5;
    return seed;
}

/* A living NPC standing ready: not the player, a creature, paralyzed, hurt or knocked down. */
static int can_dodge(const u8 *mobile, const u8 *animation)
{
    unsigned int state = mobile[0xDD], i;
    if (mobile[0x228] != 1)
        return 0;
    if (state != 0 && !(state == 1 && mobile[0xE2] != 34))
        return 0;
    if (*(const int *)(mobile + 0x348) > 0)
        return 0;
    for (i = 0; i < 3; ++i)
        if (animation[0x38 + i] >= 19 && animation[0x38 + i] <= 26)
            return 0;
    return 1;
}

static void dodge(const u8 *mobile)
{
    const u8 *ref = *(const u8 *const *)(mobile + 0x14);
    u8 *animation = ref ? ((fn_ref_animation)TES3X_CTT_REF_ANIMATION)(ref) : 0;
    unsigned int clip = next_random() % 11;
    float start, stop;

    if (!animation || !can_dodge(mobile, animation))
        return;
    if (!tes3x_ctt_clip_range(clip, &start, &stop)) {
        tes3x_log("ctt.no_range", clip);
        return;
    }
    if (tes3x_animation_virtual_play(ref, animation, start, stop) && ++dodges <= 64)
        tes3x_log("ctt.dodge", clip);
}

__attribute__((naked)) static u8 __cdecl original_roll(u8 *attacker, void *projectile,
                                                      unsigned int *magic_result)
{
    __asm__ volatile(
        "pushl %ebp\n\t"
        "movl %esp, %ebp\n\t"
        "andl $-8, %esp\n\t"
        "pushl $" TES3X_SYM(CTT_ROLL_RESUME) "\n\t"
        "ret\n\t");
}

u8 __cdecl tes3x_ctt_roll(u8 *attacker, void *projectile, unsigned int *magic_result)
{
    u8 result = original_roll(attacker, projectile, magic_result);
    u8 *target = attacker ? *(u8 **)(attacker + 0xF0) : 0;

    if (!result && !projectile && target && target != attacker) {
        if (++misses <= 64)
            tes3x_log("ctt.miss", misses);
        dodge(target);
    }
    return result;
}
