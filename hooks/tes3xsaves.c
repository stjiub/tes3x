/* Rotate automatic saves through separate Xbox save containers. */

#include "tes3x_thunks.h"
#include "tes3xnt.h"
#include "tes3xlog.h"

#ifndef TES3X_SAVE_GAME
#error "define TES3X_SAVE_GAME to the engine save routine"
#endif
#ifndef TES3X_INI_GET_STRING
#error "define TES3X_INI_GET_STRING to the VA of the ini string reader"
#endif
#ifndef TES3X_INI_PATH
#error "define TES3X_INI_PATH to the VA of the engine's ini filename string"
#endif

#define NtCreateFile KFN(THUNK_NtCreateFile, fn_NtCreateFile)
#define NtReadFile KFN(THUNK_NtReadFile, fn_NtReadFile)
#define NtWriteFile KFN(THUNK_NtWriteFile, fn_NtWriteFile)
#define NtFlushBuffersFile KFN(THUNK_NtFlushBuffersFile, fn_NtFlushBuffersFile)
#define NtClose KFN(THUNK_NtClose, fn_NtClose)

#define AUTOSAVE_DEFAULT_SLOTS 3u
#define AUTOSAVE_MAX_SLOTS 9u

typedef int(__cdecl *fn_ini_get_string)(const char *, const char *, const char *,
                                        char *, int, const char *);
typedef unsigned char(__attribute__((thiscall)) *fn_save_game)(void *, const char *, const char *);

static char state_path[] = "T:\\tes3x-autosave.dat";
static u32 rotation_enabled = 1;
static u32 slot_count = AUTOSAVE_DEFAULT_SLOTS;
static u32 next_slot;
static int settings_ready;

static u32 parse_slots(const char *s)
{
    u32 value = 0;
    int any = 0;

    while (*s == ' ' || *s == '\t')
        s++;
    while (*s >= '0' && *s <= '9') {
        value = value * 10 + (u32)(*s++ - '0');
        any = 1;
    }
    if (!any || value < 1 || value > AUTOSAVE_MAX_SLOTS)
        return AUTOSAVE_DEFAULT_SLOTS;
    return value;
}

static u32 parse_toggle(const char *s)
{
    while (*s == ' ' || *s == '\t')
        s++;
    if (*s == '0' || *s == '1')
        return (u32)(*s - '0');
    return 1;
}

static void load_settings(void)
{
    fn_ini_get_string get = (fn_ini_get_string)TES3X_INI_GET_STRING;
    ANSI_STRING name;
    OBJECT_ATTRIBUTES oa;
    IO_STATUS_BLOCK iosb;
    u64 offset = 0;
    unsigned char stored = 0;
    char buf[16];
    void *h = 0;
    int i;

    settings_ready = 1;
    for (i = 0; i < (int)sizeof(buf); i++)
        buf[i] = 0;
    get("Xbox", "RotatingAutosaves", "1", buf, (int)sizeof(buf) - 1,
        (const char *)TES3X_INI_PATH);
    rotation_enabled = parse_toggle(buf);

    for (i = 0; i < (int)sizeof(buf); i++)
        buf[i] = 0;
    get("Xbox", "AutosaveSlots", "3", buf, (int)sizeof(buf) - 1,
        (const char *)TES3X_INI_PATH);
    slot_count = parse_slots(buf);

    tes3x_log("autosave.enabled", rotation_enabled);
    tes3x_log("autosave.slots", slot_count);
    if (!rotation_enabled || slot_count <= 1)
        return;

    tes3x_dos_attributes(&oa, &name, state_path);
    if (NtCreateFile(&h, GENERIC_READ | SYNCHRONIZE, &oa, &iosb, 0,
                     FILE_ATTRIBUTE_NORMAL, FILE_SHARE_READ, FILE_OPEN,
                     FILE_SYNCHRONOUS_IO_NONALERT) == 0) {
        if (NtReadFile(h, 0, 0, 0, &iosb, &stored, 1, &offset) == 0
                && iosb.Information == 1)
            next_slot = (u32)stored % slot_count;
        NtClose(h);
    }
    tes3x_log("autosave.next", next_slot + 1);
}

static void store_next_slot(void)
{
    ANSI_STRING name;
    OBJECT_ATTRIBUTES oa;
    IO_STATUS_BLOCK iosb;
    u64 offset = 0;
    unsigned char stored = (unsigned char)next_slot;
    void *h = 0;

    tes3x_dos_attributes(&oa, &name, state_path);
    if (NtCreateFile(&h, GENERIC_WRITE | SYNCHRONIZE, &oa, &iosb, 0,
                     FILE_ATTRIBUTE_NORMAL, FILE_SHARE_READ, FILE_OVERWRITE_IF,
                     FILE_SYNCHRONOUS_IO_NONALERT) != 0) {
        tes3x_log("autosave.state_error", 1);
        return;
    }
    if (NtWriteFile(h, 0, 0, 0, &iosb, &stored, 1, &offset) != 0
            || iosb.Information != 1) {
        tes3x_log("autosave.state_error", 2);
    } else if (NtFlushBuffersFile(h, &iosb) != 0)
        tes3x_log("autosave.state_error", 3);
    NtClose(h);
}

static void slot_name(char *out, u32 cap, const char *base, u32 slot)
{
    u32 n = 0;

    while (base[n] && n + 3 < cap) {
        out[n] = base[n];
        n++;
    }
    out[n++] = ' ';
    out[n++] = (char)('1' + slot);
    out[n] = 0;
}

unsigned char __attribute__((thiscall))
tes3x_autosave_hook(void *game, const char *filename, const char *display)
{
    fn_save_game save = (fn_save_game)TES3X_SAVE_GAME;
    char slot_filename[64];
    char slot_display[64];
    u32 slot;
    unsigned char ok;

    if (!settings_ready)
        load_settings();
    if (!rotation_enabled || slot_count <= 1)
        return save(game, filename, display);

    slot = next_slot;
    tes3x_log("autosave.slot", slot + 1);
    if (slot == 0) {
        ok = save(game, filename, display);
    } else {
        slot_name(slot_filename, sizeof(slot_filename), filename, slot);
        slot_name(slot_display, sizeof(slot_display), display, slot);
        ok = save(game, slot_filename, slot_display);
    }
    if (ok) {
        next_slot = (slot + 1) % slot_count;
        store_next_slot();
    }
    return ok;
}
