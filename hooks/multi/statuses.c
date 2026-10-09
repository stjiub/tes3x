/* Statuses. An effect that changes how an actor looks or acts goes out as AFFECT from the console
 * that applies it, and each follower applies the same effect of the same spell to its copy. Damage
 * effects are left out: their result arrives with the statistics in ACTORS. AI settings and the
 * stored base disposition go out as STATUS from whichever console changes them (dialogue changes
 * disposition on the talker's), latest wins; the disposition shown adds the local player's race,
 * faction and personality and stays per player. The server keeps the latest STATUS per actor. */
#define EVENT_AFFECT 18u /* actor id, effect index, spell id */
#define EVENT_STATUS 19u /* actor id, fight, flee, alarm, hello, base disposition (i16 each) */
#define AFFECT_BYTES 5u
#define STATUS_VALUES 5u
#define STATUS_BYTES (4u + STATUS_VALUES * 2u)
#define MOBILE_FLEE 0x354
#define MOBILE_HELLO 0x358
#define MOBILE_ALARM 0x35C
#define NPC_BASE_DISPOSITION 0x8C /* vtable offset; what calculateDisposition 0x0011E210 starts from */
#define NO_DISPOSITION (-32768)
#define STATUSES 256u
#define STATUS_NEW 1u
#define STATUS_APPLIED 2u

typedef int(__attribute__((thiscall)) *fn_object_int)(const void *object);

/* The values last sent or applied per actor; pending ones wait for the actor to be loaded here. */
static struct {
    u32 refid;
    short v[STATUS_VALUES];
    u8 pending, settle;
} statuses[STATUSES];
static u32 status_next, statuses_sent, statuses_received, statuses_applied, statuses_lost;
static u32 statuses_kept;
static u32 status_mismatches, affects_sent, affects_received, affects_applied;

static int affect_shared(int id)
{
    return (id >= 3 && id <= 6) || id == 10 || id == 17 || id == 22 || (id >= 39 && id <= 42) ||
           (id >= 45 && id <= 48) || id == 79;
}

/* An effect applied here to an actor others can name: tell them if it shows. */
static void affect_send(const u8 *instance, const u8 *target, u32 effect)
{
    const u8 *source;
    const char *id;
    u8 data[AFFECT_BYTES + SPELL_ID];
    u32 refid, n;

    if (ses.state != SESSION_JOINED || effect >= SOURCE_MAX_EFFECTS || !plausible(target) ||
        target == player_reference() || is_ghost(target) || !(refid = actor_id(target)) ||
        !(id = spell_id(instance)))
        return;
    source = *(const u8 *const *)(instance + INSTANCE_SOURCE);
    if (!affect_shared(*(const short *)(source + SOURCE_EFFECTS + effect * EFFECT_BYTES)))
        return;
    put32le(data, refid);
    data[4] = (u8)effect;
    for (n = 0; id[n] && n < SPELL_ID - 1; n++)
        data[AFFECT_BYTES + n] = (u8)id[n];
    data[AFFECT_BYTES + n] = 0;
    if (!event_queue(EVENT_AFFECT, data, AFFECT_BYTES + n + 1)) {
        tes3x_log("net.event_full", EVENT_AFFECT);
        return;
    }
    affects_sent++;
    log_text("net.affect_sent", id);
    tes3x_log_hex3("net.affect_to", refid, effect, 0);
}

/* Another console applied an effect to an actor followed here: the copy casts the same spell on
 * itself and takes that one effect. */
static void affect_event(const struct event *e)
{
    const u8 *effects;
    u8 *target, *instance;
    char id[SPELL_ID];
    u32 refid, index, n, owner;

    if (e->length < AFFECT_BYTES + 1)
        return;
    refid = get32le(e->data);
    index = e->data[4];
    for (n = 0; n < SPELL_ID - 1 && AFFECT_BYTES + n < e->length && e->data[AFFECT_BYTES + n]; n++)
        id[n] = (char)e->data[AFFECT_BYTES + n];
    id[n] = 0;
    affects_received++;
    log_text("net.affect", id);
    tes3x_log_hex3("net.affect_from", e->origin, refid, index);
    if (!(target = actor_ref(refid)) || !ref_owner(target, &owner) || index >= SOURCE_MAX_EFFECTS)
        return; /* not here, or run here */
    if (!(instance = spell_start(id, target, target, refid)))
        return;
    effects = *(const u8 *const *)(instance + INSTANCE_SOURCE) + SOURCE_EFFECTS;
    for (n = 0; n <= index && *(const short *)(effects + n * EFFECT_BYTES) != -1; n++)
        ;
    if (n <= index) {
        spell_fail(id, 5, refid);
        return;
    }
    ((fn_spell_hit)TES3X_NET_SPELL_HIT)(instance, target, (int)index);
    *(u32 *)(instance + INSTANCE_STATE) = INSTANCE_WORKING;
    affects_applied++;
    tes3x_log_hex3("net.affect_applied", refid, index, 0);
}

static int is_npc(const u8 *ref)
{
    const u8 *object = *(const u8 *const *)(ref + REF_BASE);

    return plausible(object) && *(const u32 *)(object + OBJECT_TYPE) == TAG_NPC;
}

/* An NPC's stored base disposition; 50, which adds nothing to Fight, for a creature. */
static int base_disposition(const u8 *ref)
{
    const u8 *object = *(const u8 *const *)(ref + REF_BASE);

    return is_npc(ref) ? ((fn_object_int)(*(void *const *const *)object)[NPC_BASE_DISPOSITION / 4])(
                             object)
                       : 50;
}

/* An NPC's Fight or Alarm as its record sets it, or value when that is lower or the actor is not
 * an NPC. Raised above the record they belong to the console that raised them, by a crime against
 * its player, so they are neither sent nor applied. */
#define NPC_INSTANCE_BASE 0x6C
#define NPC_AI_CONFIG 0xE0

static int record_ai(const u8 *ref, u32 offset, int value)
{
    const u8 *object = *(const u8 *const *)(ref + REF_BASE), *base;

    if (!is_npc(ref) || !plausible(base = *(const u8 *const *)(object + NPC_INSTANCE_BASE)))
        return value;
    return value > base[NPC_AI_CONFIG + offset] ? base[NPC_AI_CONFIG + offset] : value;
}

static void status_read(const u8 *mobile, const u8 *ref, short *v)
{
    const u8 *object = *(const u8 *const *)(ref + REF_BASE);

    v[0] = (short)record_ai(ref, AI_FIGHT, *(const int *)(mobile + MOBILE_FIGHT));
    v[1] = (short)*(const int *)(mobile + MOBILE_FLEE);
    v[2] = (short)record_ai(ref, AI_ALARM, *(const int *)(mobile + MOBILE_ALARM));
    v[3] = (short)*(const int *)(mobile + MOBILE_HELLO);
    v[4] = is_npc(ref) ? (short)((fn_object_int)(*(void *const *const *)object)
                                     [NPC_BASE_DISPOSITION / 4])(object)
                       : NO_DISPOSITION;
}

/* The entry for refid; a new one takes a free slot, else one not waiting to be applied. */
static u32 status_slot(u32 refid)
{
    u32 i, n;

    for (i = 0; i < STATUSES; i++)
        if (statuses[i].refid == refid)
            return i;
    for (i = 0; i < STATUSES; i++)
        if (!statuses[i].refid)
            break;
    for (n = 0; i == STATUSES && n < STATUSES; n++, status_next = (status_next + 1) % STATUSES)
        if (!statuses[status_next].pending)
            i = status_next;
    if (i == STATUSES) {
        i = status_next;
        statuses_lost++;
    }
    status_next = (i + 1) % STATUSES;
    statuses[i].refid = refid;
    statuses[i].pending = 0;
    statuses[i].settle = STATUS_NEW;
    return i;
}

/* On the actor send tick, for each loaded actor: apply what arrived for it, or send what changed
 * here. The values read on first sight, or right after an apply, are taken as they are. */
static void status_frame(const u8 *mobile, u8 *ref, u32 refid)
{
    u32 i = status_slot(refid), k;
    short v[STATUS_VALUES];
    u8 data[STATUS_BYTES];

    if (statuses[i].pending) {
        statuses[i].v[0] = (short)record_ai(ref, AI_FIGHT, statuses[i].v[0]);
        statuses[i].v[2] = (short)record_ai(ref, AI_ALARM, statuses[i].v[2]);
        actor_command(ref, "SetFight ", statuses[i].v[0]);
        actor_command(ref, "SetFlee ", statuses[i].v[1]);
        actor_command(ref, "SetAlarm ", statuses[i].v[2]);
        actor_command(ref, "SetHello ", statuses[i].v[3]);
        if (statuses[i].v[4] != NO_DISPOSITION && is_npc(ref))
            actor_command(ref, "SetDisposition ", statuses[i].v[4]);
        statuses[i].pending = 0;
        statuses[i].settle = STATUS_APPLIED;
        statuses_applied++;
        tes3x_log_hex3("net.status_applied", refid, (u32)statuses[i].v[0],
                       (u32)statuses[i].v[4]);
        return;
    }
    status_read(mobile, ref, v);
    if ((v[0] != *(const int *)(mobile + MOBILE_FIGHT) ||
         v[2] != *(const int *)(mobile + MOBILE_ALARM)) && statuses_kept++ < 32)
        tes3x_log_hex3("net.status_kept", refid,
                       (u32)*(const int *)(mobile + MOBILE_FIGHT) << 16 | (u16)v[0],
                       (u32)*(const int *)(mobile + MOBILE_ALARM) << 16 | (u16)v[2]);
    for (k = 0; k < STATUS_VALUES && v[k] == statuses[i].v[k]; k++)
        ;
    if (statuses[i].settle) {
        if (statuses[i].settle == STATUS_APPLIED && k < STATUS_VALUES) {
            status_mismatches++;
            tes3x_log_hex3("net.status_mismatch", refid, k, (u32)v[k]);
        }
        statuses[i].settle = 0;
        copy((u8 *)statuses[i].v, (const u8 *)v, sizeof(v));
        return;
    }
    if (k == STATUS_VALUES)
        return;
    copy((u8 *)statuses[i].v, (const u8 *)v, sizeof(v));
    put32le(data, refid);
    for (k = 0; k < STATUS_VALUES; k++) {
        data[4 + k * 2] = (u8)v[k];
        data[5 + k * 2] = (u8)((u16)v[k] >> 8);
    }
    if (!event_queue(EVENT_STATUS, data, sizeof(data))) {
        tes3x_log("net.event_full", EVENT_STATUS);
        return;
    }
    statuses_sent++;
    tes3x_log_hex3("net.status_sent", refid, (u32)v[0], (u32)v[4]);
}

static void status_event(const struct event *e)
{
    u32 refid, i, k;

    if (e->length < STATUS_BYTES || !(refid = get32le(e->data)))
        return;
    i = status_slot(refid);
    for (k = 0; k < STATUS_VALUES; k++)
        statuses[i].v[k] = (short)(e->data[4 + k * 2] | e->data[5 + k * 2] << 8);
    statuses[i].pending = 1;
    statuses[i].settle = 0;
    statuses_received++;
    tes3x_log_hex3("net.status", refid, e->origin, (u32)statuses[i].v[4]);
}

/* Bounties. Each console sends its player's bounty when it changes, and the server replays the
 * latest of each player to a joiner. When a player's bounty drops to nothing (a fine paid, a
 * crime forgiven), the actors run here that fight that player's ghost and would not attack on
 * their record's Fight stop, as the criminal's own console stops its guards. */
#define EVENT_BOUNTY 30u /* the player's bounty, i32 */
#define BOUNTY_EVERY_US 1000000u
#define PEACE_US 3000000u

typedef int(__attribute__((thiscall)) *fn_get_bounty)(const void *mobile_player);

static struct {
    u32 client, peace_until;
    int bounty;
} bounties[PEERS];
static u32 player_peace_until;
static int bounty_sent = -1;
static u32 bounty_checked, bounties_received, peace_stops;
static u32 player_mode; /* 3 once the checkpoint's player-state replay is complete */
static u32 player_welcome, player_bounty_replayed;

static void bounty_frame(void)
{
    const u8 *ref = player_reference(), *mobile;
    u32 now = now_us();
    int bounty;
    u8 data[4];

    if (ses.state != SESSION_JOINED) {
        bounty_sent = -1;
        return;
    }
    if (player_mode != 3 || player_welcome != ses.welcomes)
        return;
    if (now - bounty_checked < BOUNTY_EVERY_US || !plausible(ref) ||
        !plausible(mobile = ref_mobile(ref)))
        return;
    bounty_checked = now;
    bounty = ((fn_get_bounty)TES3X_NET_GET_BOUNTY)(mobile);
    if (bounty == bounty_sent)
        return;
    put32le(data, (u32)bounty);
    if (!event_queue(EVENT_BOUNTY, data, sizeof(data))) {
        tes3x_log("net.event_full", EVENT_BOUNTY);
        return;
    }
    if (bounty_sent > 0 && bounty <= 0)
        player_peace_until = now + PEACE_US;
    bounty_sent = bounty;
    tes3x_log_hex3("net.bounty_sent", (u32)bounty, 0, 0);
}

static void bounty_event(const struct event *e)
{
    u32 i, free = PEERS;
    int bounty, old;

    if (e->length < 4 || !e->origin || e->origin == ses.client)
        return;
    bounty = (int)get32le(e->data);
    for (i = 0; i < PEERS && bounties[i].client != e->origin; i++)
        if (!bounties[i].client && free == PEERS)
            free = i;
    if (i == PEERS) {
        if (free == PEERS)
            return;
        i = free;
        bounties[i].client = e->origin;
        bounties[i].bounty = 0;
    }
    old = bounties[i].bounty;
    bounties[i].bounty = bounty;
    bounties_received++;
    if (old > 0 && bounty <= 0)
        bounties[i].peace_until = now_us() + PEACE_US;
    tes3x_log_hex3("net.bounty", e->origin, (u32)bounty, (u32)old);
}

static void peace_check(const u8 *mobile, void *ref, u32 refid)
{
    u32 client = combat_target(mobile), i;
    float fight;

    if (!client || client >= PLAYER_IDS)
        return;
    if (client == ses.client) {
        if ((int)(player_peace_until - now_us()) <= 0)
            return;
    } else {
        for (i = 0; i < PEERS; i++)
            if (bounties[i].client == client && (int)(bounties[i].peace_until - now_us()) > 0)
                break;
        if (i == PEERS)
            return;
    }
    fight = (float)record_ai(ref, AI_FIGHT, *(const int *)(mobile + MOBILE_FIGHT)) +
            FIGHT_DISP_MULT * (float)(50 - base_disposition(ref));
    if (fight + FIGHT_DISTANCE_BASE >= FIGHT_ATTACK)
        return;
    run_script_on("StopCombat", ref);
    peace_stops++;
    tes3x_log_hex3("net.peace", refid, client, 0);
}

static void bounty_stat(void)
{
    tes3x_log_hex3("net.bounties", (u32)bounty_sent, bounties_received, peace_stops);
}

static void status_stat(void)
{
    tes3x_log_hex3("net.statuses", statuses_sent, statuses_received, statuses_applied);
    tes3x_log_hex3("net.statuses_bad", status_mismatches, statuses_lost, statuses_kept);
    tes3x_log_hex3("net.affects", affects_sent, affects_received, affects_applied);
}
