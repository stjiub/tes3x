/* A save holds the game thread for seconds while the heartbeat DPC keeps the session: BUSY tells
 * the server to hand this console's cells and actors to another player until it is back. */
#define EVENT_BUSY 21u /* BUSY_SAVING, or 0 once back */
#define BUSY_SAVING 1u

typedef unsigned char(__attribute__((thiscall)) *fn_save_game)(void *, const char *, const char *);
typedef u32(__attribute__((stdcall)) *fn_create_save)(const char *root, const u16 *name, u32 how,
                                                      u32 options, char *path, u32 size);
static const u32 save_sites[] = TES3X_NET_SAVE_SITES;
static u32 save_hooked, saves_seen, saves_busy, saves_slotted, saves_uploaded, save_pending;
#define OPEN_EXISTING 3u
/* While joined every save goes to one slot per server and character, "MP <character>
 * <server>": SaveGame names the folder by a hash of its file name, so the slot never meets a
 * single-player save, and its name with ".ess" stays within BULK_NAME. */
#define SLOT_CHARACTER 16u
#define SLOT_SERVER 13u
static char save_slot[3 + SLOT_CHARACTER + 1 + SLOT_SERVER + 1];
static char save_path[sizeof(up.path)], save_name[BULK_NAME + 1];

static u32 slot_text(char *out, const char *text, u32 cap)
{
    u32 i;
    char c;

    for (i = 0; i < cap && (c = text[i]); i++)
        out[i] = (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') ||
                 c == ' ' || c == '_' ? c : '-';
    return i;
}

/* 0 when there is no player name yet. */
static int slot_name(void)
{
    const u8 *ref = player_reference(), *base;
    const char *name;
    u32 n = 3, k;

    /* the player's NPCInstance, its baseNPC (+0x6C) and that NPC's name (+0x70) */
    if (!ref || !plausible(base = *(const u8 *const *)(ref + 0x28)) ||
        !plausible(base = *(const u8 *const *)(base + 0x6C)) ||
        !mapped(name = *(const char *const *)(base + 0x70)) || !name[0])
        return 0;
    copy((u8 *)save_slot, (const u8 *)"MP ", 3);
    n += slot_text(save_slot + n, name, SLOT_CHARACTER);
    save_slot[n++] = ' ';
    k = slot_text(save_slot + n, trust.name, SLOT_SERVER);
    if (!k)
        n--;
    save_slot[n + k] = 0;
    return 1;
}

/* After the save: its folder from XCreateSaveGame, then the upload. */
static void slot_upload(void)
{
    u16 wide[sizeof(save_slot)];
    u32 i, n, r;

    for (i = 0; i < sizeof(save_slot); i++)
        wide[i] = (u8)save_slot[i];
    save_path[0] = 0;
    r = ((fn_create_save)TES3X_NET_CREATE_SAVE)("U:\\", wide, OPEN_EXISTING, 0, save_path,
                                                sizeof(save_path) - sizeof(save_slot) - 4);
    n = tes3x_strlen(save_path);
    tes3x_log_hex3("net.save_slot", r, n, 0);
    if (r || !n)
        return;
    if (save_path[n - 1] != '\\')
        save_path[n++] = '\\';
    i = tes3x_strlen(save_slot);
    copy((u8 *)save_path + n, (const u8 *)save_slot, i);
    copy((u8 *)save_path + n + i, (const u8 *)".ess", 5);
    copy((u8 *)save_name, (const u8 *)save_slot, i);
    copy((u8 *)save_name + i, (const u8 *)".ess", 5);
    log_text("net.save_path", save_path);
    save_pending = 1;
}

#define EVENT_SAVE 22u /* the server asks for a save */
#define CHARGEN_STATE 0xBCu /* WorldController global, -1 once character generation is done */
static u32 save_requested, saves_requested, diagnostic_save;
#define EVENT_SNAPSHOT 36u
static u32 snapshot_token, snapshot_stage, snapshot_wait, snapshot_confirmed, snapshot_deferred;
static u32 snapshot_welcome, snapshot_since, snapshot_request, snapshot_local;
static void snapshot_begin(void)
{
    if (snapshot_stage || snapshot_wait)
        return;
    snapshot_local = (snapshot_local + 1) & 0x7fffffffu;
    if (!snapshot_local)
        snapshot_local++;
    snapshot_token = snapshot_request ? snapshot_request : snapshot_local;
    snapshot_request = 0;
    snapshot_stage = 1;
    snapshot_welcome = ses.welcomes;
    snapshot_since = now_us();
}

unsigned char __attribute__((thiscall)) tes3x_net_save(void *game, const char *file,
                                                      const char *display);

static u32 player_dead; /* a joined player's death waits for its respawn */

/* A requested save waits for the world: no menu, chargen done, the player named, alive. */
static void save_request_frame(void)
{
    const u8 *world = *(const u8 **)TES3X_NET_WORLD, *global;

    if (!save_requested || !plausible(world) || (diagnostic_save && (player_dead || world[0xD2])) ||
        !plausible(global = *(const u8 *const *)(world + CHARGEN_STATE)) ||
        *(const u32 *)(global + 0x34) != 0xBF800000u)
        return;
    if (!diagnostic_save && (snapshot_stage || snapshot_wait))
        return;
    save_requested = 0;
    saves_requested++;
    if (diagnostic_save)
        tes3x_net_save(**(void ***)TES3X_NET_DATA_HANDLER, save_slot, save_slot);
    else
        snapshot_begin();
}
