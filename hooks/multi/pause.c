/* The pause menu while joined: no Save or New. Exit stays; its box says the character is saved to
 * the server first, and its Yes does that (tes3x_net_quit). MenuOptions::open sets the buttons'
 * visibility each time it opens, so this is redone whenever the menu comes up. */
#define ENGINE_ID(global) ((u32)(int)*(const short *)(global))
static u32 pause_hid, pause_joined, pause_asked, pause_switch, pause_tails;

/* Load is the server list, as at the main menu: from here it leaves the server first. */
static char __cdecl pause_load_click(void *owner, u32 id, int d0, int d1, void *source)
{
    (void)owner, (void)id, (void)d0, (void)d1, (void)source;
    pause_switch = 1;
    return 1;
}

static void pause_apply(u8 *menu, u32 joined)
{
    fn_set_visible visible = (fn_set_visible)TES3X_NET_SET_VISIBLE;
    u8 *save = ((fn_find_child)TES3X_NET_FIND_CHILD)(menu, ENGINE_ID(TES3X_NET_SAVE_ID));
    u8 *load = ((fn_find_child)TES3X_NET_FIND_CHILD)(menu, ENGINE_ID(TES3X_NET_LOAD_ID));
    u8 *exit = ((fn_find_child)TES3X_NET_FIND_CHILD)(menu, ENGINE_ID(TES3X_NET_EXIT_ID)), *new;

    if (!plausible(exit))
        return;
    if (plausible(save) && joined)
        visible(save, 0);
    visible(exit, 1);
    if (!joined)
        return;
    if (plausible(new = menu_part(menu, "MenuOptions_New_container")))
        visible(new, 0);
    if (plausible(load)) {
        visible(load, 1);
        ((fn_set_prop)TES3X_NET_SET_PROP)(load, UI_CLICK, (int)pause_load_click, UI_PROP_HANDLER);
    }
    pause_hid++;
    /* the pad's own search finds and lights what shows; no NAV links here */
    ((fn_layout)TES3X_NET_PERFORM_LAYOUT)(menu, 1);
}

/* The last thing MenuOptions::open does, before the menu is first drawn. */
typedef void *(__cdecl *fn_help_layer)(u32 id);

static void *__cdecl pause_open_tail(u32 id)
{
    u8 *menu;

    if (ses.state == SESSION_JOINED && player_reference() &&
        plausible(menu = ((fn_find_menu)TES3X_NET_FIND_MENU)(((fn_ui_id)TES3X_NET_UI_ID)("MenuOptions")))) {
        pause_apply(menu, 1);
        pause_tails++;
    }
    return ((fn_help_layer)TES3X_NET_OPEN_TAIL)(id);
}

static void open_tail_install(void)
{
    u32 cr0, flags, *site = (u32 *)TES3X_NET_OPEN_TAIL_SITE;

    if (*site + (u32)site + 4 != TES3X_NET_OPEN_TAIL) {
        tes3x_log_hex3("net.call_site_unexpected", (u32)site, *site, TES3X_NET_OPEN_TAIL);
        return;
    }
    flags = lock();
    __asm__ volatile("movl %%cr0, %0" : "=r"(cr0));
    __asm__ volatile("movl %0, %%cr0" : : "r"(cr0 & ~CR0_WP) : "memory");
    *site = (u32)pause_open_tail - ((u32)site + 4);
    __asm__ volatile("movl %0, %%cr0" : : "r"(cr0) : "memory");
    unlock(flags);
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
    if (pause_asked && (button = *(int *)TES3X_NET_BUTTON) >= 0) {
        *(int *)TES3X_NET_BUTTON = -1;
        pause_asked = 0;
        if (button == 0)
            leave_to_list();
    } else if (pause_switch && joined) {
        pause_switch = 0;
        pause_asked = 1;
        *(int *)TES3X_NET_BUTTON = -1;
        run_script("MessageBox \"Leave this server and choose another? Your character is saved "
                   "there first.\" \"Leave\" \"Stay\"");
    }
}

static void pause_stat(void)
{
    tes3x_log_hex3("net.pause_menu", pause_hid, pause_joined, pause_tails);
}

u32 tes3x_multi_present = 1;

/* From the console's poll with the pad's buttons held: those the server list swallows are used
 * here, and the rest go on to the Dash. */
unsigned int tes3x_multi_pad(unsigned int level)
{
    (void)level;
    return pad_keys;
}
