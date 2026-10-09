/* A ghost stands for a player, so damage done to it here, by this console's player or an actor
 * this console runs, goes to that player's console as PLAYER_HIT. The ghost keeps GHOST_HEALTH in
 * health and fatigue, refilled each frame, so no blow kills or fells it. */
#define GHOST_HEALTH 5000.0f

typedef u8(__attribute__((thiscall)) *fn_apply_health)(void *mobile, float damage, u8 player,
                                                        u8 difficulty, u8 keep_health);
typedef float(__attribute__((thiscall)) *fn_apply_fatigue)(void *mobile, float damage, float swing,
                                                           u8 voice);
typedef void(__attribute__((thiscall)) *fn_hit_stun)(void *mobile, float damage, u8 died);
static const u32 hit_stun_sites[] = TES3X_NET_HIT_STUN_SITES;

/* A ghost is another player, not an NPC victim. Keep the engine's hit reaction but suppress the
 * assault report that would give the attacker a bounty and turn witnesses hostile. */
static void __attribute__((thiscall)) ghost_hit_stun(u8 *mobile, float damage, u8 crime)
{
    const u8 *ref = plausible(mobile) ? *(const u8 *const *)(mobile + MOBILE_REFERENCE) : 0;

    if (crime && plausible(ref) && is_ghost(ref)) {
        crime = 0;
        ghost_crimes_blocked++;
    }
    ((fn_hit_stun)TES3X_NET_HIT_STUN)(mobile, damage, crime);
}

static void ghost_crime_hook_install(void)
{
    if (ghost_crime_hooked)
        return;
    ghost_crime_hooked = 1;
    if (redirect_calls(hit_stun_sites, sizeof(hit_stun_sites) / sizeof(hit_stun_sites[0]),
                       TES3X_NET_HIT_STUN, (const void *)ghost_hit_stun))
        ghost_crime_hooked = 2;
    tes3x_log("net.ghost_crime_hook", ghost_crime_hooked);
}

static void event_hit(u32 kind, u32 refid, u32 target, float health, float fatigue)
{
    u8 data[16];

    put32le(data, refid);
    put32le(data + 4, target);
    copy(data + 8, (const u8 *)&health, 4);
    copy(data + 12, (const u8 *)&fatigue, 4);
    if (!event_queue(kind, data, sizeof(data)))
        tes3x_log("net.event_full", kind);
}

static void ghost_health(u32 i, u8 *ref)
{
    u8 *mobile = ref_mobile(ref);
    float *health, *fatigue, damage, tired;

    if (!plausible(mobile))
        return;
    health = (float *)(mobile + MOBILE_HEALTH);
    fatigue = (float *)(mobile + MOBILE_FATIGUE);
    damage = GHOST_HEALTH - *health;
    tired = GHOST_HEALTH - *fatigue;
    if (ghosts[i].armed && (damage > 0.5f || tired > 0.5f)) {
        event_hit(EVENT_PLAYER_HIT, 0, ghosts[i].client, damage > 0 ? damage : 0,
                  tired > 0 ? tired : 0);
        player_hits_out++;
        tes3x_log_hex3("net.player_hit_sent", ghosts[i].client, (u32)round_int(damage),
                       (u32)round_int(tired));
    }
    *health = *fatigue = GHOST_HEALTH;
    ghosts[i].armed = 1;
}

static int ghost_life_dead(u32 client)
{
    u32 i;

    for (i = 0; i < PEERS; i++)
        if (ghost_lives[i].client == client)
            return ghost_lives[i].dead != 0;
    return 0;
}

/* PLAYER_DEATH and PLAYER_ALIVE relayed by the server carry the player as their origin. The
 * state is kept before a PEER packet gives that player a ghost slot, so late-join replay works. */
static void ghost_life_event(const struct event *e)
{
    u32 i, empty = PEERS;
    int dead;

    if (!e->origin || e->length < 1 ||
        (e->data[0] != PLAYER_DEATH && e->data[0] != PLAYER_ALIVE))
        return;
    dead = e->data[0] == PLAYER_DEATH;
    for (i = 0; i < PEERS; i++) {
        if (ghost_lives[i].client == e->origin)
            break;
        if (!ghost_lives[i].client && empty == PEERS)
            empty = i;
    }
    if (i == PEERS)
        i = empty;
    if (i == PEERS)
        return;
    ghost_lives[i].client = e->origin;
    ghost_lives[i].dead = dead;
    tes3x_log_hex3("net.ghost_life", e->origin, dead, e->seq);
}

static u8 *actor_ref(u32 refid);

/* Another console's ghost of this player was hit there: the damage as the engine applies a blow,
 * with its sounds, and the stun test, whose flinch the ghost there mirrors. An NPC hit is accepted
 * only while that same actor has a local scene node; otherwise an authority can kill the player
 * with an actor their console has not loaded or cannot draw. */
static void player_hit_event(const struct event *e)
{
    const u8 *ref = player_reference();
    u8 *mobile = ref ? ref_mobile(ref) : 0, *attacker;
    u32 attacker_id;
    float health, fatigue = 0;

    if (e->length < 12 || get32le(e->data + 4) != ses.client || !plausible(mobile))
        return;
    if (!float_within(e->data + 8, e->length >= 16 ? 2 : 1, STAT_LIMIT)) {
        refused_events++;
        return;
    }
    attacker_id = get32le(e->data);
    if (attacker_id && (!(attacker = actor_ref(attacker_id)) ||
                        !plausible(*(u8 **)(attacker + REF_NODE)))) {
        player_hits_unseen++;
        tes3x_log_hex3("net.player_hit_unseen", attacker_id, e->origin, player_hits_unseen);
        return;
    }
    copy((u8 *)&health, e->data + 8, 4);
    if (e->length >= 16)
        copy((u8 *)&fatigue, e->data + 12, 4);
    if (fatigue > 0)
        ((fn_apply_fatigue)TES3X_NET_APPLY_FATIGUE_DAMAGE)(mobile, fatigue, 1.0f, 0);
    if (health > 0)
        ((fn_apply_health)TES3X_NET_APPLY_HEALTH_DAMAGE)(mobile, health, 0, 0, 0);
    ((fn_hit_stun)TES3X_NET_HIT_STUN)(mobile, health > 0 ? health : fatigue, 0);
    player_hits_in++;
    tes3x_log_hex3("net.player_hit", e->origin, (u32)round_int(health), (u32)round_int(fatigue));
}

static void ghost_update(u32 i, const struct pose *local)
{
    struct pose p, placed;
    u32 client = peers[i].client;
    u8 *ref, older[ANIM_BYTES];
    float frac;
    int dead;

    if (client != ghosts[i].client) {
        if (ghosts[i].placed)
            ghost_park(i);
        ghosts[i].client = client;
        ghosts[i].identity = ghosts[i].identity_seen = 0;
        ghosts[i].look = 0;
        ghosts[i].dead = 2;
        if (client)
            tes3x_log_hex3("net.ghost", i + 1, client, 0);
    }
    if (!client || !ghost_pose(i, &p, older, &frac))
        return;
    if (!(p.flags & STATE_IN_WORLD) || !near(&p, local)) {
        if (ghosts[i].placed)
            ghost_park(i);
        return;
    }
    placed.flags = ghosts[i].flags;
    copy(placed.cell, ghosts[i].cell, CELL_NAME);
    if (!ghosts[i].placed || !same_place(&p, &placed) ||
        (!(p.flags & STATE_INTERIOR) && (grid(p.x) != ghosts[i].gx || grid(p.y) != ghosts[i].gy))) {
        ghost_place(i, &p);
        return;
    }
    if (!(ref = ghost_ref(i)))
        return;
    if (plausible(ref_mobile(ref)))
        *(u32 *)(ref_mobile(ref) + MOBILE_FLAGS) &= ~MOBILE_SIMULATED;
    /* Against where it stands: a ghost that was hit turns to its attacker by itself. */
    if (off_pose(ref, &p.x)) {
        place_ref(ref, &p.x);
        ghosts[i].x = p.x;
        ghosts[i].y = p.y;
        ghosts[i].z = p.z;
        ghosts[i].heading = p.heading;
        ghost_moves++;
    }
    dead = ghost_life_dead(client);
    if (ghosts[i].dead != (u32)dead) {
        ghost_action(i, dead ? "Kill" : "Resurrect");
        ghosts[i].dead = dead;
        ghosts[i].armed = 0;
        if (dead)
            ghost_deaths++;
        else
            ghost_respawns++;
    }
    identity_apply(i, ref);
    if (dead)
        return;
    ghost_health(i, ref);
    equipment_apply(i, ref);
    stance_apply(ref, p.flags);
    anim_apply(ref, older, p.anim, frac);
}

/* Each placed ghost's heading in its reference, its orientation attachment and its node's
 * rotation (first row, x1000): the engine may turn one without the others. */
static void ghost_heading_stat(void)
{
    const u8 *ref, *node, *mobile;
    const float *m;
    u32 i;

    for (i = 0; i < PEERS; i++) {
        if (!ghosts[i].placed || !(ref = ghost_ref(i)))
            continue;
        tes3x_log_hex3("net.ghost_heading", i + 1,
                       (u32)round_int(*(const float *)(ref + REF_ORIENTATION + 8) * 1000),
                       (u32)round_int(((const float *)((fn_ref_part)TES3X_NET_REF_ORIENTATION)(
                                          ref))[2] * 1000));
        if (plausible(node = *(const u8 *const *)(ref + REF_NODE)) &&
            plausible(m = *(const float *const *)(node + NODE_ROTATION)))
            tes3x_log_hex3("net.ghost_node", i + 1, (u32)round_int(m[0] * 1000),
                           (u32)round_int(m[1] * 1000));
        if (plausible(mobile = ref_mobile(ref)))
            tes3x_log_hex3("net.ghost_mobile", i + 1, *(const u32 *)(mobile + MOBILE_FLAGS),
                           *(const u32 *)(ref + 8));
    }
}

static void ghosts_frame(const u8 *state)
{
    static int gx, gy;
    struct pose local;
    u32 i;

    read_pose(state, &local);
    /* A save can hold a ghost wherever it stood; start every launch with all of them parked. */
    if (!ghosts_parked) {
        ghosts_parked = 1;
        for (i = 0; i < PEERS; i++)
            ghost_park(i);
    }
    if (grid(local.x) != gx || grid(local.y) != gy) {
        gx = grid(local.x);
        gy = grid(local.y);
        ghost_settle = GHOST_SETTLE_FRAMES;
    }
    if (ghost_settle) {
        ghost_settle--;
        return;
    }
    for (i = 0; i < PEERS; i++)
        ghost_update(i, &local);
}
