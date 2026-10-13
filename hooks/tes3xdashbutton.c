/* A "TES3X" button in the engine's menu, main and pause alike, that leaves for the Dash.
 *
 * The menu is built by one function; this runs right after it, adds a button made the way the
 * engine makes its own (a block of three images) above Options, and grows the menu by a row.
 * The Dash's path is [Xbox] Dash, which installing the Dash writes; without it no button is made.
 */

#include "tes3x_thunks.h"
#include "tes3xdashbutton.h"
#include "tes3xini.h"
#include "tes3xlaunch.h"
#include "tes3xlog.h"

#ifndef TES3X_DASHBTN_BUILD
#error "define TES3X_DASHBTN_BUILD to MenuOptions::build"
#endif
#define TES3X_STR_(x) #x
#define TES3X_STR(x) TES3X_STR_(x)

#define UI_TRUE 0xFFFF80BDu
#define UI_FLAG_B 0xFFFF800Bu
#define UI_FOCUS_A 0xFFFF8048u
#define UI_FOCUS_B 0xFFFF80A8u
#define UI_CLICK 0xFFFF8035u
#define UI_PRESS 0xFFFF8034u
#define UI_OVER 0xFFFF8033u
#define UI_LEAVE 0xFFFF8032u
#define UI_PROP_INT 1
#define UI_PROP_PTR 8
#define UI_PROP_ENUM 0x10
#define UI_PROP_HANDLER 0x20
#define UI_PARENT 0x34
#define UI_CHILDREN 0x28 /* vector: begin, then end at +4 */
#define UI_WIDTH 0xF4
#define UI_HEIGHT 0xF8
#define UI_IMAGE_FLAG 0x87
#define MENU_ROW 0x32 /* a main menu button's height */
#define UI_ALIGN_Y 0x12C
#define ALIGN_MAIN_MIN 0x3F19999Au /* 0.6f; positive floats order as integers */
#define ALIGN_WITH_ROW 0x3F60A3D7u /* 0.88f */
#define DASH_PATH 128

typedef void *(__attribute__((thiscall)) *fn_find_child)(void *widget, u32 id);
typedef void *(__attribute__((thiscall)) *fn_find_menu)(u32 id);
typedef void *(__attribute__((thiscall)) *fn_create_block)(void *parent, u32 id, int a0);
typedef void *(__attribute__((thiscall)) *fn_create_image)(void *parent, u32 id, const char *path,
                                                           int a0);
typedef void(__attribute__((thiscall)) *fn_set_size)(void *widget, int value);
typedef void(__attribute__((thiscall)) *fn_set_visible)(void *widget, int on);
typedef void(__attribute__((thiscall)) *fn_set_prop)(void *widget, u32 id, int value, int type);
typedef void(__attribute__((thiscall)) *fn_layout)(void *widget, int a0);
typedef u32(__cdecl *fn_ui_id)(const char *name);
typedef char(__cdecl *fn_ui_handler)(void *owner, u32 id, int d0, int d1, void *source);

void (*tes3x_dash_closing)(void);
u32 tes3x_dash_added; /* this menu has the button, so its height wants a row more */

static const char *const states[3] = {"TES3X_normal", "TES3X_over", "TES3X_pressed"};
static char dash_path[DASH_PATH];
static int dash_ready;
static int launching;

static int plausible(const void *p)
{
    return (u32)p >= 0x10000u && (u32)p < 0x80000000u && !((u32)p & 3);
}

static u8 *part(u8 *menu, const char *name)
{
    return ((fn_find_child)TES3X_DASHBTN_FIND_CHILD)(menu, ((fn_ui_id)TES3X_DASHBTN_UI_ID)(name));
}

static char *put_text(char *dst, const char *src)
{
    while (*src)
        *dst++ = *src++;
    return dst;
}

/* Show one of a button's three images and lay out its menu, as the engine's handlers do. */
static void show(u8 *button, u32 which)
{
    fn_ui_id ui_id = (fn_ui_id)TES3X_DASHBTN_UI_ID;
    u8 *image, *root = button, *up;
    u32 i;

    if (!plausible(button))
        return;
    for (i = 0; i < 3; i++)
        if (plausible(image = ((fn_find_child)TES3X_DASHBTN_FIND_CHILD)(button, ui_id(states[i]))))
            ((fn_set_visible)TES3X_DASHBTN_SET_VISIBLE)(image, i == which);
    while (plausible(up = *(u8 **)(root + UI_PARENT)) && plausible(*(u8 **)(up + UI_PARENT)))
        root = up;
    ((fn_layout)TES3X_DASHBTN_PERFORM_LAYOUT)(root, 1);
}

/* A handler is given the menu as owner and the widget hit as source, which may be an image in
 * the button: the widget holding a part named TES3X_pressed. */
static u8 *button_of(void *source)
{
    u32 id = ((fn_ui_id)TES3X_DASHBTN_UI_ID)("TES3X_pressed"), depth;
    u8 *el = source, *found;

    for (depth = 0; plausible(el) && depth < 4; depth++, el = *(u8 **)(el + UI_PARENT))
        if (plausible(found = ((fn_find_child)TES3X_DASHBTN_FIND_CHILD)(el, id)) && found != el)
            return el;
    return 0;
}

static char __cdecl button_pressed(void *owner, u32 id, int d0, int d1, void *source)
{
    (void)owner, (void)id, (void)d0, (void)d1;
    show(button_of(source), 2);
    return 1;
}

static char __cdecl button_over(void *owner, u32 id, int d0, int d1, void *source)
{
    (void)owner, (void)id, (void)d0, (void)d1;
    show(button_of(source), 1);
    return 1;
}

static char __cdecl button_left(void *owner, u32 id, int d0, int d1, void *source)
{
    (void)owner, (void)id, (void)d0, (void)d1;
    show(button_of(source), 0);
    return 1;
}

static char __cdecl dash_click(void *owner, u32 id, int d0, int d1, void *source)
{
    (void)owner, (void)id, (void)d0, (void)d1, (void)source;
    if (launching)
        return 1;
    launching = 1;
    tes3x_log("dashbutton.launch", 0);
    if (tes3x_dash_closing)
        tes3x_dash_closing();
    tes3x_launch(dash_path);
    launching = 0;
    return 1;
}

/* A button as the main menu builds its own: a block holding Textures\NAME.tga, NAME_over and
 * NAME_pressed, the last two hidden until the pad or the pointer reaches it. */
static u8 *menu_button(u8 *parent, const char *name, int width)
{
    static const char *const suffix[3] = {"", "_over", "_pressed"};
    fn_set_prop set = (fn_set_prop)TES3X_DASHBTN_SET_PROP;
    fn_ui_id ui_id = (fn_ui_id)TES3X_DASHBTN_UI_ID;
    char path[64];
    u8 *block, *image;
    u32 i;

    if (!plausible(block = ((fn_create_block)TES3X_DASHBTN_CREATE_BLOCK)(parent,
                                                                       ui_id("TES3X_Dash"), 0)))
        return 0;
    ((fn_set_size)TES3X_DASHBTN_SET_WIDTH)(block, width);
    ((fn_set_size)TES3X_DASHBTN_SET_HEIGHT)(block, MENU_ROW);
    set(block, UI_FLAG_B, 0, UI_PROP_INT);
    set(block, UI_FOCUS_A, (int)UI_TRUE, UI_PROP_ENUM);
    set(block, UI_FOCUS_B, (int)UI_TRUE, UI_PROP_ENUM);
    set(block, UI_CLICK, (int)dash_click, UI_PROP_HANDLER);
    set(block, UI_PRESS, (int)button_pressed, UI_PROP_HANDLER);
    set(block, UI_OVER, (int)button_over, UI_PROP_HANDLER);
    set(block, UI_LEAVE, (int)button_left, UI_PROP_HANDLER);
    for (i = 0; i < 3; i++) {
        *put_text(put_text(put_text(put_text(path, "Textures\\"), name), suffix[i]), ".tga") = 0;
        if (!plausible(image = ((fn_create_image)TES3X_DASHBTN_CREATE_IMAGE)(
                           block, ui_id(states[i]), path, 0)))
            continue;
        ((fn_set_size)TES3X_DASHBTN_SET_WIDTH)(image, width);
        ((fn_set_size)TES3X_DASHBTN_SET_HEIGHT)(image, MENU_ROW);
        image[UI_IMAGE_FLAG] = 0;
        set(image, UI_FLAG_B, 0, UI_PROP_INT);
        if (i)
            ((fn_set_visible)TES3X_DASHBTN_SET_VISIBLE)(image, 0);
    }
    return block;
}

/* A block added last to its column's children moves up to sit above `below`. */
static void column_before(u8 *column, u8 *block, u8 *below)
{
    u8 **begin = *(u8 ***)(column + UI_CHILDREN), **end = *(u8 ***)(column + UI_CHILDREN + 4), **at;

    if (!plausible(begin) || end <= begin || end[-1] != block)
        return;
    for (at = end - 1; at > begin && at[-1] != below; at--)
        ;
    if (at == begin)
        return;
    for (at = end - 1; at[-1] != below; at--)
        at[0] = at[-1];
    at[0] = at[-1];
    at[-1] = block;
}

/* The sibling just above `block` in its column, which a pad's Up reaches the button from. */
static u8 *child_before(u8 *column, u8 *block)
{
    u8 **begin = *(u8 ***)(column + UI_CHILDREN), **end = *(u8 ***)(column + UI_CHILDREN + 4), **at;

    if (!plausible(begin) || end <= begin)
        return 0;
    for (at = begin + 1; at < end; at++)
        if (*at == block)
            return at[-1];
    return 0;
}

void __stdcall tes3x_dashbutton_add(u8 *menu)
{
    fn_set_prop set = (fn_set_prop)TES3X_DASHBTN_SET_PROP;
    unsigned short up = *(const unsigned short *)TES3X_DASHBTN_NAV_UP_ID, down = *(const unsigned short *)TES3X_DASHBTN_NAV_DOWN_ID;
    u8 *exit, *options, *column, *button, *above, *before, *inner;

    if (!dash_ready) {
        dash_ready = 1;
        tes3x_ini_xbox("Dash", "", dash_path, sizeof(dash_path));
        tes3x_log("dashbutton.path", dash_path[0] != 0);
    }
    tes3x_dash_added = 0;
    if (!dash_path[0] || !plausible(menu))
        return;
    exit = part(menu, "MenuOptions_Exit_container");
    options = part(menu, "MenuOptions_Options_container");
    if (!plausible(exit) || !plausible(column = *(u8 **)(exit + UI_PARENT)))
        return;
    if (!plausible(button = menu_button(column, "menu_tes3x", *(const int *)(exit + UI_WIDTH))))
        return;
    /* the engine sets the height on the menu found by name, which is not the builder's argument */
    inner = ((fn_find_menu)TES3X_DASHBTN_FIND_MENU)(((fn_ui_id)TES3X_DASHBTN_UI_ID)("MenuOptions"));
    if (!plausible(inner))
        inner = menu;
    /* above Options, which a main menu has; the pause menu's rows differ, so above Exit then */
    above = plausible(options) ? options : exit;
    column_before(column, button, above);
    set(button, down, (int)above, UI_PROP_PTR);
    set(above, up, (int)button, UI_PROP_PTR);
    if (plausible(before = child_before(column, button))) {
        set(button, up, (int)before, UI_PROP_PTR);
        set(before, down, (int)button, UI_PROP_PTR);
    }
    tes3x_dash_added = 1;
    /* the main menu sits low on the screen (0.95); a row more would run off the bottom */
    if (*(const u32 *)(inner + UI_ALIGN_Y) > ALIGN_MAIN_MIN)
        *(u32 *)(inner + UI_ALIGN_Y) = ALIGN_WITH_ROW;
    tes3x_log("dashbutton.added", (u32)button);
}

/* Replaces MenuOptions::open's call that sets the menu's height once built (thiscall, the height
 * on the stack): a row more when the button was added. */
__attribute__((naked)) void tes3x_dashbutton_height_hook(void)
{
    __asm__ volatile(
        "cmpl $0, _tes3x_dash_added\n\t"
        "je 1f\n\t"
        "addl $" TES3X_STR(MENU_ROW) ", 4(%esp)\n"
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
