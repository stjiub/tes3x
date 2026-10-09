/* The pause menu while joined: Save to Server in place of Save, no Load, Leave in place of Exit.
 * MenuOptions::open sets Save's visibility and the menu's size each time it opens, so this is
 * redone whenever the menu comes up. Leave asks in its own box, since Exit's says unsaved progress
 * is lost, then quits as Exit's Yes does: saving to the server first. */
#define ENGINE_ID(global) ((u32)(int)*(const short *)(global))
static u32 pause_built, pause_joined, pause_shown, pause_save, pause_leave, pause_presses;

/* B returns to the game. */
static char __cdecl save_server_click(void *owner, u32 id, int d0, int d1, void *source)
{
    (void)id, (void)d0, (void)d1, (void)source;
    pause_save = 1;
    if (plausible(owner))
        ((fn_trigger_event)TES3X_NET_TRIGGER_EVENT)(owner, EVENT_PAD_B, 0, 0, owner);
    return 1;
}

static char __cdecl leave_click(void *owner, u32 id, int d0, int d1, void *source)
{
    (void)id, (void)d0, (void)d1, (void)source;
    pause_leave = 1;
    if (plausible(owner))
        ((fn_trigger_event)TES3X_NET_TRIGGER_EVENT)(owner, EVENT_PAD_B, 0, 0, owner);
    return 1;
}

/* Moves el, the column's last child, to just after anchor. */
static void place_after(u8 *column, u8 *el, u8 *anchor)
{
    u8 **begin = *(u8 ***)(column + UI_CHILDREN), **end = *(u8 ***)(column + UI_CHILDREN + 4), **at;

    if (!plausible(begin) || end <= begin || end[-1] != el)
        return;
    for (at = begin; at < end - 1 && *at != anchor; at++)
        ;
    if (at == end - 1)
        return;
    for (end--; end > at + 1; end--)
        end[0] = end[-1];
    at[1] = el;
}

static void pause_apply(u8 *menu, u32 joined)
{
    fn_set_visible visible = (fn_set_visible)TES3X_NET_SET_VISIBLE;
    u8 *save = ((fn_find_child)TES3X_NET_FIND_CHILD)(menu, ENGINE_ID(TES3X_NET_SAVE_ID));
    u8 *load = ((fn_find_child)TES3X_NET_FIND_CHILD)(menu, ENGINE_ID(TES3X_NET_LOAD_ID));
    u8 *exit = ((fn_find_child)TES3X_NET_FIND_CHILD)(menu, ENGINE_ID(TES3X_NET_EXIT_ID));
    u8 *ours_save = menu_part(menu, "TES3X_SaveServer"), *ours_leave = menu_part(menu, "TES3X_Leave");
    const u8 *world = *(const u8 **)TES3X_NET_WORLD, *global;
    u8 *column;
    u32 saving;

    if (!plausible(exit) || !plausible(column = *(u8 **)(exit + UI_PARENT)))
        return;
    /* the engine's Save shows only for a pause opened the pad's way; chargen must be over */
    saving = plausible(world) && plausible(global = *(const u8 *const *)(world + CHARGEN_STATE)) &&
             *(const u32 *)(global + 0x34) == 0xBF800000u;
    if (joined && !plausible(ours_leave)) {
        int width = *(const int *)(exit + UI_WIDTH);
        ours_save = menu_button(column, ((fn_ui_id)TES3X_NET_UI_ID)("TES3X_SaveServer"),
                                "menu_saveserver", 2 * width, save_server_click);
        if (plausible(ours_save))
            place_after(column, ours_save, plausible(save) ? save : exit);
        ours_leave = menu_button(column, ((fn_ui_id)TES3X_NET_UI_ID)("TES3X_Leave"), "menu_leave",
                                 width, leave_click);
        if (plausible(ours_leave))
            place_after(column, ours_leave, exit);
        pause_built++;
    }
    if (plausible(save) && joined)
        visible(save, 0);
    if (plausible(load))
        visible(load, !joined);
    visible(exit, !joined);
    if (plausible(ours_save))
        visible(ours_save, joined && saving);
    if (plausible(ours_leave))
        visible(ours_leave, joined);
    if (!joined)
        return;
    /* the pad's own search finds and lights what shows; no NAV links here */
    ((fn_set_size)TES3X_NET_SET_WIDTH)(menu, 2 * *(const int *)(exit + UI_WIDTH));
    ((fn_layout)TES3X_NET_PERFORM_LAYOUT)(menu, 1);
    pause_shown++;
}

static void pause_frame(int in_world)
{
    u32 joined = in_world && ses.state == SESSION_JOINED;
    u8 *menu = ((fn_find_menu)TES3X_NET_FIND_MENU)(((fn_ui_id)TES3X_NET_UI_ID)("MenuOptions"));
    static u32 was_up;
    u32 up = plausible(menu) && menu[MENU_VISIBLE];
    int button;

    if (up && !was_up && in_world && (joined || pause_built))
        pause_apply(menu, joined);
    else if (up && joined != pause_joined && in_world)
        pause_apply(menu, joined);
    was_up = up;
    pause_joined = joined;
    if (pause_leave == 2 && (button = *(int *)TES3X_NET_BUTTON) >= 0) {
        *(int *)TES3X_NET_BUTTON = -1;
        pause_leave = 0;
        if (button == 0)
            tes3x_net_quit();
    } else if (pause_leave == 1) {
        pause_leave = 2;
        pause_presses++;
        *(int *)TES3X_NET_BUTTON = -1;
        run_script("MessageBox \"Leave the server? Your character is saved there first.\" "
                   "\"Leave\" \"Stay\"");
    }
    if (!pause_save)
        return;
    pause_save = 0;
    pause_presses++;
    save_to_server();
}

static void pause_stat(void)
{
    tes3x_log_hex3("net.pause_menu", pause_built, pause_shown, pause_presses);
}
