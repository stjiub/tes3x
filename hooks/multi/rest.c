/* The shared wait and rest. The clock belongs to the server while joined, so a console cannot skip
 * time alone: its rest menu opening tells the server (the player agrees by opening it), pressing
 * Rest, Wait or Until Healed asks for those hours instead of running them, and once every console
 * in the world has its menu open the server's START has each console press its own button for the
 * server's hours. The engine then runs its usual wait or rest (healing, interrupts, the level-up
 * at its end) while the server's clock is ignored; the next CLOCK after it snaps this console to
 * the shared time, whether the rest ran to its end or not. */
#define EVENT_REST 42u
#define REST_OPEN 0u
#define REST_CLOSE 1u
#define REST_ASK 2u
#define REST_START 3u
#define REST_HOURS_MAX 168
#define PLAYER_REST_HOURS 0x5EC /* MobilePlayer: the hours of the rest or wait under way, else 0 */
#define EVENT_PAD_A 0xFFFF8080
#define REST_BUTTONS 3u /* Rest, Wait, Until Healed */
#define REST_JUMP 5u

typedef u32(__cdecl *fn_rest_button)(void);
typedef int(__cdecl *fn_rest_heal_hours)(void);
typedef void *(__attribute__((thiscall)) *fn_rest_get_prop)(void *menu, void *out, u32 id, int type,
                                                            int a3, int a4);
typedef void(__attribute__((thiscall)) *fn_rest_set_prop)(void *menu, u32 id, int value, int type);

#define rest_functions ((const u32[])TES3X_NET_REST_BUTTONS)
static u8 rest_saved[REST_BUTTONS][REST_JUMP];
static u32 rest_hooked, rest_go, rest_asks, rest_starts, rest_missed;
static u32 rest_menu_sent, rest_welcome;

static u8 *rest_menu(void)
{
    static u32 menu_id;
    u8 *menu;

    if (!menu_id)
        menu_id = ((fn_ui_id)TES3X_NET_UI_ID)("MenuRestWait");
    menu = ((fn_find_menu)TES3X_NET_FIND_MENU)(menu_id);
    return menu && menu[MENU_VISIBLE] ? menu : 0;
}

/* A wait or rest is running: the engine counts its hours down in MobilePlayer. */
static int rest_running(void)
{
    const u8 *mobile = player_mobile();

    return mobile && *(const int *)(mobile + PLAYER_REST_HOURS) > 0;
}

static void rest_write(u32 i, const u8 *bytes)
{
    u32 cr0, flags, k;
    u8 *at = (u8 *)rest_functions[i];

    flags = lock();
    __asm__ volatile("movl %%cr0, %0" : "=r"(cr0));
    __asm__ volatile("movl %0, %%cr0" : : "r"(cr0 & ~CR0_WP) : "memory");
    for (k = 0; k < REST_JUMP; k++)
        at[k] = bytes[k];
    __asm__ volatile("movl %0, %%cr0" : : "r"(cr0) : "memory");
    unlock(flags);
}

static void rest_jump(u32 i, const void *hook, u8 *out)
{
    out[0] = 0xE9;
    *(u32 *)(out + 1) = (u32)hook - (rest_functions[i] + REST_JUMP);
}

/* The engine's own button: the entry is put back for the call, then diverted again. */
static u32 rest_run(u32 i, const void *hook)
{
    u8 jump[REST_JUMP];
    u32 result;

    rest_write(i, rest_saved[i]);
    result = ((fn_rest_button)rest_functions[i])();
    rest_jump(i, hook, jump);
    rest_write(i, jump);
    return result;
}

static int rest_hours_chosen(u32 i)
{
    unsigned int out[8] = {0};
    u8 *menu;
    const int *hours;

    if (i == 2)
        return ((fn_rest_heal_hours)TES3X_NET_REST_HEAL_HOURS)();
    if (!(menu = rest_menu()))
        return 0;
    hours = ((fn_rest_get_prop)TES3X_NET_GET_PROP)(menu, out, *(const u16 *)TES3X_NET_REST_HOURS_ID,
                                                   1, 0, 0);
    return hours && plausible(hours) ? *hours : 0;
}

static u32 rest_pressed(u32 i, const void *hook)
{
    u8 data[3];
    int hours;

    if (ses.state != SESSION_JOINED || rest_go || !player_mobile())
        return rest_run(i, hook);
    hours = rest_hours_chosen(i);
    if (hours < 1)
        hours = 1;
    if (hours > REST_HOURS_MAX)
        hours = REST_HOURS_MAX;
    data[0] = REST_ASK;
    data[1] = (u8)hours;
    data[2] = (u8)(hours >> 8);
    if (event_queue(EVENT_REST, data, 3))
        rest_asks++;
    else
        notice("The server cannot be reached to rest.");
    tes3x_log_hex3("net.rest_ask", i, (u32)hours, rest_asks);
    return 1;
}

static u32 __cdecl rest_hook0(void) { return rest_pressed(0, (const void *)rest_hook0); }
static u32 __cdecl rest_hook1(void) { return rest_pressed(1, (const void *)rest_hook1); }
static u32 __cdecl rest_hook2(void) { return rest_pressed(2, (const void *)rest_hook2); }

static void rest_hook_install(void)
{
    static const void *const hooks[REST_BUTTONS] = {(const void *)rest_hook0,
                                                    (const void *)rest_hook1,
                                                    (const void *)rest_hook2};
    u8 jump[REST_BUTTONS][REST_JUMP];
    u32 i, k;

    rest_hooked = 1;
    for (i = 0; i < REST_BUTTONS; i++) {
        const u8 *at = (const u8 *)rest_functions[i];

        /* A rest and a wait begin as the pad handler's two buttons do; Until Healed by its
         * hours. Anything else is another engine. */
        if (i < 2 ? !(at[0] == 0x33 && at[1] == 0xC0 && at[2] == 0x66 && at[3] == 0xA1)
                  : !(at[0] == 0x83 && at[1] == 0xEC && at[2] == 0x0C && at[3] == 0x56)) {
            tes3x_log_hex3("net.rest_unexpected", i, rest_functions[i], at[0]);
            return;
        }
    }
    for (i = 0; i < REST_BUTTONS; i++) {
        for (k = 0; k < REST_JUMP; k++)
            rest_saved[i][k] = ((const u8 *)rest_functions[i])[k];
        rest_jump(i, hooks[i], jump[i]);
    }
    for (i = 0; i < REST_BUTTONS; i++)
        rest_write(i, jump[i]);
    rest_hooked = 2;
}

/* The server's go-ahead: this console's menu sets the hours and presses its own Rest or Wait,
 * whichever it offers, which the buttons above let through. */
static void rest_event(const struct event *e)
{
    u8 *menu;
    u32 hours;

    if (e->length < 3 || e->data[0] != REST_START)
        return;
    hours = e->data[1] | e->data[2] << 8;
    if (ses.state != SESSION_JOINED || !(menu = rest_menu()) || hours < 1 ||
        hours > REST_HOURS_MAX) {
        rest_missed++;
        tes3x_log_hex3("net.rest_missed", hours, menu != 0, ses.state);
        return;
    }
    ((fn_rest_set_prop)TES3X_NET_SET_PROP)(menu, *(const u16 *)TES3X_NET_REST_HOURS_ID, (int)hours,
                                           1);
    rest_go = 1;
    ((fn_trigger_event)TES3X_NET_TRIGGER_EVENT)(menu, EVENT_PAD_A, 0, 0, menu);
    rest_go = 0;
    rest_starts++;
    tes3x_log_hex3("net.rest_start", hours, rest_starts, rest_running());
}

/* Game thread, each frame while joined: the menu opening or closing is told to the server, which
 * counts an open menu as agreeing to a rest. */
static void rest_frame(void)
{
    u8 data[1];
    u32 open;

    if (!rest_hooked)
        rest_hook_install();
    if (rest_welcome != ses.welcomes) {
        rest_welcome = ses.welcomes;
        rest_menu_sent = 0;
    }
    open = rest_menu() != 0;
    if (open == rest_menu_sent)
        return;
    data[0] = open ? REST_OPEN : REST_CLOSE;
    if (event_queue(EVENT_REST, data, 1)) {
        rest_menu_sent = open;
        tes3x_log_hex3("net.rest_menu", open, 0, 0);
    }
}

static void rest_stat(void)
{
    tes3x_log_hex3("net.rest", rest_hooked, rest_asks, rest_starts);
    tes3x_log_hex3("net.rest_state", rest_running(), rest_missed, 0);
}
