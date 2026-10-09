/* Cell authority. The server names one client per loaded cell to run the actors there (AUTHORITY
 * events). It sends their states about 10 times a second; every other client holds those actors'
 * AI and places them from the states. A hit or a held dialogue on a followed actor goes to its
 * authority as an event. Only references from the
 * data files take part: their mod index and refnum name one object under one load order. */
#define EVENT_AUTHORITY 2u   /* cell key, client */
#define EVENT_HOLD 3u        /* refid, authority, on */
#define EVENT_HOLD_BROKEN 4u /* refid, holder, reason */
#define EVENT_HIT 5u         /* refid, authority, health damage, fatigue damage */
#define EVENT_DEATH 6u       /* refid; the server records it and replays it to each joining client */
/* count, then (actor id, client) pairs: the player nearest an actor runs it, since the engine runs
 * actors only within aiDistance of its own player; client 0 hands it back to its cell's authority */
#define EVENT_OWNERS 20u
#define OWNERS 256u
#define KEY_EXTERIOR 1u
#define KEY_INTERIOR 2u
#define KEY_BYTES (12u + CELL_NAME) /* kind, grid x, grid y, interior name */
/* refid, x, y, z, heading, health, flags, magicka, fatigue, combat target, animation */
#define ACTOR_HEALTH 20u
#define ACTOR_MAGICKA 28u
#define ACTOR_FATIGUE 32u
#define ACTOR_TARGET 36u /* a client id for a player, else an actor id; 0 for none */
#define ACTOR_ANIM 40u
#define ACTOR_BYTES (ACTOR_ANIM + ANIM_BYTES)
#define ACTORS_PER_PACKET 8u /* a body of at most EVENTS_BYTES */
#define PLAYER_IDS 0x01000000u /* below: a client id; an actor id has a mod index of at least 1 */
#define MOBILE_TARGET 0xEC
#define ACTOR_PERIOD_US 100000u
#define ACTOR_SAMPLES 4u
#define ACTOR_DELAY_US 200000u /* two periods: a state late by one still has a pair */
#define ACTOR_DEAD 1u
#define ACTOR_IN_COMBAT 2u
#define AUTHORITIES 16u
#define ACTORS 256u /* a full follow table leaves the rest running here too */
#define REMOTE_HOLDS 8u
#define HITS 8u
#define DEATHS 256u
#define REF_ID 0x48 /* mod index << 24 | refnum; 0 for a reference made at run time */
#define MOBILE_ACTION 0xDD /* 0x12 dying, 0x13 dead */
#define MOB_PROCESS 0x24   /* MobController -> ProcessManager: player, then the AI planners */
#define PROCESS_PLANNERS 0xC
#define PLANNER_MOBILE 4

struct cell_key {
    u32 kind;
    int gx, gy;
    u8 name[CELL_NAME];
};

/* The latest state of each actor from its authority and its last few positions; the receive DPC
 * writes, bytes only. */
static struct {
    u32 refid, origin, seq, time, head, count;
    u8 state[ACTOR_BYTES];
    struct timed track[ACTOR_SAMPLES];
} actors_in[ACTORS];
static struct {
    struct cell_key key;
    u32 client;
} authority[AUTHORITIES];
static u32 authorities, authority_welcome;
static struct {
    u32 id, client;
} owners[OWNERS];
static u32 owner_count, owners_in, owners_full;
/* Actors this console places for another authority; held while their AI is to be skipped. */
static struct {
    u32 refid, owner, held, seen, animated, look;
    u8 *mobile;
    float health, fatigue;
} followed[ACTORS];
/* Actors this console runs that another client is talking to. */
static struct {
    u32 refid, holder, simulated, found, release;
    u8 *mobile;
    float health;
} remote_holds[REMOTE_HOLDS];
static struct {
    u32 refid, origin;
    float damage, fatigue;
} hits[HITS];
static u32 hit_count, talk_refid, talk_owner, talk_broken;
static u32 actor_states_out, actor_states_in, actor_moves, follows, follows_full;
static u32 hits_out, hits_in, remote_holds_in, remote_breaks_out, remote_breaks_in, retaliations;
/* Every death this session has seen, reported here or told by the server. */
static u32 deaths[DEATHS], death_count, deaths_reported, deaths_applied;

static u32 actor_id(const u8 *ref);
static u32 actor_owner(u32 id, u32 cell_owner);
static void status_frame(const u8 *mobile, u8 *ref, u32 refid);

static void actors_reset(void)
{
    u32 i;

    for (i = 0; i < ACTORS; i++)
        actors_in[i].refid = 0;
}

/* Caller holds the lock (the receive DPC): origin client, count, then the states. */
static void actors_rx(u32 origin, u32 seq, const u8 *p, u32 n)
{
    u32 count = get32le(p), i, j, refid, slot, oldest;

    for (i = 0; i < count && 4 + (i + 1) * ACTOR_BYTES <= n; i++) {
        const u8 *a = p + 4 + i * ACTOR_BYTES;
        refid = get32le(a);
        if (!float_within(a + 4, 3, POSITION_LIMIT) || !float_within(a + 16, 1, ANGLE_LIMIT) ||
            !float_within(a + ACTOR_HEALTH, 1, STAT_LIMIT) ||
            !float_within(a + ACTOR_MAGICKA, 2, STAT_LIMIT)) {
            refused_states++;
            continue;
        }
        slot = ACTORS;
        for (j = 0; j < ACTORS && slot == ACTORS; j++)
            if (actors_in[j].refid == refid)
                slot = j;
        if (slot < ACTORS && actors_in[slot].origin == origin && seq <= actors_in[slot].seq)
            continue;
        for (j = 0, oldest = 0; j < ACTORS && slot == ACTORS; j++) {
            if (!actors_in[j].refid)
                slot = j;
            else if ((int)(actors_in[j].time - actors_in[oldest].time) < 0)
                oldest = j;
        }
        if (slot == ACTORS)
            slot = oldest;
        if (actors_in[slot].refid != refid || actors_in[slot].origin != origin)
            actors_in[slot].count = 0;
        actors_in[slot].refid = refid;
        actors_in[slot].origin = origin;
        actors_in[slot].seq = seq;
        actors_in[slot].time = now_us();
        copy(actors_in[slot].state, a, ACTOR_BYTES);
        j = actors_in[slot].head;
        actors_in[slot].track[j].time = actors_in[slot].time;
        copy((u8 *)actors_in[slot].track[j].p, a + 4, 16);
        copy(actors_in[slot].track[j].anim, a + ACTOR_ANIM, ANIM_BYTES);
        actors_in[slot].track[j].flags = get32le(a + 24);
        actors_in[slot].head = (j + 1) % ACTOR_SAMPLES;
        if (actors_in[slot].count < ACTOR_SAMPLES)
            actors_in[slot].count++;
        actor_states_in++;
    }
}

static int key_equal(const struct cell_key *a, const struct cell_key *b)
{
    u32 i;

    if (a->kind != b->kind)
        return 0;
    if (a->kind == KEY_EXTERIOR)
        return a->gx == b->gx && a->gy == b->gy;
    for (i = 0; i < CELL_NAME - 1 && a->name[i] && a->name[i] == b->name[i]; i++)
        ;
    return a->name[i] == b->name[i];
}

/* An actor's cell: the local interior, or the exterior grid cell it stands in. */
static void actor_key(const struct pose *local, float x, float y, struct cell_key *k)
{
    u32 i;

    k->kind = local->flags & STATE_INTERIOR ? KEY_INTERIOR : KEY_EXTERIOR;
    k->gx = k->kind == KEY_EXTERIOR ? grid(x) : 0;
    k->gy = k->kind == KEY_EXTERIOR ? grid(y) : 0;
    for (i = 0; i < CELL_NAME; i++)
        k->name[i] = k->kind == KEY_INTERIOR ? local->cell[i] : 0;
}

static int key_loaded(const struct cell_key *k, const struct pose *local)
{
    struct cell_key mine;
    int dx, dy;

    actor_key(local, local->x, local->y, &mine);
    if (k->kind != mine.kind)
        return 0;
    if (k->kind == KEY_INTERIOR)
        return key_equal(k, &mine);
    dx = k->gx - mine.gx;
    dy = k->gy - mine.gy;
    return dx >= -1 && dx <= 1 && dy >= -1 && dy <= 1;
}

static u32 authority_of(const struct cell_key *k)
{
    u32 i;

    for (i = 0; i < authorities; i++)
        if (key_equal(&authority[i].key, k))
            return authority[i].client;
    return 0;
}

static void authority_remove(u32 i)
{
    authority[i] = authority[--authorities];
}

static void authority_set(const struct cell_key *k, u32 client)
{
    u32 i;

    if (k->kind == KEY_INTERIOR)
        log_text("net.authority_cell", (const char *)k->name);
    tes3x_log_hex3("net.authority", client, (u32)k->gx, (u32)k->gy);
    for (i = 0; i < authorities; i++)
        if (key_equal(&authority[i].key, k))
            break;
    if (i < authorities && !client)
        authority_remove(i);
    if (!client)
        return;
    if (i == authorities) {
        if (authorities == AUTHORITIES)
            i = 0; /* more cells than a console loads: the table is stale */
        else
            authorities++;
    }
    authority[i].key = *k;
    authority[i].client = client;
}

static void event_words(u32 kind, u32 a, u32 b, const void *c)
{
    u8 data[12];

    put32le(data, a);
    put32le(data + 4, b);
    copy(data + 8, (const u8 *)c, 4);
    if (!event_queue(kind, data, sizeof(data)))
        tes3x_log("net.event_full", kind);
}

static void actor_command(void *ref, const char *verb, int value)
{
    char line[48];
    char *p = put_int(put_text(line, verb), value);

    *p = 0;
    run_script_on(line, ref);
}

static void unfollow(u32 i, int restore)
{
    if (restore && followed[i].animated)
        anim_release(*(u8 **)(followed[i].mobile + MOBILE_REFERENCE));
    followed[i].refid = 0;
}

/* A followed actor stays in the simulation, where blows, projectiles and effects reach it, but
 * its AI step runs as with ToggleAI off: no decisions, movement cleared, velocity zero. */
#define WORLD_TOGGLES 0x2C0
#define TOGGLES_FLAGS 0x24
#define TOGGLE_AI_OFF 8u

static const u32 ai_step_slots[] = TES3X_NET_AI_STEP_SLOTS;
static u32 ai_hooked, ai_held;

static void __attribute__((thiscall)) ai_step_hook(u8 *mobile)
{
    u8 *world = *(u8 **)TES3X_NET_WORLD, *toggles;
    u32 i, saved;

    for (i = 0; i < ACTORS; i++)
        if (followed[i].refid && followed[i].held && followed[i].mobile == mobile)
            break;
    if (i == ACTORS || !plausible(world) ||
        !plausible(toggles = *(u8 **)(world + WORLD_TOGGLES))) {
        ((fn_mobile_call)TES3X_NET_AI_STEP)(mobile);
        return;
    }
    saved = *(u32 *)(toggles + TOGGLES_FLAGS);
    *(u32 *)(toggles + TOGGLES_FLAGS) = saved | TOGGLE_AI_OFF;
    ((fn_mobile_call)TES3X_NET_AI_STEP)(mobile);
    *(u32 *)(toggles + TOGGLES_FLAGS) = saved;
    ai_held++;
}

static void ai_hook_install(void)
{
    u32 i, cr0, flags, n = sizeof(ai_step_slots) / sizeof(ai_step_slots[0]);

    if (ai_hooked)
        return;
    ai_hooked = 1;
    for (i = 0; i < n; i++)
        if (*(const u32 *)ai_step_slots[i] != TES3X_NET_AI_STEP) {
            tes3x_log_hex3("net.ai_slot_unexpected", ai_step_slots[i],
                           *(const u32 *)ai_step_slots[i], 0);
            return;
        }
    flags = lock();
    __asm__ volatile("movl %%cr0, %0" : "=r"(cr0));
    __asm__ volatile("movl %0, %%cr0" : : "r"(cr0 & ~CR0_WP) : "memory");
    for (i = 0; i < n; i++)
        *(u32 *)ai_step_slots[i] = (u32)ai_step_hook;
    __asm__ volatile("movl %0, %%cr0" : : "r"(cr0) : "memory");
    unlock(flags);
    ai_hooked = 2;
    tes3x_log("net.ai_hook", n);
}

/* Another client runs this actor: hold its AI, place it ACTOR_DELAY_US behind its authority's
 * states, as ghosts are, and send a drop in its health to the authority as a hit. Its statistics
 * follow the authority's, except a health of 0 or less: DEATH brings that. */
static void follow(u8 *mobile, u8 *ref, u32 refid, u32 owner)
{
    u32 *flags = (u32 *)(mobile + MOBILE_FLAGS), i, k, slot = ACTORS, count = 0, lk;
    float health = *(const float *)(mobile + MOBILE_HEALTH), damage, tired, to[4], frac, stats[3];
    float fatigue = *(const float *)(mobile + MOBILE_FATIGUE);
    struct timed track[ACTOR_SAMPLES];

    for (i = 0; i < ACTORS && slot == ACTORS; i++)
        if (followed[i].refid == refid)
            slot = i;
    if (slot < ACTORS && followed[slot].mobile != mobile)
        unfollow(slot, 0); /* the same reference in a new mobile: a reload */
    if (slot == ACTORS || !followed[slot].refid) {
        for (i = 0, slot = ACTORS; i < ACTORS && slot == ACTORS; i++)
            if (!followed[i].refid)
                slot = i;
        if (slot == ACTORS) {
            follows_full++;
            return;
        }
        followed[slot].refid = refid;
        followed[slot].mobile = mobile;
        followed[slot].health = health;
        followed[slot].fatigue = fatigue;
        followed[slot].animated = 0;
        followed[slot].look = 0;
        follows++;
        tes3x_log_hex3("net.follow", refid, owner, *flags & MOBILE_SIMULATED);
    }
    followed[slot].owner = owner;
    followed[slot].seen = 1;
    damage = followed[slot].health - health;
    tired = followed[slot].fatigue - fatigue;
    if (damage > 0.5f || tired > 0.5f) {
        event_hit(EVENT_HIT, refid, owner, damage > 0 ? damage : 0, tired > 0 ? tired : 0);
        hits_out++;
        tes3x_log_hex3("net.hit_sent", refid, (u32)round_int(damage), (u32)round_int(tired));
    }
    followed[slot].health = health;
    followed[slot].fatigue = fatigue;
    actor_equipment_apply(refid, ref, &followed[slot].look);
    followed[slot].health = *(const float *)(mobile + MOBILE_HEALTH);
    followed[slot].fatigue = *(const float *)(mobile + MOBILE_FATIGUE);
    lk = lock();
    for (i = 0; i < ACTORS; i++)
        if (actors_in[i].refid == refid && actors_in[i].origin == owner) {
            count = actors_in[i].count;
            copy((u8 *)&stats[0], actors_in[i].state + ACTOR_HEALTH, 4);
            copy((u8 *)&stats[1], actors_in[i].state + ACTOR_MAGICKA, 4);
            copy((u8 *)&stats[2], actors_in[i].state + ACTOR_FATIGUE, 4);
            for (k = 0; k < count; k++)
                track[k] = actors_in[i].track[(actors_in[i].head + ACTOR_SAMPLES - count + k) %
                                              ACTOR_SAMPLES];
            break;
        }
    unlock(lk);
    /* A death plays out in the simulation; a dead actor has nothing left to place. */
    if (mobile[MOBILE_ACTION] == 0x12 || mobile[MOBILE_ACTION] == 0x13 || health <= 0) {
        followed[slot].held = 0;
        *flags &= ~MOBILE_SCRIPTED;
        return;
    }
    followed[slot].held = 1;
    if (!count)
        return;
    if (stats[0] > 0) {
        *(float *)(mobile + MOBILE_HEALTH) = stats[0];
        followed[slot].health = stats[0];
    }
    *(float *)(mobile + MOBILE_MAGICKA) = stats[1];
    *(float *)(mobile + MOBILE_FATIGUE) = stats[2];
    followed[slot].fatigue = stats[2];
    k = track_pose(track, count, ACTOR_DELAY_US, to, &frac);
    if (off_pose(ref, to)) {
        place_ref(ref, to);
        actor_moves++;
    }
    stance_apply(ref, track[k].flags);
    anim_apply(ref, track[k ? k - 1 : k].anim, track[k].anim, frac);
    followed[slot].animated = 1;
}

/* On the authority: another client's dialogue holds this actor until it ends, the actor enters
 * combat or its health drops, from anyone's hit. */
static void remote_hold_apply(u8 *mobile, u32 refid)
{
    u32 *flags = (u32 *)(mobile + MOBILE_FLAGS), i, reason;
    float health = *(const float *)(mobile + MOBILE_HEALTH);

    for (i = 0; i < REMOTE_HOLDS; i++) {
        if (remote_holds[i].refid != refid)
            continue;
        remote_holds[i].found = 1;
        if (remote_holds[i].mobile != mobile) {
            remote_holds[i].mobile = mobile;
            remote_holds[i].simulated = *flags & MOBILE_SIMULATED;
            remote_holds[i].health = health;
            tes3x_log_hex3("net.remote_hold", refid, remote_holds[i].holder, (u32)(int)health);
        }
        reason = *flags & MOBILE_IN_COMBAT ? 1 : health < remote_holds[i].health ? 2 : 0;
        if (reason && !remote_holds[i].release) {
            event_words(EVENT_HOLD_BROKEN, refid, remote_holds[i].holder, &reason);
            remote_breaks_out++;
            tes3x_log_hex3("net.remote_hold_broken", refid, remote_holds[i].holder, reason);
        }
        if (reason || remote_holds[i].release) {
            if (remote_holds[i].simulated)
                *flags |= MOBILE_SIMULATED;
            remote_holds[i].refid = 0;
        } else {
            *flags &= ~MOBILE_SIMULATED;
        }
    }
}

static int death_known(u32 refid)
{
    u32 i, n = death_count < DEATHS ? death_count : DEATHS;

    for (i = 0; i < n; i++)
        if (deaths[i] == refid)
            return 1;
    return 0;
}

static void death_add(u32 refid)
{
    if (!death_known(refid))
        deaths[death_count++ % DEATHS] = refid;
}

/* Refids seen alive here, open addressing; a full table stops adding. Only their deaths are
 * reported, so a body the data files place dead is not. */
#define ALIVE_SLOTS 1024u
static u32 alive_seen[ALIVE_SLOTS];

static int alive_mark(u32 refid, int add)
{
    u32 i = (refid * 2654435761u) >> 22, n;

    for (n = 0; n < ALIVE_SLOTS; n++, i = (i + 1) & (ALIVE_SLOTS - 1)) {
        if (alive_seen[i] == refid)
            return 1;
        if (!alive_seen[i]) {
            if (add)
                alive_seen[i] = refid;
            return add;
        }
    }
    return 0;
}

/* A death on the authority goes to everyone once; a death told by the server is applied to any
 * living copy here, whoever runs it. */
static void death_frame(u8 *mobile, u8 *ref, u32 refid, u32 owner)
{
    float health = *(const float *)(mobile + MOBILE_HEALTH);
    int dead = mobile[MOBILE_ACTION] == 0x12 || mobile[MOBILE_ACTION] == 0x13 || health <= 0;
    u32 i;

    if (!dead)
        alive_mark(refid, 1);
    /* Not before the actor has its animation controller, which it gets only near the player:
     * the death path reads it. */
    if (!dead && death_known(refid) && plausible(*(void **)(mobile + MOBILE_ANIM_CONTROLLER))) {
        actor_command(ref, "SetHealth ", 0);
        *(u32 *)(mobile + MOBILE_FLAGS) |= MOBILE_SIMULATED;
        for (i = 0; i < ACTORS; i++)
            if (followed[i].refid == refid)
                followed[i].health = 0; /* a told death, not a hit to send back */
        deaths_applied++;
        tes3x_log_hex3("net.actor_killed", refid, owner, 0);
    } else if (dead && owner == ses.client && !death_known(refid) && alive_mark(refid, 0)) {
        death_add(refid);
        if (event_queue(EVENT_DEATH, (const u8 *)&refid, 4))
            deaths_reported++;
        tes3x_log_hex3("net.actor_died", refid, (u32)(int)health, mobile[MOBILE_ACTION]);
    }
}

/* A hit from a follower's player lands as a blow does: the damage with its sounds, the stun test
 * and, from the hitter's ghost, the blood. The actor turns on that ghost unless it is fighting
 * already. */
typedef void(__attribute__((thiscall)) *fn_blood)(void *splashes, void *victim, void *attacker);
#define WORLD_SPLASHES 0x68
static u32 bloodied;

static void hits_apply(u8 *mobile, void *ref, u32 refid)
{
    u8 *world = *(u8 **)TES3X_NET_WORLD, *attacker;
    char line[48];
    u32 i, g;

    for (i = 0; i < hit_count; i++)
        if (hits[i].refid == refid) {
            for (g = 0; g < PEERS && !(ghosts[g].client == hits[i].origin && ghosts[g].placed);
                 g++)
                ;
            if (hits[i].fatigue > 0)
                ((fn_apply_fatigue)TES3X_NET_APPLY_FATIGUE_DAMAGE)(mobile, hits[i].fatigue, 1.0f,
                                                                   0);
            if (hits[i].damage > 0)
                ((fn_apply_health)TES3X_NET_APPLY_HEALTH_DAMAGE)(mobile, hits[i].damage, 0, 0, 0);
            ((fn_hit_stun)TES3X_NET_HIT_STUN)(mobile, hits[i].damage > 0 ? hits[i].damage
                                                                          : hits[i].fatigue, 0);
            if (hits[i].damage > 0 && g < PEERS && plausible(world) &&
                plausible(*(void **)(world + WORLD_SPLASHES)) &&
                ghost_ref(g) && plausible(attacker = ref_mobile(ghost_ref(g)))) {
                ((fn_blood)TES3X_NET_BLOOD)(*(void **)(world + WORLD_SPLASHES), mobile, attacker);
                bloodied++;
            }
            if (g < PEERS && !(*(const u32 *)(mobile + MOBILE_FLAGS) & MOBILE_IN_COMBAT)) {
                *put_text(put_int(put_text(line, "StartCombat \"tes3x_ghost"), (int)g + 1),
                          "\"") = 0;
                run_script_on(line, ref);
                retaliations++;
                tes3x_log_hex3("net.retaliate", refid, hits[i].origin, g + 1);
            }
            hits[i--] = hits[--hit_count];
        }
}

/* An actor that attacks a player on sight attacks a ghost too. The engine only looks for the
 * local player, so on the actor's authority one that is not fighting turns on the nearest placed
 * ghost within HOSTILE_RANGE units that the engine's own test would attack: Fight, plus
 * iFightDistanceBase less fFightDistanceMultiplier per unit, plus fFightDispMult per point of an
 * NPC's disposition under 50, reaching iFightAttack (Morrowind.esm's values). An NPC's Fight counts
 * no higher than its record's: a crime raises the witnesses' against that console's player. */
#define HOSTILE_RANGE 1024.0f
#define FIGHT_ATTACK 100.0f
#define FIGHT_DISTANCE_BASE 20.0f
#define FIGHT_DISTANCE_MULT 0.005f
#define FIGHT_DISP_MULT 0.2f
static u32 hostiles;

#define AI_FIGHT 2 /* in an AIConfig */
#define AI_ALARM 4
static int base_disposition(const u8 *ref);
static int record_ai(const u8 *ref, u32 offset, int value);

static float square_root(float x)
{
    __asm__("fsqrt" : "+t"(x));
    return x;
}

static void ghost_fight_log(const u8 *mobile, const u8 *ref, u32 refid);

static void hostile_check(const u8 *mobile, void *ref, u32 refid)
{
    const float *at = (const float *)((const u8 *)ref + REF_POSITION);
    float dx, dy, dz, d, near, best = HOSTILE_RANGE * HOSTILE_RANGE, fight;
    u32 g, pick = PEERS;
    char line[48];

    if ((*(const u32 *)(mobile + MOBILE_FLAGS) & MOBILE_IN_COMBAT) ||
        mobile[MOBILE_ACTION] == 0x12 || mobile[MOBILE_ACTION] == 0x13 ||
        *(const float *)(mobile + MOBILE_HEALTH) <= 0)
        return;
    fight = (float)record_ai(ref, AI_FIGHT, *(const int *)(mobile + MOBILE_FIGHT)) +
            FIGHT_DISP_MULT * (float)(50 - base_disposition(ref));
    if (fight + FIGHT_DISTANCE_BASE < FIGHT_ATTACK)
        return;
    for (g = 0; g < PEERS; g++) {
        if (!ghosts[g].placed)
            continue;
        dx = ghosts[g].x - at[0];
        dy = ghosts[g].y - at[1];
        dz = ghosts[g].z - at[2];
        if ((d = dx * dx + dy * dy + dz * dz) >= best)
            continue;
        near = FIGHT_DISTANCE_BASE - FIGHT_DISTANCE_MULT * square_root(d);
        if (fight + (near > 0 ? near : 0) >= FIGHT_ATTACK) {
            best = d;
            pick = g;
        }
    }
    if (pick == PEERS)
        return;
    *put_text(put_int(put_text(line, "StartCombat \"tes3x_ghost"), (int)pick + 1), "\"") = 0;
    run_script_on(line, ref);
    hostiles++;
    tes3x_log_hex3("net.hostile", refid, ghosts[pick].client, pick + 1);
}

/* An actor run here that fights a ghost, once a second: its action bytes (+0xDC, +0xDD), its
 * upper-body animation group and how far it stands from the ghost. */
static void ghost_fight_log(const u8 *mobile, const u8 *ref, u32 refid)
{
    static u32 last, lines;
    const u8 *target = *(const u8 *const *)(mobile + MOBILE_TARGET), *tref, *a;
    const float *p, *q;
    float dx, dy, dz;
    u32 now = now_us();

    if (!plausible(target) || !plausible(tref = *(const u8 *const *)(target + MOBILE_REFERENCE)) ||
        !is_ghost(tref))
        return;
    if (now - last >= 1000000u) {
        last = now;
        lines = 0;
    }
    if (lines++ >= 4)
        return;
    p = (const float *)(ref + REF_POSITION);
    q = (const float *)(tref + REF_POSITION);
    dx = p[0] - q[0];
    dy = p[1] - q[1];
    dz = p[2] - q[2];
    a = ref_animation(ref);
    tes3x_log_hex3("net.ghost_fight", refid,
                   mobile[0xDC] | (u32)mobile[0xDD] << 8 |
                       (u32)(plausible(a) ? a[ANIM_GROUP + 1] : 0xFF) << 16,
                   (u32)round_int(square_root(dx * dx + dy * dy + dz * dz)));
    tes3x_log_hex3("net.ghost_fighter", refid, *(const u32 *)(mobile + MOBILE_FLAGS),
                   *(const u32 *)(mobile + 0x244));
}

/* The talker's side of a hold on a followed actor: HOLD on while the dialogue is open, off when
 * it closes; HOLD_BROKEN from the authority closes it. 1 if the actor is followed. */
static int hold_remote(u8 *actor)
{
    const u8 *ref;
    u32 i, refid = 0, owner = 0, on;

    for (i = 0; actor && i < ACTORS; i++)
        if (followed[i].refid && followed[i].mobile == actor) {
            refid = followed[i].refid;
            owner = followed[i].owner;
        }
    if (actor && !refid && plausible(ref = *(const u8 *const *)(actor + MOBILE_REFERENCE)) &&
        (refid = actor_id(ref)))
        owner = ses.client;
    if (talk_refid && talk_refid != refid) {
        on = 0;
        event_words(EVENT_HOLD, talk_refid, talk_owner, &on);
        talk_refid = 0;
    }
    if (!refid)
        return 0;
    if (!talk_refid) {
        on = 1;
        talk_refid = refid;
        talk_owner = owner;
        talk_broken = 0;
        event_words(EVENT_HOLD, refid, owner, &on);
        tes3x_log_hex3("net.hold_remote", refid, owner, 0);
    }
    if (talk_broken) {
        dialogue_close();
        if (talk_broken == 3) {
            notice("That person is already in conversation.");
            talk_broken = 1;
        }
    }
    return owner != ses.client;
}

/* Out of a session, or in a new one, nothing learned in the last one holds. Runs before the frame's
 * events, which may already belong to the new session. */
static void authority_session(void)
{
    u32 i;

    if (ses.state == SESSION_JOINED && authority_welcome == ses.welcomes)
        return;
    authority_welcome = ses.welcomes;
    authorities = 0;
    owner_count = 0;
    hit_count = 0;
    for (i = 0; i < REMOTE_HOLDS; i++)
        remote_holds[i].release = 1;
    talk_refid = 0;
    death_count = 0; /* the server replays its deaths after WELCOME */
    for (i = 0; i < PEERS; i++) {
        ghost_lives[i].client = 0;
        ghost_lives[i].dead = 0;
    }
}

typedef void(__attribute__((thiscall)) *fn_start_combat)(void *mobile, void *target);
static u32 combats_taken, combats_stopped, combats_lost;
static u8 *actor_ref(u32 refid);

/* What an actor run here fights, as ACTORS sends it. */
static u32 combat_target(const u8 *mobile)
{
    const u8 *target = *(const u8 *const *)(mobile + MOBILE_TARGET), *tref;
    u32 g;

    if (!(*(const u32 *)(mobile + MOBILE_FLAGS) & MOBILE_IN_COMBAT) || !plausible(target) ||
        !plausible(tref = *(const u8 *const *)(target + MOBILE_REFERENCE)))
        return 0;
    if (tref == player_reference())
        return ses.client;
    for (g = 0; g < PEERS; g++)
        if (ghosts[g].ref == tref)
            return ghosts[g].placed ? ghosts[g].client : 0;
    return is_ghost(tref) ? 0 : actor_id(tref);
}

/* An actor this console takes over from another fights what that console's copy last fought, here
 * the player, a ghost or an actor; a fight its held copy picked up meanwhile is stopped. */
static void combat_take(u8 *mobile, void *ref, u32 refid, u32 from)
{
    u8 *tref = 0, *target;
    u32 i, g, lk, flags = 0, id = 0, found = 0;

    lk = lock();
    for (i = 0; i < ACTORS && !found; i++)
        if (actors_in[i].refid == refid && actors_in[i].origin == from && actors_in[i].count) {
            flags = get32le(actors_in[i].state + 24);
            id = get32le(actors_in[i].state + ACTOR_TARGET);
            found = 1;
        }
    unlock(lk);
    if (!found || (flags & ACTOR_DEAD))
        return;
    if (!(flags & ACTOR_IN_COMBAT) || !id) {
        if (*(const u32 *)(mobile + MOBILE_FLAGS) & MOBILE_IN_COMBAT) {
            run_script_on("StopCombat", ref);
            combats_stopped++;
            tes3x_log_hex3("net.combat_stopped", refid, from, 0);
        }
        return;
    }
    if (id == ses.client)
        tref = (u8 *)player_reference();
    else if (id < PLAYER_IDS) {
        for (g = 0; g < PEERS; g++)
            if (ghosts[g].client == id && ghosts[g].placed)
                tref = ghost_ref(g);
    } else
        tref = actor_ref(id);
    if (!plausible(tref) || !plausible(target = ref_mobile(tref)) || target == mobile) {
        combats_lost++;
        tes3x_log_hex3("net.combat_lost", refid, id, from);
        return;
    }
    ((fn_start_combat)TES3X_NET_START_COMBAT)(mobile, target);
    combats_taken++;
    tes3x_log_hex3("net.combat_taken", refid, id, from);
}

static void peace_check(const u8 *mobile, void *ref, u32 refid);

/* Once per frame in the world: follow, send or hold each actor the AI planners hold. */
static void authority_frame(const u8 *player, const u8 *state)
{
    static u32 last_send;
    const u8 *world = *(const u8 **)TES3X_NET_WORLD, *mobs, *process, *node, *planner;
    u8 out[4 + ACTORS_PER_PACKET * ACTOR_BYTES], *mobile, *ref, *a;
    struct pose local;
    struct cell_key key;
    u32 i, n = 0, guard, refid, owner, now = now_us(), lk, send, took;

    read_pose(state, &local);
    for (i = 0; i < authorities; i++)
        if (!key_loaded(&authority[i].key, &local))
            authority_remove(i--);
    send = ses.state == SESSION_JOINED && now - last_send >= ACTOR_PERIOD_US;
    if (send)
        last_send = now;
    for (i = 0; i < ACTORS; i++)
        followed[i].seen = 0;
    for (i = 0; i < REMOTE_HOLDS; i++)
        remote_holds[i].found = 0;
    actor_equipment_begin_frame();
    if (!plausible(world) || !plausible(mobs = *(const u8 **)(world + 0x5C)) ||
        !plausible(process = *(const u8 **)(mobs + MOB_PROCESS)))
        return;
    node = *(const u8 *const *)(process + PROCESS_PLANNERS);
    for (guard = 0; plausible(node) && guard < 512; node = *(const u8 *const *)(node + 4), guard++) {
        if (!plausible(planner = *(const u8 *const *)(node + 8)) ||
            !plausible(mobile = *(u8 *const *)(planner + PLANNER_MOBILE)) ||
            !plausible(ref = *(u8 **)(mobile + MOBILE_REFERENCE)) || ref == player ||
            !(refid = actor_id(ref)) || is_ghost(ref))
            continue;
        actor_key(&local, *(const float *)(ref + 0x38), *(const float *)(ref + 0x3C), &key);
        owner = actor_owner(refid, authority_of(&key));
        death_frame(mobile, ref, refid, owner);
        if (send)
            status_frame(mobile, ref, refid);
        if (owner && owner != ses.client) {
            follow(mobile, ref, refid, owner);
            continue;
        }
        for (i = 0, took = 0; i < ACTORS; i++)
            if (followed[i].refid == refid) {
                took = followed[i].owner;
                unfollow(i, followed[i].mobile == mobile);
            }
        if (owner != ses.client)
            continue;
        actor_equipment_send(ref, refid);
        if (took)
            combat_take(mobile, ref, refid, took);
        remote_hold_apply(mobile, refid);
        hits_apply(mobile, ref, refid);
        if (!send)
            continue;
        hostile_check(mobile, ref, refid);
        peace_check(mobile, ref, refid);
        ghost_fight_log(mobile, ref, refid);
        a = out + 4 + n * ACTOR_BYTES;
        put32le(a, refid);
        copy(a + 4, ref + 0x38, 12);
        copy(a + 16, ref + 0x34, 4);
        copy(a + ACTOR_HEALTH, mobile + MOBILE_HEALTH, 4);
        put32le(a + 24, (mobile[MOBILE_ACTION] == 0x12 || mobile[MOBILE_ACTION] == 0x13
                         ? ACTOR_DEAD : 0) |
                        (*(const u32 *)(mobile + MOBILE_FLAGS) & MOBILE_IN_COMBAT
                         ? ACTOR_IN_COMBAT : 0) | stance_of(mobile));
        copy(a + ACTOR_MAGICKA, mobile + MOBILE_MAGICKA, 4);
        copy(a + ACTOR_FATIGUE, mobile + MOBILE_FATIGUE, 4);
        put32le(a + ACTOR_TARGET, combat_target(mobile));
        anim_capture(ref, a + ACTOR_ANIM);
        if (++n == ACTORS_PER_PACKET) {
            put32le(out, n);
            lk = lock();
            session_send(T3MP_ACTORS, out, 4 + n * ACTOR_BYTES);
            unlock(lk);
            actor_states_out += n;
            n = 0;
        }
    }
    if (n) {
        put32le(out, n);
        lk = lock();
        session_send(T3MP_ACTORS, out, 4 + n * ACTOR_BYTES);
        unlock(lk);
        actor_states_out += n;
    }
    hit_count = 0; /* a hit on an actor not loaded here is lost */
    for (i = 0; i < ACTORS; i++)
        if (followed[i].refid && !followed[i].seen)
            unfollow(i, 0);
    for (i = 0; i < REMOTE_HOLDS; i++)
        if (remote_holds[i].refid && !remote_holds[i].found &&
            (remote_holds[i].release || remote_holds[i].mobile))
            remote_holds[i].refid = 0;
}

static void owners_event(const struct event *e)
{
    u32 count = e->length >= 4 ? get32le(e->data) : 0, i, j, id, client;

    for (i = 0; i < count && 8 + i * 8 <= e->length; i++) {
        id = get32le(e->data + 4 + i * 8);
        client = get32le(e->data + 8 + i * 8);
        owners_in++;
        for (j = 0; j < owner_count && owners[j].id != id; j++)
            ;
        if (j < owner_count && !client)
            owners[j] = owners[--owner_count];
        else if (j < owner_count)
            owners[j].client = client;
        else if (client && owner_count < OWNERS) {
            owners[owner_count].id = id;
            owners[owner_count++].client = client;
        } else if (client)
            owners_full++;
        if (owners_in <= 64)
            tes3x_log_hex3("net.owner", id, client, 0);
    }
}

static void authority_event(const struct event *e)
{
    struct cell_key key;
    u32 refid = get32le(e->data), target = get32le(e->data + 4), value = get32le(e->data + 8), i;

    if (e->kind == EVENT_AUTHORITY) {
        if (e->length < KEY_BYTES + 4)
            return;
        key.kind = get32le(e->data);
        key.gx = (int)get32le(e->data + 4);
        key.gy = (int)get32le(e->data + 8);
        copy(key.name, e->data + 12, CELL_NAME);
        key.name[CELL_NAME - 1] = 0;
        authority_set(&key, get32le(e->data + KEY_BYTES));
        return;
    }
    if (e->kind == EVENT_OWNERS) {
        owners_event(e);
        return;
    }
    if (e->kind == EVENT_DEATH) {
        if (e->length >= 4 && !death_known(refid)) {
            death_add(refid);
            tes3x_log_hex3("net.death", refid, e->origin, 0);
        }
        return;
    }
    if (e->length < 12 || target != ses.client)
        return;
    if (e->kind == EVENT_HOLD) {
        for (i = 0; i < REMOTE_HOLDS; i++)
            if (remote_holds[i].refid == refid)
                break;
        if (i == REMOTE_HOLDS && value)
            for (i = 0; i < REMOTE_HOLDS && remote_holds[i].refid; i++)
                ;
        if (i == REMOTE_HOLDS)
            return;
        remote_holds_in++;
        tes3x_log_hex3("net.hold_request", refid, e->origin, value);
        if (!remote_holds[i].refid) {
            remote_holds[i].refid = refid;
            remote_holds[i].mobile = 0;
        }
        remote_holds[i].holder = e->origin;
        remote_holds[i].release = !value;
    } else if (e->kind == EVENT_HOLD_BROKEN) {
        remote_breaks_in++;
        tes3x_log_hex3("net.hold_broken_remote", refid, e->origin, value);
        if (refid == talk_refid)
            talk_broken = value ? value : 1;
    } else if (e->kind == EVENT_HIT && hit_count < HITS) {
        if (!float_within(e->data + 8, e->length >= 16 ? 2 : 1, STAT_LIMIT)) {
            refused_events++;
            return;
        }
        hits[hit_count].refid = refid;
        hits[hit_count].origin = e->origin;
        copy((u8 *)&hits[hit_count].damage, e->data + 8, 4);
        hits[hit_count].fatigue = 0;
        if (e->length >= 16)
            copy((u8 *)&hits[hit_count].fatigue, e->data + 12, 4);
        tes3x_log_hex3("net.hit", refid, e->origin, (u32)round_int(hits[hit_count].damage));
        hit_count++;
        hits_in++;
    }
}

static void authority_stat(void)
{
    u32 i, n = 0, holds_now = 0;

    for (i = 0; i < ACTORS; i++)
        n += followed[i].refid != 0;
    for (i = 0; i < REMOTE_HOLDS; i++)
        holds_now += remote_holds[i].refid != 0;
    tes3x_log_hex3("net.authorities", authorities, n, holds_now);
    tes3x_log_hex3("net.owners", owner_count, owners_in, owners_full);
    tes3x_log_hex3("net.combats_taken", combats_taken, combats_stopped, combats_lost);
    for (i = 0; i < authorities; i++)
        tes3x_log_hex3("net.authority_is", authority[i].client, (u32)authority[i].key.gx,
                       (u32)authority[i].key.gy);
    tes3x_log_hex3("net.actor_states", actor_states_out, actor_states_in, actor_moves);
    tes3x_log_hex3("net.actor_events", follows, hits_out, hits_in);
    tes3x_log_hex3("net.ai_held", ai_held, ai_hooked, follows_full);
    {
        const u8 *w = *(const u8 **)TES3X_NET_WORLD, *m, *pm;

        if (plausible(w) && plausible(m = *(const u8 **)(w + 0x5C)) &&
            plausible(pm = *(const u8 **)(m + MOB_PROCESS)))
            tes3x_log_hex3("net.ai_distance", (u32)round_int(*(const float *)(pm + 0x830)), 0, 0);
    }
    tes3x_log_hex3("net.player_hits", player_hits_out, player_hits_in, retaliations);
    tes3x_log("net.player_hits_unseen", player_hits_unseen);
    tes3x_log_hex3("net.hostiles", hostiles, bloodied, 0);
    tes3x_log_hex3("net.actor_deaths", death_count, deaths_reported, deaths_applied);
    tes3x_log_hex3("net.actor_holds", remote_holds_in, remote_breaks_out, remote_breaks_in);
}
