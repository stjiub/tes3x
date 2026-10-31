/* Take an item from a container through the engine's own contents menu, without waiting on it. */
#include "tes3xlog.h"

#if !defined(TES3X_QL_SHOW) || !defined(TES3X_QL_TRANSFER) || !defined(TES3X_QL_MENU_ID) || \
    !defined(TES3X_QL_TILE_ID) || !defined(TES3X_QL_WORLD) || !defined(TES3X_QL_FIND_MENU) || \
    !defined(TES3X_QL_SET_PROP) || !defined(TES3X_QL_CLOSE)
#error "quick-loot needs its engine bindings"
#endif

#define TILE_LIST 4
#define PROP_PTR 8

typedef void(__cdecl *fn_show)(void *, void *, void *, char);
typedef void *(__cdecl *fn_find_menu)(unsigned int);
typedef void(__attribute__((thiscall)) *fn_set_prop)(void *, unsigned int, int, int);
typedef int(__cdecl *fn_transfer)(void *, int, void *, char);
typedef int(__cdecl *fn_close)(int, int, int, int, int);

/* Index of the contents tile to take plus one; 0 opens the menu as retail does. */
static int pending;

static int starts_with(const char *s, const char *prefix)
{
    while (*prefix && *s == *prefix) {
        s++;
        prefix++;
    }
    return !*prefix;
}

static u32 mode(void)
{
    u8 *world = *(u8 **)TES3X_QL_WORLD;
    return world ? world[0xd2] : 0xffu;
}

static void take(void)
{
    u8 *world, *inventory;
    void **tile, **end, *menu, *chosen = 0;
    int want = pending - 1, total = 0, count;

    pending = 0;
    world = *(u8 **)TES3X_QL_WORLD;
    inventory = world ? *(u8 **)(world + 0x2c4) : 0;
    menu = ((fn_find_menu)TES3X_QL_FIND_MENU)(*(unsigned short *)TES3X_QL_MENU_ID);
    if (!inventory || !menu) {
        tes3x_log("quickloot.no_menu", menu != 0);
        return;
    }
    tile = *(void ***)(inventory + 4);
    end = *(void ***)(inventory + 8);
    for (; tile && tile < end; tile++) {
        if (*(int *)((u8 *)*tile + 0x3c) != TILE_LIST)
            continue;
        if (total++ == want)
            chosen = *tile;
    }
    tes3x_log("quickloot.tiles", (u32)total);
    if (!chosen) {
        ((fn_close)TES3X_QL_CLOSE)(0, 0, 0, 0, 0);
        if (!world[0xd2])
            world[0xd0] = 0;
        return;
    }
    ((fn_set_prop)TES3X_QL_SET_PROP)(menu, *(unsigned short *)TES3X_QL_TILE_ID, (int)chosen,
                                     PROP_PTR);
    count = *(int *)((u8 *)chosen + 0xc);
    tes3x_log("quickloot.count", (u32)count);
    tes3x_log("quickloot.result",
              (u32)((fn_transfer)TES3X_QL_TRANSFER)(chosen, count, *(void **)((u8 *)chosen + 0x10),
                                                    0));
    ((fn_close)TES3X_QL_CLOSE)(0, 0, 0, 0, 0);
    /* Activation asked for menu mode, which the engine only enters next frame; the close's
     * request to leave does nothing until then. */
    if (!world[0xd2])
        world[0xd0] = 0;
    tes3x_log("quickloot.mode_after", mode());
}

/* Stands in for UI::showContentsMenu at the activation call sites. */
void __cdecl tes3x_quickloot_show(void *base, void *ref, void *actor, char flag)
{
    ((fn_show)TES3X_QL_SHOW)(base, ref, actor, flag);
    if (pending)
        take();
}

/* Lets the tooltip hook know to list a targeted container's contents. */
int tes3x_quickloot_enabled(void)
{
    return 1;
}

/* `tes3xql take N`: the next container activation takes the Nth item instead of showing it. */
int tes3x_quickloot_command(const char *text)
{
    if (starts_with(text, "tes3xql mode")) {
        tes3x_log("quickloot.mode", mode());
        return 1;
    }
    if (!starts_with(text, "tes3xql take "))
        return 0;
    int index = 0;

    for (text += 13; *text >= '0' && *text <= '9'; text++)
        index = index * 10 + (*text - '0');
    pending = index + 1;
    tes3x_log("quickloot.mode_before", mode());
    tes3x_log("quickloot.pending", (u32)pending);
    return 1;
}
