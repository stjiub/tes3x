/* Server rules, sent after each join. Bit 0: a character levels up as soon as the level is
 * earned, by the same menu a rest ends with, instead of only at the end of a rest. */
#define EVENT_RULES 41u
#define RULE_AUTO_LEVEL 0x1u
#define LEVELUP_TOTAL_GMST 0x446u /* iLevelupTotal */
#define LEVELUP_TOTAL_DEFAULT 10

typedef void(__cdecl *fn_levelup_menu)(void);

static u32 rules_flags, level_ups_opened;

static void rules_event(const struct event *e)
{
    if (e->length < 1)
        return;
    rules_flags = e->data[0];
    tes3x_log_hex3("net.rules", rules_flags, 0, 0);
}

static void level_up_frame(void)
{
    static u32 menu_id;
    const u8 *world = *(const u8 **)TES3X_NET_WORLD;
    u8 *mobile = player_mobile(), *menu;
    int needed;

    if (!(rules_flags & RULE_AUTO_LEVEL) || !mobile || !plausible(world) || world[WORLD_MENU_MODE])
        return;
    needed = (int)((fn_gmst_text)TES3X_NET_GMST_TEXT)(*(void **)TES3X_NET_WORLD, LEVELUP_TOTAL_GMST);
    if (needed <= 0 || needed > 1000)
        needed = LEVELUP_TOTAL_DEFAULT;
    if (*(int *)(mobile + PLAYER_LEVEL_PROGRESS) < needed)
        return;
    if (!menu_id)
        menu_id = ((fn_ui_id)TES3X_NET_UI_ID)("MenuLevelUp");
    menu = ((fn_find_menu)TES3X_NET_FIND_MENU)(menu_id);
    if (menu && menu[MENU_VISIBLE])
        return;
    ((fn_levelup_menu)TES3X_NET_LEVELUP_MENU)();
    tes3x_log_hex3("net.level_up_menu", ++level_ups_opened, *(int *)(mobile + PLAYER_LEVEL_PROGRESS),
                   needed);
}

static void level_progress_set(u32 value)
{
    u8 *mobile = player_mobile();

    if (mobile)
        *(int *)(mobile + PLAYER_LEVEL_PROGRESS) = (int)value;
    tes3x_log_hex3("net.level_progress", mobile != 0, value, 0);
}
