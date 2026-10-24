/* What an actor's mobile holds beyond ACTORS: attributes, an NPC's skills, barter gold and the
 * corpse hourstamp. On the actor send tick each loaded actor is read against its last read; the
 * cell's authority sends what changed, any console sends a change of barter gold (the player
 * trading may follow the merchant). Values from the server are kept per actor and written when
 * the actor is loaded here, and again when its mobile is made anew. Current attributes are totals
 * that include the actor's active effects (actor_effects.c), so they are written after those. */
#define EVENT_ACTOR_STATS 44u /* refid, count, then (slot u16, value u32) */
#define ASTAT_ENTRY 6u
#define ASTAT_PER_EVENT ((EVENT_DATA - 5) / ASTAT_ENTRY)
#define ASTAT_ATTRIBUTE_CURRENT 8u
#define ASTAT_GOLD 16u
#define ASTAT_STAMP 17u
#define ASTAT_SKILL 18u
#define ASTAT_SLOTS (ASTAT_SKILL + SKILLS)
#define ASTAT_ACTORS 256u
#define MOBILE_BARTER 0x360 /* int */
#define MOBILE_STAMP 0x36A  /* u16 */

static struct {
    u32 refid;
    const u8 *mobile;
    u32 read[ASTAT_SLOTS], kept[ASTAT_SLOTS];
    u8 known[ASTAT_SLOTS], pending[ASTAT_SLOTS], fresh, used;
} astats[ASTAT_ACTORS];
static int aeff_busy(u32 refid);
static u32 astat_next, astats_out, astats_in, astats_applied, astats_lost, astats_refused;

static u32 astat_read(const u8 *mobile, u32 slot)
{
    if (slot < ASTAT_GOLD)
        return *(const u32 *)(mobile + MOBILE_ATTRIBUTES + 0xC * (slot & 7) + STAT_BASE +
                              (slot >= ASTAT_ATTRIBUTE_CURRENT ? 4 : 0));
    if (slot == ASTAT_GOLD)
        return *(const u32 *)(mobile + MOBILE_BARTER);
    if (slot == ASTAT_STAMP)
        return *(const u16 *)(mobile + MOBILE_STAMP);
    return *(const u32 *)(mobile + MOBILE_SKILLS + 0x10 * (slot - ASTAT_SKILL) + STAT_BASE);
}

static void astat_write(u8 *mobile, u32 slot, u32 value)
{
    if (slot < ASTAT_GOLD)
        put32le(mobile + MOBILE_ATTRIBUTES + 0xC * (slot & 7) + STAT_BASE +
                    (slot >= ASTAT_ATTRIBUTE_CURRENT ? 4 : 0), value);
    else if (slot == ASTAT_GOLD)
        put32le(mobile + MOBILE_BARTER, value);
    else if (slot == ASTAT_STAMP)
        *(u16 *)(mobile + MOBILE_STAMP) = (u16)value;
    else
        put32le(mobile + MOBILE_SKILLS + 0x10 * (slot - ASTAT_SKILL) + STAT_BASE, value);
}

static int astat_float(u32 slot)
{
    return slot < ASTAT_GOLD || slot >= ASTAT_SKILL;
}

/* The entry for refid; a new one takes a free slot, else one with nothing kept. */
static u32 astat_slot(u32 refid)
{
    u32 i, n, k, kept;

    for (i = 0; i < ASTAT_ACTORS; i++)
        if (astats[i].used && astats[i].refid == refid)
            return i;
    for (i = 0; i < ASTAT_ACTORS && astats[i].used; i++)
        ;
    for (n = 0; i == ASTAT_ACTORS && n < ASTAT_ACTORS; n++) {
        for (k = 0, kept = 0; k < ASTAT_SLOTS; k++)
            kept |= astats[astat_next].known[k];
        if (!kept)
            i = astat_next;
        astat_next = (astat_next + 1) % ASTAT_ACTORS;
    }
    if (i == ASTAT_ACTORS) {
        i = astat_next;
        astat_next = (astat_next + 1) % ASTAT_ACTORS;
        astats_lost++;
    }
    for (k = 0; k < sizeof(astats[i]); k++)
        ((u8 *)&astats[i])[k] = 0;
    astats[i].used = 1;
    astats[i].refid = refid;
    astats[i].mobile = 0;
    return i;
}

/* Attributes and skills through their Set commands, which keep the dependent statistics in step,
 * then the exact values; the rest directly. */
static void astat_apply(u32 i, u8 *mobile, u8 *ref, u32 limit)
{
    u32 slot, pass;
    float value;

    for (pass = 0; pass < 2; pass++)
        for (slot = 0; slot < limit; slot++) {
            if (!astats[i].pending[slot] || (slot >= ASTAT_ATTRIBUTE_CURRENT && slot < ASTAT_GOLD) !=
                                                (pass == 1))
                continue;
            if (astat_float(slot)) {
                copy((u8 *)&value, (const u8 *)&astats[i].kept[slot], 4);
                if (!float_within((const u8 *)&astats[i].kept[slot], 1, STAT_LIMIT)) {
                    astats_refused++;
                    continue;
                }
                if (slot < ASTAT_ATTRIBUTE_CURRENT && astat_read(mobile, slot) != astats[i].kept[slot])
                    player_set(ref, "Set", attribute_names[slot], round_int(value));
                else if (slot >= ASTAT_SKILL && astat_read(mobile, slot) != astats[i].kept[slot])
                    player_set(ref, "Set", skill_names[slot - ASTAT_SKILL], round_int(value));
            }
            astat_write(mobile, slot, astats[i].kept[slot]);
            astats_applied++;
            if (astats_applied <= 32)
                tes3x_log_hex3("net.astat_applied", astats[i].refid, slot, astats[i].kept[slot]);
        }
    for (slot = 0; slot < ASTAT_SLOTS; slot++)
        astats[i].pending[slot] = 0;
}

/* On the actor send tick, for each loaded actor: write what arrived for it, or send what changed
 * here. Values read on first sight, or right after a write, are taken as they are. */
static void astats_frame(const u8 *mobile_in, u8 *ref, u32 refid, int mine)
{
    u8 *mobile = (u8 *)mobile_in, data[EVENT_DATA];
    u32 e = astat_slot(refid), limit = is_npc(ref) ? ASTAT_SLOTS : ASTAT_SKILL, slot, n, value;
    u32 changed[ASTAT_SLOTS], count = 0, chunk, k;

    if (aeff_busy(refid))
        return; /* The effects go first: the values here are the totals they led to. */
    if (astats[e].mobile != mobile) {
        astats[e].mobile = mobile;
        astats[e].fresh = 1;
        for (slot = 0; slot < ASTAT_SLOTS; slot++)
            astats[e].pending[slot] = astats[e].known[slot];
    }
    for (slot = 0, n = 0; slot < limit; slot++)
        n |= astats[e].pending[slot];
    if (n) {
        astat_apply(e, mobile, ref, limit);
        for (slot = 0; slot < limit; slot++)
            astats[e].read[slot] = astat_read(mobile, slot);
        astats[e].fresh = 0;
        return;
    }
    if (astats[e].fresh) {
        for (slot = 0; slot < limit; slot++)
            astats[e].read[slot] = astat_read(mobile, slot);
        astats[e].fresh = 0;
        return;
    }
    for (slot = 0; slot < limit; slot++) {
        value = astat_read(mobile, slot);
        if (value == astats[e].read[slot])
            continue;
        if (mine || slot == ASTAT_GOLD)
            changed[count++] = slot;
        else
            astats[e].read[slot] = value;
    }
    for (n = 0; n < count; n += chunk) {
        chunk = count - n < ASTAT_PER_EVENT ? count - n : ASTAT_PER_EVENT;
        if (events_room() < 2) {
            tes3x_log("net.event_full", EVENT_ACTOR_STATS);
            return;
        }
        put32le(data, refid);
        data[4] = (u8)chunk;
        for (k = 0; k < chunk; k++) {
            data[5 + k * ASTAT_ENTRY] = (u8)changed[n + k];
            data[6 + k * ASTAT_ENTRY] = 0;
            put32le(data + 7 + k * ASTAT_ENTRY, astat_read(mobile, changed[n + k]));
        }
        if (!event_queue(EVENT_ACTOR_STATS, data, 5 + chunk * ASTAT_ENTRY))
            return;
        for (k = 0; k < chunk; k++) {
            value = get32le(data + 7 + k * ASTAT_ENTRY);
            astats[e].read[changed[n + k]] = value;
            astats[e].kept[changed[n + k]] = value;
            astats[e].known[changed[n + k]] = 1;
        }
        astats_out += chunk;
    }
}

static void astats_event(const struct event *e)
{
    u32 refid, i, k, slot;

    if (e->length < 5 || e->length != 5 + e->data[4] * ASTAT_ENTRY || !(refid = get32le(e->data)))
        return;
    i = astat_slot(refid);
    for (k = 0; k < e->data[4]; k++) {
        slot = e->data[5 + k * ASTAT_ENTRY] | (u32)e->data[6 + k * ASTAT_ENTRY] << 8;
        if (slot >= ASTAT_SLOTS) {
            astats_refused++;
            continue;
        }
        astats[i].kept[slot] = get32le(e->data + 7 + k * ASTAT_ENTRY);
        astats[i].known[slot] = 1;
        astats[i].pending[slot] = 1;
        astats_in++;
    }
}

/* tes3xnet astat 0xREFID SLOT [VALUE]: a slot of a loaded actor's mobile, written if VALUE is
 * given (for exec scripts: barter gold has no command). */
static void astat_query(const char *text)
{
    u32 refid = 0, slot = 0, value = 0, digit, i;
    int sign = 1;
    const u8 *ref, *mobile;
    float shown;

    if (text[0] != '0' || (text[1] | 32) != 'x')
        return;
    for (i = 2; text[i] && text[i] != ' '; i++) {
        digit = (u32)(text[i] | 32);
        refid = refid << 4 | (digit <= '9' ? digit - '0' : digit - 'a' + 10);
    }
    text = number(skip(text + i), &slot);
    if (!text || slot >= ASTAT_SLOTS || !plausible(ref = actor_ref(refid)) ||
        !plausible(mobile = ref_mobile((u8 *)ref))) {
        tes3x_log("net.astat_unknown", refid);
        return;
    }
    if (*skip(text)) {
        text = skip(text);
        if (*text == '-') {
            sign = -1;
            text++;
        }
        if (number(text, &value))
            astat_write((u8 *)mobile, slot, (u32)((int)value * sign));
    }
    value = astat_read(mobile, slot);
    tes3x_log_hex3("net.astat", refid, slot, value);
    if (astat_float(slot))
        copy((u8 *)&shown, (const u8 *)&value, 4);
    else
        shown = (float)(int)value;
    if (HAS_CONSOLE)
        tes3x_console_reply("%08x %u >> %.2f", refid, slot, (double)shown);
    (void)shown;
}

static void astats_stat(void)
{
    tes3x_log_hex3("net.astats", astats_out, astats_in, astats_applied);
    tes3x_log_hex3("net.astats_table", astats_lost, astats_refused, 0);
}
