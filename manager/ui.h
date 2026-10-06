#ifndef UI_H
#define UI_H

/* Widgets drawn through a theme: the plain one built in, and later a skin from the retail
 * base's menu art. */

#include "gfx.h"

#include <stddef.h>

struct theme {
    gfx_color text, dim, accent, bright, bad, line;
    const struct font *body, *bold, *small, *title;
    void (*backdrop)(void);
    void (*panel)(int x, int y, int w, int h);
    void (*select)(int x, int y, int w, int h);
};

extern const struct theme ui_plain;
extern const struct theme *ui;

/* Inside a TV's title-safe area. */
#define UI_LEFT 40
#define UI_RIGHT 600
#define UI_WIDTH (UI_RIGHT - UI_LEFT)
#define UI_HEADER_RULE 60
#define UI_PANEL_TOP 68
#define UI_PANEL_BOTTOM 428
#define UI_PAD 16

enum ui_button { UI_A, UI_B, UI_X, UI_Y, UI_START, UI_L, UI_R };

struct ui_hint {
    int button;
    const char *label;
};

struct ui_list {
    int count, sel, top, rows;
};

/* "512 KB", "962 MB", "2.1 GB" */
void ui_size(char *out, size_t n, unsigned long long bytes);
int ui_button(int button, int x, int y);
/* The logo and the tabs. */
void ui_header(const char *const *names, int n, int active);
void ui_panel(void);
void ui_footer(const struct ui_hint *hints, int n, const char *status);
void ui_list_move(struct ui_list *l, int delta);
void ui_scrollbar(int x, int y, int h, const struct ui_list *l);
/* A box over the dimmed screen: wrapped lines, then extra pixels left free for the caller,
 * then the hints. Returns the y of the free space. */
int ui_dialog(const char *title, const char *const *lines, int n, int extra,
              const struct ui_hint *hints, int nh);
/* A bar when the total is known, else the spinner. */
void ui_progress(const char *what, unsigned long long done, unsigned long long total);
/* Dots around a ring with a bright head that moves each ui_tick, so a wait never looks frozen;
 * main.c sets ui_tick from the clock before each draw. */
extern unsigned ui_tick;
void ui_spinner(int cx, int cy);

#endif
