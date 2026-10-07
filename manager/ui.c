#include "ui.h"

#include <stdio.h>
#include <string.h>

#define GOLD 0xFFCAA560
#define GOLD_DARK 0xFF6E5A36

static void plain_backdrop(void)
{
    gfx_noclip();
    gfx_gradient(0, 0, GFX_W, GFX_H, 0xFF1A1510, 0xFF070605);
}

static void corner(int x, int y, int dx, int dy)
{
    gfx_fill(dx > 0 ? x : x - 7, y, 8, 1, GOLD);
    gfx_fill(x, dy > 0 ? y : y - 7, 1, 8, GOLD);
}

static void plain_panel(int x, int y, int w, int h)
{
    gfx_fill(x, y, w, h, 0xE0141009);
    gfx_outline(x, y, w, h, GOLD_DARK);
    gfx_outline(x + 2, y + 2, w - 4, h - 4, 0x30CAA560);
    corner(x, y, 1, 1);
    corner(x + w - 1, y, -1, 1);
    corner(x, y + h - 1, 1, -1);
    corner(x + w - 1, y + h - 1, -1, -1);
}

static void plain_select(int x, int y, int w, int h)
{
    gfx_gradient(x, y, w, h, 0x50CAA560, 0x30CAA560);
    gfx_fill(x, y, 3, h, GOLD);
}

const struct theme ui_plain = {
    0xFFDCD2B4, 0xFF8F846C, GOLD, 0xFFFFF0C8, 0xFFE0705A, GOLD_DARK,
    &font_body, &font_bold, &font_small, &font_title,
    plain_backdrop, plain_panel, plain_select,
};

const struct theme *ui = &ui_plain;

void ui_size(char *out, size_t n, unsigned long long bytes)
{
    if (bytes < 1 << 20)
        snprintf(out, n, "%llu KB", (bytes + 1023) >> 10);
    else if (bytes < 1ULL << 30)
        snprintf(out, n, "%llu MB", bytes >> 20);
    else
        snprintf(out, n, "%llu.%llu GB", bytes >> 30, (bytes & ((1 << 30) - 1)) * 10 >> 30);
}

int ui_button(int button, int x, int y)
{
    static const gfx_color face[] = {0xFF4E9A32, 0xFFC23A2A, 0xFF2D69C0, 0xFFD6A21C};
    static const char *const name[] = {"A", "B", "X", "Y", "START", "L", "R", "BACK"};
    const struct font *f = ui->small;
    int w = gfx_text_width(f, name[button]);

    if (button <= UI_Y) {
        gfx_disc(x + 9, y + 9, 9, face[button]);
        gfx_text(f, x + 9 - w / 2, y, 0xFFFFFFFF, name[button]);
        return 18;
    }
    w += 10;
    gfx_fill(x, y + 1, w, 16, 0xFF4A443A);
    gfx_fill(x + 1, y, w - 2, 18, 0xFF4A443A);
    gfx_text(f, x + 5, y, 0xFFE8E0D0, name[button]);
    return w;
}

void ui_header(const char *const *names, int n, int active)
{
    const struct font *f = ui->title;
    int i, x, total = 0, gap = 28, w, baseline = 46;

    gfx_text(&font_logo, UI_LEFT, baseline - font_logo.ascent, ui->accent, "TES3X");
    for (i = 0; i < n; i++)
        total += gfx_text_width(f, names[i]) + gap;
    x = UI_RIGHT - total - (n > 1 ? 2 * 24 - gap : 0) + (n > 1 ? 0 : gap);
    if (n > 1)
        x += ui_button(UI_L, x, baseline - 16) + 14;
    for (i = 0; i < n; i++) {
        w = gfx_text_width(f, names[i]);
        gfx_text(f, x, baseline - f->ascent, i == active ? ui->accent : ui->dim, names[i]);
        if (i == active)
            gfx_fill(x, baseline + 6, w, 2, ui->accent);
        x += w + (i + 1 < n ? gap : 14);
    }
    if (n > 1)
        ui_button(UI_R, x, baseline - 16);
    gfx_fill(UI_LEFT, UI_HEADER_RULE, UI_WIDTH, 1, ui->line);
}

void ui_panel(void)
{
    ui->panel(UI_LEFT, UI_PANEL_TOP, UI_WIDTH, UI_PANEL_BOTTOM - UI_PANEL_TOP);
}

void ui_footer(const struct ui_hint *hints, int n, const char *status)
{
    int i, x = UI_LEFT, y = UI_PANEL_BOTTOM + 10, w;

    for (i = 0; i < n; i++) {
        x += ui_button(hints[i].button, x, y) + 5;
        x = gfx_text(ui->small, x, y, ui->text, hints[i].label) + 16;
    }
    if (status && *status) {
        w = gfx_text_width(ui->small, status);
        if (w > UI_RIGHT - x)
            w = UI_RIGHT - x;
        gfx_text_fit(ui->small, UI_RIGHT - w, y, w, ui->dim, status);
    }
}

void ui_list_move(struct ui_list *l, int delta)
{
    l->sel += delta;
    if (l->sel >= l->count)
        l->sel = l->count - 1;
    if (l->sel < 0)
        l->sel = 0;
    if (l->sel < l->top)
        l->top = l->sel;
    else if (l->rows > 0 && l->sel >= l->top + l->rows)
        l->top = l->sel - l->rows + 1;
    if (l->top > l->count - l->rows)
        l->top = l->count - l->rows > 0 ? l->count - l->rows : 0;
}

void ui_scrollbar(int x, int y, int h, const struct ui_list *l)
{
    int thumb, at;

    if (l->count <= l->rows || l->rows <= 0)
        return;
    gfx_fill(x + 1, y, 1, h, ui->line);
    thumb = h * l->rows / l->count;
    if (thumb < 12)
        thumb = 12;
    at = (h - thumb) * l->top / (l->count - l->rows);
    gfx_fill(x, y + at, 3, thumb, ui->accent);
}

int ui_dialog(const char *title, const char *const *lines, int n, int extra,
              const struct ui_hint *hints, int nh)
{
    const struct font *f = ui->body;
    int w = 460, inner = w - 2 * 24, h, x = (GFX_W - w) / 2, y, i, count = 0, bx, at;

    for (i = 0; i < n; i++)
        if (lines[i] && *lines[i])
            count += gfx_text_wrap(f, 0, 0, inner, 0, lines[i]);
    h = 20 + ui->title->line + 8 + count * f->line + extra + (nh ? 16 + 18 : 0) + 20;
    y = (GFX_H - h) / 2;
    gfx_noclip();
    gfx_fill(0, 0, GFX_W, GFX_H, 0xA0000000);
    ui->panel(x, y, w, h);
    gfx_text(ui->title, x + 24, y + 20, ui->accent, title);
    y += 20 + ui->title->line + 8;
    for (i = 0; i < n; i++)
        if (lines[i] && *lines[i])
            y += f->line * gfx_text_wrap(f, x + 24, y, inner, ui->text, lines[i]);
    at = y;
    y += extra + 16;
    for (i = 0, bx = x + 24; i < nh; i++) {
        bx += ui_button(hints[i].button, bx, y) + 5;
        bx = gfx_text(ui->small, bx, y, ui->text, hints[i].label) + 16;
    }
    return at;
}

unsigned ui_tick;

void ui_spinner(int cx, int cy)
{
    static const signed char ring[8][2] = {{0, -13}, {9, -9}, {13, 0}, {9, 9},
                                           {0, 13}, {-9, 9}, {-13, 0}, {-9, -9}};
    unsigned i, age;

    for (i = 0; i < 8; i++) {
        age = (ui_tick - i) % 8;
        gfx_disc(cx + ring[i][0], cy + ring[i][1], 4,
                 (ui->accent & 0xFFFFFF) | (255 - age * 26) << 24);
    }
}

void ui_progress(const char *title, const char *what, unsigned long long done, unsigned long long total)
{
    static const struct ui_hint cancel = {UI_B, "Cancel"};
    char amount[64];
    const char *lines[2];
    int bar = 460 - 48, x = (GFX_W - 460) / 2 + 24, y;

    if (!total) {
        y = ui_dialog(title, &what, 1, 40, &cancel, 1);
        ui_spinner(GFX_W / 2, y + 22);
        return;
    }
    snprintf(amount, sizeof(amount), "%llu of %llu MB", done >> 20, total >> 20);
    lines[0] = what;
    lines[1] = amount;
    y = ui_dialog(title, lines, 2, 20, &cancel, 1) + 8;
    gfx_fill(x, y, bar, 8, 0xFF2A241A);
    gfx_fill(x, y, total ? (int)(bar * done / total) : 0, 8, ui->accent);
}

#define KB_KEY_W 44
#define KB_KEY_H 32
#define KB_GAP 4
#define KB_FIELD_H 36
#define KB_GRID_W (UI_KB_COLS * (KB_KEY_W + KB_GAP) - KB_GAP)

/* A space is a key that types nothing. */
static const char *const kb_layers[3][UI_KB_ROWS - 1] = {
    {"1234567890", "qwertyuiop", "asdfghjkl:", "zxcvbnm.-_"},
    {"1234567890", "QWERTYUIOP", "ASDFGHJKL:", "ZXCVBNM.-_"},
    {"!@#$%^&*()", "-_=+[]{}\\|", ";:'\",.<>/?", "`~        "},
};
static const char *const kb_shift_label[3] = {"ABC", "#+=", "abc"};
static const struct {
    int key, col, cols;
    const char *label;
} kb_wide[4] = {
    {UI_KB_SHIFT, 0, 2, NULL},
    {UI_KB_SPACE, 2, 4, "Space"},
    {UI_KB_DELETE, 6, 2, "Delete"},
    {UI_KB_DONE, 8, 2, "Done"},
};

static int kb_wide_at(int col)
{
    int i;

    for (i = 3; i > 0 && col < kb_wide[i].col; i--)
        ;
    return i;
}

void ui_keyboard_open(struct ui_keyboard *k, const char *title, const char *note,
                      const char *text, int max, int masked)
{
    memset(k, 0, sizeof(*k));
    snprintf(k->title, sizeof(k->title), "%s", title);
    snprintf(k->note, sizeof(k->note), "%s", note ? note : "");
    snprintf(k->text, sizeof(k->text), "%s", text ? text : "");
    k->max = max > 0 && max < (int)sizeof(k->text) ? max : (int)sizeof(k->text) - 1;
    k->text[k->max] = 0;
    k->masked = masked;
    k->row = 1;
    k->open = 1;
}

void ui_keyboard_move(struct ui_keyboard *k, int dx, int dy)
{
    if (dy)
        k->row = (k->row + dy + UI_KB_ROWS) % UI_KB_ROWS;
    if (dx && k->row == UI_KB_ROWS - 1)
        k->col = kb_wide[(kb_wide_at(k->col) + dx + 4) % 4].col;
    else if (dx)
        k->col = (k->col + dx + UI_KB_COLS) % UI_KB_COLS;
}

int ui_keyboard_key(const struct ui_keyboard *k)
{
    char c;

    if (k->row == UI_KB_ROWS - 1)
        return kb_wide[kb_wide_at(k->col)].key;
    c = kb_layers[k->layer][k->row][k->col];
    return c == ' ' ? UI_KB_NONE : (unsigned char)c;
}

int ui_keyboard_press(struct ui_keyboard *k, int key)
{
    size_t n = strlen(k->text);

    if (key == UI_KB_DONE)
        return 1;
    if (key == UI_KB_SHIFT)
        k->layer = (k->layer + 1) % 3;
    else if (key == UI_KB_DELETE && n)
        k->text[n - 1] = 0;
    else if (key == UI_KB_SPACE || key > 0) {
        if ((int)n < k->max) {
            k->text[n] = key > 0 ? (char)key : ' ';
            k->text[n + 1] = 0;
        }
    }
    return 0;
}

void ui_keyboard(const struct ui_keyboard *k)
{
    static const struct ui_hint hints[] = {{UI_A, "Type"},     {UI_X, "Delete"},
                                           {UI_Y, "Shift"},    {UI_START, "Done"},
                                           {UI_B, "Cancel"}};
    char shown[sizeof(k->text)], key[2] = {0};
    const char *label;
    const struct font *f;
    int w = KB_GRID_W + 48, x = (GFX_W - w) / 2, left = x + 24, notes, h, y, r, c, i, kx, ky, kw;
    int sel;
    size_t n = strlen(k->text);

    notes = k->note[0] ? gfx_text_wrap(ui->small, 0, 0, KB_GRID_W, 0, k->note) : 0;
    h = 20 + ui->title->line + 4 + notes * ui->small->line + 8 + KB_FIELD_H + 14
        + UI_KB_ROWS * (KB_KEY_H + KB_GAP) - KB_GAP + 16 + 18 + 20;
    y = (GFX_H - h) / 2;
    gfx_noclip();
    gfx_fill(0, 0, GFX_W, GFX_H, 0xA0000000);
    ui->panel(x, y, w, h);
    gfx_text(ui->title, left, y + 20, ui->accent, k->title);
    y += 20 + ui->title->line + 4;
    if (notes)
        y += ui->small->line * gfx_text_wrap(ui->small, left, y, KB_GRID_W, ui->dim, k->note);
    y += 8;

    gfx_fill(left, y, KB_GRID_W, KB_FIELD_H, 0xFF0C0A07);
    gfx_outline(left, y, KB_GRID_W, KB_FIELD_H, ui->line);
    for (i = 0; i < (int)n; i++)
        shown[i] = k->masked && i + 1 < (int)n ? '*' : k->text[i];
    shown[n] = 0;
    /* the end of a long text, where the typing is */
    for (label = shown; *label && gfx_text_width(ui->body, label) > KB_GRID_W - 28; label++)
        ;
    kx = gfx_text(ui->body, left + 12, y + (KB_FIELD_H - ui->body->line) / 2, ui->bright, label);
    gfx_fill(kx + 1, y + 8, 2, KB_FIELD_H - 16, ui->accent);
    y += KB_FIELD_H + 14;

    for (r = 0; r < UI_KB_ROWS; r++)
        for (c = 0; c < UI_KB_COLS; c++) {
            if (r == UI_KB_ROWS - 1) {
                i = kb_wide_at(c);
                if (c != kb_wide[i].col)
                    continue;
                kw = kb_wide[i].cols * (KB_KEY_W + KB_GAP) - KB_GAP;
                label = kb_wide[i].label ? kb_wide[i].label : kb_shift_label[k->layer];
                sel = r == k->row && i == kb_wide_at(k->col);
            } else {
                key[0] = kb_layers[k->layer][r][c];
                kw = KB_KEY_W;
                label = key[0] == ' ' ? "" : key;
                sel = r == k->row && c == k->col;
            }
            kx = left + c * (KB_KEY_W + KB_GAP);
            ky = y + r * (KB_KEY_H + KB_GAP);
            if (sel) {
                ui->select(kx, ky, kw, KB_KEY_H);
                gfx_outline(kx, ky, kw, KB_KEY_H, ui->accent);
            } else {
                gfx_fill(kx, ky, kw, KB_KEY_H, 0x20CAA560);
                gfx_outline(kx, ky, kw, KB_KEY_H, 0x40CAA560);
            }
            f = sel ? ui->bold : r == UI_KB_ROWS - 1 ? ui->small : ui->body;
            gfx_text(f, kx + (kw - gfx_text_width(f, label)) / 2,
                     ky + (KB_KEY_H - f->line) / 2 + 1, sel ? ui->bright : ui->text, label);
        }
    y += UI_KB_ROWS * (KB_KEY_H + KB_GAP) - KB_GAP + 16;
    for (i = 0, kx = left; i < (int)(sizeof(hints) / sizeof(*hints)); i++) {
        kx += ui_button(hints[i].button, kx, y) + 5;
        kx = gfx_text(ui->small, kx, y, ui->text, hints[i].label) + 16;
    }
}
