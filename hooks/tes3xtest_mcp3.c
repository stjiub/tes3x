/* Test-only command for the fully unarmored physical-damage calculation. */

#include "tes3xlog.h"

#ifndef TES3X_GAME_PTR
#error "define TES3X_GAME_PTR to the game object pointer"
#endif
#ifndef TES3X_PLAYER_MOBILE
#error "define TES3X_PLAYER_MOBILE to WorldController::getMobilePlayer"
#endif

#define ARMOR_DAMAGE_SLOT 56
#define ARMOR_RATING_SLOT 57
#define TEST_DAMAGE 50.0f

typedef void *(__attribute__((thiscall)) *fn_player_mobile)(void *game);
typedef float(__attribute__((thiscall)) *fn_armor_rating)(void *mobile, u32 *equipped);
typedef float(__attribute__((thiscall)) *fn_armor_damage)(void *mobile, float damage,
                                                          float original, int damage_armor);

__attribute__((weak)) int _fltused;

static int text_equal(const char *a, const char *b)
{
    while (*a && *a == *b) {
        a++;
        b++;
    }
    return *a == *b;
}

static u32 milli(float value)
{
    if (value <= 0.0f)
        return 0;
    return (u32)(value * 1000.0f + 0.5f);
}

int tes3x_mcp3_test_command(const char *text)
{
    void *game, *mobile;
    void **vtable;
    u32 equipped = 0;
    float rating, damage;

    if (!text_equal(text, "tes3xmcp3"))
        return 0;
    game = *(void **)TES3X_GAME_PTR;
    mobile = game ? ((fn_player_mobile)TES3X_PLAYER_MOBILE)(game) : 0;
    if (!mobile) {
        tes3x_log("mcp3.ready", 0);
        return 1;
    }
    vtable = *(void ***)mobile;
    rating = ((fn_armor_rating)vtable[ARMOR_RATING_SLOT])(mobile, &equipped);
    tes3x_log("mcp3.armor_count", equipped);
    tes3x_log("mcp3.rating", milli(rating));
    if (equipped != 0) {
        tes3x_log("mcp3.ready", 0);
        return 1;
    }
    damage = ((fn_armor_damage)vtable[ARMOR_DAMAGE_SLOT])(
        mobile, TEST_DAMAGE, TEST_DAMAGE, 0);
    tes3x_log("mcp3.damage", milli(damage));
    return 1;
}
