/* The pause menu while joined: no Save, Load or Exit, and a hint line over the pad's Black and
 * White in their place: Save to Server, and Leave. MenuOptions::open sets Save's visibility and the
 * menu's size each time it opens, so this is redone whenever the menu comes up. Leave asks in its
 * own box, since Exit's says unsaved progress is lost, then quits as Exit's Yes does: saving to the
 * server first. */
#define ENGINE_ID(global) ((u32)(int)*(const short *)(global))
static u32 pause_hid, pause_joined, pause_save, pause_leave, pause_presses;

/* B returns to the game. */
static void pause_close(u8 *menu)
{
    if (plausible(menu))
        ((fn_trigger_event)TES3X_NET_TRIGGER_EVENT)(menu, EVENT_PAD_B, 0, 0, menu);
}

/* The engine's Save shows only for a pause opened the pad's way; chargen must be over. */
static u32 pause_can_save(void)
{
    const u8 *world = *(const u8 **)TES3X_NET_WORLD, *global;

    return plausible(world) && plausible(global = *(const u8 *const *)(world + CHARGEN_STATE)) &&
           *(const u32 *)(global + 0x34) == 0xBF800000u;
}

static void pause_apply(u8 *menu, u32 joined)
{
    fn_set_visible visible = (fn_set_visible)TES3X_NET_SET_VISIBLE;
    u8 *save = ((fn_find_child)TES3X_NET_FIND_CHILD)(menu, ENGINE_ID(TES3X_NET_SAVE_ID));
    u8 *load = ((fn_find_child)TES3X_NET_FIND_CHILD)(menu, ENGINE_ID(TES3X_NET_LOAD_ID));
    u8 *exit = ((fn_find_child)TES3X_NET_FIND_CHILD)(menu, ENGINE_ID(TES3X_NET_EXIT_ID));

    if (!plausible(exit))
        return;
    if (plausible(save) && joined)
        visible(save, 0);
    if (plausible(load))
        visible(load, !joined);
    visible(exit, !joined);
    if (!joined)
        return;
    pause_hid++;
    /* the pad's own search finds and lights what shows; no NAV links here */
    ((fn_layout)TES3X_NET_PERFORM_LAYOUT)(menu, 1);
}

static void pause_frame(int in_world)
{
    u32 joined = in_world && ses.state == SESSION_JOINED;
    u8 *menu = ((fn_find_menu)TES3X_NET_FIND_MENU)(((fn_ui_id)TES3X_NET_UI_ID)("MenuOptions"));
    static u32 was_up;
    u32 up = plausible(menu) && menu[MENU_VISIBLE];
    int button;

    if (up && !was_up && in_world && (joined || pause_hid))
        pause_apply(menu, joined);
    else if (up && joined != pause_joined && in_world)
        pause_apply(menu, joined);
    was_up = up;
    pause_joined = joined;
    if (up && joined) {
        pad_keys = pause_can_save() ? PAD_BLACK | PAD_WHITE : PAD_WHITE;
        pad_mode = PAD_PAUSE;
        hint_set(menu, pause_can_save() ? " Save to Server   " : 0, " Leave");
    }
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

/* The pad's Black and White, while the menu's hint offers them. Each closes the menu with B, as a
 * button's click would, and its action runs on the frame after. */
static void pause_pad(u32 edge)
{
    u8 *menu = ((fn_find_menu)TES3X_NET_FIND_MENU)(((fn_ui_id)TES3X_NET_UI_ID)("MenuOptions"));

    if (!plausible(menu) || !menu[MENU_VISIBLE])
        return;
    if ((edge & PAD_BLACK) && pause_can_save())
        pause_save = 1;
    else if (edge & PAD_WHITE)
        pause_leave = 1;
    else
        return;
    pause_close(menu);
}

static void pause_stat(void)
{
    tes3x_log_hex3("net.pause_menu", pause_hid, pause_joined, pause_presses);
}

u32 tes3x_multi_present = 1;

/* From the console's poll with the pad's buttons held: the ones the menu's hint offers are used
 * here, and the rest go on to the Dash. */
unsigned int tes3x_multi_pad(unsigned int level)
{
    static u32 prev;
    u32 used = pad_keys, edge = level & ~prev & used;

    prev = level;
    if (pad_mode == PAD_PAUSE)
        pause_pad(edge);
    else if (pad_mode == PAD_MAIN && (edge & PAD_BLACK))
        server_chosen = SERVER_OPEN;
    return used;
}
