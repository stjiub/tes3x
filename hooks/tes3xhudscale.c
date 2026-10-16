/* Scale fixed HUD art after MenuMulti builds it, before its first layout. */

#include "tes3xini.h"

#ifndef TES3X_HUD_BUILD
#error "define TES3X_HUD_BUILD to BuildMenuMultiElements"
#endif
#ifndef TES3X_HUD_WORLD
#error "define TES3X_HUD_WORLD to WorldController::instance"
#endif
#ifndef TES3X_HUD_MENU_AREA
#error "define TES3X_HUD_MENU_AREA to ui::menuAreaWidth"
#endif
#ifndef TES3X_OPTIONS_LAYOUT
#error "define TES3X_OPTIONS_LAYOUT to ui::performLayout"
#endif
#ifndef TES3X_LEVEL_LAYOUT
#error "define TES3X_LEVEL_LAYOUT to ui::performLayout"
#endif
#ifndef TES3X_MESSAGE_LAYOUT
#error "define TES3X_MESSAGE_LAYOUT to ui::performLayout"
#endif
#ifndef TES3X_NAME_LAYOUT
#error "define TES3X_NAME_LAYOUT to ui::performLayout"
#endif
#ifndef TES3X_CHARGEN_LAYOUT
#error "define TES3X_CHARGEN_LAYOUT to ui::performLayout"
#endif

#define CHILD_BEGIN 0x28
#define CHILD_END 0x2C
#define UI_DIRTY 0x81
#define UI_WIDTH 0xF4
#define UI_HEIGHT 0xF8
#define UI_MAX_WIDTH 0x10C

static unsigned scale_decimal(const char *text)
{
    unsigned whole = 0, fraction = 0, places = 0;

    if (*text != '0' && *text != '1')
        return 1000;
    whole = (unsigned)(*text++ - '0');
    if (*text == '.') {
        text++;
        while (*text >= '0' && *text <= '9' && places < 3) {
            fraction = fraction * 10 + (unsigned)(*text++ - '0');
            places++;
        }
        if (*text >= '0' && *text <= '9')
            return 1000;
    }
    if (*text)
        return 1000;
    while (places++ < 3)
        fraction *= 10;
    fraction += whole * 1000;
    return fraction > 0 && fraction <= 1000 ? fraction : 1000;
}

static u8 **children(u8 *element, unsigned *count)
{
    u8 **begin = *(u8 ***)(element + CHILD_BEGIN);
    u8 **end = *(u8 ***)(element + CHILD_END);

    if ((u32)end < (u32)begin || ((u32)end - (u32)begin) > 64 * sizeof(void *) ||
        ((u32)end - (u32)begin) % sizeof(void *)) {
        *count = 0;
        return 0;
    }
    *count = (unsigned)(end - begin);
    return begin;
}

static int fixed_art(int width, int height)
{
    return (width == 65 && (height == 65 || height == 12)) ||
           (width == 36 && (height == 36 || height == 6)) ||
           (width == 32 && height == 32);
}

static int scaled(int value, unsigned factor)
{
    int result = (int)(((unsigned)value * factor + 500) / 1000);
    return result < 1 ? 1 : result;
}

static int scaled_signed(int value, unsigned factor)
{
    if (!value)
        return 0;
    return value < 0 ? -scaled(-value, factor) : scaled(value, factor);
}

static void scale_factors(unsigned *x_factor, unsigned *y_factor)
{
    u8 *world;
    char value[24];

    tes3x_ini_xbox("HudScale", "1", value, sizeof(value));
    *y_factor = scale_decimal(value);
    *x_factor = *y_factor;
    tes3x_ini_xbox("HudWidescreen480", "0", value, sizeof(value));
    world = *(u8 **)TES3X_HUD_WORLD;
    if (value[0] == '1' && value[1] == 0 && world &&
        *(int *)(world + 0x74) == 640 && *(int *)(world + 0x78) == 480)
        *x_factor = *y_factor * 3 / 4;
}

static unsigned scale_branch(u8 *element, unsigned x_factor, unsigned y_factor, unsigned depth)
{
    u8 **items;
    unsigned count, i, changed = 0;
    int width, height;

    if (!element || depth > 10)
        return 0;
    width = *(int *)(element + UI_WIDTH);
    height = *(int *)(element + UI_HEIGHT);
    if (fixed_art(width, height)) {
        *(int *)(element + UI_WIDTH) = scaled(width, x_factor);
        *(int *)(element + UI_HEIGHT) = scaled(height, y_factor);
        element[UI_DIRTY] = 1;
        changed++;
    }
    items = children(element, &count);
    for (i = 0; i < count; i++)
        changed += scale_branch(items[i], x_factor, y_factor, depth + 1);
    return changed;
}

static unsigned scale_help_row(u8 *group, unsigned factor)
{
    u8 **container, **rows;
    unsigned count;

    if (factor == 1000)
        return 0;
    container = children(group, &count);
    if (count != 1)
        return 0;
    rows = children(container[0], &count);
    if (count != 5 || *(int *)(group + UI_HEIGHT) < 16 ||
        *(int *)(group + UI_HEIGHT) > 30 ||
        *(int *)(rows[1] + UI_HEIGHT) != 16 ||
        *(int *)(rows[3] + UI_HEIGHT) != 16)
        return 0;
    *(int *)(rows[1] + UI_HEIGHT) = scaled(16, factor);
    *(int *)(rows[3] + UI_HEIGHT) = scaled(16, factor);
    rows[1][UI_DIRTY] = rows[3][UI_DIRTY] = 1;
    return 2;
}

static unsigned scale_options_buttons(u8 *element, unsigned x_factor, unsigned y_factor,
                                      unsigned depth)
{
    u8 **items;
    unsigned count, i, changed = 0;
    int width, height;

    if (!element || depth > 8)
        return 0;
    width = *(int *)(element + UI_WIDTH);
    height = *(int *)(element + UI_HEIGHT);
    if ((width == 120 || width == 248) && height == 50) {
        *(int *)(element + UI_WIDTH) = scaled(width, x_factor);
        *(int *)(element + UI_HEIGHT) = scaled(height, y_factor);
        element[UI_DIRTY] = 1;
        changed++;
    }
    items = children(element, &count);
    for (i = 0; i < count; i++)
        changed += scale_options_buttons(items[i], x_factor, y_factor, depth + 1);
    return changed;
}

static unsigned scale_levelup_content(u8 *element, unsigned x_factor, unsigned y_factor,
                                      unsigned depth)
{
    u8 **items;
    unsigned count, i, changed = 0;
    int width, height;

    if (!element || depth > 9)
        return 0;
    width = *(int *)(element + UI_WIDTH);
    height = *(int *)(element + UI_HEIGHT);
    if (width == 400) {
        *(int *)(element + UI_WIDTH) = scaled(width, x_factor);
        if (*(int *)(element + UI_MAX_WIDTH) == 400)
            *(int *)(element + UI_MAX_WIDTH) = scaled(width, x_factor);
        element[UI_DIRTY] = 1;
        changed++;
    } else if (width == 256 && height == 128) {
        *(int *)(element + UI_WIDTH) = scaled(width, x_factor);
        *(int *)(element + UI_HEIGHT) = scaled(height, y_factor);
        element[UI_DIRTY] = 1;
        changed++;
    }
    items = children(element, &count);
    for (i = 0; i < count; i++)
        changed += scale_levelup_content(items[i], x_factor, y_factor, depth + 1);
    return changed;
}

static unsigned scale_message_width(u8 *element, unsigned x_factor, unsigned depth)
{
    u8 **items;
    unsigned count, i, changed = 0;

    if (!element || depth > 8)
        return 0;
    if (*(int *)(element + UI_MAX_WIDTH) == 400) {
        *(int *)(element + UI_MAX_WIDTH) = scaled(400, x_factor);
        element[UI_DIRTY] = 1;
        changed++;
    }
    items = children(element, &count);
    for (i = 0; i < count; i++)
        changed += scale_message_width(items[i], x_factor, depth + 1);
    return changed;
}

static unsigned scale_name_tree(u8 *element, unsigned x_factor, unsigned y_factor,
                                unsigned depth)
{
    static const unsigned offsets[] = {0xD4, 0xD8, 0xDC, 0xE0,
                                       0xE4, 0xE8, UI_WIDTH, UI_HEIGHT,
                                       0x104, 0x108, UI_MAX_WIDTH, 0x110};
    u8 **items;
    unsigned count, i, changed = 1;
    int value;

    if (!element || depth > 8)
        return 0;
    for (i = 0; i < sizeof(offsets) / sizeof(offsets[0]); i++) {
        value = *(int *)(element + offsets[i]);
        if ((i == 4 || i == 5) || (value > 0 && value < 4096))
            *(int *)(element + offsets[i]) =
                i == 0 || i == 1 || i == 4 || i == 6 || i == 8 || i == 10
                    ? scaled_signed(value, x_factor) : scaled_signed(value, y_factor);
    }
    element[UI_DIRTY] = 1;
    items = children(element, &count);
    for (i = 0; i < count; i++)
        changed += scale_name_tree(items[i], x_factor, y_factor, depth + 1);
    return changed;
}

void __stdcall tes3x_name_scale(u8 *menu)
{
    unsigned x_factor, y_factor, changed;
    int width, height, min_width;

    scale_factors(&x_factor, &y_factor);
    if ((x_factor == 1000 && y_factor == 1000) ||
        *(int *)(menu + UI_WIDTH) != 400 || *(int *)(menu + UI_HEIGHT) != 225)
        return;
    changed = scale_name_tree(menu, x_factor, y_factor, 0);
    width = scaled(400, x_factor);
    min_width = 100 + 10 * scaled(30, x_factor);
    if (width < min_width)
        width = min_width;
    height = scaled(225, y_factor) + (int)((1000 - y_factor) * 50 + 500) / 1000;
    *(int *)(menu + UI_WIDTH) = width;
    *(int *)(menu + UI_HEIGHT) = height;
    *(int *)(menu + 0xE4) = -(width / 2);
    *(int *)(menu + 0xE8) = (height + 1) / 2;
    tes3x_log_hex3("ui.name", (unsigned)width, (unsigned)height, changed);
}

void __stdcall tes3x_chargen_scale(u8 *menu)
{
    unsigned x_factor, y_factor, changed;
    int width, height;
    const char *tag;

    scale_factors(&x_factor, &y_factor);
    if (x_factor == 1000 && y_factor == 1000)
        return;
    width = *(int *)(menu + UI_WIDTH);
    height = *(int *)(menu + UI_HEIGHT);
    if (width == 390 && height == 340)
        tag = "ui.race";
    else if (width >= 250 && width <= 420 && height == 80)
        tag = "ui.class";
    else if (width == 485 && height == 355)
        tag = "ui.birth";
    else
        return;
    changed = scale_name_tree(menu, x_factor, y_factor, 0);
    tes3x_log_hex3(tag, x_factor, y_factor, changed);
}

void __stdcall tes3x_message_scale(u8 *menu)
{
    unsigned x_factor, y_factor, changed;

    scale_factors(&x_factor, &y_factor);
    if (x_factor == 1000)
        return;
    changed = scale_message_width(menu, x_factor, 0);
    if (changed)
        tes3x_log_hex3("ui.message", x_factor, y_factor, changed);
}

void __stdcall tes3x_levelup_scale(u8 *menu)
{
    unsigned x_factor, y_factor, changed;

    scale_factors(&x_factor, &y_factor);
    if (x_factor == 1000 && y_factor == 1000)
        return;
    changed = scale_levelup_content(menu, x_factor, y_factor, 0);
    if (changed)
        tes3x_log_hex3("ui.levelup", x_factor, y_factor, changed);
}

void __stdcall tes3x_options_scale(u8 *menu)
{
    unsigned x_factor, y_factor, changed;
    int width, height;

    scale_factors(&x_factor, &y_factor);
    if (x_factor == 1000 && y_factor == 1000)
        return;
    width = *(int *)(menu + UI_WIDTH);
    height = *(int *)(menu + UI_HEIGHT);
    if ((width != 120 && width != 248) || (height != 250 && height != 300))
        return;
    changed = scale_options_buttons(menu, x_factor, y_factor, 0);
    if (!changed)
        return;
    *(int *)(menu + UI_WIDTH) = scaled(width, x_factor);
    *(int *)(menu + UI_HEIGHT) = scaled(height, y_factor);
    menu[UI_DIRTY] = 1;
    tes3x_log_hex3("ui.options", x_factor, y_factor, changed);
}

void __stdcall tes3x_hud_scale(u8 *menu)
{
    u8 **root_children, **groups, *part;
    unsigned root_count, group_count, factor, x_factor, changed;

    scale_factors(&x_factor, &factor);
    if (factor == 1000 && x_factor == 1000)
        return;

    root_children = children(menu, &root_count);
    if (root_count != 2)
        return;
    part = root_children[1];
    groups = children(part, &group_count);
    if (group_count != 5)
        return;
    changed = scale_help_row(groups[0], factor);
    changed += scale_branch(groups[1], x_factor, factor, 0);
    changed += scale_branch(groups[3], x_factor, factor, 0);
    tes3x_log_hex3("hud.scale", x_factor, factor, changed);
}

__attribute__((used)) void tes3x_menu_area_scale(void)
{
    int *area = (int *)TES3X_HUD_MENU_AREA;
    unsigned x_factor, y_factor;
    int width = area[0], height = area[1], sw, sh;

    scale_factors(&x_factor, &y_factor);
    if ((x_factor == 1000 && y_factor == 1000) ||
        width < 1 || height < 1 || width > 4096 || height > 4096)
        return;
    sw = scaled(width, x_factor);
    sh = scaled(height, y_factor);
    area[2] += (width - sw) / 2;
    area[3] -= (height - sh) / 2;
    area[0] = sw;
    area[1] = sh;
    tes3x_log_hex3("ui.area", (unsigned)sw, (unsigned)sh, x_factor);
}

/* Replaces EnsureMenuMulti's epilogue after it refreshes the menu-area cache. */
__attribute__((naked)) void tes3x_menu_area_scale_hook(void)
{
    __asm__ volatile(
        "pushal\n\t"
        "call _tes3x_menu_area_scale\n\t"
        "popal\n\t"
        "popl %edi\n\t"
        "popl %esi\n\t"
        "popl %ebx\n\t"
        "popl %ecx\n\t"
        "ret\n\t");
}

/* Replaces InitializeMenuMulti's one call to BuildMenuMultiElements. */
__attribute__((naked)) void tes3x_hud_scale_hook(void)
{
    __asm__ volatile(
        "pushl 4(%esp)\n\t"
        "movl $" TES3X_SYM(HUD_BUILD) ", %eax\n\t"
        "call *%eax\n\t"
        "addl $4, %esp\n\t"
        "pushal\n\t"
        "pushl 36(%esp)\n\t"
        "call _tes3x_hud_scale@4\n\t"
        "popal\n\t"
        "ret\n\t");
}

/* Scale the options tree before its first layout and hit-region update. */
__attribute__((naked)) void tes3x_options_scale_hook(void)
{
    __asm__ volatile(
        "pushal\n\t"
        "pushl 24(%esp)\n\t"
        "call _tes3x_options_scale@4\n\t"
        "popal\n\t"
        "movl $" TES3X_SYM(OPTIONS_LAYOUT) ", %eax\n\t"
        "jmp *%eax\n\t");
}

/* Scale level-up's fixed content before its two normal layout passes. */
__attribute__((naked)) void tes3x_levelup_scale_hook(void)
{
    __asm__ volatile(
        "pushal\n\t"
        "pushl 24(%esp)\n\t"
        "call _tes3x_levelup_scale@4\n\t"
        "popal\n\t"
        "movl $" TES3X_SYM(LEVEL_LAYOUT) ", %eax\n\t"
        "jmp *%eax\n\t");
}

/* Apply the message width cap before text wrapping and hit-region layout. */
__attribute__((naked)) void tes3x_message_scale_hook(void)
{
    __asm__ volatile(
        "pushal\n\t"
        "pushl 24(%esp)\n\t"
        "call _tes3x_message_scale@4\n\t"
        "popal\n\t"
        "movl $" TES3X_SYM(MESSAGE_LAYOUT) ", %eax\n\t"
        "jmp *%eax\n\t");
}

/* Name entry has fixed keys and a fixed gap; resize its bounds before layout. */
__attribute__((naked)) void tes3x_name_scale_hook(void)
{
    __asm__ volatile(
        "pushal\n\t"
        "pushl %esi\n\t"
        "call _tes3x_name_scale@4\n\t"
        "popal\n\t"
        "movl $" TES3X_SYM(NAME_LAYOUT) ", %eax\n\t"
        "jmp *%eax\n\t");
}

__attribute__((naked)) void tes3x_chargen_scale_hook(void)
{
    __asm__ volatile(
        "pushal\n\t"
        "pushl 24(%esp)\n\t"
        "call _tes3x_chargen_scale@4\n\t"
        "popal\n\t"
        "movl $" TES3X_SYM(CHARGEN_LAYOUT) ", %eax\n\t"
        "jmp *%eax\n\t");
}
