/* TES3X manager: lists the TES3X builds on the console, shows what each holds, checks them
 * against their manifests and starts them. E:\tes3xmgr.txt logs what it does.
 *
 * E:\tes3xmgrexec.txt, when present, is read once at start and deleted: one command a line,
 * run in order without input, for unattended tests:
 *   list | verify NAME | launch NAME | rebuild NAME XBE DELTA | agent SECONDS
 *   | base [use N] | update | fetch [FEED] | get URL FILE | run XBE | press BUTTON...
 *   | shutdown | reboot
 * `base` lists the retail bases found; `base use N` makes the Nth one OverlayBase. `rebuild`
 * decodes DELTA against OverlayBase's morrowind.xbe. `update` installs a release waiting in
 * E:\TES3X\update, as a start does; `fetch` checks the update feed and installs from it; `get`
 * saves a URL to a file; `run` starts any XBE, leaving the lines after it for that XBE; `press`
 * presses controller buttons (a b x y up down l r start), for screenshots of the screens. */

#include "mgr.h"
#include "screens.h"

#include <SDL.h>
#include <hal/video.h>
#include <hal/xbox.h>
#include <pbkit/pbkit.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include <xboxkrnl/xboxkrnl.h>

#define LOG_PATH "E:\\tes3xmgr.txt"
#define EXEC_PATH "E:\\tes3xmgrexec.txt"
#define MAX_BUILDS 64
#define MAX_BASES 8
/* the agent's status can change without input */
#define REDRAW_MS 500
#define TRIGGER_DOWN 16000

static struct build builds[MAX_BUILDS];
static struct base bases[MAX_BASES];
static struct line lines[MAX_LINES];
static char overlay_base[PATH_MAX_MGR];
static struct view v;
static int video_up, dirty = 1;
static Uint32 last_draw;

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
    screen_draw(&v);
    present();
    dirty = 0;
}

static void busy(const char *text)
{
    if (!video_up)
        return;
    v.agent = agent_status();
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

static int progress(const char *what, unsigned long long done, unsigned long long total)
{
    agent_poll();
    if (!video_up || SDL_GetTicks() - last_draw < 100)
        return 0;
    v.agent = agent_status();
    screen_progress(&v, what, done, total);
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

static void find_retail_bases(void)
{
    int i;

    busy("Looking for a retail base...");
    v.base_list.count = find_bases(bases, MAX_BASES, NULL);
    v.base_list.sel = v.base_list.top = 0;
    v.base_current = -1;
    for (i = 0; i < v.base_list.count; i++) {
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
    size_t n;
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
        for (argc = 0; argc < 5 && (arg[argc] = strtok(argc ? NULL : line, " \t")); argc++)
            ;
        if (!argc || arg[0][0] == '#')
            continue;
        mgr_log("exec: %s\n", arg[0]);
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
                && atoi(arg[2]) < v.base_list.count)
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
    } else if (v.page == PAGE_BASES) {
        if (step)
            ui_list_move(&v.base_list, step);
        else if (button == SDL_CONTROLLER_BUTTON_A && v.base_list.count)
            use_base(&bases[v.base_list.sel]);
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

int main(void)
{
    SDL_Event e;
    const char *news;

    mount_drives();
    mgr_log("manager %s start\n", MGR_VERSION);
    update_locate();
    v.builds = builds;
    v.bases = bases;
    v.lines = lines;
    v.overlay_base = overlay_base;
    v.slot = update_slot();
    v.base_current = -1;
    v.build_list.rows = BUILD_ROWS;
    v.line_list.rows = LINE_ROWS;
    v.base_list.rows = BASE_ROWS;
    v.settings.count = v.settings.rows = SET_COUNT;
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
    news = update_confirm();
    /* first run: offer the bases found, since overlay builds and XBE updates need one */
    if (!console_get("OverlayBase", overlay_base, sizeof(overlay_base))) {
        find_retail_bases();
        if (v.base_list.count)
            v.tab = TAB_SETTINGS, v.page = PAGE_BASES;
    }
    if (news)
        say("Manager", news, NULL);
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
