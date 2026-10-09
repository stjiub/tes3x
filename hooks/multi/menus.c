/* Two 6-byte jnes skip the world while a menu is open: one in mainLoopBeforeInput (simulation
 * clock, controllers), one in Game::Update (ProcessMobs, idles, cell loading, weather). NOPs let
 * the world run under menus. The simulation clock is the fld operand 0x1A bytes past the first. */
#define WORLD_MENU_MODE 0xD2
#define GATE_CLOCK 0x1A
static u8 *const gates[2] = {(u8 *)TES3X_NET_MENU_GATE, (u8 *)TES3X_NET_MOB_GATE};
static u8 gate_original[2][6];
#define GATES_UNEXPECTED 2
static u32 gate_saved, gates_open;
/* `menusim`: 0 follows the session, else 1 + the forced state. */
static u32 menu_forced;

static void menu_sim(u32 on)
{
    u32 cr0, flags, i, g;

    if (on == gates_open || gate_saved == GATES_UNEXPECTED)
        return;
    if (!gate_saved) {
        for (g = 0; g < 2; g++)
            if (gates[g][0] != 0x0F || gates[g][1] != 0x85) {
                tes3x_log_hex3("net.menu_gate_unexpected", g, gates[g][0], gates[g][1]);
                gate_saved = GATES_UNEXPECTED;
                return;
            }
        for (g = 0; g < 2; g++)
            copy(gate_original[g], gates[g], 6);
        gate_saved = 1;
    }
    flags = lock();
    __asm__ volatile("movl %%cr0, %0" : "=r"(cr0));
    __asm__ volatile("movl %0, %%cr0" : : "r"(cr0 & ~CR0_WP) : "memory");
    for (g = 0; g < 2; g++)
        for (i = 0; i < 6; i++)
            gates[g][i] = on ? 0x90 : gate_original[g][i];
    __asm__ volatile("movl %0, %%cr0" : : "r"(cr0) : "memory");
    unlock(flags);
    gates_open = on;
    tes3x_log("net.menusim", on);
}

typedef void *(__cdecl *fn_find_menu)(u32 id);
typedef u32(__cdecl *fn_ui_id)(const char *name);
typedef void(__attribute__((thiscall)) *fn_trigger_event)(void *element, u32 event, int d0,
                                                          int d1, void *source);
#define MENU_VISIBLE 0x7E
#define EVENT_PAD_B 0xFFFF8081

static void run_script(const char *text);
static void notice(const char *text);
static void notice_hold(const char *text);
static void notice_release(void);
static u32 notice_holding;
static const u8 *player_reference(void);
static u32 rest_blocked;

/* Resting and waiting advance the clock, which is shared while joined: the rest menu is closed
 * as soon as it opens, from a bed, the pad or ShowRestMenu alike. */
static void rest_block(void)
{
    static u32 menu_id;
    u8 *menu;

    if (!menu_id)
        menu_id = ((fn_ui_id)TES3X_NET_UI_ID)("MenuRestWait");
    menu = ((fn_find_menu)TES3X_NET_FIND_MENU)(menu_id);
    if (!menu || !menu[MENU_VISIBLE])
        return;
    /* The Xbox menu has no cancel button; B closes it. */
    ((fn_trigger_event)TES3X_NET_TRIGGER_EVENT)(menu, EVENT_PAD_B, 0, 0, menu);
    notice("You cannot rest or wait in a multiplayer session.");
    tes3x_log("net.rest_blocked", ++rest_blocked);
}

/* The NPC a dialogue is with stays put while the world runs: it leaves the simulation, as actors
 * outside the loaded cells do, until the dialogue closes. Combat or a drop in its health releases
 * it and closes the dialogue, so holding an NPC in conversation cannot set it up to be hit. */
#define MOBILE_FLAGS 0x10
#define MOBILE_REFERENCE 0x14
#define MOBILE_SIMULATED 0x4u /* ActiveInSimulation, MWSE's activeAI */
#define MOBILE_IN_COMBAT 0x10000u
#define MOBILE_HEALTH 0x2BC /* the current value of the health statistic */
#define MOBILE_MAGICKA 0x2C8
#define MOBILE_FATIGUE 0x2E0
#define MOBILE_FIGHT 0x350 /* int, as SetFight sets it */

typedef u8 *(__cdecl *fn_service_actor)(void);

static u8 *held;
static u32 held_simulated, holds, hold_breaks;
static float held_health;

static void hold_release(void)
{
    if (held && held_simulated)
        *(u32 *)(held + MOBILE_FLAGS) |= MOBILE_SIMULATED;
    held = 0;
}

/* Services open on top of the dialogue; B closes them one at a time, then the dialogue. */
static void dialogue_close(void)
{
    static const char *const names[] = {
        "MenuBarter",     "MenuService",       "MenuServiceSpells", "MenuServiceTraining",
        "MenuServiceRepair", "MenuServiceTravel", "MenuSpellmaking", "MenuEnchantment",
        "MenuDialog"};
    static u32 ids[sizeof(names) / sizeof(names[0])];
    u32 i;
    u8 *menu;

    for (i = 0; i < sizeof(names) / sizeof(names[0]); i++) {
        if (!ids[i])
            ids[i] = ((fn_ui_id)TES3X_NET_UI_ID)(names[i]);
        menu = ((fn_find_menu)TES3X_NET_FIND_MENU)(ids[i]);
        if (menu && menu[MENU_VISIBLE]) {
            ((fn_trigger_event)TES3X_NET_TRIGGER_EVENT)(menu, EVENT_PAD_B, 0, 0, menu);
            return;
        }
    }
}

static int hold_remote(u8 *actor);

static void hold_frame(void)
{
    u8 *actor = gates_open ? ((fn_service_actor)TES3X_NET_SERVICE_ACTOR)() : 0;
    u32 *flags;
    float health;

    if (actor != held)
        hold_release();
    if (hold_remote(plausible(actor) ? actor : 0) || !plausible(actor))
        return;
    flags = (u32 *)(actor + MOBILE_FLAGS);
    health = *(const float *)(actor + MOBILE_HEALTH);
    if (!held && !(*flags & MOBILE_IN_COMBAT)) {
        held = actor;
        held_simulated = *flags & MOBILE_SIMULATED;
        held_health = health;
        tes3x_log_hex3("net.hold", ++holds, (u32)(int)health, held_simulated);
    }
    if ((*flags & MOBILE_IN_COMBAT) || (held && health < held_health)) {
        if (held)
            tes3x_log_hex3("net.hold_broken", ++hold_breaks, *flags & MOBILE_IN_COMBAT,
                           (u32)(int)health);
        hold_release();
        dialogue_close();
        return;
    }
    *flags &= ~MOBILE_SIMULATED;
}

/* The connection's state on screen: lost stays up until the session is back. */
static void connection_notice_frame(void)
{
    static u32 joined_once, was_joined, slow;
    u32 joined = ses.state == SESSION_JOINED;

    if (!player_reference())
        return;
    if (was_joined && !joined) {
        tes3x_log_hex3("net.notice", 1, ses.state, 0);
        notice_hold("Connection lost. Reconnecting to the server...");
    } else if (!was_joined && joined && joined_once) {
        tes3x_log_hex3("net.notice", 2, ses.state, 0);
        notice_release();
        notice("Connection restored.");
    } else if (!joined && notice_holding && ses.state == SESSION_REFUSED) {
        notice_release();
    } else if (!joined && notice_holding && ses.state == SESSION_UNTRUSTED) {
        notice_release();
        notice("The server's key has changed. Not reconnecting.");
    }
    if (joined)
        joined_once = 1;
    was_joined = joined;
    if (joined && !slow && ses.rtt_last >= 500000u) {
        slow = 1;
        notice("Connection is slow.");
    } else if (slow && (!joined || ses.rtt_last < 300000u)) {
        slow = 0;
        if (joined)
            notice("Connection recovered.");
    }
}

/* With the world running under a menu the player's controls would read the pad the menu is
 * using, so a press that equips an item also swings the weapon. While a menu is open the player's
 * controller runs with DisablePlayerControls' byte set, for that call only: the menu button's
 * toggle refuses to act while it is set. Nor is the activation target looked for, whose name
 * would stay up over the menu. */
#define PLAYER_CONTROLS_OFF 0x5B0 /* MobilePlayer, the byte DisablePlayerControls sets */
#define CONTROLLER_MOBILE 0x38

typedef void(__attribute__((thiscall)) *fn_game_call)(void *game);
typedef void(__attribute__((thiscall)) *fn_controller_update)(void *controller, u32 time);

static int redirect_calls(const u32 *sites, u32 n, u32 original, const void *hook);
static const u32 target_sites[] = TES3X_NET_ACTIVATION_TARGET_SITES;
static u32 target_hooked, control_hooked, controls_held;

static int menu_open(void)
{
    const u8 *world = *(const u8 **)TES3X_NET_WORLD;

    return gates_open && plausible(world) && world[WORLD_MENU_MODE];
}

static void __attribute__((thiscall)) activation_target_hook(void *game)
{
    if (!menu_open())
        ((fn_game_call)TES3X_NET_ACTIVATION_TARGET)(game);
}

/* The player's own activation (the pad's A) does nothing on a ghost. Its noPickUp script cannot
 * swallow it: a ghost moved in from its never-loaded cell has no script variables attachment,
 * without which activation takes the default path and opens the dialogue. */
typedef void(__attribute__((thiscall)) *fn_activate)(void *target, void *activator, int a2);
static int is_ghost(const u8 *ref);
static const u32 activate_sites[] = TES3X_NET_PLAYER_ACTIVATE_SITES;
static u32 ghost_activations;

static void __attribute__((thiscall)) player_activate_hook(u8 *target, void *activator, int a2)
{
    if (plausible(target) && is_ghost(target)) {
        ghost_activations++;
        return;
    }
    ((fn_activate)TES3X_NET_REF_ACTIVATE)(target, activator, a2);
}

static void __attribute__((thiscall)) player_control_hook(u8 *controller, u32 time)
{
    u8 *mobile = *(u8 **)(controller + CONTROLLER_MOBILE), saved;

    if (!menu_open() || !plausible(mobile)) {
        ((fn_controller_update)TES3X_NET_PLAYER_CONTROL)(controller, time);
        return;
    }
    saved = mobile[PLAYER_CONTROLS_OFF];
    mobile[PLAYER_CONTROLS_OFF] = 1;
    ((fn_controller_update)TES3X_NET_PLAYER_CONTROL)(controller, time);
    mobile[PLAYER_CONTROLS_OFF] = saved;
    controls_held++;
}

static void control_hook_install(void)
{
    u32 *slot = (u32 *)TES3X_NET_PLAYER_CONTROL_SLOT, cr0, flags;

    control_hooked = 1;
    if (*slot != TES3X_NET_PLAYER_CONTROL) {
        tes3x_log_hex3("net.control_slot_unexpected", (u32)slot, *slot, 0);
        return;
    }
    flags = lock();
    __asm__ volatile("movl %%cr0, %0" : "=r"(cr0));
    __asm__ volatile("movl %0, %%cr0" : : "r"(cr0 & ~CR0_WP) : "memory");
    *slot = (u32)player_control_hook;
    __asm__ volatile("movl %0, %%cr0" : : "r"(cr0) : "memory");
    unlock(flags);
    control_hooked = 2;
}

/* While joined, the world runs under menus as it does for the other players, and nobody rests.
 * Only in the world: the main menu at boot has no player to simulate. */
static void menu_frame(int in_world)
{
    u32 joined = ses.state == SESSION_JOINED && in_world;

    if (!target_hooked) {
        target_hooked = 1;
        if (redirect_calls(target_sites, sizeof(target_sites) / sizeof(target_sites[0]),
                           TES3X_NET_ACTIVATION_TARGET, (const void *)activation_target_hook))
            target_hooked = 2;
    }
    if (!control_hooked) {
        control_hook_install();
        if (redirect_calls(activate_sites, sizeof(activate_sites) / sizeof(activate_sites[0]),
                           TES3X_NET_REF_ACTIVATE, (const void *)player_activate_hook))
            control_hooked |= 4;
    }
    menu_sim(menu_forced ? menu_forced - 1 : joined);
    if (joined)
        rest_block();
    hold_frame();
}

static void menu_stat(void)
{
    const u8 *world = *(const u8 **)TES3X_NET_WORLD, *gate = (const u8 *)TES3X_NET_MENU_GATE;
    const float *clock = *(const float *const *)(gate + GATE_CLOCK);

    if (plausible(world) && gate[GATE_CLOCK - 2] == 0xD9 && gate[GATE_CLOCK - 1] == 0x05 &&
        plausible(clock))
        tes3x_log_hex3("net.menu_mode", world[WORLD_MENU_MODE], (u32)(int)(*clock * 1000.0f),
                       gate[0] == 0x90);
    tes3x_log_hex3("net.menu_sim", gates_open, menu_forced, rest_blocked);
    tes3x_log_hex3("net.menu_controls", controls_held, control_hooked, target_hooked);
    tes3x_log_hex3("net.ghost_activations", ghost_activations, 0, 0);
    tes3x_log_hex3("net.ghost_crimes", ghost_crimes_blocked, ghost_crime_hooked, 0);
    tes3x_log_hex3("net.holds", holds, hold_breaks, held != 0);
}
