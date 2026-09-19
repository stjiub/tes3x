/* Replace the unreachable console action with a two-button check.
 * Leave the binding table and player controls untouched.
 */

#include "tes3x_thunks.h"
#include "tes3xlog.h"
#ifdef TES3X_DIAGNOSTICS
#include "tes3xdiag.h"
#endif

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
#ifndef TES3X_WIDGET_SET_TEXT
#error "define TES3X_WIDGET_SET_TEXT to the VA of the widget text setter"
#endif
#ifndef TES3X_WIDGET_DIRTY
#error "define TES3X_WIDGET_DIRTY to the VA of the widget redraw flag setter"
#endif

#define MENU_VISIBLE 0x7E   /* the byte Console::Toggle flips */
#define GAME_SCRIPT 0x54    /* the compiler CompileAndRun is a method on */
#define GAME_MENUMGR 0x2C0  /* the menu manager */
#define MENUMGR_CTX 0x20    /* its script scratch object, which CompileAndRun writes into */
#define CMD_MAX 96
#define HIST_MAX 8

/* While the console is up: Start raises the keyboard, Black steps further back through history. */
#define KEY_RAISE 6
#define KEY_BACK_HIST 14

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
typedef void(__attribute__((thiscall)) *fn_widget_set_text)(void *widget, const char *text);
typedef void(__attribute__((thiscall)) *fn_widget_dirty)(void *widget);
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
static int vk_watch;
static char cmd[CMD_MAX];
static int run_delay;
static char hist[HIST_MAX][CMD_MAX];
static int hist_count;
static int hist_sel;
static int held_raise;
static int held_hist;
static int seed_pending;
static int hist_armed;   /* Black was pressed, so the next raise is seeded */

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


static void hist_push(const char *text)
{
    int i, j;

    for (i = 0; i < CMD_MAX && hist[0][i] == text[i]; i++)
        if (!text[i])
            return; /* same as the last one */
    for (i = HIST_MAX - 1; i > 0; i--)
        for (j = 0; j < CMD_MAX; j++)
            hist[i][j] = hist[i - 1][j];
    for (i = 0; i < CMD_MAX - 1 && text[i]; i++)
        hist[0][i] = text[i];
    hist[0][i] = 0;
    if (hist_count < HIST_MAX)
        hist_count++;
    hist_sel = 0;
}

/* seed_pending selects a command-history entry. */
static void raise_keyboard(int seed)
{
    fn_find_menu find = (fn_find_menu)TES3X_FIND_MENU;
    void *menu = find(*(unsigned short *)TES3X_CONSOLE_MENU_ID);
    const char *initial = 0;

    if (!menu) {
        tes3x_log("console.vk_no_menu", 0);
        return;
    }
    /* Passing initial text here prevents the keyboard from appearing; seed it later. */
    (void)initial;
    ((fn_open_vk)TES3X_OPEN_VK)(menu, 0);
    vk_watch = 1;
    cmd[0] = 0;
    seed_pending = (seed && hist_count) ? 1 : 0;
    hist_armed = 0;
    tes3x_log("console.vk_raise", (u32)(seed_pending ? hist_sel + 1 : 0));
}

/* a0 is the menu manager's scratch script object; a3 is an optional reference. */
static void run_command(const char *text)
{
    unsigned char *game = *(unsigned char **)TES3X_GAME_PTR;
    unsigned char *mgr;
    void *ctx, *script;

#ifdef TES3X_DIAGNOSTICS
    if (tes3x_diag_command(text))
        return;
#endif
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
static void *keyboard_field(void *vk)
{
    fn_get_prop get = (fn_get_prop)TES3X_GET_PROP;
    unsigned int out[8];
    void **slot;
    int i;

    for (i = 0; i < 8; i++)
        out[i] = 0;
    /* The property yields the widget, not the string. */
    slot = (void **)get(vk, out, *(unsigned short *)TES3X_VK_TEXT_ID, 8, 0, 0);
    return (slot && *slot) ? *slot : 0;
}

static const char *keyboard_text(void *vk)
{
    void *field = keyboard_field(vk);

    return field ? ((fn_widget_text)TES3X_WIDGET_TEXT)(field) : 0;
}

static void watch_keyboard(void)
{
    fn_find_menu find = (fn_find_menu)TES3X_FIND_MENU;
    void *vk = find(*(unsigned short *)TES3X_VK_MENU_ID);
    const char *text;
    int i;

    if (vk && *((unsigned char *)vk + MENU_VISIBLE)) {
        if (seed_pending) {
            void *field = keyboard_field(vk);

            seed_pending = 0;
            if (field) {
                ((fn_widget_set_text)TES3X_WIDGET_SET_TEXT)(field, hist[hist_sel]);
                tes3x_log("console.seeded", (u32)(hist_sel + 1));
            } else {
                tes3x_log("console.seed_no_field", 0);
            }
            return; /* let it take effect before reading back */
        }
        text = keyboard_text(vk);
        if (text) {
            for (i = 0; i < CMD_MAX - 1 && text[i] >= 0x20 && text[i] < 0x7F; i++)
                cmd[i] = text[i];
            cmd[i] = 0;
        }
        return;
    }

    vk_watch = 0;
    seed_pending = 0;
    /* Require release after confirmation so the keyboard does not reopen. */
    held_raise = 1;
    held_hist = 1;
    if (!cmd[0]) {
        tes3x_log("console.cancelled", 0);
        return;
    }
    for (i = 0; cmd[i]; i++)
        ;
    tes3x_log_raw("console> ", 9);
    tes3x_log_raw(cmd, (u32)i);
    tes3x_log_raw("\n", 1);
    hist_push(cmd);
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

    /* Log first entry so startup failures remain diagnosable. */
    if (!seen_first) {
        seen_first = 1;
        tes3x_log("console.hook_first", (u32)(unsigned int)ctrl);
    }

    if (!combo_ready)
        load_combo();


    /* Cache text because the keyboard has no delivery path for the console. */
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

    /* Start opens the keyboard; Black selects command history. */
    if (console_open && !vk_watch && !run_delay) {
        /* Each Black press selects an older command. */
        if (in[KEY_BACK_HIST]) {
            in[KEY_BACK_HIST] = 0;
            if (!held_hist) {
                held_hist = 1;
                if (!hist_count) {
                    tes3x_log("console.no_history", 0);
                } else {
                    if (!hist_armed) {
                        hist_armed = 1;
                        hist_sel = 0;
                    } else if (++hist_sel >= hist_count) {
                        hist_sel = 0;
                    }
                    tes3x_log("console.hist_sel", (u32)(hist_sel + 1));
                }
            }
        } else {
            held_hist = 0;
        }
        if (in[KEY_RAISE]) {
            in[KEY_RAISE] = 0;
            if (!held_raise) {
                held_raise = 1;
                raise_keyboard(hist_armed);
            }
        } else {
            held_raise = 0;
        }
    } else {
        held_raise = 0;
        held_hist = 0;
    }

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
    if (!console_open) {
        vk_watch = 0;
        hist_armed = 0;
    }
    tes3x_log("console.toggle", (u32)console_open);
#ifdef TES3X_DIAGNOSTICS
    tes3x_diag_note(TES3X_DIAG_NOTE_CONSOLE, (u32)console_open);
#endif
    return 1;
}
