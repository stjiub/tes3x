/* The clock globals hang off the WorldController, in HELLO's order: GameHour +0xA4, Day +0xB0,
 * Month +0xAC, Year +0xA8, DaysPassed +0xB4, TimeScale +0xB8. A global's value is at +0x34. */
static const u32 clock_offsets[CLOCK_GLOBALS] = {0xA4, 0xB0, 0xAC, 0xA8, 0xB4, 0xB8};
#define GLOBAL_VALUE 0x34

static float *clock_global(u32 i)
{
    const u8 *world = *(const u8 **)TES3X_NET_WORLD, *global;

    if (!plausible(world) || !plausible(global = *(const u8 *const *)(world + clock_offsets[i])))
        return 0;
    return (float *)(global + GLOBAL_VALUE);
}

/* Game thread, in the world: while joined the server's clock overwrites the globals, latest
 * wins; either way this console's clock is kept for the next HELLO. */
static void clock_frame(void)
{
    u8 local[CLOCK_BYTES], server[CLOCK_BYTES];
    float *globals[CLOCK_GLOBALS];
    u32 i, flags, received;

    for (i = 0; i < CLOCK_GLOBALS; i++)
        if (!(globals[i] = clock_global(i)))
            return;
    flags = lock();
    received = game_clock.received;
    copy(server, game_clock.server, CLOCK_BYTES);
    unlock(flags);
    if (ses.state == SESSION_JOINED && received != game_clock.applied) {
        for (i = 0; i < CLOCK_GLOBALS; i++)
            copy((u8 *)globals[i], server + 4 * i, 4);
        if (!game_clock.applied)
            tes3x_log_hex3("net.clock_set", (u32)(int)(*globals[0] * 1000.0f),
                           (u32)(int)*globals[1], (u32)(int)*globals[2]);
        game_clock.applied = received;
    }
    for (i = 0; i < CLOCK_GLOBALS; i++)
        copy(local + 4 * i, (const u8 *)globals[i], 4);
    flags = lock();
    copy(game_clock.local, local, CLOCK_BYTES);
    game_clock.local_valid = 1;
    unlock(flags);
}

static void clock_stat(void)
{
    const float *hour = clock_global(0), *day = clock_global(1), *scale = clock_global(5);

    tes3x_log_hex3("net.clock", game_clock.received, game_clock.applied, 0);
    if (hour && day && scale)
        tes3x_log_hex3("net.clock_now", (u32)(int)(*hour * 1000.0f), (u32)(int)*day,
                       (u32)(int)*scale);
}

/* Weather. A region's weather is Region+0x74; when the hours between changes have passed the
 * engine rolls every region at once, and each frame the sky transitions to the weather of the
 * player's region when it differs. While joined only the lowest client id in the session rolls;
 * the others NOP the roll. Each change a client sees goes to the server as a WEATHER event
 * (flags, count, then region index and weather), and the server sends the result to every client,
 * the sender too, so all end in the server's order. After WELCOME a client offers its whole table,
 * of which the server keeps the regions it does not know yet, and the server replays its own.
 * Regions are numbered by their place in the region list, the same under one load order. */
#define WEATHER_OFFER 1u
#define WEATHER_ENTRY 3u
#define WEATHER_PER_EVENT ((EVENT_DATA - 2) / WEATHER_ENTRY)
#define WEATHERS 10u
#define REGIONS 256u
#define RECORDS_REGIONS 0x4C /* list: head +0x8; node: next +0x4, region +0x8 */
#define REGION_WEATHER 0x74
#define REGION_MODIFIED 0x14 /* vtable offset; randomizeWeather passes 1 */
#define WORLD_WEATHER 0x58
#define WEATHER_REGION 0x1D0 /* the player's region */
#define WEATHER_TRANSITION 0x170 /* how far the sky is from the current weather to the next */

typedef void(__attribute__((thiscall)) *fn_object_flag)(void *object, int on);

static u8 *const weather_roll = (u8 *)TES3X_NET_WEATHER_ROLL;
static u8 weather_call[5], weather_seen[REGIONS];
static u8 *regions[REGIONS];
static u32 weather_saved, weather_blocked, weather_welcome, weather_forced;
static u32 weather_sent, weather_received, weather_applied;

/* The region list in load order, into regions; 0 if it is not there or looks wrong. */
static u32 regions_list(void)
{
    const u8 *handler = *(const u8 **)TES3X_NET_DATA_HANDLER, *records, *list, *node;
    u32 n = 0;

    if (!plausible(handler) || !plausible(records = *(const u8 *const *)handler) ||
        !plausible(list = *(const u8 *const *)(records + RECORDS_REGIONS)))
        return 0;
    for (node = *(const u8 *const *)(list + 8); plausible(node) && n < REGIONS;
         node = *(const u8 *const *)(node + 4))
        if (!plausible(regions[n++] = *(u8 *const *)(node + 8)))
            return 0;
    return n;
}

static void weather_gate(u32 block)
{
    u32 cr0, flags, i;

    if (block == weather_blocked || weather_saved == 2)
        return;
    if (!weather_saved) {
        if (weather_roll[0] != 0xE8) {
            tes3x_log_hex3("net.weather_roll_unexpected", weather_roll[0], weather_roll[1], 0);
            weather_saved = 2;
            return;
        }
        copy(weather_call, weather_roll, 5);
        weather_saved = 1;
    }
    flags = lock();
    __asm__ volatile("movl %%cr0, %0" : "=r"(cr0));
    __asm__ volatile("movl %0, %%cr0" : : "r"(cr0 & ~CR0_WP) : "memory");
    for (i = 0; i < 5; i++)
        weather_roll[i] = block ? 0x90 : weather_call[i];
    __asm__ volatile("movl %0, %%cr0" : : "r"(cr0) : "memory");
    unlock(flags);
    weather_blocked = block;
    tes3x_log("net.weather_rolls", !block);
}

static int weather_roller(void)
{
    u32 i;

    if (weather_forced)
        return weather_forced - 1;
    for (i = 0; i < PEERS; i++)
        if (peers[i].client && peers[i].client < ses.client)
            return 0;
    return 1;
}

/* Game thread, each frame: the roll gate, then the offer after WELCOME or the regions changed
 * since the last frame, whole or not at all. */
static void weather_frame(int in_world)
{
    u8 entries[REGIONS * WEATHER_ENTRY], data[EVENT_DATA];
    u32 joined = ses.state == SESSION_JOINED && in_world, n, i, count = 0, offer, room, lk, part;

    weather_gate(weather_forced ? weather_forced == 1 : joined && !weather_roller());
    if (!joined || !(n = regions_list()))
        return;
    offer = weather_welcome != ses.welcomes;
    for (i = 0; i < n; i++) {
        u8 w = (u8) * (const int *)(regions[i] + REGION_WEATHER);
        if (!offer && w == weather_seen[i])
            continue;
        entries[count * WEATHER_ENTRY] = (u8)i;
        entries[count * WEATHER_ENTRY + 1] = (u8)(i >> 8);
        entries[count * WEATHER_ENTRY + 2] = w;
        count++;
    }
    lk = lock();
    room = EVENTS_OUT - (rel.out_next - rel.out_first);
    unlock(lk);
    if (room < (count + WEATHER_PER_EVENT - 1) / WEATHER_PER_EVENT)
        return;
    for (i = 0; i < count; i += part) {
        part = count - i < WEATHER_PER_EVENT ? count - i : WEATHER_PER_EVENT;
        data[0] = (u8)(offer ? WEATHER_OFFER : 0);
        data[1] = (u8)part;
        copy(data + 2, entries + i * WEATHER_ENTRY, part * WEATHER_ENTRY);
        event_queue(EVENT_WEATHER, data, 2 + part * WEATHER_ENTRY);
    }
    for (i = 0; i < count; i++)
        weather_seen[entries[i * WEATHER_ENTRY] | entries[i * WEATHER_ENTRY + 1] << 8] =
            entries[i * WEATHER_ENTRY + 2];
    weather_sent += count;
    if (offer || count)
        tes3x_log_hex3("net.weather_sent", offer, count, n);
    weather_welcome = ses.welcomes;
}

/* The server's weather: written as randomizeWeather writes a roll, so the sky transitions as it
 * does for one, and remembered as seen so it is not sent back. */
static void weather_event(const struct event *e)
{
    u32 n = regions_list(), i, index, w;
    int *at;

    if (e->length < 2 || (e->data[0] & WEATHER_OFFER))
        return;
    weather_received++;
    for (i = 0; i < e->data[1] && 2 + (i + 1) * WEATHER_ENTRY <= e->length; i++) {
        index = e->data[2 + i * WEATHER_ENTRY] | e->data[3 + i * WEATHER_ENTRY] << 8;
        w = e->data[4 + i * WEATHER_ENTRY];
        if (index >= n || w >= WEATHERS)
            continue;
        weather_seen[index] = (u8)w;
        at = (int *)(regions[index] + REGION_WEATHER);
        if (*at == (int)w)
            continue;
        *at = (int)w;
        ((fn_object_flag)(*(void *const *const *)regions[index])[REGION_MODIFIED / 4])(
            regions[index], 1);
        weather_applied++;
        tes3x_log_hex3("net.weather_set", index, w, e->origin);
    }
}

static void weather_stat(void)
{
    const u8 *world = *(const u8 **)TES3X_NET_WORLD, *controller, *here;
    u32 n = regions_list(), i;

    tes3x_log_hex3("net.weather", weather_sent, weather_received, weather_applied);
    tes3x_log_hex3("net.weather_roller", weather_roller(), weather_blocked, n);
    if (!plausible(world) || !plausible(controller = *(const u8 *const *)(world + WORLD_WEATHER)) ||
        !plausible(here = *(const u8 *const *)(controller + WEATHER_REGION)))
        return;
    for (i = 0; i < n && regions[i] != here; i++)
        ;
    tes3x_log_hex3("net.weather_here", i, *(const u32 *)(here + REGION_WEATHER),
                   (u32)(int)(*(const float *)(controller + WEATHER_TRANSITION) * 1000.0f));
}
