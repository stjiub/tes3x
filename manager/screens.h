#ifndef SCREENS_H
#define SCREENS_H

/* The manager's screens, drawn from a view main.c keeps; portable, so preview/ renders them
 * on the PC. */

#include "mgr.h"
#include "ui.h"

enum tab { TAB_BUILDS, TAB_SETTINGS, TAB_COUNT };
enum page { PAGE_MAIN, PAGE_DETAILS, PAGE_BASES };
enum setting { SET_BASE, SET_AGENT, SET_MANAGER, SET_COUNT };

#define LINE_HEAD 1
#define LINE_BAD 2
#define MAX_LINES 512

struct line {
    char label[24];
    char text[128];
    int style;
};

struct view {
    int tab, page;
    const struct build *builds;
    struct ui_list build_list;
    const struct line *lines; /* the details page */
    struct ui_list line_list;
    const struct base *bases;
    struct ui_list base_list;
    int base_current; /* index of OverlayBase among bases, or -1 */
    struct ui_list settings;
    const char *overlay_base, *agent, *slot;
    /* a dialog over the page, when title is set */
    char title[48];
    char text[4][128];
    int dismiss; /* B closes it */
};

/* Rows the lists show, for ui_list.rows. */
#define BUILD_ROWS 7
#define LINE_ROWS 12
#define BASE_ROWS 5

void screen_draw(const struct view *v);
/* The page with a dialog of text and no buttons over it, for work that cannot be cancelled. */
void screen_busy(const struct view *v, const char *text);
void screen_progress(const struct view *v, const char *what, unsigned long long done,
                     unsigned long long total);

#endif
