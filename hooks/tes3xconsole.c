/* Make the in-game console reachable.
 *
 * Console::Toggle is already called from the update loop, gated on an input action that waits on
 * a latch bit nothing sets. This replaces that gate with a two-button check. The binding table
 * and t:\controls.dat are deliberately untouched - that file is the player's own control scheme.
 */

#include "tes3x_thunks.h"
#include "tes3xlog.h"

#ifndef TES3X_INI_GET_STRING
#error "define TES3X_INI_GET_STRING to the VA of the ini string reader"
#endif
#ifndef TES3X_INI_PATH
#error "define TES3X_INI_PATH to the VA of the engine's ini filename string"
#endif
#ifndef TES3X_FIND_MENU
#error "define TES3X_FIND_MENU to the VA of findMenuById"
#endif
#ifndef TES3X_OPEN_VK
#error "define TES3X_OPEN_VK to the VA of the virtual keyboard open"
#endif
#ifndef TES3X_CONSOLE_MENU_ID
#error "define TES3X_CONSOLE_MENU_ID to the VA of the console menu's id global"
#endif
#ifndef TES3X_GET_PROP
#error "define TES3X_GET_PROP to the VA of the widget getProperty"
#endif
#ifndef TES3X_COMPILE_RUN
#error "define TES3X_COMPILE_RUN to the VA of CompileAndRun"
#endif
#ifndef TES3X_VK_MENU_ID
#error "define TES3X_VK_MENU_ID to the VA of the keyboard menu's id global"
#endif
#ifndef TES3X_VK_TEXT_ID
#error "define TES3X_VK_TEXT_ID to the VA of MenuVirtualKeyboard_TextSpace's id global"
#endif
#ifndef TES3X_GAME_PTR
#error "define TES3X_GAME_PTR to the VA of the game object pointer"
#endif
#ifndef TES3X_WIDGET_TEXT
#error "define TES3X_WIDGET_TEXT to the VA of the widget text getter"
#endif

#define MENU_VISIBLE 0x7E   /* the byte Console::Toggle flips */
#define GAME_SCRIPT 0x54    /* the compiler CompileAndRun is a method on */
#define GAME_MENUMGR 0x2C0  /* the menu manager */
#define MENUMGR_CTX 0x20    /* its script scratch object, which CompileAndRun writes into */
#define CMD_MAX 96

/* Per-port block: 22 bytes of XINPUT_STATE, then 30 derived words. A held button reads 0x7FFF. */
#define CTRL_PORT 0x804
#define PORT_STRIDE 0x200
#define INPUT_BASE 0x16
#define INPUT_COUNT 30

/* 7 is Back, 9 is the right thumb click - the only index bound to nothing. */
#define COMBO_DEFAULT_A 7
#define COMBO_DEFAULT_B 9

/* __cdecl: 0x001933E0 ends `mov esp,ebp; pop ebp; ret`, so the caller clears the arguments. */
typedef void *(__cdecl *fn_find_menu)(unsigned int id);
typedef void(__cdecl *fn_open_vk)(void *return_menu, const char *initial);
typedef void *(__attribute__((thiscall)) *fn_get_prop)(void *self, void *out, unsigned int id,
                                                       int type, int a3, int a4);
typedef const char *(__attribute__((thiscall)) *fn_widget_text)(void *widget);
typedef int(__attribute__((thiscall)) *fn_compile_run)(void *self, void *ref, const char *text,
                                                       int a2, int a3, int a4, int a5, int a6);

typedef int(__cdecl *fn_ini_get_string)(const char *section, const char *key, const char *dflt,
                                        char *buf, int size, const char *file);

static int combo_a = COMBO_DEFAULT_A;
static int combo_b = COMBO_DEFAULT_B;
static int combo_ready;
static int seen_first;
static int was_held;
static int console_open;
static int pending_raise;
static int vk_watch;
static char cmd[CMD_MAX];
static int run_delay;

/* "7,9". Anything unparseable leaves the defaults. */
static int parse_combo(const char *s)
{
    int v[2] = {-1, -1};
    int n = 0, digits = 0, acc = 0;

    for (;; s++) {
        if (*s >= '0' && *s <= '9') {
            acc = acc * 10 + (*s - '0');
            digits = 1;
            continue;
        }
        if (digits) {
            if (n < 2)
                v[n] = acc;
            n++;
            acc = 0;
            digits = 0;
        }
        if (*s == 0)
            break;
    }
    if (n != 2 || v[0] == v[1])
        return 0;
    if (v[0] < 0 || v[0] >= INPUT_COUNT || v[1] < 0 || v[1] >= INPUT_COUNT)
        return 0;
    combo_a = v[0];
    combo_b = v[1];
    return 1;
}

/* Deferred: D: is not mounted at the XBE entry point. */
static void load_combo(void)
{
    fn_ini_get_string get = (fn_ini_get_string)TES3X_INI_GET_STRING;
    char buf[32];
    int i;

    combo_ready = 1;
    for (i = 0; i < (int)sizeof(buf); i++)
        buf[i] = 0;

    get("Xbox", "ConsoleCombo", "", buf, (int)sizeof(buf) - 1, (const char *)TES3X_INI_PATH);
    if (buf[0] && parse_combo(buf))
        tes3x_log("console.combo_ini", (u32)((combo_a << 8) | combo_b));
    else
        tes3x_log("console.combo_default", (u32)((combo_a << 8) | combo_b));
}


/* a0 is a scratch script object owned by the menu manager - CompileAndRun zeroes 52 bytes at
 * a0+0xC and writes a0+0x478. The console loads it at 0x001C4DA1, well after the console menu it
 * held in the same register earlier. a3 is the optional reference, and 0 is what the console's
 * simple call site passes. */
static void run_command(const char *text)
{
    unsigned char *game = *(unsigned char **)TES3X_GAME_PTR;
    unsigned char *mgr;
    void *ctx, *script;

    if (!game) {
        tes3x_log("console.no_game", 0);
        return;
    }
    mgr = *(unsigned char **)(game + GAME_MENUMGR);
    script = *(void **)(game + GAME_SCRIPT);
    ctx = mgr ? *(void **)(mgr + MENUMGR_CTX) : 0;
    if (!script || !ctx) {
        tes3x_log("console.no_ctx", (u32)(unsigned int)ctx);
        return;
    }
    ((fn_compile_run)TES3X_COMPILE_RUN)(script, ctx, text, 1, 0, 0, 0, 0);
}

/* The keyboard's text, while it is still on screen. Returns 0 when there is nothing readable. */
static const char *keyboard_text(void *vk)
{
    fn_get_prop get = (fn_get_prop)TES3X_GET_PROP;
    unsigned int out[8];
    void **slot;
    int i;

    for (i = 0; i < 8; i++)
        out[i] = 0;
    slot = (void **)get(vk, out, *(unsigned short *)TES3X_VK_TEXT_ID, 8, 0, 0);
    if (!slot || !*slot)
        return 0;
    /* The property yields the widget, not the string. */
    return ((fn_widget_text)TES3X_WIDGET_TEXT)(*slot);
}

static void watch_keyboard(void)
{
    fn_find_menu find = (fn_find_menu)TES3X_FIND_MENU;
    void *vk = find(*(unsigned short *)TES3X_VK_MENU_ID);
    const char *text;
    int i;

    if (vk && *((unsigned char *)vk + MENU_VISIBLE)) {
        text = keyboard_text(vk);
        if (text) {
            for (i = 0; i < CMD_MAX - 1 && text[i] >= 0x20 && text[i] < 0x7F; i++)
                cmd[i] = text[i];
            cmd[i] = 0;
        }
        return;
    }

    vk_watch = 0;
    if (!cmd[0]) {
        tes3x_log("console.cmd_empty", 0);
        return;
    }
    for (i = 0; cmd[i]; i++)
        ;
    tes3x_log_raw("console> ", 9);
    tes3x_log_raw(cmd, (u32)i);
    tes3x_log_raw("\n", 1);
    /* Not from here: this runs inside the input gate while the keyboard is being torn down. */
    run_delay = 8;
}

/* Replaces the engine's action check at its console call site; ret 8 matches the original. */
unsigned int __attribute__((thiscall)) tes3x_console_hook(void *ctrl, int action, int mode)
{
    unsigned char *base = (unsigned char *)ctrl;
    short *in;
    int port;

    (void)action;
    (void)mode;

    /* Once, before anything else: separates "hook never ran" from "hook ran and died". */
    if (!seen_first) {
        seen_first = 1;
        tes3x_log("console.hook_first", (u32)(unsigned int)ctrl);
    }

    if (!combo_ready)
        load_combo();

    /* Raised a frame late, so the console is already up when the keyboard attaches to it. */
    if (pending_raise) {
        fn_find_menu find = (fn_find_menu)TES3X_FIND_MENU;
        void *menu = find(*(unsigned short *)TES3X_CONSOLE_MENU_ID);
        pending_raise = 0;
        if (menu) {
            ((fn_open_vk)TES3X_OPEN_VK)(menu, 0);
            vk_watch = 1;
            cmd[0] = 0;
        }
        tes3x_log("console.vk_raise", (u32)(unsigned int)menu);
    }

    /* The keyboard delivers only to four hardcoded menus and the console is not one, so take the
     * text ourselves: cache it while the keyboard is up, and run it once the keyboard goes away. */
    if (vk_watch)
        watch_keyboard();

    if (run_delay && !--run_delay && cmd[0]) {
        tes3x_log("console.run_begin", 0);
        run_command(cmd);
        tes3x_log("console.run_done", 0);
        cmd[0] = 0;
    }

    if (!base) {
        tes3x_log("console.no_ctrl", 0);
        return 0;
    }

    port = *(int *)(base + CTRL_PORT);
    if (port < 0 || port > 3)
        return 0;

    in = (short *)(base + port * PORT_STRIDE + INPUT_BASE);
    if (!in[combo_a] || !in[combo_b]) {
        was_held = 0;
        return 0;
    }

    /* Consume both, so their own actions do not also fire. The array is rebuilt each poll. */
    in[combo_a] = 0;
    in[combo_b] = 0;

    /* Rising edge only - the array is level, so without this it toggles every frame held. */
    if (was_held)
        return 0;
    was_held = 1;
    console_open = !console_open;
    if (console_open)
        pending_raise = 1;
    tes3x_log("console.toggle", (u32)console_open);
    return 1;
}
