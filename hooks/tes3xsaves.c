/* Rotate automatic saves through separate Xbox save containers. */

#include "tes3x_thunks.h"
#include "tes3xnt.h"
#include "tes3xlog.h"

#ifndef TES3X_SAVE_GAME
#error "define TES3X_SAVE_GAME to the engine save routine"
#endif
#ifndef TES3X_SAVE_THIS_PTR
#error "define TES3X_SAVE_THIS_PTR to the engine save owner pointer"
#endif
#if !defined(TES3X_SAVE_GATE_OWNER) || !defined(TES3X_SAVE_GATE_OWNER_OFFSET) || \
    !defined(TES3X_SAVE_GATE_STATE_OFFSET)
#error "define the retail save-gate owner and fields"
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

#ifdef TES3X_NET
/* The multiplayer patch wraps every save; the autosave call sites are this file's. */
unsigned char __attribute__((thiscall)) tes3x_net_save(void *, const char *, const char *);
#define SAVE_ENTRY tes3x_net_save
#else
#define SAVE_ENTRY ((fn_save_game)TES3X_SAVE_GAME)
#endif

static char state_path[] = "T:\\tes3x-autosave.dat";
static char autosave_name[] = "autosave";
static u32 rotation_enabled = 1;
static u32 slot_count = AUTOSAVE_DEFAULT_SLOTS;
static u32 next_slot;
static u32 transition_enabled = 1;
static int transition_saving;
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

    for (i = 0; i < (int)sizeof(buf); i++)
        buf[i] = 0;
    get("Xbox", "TransitionAutosaves", "1", buf, (int)sizeof(buf) - 1,
        (const char *)TES3X_INI_PATH);
    transition_enabled = parse_toggle(buf);

    tes3x_log("autosave.enabled", rotation_enabled);
    tes3x_log("autosave.slots", slot_count);
    tes3x_log("autosave.transitions", transition_enabled);
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
    fn_save_game save = SAVE_ENTRY;
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

unsigned char tes3x_autosave_now(void)
{
    void *game = **(void ***)TES3X_SAVE_THIS_PTR;

    return tes3x_autosave_hook(game, autosave_name, autosave_name);
}

static int retail_save_allowed(void)
{
    unsigned char *game = *(unsigned char **)TES3X_SAVE_GATE_OWNER;
    unsigned char *owner;

    if (!game)
        return 0;
    owner = *(unsigned char **)(game + TES3X_SAVE_GATE_OWNER_OFFSET);
    return owner && *(u32 *)(owner + TES3X_SAVE_GATE_STATE_OFFSET) == 0xBF800000u;
}

void tes3x_transition_save(u32 kind, u32 site)
{
    unsigned char ok;

    if (!settings_ready)
        load_settings();
    if (!transition_enabled || transition_saving)
        return;
    if (!retail_save_allowed()) {
        tes3x_log("autosave.transition_blocked", 1);
        return;
    }
    transition_saving = 1;
    tes3x_log("autosave.transition", kind);
    tes3x_log_hex("autosave.transition_site", site);
    ok = tes3x_autosave_now();
    tes3x_log("autosave.transition_ok", ok);
    transition_saving = 0;
}

#define TES3X_SAVE_STR_(x) #x
#define TES3X_SAVE_STR(x) TES3X_SAVE_STR_(x)
#define TRANSITION_HOOK(name, kind, target) \
    __attribute__((naked)) void name(void) \
    { \
        __asm__ volatile( \
            "pushal\n\t" \
            "pushfl\n\t" \
            "pushl 36(%esp)\n\t" \
            "pushl $" TES3X_SAVE_STR(kind) "\n\t" \
            "call _tes3x_transition_save\n\t" \
            "addl $8, %esp\n\t" \
            "popfl\n\t" \
            "popal\n\t" \
            "pushl $" TES3X_SAVE_STR(target) "\n\t" \
            "ret\n\t"); \
    }

TRANSITION_HOOK(tes3x_transition_cell_hook, 1, TES3X_CELL_CHANGE)
TRANSITION_HOOK(tes3x_transition_cell_companions_hook, 1, TES3X_CELL_CHANGE_COMPANIONS)
TRANSITION_HOOK(tes3x_transition_teleport_hook, 2, TES3X_CELL_CHANGE)
TRANSITION_HOOK(tes3x_transition_travel_hook, 3, TES3X_CELL_CHANGE)

int tes3x_autosave_command(void *game, const char *text)
{
    static const char command[] = "tes3xautosave";
    u32 i;

    for (i = 0; command[i] && text[i] == command[i]; i++)
        ;
    if (command[i] || text[i])
        return 0;
    (void)game;
    tes3x_log("autosave.command", tes3x_autosave_now());
    return 1;
}
