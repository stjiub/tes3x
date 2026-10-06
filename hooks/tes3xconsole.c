/* Replace the unreachable console action with a two-button check.
 * Leave the binding table and player controls untouched.
 */

#include "tes3x_thunks.h"
#include "tes3xnt.h"
#include "tes3xlog.h"
#include "tes3xini.h"
#include "tes3xlaunch.h"
#ifdef TES3X_DIAGNOSTICS
#include "tes3xdiag.h"
#endif
#ifdef TES3X_PROFILE
#include "tes3xprof.h"
#endif
#ifdef TES3X_HEAP
#include "tes3xheap.h"
#endif
#ifdef TES3X_MEM
#include "tes3xmem.h"
#endif
#ifdef TES3X_PAGER
#include "tes3xpager.h"
#endif
#ifdef TES3X_REGION
#include "tes3xregion.h"
#endif
#ifdef TES3X_NET
int tes3x_net_command(const char *text);
#endif
#ifdef TES3X_MCP3_TEST
int tes3x_mcp3_test_command(const char *text);
#endif
#ifdef TES3X_DIALMERGE_TEST
int tes3x_dialmerge_test_command(const char *text);
#endif
#ifdef TES3X_SAVES
int tes3x_autosave_command(void *game, const char *text);
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
#ifndef TES3X_PERFORM_LAYOUT
#error "define TES3X_PERFORM_LAYOUT to the VA of the widget layout pass"
#endif
#ifndef TES3X_SET_PROP
#error "define TES3X_SET_PROP to the VA of the widget setProperty"
#endif
#ifndef TES3X_SET_AUTO_WIDTH
#error "define TES3X_SET_AUTO_WIDTH to the VA of the widget autoWidth setter"
#endif
#ifndef TES3X_SET_AUTO_HEIGHT
#error "define TES3X_SET_AUTO_HEIGHT to the VA of the widget autoHeight setter"
#endif
#ifndef TES3X_FIND_CHILD
#error "define TES3X_FIND_CHILD to the VA of findChild"
#endif
#ifndef TES3X_UI_ID
#error "define TES3X_UI_ID to the VA of the UI name-to-id lookup"
#endif
#ifndef TES3X_VK_CASE_ID
#error "define TES3X_VK_CASE_ID to the VA of the keyboard's case-state property id global"
#endif
#ifndef TES3X_VK_DONE_ID
#error "define TES3X_VK_DONE_ID to the VA of the keyboard's Done button id global"
#endif
#ifndef TES3X_TRIGGER_EVENT
#error "define TES3X_TRIGGER_EVENT to the VA of the widget event dispatch"
#endif
#ifndef TES3X_CREATE_WIDGET
#error "define TES3X_CREATE_WIDGET to the VA of createWidget"
#endif
#ifndef TES3X_VK_BUTTON
#error "define TES3X_VK_BUTTON to the VA of the keyboard's button factory"
#endif
#ifndef TES3X_NAV_RIGHT_ID
#error "define TES3X_NAV_RIGHT_ID to the VA of the focus-right property id global"
#endif
#ifndef TES3X_NAV_LEFT_ID
#error "define TES3X_NAV_LEFT_ID to the VA of the focus-left property id global"
#endif
#ifndef TES3X_NAV_UP_ID
#error "define TES3X_NAV_UP_ID to the VA of the focus-up property id global"
#endif
#ifndef TES3X_NAV_DOWN_ID
#error "define TES3X_NAV_DOWN_ID to the VA of the focus-down property id global"
#endif
#ifndef TES3X_VK_ROW_NUM_ID
#error "define TES3X_VK_ROW_NUM_ID to the VA of MenuVirtualKeyboard_RowNum's id global"
#endif
#ifndef TES3X_VK_COL_NUM_ID
#error "define TES3X_VK_COL_NUM_ID to the VA of MenuVirtualKeyboard_ColNum's id global"
#endif
#ifndef TES3X_VK_CAPS_ID
#error "define TES3X_VK_CAPS_ID to the VA of the keyboard's Caps button id global"
#endif
#ifndef TES3X_VK_SPACE_ID
#error "define TES3X_VK_SPACE_ID to the VA of the keyboard's space bar id global"
#endif
#ifndef TES3X_VK_BACKSPACE_ID
#error "define TES3X_VK_BACKSPACE_ID to the VA of the keyboard's Backspace button id global"
#endif
#ifndef TES3X_VK_CAPS
#error "define TES3X_VK_CAPS to the VA of the keyboard's Caps click handler"
#endif
#ifndef TES3X_CREATE_IMAGE
#error "define TES3X_CREATE_IMAGE to the VA of createImage"
#endif
#ifndef TES3X_BUTTON_HINT
#error "define TES3X_BUTTON_HINT to the VA of the button-hint strip setter"
#endif
#if !defined(TES3X_OPEN_MENU) || !defined(TES3X_OPEN_JOURNAL) || !defined(TES3X_JOURNAL_OPENED)
#error "define TES3X_OPEN_MENU, TES3X_OPEN_JOURNAL and TES3X_JOURNAL_OPENED to the pad's menu openers"
#endif
#if !defined(TES3X_RECORDS_PTR) || !defined(TES3X_RESOLVE_OBJECT) || !defined(TES3X_CLOSEST_REF) || \
    !defined(TES3X_REF_ACTIVATE) || !defined(TES3X_PLAYER_MOBILE)
#error "define the object lookup, Reference::activate and the player mobile getter"
#endif
#ifndef TES3X_CONSOLE_PRINT
#error "define TES3X_CONSOLE_PRINT to the VA of the console's printf"
#endif
#ifndef TES3X_VSPRINTF
#error "define TES3X_VSPRINTF to the VA of the engine's vsprintf"
#endif

#define MENU_VISIBLE 0x7E   /* the byte Console::Toggle flips */
#define GAME_SCRIPT 0x54    /* the compiler CompileAndRun is a method on */
#define GAME_MENUMGR 0x2C0  /* the menu manager */
#define MENUMGR_CTX 0x20    /* its script scratch object, which CompileAndRun writes into */
#define CMD_MAX 96
#define HIST_MAX 8

/* Console up: A raises the keyboard. Keyboard up: Y confirms, B cancels, X is backspace, Black is
 * space, the left stick click is caps, White swaps the letter keys for symbols, and the triggers
 * step through earlier commands. */
#define KEY_CAPS 8
#define KEY_A 10
#define KEY_B 11
#define KEY_SPACE 14
#define KEY_SYMBOLS 15
#define KEY_HIST_OLDER 16
#define KEY_HIST_NEWER 17

/* Element fields and property ids, as the engine's own menu builders use them. */
#define EL_PARENT 0x34
#define EL_CHILDREN 0x28    /* vector: begin, then end at +4 */
#define EL_X 0xE4            /* relative to the parent */
#define EL_WIDTH 0xF4
#define EL_HEIGHT 0xF8
#define EL_ALIGN_X 0x128
#define EL_ALIGN_Y 0x12C
#define PROP_WIDTH 0xFFFF802B
#define PROP_HEIGHT 0xFFFF802C
#define PROP_MIN_WIDTH 0xFFFF802D
#define PROP_MIN_HEIGHT 0xFFFF802E
#define PROP_MAX_WIDTH 0xFFFF802F
#define PROP_MAX_HEIGHT 0xFFFF8030
#define PROP_INT 1
#define PROP_PTR 8
#define PROP_HANDLER 0x20
#define CASE_LOWER 0x80BE
#define EVENT_CLICK 0xFFFF8035
#define EVENT_PAD_A 0xFFFF8080 /* then B, X, Y */
#define EVENT_PAD_Y 0xFFFF8083
#define ID_GENERIC 0xFFFF80B4  /* ids in the engine's anonymous range are not registered */
#define HINT_MODE 0xFFFF80D1
#define VK_TEXT_LIMIT 0x1F     /* the keyboard's own cap, which names and saves rely on */

/* Float bit patterns, so the payload needs no float runtime. */
#define F_ZERO 0x00000000
#define F_HALF 0x3F000000
#define F_ONE 0x3F800000

#define KEY_ROWS 4
#define KEY_COLS 10
#define KEY_COUNT 36        /* 0-9, then A-Z; row 4 ends in the space bar */
#define KEYBOARD_PAD 64     /* the keyboard's frame, when a row cannot be measured */
#define KEY_GAP 2           /* the key frame draws slightly outside its width */
#define ITEM_SPACING 8      /* the layout's space between buttons in a row, as drawn */
#define VK_APPEAR_FRAMES 30 /* a new keyboard is not visible on the frame after it is raised */
#define VK_RETRY_FRAME 8    /* the first open only builds the menu; a second shows it */
#define BOTTOM_BUTTONS 6
/* Bottom row shares, in percent, by label length: Older, Newer, !?#, Backspace, Shift, Done. */
static const int bottom_share[BOTTOM_BUTTONS] = {16, 17, 13, 22, 14, 18};
static const int bottom_share_text[BOTTOM_BUTTONS - 2] = {20, 30, 22, 28};

/* Per-port block: 22 bytes of XINPUT_STATE, then 30 derived words. A held button reads 0x7FFF. */
#define CTRL_PORT 0x804
#define PORT_STRIDE 0x200
#define INPUT_BASE 0x16
#define INPUT_COUNT 30

/* 7 is Back, 9 is the right thumb click - the only index bound to nothing. */
#define COMBO_DEFAULT_A 7
#define COMBO_DEFAULT_B 9

/* E:\tes3xexec.txt, else D:\tes3xexec.txt, runs without input. `@menu` lines run while the main
 * menu is up, the rest once it has been gone EXEC_SETTLE frames; the main menu and a New Game are
 * separate processes unless the relaunch is disabled. */
#define EXEC_MAX (1024 * 1024)
#define EXEC_PAD 16
#define MAILBOX_MAGIC 0x424D3354 /* 'T3MB', so the host can check the address it found */
#define EXEC_SETTLE 150
#define EXEC_CLICK_FRAMES 600

#define NtCreateFile KFN(THUNK_NtCreateFile, fn_NtCreateFile)
#define NtReadFile KFN(THUNK_NtReadFile, fn_NtReadFile)
#define NtClose KFN(THUNK_NtClose, fn_NtClose)
#define HalInitiateShutdown KFN(THUNK_HalInitiateShutdown, fn_HalInitiateShutdown)
#define HalReturnToFirmware KFN(THUNK_HalReturnToFirmware, fn_HalReturnToFirmware)
#define NtQueryInformationFile KFN(THUNK_NtQueryInformationFile, fn_NtQueryInformationFile)
#define MmAllocateSystemMemory KFN(THUNK_MmAllocateSystemMemory, fn_MmAllocateSystemMemory)
#define MmFreeSystemMemory KFN(THUNK_MmFreeSystemMemory, fn_MmFreeSystemMemory)
#define MmQueryStatistics KFN(THUNK_MmQueryStatistics, fn_MmQueryStatistics)

typedef u32(__stdcall *fn_MmQueryStatistics)(void *);

/* MM_STATISTICS, XDK layout: only the page counts are read. */
typedef struct {
    u32 Length;
    u32 TotalPhysicalPages;
    u32 AvailablePages;
    u32 VirtualMemoryBytesCommitted;
    u32 VirtualMemoryBytesReserved;
    u32 CachePagesCommitted;
    u32 PoolPagesCommitted;
    u32 StackPagesCommitted;
    u32 ImagePagesCommitted;
} MM_STATS;

typedef void(__stdcall *fn_HalInitiateShutdown)(void);
typedef void(__stdcall *fn_HalReturnToFirmware)(u32 routine);
#define HAL_REBOOT_ROUTINE 1

/* LAUNCH_DATA_PAGE: a header, then the title's launch data at 0x400. XGetLaunchInfo copies the
 * data out and frees the page. */
#define MmAllocateContiguousMemory \
    KFN(THUNK_MmAllocateContiguousMemory, fn_MmAllocateContiguousMemory)
typedef void *(__stdcall *fn_MmAllocateContiguousMemory)(u32 bytes);
#define MmPersistContiguousMemory \
    KFN(THUNK_MmPersistContiguousMemory, fn_MmPersistContiguousMemory)
typedef void(__stdcall *fn_MmPersistContiguousMemory)(void *base, u32 bytes, int persist);
#define LAUNCH_PAGE 0x1000
#define LAUNCH_DATA 0x400
#define LDT_TITLE 0
#define XBE_CERT_PTR 0x00010118

/* The engine's relaunch data: magic, pad port, a value it adds to a setting, mode, then the save
 * path to load (0x00092A93, 0x000959EC, 0x00095DCB read it). */
#define BXWM_MAGIC 0x4D575842
#define BXWM_NEW_GAME 0
#define BXWM_LOAD 1
#define BXWM_NAME 0x10
#define BXWM_NAME_MAX 0x100

/* __cdecl: 0x001933E0 ends `mov esp,ebp; pop ebp; ret`, so the caller clears the arguments. */
typedef void *(__cdecl *fn_find_menu)(unsigned int id);
typedef void(__cdecl *fn_open_vk)(void *return_menu, const char *initial);
typedef void *(__attribute__((thiscall)) *fn_get_prop)(void *self, void *out, unsigned int id,
                                                       int type, int a3, int a4);
typedef const char *(__attribute__((thiscall)) *fn_widget_text)(void *widget);
typedef void(__attribute__((thiscall)) *fn_widget_set_text)(void *widget, const char *text);
typedef void(__attribute__((thiscall)) *fn_widget_dirty)(void *widget);
typedef void(__attribute__((thiscall)) *fn_perform_layout)(void *widget, int a0);
typedef void(__attribute__((thiscall)) *fn_set_prop)(void *widget, unsigned int id, int value,
                                                     int type);
typedef void(__attribute__((thiscall)) *fn_set_auto)(void *widget, int on);
typedef void *(__attribute__((thiscall)) *fn_find_child)(void *widget, unsigned int id);
typedef unsigned int(__cdecl *fn_ui_id)(const char *name);
typedef void(__attribute__((thiscall)) *fn_trigger_event)(void *widget, unsigned int id, int d0,
                                                          int d1, void *source);
typedef void *(__attribute__((thiscall)) *fn_create_widget)(void *parent, unsigned int id,
                                                            unsigned int factory, int a0);
typedef void *(__attribute__((thiscall)) *fn_create_image)(void *parent, unsigned int id,
                                                           const char *path, int reuse);
typedef char(__cdecl *fn_handler)(void *owner, unsigned int id, int d0, int d1, void *source);
typedef void(__cdecl *fn_button_hint)(int button, unsigned int label, unsigned int mode);
typedef void(__cdecl *fn_console_print)(void *game, const char *fmt, ...);
typedef void(__cdecl *fn_void)(void);
typedef void(__cdecl *fn_open_menu)(int);
typedef void *(__attribute__((thiscall)) *fn_resolve_object)(void *records, const char *id);
typedef void *(__attribute__((thiscall)) *fn_closest_ref)(void *records, void *object,
                                                         const float *position, int any_cell,
                                                         int unknown);
typedef void(__attribute__((thiscall)) *fn_ref_activate)(void *ref, void *activator, int flag);
typedef void *(__attribute__((thiscall)) *fn_player_mobile)(void *game);
typedef int(__cdecl *fn_vsprintf)(char *buf, const char *fmt, __builtin_va_list args);
typedef int(__attribute__((thiscall)) *fn_compile_run)(void *self, void *ref, const char *text,
                                                       int a2, int a3, int a4, int a5, int a6);

static int combo_a = COMBO_DEFAULT_A;
static int combo_b = COMBO_DEFAULT_B;
static int combo_ready;
static int seen_first;
static int was_held;
static int console_open;
static int vk_watch;
static char cmd[CMD_MAX];
static int run_delay;
static int running;         /* output belongs to a command being run */
static int output_lines;
static char first_output[0x104]; /* a command's first printed line, for `assert` */
static u32 first_len;
#define OUTPUT_MAX 8        /* a cell load inside a command runs scripts that print too */
static char hist[HIST_MAX][CMD_MAX];
static int hist_count;
static int hist_sel;      /* -1 is the empty field */
static int held_raise;
static int held_older;
static int held_newer;
static int held_symbols;
static int held_space;
static int held_caps;
static int held_a;        /* the press that raised the keyboard, withheld from it */
static int vk_fresh;      /* raised, not yet laid out */
static int vk_seen;
static int vk_wait;
static int vk_cancel;     /* B was seen while the keyboard was up */
static int vk_text_client; /* 1 active, 2 accepted, -1 cancelled */
static void *vk_owner;      /* a client's menu to open the keyboard on, or the console's */
static int symbols_on;
static int console_layout;
static unsigned int row_id[KEY_ROWS];
static unsigned int symbols_id;
static unsigned int older_id;
static unsigned int newer_id;

/* Read by the length-check stubs the patch puts in the keyboard's key and space handlers. */
u32 tes3x_vk_limit = VK_TEXT_LIMIT;

static const char *const symbol_keys[KEY_COUNT] = {
    "!", "@", "#", "$", "%", "^", "&", "*", "(", ")",
    "-", "_", "=", "+", "[", "]", "{", "}", "\\", "|",
    ";", ":", "'", "\"", ",", ".", "<", ">", "/", "?",
    "->", "~", "`", "<=", ">=", "!=",
};

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
    char buf[32];

    combo_ready = 1;
    tes3x_ini_xbox("ConsoleCombo", "", buf, sizeof(buf));
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

/* The engine's own key handlers follow a text change with these two, on the menu. */
static void relayout(void *menu)
{
    ((fn_widget_dirty)TES3X_WIDGET_DIRTY)(menu);
    ((fn_perform_layout)TES3X_PERFORM_LAYOUT)(menu, 1);
}

static void set_prop(void *el, unsigned int id, int value)
{
    ((fn_set_prop)TES3X_SET_PROP)(el, id, value, PROP_INT);
}

static void fix_size(void *el, int w, int h)
{
    set_prop(el, PROP_MIN_WIDTH, w);
    set_prop(el, PROP_MAX_WIDTH, w);
    set_prop(el, PROP_MIN_HEIGHT, h);
    set_prop(el, PROP_MAX_HEIGHT, h);
    set_prop(el, PROP_WIDTH, w);
    set_prop(el, PROP_HEIGHT, h);
}

/* The console takes the top half of the screen and the keyboard the bottom, both full width. */
static int place_half(void *menu, int bottom)
{
    unsigned char *m = (unsigned char *)menu;
    unsigned char *root = *(unsigned char **)(m + EL_PARENT);
    int w = root ? *(int *)(root + EL_WIDTH) : 0;
    int h = root ? *(int *)(root + EL_HEIGHT) : 0;

    if (w < 320 || w > 4096 || h < 240 || h > 4096) {
        tes3x_log("console.no_screen", (u32)((w << 16) | (h & 0xFFFF)));
        w = 640;
        h = 480;
    }
    ((fn_set_auto)TES3X_SET_AUTO_WIDTH)(menu, 0);
    ((fn_set_auto)TES3X_SET_AUTO_HEIGHT)(menu, 0);
    fix_size(menu, w, h / 2);
    *(u32 *)(m + EL_ALIGN_X) = F_HALF;
    *(u32 *)(m + EL_ALIGN_Y) = bottom ? F_ONE : F_ZERO;
    return w;
}

static void **row_keys(void *vk, int row, int *count)
{
    unsigned char *el = ((fn_find_child)TES3X_FIND_CHILD)(vk, row_id[row]);
    void **begin, **end;

    *count = 0;
    if (!el)
        return 0;
    begin = *(void ***)(el + EL_CHILDREN);
    end = *(void ***)(el + EL_CHILDREN + 4);
    if (!begin || end < begin)
        return 0;
    *count = (int)(end - begin);
    return begin;
}

static void load_row_ids(void)
{
    static char name[] = "MenuVirtualKeyboard_Row1";
    int i;

    if (row_id[0])
        return;
    symbols_id = ((fn_ui_id)TES3X_UI_ID)("MenuVirtualKeyboard_SymbolsButton");
    older_id = ((fn_ui_id)TES3X_UI_ID)("MenuVirtualKeyboard_OlderButton");
    newer_id = ((fn_ui_id)TES3X_UI_ID)("MenuVirtualKeyboard_NewerButton");
    for (i = 0; i < KEY_ROWS; i++) {
        name[sizeof(name) - 2] = (char)('1' + i);
        row_id[i] = ((fn_ui_id)TES3X_UI_ID)(name);
    }
}

static void set_ptr(void *el, unsigned int id_global, void *value)
{
    ((fn_set_prop)TES3X_SET_PROP)(el, *(unsigned short *)id_global, (int)value, PROP_PTR);
}

/* A key types its own label, so a symbol layer is only a relabel. */
static void label_keys(void *vk, int symbols)
{
    fn_widget_set_text set = (fn_widget_set_text)TES3X_WIDGET_SET_TEXT;
    fn_get_prop get = (fn_get_prop)TES3X_GET_PROP;
    unsigned int out[8] = {0};
    unsigned short *state;
    char letter = 'A';
    char buf[2];
    void **keys, *button;
    int row, col, n, k;

    state = (unsigned short *)get(vk, out, *(unsigned short *)TES3X_VK_CASE_ID, 0x10, 0, 0);
    if (state && *state == CASE_LOWER)
        letter = 'a';
    buf[1] = 0;
    for (row = 0; row < KEY_ROWS; row++) {
        keys = row_keys(vk, row, &n);
        for (col = 0; col < KEY_COLS && col < n; col++) {
            k = row * KEY_COLS + col;
            if (k >= KEY_COUNT || !keys[col])
                break;
            if (symbols) {
                set(keys[col], symbol_keys[k]);
                continue;
            }
            buf[0] = k < 10 ? (char)('0' + k) : (char)(letter + k - 10);
            set(keys[col], buf);
        }
    }
    button = ((fn_find_child)TES3X_FIND_CHILD)(vk, symbols_id);
    if (button)
        set(button, symbols ? "ABC" : "!?#");
    relayout(vk);
}

static void toggle_symbols(void *vk)
{
    symbols_on = !symbols_on;
    label_keys(vk, symbols_on);
    tes3x_log("console.symbols", (u32)symbols_on);
}

static void *keyboard_menu(void)
{
    return ((fn_find_menu)TES3X_FIND_MENU)(*(unsigned short *)TES3X_VK_MENU_ID);
}

static void *vk_child(void *vk, unsigned int id)
{
    return ((fn_find_child)TES3X_FIND_CHILD)(vk, id);
}

static void recall(void *vk, int step);
static void confirm(void *vk);

/* Engine event handlers are __cdecl, and true means handled. */
static char __cdecl symbols_clicked(void *owner, unsigned int id, int d0, int d1, void *source)
{
    void *vk = keyboard_menu();

    (void)owner, (void)id, (void)d0, (void)d1, (void)source;
    if (vk)
        toggle_symbols(vk);
    return 1;
}

static char __cdecl older_clicked(void *owner, unsigned int id, int d0, int d1, void *source)
{
    void *vk = keyboard_menu();

    (void)owner, (void)id, (void)d0, (void)d1, (void)source;
    if (vk)
        recall(vk, 1);
    return 1;
}

static char __cdecl newer_clicked(void *owner, unsigned int id, int d0, int d1, void *source)
{
    void *vk = keyboard_menu();

    (void)owner, (void)id, (void)d0, (void)d1, (void)source;
    if (vk)
        recall(vk, -1);
    return 1;
}

static char __cdecl y_pressed(void *owner, unsigned int id, int d0, int d1, void *source)
{
    void *vk = keyboard_menu();

    (void)owner, (void)id, (void)d0, (void)d1, (void)source;
    if (vk)
        confirm(vk);
    return 1;
}

/* Caps relabels every key as letters, so the symbol layer is off afterwards. */
static char __cdecl caps_clicked(void *owner, unsigned int id, int d0, int d1, void *source)
{
    char r = ((fn_handler)TES3X_VK_CAPS)(owner, id, d0, d1, source);
    void *vk = keyboard_menu();
    void *button;

    symbols_on = 0;
    button = vk ? vk_child(vk, symbols_id) : 0;
    if (button)
        ((fn_widget_set_text)TES3X_WIDGET_SET_TEXT)(button, "!?#");
    return r;
}

static void add_icon(void *button, const char *path)
{
    if (button)
        ((fn_create_image)TES3X_CREATE_IMAGE)(button, ID_GENERIC, path, 0);
}

static void *add_button(void *block, unsigned int id, const char *label, fn_handler handler)
{
    void *btn = ((fn_create_widget)TES3X_CREATE_WIDGET)(block, id, TES3X_VK_BUTTON, 0);

    if (!btn)
        return 0;
    ((fn_widget_set_text)TES3X_WIDGET_SET_TEXT)(btn, label);
    set_prop(btn, *(unsigned short *)TES3X_VK_ROW_NUM_ID, 4);
    set_prop(btn, *(unsigned short *)TES3X_VK_COL_NUM_ID, -1);
    ((fn_set_prop)TES3X_SET_PROP)(btn, EVENT_CLICK, (int)handler, PROP_HANDLER);
    return btn;
}

static void hint(int button, const char *label)
{
    ((fn_button_hint)TES3X_BUTTON_HINT)(button, ((fn_ui_id)TES3X_UI_ID)(label), HINT_MODE);
}

/* The width a row actually gets inside the keyboard's frame, split between n items. */
static int share(void *row, int fallback, int n)
{
    int w = row ? *(int *)((unsigned char *)row + EL_WIDTH) : 0;

    if (w < 100 || w > fallback)
        w = fallback - KEYBOARD_PAD;
    return w / n - KEY_GAP;
}

static int item_gap = ITEM_SPACING;  /* the spacing the last refit measured */

static int el_int(void *el, int field)
{
    return *(int *)((unsigned char *)el + field);
}

/* Items are laid out wider apart than their width. Measure the pitch the layout produced for
 * `width` and return the width that makes n items end inside the row. */
static int refit(void *row, void **items, int n, int width)
{
    int row_w, first, last, extra, fitted;

    if (!row || !items || n < 2 || !items[0] || !items[n - 1])
        return width;
    row_w = el_int(row, EL_WIDTH);
    first = el_int(items[0], EL_X);
    last = el_int(items[n - 1], EL_X);
    extra = (last - first) / (n - 1) - width;
    tes3x_log("console.vk_measure", (u32)((row_w << 16) | ((last - first) & 0xFFFF)));
    if (row_w < 100 || extra < 0 || extra > 64 || first < 0 || first > 64)
        return width;
    item_gap = extra;
    fitted = (row_w - 2 * first - (n - 1) * extra) / n;
    return fitted > 8 && fitted < width ? fitted : width;
}

static void set_width(void **items, int n, int width)
{
    int i;

    for (i = 0; i < n; i++) {
        if (!items[i])
            continue;
        set_prop(items[i], PROP_MIN_WIDTH, width);
        set_prop(items[i], PROP_MAX_WIDTH, width);
    }
}

/* Symbols, older and newer join the bottom row, which is reordered and sized to end where the key
 * rows do. The engine's focus links to and from this row follow its old order, so all are set
 * again. */
static void extend_bottom_row(void *vk, int kw)
{
    unsigned char *done = vk_child(vk, *(unsigned short *)TES3X_VK_DONE_ID);
    void *caps = vk_child(vk, *(unsigned short *)TES3X_VK_CAPS_ID);
    void *back = vk_child(vk, *(unsigned short *)TES3X_VK_BACKSPACE_ID);
    unsigned char *block;
    void **begin, **end, *added[3], *order[BOTTOM_BUTTONS];
    void **first_row, **last_row;
    int i, n, span, used, w, col, pitch, mid, first_count, last_count;
    int start[BOTTOM_BUTTONS], mid_of[BOTTOM_BUTTONS];
    /* typing for another menu: no history, so no Older and Newer */
    int nb = vk_owner ? BOTTOM_BUTTONS - 2 : BOTTOM_BUTTONS;
    const int *share = vk_owner ? bottom_share_text : bottom_share;

    if (!done || !caps || !back || vk_child(vk, symbols_id))
        return;
    block = *(unsigned char **)(done + EL_PARENT);
    if (!block)
        return;
    added[0] = add_button(block, symbols_id, "!?#", symbols_clicked);
    added[1] = vk_owner ? 0 : add_button(block, older_id, "Older", older_clicked);
    added[2] = vk_owner ? 0 : add_button(block, newer_id, "Newer", newer_clicked);

    begin = *(void ***)(block + EL_CHILDREN);
    end = *(void ***)(block + EL_CHILDREN + 4);
    n = (int)(end - begin);
    i = 0;
    if (!vk_owner) {
        order[i++] = added[1];
        order[i++] = added[2];
    }
    order[i++] = added[0];
    order[i++] = back;
    order[i++] = caps;
    order[i++] = done;
    for (i = 0; i < nb; i++)
        if (!order[i])
            break;
    if (n != nb || i != nb) {
        tes3x_log("console.vk_bottom_row", (u32)n);
        return;
    }
    first_row = row_keys(vk, 0, &first_count);
    last_row = row_keys(vk, KEY_ROWS - 1, &last_count);

    span = KEY_COLS * kw + (KEY_COLS - 1 - (nb - 1)) * item_gap;
    for (i = 0, used = 0; i < nb; i++) {
        begin[i] = order[i];
        w = i + 1 < nb ? span * share[i] / 100 : span - used;
        set_prop(order[i], PROP_MIN_WIDTH, w);
        set_prop(order[i], PROP_MAX_WIDTH, w);
        set_ptr(order[i], TES3X_NAV_LEFT_ID, order[(i + nb - 1) % nb]);
        set_ptr(order[i], TES3X_NAV_RIGHT_ID, order[(i + 1) % nb]);
        start[i] = used + i * item_gap;
        mid_of[i] = start[i] + w / 2;
        used += w;
    }

    /* Vertical links join each button to the keys over its centre: up to the last key row, and
     * down, wrapping, to the first. The space bar covers the last row's remaining columns. */
    pitch = kw + item_gap;
    for (i = 0; i < nb; i++) {
        col = mid_of[i] / pitch;
        if (last_count && last_row[col < last_count ? col : last_count - 1])
            set_ptr(order[i], TES3X_NAV_UP_ID, last_row[col < last_count ? col : last_count - 1]);
        if (first_count && first_row[col < first_count ? col : first_count - 1])
            set_ptr(order[i], TES3X_NAV_DOWN_ID,
                    first_row[col < first_count ? col : first_count - 1]);
    }
    for (col = 0; col < last_count; col++) {
        mid = col + 1 < last_count ? col * pitch + kw / 2
                                   : (col * pitch + KEY_COLS * pitch - item_gap) / 2;
        for (i = nb - 1; i > 0 && start[i] > mid; i--)
            ;
        if (last_row[col])
            set_ptr(last_row[col], TES3X_NAV_DOWN_ID, order[i]);
    }
    for (col = 0; col < first_count; col++) {
        mid = col * pitch + kw / 2;
        for (i = nb - 1; i > 0 && start[i] > mid; i--)
            ;
        if (first_row[col])
            set_ptr(first_row[col], TES3X_NAV_UP_ID, order[i]);
    }

    ((fn_set_prop)TES3X_SET_PROP)(caps, EVENT_CLICK, (int)caps_clicked, PROP_HANDLER);
    ((fn_set_prop)TES3X_SET_PROP)(vk, EVENT_PAD_Y, (int)y_pressed, PROP_HANDLER);

    add_icon(vk_child(vk, *(unsigned short *)TES3X_VK_BACKSPACE_ID), "Textures\\xbox_button_x.tga");
    add_icon(added[0], "Textures\\xbox_button_white.tga");
    add_icon(added[1], "Textures\\xbox_button_left.tga");
    add_icon(added[2], "Textures\\xbox_button_right.tga");
    add_icon(done, "Textures\\xbox_button_y.tga");
    add_icon(caps, "Textures\\xbox_button_lthumb.tga");
    add_icon(vk_child(vk, *(unsigned short *)TES3X_VK_SPACE_ID), "Textures\\xbox_button_black.tga");

    hint('Y', ((fn_widget_text)TES3X_WIDGET_TEXT)(done));
    hint('W', "Symbols");
    hint('K', "Space");
    hint('L', "Older");
    hint('R', "Newer");
}

static void layout_keyboard(void *vk)
{
    void **keys;
    int w, kw, row, col, n, k;

    load_row_ids();
    w = place_half(vk, 1);
    relayout(vk);
    kw = share(vk_child(vk, row_id[0]), w, KEY_COLS);
    keys = row_keys(vk, 0, &n);
    if (n >= KEY_COLS) {
        set_width(keys, KEY_COLS, kw);
        relayout(vk);
        kw = refit(vk_child(vk, row_id[0]), keys, KEY_COLS, kw);
    }
    for (row = 0; row < KEY_ROWS; row++) {
        keys = row_keys(vk, row, &n);
        for (col = 0; col < n; col++) {
            if (!keys[col])
                continue;
            k = row * KEY_COLS + col;
            if (k < KEY_COUNT) {
                set_prop(keys[col], PROP_MIN_WIDTH, kw);
                set_prop(keys[col], PROP_MAX_WIDTH, kw);
            } else {
                /* the space bar fills the rest of the last row */
                set_prop(keys[col], PROP_MIN_WIDTH,
                         (kw + item_gap) * (KEY_ROWS * KEY_COLS - KEY_COUNT) - item_gap);
            }
        }
    }
    extend_bottom_row(vk, kw);
    relayout(vk);
    tes3x_log("console.vk_layout", (u32)kw);
}

static void layout_console(void)
{
    fn_find_menu find = (fn_find_menu)TES3X_FIND_MENU;
    unsigned char *menu = find(*(unsigned short *)TES3X_CONSOLE_MENU_ID);

    if (!menu || !menu[MENU_VISIBLE])
        return;
    console_layout = 0;
    place_half(menu, 0);
    relayout(menu);
    tes3x_log("console.layout", 0);
}

static int open_keyboard(void)
{
    void *menu = vk_owner ? vk_owner
                          : ((fn_find_menu)TES3X_FIND_MENU)(*(unsigned short *)TES3X_CONSOLE_MENU_ID);

    if (!menu) {
        tes3x_log("console.vk_no_menu", 0);
        return 0;
    }
    /* Passing initial text here prevents the keyboard from appearing. */
    ((fn_open_vk)TES3X_OPEN_VK)(menu, 0);
    return 1;
}

static void raise_keyboard(void)
{
    if (!open_keyboard())
        return;
    vk_watch = 1;
    vk_fresh = 1;
    vk_seen = 0;
    vk_wait = 0;
    vk_cancel = 0;
    symbols_on = 0;
    hist_sel = -1;
    cmd[0] = 0;
    tes3x_vk_limit = CMD_MAX - 1;
    tes3x_log("console.vk_raise", 0);
}

/* a0 is the menu manager's scratch script object; a3 is an optional reference. */
static void run_command(const char *text)
{
    unsigned char *game = *(unsigned char **)TES3X_GAME_PTR;
    unsigned char *mgr;
    void *ctx, *script;

#ifdef TES3X_PROFILE
    if (tes3x_prof_command(text))
        return;
#endif
#ifdef TES3X_HEAP
    if (tes3x_heap_command(text))
        return;
#endif
#ifdef TES3X_MEM
    if (tes3x_mem_command(text))
        return;
#endif
#ifdef TES3X_PAGER
    if (tes3x_pager_command(text))
        return;
#endif
#ifdef TES3X_NET
    if (tes3x_net_command(text))
        return;
#endif
#ifdef TES3X_REGION
    if (tes3x_region_command(text))
        return;
#endif
#ifdef TES3X_MCP3_TEST
    if (tes3x_mcp3_test_command(text))
        return;
#endif
#ifdef TES3X_DIALMERGE_TEST
    if (tes3x_dialmerge_test_command(text))
        return;
#endif
#ifdef TES3X_DIAGNOSTICS
    if (tes3x_diag_command(text))
        return;
#endif
    if (!game) {
        tes3x_log("console.no_game", 0);
        return;
    }
#ifdef TES3X_SAVES
    if (tes3x_autosave_command(game, text))
        return;
#endif
    mgr = *(unsigned char **)(game + GAME_MENUMGR);
    script = *(void **)(game + GAME_SCRIPT);
    ctx = mgr ? *(void **)(mgr + MENUMGR_CTX) : 0;
    if (!script || !ctx) {
        tes3x_log("console.no_ctx", (u32)(unsigned int)ctx);
        return;
    }
    running = 1;
    output_lines = 0;
    first_len = 0;
    ((fn_compile_run)TES3X_COMPILE_RUN)(script, ctx, text, 1, 0, 0, 0, 0);
    running = 0;
    if (output_lines > OUTPUT_MAX)
        tes3x_log("console.more", (u32)(output_lines - OUTPUT_MAX));
}

static char exec_hdd[] = "\\Device\\Harddisk0\\Partition1\\tes3xexec.txt";
static char exec_disc[] = "D:\\tes3xexec.txt";
static char *exec_buf;
static u32 exec_len;
static u32 exec_alloc;
static u32 exec_pos[2];   /* next unread offset: menu lines, then game lines */
static int exec_wait;
static int exec_quiet;    /* frames since the main menu was last up */
static int exec_tries;
static unsigned int options_id;

static int exec_open(void **h, char *path, int dos)
{
    ANSI_STRING name;
    OBJECT_ATTRIBUTES oa;
    IO_STATUS_BLOCK iosb;

    if (dos)
        tes3x_dos_attributes(&oa, &name, path);
    else
        tes3x_object_attributes(&oa, &name, path);
    return NtCreateFile(h, GENERIC_READ | SYNCHRONIZE, &oa, &iosb, 0, FILE_ATTRIBUTE_NORMAL,
                        FILE_SHARE_READ, FILE_OPEN, FILE_SYNCHRONOUS_IO_NONALERT) == 0;
}

/* Sized to the file, so a long script costs memory only while it runs. The zeroed tail lets a
 * prefix test run past the last line. */
static void exec_load(void)
{
    IO_STATUS_BLOCK iosb;
    FILE_NETWORK_OPEN_INFORMATION st;
    u64 zero = 0;
    void *h = 0;
    int disc = 0;
    u32 size, i, status;

    if (!exec_open(&h, exec_hdd, 0)) {
        if (!exec_open(&h, exec_disc, 1))
            return;
        disc = 1;
    }
    size = 0;
    status = NtQueryInformationFile(h, &iosb, &st, sizeof(st), FileNetworkOpenInformation);
    if (status == 0)
        size = (u32)st.EndOfFile;
    else
        tes3x_log_hex("exec.size_status", status);
    if (size > EXEC_MAX)
        size = EXEC_MAX;
    exec_alloc = size + EXEC_PAD;
    exec_buf = size ? MmAllocateSystemMemory(exec_alloc, PAGE_READWRITE) : 0;
    if (size && !exec_buf)
        tes3x_log("exec.no_buffer", exec_alloc);
    if (exec_buf) {
        for (i = 0; i < exec_alloc; i++)
            exec_buf[i] = 0;
        iosb.Information = 0;
        status = NtReadFile(h, 0, 0, 0, &iosb, exec_buf, size, &zero);
        if (status == 0)
            exec_len = iosb.Information;
        else
            tes3x_log_hex("exec.read_status", status);
    }
    NtClose(h);
    tes3x_log(disc ? "exec.loaded_disc" : "exec.loaded", exec_len);
}

static void exec_free(void)
{
    if (exec_buf)
        MmFreeSystemMemory(exec_buf, exec_alloc);
    exec_buf = 0;
    exec_len = 0;
    tes3x_log("exec.done", 0);
}

static int starts_with(const char *s, const char *prefix)
{
    while (*prefix)
        if (*s++ != *prefix++)
            return 0;
    return 1;
}

/* The next line of this phase from *pos, trimmed, into line. Returns 0 at the end. */
static int exec_line(int menu, u32 *pos, char *line)
{
    u32 p = *pos, start, end, n;
    int is_menu;

    while (p < exec_len) {
        start = p;
        while (p < exec_len && exec_buf[p] != '\n')
            p++;
        end = p;
        if (p < exec_len)
            p++;
        while (start < end && (exec_buf[start] == ' ' || exec_buf[start] == '\t'))
            start++;
        while (end > start && (exec_buf[end - 1] == '\r' || exec_buf[end - 1] == ' '))
            end--;
        if (start == end || exec_buf[start] == '#' || starts_with(exec_buf + start, "@start "))
            continue;
        is_menu = end - start > 6 && starts_with(exec_buf + start, "@menu ");
        if (is_menu != menu)
            continue;
        if (is_menu)
            start += 6;
        for (n = 0; start < end && n < CMD_MAX - 1; n++)
            line[n] = exec_buf[start++];
        line[n] = 0;
        *pos = p;
        return 1;
    }
    *pos = p;
    return 0;
}

/* `@start new` or `@start load U:\DIR\NAME.ess`, run at the XBE entry. Hands the engine the same
 * launch data its own New Game and Load relaunches pass (built at 0x001FFD79 and 0x00201AC8), so
 * it starts the game without drawing the main menu. Only when nothing launched the title with data
 * of its own. */
void tes3x_console_start(void)
{
    void **page_var = *(void ***)THUNK_LaunchDataPage;
    char line[CMD_MAX];
    const char *name;
    u32 p = 0, start = 0, n, mode;
    unsigned char *page, *data;
    int found = 0;

    exec_load();
    while (p < exec_len && !found) {
        start = p;
        while (p < exec_len && exec_buf[p] != '\n')
            p++;
        found = starts_with(exec_buf + start, "@start ");
        if (!found)
            p++;
    }
    if (!found)
        return;
    start += 7;
    for (n = 0; start + n < p && n < CMD_MAX - 1 && exec_buf[start + n] != '\r'; n++)
        line[n] = exec_buf[start + n];
    line[n] = 0;
    if (starts_with(line, "new") && !line[3]) {
        mode = BXWM_NEW_GAME;
        name = "";
    } else if (starts_with(line, "load ") && line[5]) {
        mode = BXWM_LOAD;
        name = line + 5;
    } else {
        tes3x_log("exec.start_unknown", 0);
        return;
    }
    if (*page_var) {
        tes3x_log("exec.start_launched", 0);
        return;
    }
    page = MmAllocateContiguousMemory(LAUNCH_PAGE);
    if (!page) {
        tes3x_log("exec.start_no_page", 0);
        return;
    }
    for (p = 0; p < LAUNCH_PAGE; p++)
        page[p] = 0;
    /* XGetLaunchInfo takes title data only for this title: the id at XBE certificate + 8. */
    ((u32 *)page)[0] = LDT_TITLE;
    ((u32 *)page)[1] = *(u32 *)(*(u32 *)XBE_CERT_PTR + 8);
    data = page + LAUNCH_DATA;
    ((u32 *)data)[0] = BXWM_MAGIC;
    ((u32 *)data)[3] = mode;
    for (p = 0; name[p] && p < BXWM_NAME_MAX - 1; p++)
        data[BXWM_NAME + p] = name[p];
    *page_var = page;
    tes3x_log(mode == BXWM_LOAD ? "exec.start_load" : "exec.start_new", p);
}

/* `launch \Device\...\NAME.xbe` or `launch F:\...\NAME.xbe`: start another title in any
 * folder. */
static void exec_summary(void);
static void exec_launch(const char *path)
{
    exec_summary();
    tes3x_log("exec.launch", tes3x_strlen(path));
    tes3x_launch(path);
}

static int menu_up(void *menu)
{
    return menu && *((unsigned char *)menu + MENU_VISIBLE);
}

/* "MENU WIDGET". Returns 0 while the widget is not on screen yet. */
static int exec_click(char *args)
{
    fn_ui_id ui_id = (fn_ui_id)TES3X_UI_ID;
    char *widget = args;
    void *menu, *el;

    while (*widget && *widget != ' ')
        widget++;
    if (!*widget)
        return 1;
    *widget++ = 0;
    while (*widget == ' ')
        widget++;
    menu = ((fn_find_menu)TES3X_FIND_MENU)(ui_id(args));
    if (!menu_up(menu))
        return 0;
    el = ((fn_find_child)TES3X_FIND_CHILD)(menu, ui_id(widget));
    if (!el)
        return 0;
    ((fn_trigger_event)TES3X_TRIGGER_EVENT)(el, EVENT_CLICK, 0, 0, el);
    return 1;
}

static int exec_number(const char *s);

/* "MENU A|B|X|Y|N": the pad button's event on the menu, as the pad delivers it; N is the event's
 * offset from A's. Returns 0 while the menu is not on screen yet. */
static int exec_pad(char *args)
{
    static const char buttons[] = "ABXY";
    char *button = args;
    void *menu;
    u32 i;

    while (*button && *button != ' ')
        button++;
    if (!*button)
        return 1;
    *button++ = 0;
    while (*button == ' ')
        button++;
    for (i = 0; buttons[i] && buttons[i] != *button; i++)
        ;
    if (*button >= '0' && *button <= '9')
        i = (u32)exec_number(button);
    else if (!buttons[i] || button[1])
        return 1;
    menu = ((fn_find_menu)TES3X_FIND_MENU)(((fn_ui_id)TES3X_UI_ID)(args));
    if (!menu_up(menu))
        return 0;
    ((fn_trigger_event)TES3X_TRIGGER_EVENT)(menu, EVENT_PAD_A + i, 0, 0, menu);
    return 1;
}

/* "up|down|left|right": the D-pad, through the engine's own navigation. */
typedef void(__cdecl *fn_ui_nav)(int direction, char sound, char remember);
static int exec_nav(const char *args)
{
    static const char *const names[4] = {"up", "down", "left", "right"};
    u32 i, n = 0;

    while (args[n] && args[n] != ' ')
        n++;
    for (i = 0; i < 4 && !(starts_with(args, names[i]) && !names[i][n]); i++)
        ;
    if (i == 4)
        return 0;
    ((fn_ui_nav)TES3X_UI_NAV)(0xE + (int)i, 1, 1);
    return 1;
}

/* `visible MENU`: whether it is on screen, as `menu.MENU 0|1`. */
static void exec_visible(const char *name)
{
    char tag[64];
    u32 n;

    tag[0] = 'm';
    tag[1] = 'e';
    tag[2] = 'n';
    tag[3] = 'u';
    tag[4] = '.';
    for (n = 5; name[n - 5] && n < sizeof(tag) - 1; n++)
        tag[n] = name[n - 5];
    tag[n] = 0;
    tes3x_log(tag, (u32)menu_up(((fn_find_menu)TES3X_FIND_MENU)(((fn_ui_id)TES3X_UI_ID)(name))));
}

/* `activate ID`: the player activates the nearest reference of ID, as the pad's A does; a script
 * `Activate` only flags scripted objects. 0 if there is none. */
static int exec_activate(const char *id)
{
    void **handler = *(void ***)TES3X_RECORDS_PTR, *game = *(void **)TES3X_GAME_PTR;
    unsigned char *mobile, *player;
    void *records, *object, *ref;

    if (!handler || !(records = *handler) || !game ||
        !(mobile = ((fn_player_mobile)TES3X_PLAYER_MOBILE)(game)) ||
        !(player = *(unsigned char **)(mobile + 0x14)))
        return 0;
    if (!(object = ((fn_resolve_object)TES3X_RESOLVE_OBJECT)(records, id)))
        return 0;
    ref = ((fn_closest_ref)TES3X_CLOSEST_REF)(records, object, (const float *)(player + 0x38), 0,
                                              -1);
    if (!ref)
        return 0;
    ((fn_ref_activate)TES3X_REF_ACTIVATE)(ref, player, 1);
    return 1;
}

/* "inventory" or "journal": what the pad's menu and journal buttons call in gameplay. */
static int exec_menu(const char *name)
{
    if (starts_with(name, "inventory") && !name[9]) {
        ((fn_open_menu)TES3X_OPEN_MENU)(1);
    } else if (starts_with(name, "journal") && !name[7]) {
        ((fn_void)TES3X_OPEN_JOURNAL)();
        ((fn_void)TES3X_JOURNAL_OPENED)();
    } else if (starts_with(name, "options") && !name[7]) {
        /* as Start does: menu mode, MenuOptions::open, then the menu to the front */
        unsigned char *world = *(unsigned char **)TES3X_GAME_PTR;
        world[0xD2] = 1;
        world[0xD0] = 1;
        world[0xD1] = 0;
        ((void(__cdecl *)(int))TES3X_OPTIONS_OPEN)(0);
        ((void(__cdecl *)(void *))TES3X_MENU_MODE_ON)(((fn_find_menu)TES3X_FIND_MENU)(
            (u32)(int)*(const short *)TES3X_OPTIONS_ID));
    } else {
        return 0;
    }
    return 1;
}

static int exec_number(const char *s)
{
    int v = 0;

    while (*s >= '0' && *s <= '9')
        v = v * 10 + (*s++ - '0');
    return v;
}

/* `mark LABEL`: free physical memory now, as `mem.LABEL <KB>`. */
static void exec_mark(const char *label)
{
    MM_STATS st;
#ifdef TES3X_REGION
    const char *ws_label = label;
#endif
    char tag[64];
    u32 n;

    tag[0] = 'm';
    tag[1] = 'e';
    tag[2] = 'm';
    tag[3] = '.';
    for (n = 4; *label && n < sizeof(tag) - 1; n++)
        tag[n] = *label++;
    tag[n] = 0;
    st.Length = sizeof(st);
    if (MmQueryStatistics(&st) != 0)
        st.AvailablePages = 0;
    tes3x_log(tag, st.AvailablePages * 4);
#ifdef TES3X_REGION
    tes3x_region_mark(ws_label);
#endif
}

static u32 assert_total, assert_failed;

/* `assert COMMAND == VALUE`: runs the command and compares the value it prints, the text after the
 * last ">> " of its first line (`GetPos >> 61.00` prints 61.00), with VALUE. */
static void exec_assert(char *line)
{
    char *cmd = line + 7, *want = 0, *got;
    u32 i, n, wn;

    for (i = 0; cmd[i]; i++)
        if (starts_with(cmd + i, " == "))
            want = cmd + i;
    tes3x_log_raw("assert> ", 8);
    for (n = 0; cmd[n]; n++)
        ;
    tes3x_log_raw(cmd, n);
    tes3x_log_raw("\n", 1);
    assert_total++;
    if (want) {
        *want = 0;
        want += 4;
        run_command(cmd);
    }
    got = first_output;
    n = want ? first_len : 0;
    while (n && (got[n - 1] == '\n' || got[n - 1] == '\r' || got[n - 1] == ' '))
        n--;
    for (i = 0; i + 3 <= n; i++)
        if (starts_with(first_output + i, ">> ")) {
            got = first_output + i + 3;
            n -= i + 3;
            i = 0;
        }
    for (wn = 0; want && want[wn]; wn++)
        ;
    for (i = 0; want && i < n && i < wn && got[i] == want[i]; i++)
        ;
    if (want && i == n && i == wn) {
        tes3x_log("assert.pass", assert_total);
        return;
    }
    assert_failed++;
    tes3x_log("assert.fail", assert_total);
    tes3x_log_raw("assert.got ", 11);
    tes3x_log_raw(got, n);
    tes3x_log_raw("\n", 1);
}

/* Logged before a script ends the session, or when it runs out. */
static void exec_summary(void)
{
    if (!assert_total)
        return;
    tes3x_log("assert.total", assert_total);
    tes3x_log("assert.failed", assert_failed);
    assert_total = 0;
}

/* One line per frame at most, so each command sees the frame the last one left. */
static void exec_step(void)
{
    char line[CMD_MAX];
    int up, phase;
    u32 pos, n;

    if (!options_id)
        options_id = ((fn_ui_id)TES3X_UI_ID)("MenuOptions");
    up = menu_up(((fn_find_menu)TES3X_FIND_MENU)(options_id));
    phase = up ? 0 : 1;
    exec_quiet = up ? 0 : exec_quiet + 1;
    if (exec_wait) {
        exec_wait--;
        return;
    }
    if (!up && exec_quiet < EXEC_SETTLE)
        return;
    pos = exec_pos[phase];
    if (!exec_line(phase == 0, &pos, line)) {
        if (phase == 1) {
            exec_summary();
            exec_free();
        }
        return;
    }
    for (n = 0; line[n]; n++)
        ;
    if (starts_with(line, "wait ")) {
        exec_wait = exec_number(line + 5);
    } else if (starts_with(line, "mark ")) {
        exec_mark(line + 5);
    } else if (starts_with(line, "exit") && !line[4]) {
        exec_summary();
        tes3x_log("exec.exit", 0);
        HalInitiateShutdown();
    } else if (starts_with(line, "reboot") && !line[6]) {
        /* A full reboot goes through the BIOS to the dashboard, like power-on. */
        exec_summary();
        tes3x_log("exec.reboot", 0);
        HalReturnToFirmware(HAL_REBOOT_ROUTINE);
    } else if (starts_with(line, "launch ")) {
        exec_launch(line + 7);
    } else if (starts_with(line, "click ")) {
        if (!exec_click(line + 6) && ++exec_tries < EXEC_CLICK_FRAMES)
            return;
        tes3x_log(exec_tries < EXEC_CLICK_FRAMES ? "exec.clicked" : "exec.click_missing",
                  (u32)exec_tries);
        exec_tries = 0;
    } else if (starts_with(line, "pad ")) {
        if (!exec_pad(line + 4) && ++exec_tries < EXEC_CLICK_FRAMES)
            return;
        tes3x_log(exec_tries < EXEC_CLICK_FRAMES ? "exec.pad" : "exec.pad_missing",
                  (u32)exec_tries);
        exec_tries = 0;
    } else if (starts_with(line, "nav ")) {
        tes3x_log("exec.nav", (u32)exec_nav(line + 4));
    } else if (starts_with(line, "menu ")) {
        tes3x_log("exec.menu", (u32)exec_menu(line + 5));
    } else if (starts_with(line, "activate ")) {
        tes3x_log("exec.activate", (u32)exec_activate(line + 9));
    } else if (starts_with(line, "visible ")) {
        exec_visible(line + 8);
    } else if (starts_with(line, "assert ")) {
        exec_assert(line);
    } else {
        tes3x_log_raw("exec> ", 6);
        tes3x_log_raw(line, n);
        tes3x_log_raw("\n", 1);
        run_command(line);
    }
    exec_pos[phase] = pos;
}

/* Every engine call to the console's printf comes here, so console output reaches the log whether
 * or not the console menu exists. Only a running command's first lines are logged: the engine can
 * leave printing on, and every script then reports its checks. The buffer matches the original's. */
void __cdecl tes3x_console_print(void *game, const char *fmt, ...)
{
    char buf[0x104];
    __builtin_va_list args;
    int n;

    __builtin_va_start(args, fmt);
    n = ((fn_vsprintf)TES3X_VSPRINTF)(buf, fmt, args);
    __builtin_va_end(args);
    if (n < 0 || n >= (int)sizeof(buf))
        n = 0;
    if (running && output_lines == 0) {
        for (first_len = 0; first_len < (u32)n; first_len++)
            first_output[first_len] = buf[first_len];
    }
    if (running && ++output_lines <= OUTPUT_MAX) {
        tes3x_log_raw("console< ", 9);
        tes3x_log_raw(buf, (u32)n);
        tes3x_log_raw("\n", 1);
    }
    ((fn_console_print)TES3X_CONSOLE_PRINT)(game, "%s", buf);
}

/* A debugger writes `text`, then changes `seq`; the next frame runs it and sets `done` to match,
 * so the host knows it was taken. Nothing writes it on hardware. */
struct {
    u32 magic;
    volatile u32 seq;
    volatile u32 done;
    char text[CMD_MAX];
} tes3x_mailbox = {MAILBOX_MAGIC, 0, 0, {0}};

static void mailbox_step(void)
{
    char line[CMD_MAX];
    u32 n;

    for (n = 0; n < CMD_MAX - 1 && tes3x_mailbox.text[n]; n++)
        line[n] = tes3x_mailbox.text[n];
    line[n] = 0;
    tes3x_mailbox.done = tes3x_mailbox.seq;
    tes3x_log_raw("live> ", 6);
    tes3x_log_raw(line, n);
    tes3x_log_raw("\n", 1);
    if (starts_with(line, "mark ")) {
        exec_mark(line + 5);
    } else if (starts_with(line, "exit") && !line[4]) {
        exec_summary();
        tes3x_log("live.exit", 0);
        HalInitiateShutdown();
    } else {
        run_command(line);
    }
}

/* Queue a line for the next frame through the mailbox. Returns 0 while another is pending. */
int tes3x_console_submit(const char *text, u32 n)
{
    u32 i;

    if (tes3x_mailbox.seq != tes3x_mailbox.done)
        return 0;
    for (i = 0; i < n && i < CMD_MAX - 1 && text[i]; i++)
        tes3x_mailbox.text[i] = text[i];
    tes3x_mailbox.text[i] = 0;
    tes3x_mailbox.seq++;
    return 1;
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

/* Rising edge of a button while the keyboard is up; the press is consumed either way. */
static int pressed(short *in, int key, int *held)
{
    int down = in[key] != 0;
    int edge = down && !*held;

    in[key] = 0;
    *held = down;
    return edge;
}

/* step 1 is older, -1 newer; the empty field sits between the newest and the oldest. */
static void recall(void *vk, int step)
{
    void *field = keyboard_field(vk);

    if (!hist_count || !field)
        return;
    hist_sel += step;
    if (hist_sel >= hist_count)
        hist_sel = -1;
    else if (hist_sel < -1)
        hist_sel = hist_count - 1;
    ((fn_widget_set_text)TES3X_WIDGET_SET_TEXT)(field, hist_sel < 0 ? "" : hist[hist_sel]);
    relayout(vk);
    tes3x_log("console.recall", (u32)(hist_sel + 1));
}

/* Press a keyboard button the way A does on the focused one. */
static void click(void *vk, unsigned int id_global)
{
    void *button = vk_child(vk, *(unsigned short *)id_global);

    if (button)
        ((fn_trigger_event)TES3X_TRIGGER_EVENT)(button, EVENT_CLICK, 0, 0, button);
}

static void confirm(void *vk)
{
    click(vk, TES3X_VK_DONE_ID);
}

static void watch_keyboard(short *in)
{
    fn_find_menu find = (fn_find_menu)TES3X_FIND_MENU;
    void *vk = find(*(unsigned short *)TES3X_VK_MENU_ID);
    const char *text;
    int i;

    if (vk && *((unsigned char *)vk + MENU_VISIBLE)) {
        vk_seen = 1;
        if (vk_fresh) {
            vk_fresh = 0;
            layout_keyboard(vk);
            return;
        }
        text = keyboard_text(vk);
        if (text) {
            for (i = 0; i < CMD_MAX - 1 && text[i] >= 0x20 && text[i] < 0x7F; i++)
                cmd[i] = text[i];
            cmd[i] = 0;
        }
        if (in) {
            if (in[KEY_B])
                vk_cancel = 1;
            if (pressed(in, KEY_HIST_OLDER, &held_older) && !vk_owner)
                recall(vk, 1);
            if (pressed(in, KEY_HIST_NEWER, &held_newer) && !vk_owner)
                recall(vk, -1);
            if (pressed(in, KEY_SYMBOLS, &held_symbols))
                toggle_symbols(vk);
            if (pressed(in, KEY_SPACE, &held_space))
                click(vk, TES3X_VK_SPACE_ID);
            if (pressed(in, KEY_CAPS, &held_caps))
                click(vk, TES3X_VK_CAPS_ID);
        }
        return;
    }
    if (!vk_seen && ++vk_wait < VK_APPEAR_FRAMES) {
        if (vk_wait == VK_RETRY_FRAME && open_keyboard())
            tes3x_log("console.vk_retry", 0);
        return;
    }

    vk_watch = 0;
    vk_fresh = 0;
    tes3x_vk_limit = VK_TEXT_LIMIT;
    if (vk_text_client == 1) {
        vk_text_client = vk_cancel ? -1 : 2;
        return;
    }
    if (vk_cancel || !cmd[0]) {
        tes3x_log("console.cancelled", (u32)vk_cancel);
        cmd[0] = 0;
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

static int text_begin(void *owner, const char *initial)
{
    u32 i = 0;
    if (vk_watch || run_delay || vk_text_client)
        return 0;
    vk_owner = owner;
    while (initial && initial[i] && i < CMD_MAX - 1) {
        cmd[i] = initial[i];
        i++;
    }
    cmd[i] = 0;
    if (!open_keyboard()) {
        vk_owner = 0;
        return 0;
    }
    vk_watch = 1;
    vk_fresh = 1;
    vk_seen = 0;
    vk_wait = 0;
    vk_cancel = 0;
    symbols_on = 0;
    hist_sel = -1;
    vk_text_client = 1;
    tes3x_vk_limit = CMD_MAX - 1;
    return 1;
}

int tes3x_console_text_begin(const char *initial)
{
    return text_begin(0, initial);
}

/* As tes3x_console_text_begin, on another menu: the console's exists only once a game is
 * loaded. */
int tes3x_console_text_begin_on(void *menu, const char *initial)
{
    return text_begin(menu, initial);
}

/* The text typed so far, while a client's keyboard is up. */
const char *tes3x_console_text_now(void)
{
    return vk_text_client == 1 ? cmd : 0;
}

/* The keyboard's own line of text, while a client's keyboard is up; 0 before it shows. */
void *tes3x_console_text_field(void)
{
    void *vk = ((fn_find_menu)TES3X_FIND_MENU)(*(unsigned short *)TES3X_VK_MENU_ID);

    return vk_text_client == 1 && vk_seen && vk ? keyboard_field(vk) : 0;
}

int tes3x_console_text_poll(char *out, u32 size)
{
    u32 i = 0;
    int status;
    if (vk_text_client == 1)
        watch_keyboard(0);
    if (vk_text_client == 1)
        return 0;
    status = vk_text_client;
    if (status != 2 && status != -1)
        return -2;
    if (size) {
        while (cmd[i] && i + 1 < size) {
            out[i] = cmd[i];
            i++;
        }
        out[i] = 0;
    }
    vk_text_client = 0;
    vk_owner = 0;
    cmd[0] = 0;
    return status;
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
        if (!exec_len)
            exec_load();
    }

    if (!combo_ready)
        load_combo();


    in = 0;
    if (base) {
        port = *(int *)(base + CTRL_PORT);
        if (port >= 0 && port <= 3)
            in = (short *)(base + port * PORT_STRIDE + INPUT_BASE);
    }

    /* Cache text because the keyboard has no delivery path for the console. */
    if (vk_watch)
        watch_keyboard(in);

    if (run_delay && !--run_delay && cmd[0]) {
        tes3x_log("console.run_begin", 0);
        run_command(cmd);
        tes3x_log("console.run_done", 0);
        cmd[0] = 0;
    }

    if (exec_len && !vk_watch && !run_delay)
        exec_step();

    if (tes3x_mailbox.seq != tes3x_mailbox.done && !vk_watch && !run_delay)
        mailbox_step();

    if (!in) {
        if (!base)
            tes3x_log("console.no_ctrl", 0);
        return 0;
    }

    if (console_open && console_layout)
        layout_console();

    /* The A that raised the keyboard stays down into its first frames; the keys must not see it. */
    if (held_a) {
        if (in[KEY_A])
            in[KEY_A] = 0;
        else
            held_a = 0;
    }

    /* After Done, A is still down; a raise needs a fresh press. */
    if (console_open && !vk_watch && !run_delay && in[KEY_A] && !held_raise) {
        in[KEY_A] = 0;
        held_a = 1;
        raise_keyboard();
    }
    held_raise = in[KEY_A] != 0 || held_a;

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
    console_layout = console_open;
    if (!console_open) {
        vk_watch = 0;
        tes3x_vk_limit = VK_TEXT_LIMIT;
    }
    tes3x_log("console.toggle", (u32)console_open);
#ifdef TES3X_DIAGNOSTICS
    tes3x_diag_note(TES3X_DIAG_NOTE_CONSOLE, (u32)console_open);
#endif
    return 1;
}

/* Stand in for `cmp dword [esp+N], 0x1F` in the keyboard's key and space handlers. The call and
 * the saved eax move the operand 8 bytes further up; the flags survive pop and ret. */
__attribute__((naked)) void tes3x_vk_key_limit(void)
{
    __asm__ volatile(
        "pushl %eax\n\t"
        "movl _tes3x_vk_limit, %eax\n\t"
        "cmpl %eax, 0x2C(%esp)\n\t"
        "popl %eax\n\t"
        "ret\n\t");
}

__attribute__((naked)) void tes3x_vk_space_limit(void)
{
    __asm__ volatile(
        "pushl %eax\n\t"
        "movl _tes3x_vk_limit, %eax\n\t"
        "cmpl %eax, 0x34(%esp)\n\t"
        "popl %eax\n\t"
        "ret\n\t");
}
