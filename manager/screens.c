#include "screens.h"

#include <stdio.h>

#define ROW_H 46
#define LINE_H 24
#define IN_LEFT (UI_LEFT + UI_PAD)
#define IN_RIGHT (UI_RIGHT - UI_PAD)
#define LIST_TOP (UI_PANEL_TOP + 8)
#define SCROLL_X (UI_RIGHT - 10)

static const char *const tab_names[TAB_COUNT] = {"Builds", "Servers", "Settings"};

/* A two-line row: title and an optional right-hand note, then a smaller line below. */
static void row(int y, int selected, const char *title, const char *note, gfx_color note_color,
                const char *sub, gfx_color sub_color)
{
    int right = IN_RIGHT - 8, nw = note ? gfx_text_width(ui->small, note) : 0;

    if (selected)
        ui->select(UI_LEFT + 4, y, UI_WIDTH - 8 - 14, ROW_H - 2);
    if (note)
        gfx_text(ui->small, right - nw, y + 6, note_color, note);
    gfx_text_fit(selected ? ui->bold : ui->body, IN_LEFT, y + 3, right - IN_LEFT - nw - 12,
                 selected ? ui->bright : ui->text, title);
    if (sub)
        gfx_text_fit(ui->small, IN_LEFT, y + 24, right - IN_LEFT, sub_color, sub);
}

static void empty(const char *title, const char *text)
{
    int w = UI_WIDTH - 2 * 48;

    gfx_text(ui->bold, (GFX_W - gfx_text_width(ui->bold, title)) / 2, 180, ui->text, title);
    gfx_text_wrap(ui->body, UI_LEFT + 48, 212, w, ui->dim, text);
}

static void draw_builds(const struct view *v)
{
    const struct build *b;
    char size[24], sub[160];
    int i, y;

    if (!v->build_list.count) {
        empty("No builds found",
              "Builds installed from the PC appear here. The manager looks for " MANIFEST
              " in the folders under C, E, F and G:\\Games.");
        return;
    }
    for (i = v->build_list.top, y = LIST_TOP;
         i < v->build_list.count && i < v->build_list.top + BUILD_ROWS; i++, y += ROW_H) {
        b = &v->builds[i];
        ui_size(size, sizeof(size), b->bytes);
        if (!b->error[0])
            snprintf(sub, sizeof(sub), "%s  \xB7  %s profile  \xB7  %d plugin%s", b->path,
                     b->profile, b->plugins, b->plugins == 1 ? "" : "s");
        row(y, i == v->build_list.sel, b->name, size, ui->dim, b->error[0] ? b->error : sub,
            b->error[0] ? ui->bad : ui->dim);
    }
    ui_scrollbar(SCROLL_X, LIST_TOP, BUILD_ROWS * ROW_H, &v->build_list);
}

static void draw_servers(const struct view *v)
{
    const struct server *s;
    const char *build;
    char sub[192];
    int i, y;

    if (!v->server_list.count) {
        empty("No servers yet",
              "Servers joined from the game's main menu appear here, with the keys the game "
              "keeps for them. Add one by its address to install its build.");
        return;
    }
    for (i = v->server_list.top, y = LIST_TOP;
         i < v->server_list.count && i < v->server_list.top + SERVER_ROWS; i++, y += ROW_H) {
        s = &v->servers[i];
        build = v->server_builds[i];
        snprintf(sub, sizeof(sub), "%s%.16s  \xB7  %s%s%s",
                 s->has_server_key ? "Key " : "Not contacted yet",
                 s->has_server_key ? s->fingerprint : "", build ? build : "build not installed",
                 s->character[0] ? "  \xB7  " : "", s->character);
        row(y, i == v->server_list.sel, s->name,
            s->online == 2 ? "Manager update needed" : s->online == 3 ? "Server update needed" :
            s->online > 0 ? (build ? "Online  \xB7  Installed" : "Online") : "Offline",
            s->online == 1 ? ui->accent : ui->bad, sub, ui->dim);
    }
    ui_scrollbar(SCROLL_X, LIST_TOP, SERVER_ROWS * ROW_H, &v->server_list);
}

static void draw_lines(const struct view *v, const char *title, const char *sub)
{
    const struct line *l;
    int i, y;

    gfx_text_fit(ui->bold, IN_LEFT, UI_PANEL_TOP + 10, IN_RIGHT - IN_LEFT, ui->accent, title);
    gfx_text_fit(ui->small, IN_LEFT, UI_PANEL_TOP + 32, IN_RIGHT - IN_LEFT, ui->dim, sub);
    gfx_fill(IN_LEFT, UI_PANEL_TOP + 56, IN_RIGHT - IN_LEFT, 1, ui->line);
    for (i = v->line_list.top, y = UI_PANEL_TOP + 64;
         i < v->line_list.count && i < v->line_list.top + LINE_ROWS; i++, y += LINE_H) {
        l = &v->lines[i];
        if (l->style & LINE_HEAD) {
            gfx_text(ui->bold, IN_LEFT, y, ui->accent, l->text);
        } else if (l->label[0]) {
            gfx_text(ui->body, IN_LEFT, y, ui->dim, l->label);
            gfx_text_fit(ui->body, IN_LEFT + 120, y, IN_RIGHT - IN_LEFT - 128,
                         l->style & LINE_BAD ? ui->bad : ui->text, l->text);
        } else {
            gfx_text_fit(ui->body, IN_LEFT + 16, y, IN_RIGHT - IN_LEFT - 24,
                         l->style & LINE_BAD ? ui->bad : ui->text, l->text);
        }
    }
    ui_scrollbar(SCROLL_X, UI_PANEL_TOP + 64, LINE_ROWS * LINE_H, &v->line_list);
}

static void draw_details(const struct view *v)
{
    const struct build *b = &v->builds[v->build_list.sel];

    draw_lines(v, b->name, b->path);
}

static void draw_settings(const struct view *v)
{
    char version[64];
    int y = LIST_TOP;

    snprintf(version, sizeof(version), "Version %s%s%s", MGR_VERSION,
             v->slot && *v->slot ? ", slot " : "", v->slot ? v->slot : "");
    row(y, v->settings.sel == SET_BASE, "Retail base", NULL, 0,
        v->overlay_base[0] ? v->overlay_base : "Not set: overlay builds and XBE updates need one",
        v->overlay_base[0] ? ui->dim : ui->bad);
    row(y += ROW_H, v->settings.sel == SET_AGENT, "PC agent", NULL, 0, v->agent, ui->dim);
    row(y += ROW_H, v->settings.sel == SET_MANAGER, "Manager", NULL, 0, version, ui->dim);
}

static void draw_bases(const struct view *v)
{
    const struct base *b;
    const char *sub;
    int i, y, top = UI_PANEL_TOP + 80, rows = BASE_ROWS;

    gfx_text(ui->bold, IN_LEFT, UI_PANEL_TOP + 10, ui->accent, "Retail base");
    gfx_text_wrap(ui->small, IN_LEFT, UI_PANEL_TOP + 34, IN_RIGHT - IN_LEFT, ui->dim,
                  "Overlay builds read unchanged game files from it, and XBE updates are rebuilt "
                  "from its morrowind.xbe.");
    if (!v->base_count) {
        gfx_text_wrap(ui->body, IN_LEFT, top + 10, IN_RIGHT - IN_LEFT, ui->text,
                      "No retail base found under C, E, F or G:\\Games. Install one from the PC, "
                      "or choose the folder that holds the game (morrowind.xbe and Data Files).");
        top += 90, rows = 1;
    }
    for (i = v->base_list.top, y = top;
         i < v->base_list.count && i < v->base_list.top + rows; i++, y += ROW_H) {
        if (i == v->base_count) {
            row(y, i == v->base_list.sel, "Choose a folder...", NULL, 0,
                "A copy of the game anywhere on the disk", ui->dim);
            continue;
        }
        b = &v->bases[i];
        sub = !b->installed ? "Copied, not installed by TES3X: use it only if it is unmodded"
              : b->has_xbe  ? "Installed by TES3X"
                            : "Installed by TES3X without the retail XBE: XBE updates cannot use it";
        row(y, i == v->base_list.sel, b->path, i == v->base_current ? "In use" : NULL, ui->accent,
            sub, b->installed && b->has_xbe ? ui->dim : ui->bad);
    }
    ui_scrollbar(SCROLL_X, top, rows * ROW_H, &v->base_list);
}

static void draw_browse(const struct view *v)
{
    const struct folder *f;
    int i, y, top = UI_PANEL_TOP + 64;

    gfx_text(ui->bold, IN_LEFT, UI_PANEL_TOP + 10, ui->accent, "Choose the retail base");
    gfx_text_fit(ui->small, IN_LEFT, UI_PANEL_TOP + 34, IN_RIGHT - IN_LEFT, ui->dim,
                 v->browse_path[0] ? v->browse_path : "Drives");
    gfx_fill(IN_LEFT, UI_PANEL_TOP + 56, IN_RIGHT - IN_LEFT, 1, ui->line);
    if (!v->folder_list.count) {
        gfx_text(ui->body, IN_LEFT, top + 4, ui->dim, "No folders here.");
        return;
    }
    for (i = v->folder_list.top, y = top;
         i < v->folder_list.count && i < v->folder_list.top + FOLDER_ROWS; i++, y += LINE_H) {
        f = &v->folders[i];
        if (i == v->folder_list.sel)
            ui->select(UI_LEFT + 4, y, UI_WIDTH - 8 - 14, LINE_H);
        if (f->game)
            gfx_text(ui->small, IN_RIGHT - 8 - gfx_text_width(ui->small, "morrowind.xbe"), y + 3,
                     ui->accent, "morrowind.xbe");
        gfx_text_fit(i == v->folder_list.sel ? ui->bold : ui->body, IN_LEFT, y + 1,
                     IN_RIGHT - IN_LEFT - 140, i == v->folder_list.sel ? ui->bright : ui->text,
                     f->name);
    }
    ui_scrollbar(SCROLL_X, top, FOLDER_ROWS * LINE_H, &v->folder_list);
}

static void draw_page(const struct view *v)
{
    static const struct ui_hint list[] = {{UI_A, "Details"}, {UI_Y, "Rescan"},
                                          {UI_START, "Launch"}, {UI_BACK, "Delete"}};
    static const struct ui_hint details[] = {{UI_A, "Launch"}, {UI_X, "Verify"}, {UI_B, "Back"},
                                             {UI_BACK, "Delete"}};
    static const struct ui_hint bases[] = {{UI_A, "Use"}, {UI_B, "Back"}};
    static const struct ui_hint choose[] = {{UI_A, "Choose"}, {UI_B, "Back"}};
    static const struct ui_hint browse[] = {{UI_A, "Open"}, {UI_X, "Use this folder"},
                                            {UI_B, "Up"}};
    static const struct ui_hint drives[] = {{UI_A, "Open"}, {UI_B, "Back"}};
    static const struct ui_hint change[] = {{UI_A, "Change"}};
    static const struct ui_hint update[] = {{UI_A, "Check for update"}};
    static const struct ui_hint servers[] = {{UI_A, "Details"}, {UI_X, "Add"},
                                             {UI_Y, "Rescan"}, {UI_START, "Join"},
                                             {UI_BACK, "Delete"}};
    static const struct ui_hint server[] = {{UI_A, "Join"}, {UI_X, "Update build"},
                                            {UI_Y, "Password"}, {UI_B, "Back"},
                                            {UI_BACK, "Delete"}};
    const struct ui_hint *hints = NULL;
    char status[96];
    int n = 0;

    ui->backdrop();
    ui_header(tab_names, TAB_COUNT, v->tab);
    ui_panel();
    gfx_clip(UI_LEFT, UI_PANEL_TOP, UI_WIDTH, UI_PANEL_BOTTOM - UI_PANEL_TOP);
    if (v->tab == TAB_BUILDS && v->page == PAGE_DETAILS) {
        draw_details(v);
        hints = details, n = 4;
        if (v->builds[v->build_list.sel].error[0])
            hints = details + 2, n = 2;
    } else if (v->tab == TAB_BUILDS) {
        draw_builds(v);
        hints = list, n = 4;
        if (!v->build_list.count)
            hints = list + 1, n = 1;
    } else if (v->tab == TAB_SERVERS && v->page == PAGE_SERVER) {
        const struct server *s = &v->servers[v->server_list.sel];

        draw_lines(v, s->name, s->file);
        hints = server, n = 5;
        if (!v->server_builds[v->server_list.sel])
            hints = server + 1, n = 4;
    } else if (v->tab == TAB_SERVERS) {
        draw_servers(v);
        hints = servers, n = 5;
        if (!v->server_list.count)
            hints = servers + 1, n = 2;
    } else if (v->page == PAGE_BASES) {
        draw_bases(v);
        hints = v->base_list.sel == v->base_count ? choose : bases, n = 2;
    } else if (v->page == PAGE_BROWSE) {
        draw_browse(v);
        if (!v->browse_path[0])
            hints = drives, n = 2;
        else if (v->folder_list.count)
            hints = browse, n = 3;
        else
            hints = browse + 1, n = 2;
    } else {
        draw_settings(v);
        if (v->settings.sel == SET_BASE)
            hints = change, n = 1;
        else if (v->settings.sel == SET_MANAGER)
            hints = update, n = 1;
    }
    gfx_noclip();
    snprintf(status, sizeof(status), "Agent: %s", v->agent);
    ui_footer(hints, n, status);
}

void screen_draw(const struct view *v)
{
    static const struct ui_hint close = {UI_B, "Close"};
    static const struct ui_hint confirm[] = {{UI_A, "Go ahead"}, {UI_B, "Cancel"}};
    static const struct ui_hint update[] = {{UI_X, "Update"}, {UI_B, "Cancel"}};
    static const struct ui_hint manager_update[] = {{UI_X, "Check for update"}, {UI_B, "Cancel"}};
    const char *lines[4];
    int i;

    draw_page(v);
    if (v->kb.open)
        ui_keyboard(&v->kb);
    if (!v->title[0])
        return;
    for (i = 0; i < 4; i++)
        lines[i] = v->text[i];
    if (v->confirm)
        ui_dialog(v->title, lines, 4, 0, v->confirm == CONFIRM_MANAGER_UPDATE ? manager_update :
                  v->confirm == CONFIRM_UPDATE ? update : confirm, 2);
    else
        ui_dialog(v->title, lines, 4, 0, v->dismiss ? &close : NULL, v->dismiss ? 1 : 0);
}

void screen_busy(const struct view *v, const char *text)
{
    draw_page(v);
    ui_spinner(GFX_W / 2, ui_dialog("Working", &text, 1, 40, NULL, 0) + 22);
}

void screen_progress(const struct view *v, const char *title, const char *what, unsigned long long done,
                     unsigned long long total)
{
    draw_page(v);
    ui_progress(title ? title : "Working", what, done, total);
}
