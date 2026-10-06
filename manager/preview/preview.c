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
     1009254400ULL, ""},
    {"F:\\Games\\MorrowindBasemods", "MorrowindBasemods", "basemods", "full", "2026-10-01", 9120,
     64, 1, 2147483648ULL, ""},
    {"F:\\Games\\TR Preview", "TR Preview", "tr-masters", "overlay", "", 4410, 9, 1, 734003200ULL,
     ""},
    {"E:\\Games\\OldBuild", "OldBuild", "", "", "", 0, 0, 0, 0ULL, "manifest format 3 is newer"},
};

static struct base bases[] = {
    {"F:\\Games\\MorrowindRetail", 1, 1},
    {"F:\\Games\\Morrowind Game of the Year", 0, 1},
    {"G:\\Games\\Morrowind Game of the Year", 0, 1},
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
    v.base_list = (struct ui_list){3, 1, 0, BASE_ROWS};
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
    v.tab = TAB_SETTINGS;
    v.page = PAGE_MAIN;
    screen_draw(&v);
    save("settings");
    v.page = PAGE_BASES;
    screen_draw(&v);
    save("bases");
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
