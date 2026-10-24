/* Game-test probe for the alchemy menu. */

#include "tes3xlog.h"

#if !defined(TES3X_UI_ID) || !defined(TES3X_FIND_MENU) || \
    !defined(TES3X_FIND_CHILD) || !defined(TES3X_GET_PROP) || \
    !defined(TES3X_WIDGET_TEXT) || !defined(TES3X_WIDGET_SET_TEXT) || \
    !defined(TES3X_GAME_PTR)
#error "mcp-55 test needs console UI bindings"
#endif

typedef unsigned int(__cdecl *fn_ui_id)(const char *name);
typedef void *(__cdecl *fn_find_menu)(unsigned int id);
typedef void *(__attribute__((thiscall)) *fn_find_child)(void *widget, unsigned int id);
typedef void *(__attribute__((thiscall)) *fn_get_prop)(void *widget, void *out,
                                                       unsigned int id, int type,
                                                       int a3, int a4);
typedef const char *(__attribute__((thiscall)) *fn_text)(void *widget);
typedef void(__attribute__((thiscall)) *fn_set_text)(void *widget, const char *text);

static int same(const char *a, const char *b)
{
    while (*a && *a == *b) {
        a++;
        b++;
    }
    return *a == *b;
}

static void *field(void *menu, const char *name)
{
    return menu ? ((fn_find_child)TES3X_FIND_CHILD)(
        menu, ((fn_ui_id)TES3X_UI_ID)(name)) : 0;
}

static void *item(void *widget)
{
    u32 out[8] = {0};
    void **slot;
    if (!widget)
        return 0;
    slot = (void **)((fn_get_prop)TES3X_GET_PROP)(
        widget, out, ((fn_ui_id)TES3X_UI_ID)("MenuAlchemy_object"), 8, 0, 0);
    return slot ? *slot : 0;
}

int tes3x_mcp55_test_command(const char *text)
{
    void *menu, *name, *world, *inventory_data;
    void **first, **end;
    const char *actual;

    if (!same(text, "tes3xmcp55 name") && !same(text, "tes3xmcp55 state") &&
        !same(text, "tes3xmcp55 stale"))
        return 0;
    world = *(void **)TES3X_GAME_PTR;
    inventory_data = world ? *(void **)((u8 *)world + 0x2c4) : 0;
    if (same(text, "tes3xmcp55 stale")) {
        void **current;
        first = inventory_data ? *(void ***)((u8 *)inventory_data + 4) : 0;
        end = inventory_data ? *(void ***)((u8 *)inventory_data + 8) : 0;
        for (current = first; current && current < end; current++) {
            if (*(u32 *)((u8 *)*current + 0x3c) == 2) {
                for (; current + 1 < end; current++)
                    *current = current[1];
                *(void ***)((u8 *)inventory_data + 8) = end - 1;
                tes3x_log("mcp55.stale", 1);
                return 1;
            }
        }
        tes3x_log("mcp55.stale", 0);
        return 1;
    }
    menu = ((fn_find_menu)TES3X_FIND_MENU)(
        ((fn_ui_id)TES3X_UI_ID)("MenuAlchemy"));
    name = field(menu, "MenuAlchemy_potion_name");
    if (same(text, "tes3xmcp55 name")) {
        if (name)
            ((fn_set_text)TES3X_WIDGET_SET_TEXT)(name, "TES3X Potion");
        tes3x_log("mcp55.name_set", name != 0);
        return 1;
    }
    actual = name ? ((fn_text)TES3X_WIDGET_TEXT)(name) : 0;
    tes3x_log("mcp55.menu", menu != 0);
    tes3x_log("mcp55.name_custom", actual && same(actual, "TES3X Potion"));
    tes3x_log("mcp55.slot1", item(field(menu, "MenuAlchemy_ingredient_one")) != 0);
    tes3x_log("mcp55.slot2", item(field(menu, "MenuAlchemy_ingredient_two")) != 0);
    tes3x_log("mcp55.list_item", item(field(menu, "MenuAlchemy_thing")) != 0);
    first = inventory_data ? *(void ***)((u8 *)inventory_data + 4) : 0;
    end = inventory_data ? *(void ***)((u8 *)inventory_data + 8) : 0;
    tes3x_log("mcp55.tiles", first && end ? (u32)(end - first) : 0);
    return 1;
}
