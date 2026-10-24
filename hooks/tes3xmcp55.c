/* Preserve the entered potion name while the brew callback refreshes the menu. */

volatile unsigned char mcp55_brew_refresh;

typedef void *(__attribute__((thiscall)) *mcp55_get_player_fn)(void *world);
typedef void(__attribute__((thiscall)) *mcp55_clear_icons_fn)(void *data, int category);
typedef void(__attribute__((thiscall)) *mcp55_add_items_fn)(void *data, void *inventory,
                                                            int category);

void tes3x_mcp55_refresh_brew(void)
{
    mcp55_brew_refresh = 1;
    ((void (__cdecl *)(void))TES3X_VALUE(MCP55_REFRESH))();
    mcp55_brew_refresh = 0;
}

__attribute__((naked)) void tes3x_mcp55_refresh_name(void)
{
    __asm__ volatile(
        "cmpb $0, _mcp55_brew_refresh\n\t"
        "jne 1f\n\t"
        "movl $" TES3X_SYM(MCP55_SET_TEXT) ", %eax\n\t"
        "jmp *%eax\n\t"
        "1:\n\t"
        "ret $4\n\t");
}

void tes3x_mcp55_close_inventory(void)
{
    void *world = *(void **)TES3X_VALUE(MCP55_WORLD_PTR);
    void *data = *(void **)((unsigned char *)world + 0x2c4);
    void *player;
    void *inventory;

    ((mcp55_clear_icons_fn)TES3X_VALUE(MCP55_CLEAR_ICONS))(data, 2);
    player = ((mcp55_get_player_fn)TES3X_VALUE(MCP55_GET_PLAYER))(world);
    inventory = (unsigned char *)*(void **)((unsigned char *)player + 0x560) + 0x3c;
    ((mcp55_add_items_fn)TES3X_VALUE(MCP55_ADD_ITEMS))(data, inventory, 2);
    ((void (__cdecl *)(void))TES3X_VALUE(MCP55_UPDATE_TILES))();
}
