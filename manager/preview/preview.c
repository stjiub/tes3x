/* Renders the manager's screens with sample data to OUT/NAME.ppm, for looking at the UI on the
 * PC. Build from manager/:
 *   clang -O2 -I. preview/preview.c screens.c ui.c gfx.c fontdata.c -o preview.exe
 *   preview.exe OUT */

#include "../screens.h"

#include <stdio.h>
#include <string.h>

static const char *out_dir;

static void save(const char *name)
{
    char path[512];
    FILE *f;
    int i;

    snprintf(path, sizeof(path), "%s/%s.ppm", out_dir, name);
    f = fopen(path, "wb");
    if (!f)
        return;
    fprintf(f, "P6 %d %d 255\n", GFX_W, GFX_H);
    for (i = 0; i < GFX_W * GFX_H; i++) {
        fputc(gfx_pixels[i] >> 16 & 0xFF, f);
        fputc(gfx_pixels[i] >> 8 & 0xFF, f);
        fputc(gfx_pixels[i] & 0xFF, f);
    }
    fclose(f);
}

static struct build builds[] = {
    {"F:\\Games\\MorrowindNet", "MorrowindNet", "net", "overlay", "2026-10-05", 7343, 18, 2,
     1009254400ULL, "play.example.net", ""},
    {"F:\\Games\\MorrowindBasemods", "MorrowindBasemods", "basemods", "full", "2026-10-01", 9120,
     64, 1, 2147483648ULL, "", ""},
    {"F:\\Games\\TR Preview", "TR Preview", "tr-masters", "overlay", "", 4410, 9, 1, 734003200ULL,
     "", ""},
    {"E:\\Games\\OldBuild", "OldBuild", "", "", "", 0, 0, 0, 0ULL, "",
     "manifest format 3 is newer"},
};

static struct base bases[] = {
    {"F:\\Games\\MorrowindRetail", 1, 1},
    {"F:\\Games\\Morrowind Game of the Year", 0, 1},
    {"G:\\Games\\Morrowind Game of the Year", 0, 1},
};

static struct folder folders[] = {
    {"Apps", 0}, {"Games", 0}, {"Morrowind GOTY Clean", 1}, {"Morrowind Ultimate", 1},
    {"Music", 0}, {"XBMC4Gamers", 0},
};

static struct server servers[] = {
    {"play.example.net", "play.example.net", 26500, "E:\\UDATA\\42530005\\TES3X\\servers.ini", 1,
     1, {0}, {0}, "", "4e1f0c9a7b2d55e0c3a1f9e8d7b6c5a4"},
    {"10.0.0.7:26500", "10.0.0.7", 26500, "E:\\UDATA\\42530005\\TES3X\\servers.ini", 1, 1, {0},
     {0}, "", "b81d2e44a09f3c7e1d5a6b4c3e2f1a09"},
    {"friends.example.org:27000", "friends.example.org", 27000,
     "E:\\UDATA\\42530005\\TES3X\\servers.ini", 0, 0, {0}, {0}, "", ""},
};
static const char *const server_builds[] = {"F:\\Games\\MorrowindNet", NULL, NULL};

static const struct line server_lines[] = {
    {"Address", "play.example.net, port 26500", 0},
    {"Server key", "4e1f0c9a7b2d55e0c3a1f9e8d7b6c5a4", 0},
    {"This console", "has an identity there", 0},
    {"Build", "F:\\Games\\MorrowindNet", 0},
};

static struct line lines[MAX_LINES];

static void add(const char *label, const char *text, int style)
{
    static int n;

    snprintf(lines[n].label, sizeof(lines[n].label), "%s", label);
    snprintf(lines[n].text, sizeof(lines[n].text), "%s", text);
    lines[n++].style = style;
}

int main(int argc, char **argv)
{
    static const char *const plugins[] = {
        "Morrowind.esm", "Tribunal.esm", "Bloodmoon.esm", "Patch for Purists.esm",
        "Better Bodies.esp", "Better Heads.esm", "Tamriel_Data.esm", "OAAB_Data.esm",
        "Graphic Herbalism.esp", "Morrowind Optimization Patch.esp", "TES3X Multiplayer.esp"};
    struct view v;
    unsigned i;

    out_dir = argc > 1 ? argv[1] : ".";
    memset(&v, 0, sizeof(v));
    v.builds = builds;
    v.build_list = (struct ui_list){4, 0, 0, BUILD_ROWS};
    v.bases = bases;
    v.base_count = 3;
    v.base_list = (struct ui_list){4, 1, 0, BASE_ROWS};
    v.folders = folders;
    v.folder_list = (struct ui_list){6, 2, 0, FOLDER_ROWS};
    v.browse_path = "F:\\Backup";
    v.base_current = 0;
    v.settings = (struct ui_list){SET_COUNT, 0, 0, SET_COUNT};
    v.overlay_base = "F:\\Games\\MorrowindRetail";
    v.agent = "connected to the PC";
    v.slot = "a";
    add("Profile", "net, overlay layout", 0);
    add("Files", "7343, 962 MB, deployed 2026-10-05", 0);
    add("Title ID", "42530005", 0);
    add("XBE", "default.xbe: 14 patches, retail fd4cf820, delta", 0);
    add("XBE", "morrowind.xbe: 14 patches, retail fd4cf820, delta", 0);
    add("", "Plugins (11)", LINE_HEAD);
    for (i = 0; i < sizeof(plugins) / sizeof(*plugins); i++)
        add("", plugins[i], 0);
    v.lines = lines;
    v.line_list = (struct ui_list){6 + 11, 0, 0, LINE_ROWS};

    screen_draw(&v);
    save("builds");
    v.build_list.sel = 3;
    screen_draw(&v);
    save("builds-error");
    v.build_list.sel = 0;
    v.page = PAGE_DETAILS;
    screen_draw(&v);
    save("details");
    ui_list_move(&v.line_list, 16);
    screen_draw(&v);
    save("details-end");
    screen_progress(&v, "Verifying MorrowindNet", 412ULL << 20, 962ULL << 20);
    save("progress");
    snprintf(v.title, sizeof(v.title), "Build differs from its manifest");
    snprintf(v.text[0], sizeof(v.text[0]), "7340 ok, 1 missing, 2 changed");
    snprintf(v.text[1], sizeof(v.text[1]), "The files are listed in E:\\tes3xmgr.txt.");
    v.dismiss = 1;
    screen_draw(&v);
    save("dialog");
    memset(v.title, 0, sizeof(v.title));
    memset(v.text, 0, sizeof(v.text));
    v.tab = TAB_SERVERS;
    v.page = PAGE_MAIN;
    v.servers = servers;
    v.server_builds = server_builds;
    v.server_list = (struct ui_list){3, 0, 0, SERVER_ROWS};
    screen_draw(&v);
    save("servers");
    v.page = PAGE_SERVER;
    v.lines = server_lines;
    v.line_list = (struct ui_list){4, 0, 0, LINE_ROWS};
    screen_draw(&v);
    save("server");
    screen_progress(&v, "Data Files/TES3X Multiplayer.esp", 3ULL << 20, 7ULL << 20);
    save("server-progress");
    ui_tick = 3;
    screen_progress(&v, "Asking the server for its build", 0, 0);
    save("server-wait");
    snprintf(v.title, sizeof(v.title), "Build up to date");
    snprintf(v.text[0], sizeof(v.text[0]), "F:\\Games\\MorrowindNet");
    snprintf(v.text[1], sizeof(v.text[1]), "3 downloaded, 0 from the base, 2 XBEs rebuilt, 33 kept");
    v.dismiss = 1;
    screen_draw(&v);
    save("server-done");
    v.server_list.sel = 2;
    snprintf(v.title, sizeof(v.title), "Replace this folder?");
    snprintf(v.text[0], sizeof(v.text[0]), "F:\\Games\\MorrowindNet");
    snprintf(v.text[1], sizeof(v.text[1]), "It holds files that are not a TES3X build. Installing "
             "removes nothing, but replaces any file the build has.");
    v.confirm = 1;
    screen_draw(&v);
    save("server-confirm");
    v.confirm = 0;
    memset(v.title, 0, sizeof(v.title));
    memset(v.text, 0, sizeof(v.text));
    v.server_list.count = 0;
    v.page = PAGE_MAIN;
    screen_draw(&v);
    save("servers-empty");
    v.lines = lines;
    v.tab = TAB_SETTINGS;
    v.page = PAGE_MAIN;
    screen_draw(&v);
    save("settings");
    v.page = PAGE_BASES;
    screen_draw(&v);
    save("bases");
    v.base_list.sel = 3;
    screen_draw(&v);
    save("bases-choose");
    v.base_count = 0;
    v.base_list = (struct ui_list){1, 0, 0, BASE_ROWS};
    screen_draw(&v);
    save("bases-none");
    v.page = PAGE_BROWSE;
    screen_draw(&v);
    save("browse");
    snprintf(v.title, sizeof(v.title), "Not a retail base");
    snprintf(v.text[0], sizeof(v.text[0]), "F:\\Backup\\Music");
    snprintf(v.text[1], sizeof(v.text[1]), "It has no morrowind.xbe.");
    v.dismiss = 1;
    screen_draw(&v);
    save("browse-refused");
    memset(v.title, 0, sizeof(v.title));
    memset(v.text, 0, sizeof(v.text));
    v.tab = TAB_BUILDS;
    v.page = PAGE_MAIN;
    v.build_list.count = 0;
    v.agent = "off";
    screen_draw(&v);
    save("builds-empty");
    screen_busy(&v, "Looking for builds...");
    save("busy");
    return 0;
}
