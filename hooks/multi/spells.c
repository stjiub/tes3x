/* Spells take effect where their target is run: the local player, or an actor this console runs.
 * The engine applies one effect of a magic source instance to one target at a time (spellHit, from
 * a cast on self, a touch or a projectile's hit), and its call sites lead to spell_hit_hook. A
 * spell cast here that reaches another client's ghost, or an actor another client runs, is sent to
 * that client as SPELL instead, which applies it to the real target with the caster's stand-in as
 * caster. A stand-in's own casts apply nowhere: its console sends what they hit. Sources other than
 * spells (enchantments, potions) apply here as before, so their damage still goes out as a hit. */
#define EVENT_SPELL 10u /* caster refid, target client, target refid (0: its player), source,
                           caster is a player, spell id */
#define SPELL_BYTES 14u
#define SPELL_ID 32u
#define SOURCE_SPELL 1u
#define TYPE_SPELL 0x4C455053u /* 'SPEL' */
#define OBJECT_TYPE 4
#define WORLD_MAGIC 0x6C /* the MagicInstanceController */
#define INSTANCE_CHANCE 0x10
#define INSTANCE_TARGET 0x14 /* touch and target effects go straight to it, as a trap's do */
#define INSTANCE_SOURCE 0xA0 /* object, then the source type byte */
#define INSTANCE_STATE 0xB4
#define INSTANCE_CASTER 0xB8
#define INSTANCE_WORKING 5 /* cast and working its effects */
#define SOURCE_EFFECTS 0x34 /* eight of EFFECT_BYTES; an id of -1 ends them */
#define EFFECT_BYTES 0x18
#define EFFECT_RANGE 4
#define RANGE_SELF 0
#define SOURCE_MAX_EFFECTS 8
#define NOBODY 0xFFFFFFFFu
#define SPELLS_SENT 8u

typedef void(__attribute__((thiscall)) *fn_spell_hit)(void *instance, void *target, int effect);
typedef int(__attribute__((thiscall)) *fn_activate_spell)(void *controller, void *caster, void *item,
                                                          void *combo);
typedef u8 *(__attribute__((thiscall)) *fn_magic_instance)(void *controller, int serial);
typedef u8 *(__attribute__((thiscall)) *fn_resolve_object)(void *records, const char *id);

static const u32 spell_sites[] = TES3X_NET_SPELL_HIT_SITES;
static u32 spell_hooked, spells_sent, spells_received, spells_applied, spells_withheld;
static u32 spell_failures;
static const u8 *spell_withheld_last;
/* Instance and target pairs sent this frame: spellHit comes once per effect. */
static struct {
    const u8 *instance, *target;
} spell_sent[SPELLS_SENT];
static u32 spell_sent_count;

/* Which console applies what happens to ref: 0 this one, NOBODY none (a parked ghost), else the
 * client that runs it. A followed actor's refid goes to *refid. */
static u32 ref_owner(const u8 *ref, u32 *refid)
{
    const u8 *mobile;
    u32 i;

    *refid = 0;
    if (!plausible(ref) || ref == player_reference() || !plausible(mobile = ref_mobile(ref)))
        return 0;
    for (i = 0; i < PEERS; i++)
        if (ghosts[i].ref == ref)
            return ghosts[i].placed && ghosts[i].client ? ghosts[i].client : NOBODY;
    if (is_ghost(ref))
        return NOBODY;
    for (i = 0; i < ACTORS; i++)
        if (followed[i].refid && followed[i].mobile == mobile) {
            *refid = followed[i].refid;
            return followed[i].owner;
        }
    return 0;
}

/* An actor the AI planners hold, by refid. */
static u8 *actor_ref(u32 refid)
{
    const u8 *world = *(const u8 **)TES3X_NET_WORLD, *mobs, *process, *node, *planner, *mobile;
    u8 *ref;
    u32 guard;

    if (!refid || !plausible(world) || !plausible(mobs = *(const u8 **)(world + 0x5C)) ||
        !plausible(process = *(const u8 **)(mobs + MOB_PROCESS)))
        return 0;
    node = *(const u8 *const *)(process + PROCESS_PLANNERS);
    for (guard = 0; plausible(node) && guard < 512; node = *(const u8 *const *)(node + 4), guard++)
        if (plausible(planner = *(const u8 *const *)(node + 8)) &&
            plausible(mobile = *(const u8 *const *)(planner + PLANNER_MOBILE)) &&
            plausible(ref = *(u8 *const *)(mobile + MOBILE_REFERENCE)) && actor_id(ref) == refid)
            return ref;
    return 0;
}

/* The id of an instance's spell, or 0 if its source is not a spell. */
static const char *spell_id(const u8 *instance)
{
    const u8 *source = *(const u8 *const *)(instance + INSTANCE_SOURCE);
    const char *id;

    if (instance[INSTANCE_SOURCE + 4] != SOURCE_SPELL || !plausible(source))
        return 0;
    id = ((fn_object_id)(*(void *const *const *)source)[OBJECT_GET_ID / 4])(source);
    return mapped(id) && *id ? id : 0;
}

/* SPELL or CAST data for an instance's spell cast by caster; its length, or 0 if the source is not
 * a spell. */
static u32 spell_data(u8 *data, const u8 *instance, const u8 *caster, u32 client, u32 refid)
{
    const u8 *player;
    const char *id;
    u32 n;

    if (!(id = spell_id(instance)))
        return 0;
    player = player_reference();
    put32le(data, plausible(caster) && caster != player ? actor_id(caster) : 0);
    put32le(data + 4, client);
    put32le(data + 8, refid);
    data[12] = SOURCE_SPELL;
    data[13] = caster == player;
    for (n = 0; id[n] && n < SPELL_ID - 1; n++)
        data[SPELL_BYTES + n] = (u8)id[n];
    data[SPELL_BYTES + n] = 0;
    return SPELL_BYTES + n + 1;
}

/* 1 if the hit went out, or went out already for another effect; 0 if it cannot be shared. */
static int spell_send(const u8 *instance, const u8 *caster, const u8 *target, u32 owner,
                      u32 refid)
{
    u8 data[SPELL_BYTES + SPELL_ID];
    u32 i, n;

    for (i = 0; i < spell_sent_count; i++)
        if (spell_sent[i].instance == instance && spell_sent[i].target == target)
            return 1;
    if (!(n = spell_data(data, instance, caster, owner, refid)))
        return 0;
    if (spell_sent_count < SPELLS_SENT) {
        spell_sent[spell_sent_count].instance = instance;
        spell_sent[spell_sent_count++].target = target;
    }
    if (!event_queue(EVENT_SPELL, data, n)) {
        tes3x_log("net.event_full", EVENT_SPELL);
        return 1;
    }
    spells_sent++;
    log_text("net.spell_sent", (const char *)data + SPELL_BYTES);
    tes3x_log_hex3("net.spell_to", owner, refid, get32le(data));
    return 1;
}

/* A reference as the other consoles can find it: the client that runs it, and its refid unless it
 * is that client's player. 0 if it cannot be named. */
static u32 ref_name(const u8 *ref, u32 *refid)
{
    u32 owner = ref_owner(ref, refid);

    if (owner == NOBODY || !plausible(ref))
        return 0;
    if (!owner && ref != player_reference() && !(*refid = actor_id(ref)))
        return 0;
    return owner ? owner : ses.client;
}

/* The reference a name from another console stands for here, or 0. */
static u8 *ref_named(u32 client, u32 refid)
{
    u32 i;

    if (refid)
        return actor_ref(refid);
    if (client == ses.client)
        return (u8 *)player_reference();
    for (i = 0; i < PEERS; i++)
        if (ghosts[i].client == client && ghosts[i].placed)
            return ghost_ref(i);
    return 0;
}

/* Casts. When a caster run here casts a spell with target effects, the spell goes to the other
 * consoles as CAST, with its target when the instance has one, and they replay it from the
 * caster's stand-in so the bolt flies there too. Every effect of a stand-in's cast is withheld,
 * so the replay only shows; what it does arrives as SPELL. */
#define EVENT_CAST 11u /* as SPELL; the target is optional */
#define INSTANCE_CASTING 1

typedef void(__cdecl *fn_cast_bolt)(void *instance, int effect, int index, int count);

static const u32 bolt_sites[] = TES3X_NET_CAST_BOLT_SITES;
static u32 casts_sent, casts_received, casts_replayed, casts_bolted;
static const u8 *cast_sent_last;

/* With no target, the engine launches an actor's bolt only when the actor has a combat target,
 * and then aims it there. A replayed cast without one borrows the stand-in itself as combat
 * target past that test, and gives it back before the bolt is aimed, so it flies where the
 * stand-in faces; or after a few frames if the cast never gets that far. */
#define CAST_AIM_FRAMES 8
static struct {
    u8 *mobile;
    const u8 *instance;
    u32 frames;
} cast_aim;

static void cast_aim_end(void)
{
    if (cast_aim.mobile && *(u8 **)(cast_aim.mobile + MOBILE_TARGET) == cast_aim.mobile)
        *(u8 **)(cast_aim.mobile + MOBILE_TARGET) = 0;
    cast_aim.mobile = 0;
    cast_aim.instance = 0;
}

static void cast_aim_frame(void)
{
    if (cast_aim.mobile && !--cast_aim.frames)
        cast_aim_end();
}

/* The caster's three layer groups and keys as the stream sends them (the player's, in first
 * person, from the model it runs), for comparing a cast's animation on both consoles. */
static void cast_anim_log(const char *what, const u8 *caster)
{
    u8 anim[ANIM_BYTES];

    if (caster == player_reference())
        player_anim_capture(caster, anim);
    else
        anim_capture(caster, anim);
    tes3x_log_hex3(what, (u32)anim[0] | (u32)anim[1] << 8 | (u32)anim[2] << 16,
                   (u32)anim[4] | (u32)anim[5] << 8 | (u32)anim[6] << 16, 0);
}

static void __cdecl cast_bolt_hook(u8 *instance, int effect, int index, int count)
{
    const u8 *caster = *(const u8 *const *)(instance + INSTANCE_CASTER), *target;
    u8 data[SPELL_BYTES + SPELL_ID];
    u32 client = 0, refid = 0, n;

    if (instance == cast_aim.instance && index + 1 >= count)
        cast_aim_end();
    ((fn_cast_bolt)TES3X_NET_CAST_BOLT)(instance, effect, index, count);
    if (ses.state != SESSION_JOINED)
        return;
    if (ref_owner(caster, &n)) {
        casts_bolted++;
        tes3x_log_hex3("net.cast_bolt", (u32)caster, (u32)index, (u32)count);
        return;
    }
    if (instance == cast_sent_last)
        return;
    cast_sent_last = instance;
    if (plausible(target = *(const u8 *const *)(instance + INSTANCE_TARGET)))
        client = ref_name(target, &refid);
    if (!(n = spell_data(data, instance, caster, client, client ? refid : 0)))
        return;
    if (!event_queue(EVENT_CAST, data, n)) {
        tes3x_log("net.event_full", EVENT_CAST);
        return;
    }
    casts_sent++;
    log_text("net.cast_sent", (const char *)data + SPELL_BYTES);
    tes3x_log_hex3("net.cast_at", client, refid, get32le(data));
    cast_anim_log("net.cast_anim_sent", caster);
}

static void affect_send(const u8 *instance, const u8 *target, u32 effect);

static void __attribute__((thiscall)) spell_hit_hook(u8 *instance, u8 *target, int effect)
{
    const u8 *caster;
    u32 refid, owner;

    if (ses.state == SESSION_JOINED && plausible(instance)) {
        caster = *(const u8 *const *)(instance + INSTANCE_CASTER);
        owner = ref_owner(caster, &refid) ? NOBODY : ref_owner(target, &refid);
        if (owner == NOBODY) {
            if (instance != spell_withheld_last) {
                spell_withheld_last = instance;
                tes3x_log_hex3("net.spell_withheld", (u32)caster, (u32)target, (u32)effect);
            }
            spells_withheld++;
            return;
        }
        if (owner && spell_send(instance, caster, target, owner, refid))
            return;
        if (!owner) {
            ((fn_spell_hit)TES3X_NET_SPELL_HIT)(instance, target, effect);
            affect_send(instance, target, (u32)effect);
            return;
        }
    }
    ((fn_spell_hit)TES3X_NET_SPELL_HIT)(instance, target, effect);
}

/* Points each `call rel32` in sites from original to hook; none if any site is not such a call. */
static int redirect_calls(const u32 *sites, u32 n, u32 original, const void *hook)
{
    u32 i, cr0, flags;
    u8 *site;

    for (i = 0; i < n; i++) {
        site = (u8 *)sites[i];
        if (site[0] != 0xE8 || (u32)site + 5 + *(const u32 *)(site + 1) != original) {
            tes3x_log_hex3("net.call_site_unexpected", (u32)site, site[0], original);
            return 0;
        }
    }
    flags = lock();
    __asm__ volatile("movl %%cr0, %0" : "=r"(cr0));
    __asm__ volatile("movl %0, %%cr0" : : "r"(cr0 & ~CR0_WP) : "memory");
    for (i = 0; i < n; i++) {
        site = (u8 *)sites[i];
        *(u32 *)(site + 1) = (u32)hook - ((u32)site + 5);
    }
    __asm__ volatile("movl %0, %%cr0" : : "r"(cr0) : "memory");
    unlock(flags);
    return 1;
}

static void spell_hook_install(void)
{
    if (spell_hooked)
        return;
    spell_hooked = 1;
    if (redirect_calls(spell_sites, sizeof(spell_sites) / sizeof(spell_sites[0]),
                       TES3X_NET_SPELL_HIT, (const void *)spell_hit_hook))
        spell_hooked |= 2;
    if (redirect_calls(bolt_sites, sizeof(bolt_sites) / sizeof(bolt_sites[0]),
                       TES3X_NET_CAST_BOLT, (const void *)cast_bolt_hook))
        spell_hooked |= 4;
    tes3x_log("net.spell_hook", spell_hooked);
}

static void spell_fail(const char *id, u32 why, u32 refid)
{
    spell_failures++;
    log_text("net.spell_failed", id);
    tes3x_log_hex3("net.spell_failed_why", why, refid, 0);
}

/* The id and names of a SPELL or CAST; 0 if it is too short. */
static int spell_read(const struct event *e, char *id, u32 *caster_id, u32 *client, u32 *refid)
{
    u32 n;

    if (e->length < SPELL_BYTES + 1)
        return 0;
    *caster_id = get32le(e->data);
    *client = get32le(e->data + 4);
    *refid = get32le(e->data + 8);
    for (n = 0; n < SPELL_ID - 1 && SPELL_BYTES + n < e->length && e->data[SPELL_BYTES + n]; n++)
        id[n] = (char)e->data[SPELL_BYTES + n];
    id[n] = 0;
    return 1;
}

/* The caster's stand-in here: the origin's ghost, or the actor. */
static u8 *spell_caster(const struct event *e, u32 caster_id)
{
    u32 i;

    if (!e->data[13])
        return actor_ref(caster_id);
    for (i = 0; i < PEERS; i++)
        if (ghosts[i].client == e->origin && ghosts[i].placed)
            return ghost_ref(i);
    return 0;
}

/* A new instance of the spell id from caster, certain to succeed, aimed at target; 0 on failure,
 * logged with refid. */
static u8 *spell_start(const char *id, u8 *caster, u8 *target, u32 refid)
{
    const u8 *handler = *(const u8 **)TES3X_NET_DATA_HANDLER;
    u8 *world = *(u8 **)TES3X_NET_WORLD, *source, *instance;
    void *records, *controller;
    struct {
        u8 *object;
        u32 type;
    } combo;

    if (!plausible(world) || !plausible(handler) || !plausible(records = *(void **)handler) ||
        !plausible(controller = *(void **)(world + WORLD_MAGIC))) {
        spell_fail(id, 2, refid);
        return 0;
    }
    source = ((fn_resolve_object)TES3X_NET_RESOLVE_OBJECT)(records, id);
    if (!plausible(source) || *(const u32 *)(source + OBJECT_TYPE) != TYPE_SPELL) {
        spell_fail(id, 3, refid); /* a spell made in the caster's game */
        return 0;
    }
    combo.object = source;
    combo.type = SOURCE_SPELL;
    instance = ((fn_magic_instance)TES3X_NET_MAGIC_INSTANCE)(
        controller, ((fn_activate_spell)TES3X_NET_ACTIVATE_SPELL)(controller, caster, 0, &combo));
    if (!plausible(instance)) {
        spell_fail(id, 4, refid);
        return 0;
    }
    *(float *)(instance + INSTANCE_CHANCE) = 100.0f;
    *(u8 **)(instance + INSTANCE_TARGET) = target;
    return instance;
}

/* Another console's spell reached a target this one runs: a new instance of the spell from the
 * caster's stand-in (or the target itself), each touch and target effect applied to the target as
 * a hit applies it, then left to work as a cast one does. */
static void spell_event(const struct event *e)
{
    const u8 *effects;
    u8 *target, *caster, *instance;
    char id[SPELL_ID];
    u32 refid, caster_id, client, i;

    if (!spell_read(e, id, &caster_id, &client, &refid) || client != ses.client)
        return;
    spells_received++;
    log_text("net.spell", id);
    tes3x_log_hex3("net.spell_from", e->origin, refid, caster_id);
    target = refid ? actor_ref(refid) : (u8 *)player_reference();
    if (!target || ref_owner(target, &i)) {
        spell_fail(id, 1, refid); /* not here, or run elsewhere by now */
        return;
    }
    if (!(caster = spell_caster(e, caster_id)))
        caster = target;
    if (!(instance = spell_start(id, caster, target, refid)))
        return;
    effects = *(const u8 *const *)(instance + INSTANCE_SOURCE) + SOURCE_EFFECTS;
    for (i = 0; i < SOURCE_MAX_EFFECTS && *(const short *)(effects + i * EFFECT_BYTES) != -1; i++)
        if (effects[i * EFFECT_BYTES + EFFECT_RANGE] != RANGE_SELF) {
            ((fn_spell_hit)TES3X_NET_SPELL_HIT)(instance, target, (int)i);
            affect_send(instance, target, i);
        }
    *(u32 *)(instance + INSTANCE_STATE) = INSTANCE_WORKING;
    spells_applied++;
    tes3x_log_hex3("net.spell_applied", refid, (u32)caster, i);
}

/* Another console's caster cast: the stand-in casts it here too, at the target when it has one. */
static void cast_event(const struct event *e)
{
    u8 *caster, *target, *instance, *mobile;
    char id[SPELL_ID];
    u32 refid, caster_id, client, own;

    if (!spell_read(e, id, &caster_id, &client, &refid))
        return;
    casts_received++;
    log_text("net.cast", id);
    tes3x_log_hex3("net.cast_from", e->origin, client, refid);
    caster = spell_caster(e, caster_id);
    if (!caster || !ref_owner(caster, &own))
        return; /* not here, or run here: not a stand-in */
    target = client ? ref_named(client, refid) : 0;
    if (!(instance = spell_start(id, caster, target, refid)))
        return;
    *(u32 *)(instance + INSTANCE_STATE) = INSTANCE_CASTING;
    if (!target && plausible(mobile = ref_mobile(caster)) &&
        !*(const u32 *)(mobile + MOBILE_TARGET)) {
        cast_aim_end();
        *(u8 **)(mobile + MOBILE_TARGET) = mobile;
        cast_aim.mobile = mobile;
        cast_aim.instance = instance;
        cast_aim.frames = CAST_AIM_FRAMES;
    }
    casts_replayed++;
    tes3x_log_hex3("net.cast_replayed", (u32)caster, (u32)target, 0);
    cast_anim_log("net.cast_anim_here", caster);
}

static void spell_stat(void)
{
    tes3x_log_hex3("net.spells", spells_sent, spells_received, spells_applied);
    tes3x_log_hex3("net.spells_withheld", spells_withheld, spell_failures, spell_hooked);
    tes3x_log_hex3("net.casts", casts_sent, casts_received, casts_replayed);
    tes3x_log_hex3("net.casts_bolted", casts_bolted, 0, 0);
}
