/* Test the melee miss event with the actor-local dodge probe. */

#include "tes3xlog.h"

typedef u8(__attribute__((cdecl)) *fn_hit_roll)(u8 *, void *, unsigned int *);
typedef u8 *(__attribute__((thiscall)) *fn_find_reference)(void *, const char *);

TES3X_OPTIONAL int tes3x_mcp132_test_command(const char *text);

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

static u8 *fargoth_mobile(void)
{
    const u8 *handler = *(const u8 **)TES3X_CTT_TEST_RECORDS;
    void *records = handler ? *(void **)handler : 0;
    u8 *ref = records ? ((fn_find_reference)TES3X_CTT_TEST_FIND_REF)(records, "fargoth") : 0;
    return ref ? mobile_of(ref) : 0;
}

__attribute__((naked)) static u8 __cdecl original_roll(u8 *attacker, void *projectile,
                                                      unsigned int *magic_result)
{
    __asm__ volatile(
        "pushl %ebp\n\t"
        "movl %esp, %ebp\n\t"
        "andl $-8, %esp\n\t"
        "pushl $" TES3X_SYM(CTT_TEST_ROLL_RESUME) "\n\t"
        "ret\n\t");
}

u8 __cdecl tes3x_ctt_test_roll(u8 *attacker, void *projectile,
                               unsigned int *magic_result)
{
    u8 result = original_roll(attacker, projectile, magic_result);
    u8 *target = attacker ? *(u8 **)(attacker + 0xF0) : 0;

    if (!projectile && target && target == fargoth_mobile()) {
        tes3x_log(result ? "ctt.test.hit" : "ctt.test.miss", 1);
        if (!result && tes3x_mcp132_test_command)
            tes3x_mcp132_test_command("tes3xmcp132 dodge_fargoth");
    }
    return result;
}
