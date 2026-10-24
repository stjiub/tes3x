/* A hint line at the foot of the engine's menu, main and pause alike: a pad button's icon and what
 * it does, in the gold of the server list's text. Here it offers "Dash" on White; the multiplayer
 * layer shows its own buttons through tes3x_hint_show.
 *
 * The menu is built by one function; this runs right after it, adds the line below the last
 * button, made the way the engine makes its own, and grows the menu by its row. The Dash's path is
 * [Xbox] Dash, which installing the Dash writes; without it, and without multiplayer, no line is
 * made.
 */

#include "tes3x_thunks.h"
#include "tes3xini.h"
#include "tes3xlaunch.h"
#include "tes3xlog.h"
#include "tes3xdashbutton.h"

#ifndef TES3X_DASHBTN_BUILD
#error "define TES3X_DASHBTN_BUILD to MenuOptions::build"
#endif
#define TES3X_STR_(x) #x
#define TES3X_STR(x) TES3X_STR_(x)

#define UI_PARENT 0x34
#define UI_WIDTH 0xF4
#define UI_COLOUR 0x154 /* red, green, blue, alpha; MWSE's PC Element + 8 */
#define UI_COLOUR_CHANGED 0x7F /* flagColourChanged, likewise */
#define UI_FONT 0x164   /* 0 the small Century Gothic, 1 the big one */
#define UI_IMAGE_FLAG 0x87
#define UI_CHILD_ALIGN_Y 0xFFFF8055u
#define UI_PROP_FLOAT 2
#define UI_HALF 0x3F000000 /* 0.5f */
#define MENU_VISIBLE 0x7E
#define UI_ALIGN_Y 0x12C
#define ALIGN_MAIN_MIN 0x3F19999Au /* 0.6f; positive floats order as integers */
#define ALIGN_LOW 0x3F7C28F6u      /* 0.985f: the main menu sits at 0.95, the line below it */
#define HINT_ROW 0x1E   /* the line's height, added to the menu's */
#define HINT_SETTLE 10  /* frames the line's colour is set again after its text */
#define HINT_DELAY 20   /* frames the line stays empty after its menu is built */
#define HINT_ICON 24    /* a button icon's size */
#define HINT_WIDTH 300  /* the menu's width while the line shows two buttons */
#define PAD_BLACK 1u
#define PAD_WHITE 2u
#define DASH_PATH 128

typedef void *(__attribute__((thiscall)) *fn_find_child)(void *widget, u32 id);
typedef void *(__attribute__((thiscall)) *fn_find_menu)(u32 id);
typedef void *(__attribute__((thiscall)) *fn_create_label)(void *parent, u32 id, const char *text,
                                                           int black, int replace);
typedef void *(__attribute__((thiscall)) *fn_create_block)(void *parent, u32 id, int a0);
typedef void *(__attribute__((thiscall)) *fn_create_image)(void *parent, u32 id, const char *path,
                                                           int a0);
typedef void(__attribute__((thiscall)) *fn_set_size)(void *widget, int value);
typedef void(__attribute__((thiscall)) *fn_set_auto)(void *widget, int on);
typedef void(__attribute__((thiscall)) *fn_set_prop)(void *widget, u32 id, int value, int type);
typedef void(__attribute__((thiscall)) *fn_set_visible)(void *widget, int on);
typedef void(__attribute__((thiscall)) *fn_set_text)(void *widget, const char *text);
typedef void(__attribute__((thiscall)) *fn_layout)(void *widget, int a0);
typedef u32(__cdecl *fn_ui_id)(const char *name);

void (*tes3x_dash_closing)(void);
u32 tes3x_dash_added;  /* this menu has the line, so its height wants a row more */
u32 tes3x_hint_serial; /* counts the menus given a line, for text that must be set again */
extern u32 tes3x_multi_present __attribute__((weak));
extern void tes3x_still_refresh(void) __attribute__((weak));

static const char dash_hint[] = " Dash";
static const float gold[3] = {0.88f, 0.74f, 0.42f};
static char dash_path[DASH_PATH];
static int dash_ready;
static int launching;
static u32 pad_prev;

static int plausible(const void *p)
{
    return (u32)p >= 0x10000u && (u32)p < 0x80000000u && !((u32)p & 3);
}

static u8 *part(u8 *menu, const char *name)
{
    return ((fn_find_child)TES3X_DASHBTN_FIND_CHILD)(menu, ((fn_ui_id)TES3X_DASHBTN_UI_ID)(name));
}

/* Leave for the Dash. */
void tes3x_dashbutton_launch(void)
{
    if (launching || !dash_path[0])
        return;
    launching = 1;
    tes3x_log("dashbutton.launch", 0);
    if (tes3x_dash_closing)
        tes3x_dash_closing();
    tes3x_launch(dash_path);
    launching = 0;
}

/* Whether a Dash is installed to leave for. */
u32 tes3x_dashbutton_ready(void)
{
    if (!dash_ready) {
        dash_ready = 1;
        tes3x_ini_xbox("Dash", "", dash_path, sizeof(dash_path));
        tes3x_log("dashbutton.path", dash_path[0] != 0);
    }
    return dash_path[0] != 0;
}

/* The pad's buttons held, from the console's poll, less any the multiplayer layer used: White
 * leaves for the Dash while the engine's menu is up. */
void tes3x_dashbutton_pad(u32 level)
{
    u32 edge = level & ~pad_prev;
    u8 *menu;

    pad_prev = level;
    if (!tes3x_dashbutton_ready())
        return;
    menu = ((fn_find_menu)TES3X_DASHBTN_FIND_MENU)(((fn_ui_id)TES3X_DASHBTN_UI_ID)("MenuOptions"));
    if (!plausible(menu) || !menu[MENU_VISIBLE])
        return;
    if (!&tes3x_multi_present)
        tes3x_hint_show(menu, 0, dash_hint); /* with multiplayer, its frame shows the line */
    if (edge & PAD_WHITE)
        tes3x_dashbutton_launch();
}

/* The server list's gold; set again after the text, which resets it. */
static void gild(u8 *label)
{
    u32 i;

    for (i = 0; i < 12; i++)
        label[UI_COLOUR + i] = ((const u8 *)gold)[i];
    label[UI_COLOUR_CHANGED] = 1;
}

/* One of the line's two buttons: its icon and its text, which is empty to hide the pair. */
static void hint_pair(u8 *line, const char *icon_id, const char *text_id, const char *icon,
                      const char *text)
{
    fn_ui_id ui_id = (fn_ui_id)TES3X_DASHBTN_UI_ID;
    u8 *image = ((fn_create_image)TES3X_DASHBTN_CREATE_IMAGE)(line, ui_id(icon_id), icon, 0);
    u8 *label;
    u32 i;

    if (plausible(image)) {
        ((fn_set_size)TES3X_DASHBTN_SET_WIDTH)(image, HINT_ICON);
        ((fn_set_size)TES3X_DASHBTN_SET_HEIGHT)(image, HINT_ICON);
        image[UI_IMAGE_FLAG] = 0;
        ((fn_set_visible)TES3X_DASHBTN_SET_VISIBLE)(image, text[0] != 0);
    }
    label = ((fn_create_label)TES3X_DASHBTN_CREATE_LABEL)(line, ui_id(text_id), text, 0, 0);
    if (plausible(label)) {
        *(int *)(label + UI_FONT) = 0;
        gild(label);
    }
}

static void hint_text(u8 *menu, const char *icon_id, const char *text_id, const char *text)
{
    u8 *image = part(menu, icon_id), *label = part(menu, text_id);

    if (plausible(image))
        ((fn_set_visible)TES3X_DASHBTN_SET_VISIBLE)(image, text != 0);
    if (plausible(label)) {
        gild(label);
        ((fn_set_text)TES3X_DASHBTN_WIDGET_SET_TEXT)(label, text ? text : "");
        gild(label);
    }
}

/* Show the line's Black and White buttons with their text, or hide one with 0. The menu is widened
 * to hold both. Text is set again only for a new menu or new text. */
void tes3x_hint_show(u8 *menu, const char *black, const char *white)
{
    static u32 serial, settle, delay;
    static const char *shown_black, *shown_white;
    int wide = *(const int *)(menu + UI_WIDTH) < HINT_WIDTH && (black || white);
    u8 *label;
    u32 i;

    if (!plausible(part(menu, "TES3X_Hint")))
        return;
    if (wide)
        ((fn_set_size)TES3X_DASHBTN_SET_WIDTH)(menu, HINT_WIDTH);
    /* a label's text takes the label's colour only once the label has been drawn: leave it empty
     * for a while after the menu is built */
    if (serial != tes3x_hint_serial) {
        serial = tes3x_hint_serial;
        delay = HINT_DELAY;
        shown_black = shown_white = (const char *)1;
    }
    if (delay) {
        delay--;
        return;
    }
    if (shown_black != black || shown_white != white) {
        shown_black = black;
        shown_white = white;
        settle = HINT_SETTLE;
        hint_text(menu, "TES3X_HintBlack", "TES3X_HintBlackText", black);
        hint_text(menu, "TES3X_HintWhite", "TES3X_HintWhiteText", white);
        wide = 1;
        if (tes3x_still_refresh)
            tes3x_still_refresh();
    }
    /* the text's element is made by a layout, a frame or more after the text is set, and takes
     * the label's colour only if it is set again after that: a few frames later, then repaint */
    if (settle) {
        for (i = 0; i < 2; i++)
            if (plausible(label = part(menu, i ? "TES3X_HintWhiteText" : "TES3X_HintBlackText")))
                gild(label);
        if (!--settle) {
            wide = 1;
            if (tes3x_still_refresh)
                tes3x_still_refresh();
        }
    }
    if (wide)
        ((fn_layout)TES3X_DASHBTN_PERFORM_LAYOUT)(menu, 1);
}

void __stdcall tes3x_dashbutton_add(u8 *menu)
{
    u8 *exit, *column, *line;

    tes3x_dash_added = 0;
    if (!plausible(menu))
        return;
    if (!tes3x_dashbutton_ready() && !&tes3x_multi_present)
        return;
    exit = part(menu, "MenuOptions_Exit_container");
    if (!plausible(exit) || !plausible(column = *(u8 **)(exit + UI_PARENT)))
        return;
    if (!plausible(line = ((fn_create_block)TES3X_DASHBTN_CREATE_BLOCK)(
                       column, ((fn_ui_id)TES3X_DASHBTN_UI_ID)("TES3X_Hint"), 0)))
        return;
    ((fn_set_auto)TES3X_DASHBTN_SET_AUTO_WIDTH)(line, 1);
    ((fn_set_auto)TES3X_DASHBTN_SET_AUTO_HEIGHT)(line, 1);
    ((fn_set_prop)TES3X_DASHBTN_SET_PROP)(line, UI_CHILD_ALIGN_Y, UI_HALF, UI_PROP_FLOAT);
    hint_pair(line, "TES3X_HintBlack", "TES3X_HintBlackText", "Textures\\xbox_button_black.tga", "");
    hint_pair(line, "TES3X_HintWhite", "TES3X_HintWhiteText", "Textures\\xbox_button_white.tga", "");
    tes3x_dash_added = 1;
    tes3x_hint_serial++;
    tes3x_log("dashbutton.added", (u32)line);
}

/* Replaces MenuOptions::open's call that sets the menu's height once built (thiscall, the height
 * on the stack): the hint's row more when the line was added. */
__attribute__((naked)) void tes3x_dashbutton_height_hook(void)
{
    __asm__ volatile(
        "cmpl $0, _tes3x_dash_added\n\t"
        "je 1f\n\t"
        "cmpl $" TES3X_STR(ALIGN_MAIN_MIN) ", " TES3X_STR(UI_ALIGN_Y) "(%ecx)\n\t"
        "jbe 2f\n\t"
        "movl $" TES3X_STR(ALIGN_LOW) ", " TES3X_STR(UI_ALIGN_Y) "(%ecx)\n"
        "2:\n\t"
        "addl $" TES3X_STR(HINT_ROW) ", 4(%esp)\n"
        "1:\n\t"
        "pushl $" TES3X_SYM(DASHBTN_SET_HEIGHT) "\n\t"
        "ret\n\t");
}

/* Replaces MenuOptions::build's call in MenuOptions::open (cdecl, the menu on the stack). */
__attribute__((naked)) void tes3x_dashbutton_hook(void)
{
    __asm__ volatile(
        "pushl 4(%esp)\n\t"
        "movl $" TES3X_SYM(DASHBTN_BUILD) ", %eax\n\t"
        "call *%eax\n\t"
        "addl $4, %esp\n\t"
        "pushal\n\t"
        "pushl 36(%esp)\n\t"
        "call _tes3x_dashbutton_add@4\n\t"
        "popal\n\t"
        "ret\n\t");
}
