/* TES3X manager: lists the TES3X builds on the console, shows what each holds, checks them
 * against their manifests and starts them. E:\tes3xmgr.txt logs what it does.
 *
 * E:\tes3xmgrexec.txt, when present, is read once at start and deleted: one command a line,
 * run in order without input, for unattended tests:
 *   list | verify NAME | launch NAME | rebuild NAME XBE DELTA | agent SECONDS
 *   | base [use N] | shutdown | reboot
 * `base` lists the retail bases found; `base use N` makes the Nth one OverlayBase. `rebuild`
 * decodes DELTA against OverlayBase's morrowind.xbe. */

#include "mgr.h"

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
#define ROWS 16
#define COLS 60
#define LIST_ROWS (ROWS - 5)
#define MAX_LINES 512
#define MAX_BASES 8

enum screen { LIST, DETAILS, MESSAGE, BASE };

static struct build builds[MAX_BUILDS];
static int build_count, selected, top;
static enum screen screen, back;
static struct base bases[MAX_BASES];
static int base_count, base_selected;
static char overlay_base[PATH_MAX_MGR];
static char lines[MAX_LINES][COLS + 1];
static int line_count, scroll;
static char message[4][COLS + 1];
static int video_up;
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

static void add_line(const char *format, ...)
{
    va_list args;

    if (line_count >= MAX_LINES)
        return;
    va_start(args, format);
    vsnprintf(lines[line_count++], COLS + 1, format, args);
    va_end(args);
}

static void say(const char *a, const char *b)
{
    memset(message, 0, sizeof(message));
    snprintf(message[0], COLS + 1, "%s", a);
    snprintf(message[1], COLS + 1, "%s", b ? b : "");
    if (screen != MESSAGE)
        back = screen;
    screen = MESSAGE;
}

static void frame_begin(void)
{
    pb_wait_for_vbl();
    pb_target_back_buffer();
    pb_reset();
    pb_fill(0, 0, 640, 480, 0x00101820);
    pb_erase_text_screen();
}

static void frame_end(void)
{
    pb_draw_text_screen();
    while (pb_busy())
        ;
    while (pb_finished())
        ;
}

static void draw_list(void)
{
    const struct build *b;
    int i, row;

    pb_printat(0, 0, "TES3X manager %s - %d build%s", MGR_VERSION, build_count,
               build_count == 1 ? "" : "s");
    if (!build_count)
        pb_printat(2, 0, "No folder under C/E/F/G:\\Games holds " MANIFEST ".");
    for (row = 0, i = top; i < build_count && row < LIST_ROWS; i++, row++) {
        b = &builds[i];
        pb_printat(2 + row, 0, "%c %-22.22s %-16.16s %5lluMB", i == selected ? '>' : ' ',
                   b->path, b->error[0] ? b->error : b->profile, b->bytes >> 20);
    }
    pb_printat(ROWS - 3, 0, "Retail base: %.45s", overlay_base[0] ? overlay_base : "not set");
    pb_printat(ROWS - 2, 0, "Agent: %.50s", agent_status());
    pb_printat(ROWS - 1, 0, "A details  X retail base  Y rescan");
}

static void draw_bases(void)
{
    const struct base *b;
    int i;

    pb_printat(0, 0, "Retail base");
    pb_printat(1, 0, "Overlay builds read unchanged game files from it,");
    pb_printat(2, 0, "and XBE updates are rebuilt from its morrowind.xbe.");
    if (!base_count) {
        pb_printat(3, 0, "No retail base found under C/E/F/G:\\Games.");
        pb_printat(4, 0, "Install one from the PC, or copy the game");
        pb_printat(5, 0, "(morrowind.xbe and Data Files) into a folder there.");
        pb_printat(ROWS - 1, 0, "B back");
        return;
    }
    for (i = 0; i < base_count && i < LIST_ROWS; i++) {
        b = &bases[i];
        pb_printat(3 + i, 0, "%c%c %-34.34s %s", i == base_selected ? '>' : ' ',
                   !name_cmp(b->path, overlay_base) ? '*' : ' ', b->path,
                   !b->installed ? "copied" : b->has_xbe ? "TES3X" : "TES3X, no XBE");
    }
    b = &bases[base_selected];
    if (!b->installed)
        pb_printat(ROWS - 3, 0, "Not installed by TES3X: use it only if it is unmodded.");
    else if (!b->has_xbe)
        pb_printat(ROWS - 3, 0, "No retail XBE: updates cannot rebuild XBEs from it.");
    pb_printat(ROWS - 1, 0, "A use  B back");
}

static void draw_details(void)
{
    int i;

    for (i = 0; i < ROWS - 2 && scroll + i < line_count; i++)
        pb_printat(i, 0, "%s", lines[scroll + i]);
    pb_printat(ROWS - 1, 0, "A launch  X verify  B back");
}

static void draw(void)
{
    int i;

    if (!video_up)
        return;
    frame_begin();
    if (screen == LIST) {
        draw_list();
    } else if (screen == DETAILS) {
        draw_details();
    } else if (screen == BASE) {
        draw_bases();
    } else {
        for (i = 0; i < 4; i++)
            pb_printat(4 + i, 0, "%s", message[i]);
        pb_printat(ROWS - 1, 0, "B back");
    }
    frame_end();
    last_draw = SDL_GetTicks();
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
    frame_begin();
    pb_printat(4, 0, "Working: %.48s", what);
    pb_printat(6, 0, "%llu of %llu MB (%llu%%)", done >> 20, total >> 20,
               total ? done * 100 / total : 0);
    pb_printat(ROWS - 1, 0, "B cancel");
    frame_end();
    last_draw = SDL_GetTicks();
    return cancel_pressed();
}

static void scan(void)
{
    int i;

    if (video_up) {
        frame_begin();
        pb_printat(4, 0, "Looking for builds...");
        frame_end();
    }
    build_count = scan_builds(builds, MAX_BUILDS);
    selected = top = 0;
    mgr_log("scan: %d build(s)\n", build_count);
    for (i = 0; i < build_count; i++)
        mgr_log("  %s: profile '%s', %s layout, %d files, %llu bytes, %d plugins, %d xbe%s%s\n",
                builds[i].path, builds[i].profile, builds[i].layout, builds[i].files,
                builds[i].bytes, builds[i].plugins, builds[i].xbes,
                builds[i].error[0] ? ", " : "", builds[i].error);
}

static void find_retail_bases(void)
{
    int i;

    if (video_up) {
        frame_begin();
        pb_printat(4, 0, "Looking for a retail base...");
        frame_end();
    }
    base_count = find_bases(bases, MAX_BASES, NULL);
    base_selected = 0;
    for (i = 0; i < base_count; i++) {
        mgr_log("base %d: %s, %s%s\n", i, bases[i].path,
                bases[i].installed ? "installed by TES3X" : "copied",
                bases[i].has_xbe ? ", retail XBE" : ", no retail XBE");
        if (!name_cmp(bases[i].path, overlay_base))
            base_selected = i;
    }
}

static void use_base(const struct base *b)
{
    screen = LIST;
    if (console_set("OverlayBase", b->path)) {
        say("Could not write " CONSOLE_INI ".", NULL);
        return;
    }
    snprintf(overlay_base, sizeof(overlay_base), "%s", b->path);
    say("Retail base set:", b->path);
}

static void details(const struct build *b)
{
    struct json j;
    char *text, s[COLS + 1], d[16];
    int list, i, k, patches;
    unsigned title_id;
    char xbe[PATH_MAX_MGR];

    line_count = scroll = 0;
    add_line("%s", b->path);
    if (b->error[0])
        add_line("Cannot use: %s", b->error);
    add_line("Profile %s, %s layout", b->profile, b->layout[0] ? b->layout : "full");
    add_line("%d files, %llu MB%s%s", b->files, b->bytes >> 20,
             b->deployed[0] ? ", deployed " : "", b->deployed);
    join_path(xbe, sizeof(xbe), b->path, "default.xbe");
    if (!xbe_title_id(xbe, &title_id))
        add_line("Title ID %08X", title_id);
    if (manifest_load(b, &text, &j)) {
        add_line("Manifest unreadable");
        return;
    }
    list = json_get(&j, 0, "xbe");
    for (i = json_child(&j, list); i >= 0; i = json_sibling(&j, list, i)) {
        json_string(&j, json_get(&j, i, "path"), s, sizeof(s));
        json_string(&j, json_get(&j, i, "retail_digest"), d, sizeof(d));
        k = json_get(&j, i, "patches");
        patches = k >= 0 ? j.t[k].count : 0;
        add_line("XBE %s: %d patches, retail %.8s%s", s, patches, d,
                 json_get(&j, i, "delta") >= 0 ? ", delta" : "");
    }
    list = json_get(&j, 0, "plugins");
    add_line("Plugins (%d):", b->plugins);
    for (i = json_child(&j, list); i >= 0; i = json_sibling(&j, list, i)) {
        json_string(&j, i, s, sizeof(s));
        add_line("  %s", s);
    }
    manifest_free(text, &j);
}

static void verify(const struct build *b)
{
    struct verify v;
    char line[COLS + 1];

    if (verify_build(b, &v, progress)) {
        say("Verify failed: manifest unreadable.", NULL);
        return;
    }
    snprintf(line, sizeof(line), "%d ok, %d missing, %d changed", v.ok, v.missing, v.changed);
    say(v.cancelled ? "Verify cancelled." : v.missing || v.changed ? "Build differs from its manifest:"
                                                               : "Build matches its manifest.",
        line);
    if (v.missing || v.changed)
        snprintf(message[2], COLS + 1, "The files are listed in " LOG_PATH ".");
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
    say("Cannot launch:", err);
}

void mgr_launch_xbe(const char *xbe)
{
    const char *err = launch_xbe(xbe, leaving);

    mgr_log("launch %s failed: %s\n", xbe, err);
    say("Cannot launch:", err);
}

static struct build *find_build(const char *name)
{
    int i;

    for (i = 0; i < build_count; i++)
        if (!name_cmp(builds[i].name, name))
            return &builds[i];
    mgr_log("exec: no build named %s\n", name);
    return NULL;
}

/* Runs the unattended command file; returns nonzero to stop after it. */
static void run_exec(void)
{
    unsigned char *data;
    char *line, *next, *arg[5];
    struct build *b;
    struct verify v;
    const char *err;
    size_t n;
    int argc;

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
            verify_build(b, &v, NULL);
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
                && atoi(arg[2]) < base_count)
                use_base(&bases[atoi(arg[2])]);
            else if (argc != 1)
                mgr_log("exec: no such base\n");
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

static void press(int button)
{
    struct build *b = build_count ? &builds[selected] : NULL;
    int max;

    if (screen == LIST) {
        if (button == SDL_CONTROLLER_BUTTON_DPAD_UP && selected > 0)
            selected--;
        else if (button == SDL_CONTROLLER_BUTTON_DPAD_DOWN && selected + 1 < build_count)
            selected++;
        else if (button == SDL_CONTROLLER_BUTTON_A && b)
            details(b), screen = DETAILS;
        else if (button == SDL_CONTROLLER_BUTTON_Y)
            scan();
        else if (button == SDL_CONTROLLER_BUTTON_X)
            find_retail_bases(), screen = BASE;
        if (selected < top)
            top = selected;
        else if (selected >= top + LIST_ROWS)
            top = selected - LIST_ROWS + 1;
    } else if (screen == DETAILS) {
        max = line_count > ROWS - 2 ? line_count - (ROWS - 2) : 0;
        if (button == SDL_CONTROLLER_BUTTON_DPAD_UP && scroll > 0)
            scroll--;
        else if (button == SDL_CONTROLLER_BUTTON_DPAD_DOWN && scroll < max)
            scroll++;
        else if (button == SDL_CONTROLLER_BUTTON_B)
            screen = LIST;
        else if (button == SDL_CONTROLLER_BUTTON_X && !b->error[0])
            verify(b);
        else if (button == SDL_CONTROLLER_BUTTON_A)
            launch(b);
    } else if (screen == BASE) {
        if (button == SDL_CONTROLLER_BUTTON_DPAD_UP && base_selected > 0)
            base_selected--;
        else if (button == SDL_CONTROLLER_BUTTON_DPAD_DOWN && base_selected + 1 < base_count)
            base_selected++;
        else if (button == SDL_CONTROLLER_BUTTON_A && base_count)
            use_base(&bases[base_selected]);
        else if (button == SDL_CONTROLLER_BUTTON_B)
            screen = LIST;
    } else if (button == SDL_CONTROLLER_BUTTON_B) {
        screen = back == DETAILS && build_count ? DETAILS : LIST;
    }
}

int main(void)
{
    SDL_Event e;

    mount_drives();
    mgr_log("manager %s start\n", MGR_VERSION);
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
    /* first run: offer the bases found, since overlay builds and XBE updates need one */
    if (!console_get("OverlayBase", overlay_base, sizeof(overlay_base))) {
        find_retail_bases();
        if (base_count)
            screen = BASE;
    }
    agent_start();
    draw();
    run_exec();
    for (;;) {
        while (SDL_PollEvent(&e)) {
            if (e.type == SDL_CONTROLLERDEVICEADDED)
                SDL_GameControllerOpen(e.cdevice.which);
            else if (e.type == SDL_CONTROLLERBUTTONDOWN)
                press(e.cbutton.button);
        }
        agent_poll();
        draw();
    }
}
