#ifndef SCREENS_H
#define SCREENS_H

/* The Dash's screens, drawn from a view main.c keeps; portable, so preview/ renders them
 * on the PC. */

#include "mgr.h"
#include "ui.h"

enum tab { TAB_BUILDS, TAB_SERVERS, TAB_CONTENT, TAB_SETTINGS, TAB_COUNT };
enum page { PAGE_MAIN, PAGE_DETAILS, PAGE_BASES, PAGE_BROWSE, PAGE_SERVER, PAGE_CONTENT,
            PAGE_STORE };
enum setting { SET_BASE, SET_AGENT, SET_STORE, SET_DASH, SET_QUIT, SET_COUNT };

#define LINE_HEAD 1
#define LINE_BAD 2
#define MAX_LINES 512

struct line {
    char label[24];
    char text[128];
    int style;
};

/* What a dialog's v.confirm asks. */
enum confirm { CONFIRM_NONE, CONFIRM_INSTALL, CONFIRM_BUILD, CONFIRM_SERVER, CONFIRM_UPDATE,
               CONFIRM_DASH_UPDATE, CONFIRM_SERVER_KEY, CONFIRM_CONTENT_KEY,
               CONFIRM_CONTENT_INSTALL, CONFIRM_CONTENT_REMOVE, CONFIRM_STORE_DELETE };

struct view {
    int tab, page;
    const struct build *builds;
    struct ui_list build_list;
    const struct line *lines; /* the details pages */
    struct ui_list line_list;
    const struct base *bases;
    int base_count;
    struct ui_list base_list; /* the bases, then a row for choosing a folder */
    int base_current;         /* index of OverlayBase among bases, or -1 */
    const struct folder *folders;
    struct ui_list folder_list;
    const char *browse_path; /* the folder shown, empty for the drives */
    const struct server *servers;
    const char *const *server_builds; /* each server's installed build folder, or NULL */
    struct ui_list server_list;
    const struct content_host *content_hosts;
    struct ui_list content_hosts_list;
    const struct content_entry *content_entries;
    struct ui_list content_entries_list;
    struct ui_list settings;
    const char *overlay_base, *agent, *slot;
    const char *store_summary; /* the shared archives, for Settings */
    int store_unused;          /* shared archives no installed build lists */
    struct ui_keyboard kb; /* over the page when open */
    /* a dialog over the page and the keyboard, when title is set */
    char title[48];
    char text[4][128];
    int dismiss; /* B closes it */
    int confirm; /* A goes ahead, B cancels */
};

/* Rows the lists show, for ui_list.rows. */
#define BUILD_ROWS 7
#define SERVER_ROWS 7
#define LINE_ROWS 12
#define BASE_ROWS 5
#define FOLDER_ROWS 12

void screen_draw(const struct view *v);
/* The page with a dialog of text and no buttons over it, for work that cannot be cancelled. */
void screen_busy(const struct view *v, const char *text);
void screen_progress(const struct view *v, const char *title, const char *what, unsigned long long done,
                     unsigned long long total);

#endif
