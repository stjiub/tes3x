/* The main menu's Join, above Exit. It turns the menu's column into the server list: one row per
 * server this console knows (NetServer, then servers.ini's sections, newest first), New server,
 * which types an address on the console patch's keyboard, and Return. Choosing a server brings the
 * NIC up if it is not, opens a lobby session and marks later relaunches with the server; the
 * character list or the start points follow at the main menu as they do in a game. */
#define UI_CLICK 0xFFFF8035u
#define UI_PRESS 0xFFFF8034u /* the main menu's buttons show their pressed image */
#define UI_OVER 0xFFFF8033u  /* ... their highlighted one */
#define UI_LEAVE 0xFFFF8032u /* ... their normal one */
#define UI_FLAG_B 0xFFFF800Bu
#define UI_FOCUS_A 0xFFFF8048u /* both UI_TRUE on each main menu button */
#define UI_FOCUS_B 0xFFFF80A8u
#define UI_CHILD_ALIGN_X 0xFFFF8054u
#define UI_CHILD_ALIGN_Y 0xFFFF8055u
#define UI_TRUE 0xFFFF80BDu
#define UI_HALF 0x3F000000 /* 0.5f */
#define UI_PROP_INT 1
#define UI_PROP_FLOAT 2
#define UI_PROP_PTR 8
#define UI_PROP_ENUM 0x10
#define UI_PROP_HANDLER 0x20
#define UI_PARENT 0x34
#define UI_CHILDREN 0x28 /* vector: begin, then end at +4 */
#define UI_WIDTH 0xF4
#define UI_HEIGHT 0xF8
#define UI_COLOUR 0x154    /* red, green, blue, alpha; MWSE's PC Element + 8 */
#define UI_COLOUR_CHANGED 0x7F /* flagColourChanged, likewise */
#define UI_FONT 0x164      /* 0 the small Century Gothic, 1 the big one */
#define UI_IMAGE_FLAG 0x87 /* cleared on each main menu image */
#define MENU_ROW 0x32      /* a main menu button's height */
#define MENU_ROWS 6        /* New, Load, Join, Manager, Options, Exit */
#define SERVERS_SHOWN 8
#define LIST_VISIBLE 4   /* rows the list's box shows; the rest scroll */
#define LIST_ROW 32
#define BOX_PAD 8
#define UI_FLOW 0xFFFF8059u
#define UI_TOP_DOWN 0xFFFF80CCu
#define UI_ALIGN_Y 0x12C /* the main menu sits at 0.95 */
#define TYPE_ALIGN_Y 0.12f
typedef void *(__attribute__((thiscall)) *fn_find_child)(void *widget, u32 id);
typedef void *(__attribute__((thiscall)) *fn_create_block)(void *parent, u32 id, int a0);
typedef void *(__attribute__((thiscall)) *fn_create_image)(void *parent, u32 id, const char *path,
                                                           int a0);
typedef void *(__attribute__((thiscall)) *fn_create_label)(void *parent, u32 id, const char *text,
                                                           int black, int replace);
typedef void *(__attribute__((thiscall)) *fn_create_nif)(void *parent, u32 id, const char *path,
                                                         int a0);
typedef void *(__attribute__((thiscall)) *fn_create_widget)(void *parent, u32 id, u32 factory,
                                                            int a0);
typedef void(__attribute__((thiscall)) *fn_set_size)(void *widget, int value);
typedef void(__attribute__((thiscall)) *fn_set_auto)(void *widget, int on);
typedef void(__attribute__((thiscall)) *fn_set_visible)(void *widget, int on);
typedef void(__attribute__((thiscall)) *fn_set_prop)(void *widget, u32 id, int value, int type);
typedef void(__attribute__((thiscall)) *fn_set_text)(void *widget, const char *text);
typedef void(__attribute__((thiscall)) *fn_layout)(void *widget, int a0);
typedef void(__cdecl *fn_set_focus)(void *widget, int on);
typedef char(__cdecl *fn_ui_handler)(void *owner, u32 id, int d0, int d1, void *source);
static const char *const button_states[3] = {"TES3X_normal", "TES3X_over", "TES3X_pressed"};
static const char *const main_rows[MENU_ROWS] = {
    "MenuOptions_New_container", "MenuOptions_Load_container", "TES3X_Join", "TES3X_Manager",
    "MenuOptions_Options_container", "MenuOptions_Exit_container"};
static const char *const server_rows[SERVERS_SHOWN] = {
    "TES3X_Server1", "TES3X_Server2", "TES3X_Server3", "TES3X_Server4",
    "TES3X_Server5", "TES3X_Server6", "TES3X_Server7", "TES3X_Server8"};
static const float gold[3] = {0.88f, 0.74f, 0.42f}, gold_lit[3] = {1.0f, 0.93f, 0.68f};
static char servers[SERVERS_SHOWN][JOIN_NAME + 1];
static u32 servers_n, servers_view, join_buttons, joins_pressed, menu_height, menu_width;
static int server_chosen = -1; /* a row, SERVER_NEW or SERVER_RETURN, from a click handler */
static u32 typing;             /* the keyboard is up, for TYPE_SERVER or TYPE_PASSWORD */
#define TYPE_SERVER 1u
#define TYPE_PASSWORD 2u
#define JOIN_WAIT_US 20000000u /* a join with no WELCOME by then has failed */
static u8 *join_row, *list_focus; /* the row being joined; the focus the list last followed */
static u32 join_started, join_failures, list_follows;
typedef u8 *(__attribute__((thiscall)) *fn_get_focus)(void *menu);
typedef char(__cdecl *fn_scroll_to)(void *element);
#define SERVER_NEW SERVERS_SHOWN
#define SERVER_RETURN (SERVERS_SHOWN + 1)
#define SERVER_OPEN (SERVERS_SHOWN + 2)
#define SERVER_MANAGER (SERVERS_SHOWN + 3)
static char manager_path[128]; /* [Xbox] Manager, which installing the manager writes */

static int same_server(const char *a, const char *b)
{
    for (; *a && (*a | 0x20) == (*b | 0x20); a++, b++)
        ;
    return !*a && !*b;
}

static void server_add(const char *name, u32 n)
{
    u32 i;

    if (!n || n > JOIN_NAME)
        return;
    if (servers_n == SERVERS_SHOWN)
        servers_n--; /* the oldest goes */
    for (i = servers_n; i > 0; i--)
        copy((u8 *)servers[i], (const u8 *)servers[i - 1], JOIN_NAME + 1);
    copy((u8 *)servers[0], (const u8 *)name, n);
    servers[0][n] = 0;
    servers_n++;
    for (i = 1; i < servers_n; i++)
        if (same_server(servers[i], servers[0])) {
            for (; i + 1 < servers_n; i++)
                copy((u8 *)servers[i], (const u8 *)servers[i + 1], JOIN_NAME + 1);
            servers_n--;
            break;
        }
}

/* servers.ini is small; trust.text is free until the next `up` loads it again. Its last section is
 * the newest, so sections are added oldest first, each in front. */
static void servers_read(void)
{
    char server[JOIN_NAME + 1];
    IO_STATUS_BLOCK iosb;
    u64 offset = 0, size;
    u32 off, n, len = 0;
    void *h;

    servers_n = 0;
    if (!bulk_open(trust_path, GENERIC_READ, FILE_OPEN, 0, &h)) {
        size = bulk_size(h);
        if (size && size < TRUST_TEXT &&
            !NtReadFile(h, 0, 0, 0, &iosb, trust.text, (u32)size, &offset))
            len = iosb.Information;
        NtClose(h);
        trust.loaded = 0;
    }
    for (off = 0; off < len; off += n + 1) {
        const char *line = trust.text + off;
        n = line_length(line, len - off);
        if (n > 2 && line[0] == '[' && line[n - 1] == ']')
            server_add(line + 1, n - 2);
    }
    if ((n = ini_text("NetServer", server, sizeof(server))))
        server_add(server, n);
}

static u8 *menu_part(u8 *menu, const char *name)
{
    return ((fn_find_child)TES3X_NET_FIND_CHILD)(menu, ((fn_ui_id)TES3X_NET_UI_ID)(name));
}

/* Show one of a button's three images, as the engine's own handlers do, and lay out its menu:
 * not the root above the menus, which in a game lays the HUD out again in the wrong place. */
static void button_show(u8 *button, u32 which)
{
    fn_ui_id ui_id = (fn_ui_id)TES3X_NET_UI_ID;
    fn_find_child child = (fn_find_child)TES3X_NET_FIND_CHILD;
    u8 *image, *root = button, *up;
    u32 i;

    if (!plausible(button))
        return;
    for (i = 0; i < 3; i++)
        if (plausible(image = child(button, ui_id(button_states[i]))))
            ((fn_set_visible)TES3X_NET_SET_VISIBLE)(image, i == which);
    while (plausible(up = *(u8 **)(root + UI_PARENT)) && plausible(*(u8 **)(up + UI_PARENT)))
        root = up;
    ((fn_layout)TES3X_NET_PERFORM_LAYOUT)(root, 1);
}

/* A handler is given the menu as owner and the widget hit as source, which may be a part of the
 * button or row: the widget holding a part named so. */
static u8 *event_widget(void *source, const char *part)
{
    u32 id = ((fn_ui_id)TES3X_NET_UI_ID)(part), depth;
    u8 *el = source, *found;

    for (depth = 0; plausible(el) && depth < 4; depth++, el = *(u8 **)(el + UI_PARENT))
        if (plausible(found = ((fn_find_child)TES3X_NET_FIND_CHILD)(el, id)) && found != el)
            return el;
    return 0;
}

static char __cdecl button_pressed(void *owner, u32 id, int d0, int d1, void *source)
{
    (void)owner, (void)id, (void)d0, (void)d1;
    button_show(event_widget(source, "TES3X_pressed"), 2);
    return 1;
}

static char __cdecl button_over(void *owner, u32 id, int d0, int d1, void *source)
{
    (void)owner, (void)id, (void)d0, (void)d1;
    button_show(event_widget(source, "TES3X_pressed"), 1);
    return 1;
}

static char __cdecl button_left(void *owner, u32 id, int d0, int d1, void *source)
{
    (void)owner, (void)id, (void)d0, (void)d1;
    button_show(event_widget(source, "TES3X_pressed"), 0);
    return 1;
}

static void focusable(u8 *block, int width, fn_ui_handler click)
{
    fn_set_prop set = (fn_set_prop)TES3X_NET_SET_PROP;

    ((fn_set_size)TES3X_NET_SET_WIDTH)(block, width);
    ((fn_set_size)TES3X_NET_SET_HEIGHT)(block, MENU_ROW);
    set(block, UI_FLAG_B, 0, UI_PROP_INT);
    set(block, UI_FOCUS_A, (int)UI_TRUE, UI_PROP_ENUM);
    set(block, UI_FOCUS_B, (int)UI_TRUE, UI_PROP_ENUM);
    set(block, UI_CLICK, (int)click, UI_PROP_HANDLER);
}

/* A button as the main menu builds its own: a block holding Textures\NAME.tga, NAME_over and
 * NAME_pressed, the last two hidden until the pad or the pointer reaches it. */
static u8 *menu_button(u8 *parent, u32 id, const char *name, int width, fn_ui_handler click)
{
    static const char *const suffix[3] = {"", "_over", "_pressed"};
    fn_set_prop set = (fn_set_prop)TES3X_NET_SET_PROP;
    fn_ui_id ui_id = (fn_ui_id)TES3X_NET_UI_ID;
    char path[64];
    u8 *block, *image;
    u32 i;

    if (!plausible(block = ((fn_create_block)TES3X_NET_CREATE_BLOCK)(parent, id, 0)))
        return 0;
    focusable(block, width, click);
    set(block, UI_PRESS, (int)button_pressed, UI_PROP_HANDLER);
    set(block, UI_OVER, (int)button_over, UI_PROP_HANDLER);
    set(block, UI_LEAVE, (int)button_left, UI_PROP_HANDLER);
    for (i = 0; i < 3; i++) {
        *put_text(put_text(put_text(put_text(path, "Textures\\"), name), suffix[i]), ".tga") = 0;
        if (!plausible(image = ((fn_create_image)TES3X_NET_CREATE_IMAGE)(
                           block, ui_id(button_states[i]), path, 0)))
            continue;
        ((fn_set_size)TES3X_NET_SET_WIDTH)(image, width);
        ((fn_set_size)TES3X_NET_SET_HEIGHT)(image, MENU_ROW);
        image[UI_IMAGE_FLAG] = 0;
        set(image, UI_FLAG_B, 0, UI_PROP_INT);
        if (i)
            ((fn_set_visible)TES3X_NET_SET_VISIBLE)(image, 0);
    }
    return block;
}

/* A server's row: its address in the gold of the buttons, brighter while highlighted. */
static void row_colour(u8 *row, int lit)
{
    u8 *label;

    if (!plausible(row) || !plausible(label = menu_part(row, "TES3X_label")))
        return;
    copy(label + UI_COLOUR, (const u8 *)(lit ? gold_lit : gold), 12);
    label[UI_COLOUR_CHANGED] = 1;
    button_show(row, 0);
}

static char __cdecl row_over(void *owner, u32 id, int d0, int d1, void *source)
{
    (void)owner, (void)id, (void)d0, (void)d1;
    row_colour(event_widget(source, "TES3X_label"), 1);
    return 1;
}

static char __cdecl row_left(void *owner, u32 id, int d0, int d1, void *source)
{
    (void)owner, (void)id, (void)d0, (void)d1;
    row_colour(event_widget(source, "TES3X_label"), 0);
    return 1;
}

/* A row's text, in the small font when it has a ':', which the big one draws as ';'. */
static void row_label(u8 *row, const char *text)
{
    u8 *label = menu_part(row, "TES3X_label");
    u32 i;

    if (!plausible(label))
        return;
    for (i = 0; text[i] && text[i] != ':'; i++)
        ;
    *(int *)(label + UI_FONT) = !text[i];
    ((fn_set_text)TES3X_NET_WIDGET_SET_TEXT)(label, text);
}

/* The row holding el, which may be the row, its label or the menu's own element. */
static int server_row(const u8 *el)
{
    u8 *menu = ((fn_find_menu)TES3X_NET_FIND_MENU)(((fn_ui_id)TES3X_NET_UI_ID)("MenuOptions"));
    u32 depth;
    int i;

    for (depth = 0; plausible(el) && plausible(menu) && depth < 4;
         depth++, el = *(const u8 *const *)(el + UI_PARENT))
        for (i = 0; i < SERVERS_SHOWN; i++)
            if (menu_part(menu, server_rows[i]) == el)
                return i;
    return -1;
}

static char __cdecl join_click(void *owner, u32 id, int d0, int d1, void *source)
{
    (void)owner, (void)id, (void)d0, (void)d1, (void)source;
    server_chosen = SERVER_OPEN;
    return 1;
}

static char __cdecl manager_click(void *owner, u32 id, int d0, int d1, void *source)
{
    (void)owner, (void)id, (void)d0, (void)d1, (void)source;
    server_chosen = SERVER_MANAGER;
    return 1;
}

static char __cdecl row_click(void *owner, u32 id, int d0, int d1, void *source)
{
    (void)id, (void)d0, (void)d1;
    server_chosen = server_row(source);
    tes3x_log_hex3("net.join_row", (u32)owner, (u32)source, (u32)server_chosen);
    return 1;
}

static char __cdecl new_server_click(void *owner, u32 id, int d0, int d1, void *source)
{
    (void)owner, (void)id, (void)d0, (void)d1, (void)source;
    server_chosen = SERVER_NEW;
    return 1;
}

static char __cdecl return_click(void *owner, u32 id, int d0, int d1, void *source)
{
    (void)owner, (void)id, (void)d0, (void)d1, (void)source;
    server_chosen = SERVER_RETURN;
    return 1;
}

static void typing_text(u8 *menu, const char *text)
{
    char line[JOIN_NAME + 2];
    u32 i;

    for (i = 0; text[i] && i < JOIN_NAME; i++)
        line[i] = typing == TYPE_PASSWORD ? '*' : text[i];
    line[i++] = '_';
    line[i] = 0;
    ((fn_set_text)TES3X_NET_WIDGET_SET_TEXT)(menu_part(menu, "TES3X_TypeText"), line);
    ((fn_layout)TES3X_NET_PERFORM_LAYOUT)(menu, 1);
}

/* A box with the thin border the settings lists have, its children top to bottom. */
static u8 *bordered(u8 *parent, const char *name, int width, int height)
{
    fn_set_prop set = (fn_set_prop)TES3X_NET_SET_PROP;
    u8 *box = ((fn_create_nif)TES3X_NET_CREATE_NIF)(parent, ((fn_ui_id)TES3X_NET_UI_ID)(name),
                                                    "menu_thin_border.nif", 0);

    if (!plausible(box))
        return 0;
    ((fn_set_size)TES3X_NET_SET_WIDTH)(box, width);
    ((fn_set_size)TES3X_NET_SET_HEIGHT)(box, height);
    set(box, UI_FLOW, (int)UI_TOP_DOWN, UI_PROP_ENUM);
    set(box, UI_CHILD_ALIGN_X, UI_HALF, UI_PROP_FLOAT);
    return box;
}

static u8 *gold_label(u8 *parent, const char *name, const char *text)
{
    u8 *label = ((fn_create_label)TES3X_NET_CREATE_LABEL)(
        parent, ((fn_ui_id)TES3X_NET_UI_ID)(name), text, 0, 0);

    if (plausible(label)) {
        *(int *)(label + UI_FONT) = 1;
        copy(label + UI_COLOUR, (const u8 *)gold, 12);
    }
    return label;
}

/* The list's box, New Server and Return go at the end of the column, hidden; the box that shows
 * what is typed too. */
static void servers_build(u8 *column, int width)
{
    fn_set_prop set = (fn_set_prop)TES3X_NET_SET_PROP;
    fn_ui_id ui_id = (fn_ui_id)TES3X_NET_UI_ID;
    fn_set_visible visible = (fn_set_visible)TES3X_NET_SET_VISIBLE;
    u8 *box, *pane, *content, *row, *text;
    u32 i;

    if (plausible(box = bordered(column, "TES3X_TypeBox", 3 * width, 2 * LIST_ROW + 2 * BOX_PAD))) {
        /* the small font: the big one draws ':' as ';' */
        if (plausible(text = gold_label(box, "TES3X_TypeTitle", "IP/DNS:PORT")))
            *(int *)(text + UI_FONT) = 0;
        if (plausible(text = gold_label(box, "TES3X_TypeText", "_")))
            *(int *)(text + UI_FONT) = 0;
        visible(box, 0);
    }
    if (!plausible(box = bordered(column, "TES3X_ServerBox", 3 * width,
                                  LIST_VISIBLE * LIST_ROW + 2 * BOX_PAD)))
        return;
    pane = ((fn_create_widget)TES3X_NET_CREATE_WIDGET)(box, ui_id("TES3X_ServerPane"),
                                                       TES3X_NET_SCROLL_PANE, 0);
    if (!plausible(pane))
        return;
    ((fn_set_size)TES3X_NET_SET_WIDTH)(pane, 3 * width - 2 * BOX_PAD);
    ((fn_set_size)TES3X_NET_SET_HEIGHT)(pane, LIST_VISIBLE * LIST_ROW);
    content = menu_part(pane, "PartScrollPane_pane");
    if (!plausible(content))
        content = pane;
    set(content, UI_FLOW, (int)UI_TOP_DOWN, UI_PROP_ENUM);
    set(content, UI_CHILD_ALIGN_X, UI_HALF, UI_PROP_FLOAT);
    for (i = 0; i < SERVERS_SHOWN; i++) {
        if (!plausible(row = ((fn_create_block)TES3X_NET_CREATE_BLOCK)(
                           content, ui_id(server_rows[i]), 0)))
            continue;
        focusable(row, width, row_click);
        ((fn_set_size)TES3X_NET_SET_HEIGHT)(row, LIST_ROW);
        ((fn_set_auto)TES3X_NET_SET_AUTO_WIDTH)(row, 1); /* fitted to the name, so centred */
        set(row, UI_CHILD_ALIGN_Y, UI_HALF, UI_PROP_FLOAT);
        set(row, UI_OVER, (int)row_over, UI_PROP_HANDLER);
        set(row, UI_LEAVE, (int)row_left, UI_PROP_HANDLER);
        gold_label(row, "TES3X_label", "-");
    }
    visible(box, 0);
#ifdef TES3X_CONSOLE
    if (plausible(row = menu_button(column, ui_id("TES3X_NewServer"), "menu_newserver", 2 * width,
                                    new_server_click)))
        visible(row, 0);
#endif
    if (plausible(row = menu_button(column, ui_id("TES3X_Return"), "menu_return", width,
                                    return_click)))
        visible(row, 0);
}

/* Show the main column or the server list, link the pad's focus through what shows and put it on
 * the first. */
static void servers_show(u8 *menu, u32 on)
{
    fn_set_visible visible = (fn_set_visible)TES3X_NET_SET_VISIBLE;
    fn_set_prop set = (fn_set_prop)TES3X_NET_SET_PROP;
    u16 up = *(const u16 *)TES3X_NET_NAV_UP_ID, down = *(const u16 *)TES3X_NET_NAV_DOWN_ID;
    u8 *shown[SERVERS_SHOWN + 2], *part;
    u32 i, n = 0;

    for (i = 0; i < MENU_ROWS; i++)
        if (plausible(part = menu_part(menu, main_rows[i])))
            visible(part, !on);
    if (plausible(part = menu_part(menu, "TES3X_ServerBox")))
        visible(part, on);
    for (i = 0; i < SERVERS_SHOWN; i++)
        if (plausible(part = menu_part(menu, server_rows[i]))) {
            visible(part, on && i < servers_n);
            if (on && i < servers_n) {
                row_label(part, servers[i]);
                row_colour(part, 0);
                shown[n++] = part;
            }
        }
    if (plausible(part = menu_part(menu, "TES3X_NewServer"))) {
        visible(part, on);
        if (on)
            shown[n++] = part;
    }
    if (plausible(part = menu_part(menu, "TES3X_Return"))) {
        visible(part, on);
        if (on)
            shown[n++] = part;
    }
    servers_view = on;
    for (i = 0; i < n; i++) {
        set(shown[i], up, (int)shown[(i + n - 1) % n], UI_PROP_PTR);
        set(shown[i], down, (int)shown[(i + 1) % n], UI_PROP_PTR);
    }
    /* the menu's width is set, not fitted: the box is wider than a button */
    ((fn_set_size)TES3X_NET_SET_WIDTH)(menu, (int)(on ? 3 * menu_width + 2 * BOX_PAD : menu_width));
    ((fn_layout)TES3X_NET_PERFORM_LAYOUT)(menu, 1);
    part = on ? (n ? shown[0] : 0) : menu_part(menu, "TES3X_Join");
    if (plausible(part))
        ((fn_set_focus)TES3X_NET_SET_FOCUS)(part, 1);
}

/* While the keyboard is up the menu shows only what is typed, moved up above the keyboard. */
static void typing_show(u8 *menu, u32 on)
{
    static const char *const list[3] = {"TES3X_ServerBox", "TES3X_NewServer", "TES3X_Return"};
    static float bottom;
    fn_set_visible visible = (fn_set_visible)TES3X_NET_SET_VISIBLE;
    u8 *part;
    u32 i;

    for (i = 0; i < 3; i++)
        if (plausible(part = menu_part(menu, list[i])))
            visible(part, !on);
    if (plausible(part = menu_part(menu, "TES3X_TypeBox")))
        visible(part, on);
    if (on && plausible(part = menu_part(menu, "TES3X_TypeTitle")))
        ((fn_set_text)TES3X_NET_WIDGET_SET_TEXT)(part, typing == TYPE_PASSWORD ? "PASSWORD" :
                                                 "IP/DNS:PORT");
    if (on) {
        bottom = *(float *)(menu + UI_ALIGN_Y);
        *(float *)(menu + UI_ALIGN_Y) = TYPE_ALIGN_Y;
        typing_text(menu, "");
    } else {
        *(float *)(menu + UI_ALIGN_Y) = bottom;
        servers_show(menu, 1);
    }
    ((fn_layout)TES3X_NET_PERFORM_LAYOUT)(menu, 1);
}

static void row_text(u8 *menu, u8 *row, const char *a, const char *b, const char *c)
{
    char text[JOIN_NAME + 40];

    if (!plausible(row))
        return;
    *put_text(put_text(put_text(text, a), b), c) = 0;
    row_label(row, text);
    ((fn_layout)TES3X_NET_PERFORM_LAYOUT)(menu, 1);
}

/* 1 if the session in hand, or the one being opened, is to this server (NetServer's text,
 * fingerprint aside, as trust_configure keeps it). */
static int session_for(const char *server)
{
    u32 i;

    if (ses.state == SESSION_IDLE || ses.state == SESSION_REFUSED ||
        ses.state == SESSION_UNTRUSTED || (!ses.server && !ses.host[0]))
        return 0;
    for (i = 0; trust.name[i] && (trust.name[i] | 0x20) == (server[i] | 0x20); i++)
        ;
    return !trust.name[i] && (!server[i] || server[i] == '#');
}

static void join_server_now(u8 *menu, const char *server, u8 *row)
{
    if (lobby)
        return;
    joins_pressed++;
    if (server != join_server)
        copy((u8 *)join_server, (const u8 *)server, tes3x_strlen(server) + 1);
    lobby = 1;
    join_row = row;
    join_started = now_us();
    log_text("net.join", join_server);
    row_text(menu, row, "Joining ", join_server, "");
    /* NetAddress alone brings the NIC up without a session; NetServer's may be another server */
    if (!net.up || !session_for(join_server))
        autostart();
}

static void join_failed(u8 *menu, const char *why)
{
    u32 flags = lock();

    if (ses.state != SESSION_JOINED && ses.state != SESSION_REFUSED &&
        ses.state != SESSION_UNTRUSTED) {
        ses.state = SESSION_IDLE;
        handshake_reset();
    }
    unlock(flags);
    lobby = 0;
    join_failures++;
    log_text("net.join_failed", why);
    row_text(menu, join_row, join_server, " - ", why);
    join_row = 0;
}

static void password_ask(u8 *menu)
{
    lobby = 0;
#ifdef TES3X_CONSOLE
    if (tes3x_console_text_begin_on(menu, "")) {
        typing = TYPE_PASSWORD;
        typing_show(menu, 1);
        tes3x_log("net.join_password", trust.password_n);
        return;
    }
#endif
    join_failed(menu, "password needed");
}

/* The manager's launch data when the server refused this build as stale: it opens that server. */
#define HANDOFF_MAGIC 0x484D3354u /* "T3MH" */
#define HANDOFF_STALE 1u
#define HANDOFF_ASKING 1u /* the box offers the manager */
#define HANDOFF_GOING 2u
static u32 handoff;

/* The box's answer; then, once servers.ini holds this console's key for the server, which the
 * manager asks with, the manager. */
static void handoff_frame(u8 *menu)
{
    static u8 data[8 + JOIN_NAME + 1];
    int button = *(int *)TES3X_NET_BUTTON;
    u32 i;

    if (handoff == HANDOFF_ASKING && button >= 0) {
        *(int *)TES3X_NET_BUTTON = -1;
        handoff = button == 0 ? HANDOFF_GOING : 0;
        tes3x_log("net.handoff_answer", (u32)button);
        if (!handoff)
            row_text(menu, join_row, join_server, " - ", "build out of date");
        if (!handoff)
            join_row = 0;
    }
    if (handoff != HANDOFF_GOING ||
        (net.up && ((trust.dirty && trust.has_server) || trust_job.pending)))
        return;
    handoff = 0;
    put32le(data, HANDOFF_MAGIC);
    put32le(data + 4, HANDOFF_STALE);
    for (i = 0; join_server[i]; i++)
        data[8 + i] = (u8)join_server[i];
    data[8 + i] = 0;
    log_text("net.handoff", manager_path);
    multi_closing();
    tes3x_launch_data(manager_path, data, sizeof(data));
}

/* A launch the manager's Join began: the server's refusal comes up as a box, then the manager or
 * the main menu, instead of waiting out the arrival and landing on the prison ship. */
static u32 refusal_asked, refusal_done;

static const char *const refusal_why[8] = {
    "The server refused the connection.", "Your mods differ from the server's.",
    "The server is full.", "Wrong password. Set it in the TES3X Manager.",
    "You were kicked from the server.", "This console is banned from the server.",
    "Build out of date. Update it in the TES3X Manager.",
    "Update this game build in the TES3X Manager to connect."};

static const char *refusal_text(u32 reason)
{
    if (reason == REFUSED_PROTOCOL && ses.refused_hash < T3MP_VERSION)
        return "The server needs an update to work with this game build.";
    return refusal_why[reason < 8 ? reason : 0];
}

static void refusal_frame(void)
{
    static u8 data[8 + JOIN_NAME + 1];
    u32 state = ses.state, reason = ses.refused_reason, i;
    int button;

    if (!join_server[0] || refusal_done)
        return;
    if (refusal_asked) {
        if ((button = *(int *)TES3X_NET_BUTTON) < 0)
            return;
        *(int *)TES3X_NET_BUTTON = -1;
        refusal_asked = 0;
        refusal_done = 1;
        tes3x_log("net.refusal_answer", (u32)button);
        if (button != 0 || !manager_path[0]) {
            ((fn_quit)TES3X_NET_QUIT)();
            return;
        }
        multi_closing();
        if ((reason == REFUSED_STALE || (reason == REFUSED_PROTOCOL &&
             ses.refused_hash > T3MP_VERSION)) && state == SESSION_REFUSED) {
            put32le(data, HANDOFF_MAGIC);
            put32le(data + 4, HANDOFF_STALE);
            for (i = 0; join_server[i]; i++)
                data[8 + i] = (u8)join_server[i];
            data[8 + i] = 0;
            tes3x_launch_data(manager_path, data, sizeof(data));
        } else {
            tes3x_launch_data(manager_path, 0, 0);
        }
        return;
    }
    if ((state != SESSION_REFUSED && state != SESSION_UNTRUSTED) || !world_idle() ||
        !player_reference())
        return;
    arrival_start = ARRIVAL_CLAIMED;
    log_text("net.join_failed", state == SESSION_REFUSED ? refusal_text(reason) : "key");
    *(int *)TES3X_NET_BUTTON = -1;
    ((fn_message_menu)TES3X_NET_MESSAGE_MENU)(
        state == SESSION_UNTRUSTED ? "The server's key has changed since this console first met it."
                                   : refusal_text(reason),
        manager_path[0] ? "Open Manager" : "Main Menu", manager_path[0] ? "Main Menu" : (const char *)0,
        (const char *)0);
    refusal_asked = 1;
}

static void welcome_frame(void)
{
    static u32 asked;

    if (asked) {
        if (*(int *)TES3X_NET_BUTTON < 0)
            return;
        *(int *)TES3X_NET_BUTTON = -1;
        asked = 0;
        return;
    }
    if (!welcome_pending || ses.state != SESSION_JOINED || !world_idle())
        return;
    welcome_pending = 0;
    *(int *)TES3X_NET_BUTTON = -1;
    ((fn_message_menu)TES3X_NET_MESSAGE_MENU)(welcome_text, "Continue", (const char *)0,
                                              (const char *)0);
    asked = 1;
}

/* A session that was joined and is over: the server refused it (a kick or a ban), or it stayed
 * unreachable for DROP_US. A box says so and leaves for the main menu. */
#define DROP_US 60000000u

static void session_ended_frame(void)
{
    static u32 joined_once, lost_timing, lost_since, asked;
    const char *text = 0;

    if (ses.state == SESSION_JOINED) {
        joined_once = 1;
        lost_timing = 0;
        return;
    }
    if (asked) {
        if (*(int *)TES3X_NET_BUTTON < 0 || asked == 2)
            return;
        *(int *)TES3X_NET_BUTTON = -1;
        asked = 2;
        ((fn_quit)TES3X_NET_QUIT)();
        return;
    }
    if (!joined_once || ses.state == SESSION_UNTRUSTED)
        return;
    if (ses.state == SESSION_REFUSED) {
        if (join_server[0]) /* refusal_frame's */
            return;
        text = refusal_text(ses.refused_reason);
    } else {
        if (!lost_timing) {
            lost_timing = 1;
            lost_since = now_us();
        }
        if (now_us() - lost_since >= DROP_US)
            text = "The connection to the server was lost.";
    }
    if (!text || !world_idle())
        return;
    log_text("net.session_ended", text);
    *(int *)TES3X_NET_BUTTON = -1;
    ((fn_message_menu)TES3X_NET_MESSAGE_MENU)(text, "Main Menu", (const char *)0,
                                              (const char *)0);
    asked = 1;
}

/* Until WELCOME: what stopped the join, on its row. */
static void join_watch(u8 *menu)
{
    static const char *const refused[8] = {"refused", "different mods", "server full",
                                           "wrong password", "kicked", "banned",
                                           "build out of date", "client needs update"};
    u32 state = ses.state, reason = ses.refused_reason;

    handoff_frame(menu);
    if (!lobby || !join_row)
        return;
    if (state == SESSION_JOINED) {
        join_row = 0;
    } else if (state == SESSION_REFUSED && reason == 3) {
        password_ask(menu);
    } else if (state == SESSION_REFUSED && (reason == REFUSED_STALE ||
                 (reason == REFUSED_PROTOCOL && ses.refused_hash > T3MP_VERSION)) && manager_path[0]) {
        lobby = 0;
        handoff = HANDOFF_ASKING;
        log_text("net.join_failed", reason == REFUSED_PROTOCOL ?
                                      "client protocol needs update" : "build out of date");
        *(int *)TES3X_NET_BUTTON = -1;
        ((fn_message_menu)TES3X_NET_MESSAGE_MENU)(
            reason == REFUSED_PROTOCOL ? refusal_text(reason) :
                "Build out of date. Update it in the TES3X Manager?", "Open Manager", "Back",
            (const char *)0);
    } else if (state == SESSION_REFUSED) {
        join_failed(menu, reason == REFUSED_PROTOCOL && ses.refused_hash < T3MP_VERSION ?
                            "server needs update" : refused[reason < 8 ? reason : 0]);
    } else if (state == SESSION_UNTRUSTED) {
        join_failed(menu, "server key changed");
    } else if (now_us() - join_started > JOIN_WAIT_US) {
        join_failed(menu, state == SESSION_DHCP ? "no network address" :
                          state == SESSION_RESOLVE ? "name not found" :
                          state == SESSION_ARP ? "no route" : "no answer");
    }
}

/* The D-pad moves focus along NAV links without the events that light a button, so they are sent
 * here, for the main column as well as the list; on a server row the list scrolls to show it. */
static void list_follow(u8 *menu)
{
    fn_trigger_event trigger = (fn_trigger_event)TES3X_NET_TRIGGER_EVENT;
    static u8 *list_menu;
    u8 *focus = ((fn_get_focus)TES3X_NET_GET_FOCUS)(menu);

    if (menu != list_menu) /* a menu made again frees the one the focus was in */
        list_focus = 0;
    list_menu = menu;
    if (focus == list_focus)
        return;
    if (plausible(list_focus))
        trigger(list_focus, UI_LEAVE, 0, 0, list_focus);
    if (plausible(focus))
        trigger(focus, UI_OVER, 0, 0, focus);
    list_focus = focus;
    if (plausible(focus) && server_row(focus) >= 0) {
        ((fn_scroll_to)TES3X_NET_SCROLL_TO)(focus);
        list_follows++;
    }
}

/* New server: whatever was typed, without spaces, is the server to join. A password goes into
 * the server's section of servers.ini and the join is made again. */
static void typing_frame(u8 *menu)
{
#ifdef TES3X_CONSOLE
    char text[JOIN_NAME + 1];
    const char *now = tes3x_console_text_now();
    u8 *field = tes3x_console_text_field();
    u32 i, n = 0, kind = typing;
    int status = tes3x_console_text_poll(text, sizeof(text));

    if (!status) {
        if (now)
            typing_text(menu, now);
        /* the keyboard echoes what is typed above its keys; the box shows a password masked */
        if (kind == TYPE_PASSWORD && plausible(field) && field[MENU_VISIBLE])
            ((fn_set_visible)TES3X_NET_SET_VISIBLE)(field, 0);
        return;
    }
    typing = 0;
    typing_show(menu, 0);
    if (kind == TYPE_PASSWORD) {
        for (n = 0; status == 2 && text[n] && n < PASSWORD_MAX; n++)
            trust.password[n] = text[n];
        crypto_wipe(text, sizeof(text));
        if (!n) {
            join_failed(menu, "password needed");
            return;
        }
        trust.password_n = n;
        trust.dirty = 1;
        join_server_now(menu, join_server, join_row);
        return;
    }
    if (status != 2)
        return;
    for (i = 0; text[i]; i++)
        if (text[i] != ' ')
            text[n++] = text[i];
    text[n] = 0;
    log_text("net.join_typed", text);
    if (!n)
        return;
    server_add(text, n);
    servers_show(menu, 1);
    join_server_now(menu, servers[0], menu_part(menu, server_rows[0]));
#else
    (void)menu;
    typing = 0;
#endif
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

static void join_frame(void)
{
    fn_ui_id ui_id = (fn_ui_id)TES3X_NET_UI_ID;
    fn_set_prop set = (fn_set_prop)TES3X_NET_SET_PROP;
    u16 up = *(const u16 *)TES3X_NET_NAV_UP_ID, down = *(const u16 *)TES3X_NET_NAV_DOWN_ID;
    u8 *menu, *exit, *options, *load, *column, *button, *manager;
    int chosen;

    if (player_reference())
        return; /* a game is loaded: Load and New relaunch, and the pause menu has no Join */
    if (!plausible(menu = ((fn_find_menu)TES3X_NET_FIND_MENU)(ui_id("MenuOptions"))))
        return;
    if (!plausible(button = menu_part(menu, "TES3X_Join"))) {
        exit = menu_part(menu, "MenuOptions_Exit_container");
        options = menu_part(menu, "MenuOptions_Options_container");
        load = menu_part(menu, "MenuOptions_Load_container");
        if (!plausible(exit) || !plausible(options) ||
            !plausible(column = *(u8 **)(exit + UI_PARENT)) ||
            !plausible(button = menu_button(column, ui_id("TES3X_Join"), "menu_join",
                                            *(const int *)(exit + UI_WIDTH), join_click)))
            return;
        /* the menu's height is set, not fitted: a row more for each button added */
        menu_height = *(const int *)(menu + UI_HEIGHT) + MENU_ROW;
        menu_width = *(const int *)(menu + UI_WIDTH);
        column_before(column, button, options);
        set(button, down, (int)options, UI_PROP_PTR);
        set(options, up, (int)button, UI_PROP_PTR);
        if (plausible(load)) {
            set(button, up, (int)load, UI_PROP_PTR);
            set(load, down, (int)button, UI_PROP_PTR);
        }
        tes3x_ini_xbox("Manager", "", manager_path, sizeof(manager_path));
        if (manager_path[0] &&
            plausible(manager = menu_button(column, ui_id("TES3X_Manager"), "menu_manager",
                                            *(const int *)(exit + UI_WIDTH), manager_click))) {
            menu_height += MENU_ROW;
            column_before(column, manager, options);
            set(button, down, (int)manager, UI_PROP_PTR);
            set(manager, up, (int)button, UI_PROP_PTR);
            set(manager, down, (int)options, UI_PROP_PTR);
            set(options, up, (int)manager, UI_PROP_PTR);
        }
        ((fn_set_size)TES3X_NET_SET_HEIGHT)(menu, (int)menu_height);
        servers_build(column, *(const int *)(exit + UI_WIDTH));
        servers_view = 0;
        ((fn_layout)TES3X_NET_PERFORM_LAYOUT)(menu, 0);
        join_buttons++;
    }
    if (typing) {
        typing_frame(menu);
        return;
    }
    join_watch(menu);
    if (typing)
        return;
    list_follow(menu);
    chosen = server_chosen;
    server_chosen = -1;
    if (chosen == SERVER_OPEN) {
        servers_read();
        servers_show(menu, 1);
    } else if (chosen == SERVER_RETURN) {
        servers_show(menu, 0);
    } else if (chosen == SERVER_MANAGER) {
        log_text("net.manager", manager_path);
        multi_closing();
        tes3x_launch(manager_path);
#ifdef TES3X_CONSOLE
    } else if (chosen == SERVER_NEW) {
        typing = tes3x_console_text_begin_on(menu, "") ? TYPE_SERVER : 0;
        tes3x_log("net.join_keyboard", typing);
        if (typing)
            typing_show(menu, 1);
#endif
    } else if (chosen >= 0 && chosen < (int)servers_n) {
        join_server_now(menu, servers[chosen], menu_part(menu, server_rows[chosen]));
    }
}

static void join_stat(void)
{
    tes3x_log_hex3("net.join_menu", join_buttons, joins_pressed, lobby);
    tes3x_log_hex3("net.join_servers", servers_n, servers_view, typing);
    tes3x_log_hex3("net.join_failures", join_failures, list_follows, trust.password_n);
}
