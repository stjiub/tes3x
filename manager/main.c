/* TES3X manager: lists the TES3X builds on the console, shows what each holds, checks them
 * against their manifests and starts them. E:\tes3xmgr.txt logs what it does.
 *
 * E:\tes3xmgrexec.txt, when present, is read once at start and deleted: one command a line,
 * run in order without input, for unattended tests:
 *   list | verify NAME | launch NAME | rebuild NAME XBE DELTA | agent SECONDS
 *   | base [use N | path FOLDER] | update | fetch [FEED] | get URL FILE | run XBE
 *   | servers | server add NAME | install SERVER [replace] | join SERVER | wait SECONDS
 *   | press BUTTON... | type TEXT | shutdown | reboot
 * `base` lists the retail bases found; `base use N` makes the Nth one OverlayBase, `base path`
 * the folder named by the rest of the line, as choosing it on screen does. `rebuild`
 * decodes DELTA against OverlayBase's morrowind.xbe. `update` installs a release waiting in
 * E:\TES3X\update, as a start does; `fetch` checks the update feed and installs from it; `get`
 * saves a URL to a file; `run` starts any XBE, leaving the lines after it for that XBE; `press`
 * presses controller buttons (a b x y up down left right l r start), for screenshots of the
 * screens; `type` types the rest of the line into the open keyboard.
 * `servers` lists the servers servers.ini knows; `server add` adds one ("host[:port]") to the
 * game's pool and opens its page; `install` installs or updates a server's build, `replace` going ahead over a
 * folder that holds something else; `join` starts that build joining the server. */

#include "mgr.h"
#include "screens.h"
#include "sha256.h"

#include <SDL.h>
#include <hal/video.h>
#include <hal/xbox.h>
#include <pbkit/pbkit.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <usbh_lib.h>
#include <windows.h>
#include <xboxkrnl/xboxkrnl.h>

#define LOG_PATH "E:\\tes3xmgr.txt"
#define EXEC_PATH "E:\\tes3xmgrexec.txt"
#define MAX_BUILDS 64
#define MAX_BASES 8
#define MAX_FOLDERS 256
#define MAX_SERVERS 32
/* the agent's status can change without input */
#define REDRAW_MS 500
#define TRIGGER_DOWN 16000

static struct build builds[MAX_BUILDS];
static struct base bases[MAX_BASES];
static struct folder folders[MAX_FOLDERS];
static char browse_path[PATH_MAX_MGR];
static struct line lines[MAX_LINES];
static struct server servers[MAX_SERVERS];
static const char *server_builds[MAX_SERVERS];
static char overlay_base[PATH_MAX_MGR];
static struct view v;
static int video_up, dirty = 1;
static Uint32 last_draw;
static const char *progress_name;

void mgr_log(const char *format, ...)
{
    va_list args;
    FILE *f = fopen(LOG_PATH, "a");

    if (!f)
        return;
    va_start(args, format);
    vfprintf(f, format, args);
    va_end(args);
    fclose(f);
}

static void add_line(const char *label, int style, const char *format, ...)
{
    struct line *l;
    va_list args;

    if (v.line_list.count >= MAX_LINES)
        return;
    l = &lines[v.line_list.count++];
    snprintf(l->label, sizeof(l->label), "%s", label);
    l->style = style;
    va_start(args, format);
    vsnprintf(l->text, sizeof(l->text), format, args);
    va_end(args);
}

static void say(const char *title, const char *a, const char *b)
{
    memset(v.text, 0, sizeof(v.text));
    snprintf(v.title, sizeof(v.title), "%s", title);
    snprintf(v.text[0], sizeof(v.text[0]), "%s", a ? a : "");
    snprintf(v.text[1], sizeof(v.text[1]), "%s", b ? b : "");
    v.dismiss = 1;
    dirty = 1;
}

/* Copies the canvas to the back buffer and shows it at the next vertical blank. */
static void present(void)
{
    DWORD *fb;
    int y, pitch;

    pb_wait_for_vbl();
    pb_target_back_buffer();
    pb_reset();
    while (pb_busy())
        ;
    fb = pb_back_buffer();
    pitch = pb_back_buffer_pitch() / 4;
    for (y = 0; y < GFX_H; y++)
        memcpy(fb + y * pitch, gfx_pixels + y * GFX_W, GFX_W * 4);
    while (pb_finished())
        ;
    last_draw = SDL_GetTicks();
}

static void draw(void)
{
    if (!video_up)
        return;
    v.agent = agent_status();
    ui_tick = SDL_GetTicks() / 100;
    screen_draw(&v);
    present();
    dirty = 0;
}

static void busy(const char *text)
{
    if (!video_up)
        return;
    v.agent = agent_status();
    ui_tick = SDL_GetTicks() / 100;
    screen_busy(&v, text);
    present();
    dirty = 1;
}

static int cancel_pressed(void)
{
    SDL_Event e;
    int cancel = 0;

    while (SDL_PollEvent(&e))
        if (e.type == SDL_CONTROLLERBUTTONDOWN && e.cbutton.button == SDL_CONTROLLER_BUTTON_B)
            cancel = 1;
        else if (e.type == SDL_CONTROLLERDEVICEADDED)
            SDL_GameControllerOpen(e.cdevice.which);
    return cancel;
}

void mgr_progress_title(const char *title)
{
    progress_name = title;
}

static int progress(const char *what, unsigned long long done, unsigned long long total)
{
    agent_poll();
    if (!video_up || SDL_GetTicks() - last_draw < 100)
        return 0;
    v.agent = agent_status();
    ui_tick = SDL_GetTicks() / 100;
    screen_progress(&v, progress_name, what, done, total);
    present();
    dirty = 1;
    return cancel_pressed();
}

static void scan(void)
{
    int i;

    busy("Looking for builds...");
    v.build_list.count = scan_builds(builds, MAX_BUILDS);
    v.build_list.sel = v.build_list.top = 0;
    mgr_log("scan: %d build(s)\n", v.build_list.count);
    for (i = 0; i < v.build_list.count; i++)
        mgr_log("  %s: profile '%s', %s layout, %d files, %llu bytes, %d plugins, %d xbe%s%s\n",
                builds[i].path, builds[i].profile, builds[i].layout, builds[i].files,
                builds[i].bytes, builds[i].plugins, builds[i].xbes,
                builds[i].error[0] ? ", " : "", builds[i].error);
}

/* Redraws the busy dialog, for work that cannot be cancelled but takes a while. */
static int spin(const char *what, unsigned long long done, unsigned long long total)
{
    char text[PATH_MAX_MGR + 16];

    (void)done, (void)total;
    agent_poll();
    if (video_up && SDL_GetTicks() - last_draw >= 100) {
        snprintf(text, sizeof(text), "Checking %s...", what);
        busy(text);
    }
    return 0;
}

static void find_retail_bases(void)
{
    int i;

    busy("Looking for a retail base...");
    v.base_count = find_bases(bases, MAX_BASES - 1, spin);
    /* a base chosen by folder is outside the search */
    for (i = 0; i < v.base_count && name_cmp(bases[i].path, overlay_base); i++)
        ;
    if (overlay_base[0] && i == v.base_count && !check_base(overlay_base, &bases[i]))
        v.base_count++;
    v.base_list.count = v.base_count + 1;
    v.base_list.sel = v.base_list.top = 0;
    v.base_current = -1;
    for (i = 0; i < v.base_count; i++) {
        mgr_log("base %d: %s, %s%s\n", i, bases[i].path,
                bases[i].installed ? "installed by TES3X" : "copied",
                bases[i].has_xbe ? ", retail XBE" : ", no retail XBE");
        if (!name_cmp(bases[i].path, overlay_base))
            v.base_current = v.base_list.sel = i;
    }
    ui_list_move(&v.base_list, 0);
}

static void use_base(const struct base *b)
{
    if (console_set("OverlayBase", b->path)) {
        say("Retail base", "Could not write " CONSOLE_INI ".", NULL);
        return;
    }
    /* the first run's choice leads on to the builds */
    if (!overlay_base[0])
        v.tab = TAB_BUILDS;
    snprintf(overlay_base, sizeof(overlay_base), "%s", b->path);
    v.base_current = (int)(b - bases);
    v.page = PAGE_MAIN;
    mgr_log("base: %s\n", b->path);
}

static void browse(const char *path)
{
    char from[PATH_MAX_MGR];

    snprintf(from, sizeof(from), "%s", path);
    snprintf(browse_path, sizeof(browse_path), "%s", from);
    busy("Reading folders...");
    v.folder_list.count = list_folders(browse_path, folders, MAX_FOLDERS);
    v.folder_list.sel = v.folder_list.top = 0;
    v.page = PAGE_BROWSE;
    mgr_log("browse: '%s', %d folder(s)\n", browse_path, v.folder_list.count);
}

static void browse_into(const struct folder *f)
{
    char path[PATH_MAX_MGR];

    if (browse_path[0])
        join_path(path, sizeof(path), browse_path, f->name);
    else
        snprintf(path, sizeof(path), "%s", f->name);
    browse(path);
}

/* Up a level, keeping the folder left selected; from the drives, back to the bases. */
static void browse_up(void)
{
    char path[PATH_MAX_MGR], *slash;
    const char *name;
    int i;

    if (!browse_path[0]) {
        v.page = PAGE_BASES;
        return;
    }
    snprintf(path, sizeof(path), "%s", browse_path);
    slash = strrchr(path, '\\');
    if (slash)
        *slash = 0;
    name = slash ? slash + 1 : path;
    browse(slash ? path : "");
    for (i = 0; i < v.folder_list.count; i++)
        if (!name_cmp(folders[i].name, name))
            v.folder_list.sel = i;
    ui_list_move(&v.folder_list, 0);
}

static void use_folder(const char *path)
{
    struct base b;
    const char *why = check_base(path, &b);
    int i;

    if (why) {
        mgr_log("base: %s refused: %s\n", path, why);
        say("Not a retail base", path, why);
        return;
    }
    for (i = 0; i < v.base_count && name_cmp(bases[i].path, b.path); i++)
        ;
    if (i == v.base_count) {
        i = v.base_count < MAX_BASES ? v.base_count++ : MAX_BASES - 1;
        v.base_list.count = v.base_count + 1;
    }
    bases[i] = b;
    use_base(&bases[i]);
    if (!b.installed)
        say("Retail base", b.path, "Not installed by TES3X: use it only if it is unmodded.");
}

static void details(const struct build *b)
{
    struct json j;
    char *text, s[96], d[16];
    int list, i, k, patches;
    unsigned title_id;
    char xbe[PATH_MAX_MGR];

    v.line_list.count = v.line_list.sel = v.line_list.top = 0;
    if (b->error[0])
        add_line("", LINE_BAD, "Cannot use: %s", b->error);
    add_line("Profile", 0, "%s, %s layout", b->profile, b->layout[0] ? b->layout : "full");
    ui_size(s, sizeof(s), b->bytes);
    add_line("Files", 0, "%d, %s%s%s", b->files, s, b->deployed[0] ? ", deployed " : "",
             b->deployed);
    join_path(xbe, sizeof(xbe), b->path, "default.xbe");
    if (!xbe_title_id(xbe, &title_id))
        add_line("Title ID", 0, "%08X", title_id);
    if (manifest_load(b, &text, &j)) {
        add_line("", LINE_BAD, "Manifest unreadable");
        return;
    }
    list = json_get(&j, 0, "xbe");
    for (i = json_child(&j, list); i >= 0; i = json_sibling(&j, list, i)) {
        json_string(&j, json_get(&j, i, "path"), s, sizeof(s));
        json_string(&j, json_get(&j, i, "retail_digest"), d, sizeof(d));
        k = json_get(&j, i, "patches");
        patches = k >= 0 ? j.t[k].count : 0;
        add_line("XBE", 0, "%s: %d patches, retail %.8s%s", s, patches, d,
                 json_get(&j, i, "delta") >= 0 ? ", delta" : "");
    }
    list = json_get(&j, 0, "plugins");
    add_line("", LINE_HEAD, "Plugins (%d)", b->plugins);
    for (i = json_child(&j, list); i >= 0; i = json_sibling(&j, list, i)) {
        json_string(&j, i, s, sizeof(s));
        add_line("", 0, "%s", s);
    }
    manifest_free(text, &j);
}

static void verify(const struct build *b)
{
    struct verify r;
    char counts[96];

    if (verify_build(b, &r, progress)) {
        say("Verify failed", "The manifest is unreadable.", NULL);
        return;
    }
    snprintf(counts, sizeof(counts), "%d ok, %d missing, %d changed.", r.ok, r.missing,
             r.changed);
    say(r.cancelled ? "Verify cancelled" : r.missing || r.changed ? "Build differs" : "Build verified",
        r.missing || r.changed || r.cancelled ? counts : "Every file matches its manifest.",
        r.missing || r.changed ? "The files are listed in " LOG_PATH "." : NULL);
}

static void leaving(void)
{
    agent_goodbye();
    net_down();
    /* SDL leaves the USB host running, and it would keep writing into the next title */
    usbh_core_deinit();
    if (video_up)
        pb_kill();
    video_up = 0;
}

static void launch(const struct build *b)
{
    const char *err = launch_build(b, leaving);

    mgr_log("launch %s failed: %s\n", b->path, err);
    say("Cannot launch", err, NULL);
}

void mgr_launch_xbe(const char *xbe)
{
    const char *err = launch_xbe(xbe, leaving);

    mgr_log("launch %s failed: %s\n", xbe, err);
    say("Cannot launch", err, NULL);
}

/* servers.ini's servers, each with the build installed from it. */
static void load_servers(void)
{
    int i, k;

    v.server_list.count = servers_load(servers, MAX_SERVERS);
    if (v.server_list.sel >= v.server_list.count)
        v.server_list.sel = v.server_list.top = 0;
    ui_list_move(&v.server_list, 0);
    for (i = 0; i < v.server_list.count; i++) {
        busy("Checking servers...");
        servers[i].online = server_probe(&servers[i]);
        server_builds[i] = NULL;
        for (k = 0; k < v.build_list.count; k++)
            if (!strcmp(builds[k].server, servers[i].name))
                server_builds[i] = builds[k].path;
        mgr_log("server %d: %s in %s, key %s%s%s\n", i, servers[i].name, servers[i].file,
                servers[i].has_server_key ? servers[i].fingerprint : "not pinned",
                server_builds[i] ? ", build " : "", server_builds[i] ? server_builds[i] : "");
    }
}

static void server_details(const struct server *s, const char *build)
{
    v.line_list.count = v.line_list.sel = v.line_list.top = 0;
    add_line("Address", 0, "%s, port %u", s->host, s->port);
    add_line("Server key", s->has_server_key ? 0 : LINE_BAD, "%s",
             s->has_server_key ? s->fingerprint : "not pinned: the first contact pins it");
    add_line("This console", 0, s->has_client_key ? "has an identity there"
                                                  : "not yet known there");
    if (s->password[0])
        add_line("Password", 0, "kept");
    add_line("Build", build ? 0 : LINE_BAD, "%s", build ? build : "not installed");
}

static struct server *chosen_server(void)
{
    return v.server_list.count ? &servers[v.server_list.sel] : NULL;
}

/* What the open keyboard is typing. */
enum { KB_SERVER, KB_PASSWORD };
static int kb_purpose, kb_then_install;

static void ask_server(void)
{
    ui_keyboard_open(&v.kb, "Add a server", "Its name or address, with :port when it is not 26500.",
                     NULL, sizeof(servers[0].name) - 1, 0);
    kb_purpose = KB_SERVER;
}

/* The password for the chosen server; then_install retries the update once it is typed. */
static void ask_password(const char *why, int then_install)
{
    struct server *s = chosen_server();
    char note[128];

    snprintf(note, sizeof(note), "%s%s%s", s->name, why ? ": " : "", why ? why : "");
    ui_keyboard_open(&v.kb, "Server password", note, s->password, sizeof(s->password) - 1, 1);
    kb_purpose = KB_PASSWORD;
    kb_then_install = then_install;
}

/* Selects a server, adding it to the game's pool first if no servers.ini has it, and opens its
 * page; nonzero if the name is not a server's. */
static int add_server(const char *name)
{
    struct server s;
    int i;

    for (i = 0; i < v.server_list.count && name_cmp(servers[i].name, name); i++)
        ;
    if (i == v.server_list.count) {
        if (server_add(name, &s))
            return -1;
        mgr_log("server added: %s\n", name);
        load_servers();
        for (i = 0; i < v.server_list.count && name_cmp(servers[i].name, name); i++)
            ;
        if (i == v.server_list.count)
            return -1;
    }
    v.tab = TAB_SERVERS;
    v.server_list.sel = i;
    ui_list_move(&v.server_list, 0);
    server_details(&servers[i], server_builds[i]);
    v.page = PAGE_SERVER;
    return 0;
}

static void install_server(struct server *s, int replace);

static void keyboard_done(void)
{
    struct server *s = chosen_server();
    char *text = v.kb.text;
    size_t n;

    if (kb_purpose == KB_SERVER) {
        text += strspn(text, " ");
        for (n = strlen(text); n && text[n - 1] == ' '; n--)
            text[n - 1] = 0;
        if (!*text) {
            v.kb.open = 0;
        } else if (add_server(text)) {
            /* the keyboard stays, to correct it */
            say("Not a server address", text,
                "Type a host name or IP address, with :port when the server does not use 26500.");
        } else {
            v.kb.open = 0;
        }
        return;
    }
    v.kb.open = 0;
    if (!s)
        return;
    snprintf(s->password, sizeof(s->password), "%s", text);
    memset(v.kb.text, 0, sizeof(v.kb.text));
    if (server_save(s)) {
        say("Password not kept", "Could not write", s->file);
        return;
    }
    mgr_log("server %s: password %s\n", s->name, s->password[0] ? "set" : "cleared");
    server_details(s, server_builds[v.server_list.sel]);
    if (kb_then_install)
        install_server(s, 0);
}

static void keyboard_press(int button)
{
    if (button == SDL_CONTROLLER_BUTTON_DPAD_UP || button == SDL_CONTROLLER_BUTTON_DPAD_DOWN)
        ui_keyboard_move(&v.kb, 0, button == SDL_CONTROLLER_BUTTON_DPAD_UP ? -1 : 1);
    else if (button == SDL_CONTROLLER_BUTTON_DPAD_LEFT || button == SDL_CONTROLLER_BUTTON_DPAD_RIGHT)
        ui_keyboard_move(&v.kb, button == SDL_CONTROLLER_BUTTON_DPAD_LEFT ? -1 : 1, 0);
    else if (button == SDL_CONTROLLER_BUTTON_A && ui_keyboard_press(&v.kb, ui_keyboard_key(&v.kb)))
        keyboard_done();
    else if (button == SDL_CONTROLLER_BUTTON_X)
        ui_keyboard_press(&v.kb, UI_KB_DELETE);
    else if (button == SDL_CONTROLLER_BUTTON_Y)
        ui_keyboard_press(&v.kb, UI_KB_SHIFT);
    else if (button == SDL_CONTROLLER_BUTTON_START)
        keyboard_done();
    else if (button == SDL_CONTROLLER_BUTTON_B)
        memset(&v.kb, 0, sizeof(v.kb));
}

/* The game's launch data when a server refused its build as stale and the player chose the
 * manager (HANDOFF_MAGIC in tes3xmulti.c): that server's page, where X updates. */
#define HANDOFF_MAGIC 0x484D3354u

static void handoff(void)
{
    PLAUNCH_DATA_PAGE page = LaunchDataPage;
    unsigned magic, reason;
    char name[sizeof(servers[0].name)];
    int i;

    if (!page || page->Header.dwLaunchDataType != LDT_TITLE)
        return;
    memcpy(&magic, page->LaunchData, 4);
    memcpy(&reason, page->LaunchData + 4, 4);
    if (magic != HANDOFF_MAGIC)
        return;
    snprintf(name, sizeof(name), "%.*s", (int)strcspn((char *)page->LaunchData + 8, "#"),
             (char *)page->LaunchData + 8);
    memset(page->LaunchData, 0, 8);
    mgr_log("handoff %u from the game: %s\n", reason, name);
    for (i = 0; i < v.server_list.count && name_cmp(servers[i].name, name); i++)
        ;
    if (i == v.server_list.count) {
        say("Server not known", name, "The game named a server no servers.ini holds.");
        return;
    }
    v.tab = TAB_SERVERS;
    v.server_list.sel = i;
    ui_list_move(&v.server_list, 0);
    server_details(&servers[i], server_builds[i]);
    v.page = PAGE_SERVER;
}

static void install_server(struct server *s, int replace)
{
    char folder[PATH_MAX_MGR], summary[128];
    const char *err;

    busy("Contacting the server...");
    err = server_install(s, replace, folder, sizeof(folder), summary, sizeof(summary), progress);
    scan();
    load_servers();
    if (v.page == PAGE_SERVER)
        server_details(s, server_builds[v.server_list.sel]);
    if (err == SERVER_PASSWORD) {
        ask_password(err, 1);
        return;
    }
    if (err == INSTALL_CONFIRM) {
        say("Replace this folder?", folder,
            "It holds files that are not a TES3X build. Installing removes nothing, but "
            "replaces any file the build has.");
        v.confirm = 1;
        return;
    }
    if (err)
        say("Update failed", err, NULL);
    else
        say("Build up to date", folder, summary);
}

/* Whether the build's manifest is the one the server hands out: as it is, or without the
 * "server" and "deployed" lines an install puts in front (write_manifest). */
static int build_current(const char *build, const struct ticket *t)
{
    char path[PATH_MAX_MGR];
    unsigned char *text, digest[32];
    struct sha256 sh;
    char *head;
    size_t n;
    int same;

    join_path(path, sizeof(path), build, MANIFEST);
    if (read_file(path, &text, &n))
        return 0;
    sha256_init(&sh);
    sha256_update(&sh, text, n);
    sha256_final(&sh, digest);
    same = n == t->size && !memcmp(digest, t->sha256, 32);
    if (!same && (head = strstr((char *)text, "\"deployed\"")) && (head = strchr(head, ','))) {
        sha256_init(&sh);
        sha256_update(&sh, "{", 1);
        sha256_update(&sh, head + 1, n - (size_t)(head + 1 - (char *)text));
        sha256_final(&sh, digest);
        same = n - (size_t)(head - (char *)text) == t->size && !memcmp(digest, t->sha256, 32);
    }
    free(text);
    return same;
}

/* The game only shows a refusal once it is running, so the server is asked first. */
static int join_ready(struct server *s, const char *build)
{
    struct ticket t;
    const char *err;

    busy("Checking the server...");
    err = server_ticket(s, &t, progress);
    if (err == SERVER_PASSWORD) {
        say("Cannot join", err, "Set it on the server's page.");
        return 0;
    }
    if (err && strcmp(err, "The server hands out no build.")) {
        say("Cannot join", s->name, err);
        return 0;
    }
    if (!err && !build_current(build, &t)) {
        mgr_log("join %s: build out of date\n", s->name);
        say("Build out of date", s->name, "The server's build has changed. Open the server and "
                                          "press X to update it.");
        return 0;
    }
    return 1;
}

static void join(const struct server *s, const char *build)
{
    if (!join_ready((struct server *)s, build))
        return;
    const char *err = join_server(build, s->name, leaving);

    mgr_log("join %s failed: %s\n", s->name, err);
    say("Cannot join", err, NULL);
}

static void check_update(void)
{
    char version[16], launcher[PATH_MAX_MGR], text[48];
    const char *why = update_apply(version, sizeof(version), launcher, sizeof(launcher));

    if (!why)
        return;
    if (*why) {
        say("Update refused", why, NULL);
        return;
    }
    snprintf(text, sizeof(text), "Starting manager %s...", version);
    busy(text);
    mgr_launch_xbe(launcher);
}

/* Checks the update feed (or `feed`) and installs a newer manager from it. */
static void fetch_update(const char *feed)
{
    char version[16];
    const char *why;
    int newer;

    busy("Checking for a newer manager...");
    why = update_fetch(feed, progress, version, sizeof(version), &newer);
    if (newer)
        check_update();
    else if (!strncmp(why, "Manager ", 8))
        say("Up to date", why, NULL);
    else
        say("No update", why, NULL);
}

/* Saves any URL to a file, for testing the network. */
static void get_url(const char *url, const char *path)
{
    unsigned char *body;
    size_t n;
    const char *why = http_get(url, &body, &n, 64 << 20, NULL);
    FILE *f;

    if (!why && (f = fopen(path, "wb"))) {
        if (fwrite(body, 1, n, f) != n)
            why = "write failed";
        fclose(f);
    }
    mgr_log("exec: get %s: %s, %u bytes\n", url, why ? why : "ok", (unsigned)n);
    free(body);
}

static struct build *find_build(const char *name)
{
    int i;

    for (i = 0; i < v.build_list.count; i++)
        if (!name_cmp(builds[i].name, name))
            return &builds[i];
    mgr_log("exec: no build named %s\n", name);
    return NULL;
}

static void press(int button);

static void press_named(const char *name)
{
    static const struct {
        const char *name;
        int button;
    } names[] = {
        {"a", SDL_CONTROLLER_BUTTON_A}, {"b", SDL_CONTROLLER_BUTTON_B},
        {"x", SDL_CONTROLLER_BUTTON_X}, {"y", SDL_CONTROLLER_BUTTON_Y},
        {"up", SDL_CONTROLLER_BUTTON_DPAD_UP}, {"down", SDL_CONTROLLER_BUTTON_DPAD_DOWN},
        {"left", SDL_CONTROLLER_BUTTON_DPAD_LEFT}, {"right", SDL_CONTROLLER_BUTTON_DPAD_RIGHT},
        {"l", SDL_CONTROLLER_BUTTON_LEFTSHOULDER}, {"r", SDL_CONTROLLER_BUTTON_RIGHTSHOULDER},
        {"start", SDL_CONTROLLER_BUTTON_START},
    };
    unsigned i;

    for (i = 0; i < sizeof(names) / sizeof(*names); i++)
        if (!strcmp(name, names[i].name)) {
            press(names[i].button);
            draw();
            return;
        }
    mgr_log("exec: no button %s\n", name);
}

/* Runs the unattended command file. */
static void run_exec(void)
{
    unsigned char *data;
    char *line, *next, *arg[5];
    struct build *b;
    struct verify r;
    const char *err;
    size_t n, len;
    int argc;
    FILE *f;

    if (read_file(EXEC_PATH, &data, &n))
        return;
    DeleteFileA(EXEC_PATH);
    mgr_log("exec: %u bytes\n", (unsigned)n);
    for (line = (char *)data; line && *line; line = next) {
        next = strpbrk(line, "\r\n");
        if (next)
            for (*next++ = 0; *next == '\r' || *next == '\n'; next++)
                ;
        len = strlen(line);
        for (argc = 0; argc < 5 && (arg[argc] = strtok(argc ? NULL : line, " \t")); argc++)
            ;
        if (!argc || arg[0][0] == '#')
            continue;
        mgr_log("exec: %s\n", arg[0]);
        if (!strcmp(arg[0], "base") && argc >= 3 && !strcmp(arg[1], "path")) {
            /* the folder may hold spaces, which strtok took */
            for (n = arg[2] - line; n < len; n++)
                if (!line[n])
                    line[n] = ' ';
            use_folder(arg[2]);
            continue;
        }
        if (!strcmp(arg[0], "type") && argc >= 2) {
            for (n = arg[1] - line; n < len; n++)
                if (!line[n])
                    line[n] = ' ';
            if (!v.kb.open)
                mgr_log("exec: no keyboard open\n");
            for (n = 0; v.kb.open && arg[1][n]; n++)
                ui_keyboard_press(&v.kb, (unsigned char)arg[1][n]);
            draw();
            continue;
        }
        if (!strcmp(arg[0], "list")) {
            scan();
        } else if (!strcmp(arg[0], "verify") && argc == 2 && (b = find_build(arg[1]))) {
            verify_build(b, &r, NULL);
        } else if (!strcmp(arg[0], "launch") && argc == 2 && (b = find_build(arg[1]))) {
            free(data);
            launch(b);
            return;
        } else if (!strcmp(arg[0], "rebuild") && argc == 4 && (b = find_build(arg[1]))) {
            err = rebuild_xbe(b, arg[2], arg[3], NULL);
            mgr_log("exec: rebuild %s\n", err ? err : "ok");
        } else if (!strcmp(arg[0], "agent") && argc == 2) {
            /* serve the agent unattended, until the PC reboots or launches */
            for (n = KeTickCount; KeTickCount - n < (DWORD)atoi(arg[1]) * 1000;) {
                agent_poll();
                if (SDL_GetTicks() - last_draw >= 1000)
                    draw();
                Sleep(5);
            }
        } else if (!strcmp(arg[0], "base")) {
            find_retail_bases();
            if (argc == 3 && !strcmp(arg[1], "use") && atoi(arg[2]) >= 0
                && atoi(arg[2]) < v.base_count)
                use_base(&bases[atoi(arg[2])]);
            else if (argc != 1)
                mgr_log("exec: no such base\n");
        } else if (!strcmp(arg[0], "update")) {
            check_update();
        } else if (!strcmp(arg[0], "fetch") && argc <= 2) {
            fetch_update(argc == 2 ? arg[1] : NULL);
        } else if (!strcmp(arg[0], "get") && argc == 3) {
            get_url(arg[1], arg[2]);
        } else if (!strcmp(arg[0], "run") && argc == 2) {
            /* the lines left are for the manager it starts */
            if (next && *next && (f = fopen(EXEC_PATH, "wb"))) {
                fputs(next, f);
                fclose(f);
            }
            mgr_launch_xbe(arg[1]);
        } else if (!strcmp(arg[0], "wait") && argc == 2) {
            Sleep((DWORD)atoi(arg[1]) * 1000);
        } else if (!strcmp(arg[0], "servers")) {
            load_servers();
        } else if (!strcmp(arg[0], "server") && argc == 3 && !strcmp(arg[1], "add")) {
            mgr_log("exec: server add %s: %s\n", arg[2], add_server(arg[2]) ? "refused" : "ok");
        } else if ((!strcmp(arg[0], "install") || !strcmp(arg[0], "join")) && argc >= 2) {
            for (n = 0; n < (size_t)v.server_list.count && name_cmp(servers[n].name, arg[1]); n++)
                ;
            if (n == (size_t)v.server_list.count) {
                mgr_log("exec: no server named %s\n", arg[1]);
            } else if (arg[0][0] == 'i') {
                v.server_list.sel = (int)n;
                install_server(&servers[n], argc == 3 && !strcmp(arg[2], "replace"));
                mgr_log("exec: install: %s: %s; %s\n", v.title, v.text[0], v.text[1]);
                v.title[0] = 0, v.confirm = 0;
            } else if (server_builds[n]) {
                free(data);
                join(&servers[n], server_builds[n]);
                return;
            } else {
                mgr_log("exec: %s has no build installed\n", arg[1]);
            }
        } else if (!strcmp(arg[0], "press")) {
            for (n = 1; n < (size_t)argc; n++)
                press_named(arg[n]);
        } else if (!strcmp(arg[0], "shutdown")) {
            mgr_log("exec: shutdown\n");
            HalInitiateShutdown();
            for (;;)
                Sleep(1000);
        } else if (!strcmp(arg[0], "reboot")) {
            HalReturnToFirmware(HalRebootRoutine);
        } else {
            mgr_log("exec: not understood\n");
        }
    }
    free(data);
}

static void switch_tab(int delta)
{
    v.tab = (v.tab + TAB_COUNT + delta) % TAB_COUNT;
    v.page = PAGE_MAIN;
}

static void press(int button)
{
    struct build *b = v.build_list.count ? &builds[v.build_list.sel] : NULL;
    int step = button == SDL_CONTROLLER_BUTTON_DPAD_UP     ? -1
               : button == SDL_CONTROLLER_BUTTON_DPAD_DOWN ? 1
                                                           : 0;

    dirty = 1;
    if (v.title[0]) {
        if (button == SDL_CONTROLLER_BUTTON_B || button == SDL_CONTROLLER_BUTTON_A)
            v.title[0] = 0;
        /* the one question asked: whether to install over a folder that is not a build */
        if (v.confirm && button == SDL_CONTROLLER_BUTTON_A && chosen_server()) {
            v.confirm = 0;
            install_server(chosen_server(), 1);
        } else if (!v.title[0]) {
            v.confirm = 0;
        }
        return;
    }
    if (v.kb.open) {
        keyboard_press(button);
        return;
    }
    if (button == SDL_CONTROLLER_BUTTON_LEFTSHOULDER) {
        switch_tab(-1);
    } else if (button == SDL_CONTROLLER_BUTTON_RIGHTSHOULDER) {
        switch_tab(1);
    } else if (v.tab == TAB_BUILDS && v.page == PAGE_MAIN) {
        if (step)
            ui_list_move(&v.build_list, step);
        else if (button == SDL_CONTROLLER_BUTTON_A && b)
            details(b), v.page = PAGE_DETAILS;
        else if (button == SDL_CONTROLLER_BUTTON_Y)
            scan();
    } else if (v.tab == TAB_BUILDS) {
        if (step)
            ui_list_move(&v.line_list, step);
        else if (button == SDL_CONTROLLER_BUTTON_B)
            v.page = PAGE_MAIN;
        else if (button == SDL_CONTROLLER_BUTTON_X && !b->error[0])
            verify(b);
        else if (button == SDL_CONTROLLER_BUTTON_A && !b->error[0])
            launch(b);
    } else if (v.tab == TAB_SERVERS && v.page == PAGE_MAIN) {
        if (step)
            ui_list_move(&v.server_list, step);
        else if (button == SDL_CONTROLLER_BUTTON_A && chosen_server())
            server_details(chosen_server(), server_builds[v.server_list.sel]),
                v.page = PAGE_SERVER;
        else if (button == SDL_CONTROLLER_BUTTON_X)
            ask_server();
        else if (button == SDL_CONTROLLER_BUTTON_Y)
            scan(), load_servers();
    } else if (v.tab == TAB_SERVERS) {
        if (step)
            ui_list_move(&v.line_list, step);
        else if (button == SDL_CONTROLLER_BUTTON_B)
            v.page = PAGE_MAIN;
        else if (button == SDL_CONTROLLER_BUTTON_X)
            install_server(chosen_server(), 0);
        else if (button == SDL_CONTROLLER_BUTTON_Y)
            ask_password(NULL, 0);
        else if (button == SDL_CONTROLLER_BUTTON_A && server_builds[v.server_list.sel])
            join(chosen_server(), server_builds[v.server_list.sel]);
    } else if (v.page == PAGE_BROWSE) {
        if (step)
            ui_list_move(&v.folder_list, step);
        else if (button == SDL_CONTROLLER_BUTTON_A && v.folder_list.count)
            browse_into(&folders[v.folder_list.sel]);
        else if (button == SDL_CONTROLLER_BUTTON_X && browse_path[0])
            use_folder(browse_path);
        else if (button == SDL_CONTROLLER_BUTTON_B)
            browse_up();
    } else if (v.page == PAGE_BASES) {
        if (step)
            ui_list_move(&v.base_list, step);
        else if (button == SDL_CONTROLLER_BUTTON_A && v.base_list.sel < v.base_count)
            use_base(&bases[v.base_list.sel]);
        else if (button == SDL_CONTROLLER_BUTTON_A)
            browse(browse_path);
        else if (button == SDL_CONTROLLER_BUTTON_B)
            v.page = PAGE_MAIN;
    } else {
        if (step)
            ui_list_move(&v.settings, step);
        else if (button == SDL_CONTROLLER_BUTTON_A && v.settings.sel == SET_BASE)
            find_retail_bases(), v.page = PAGE_BASES;
        else if (button == SDL_CONTROLLER_BUTTON_A && v.settings.sel == SET_MANAGER)
            fetch_update(NULL);
    }
}

/* The triggers switch tabs too; they are axes, so a press is crossing the threshold. */
static void trigger(int axis, int value)
{
    static int down[2];
    int i = axis == SDL_CONTROLLER_AXIS_TRIGGERRIGHT;

    if (value > TRIGGER_DOWN && !down[i]) {
        down[i] = 1;
        press(i ? SDL_CONTROLLER_BUTTON_RIGHTSHOULDER : SDL_CONTROLLER_BUTTON_LEFTSHOULDER);
    } else if (value < TRIGGER_DOWN / 2) {
        down[i] = 0;
    }
}

/* The left stick steps through menus as the d-pad does: one press each time it leaves the centre. */
#define STICK_ON 16000
#define STICK_OFF 8000
static void stick(int axis, int value)
{
    static int held[2];
    int i = axis == SDL_CONTROLLER_AXIS_LEFTY, now = held[i];

    if (value <= -STICK_ON)
        now = -1;
    else if (value >= STICK_ON)
        now = 1;
    else if (value > -STICK_OFF && value < STICK_OFF)
        now = 0;
    if (now && now != held[i])
        press(i ? (now < 0 ? SDL_CONTROLLER_BUTTON_DPAD_UP : SDL_CONTROLLER_BUTTON_DPAD_DOWN)
                : (now < 0 ? SDL_CONTROLLER_BUTTON_DPAD_LEFT : SDL_CONTROLLER_BUTTON_DPAD_RIGHT));
    held[i] = now;
}

int main(void)
{
    SDL_Event e;
    const char *news;

    mount_drives();
    mgr_log("manager %s start\n", MGR_VERSION);
    update_locate();
    v.builds = builds;
    v.bases = bases;
    v.folders = folders;
    v.browse_path = browse_path;
    v.lines = lines;
    v.overlay_base = overlay_base;
    v.slot = update_slot();
    v.base_current = -1;
    v.build_list.rows = BUILD_ROWS;
    v.line_list.rows = LINE_ROWS;
    v.base_list.rows = BASE_ROWS;
    v.folder_list.rows = FOLDER_ROWS;
    v.settings.count = v.settings.rows = SET_COUNT;
    v.servers = servers;
    v.server_builds = server_builds;
    v.server_list.rows = SERVER_ROWS;
    v.agent = agent_status();
    /* before the command file too: without a video mode the rig's capture sees no signal */
    XVideoSetMode(640, 480, 32, REFRESH_DEFAULT);
    if (SDL_Init(SDL_INIT_GAMECONTROLLER) || pb_init()) {
        mgr_log("video or input init failed\n");
        Sleep(5000);
        return 1;
    }
    pb_show_front_screen();
    video_up = 1;
    scan();
    load_servers();
    news = update_confirm();
    /* first run: offer the bases found, since overlay builds and XBE updates need one */
    if (!console_get("OverlayBase", overlay_base, sizeof(overlay_base))) {
        find_retail_bases();
        if (v.base_count)
            v.tab = TAB_SETTINGS, v.page = PAGE_BASES;
    }
    if (news)
        say("Manager", news, NULL);
    if (v.page != PAGE_BASES)
        handoff();
    agent_start();
    draw();
    check_update();
    run_exec();
    for (;;) {
        while (SDL_PollEvent(&e)) {
            if (e.type == SDL_CONTROLLERDEVICEADDED)
                SDL_GameControllerOpen(e.cdevice.which);
            else if (e.type == SDL_CONTROLLERBUTTONDOWN)
                press(e.cbutton.button);
            else if (e.type == SDL_CONTROLLERAXISMOTION
                     && (e.caxis.axis == SDL_CONTROLLER_AXIS_LEFTX
                         || e.caxis.axis == SDL_CONTROLLER_AXIS_LEFTY))
                stick(e.caxis.axis, e.caxis.value);
            else if (e.type == SDL_CONTROLLERAXISMOTION
                     && (e.caxis.axis == SDL_CONTROLLER_AXIS_TRIGGERLEFT
                         || e.caxis.axis == SDL_CONTROLLER_AXIS_TRIGGERRIGHT))
                trigger(e.caxis.axis, e.caxis.value);
        }
        agent_poll();
        if (dirty || SDL_GetTicks() - last_draw >= REDRAW_MS)
            draw();
        else
            Sleep(5);
    }
}
