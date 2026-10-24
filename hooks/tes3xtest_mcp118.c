/* Invoke the menu's MobileActor drop path from an exec script. */

#include "tes3xlog.h"

#if !defined(TES3X_GAME_PTR) || !defined(TES3X_PLAYER_MOBILE) || \
    !defined(TES3X_RECORDS_PTR) || !defined(TES3X_RESOLVE_OBJECT) || \
    !defined(TES3X_MCP118_TEST_DROP)
#error "mcp-118 test needs console object and player locators"
#endif

typedef void *(__attribute__((thiscall)) *fn_player_mobile)(void *world);
typedef void *(__attribute__((thiscall)) *fn_resolve_object)(void *records, const char *id);
typedef u32 (__attribute__((thiscall)) *fn_drop_item)(void *mobile, void *item,
                                                      void *data, int count, int flag);

static int same(const char *a, const char *b)
{
    while (*a && *a == *b) {
        a++;
        b++;
    }
    return *a == *b;
}

int tes3x_mcp118_test_command(const char *text)
{
    void *world, **handler, *records, *mobile, *item;
    u32 result = 0;

    if (!same(text, "tes3xmcp118 drop"))
        return 0;
    world = *(void **)TES3X_GAME_PTR;
    handler = *(void ***)TES3X_RECORDS_PTR;
    records = handler ? *handler : 0;
    mobile = world ? ((fn_player_mobile)TES3X_PLAYER_MOBILE)(world) : 0;
    item = records ? ((fn_resolve_object)TES3X_RESOLVE_OBJECT)(
        records, "misc_com_bottle_01") : 0;
    if (mobile && item)
        result = ((fn_drop_item)TES3X_MCP118_TEST_DROP)(mobile, item, 0, 1, 1);
    tes3x_log("mcp118.drop", mobile && item ? result : 0);
    return 1;
}
