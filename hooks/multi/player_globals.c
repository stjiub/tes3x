#define PLAYER_GLOBALS 19u
#define PLAYER_GLOBALS_MAX 128u
#define GLOBAL_NAME 32u
#define GLOBAL_ENTRY 4u /* value f32, then the name ending in zero */
#define RECORDS_GLOBALS 0x44 /* list: head +0x8; node: next +0x4, global +0x8 */
#define GLOBAL_OBJECT_ID 0x10
#define GLOBAL_VALUE 0x34

typedef u8 *(__attribute__((thiscall)) *fn_find_global)(void *records, const char *id);

/* The character's globals: every PC* one except those the engine or a script derives from other
 * replayed state (crime gold from bounty, PCGold from the inventory), and these, which vanilla
 * keeps per player under other names. CharGenState belongs to chargen and the preload finish. */
static const char *const globals_player[] = {
    "VampClan", "ExpFightersGuild", "ExpImperialCult", "ExpImperialLegion", "ExpMagesGuild",
    "ExpMoragTong", "ExpRedoran", "ExpTemple", "ExpThievesGuild", "WearingLegionUni",
    "WearingOrdinatorUni", "wearingHelmHHDA", "wraithguardEquipped"};
static const char *const globals_derived[] = {"PCGold", "PCHasCrimeGold", "PCHasGoldDiscount",
                                              "PCHasTurnIn"};

static u32 globals_sent[PLAYER_GLOBALS_MAX];
static u32 globals_known, globals_out, globals_in, globals_bad;

static int global_name_is(const u8 *name, const char *want)
{
    u32 a, b;

    do {
        a = *name++;
        b = (u8)*want++;
        a += a - 'A' < 26u ? 32 : 0;
        b += b - 'A' < 26u ? 32 : 0;
    } while (a && a == b);
    return a == b;
}

static int global_is_player(const u8 *name)
{
    u32 i;

    for (i = 0; i < sizeof(globals_derived) / sizeof(*globals_derived); i++)
        if (global_name_is(name, globals_derived[i]))
            return 0;
    if ((name[0] | 32) == 'p' && (name[1] | 32) == 'c')
        return 1;
    for (i = 0; i < sizeof(globals_player) / sizeof(*globals_player); i++)
        if (global_name_is(name, globals_player[i]))
            return 1;
    return 0;
}

static const u8 *globals_head(void)
{
    const u8 *records = faction_records(), *list;

    if (!records || !plausible(list = *(const u8 *const *)(records + RECORDS_GLOBALS)))
        return 0;
    return *(const u8 *const *)(list + 8);
}

static u32 global_name_length(const u8 *global)
{
    u32 n;

    for (n = 0; n < GLOBAL_NAME && global[GLOBAL_OBJECT_ID + n]; n++)
        ;
    return n < GLOBAL_NAME && n && script_safe(global + GLOBAL_OBJECT_ID, n + 1) ? n : 0;
}

/* The player's globals, absolute, once each changes; kept by their order among them. */
static void globals_scan(int send)
{
    const u8 *node = globals_head(), *global = 0;
    u8 data[EVENT_DATA];
    u32 pending[EVENT_DATA / (GLOBAL_ENTRY + 2)], values[EVENT_DATA / (GLOBAL_ENTRY + 2)];
    u32 guard, size = 0, i, k = 0, n = 0, length = 2, now;

    for (guard = 0; guard < 65536; guard++) {
        global = 0;
        while (plausible(node)) {
            global = *(const u8 *const *)(node + 8);
            node = *(const u8 *const *)(node + 4);
            if (!plausible(global) || !(size = global_name_length(global)) ||
                !global_is_player(global + GLOBAL_OBJECT_ID)) {
                global = 0;
                continue;
            }
            if (k == PLAYER_GLOBALS_MAX) {
                globals_bad++;
                global = 0;
                node = 0;
                break;
            }
            now = get32le(global + GLOBAL_VALUE);
            if ((now & 0x7F800000u) == 0x7F800000u) { /* not finite: nothing can keep it */
                if (!globals_known)
                    globals_bad++;
                k++;
                global = 0;
                continue;
            }
            if (globals_known && now == globals_sent[k]) {
                k++;
                global = 0;
                continue;
            }
            break;
        }
        if (n && (!global || length + GLOBAL_ENTRY + size + 1 > EVENT_DATA)) {
            data[0] = PLAYER_GLOBALS;
            data[1] = (u8)n;
            if (send && !event_queue(EVENT_PLAYER, data, length)) {
                snapshot_deferred++;
                return;
            }
            for (i = 0; i < n; i++)
                globals_sent[pending[i]] = values[i];
            globals_out += send;
            n = 0;
            length = 2;
        }
        if (!global)
            break;
        now = get32le(global + GLOBAL_VALUE);
        put32le(data + length, now);
        copy(data + length + GLOBAL_ENTRY, global + GLOBAL_OBJECT_ID, size + 1);
        length += GLOBAL_ENTRY + size + 1;
        values[n] = now;
        pending[n++] = k++;
    }
    globals_known = 1;
}

/* Direct writes, as a save load restores them; only the player's are accepted. */
static void globals_apply(const u8 *data, u32 length)
{
    void *records = faction_records();
    u8 *global;
    u32 off = 2, end, i;

    for (i = 0; i < data[1]; i++) {
        if (off + GLOBAL_ENTRY >= length)
            break;
        for (end = off + GLOBAL_ENTRY; end < length && data[end]; end++)
            ;
        if (end == length || end == off + GLOBAL_ENTRY || end - off - GLOBAL_ENTRY >= GLOBAL_NAME ||
            !script_safe(data + off + GLOBAL_ENTRY, end - off - GLOBAL_ENTRY + 1))
            break;
        off = end + 1;
    }
    if (i != data[1] || off != length || !records) {
        globals_bad++;
        return;
    }
    for (i = 0, off = 2; i < data[1]; i++) {
        global = ((fn_find_global)TES3X_NET_FIND_GLOBAL)(records,
                                                          (const char *)data + off + GLOBAL_ENTRY);
        if (!plausible(global) || !global_is_player(data + off + GLOBAL_ENTRY)) {
            globals_bad++;
            log_text("net.global_unknown", (const char *)data + off + GLOBAL_ENTRY);
        } else {
            copy(global + GLOBAL_VALUE, data + off, 4);
            globals_in++;
        }
        while (data[off + GLOBAL_ENTRY])
            off++;
        off += GLOBAL_ENTRY + 1;
    }
    tes3x_log("net.player_globals_applied", data[1]);
}

static void global_query(const char *id)
{
    void *records = faction_records();
    const u8 *global = records ? ((fn_find_global)TES3X_NET_FIND_GLOBAL)(records, id) : 0;

    if (!plausible(global)) {
        log_text("net.global_unknown", id);
        return;
    }
    tes3x_log_hex3("net.global", get32le(global + GLOBAL_VALUE), global[0x30],
                   (u32)global_is_player(global + GLOBAL_OBJECT_ID));
#ifdef TES3X_CONSOLE
    tes3x_console_reply("%s >> %.2f", id, (double)*(const float *)(global + GLOBAL_VALUE));
#endif
}
