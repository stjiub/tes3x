#define PLAYER_FACTIONS 18u
#define FACTIONS_MAX 512u
#define FACTION_ID 32u
#define FACTION_ENTRY 9u /* bits, rank i32, reputation i32, then the id ending in zero */
#define FACTION_RANK 0x10u /* bits: the low two are the membership flags, these say what applies */
#define FACTION_REPUTATION 0x20u
#define FACTION_FLAGS 0x40u
#define FACTION_ALL 0x70u
#define FACTION_MEMBERSHIP 3u
#define RECORDS_FACTIONS 0x34 /* list: head +0x8; node: next +0x4, faction +0x8 */
#define FACTION_OBJECT_ID 0x10
#define FACTION_PLAYER 0x294 /* rank, reputation, membership flags (1 joined, 2 expelled) */

typedef u8 *(__attribute__((thiscall)) *fn_find_faction)(void *records, const char *id);
typedef void(__attribute__((thiscall)) *fn_faction_load)(void *faction, const int *data);
typedef void (*fn_update_stats)(void);

static int factions_sent[FACTIONS_MAX][3];
static u32 factions_known, factions_out, factions_in, factions_bad;

static void *faction_records(void)
{
    const u8 *handler = *(const u8 **)TES3X_NET_DATA_HANDLER;
    void *records;

    return plausible(handler) && plausible(records = *(void *const *)handler) ? records : 0;
}

static const u8 *factions_head(void)
{
    const u8 *records = faction_records(), *list;

    if (!records || !plausible(list = *(const u8 *const *)(records + RECORDS_FACTIONS)))
        return 0;
    return *(const u8 *const *)(list + 8);
}

static u32 faction_id_length(const u8 *faction)
{
    u32 n;

    for (n = 0; n < FACTION_ID && faction[FACTION_OBJECT_ID + n]; n++)
        ;
    return n < FACTION_ID && n && script_safe(faction + FACTION_OBJECT_ID, n + 1) ? n : 0;
}

/* Each faction's absolute player state once it changes, kept by its place in the list. The
 * first scan after READY skips untouched factions: the replay already matches the server. */
static void factions_scan(int send)
{
    const u8 *node = factions_head(), *faction = 0;
    const int *now;
    u8 data[EVENT_DATA];
    u32 pending[EVENT_DATA / FACTION_ENTRY], guard, size = 0, i, k = 0, n = 0, length = 2;
    int values[EVENT_DATA / FACTION_ENTRY][3];

    for (guard = 0; guard < 65536; guard++) {
        faction = 0;
        while (plausible(node) && k < FACTIONS_MAX) {
            faction = *(const u8 *const *)(node + 8);
            node = *(const u8 *const *)(node + 4);
            if (!plausible(faction)) {
                faction = 0;
                continue;
            }
            now = (const int *)(faction + FACTION_PLAYER);
            if (factions_known ? now[0] == factions_sent[k][0] && now[1] == factions_sent[k][1] &&
                                     now[2] == factions_sent[k][2]
                               : !now[0] && !now[1] && !now[2]) {
                for (i = 0; i < 3; i++)
                    factions_sent[k][i] = now[i];
                k++;
                faction = 0;
                continue;
            }
            if (!(size = faction_id_length(faction))) {
                factions_bad++;
                k++;
                faction = 0;
                continue;
            }
            break;
        }
        if (plausible(node) && k == FACTIONS_MAX && !faction)
            factions_bad++;
        if (n && (!faction || length + FACTION_ENTRY + size + 1 > EVENT_DATA)) {
            data[0] = PLAYER_FACTIONS;
            data[1] = (u8)n;
            if (send && !event_queue(EVENT_PLAYER, data, length)) {
                snapshot_deferred++;
                return;
            }
            for (i = 0; i < n; i++)
                copy((u8 *)factions_sent[pending[i]], (const u8 *)values[i], 12);
            factions_out += send;
            n = 0;
            length = 2;
        }
        if (!faction)
            break;
        now = (const int *)(faction + FACTION_PLAYER);
        data[length] = (u8)(FACTION_ALL | ((u32)now[2] & FACTION_MEMBERSHIP));
        put32le(data + length + 1, (u32)now[0]);
        put32le(data + length + 5, (u32)now[1]);
        copy(data + length + FACTION_ENTRY, faction + FACTION_OBJECT_ID, size + 1);
        length += FACTION_ENTRY + size + 1;
        copy((u8 *)values[n], (const u8 *)now, 12);
        pending[n++] = k++;
    }
    factions_known = 1;
}

/* Apply what an event's bits select through Faction::loadPlayerData, as a save load does. */
static void factions_apply(const u8 *data, u32 length)
{
    void *records = faction_records();
    u8 *faction;
    int state[3];
    u32 off = 2, end, i, bits;

    for (i = 0; i < data[1]; i++) {
        if (off + FACTION_ENTRY >= length)
            break;
        for (end = off + FACTION_ENTRY; end < length && data[end]; end++)
            ;
        if (end == length || end == off + FACTION_ENTRY || end - off - FACTION_ENTRY >= FACTION_ID ||
            !script_safe(data + off + FACTION_ENTRY, end - off - FACTION_ENTRY + 1) ||
            data[off] & 0x8C)
            break;
        off = end + 1;
    }
    if (i != data[1] || off != length || !records) {
        factions_bad++;
        return;
    }
    for (i = 0, off = 2; i < data[1]; i++) {
        bits = data[off];
        faction = ((fn_find_faction)TES3X_NET_FIND_FACTION)(records,
                                                            (const char *)data + off + FACTION_ENTRY);
        if (!plausible(faction)) {
            factions_bad++;
            log_text("net.faction_unknown", (const char *)data + off + FACTION_ENTRY);
        } else {
            copy((u8 *)state, faction + FACTION_PLAYER, 12);
            if (bits & FACTION_RANK)
                state[0] = (int)get32le(data + off + 1);
            if (bits & FACTION_REPUTATION)
                state[1] = (int)get32le(data + off + 5);
            if (bits & FACTION_FLAGS)
                state[2] = (int)(((u32)state[2] & ~FACTION_MEMBERSHIP) | (bits & FACTION_MEMBERSHIP));
            ((fn_faction_load)TES3X_NET_FACTION_LOAD)(faction, state);
            factions_in++;
        }
        while (data[off + FACTION_ENTRY])
            off++;
        off += FACTION_ENTRY + 1;
    }
    ((fn_update_stats)TES3X_NET_UPDATE_STATS_PANE)();
    tes3x_log("net.player_factions_applied", data[1]);
}

static void faction_query(const char *id)
{
    void *records = faction_records();
    const u8 *faction = records ? ((fn_find_faction)TES3X_NET_FIND_FACTION)(records, id) : 0;
    const int *now;

    if (!plausible(faction)) {
        log_text("net.faction_unknown", id);
        return;
    }
    now = (const int *)(faction + FACTION_PLAYER);
    tes3x_log_hex3("net.faction", (u32)now[0], (u32)now[1], (u32)now[2]);
}
