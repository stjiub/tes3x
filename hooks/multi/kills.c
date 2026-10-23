/* Kill counts. The engine counts a death under the dead actor's base object (MobileActor::onDeath
 * calls KillCounter::increment) and GetDeadCount reads it. A death the authority reports also goes
 * to the server as KILL with that object's id; the server answers every console with KILLS, the id
 * and the number of deaths it keeps under it, which each console writes into its own counter once
 * a second, so a count survives a restart and holds for actors not loaded here. */
#define EVENT_KILL 45u  /* refid, then the object id and a zero */
#define EVENT_KILLS 46u /* count, then the object id and a zero; only the server sends it */
#define KILL_ID 32u
#define KILL_IDS 256u
#define KILL_POLL_US 1000000u
#define WORLD_KILL_COUNTER 0x60
#define KILL_LIST 8 /* the counter's list of {count, object} */
#define OBJECT_KILL_KEY 0x154 /* vtable offset: the object the counter counts under */

typedef void *(__attribute__((thiscall)) *fn_kill_find)(void *counter, const void *object);
typedef const u8 *(__attribute__((thiscall)) *fn_kill_key)(const void *object);

static struct {
    char id[KILL_ID];
    u32 count;
} kills_known[KILL_IDS];
static u32 kills_known_count, kills_polled, kills_out, kills_in, kills_written, kills_dropped;

/* A death this console saw as the authority: tell the server under which object it is counted. */
static void kill_report(const u8 *ref, u32 refid)
{
    const u8 *base = *(const u8 *const *)(ref + REF_BASE), *key;
    const char *id;
    u8 data[4 + KILL_ID];
    u32 n;

    if (!plausible(base) || !plausible(*(const void *const *)base) ||
        !plausible(key = ((fn_kill_key)(*(void *const *const *)base)[OBJECT_KILL_KEY / 4])(base)) ||
        !(id = object_id(key)))
        return;
    put32le(data, refid);
    for (n = 0; id[n] && n < KILL_ID - 1; n++)
        data[4 + n] = (u8)id[n];
    data[4 + n] = 0;
    if (event_queue(EVENT_KILL, data, 5 + n))
        kills_out++;
}

static void kills_event(const struct event *e)
{
    u32 i, n;

    if (e->length < 6 || e->data[e->length - 1] || e->length - 5 >= KILL_ID)
        return;
    for (i = 0; i < kills_known_count; i++)
        if (same_name(kills_known[i].id, (const char *)e->data + 4))
            break;
    if (i == kills_known_count) {
        if (i == KILL_IDS) {
            kills_dropped++;
            return;
        }
        for (n = 0; n < e->length - 4; n++)
            kills_known[i].id[n] = (char)e->data[4 + n];
        kills_known_count++;
    }
    kills_known[i].count = get32le(e->data);
    kills_in++;
}

/* Game thread: write the server's counts into the engine's counter. */
static void kills_frame(void)
{
    const u8 *world = *(const u8 *const *)TES3X_NET_WORLD, *object;
    u8 *counter, *list;
    u32 *entry, i, now = now_us();

    if (ses.state != SESSION_JOINED || !kills_known_count || now - kills_polled < KILL_POLL_US ||
        !plausible(world) || !plausible(counter = *(u8 *const *)(world + WORLD_KILL_COUNTER)) ||
        !plausible(list = *(u8 *const *)(counter + KILL_LIST)))
        return;
    kills_polled = now;
    for (i = 0; i < kills_known_count; i++) {
        if (!plausible(object = resolve_object(kills_known[i].id)))
            continue;
        entry = ((fn_kill_find)TES3X_NET_KILL_FIND)(counter, object);
        if (!entry) {
            if (!plausible(entry = ((fn_engine_allocate)TES3X_NET_ENGINE_ALLOCATE)(
                               (void *)TES3X_NET_HEAP, 8, "tes3x", 0)))
                continue;
            entry[0] = kills_known[i].count;
            entry[1] = (u32)object;
            ((fn_list_append)TES3X_NET_LIST_APPEND)(list, entry);
            if (kills_written++ < 16)
                tes3x_log_hex3("net.kill_write", i, 0xFFFFFFFFu, kills_known[i].count);
        } else if (entry[0] != kills_known[i].count) {
            if (kills_written++ < 16)
                tes3x_log_hex3("net.kill_write", i, entry[0], kills_known[i].count);
            entry[0] = kills_known[i].count;
        }
    }
}

static void kills_stat(void)
{
    tes3x_log_hex3("net.kills", kills_out, kills_in, kills_written);
    tes3x_log_hex3("net.kills_table", kills_known_count, kills_dropped, 0);
}
