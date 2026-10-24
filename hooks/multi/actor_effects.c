/* An actor's active effects (the SPLM records of its magic instances), as the player's are shared
 * but per actor. The cell's authority sends a complete set when it changes, and again every
 * AEFF_REFRESH_US so the server's elapsed times stay close. Sets from the server are kept per actor
 * and re-created when the actor is loaded here and again when its mobile is made anew: the old
 * effects end, then each is activated at its saved state, which makes its callback rebuild the
 * attribute and skill modifiers. Not kept: bound effects, and the abilities, diseases and curses
 * that the actor's spell list re-applies on every load. */
#define EVENT_ACTOR_EFFECTS 47u /* refid, part u16, parts u16, then PLAYER_EFFECT_BYTES records */
#define AEFF_HEADER 8u
#define AEFF_CHUNK (EVENT_DATA - AEFF_HEADER)
#define AEFF_ACTORS 24u
#define AEFF_MAX 6u
#define AEFF_BODY (PLAYER_EFFECT_BYTES * AEFF_MAX)
#define AEFF_REFRESH_US 20000000u

static struct {
    u32 refid, size, hash, sent, in_size, in_parts, in_part;
    const u8 *mobile;
    u8 used, known, pending, open;
    u8 body[AEFF_BODY];
} aeffs[AEFF_ACTORS];
static u8 aeff_out[AEFF_BODY];
static u32 aeff_out_refid, aeff_out_size, aeff_out_part, aeff_out_parts, aeff_welcome;
static u32 aeffs_out, aeffs_in, aeffs_applied, aeffs_lost, aeffs_bad;

/* What identifies a set: sources, magnitudes and casters, not serials or elapsed time. */
static u32 aeff_hash(const u8 *body, u32 size)
{
    static const u8 spans[][2] = {{4, 12}, {16, 64}, {82, 2}, {92, 8}};
    u32 off, i, k, hash = 2166136261u;
    for (off = 0; off < size; off += PLAYER_EFFECT_BYTES)
        for (k = 0; k < 4; k++)
            for (i = 0; i < spans[k][1]; i++)
                hash = (hash ^ body[off + spans[k][0] + i]) * 16777619u;
    return hash ^ size;
}

static u32 aeff_find(u32 refid)
{
    u32 i;
    for (i = 0; i < AEFF_ACTORS; i++)
        if (aeffs[i].used && aeffs[i].refid == refid)
            return i;
    return AEFF_ACTORS;
}

static u32 aeff_slot(u32 refid)
{
    u32 i = aeff_find(refid), k;
    if (i != AEFF_ACTORS)
        return i;
    for (i = 0; i < AEFF_ACTORS && aeffs[i].used; i++)
        ;
    if (i == AEFF_ACTORS) {
        aeffs_lost++;
        return i;
    }
    for (k = 0; k < sizeof(aeffs[i]); k++)
        ((u8 *)&aeffs[i])[k] = 0;
    aeffs[i].used = 1;
    aeffs[i].refid = refid;
    return i;
}

static void aeff_session(void)
{
    u32 i;
    if (aeff_welcome == ses.welcomes)
        return;
    aeff_welcome = ses.welcomes;
    aeff_out_parts = 0;
    for (i = 0; i < AEFF_ACTORS; i++)
        aeffs[i].open = 0;
}

/* Drain the staged set over several frames without filling the reliable queue. */
static void aeff_send(void)
{
    u8 data[EVENT_DATA];
    u32 offset, n;
    while (aeff_out_part < aeff_out_parts && events_room() > 2) {
        offset = aeff_out_part * AEFF_CHUNK;
        n = aeff_out_size > offset ? aeff_out_size - offset : 0;
        if (n > AEFF_CHUNK)
            n = AEFF_CHUNK;
        put32le(data, aeff_out_refid);
        data[4] = (u8)aeff_out_part;
        data[5] = (u8)(aeff_out_part >> 8);
        data[6] = (u8)aeff_out_parts;
        data[7] = (u8)(aeff_out_parts >> 8);
        copy(data + AEFF_HEADER, aeff_out + offset, n);
        if (!event_queue(EVENT_ACTOR_EFFECTS, data, AEFF_HEADER + n))
            return;
        aeff_out_part++;
    }
    if (aeff_out_parts && aeff_out_part == aeff_out_parts) {
        aeff_out_parts = 0;
        aeffs_out++;
    }
}

/* Replace the actor's effects with the kept set. */
static void aeff_apply(u32 e, u8 *mobile, u8 *ref)
{
    u8 *controller = magic_controller(), *head, *instances[AEFF_MAX];
    u32 serials[AEFF_MAX], used = 0, off;

    if (!controller || !plausible(head = *(u8 **)(mobile + MOBILE_ACTIVE_EFFECTS + 4)))
        return;
    aeffs[e].pending = 0;
    for (off = 0; off < aeffs[e].size; off += PLAYER_EFFECT_BYTES)
        if (!effect_record_ok(aeffs[e].body + off, ref)) {
            aeffs_bad++;
            return;
        }
    effect_retire_all(mobile, ref, controller);
    for (off = 0; off < aeffs[e].size; off += PLAYER_EFFECT_BYTES)
        effect_restore(mobile, ref, controller, head, aeffs[e].body + off, serials, instances,
                       &used);
    aeffs_applied++;
    tes3x_log_hex3("net.aeffects_applied", aeffs[e].refid, aeffs[e].size / PLAYER_EFFECT_BYTES,
                   aeffs_bad);
}

/* An actor whose effects are arriving or waiting to be re-created is not yet ready for the
 * statistics that include them. */
static int aeff_busy(u32 refid)
{
    u32 e = aeff_find(refid);
    return e != AEFF_ACTORS && (aeffs[e].open || aeffs[e].pending);
}

/* On the actor send tick, for each loaded actor: re-create what arrived for it, or send its set
 * when it changed here. */
static void aeffects_frame(const u8 *mobile_in, u8 *ref, u32 refid, int mine)
{
    u8 *mobile = (u8 *)mobile_in;
    const u8 *head;
    u32 e, now, hash;
    int n, stale;

    aeff_session();
    aeff_send();
    if (!plausible(head = *(const u8 **)(mobile + MOBILE_ACTIVE_EFFECTS + 4)))
        return;
    e = aeff_find(refid);
    if (e == AEFF_ACTORS) {
        if (*(const u8 *const *)head == head || !mine)
            return;
        e = aeff_slot(refid);
        if (e == AEFF_ACTORS)
            return;
        aeffs[e].mobile = mobile;
    }
    if (aeffs[e].mobile != mobile) {
        aeffs[e].mobile = mobile;
        aeffs[e].pending = aeffs[e].known;
    }
    if (aeffs[e].pending && !aeffs[e].open) {
        aeff_apply(e, mobile, ref);
        return;
    }
    if (!mine || aeff_out_parts || aeffs[e].open)
        return;
    n = effect_collect(mobile, ref, aeff_out, AEFF_MAX, 1);
    if (n < 0) {
        aeffs_bad++;
        return;
    }
    now = now_us();
    hash = aeff_hash(aeff_out, (u32)n * PLAYER_EFFECT_BYTES);
    stale = n && aeffs[e].known && now - aeffs[e].sent >= AEFF_REFRESH_US;
    if (aeffs[e].known ? hash == aeffs[e].hash && !stale : !n)
        return;
    aeff_out_refid = refid;
    aeff_out_size = (u32)n * PLAYER_EFFECT_BYTES;
    aeff_out_part = 0;
    aeff_out_parts = (aeff_out_size + AEFF_CHUNK - 1) / AEFF_CHUNK;
    if (!aeff_out_parts)
        aeff_out_parts = 1;
    copy(aeffs[e].body, aeff_out, aeff_out_size);
    aeffs[e].size = aeff_out_size;
    aeffs[e].hash = hash;
    aeffs[e].sent = now;
    aeffs[e].known = 1;
    aeff_send();
}

static void aeffects_event(const struct event *ev)
{
    u32 refid, part, parts, n, e;

    if (ev->length < AEFF_HEADER || !(refid = get32le(ev->data)))
        return;
    part = ev->data[4] | ev->data[5] << 8;
    parts = ev->data[6] | ev->data[7] << 8;
    n = ev->length - AEFF_HEADER;
    aeff_session();
    e = aeff_slot(refid);
    if (e == AEFF_ACTORS)
        return;
    if (!part) {
        aeffs[e].known = aeffs[e].pending = 0;
        aeffs[e].open = 1;
        aeffs[e].in_size = aeffs[e].in_part = 0;
        aeffs[e].in_parts = parts;
    }
    if (!aeffs[e].open || !parts || part >= parts || part != aeffs[e].in_part ||
        parts != aeffs[e].in_parts || aeffs[e].in_size + n > AEFF_BODY) {
        aeffs[e].open = 0;
        aeffs_bad++;
        return;
    }
    copy(aeffs[e].body + aeffs[e].in_size, ev->data + AEFF_HEADER, n);
    aeffs[e].in_size += n;
    if (++aeffs[e].in_part < parts)
        return;
    aeffs[e].open = 0;
    if (aeffs[e].in_size % PLAYER_EFFECT_BYTES) {
        aeffs_bad++;
        return;
    }
    aeffs[e].size = aeffs[e].in_size;
    aeffs[e].hash = aeff_hash(aeffs[e].body, aeffs[e].size);
    aeffs[e].sent = now_us();
    aeffs[e].known = aeffs[e].pending = 1;
    aeffs_in++;
}

static void aeffects_stat(void)
{
    tes3x_log_hex3("net.aeffects", aeffs_out, aeffs_in, aeffs_applied);
    tes3x_log_hex3("net.aeffects_table", aeffs_lost, aeffs_bad, 0);
}
