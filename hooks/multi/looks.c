/* Equipment. Each client sends the ids of what its player has equipped whenever they change, as
 * EQUIPMENT events: part, parts, then ids each ending in a zero. The server keeps every client's
 * latest set and replays it to whoever joins. A ghost is made to wear its peer's set by comparing
 * it with what the ghost has equipped: RemoveItem what it should not wear, Equip what it lacks. */
#define EVENT_EQUIPMENT 7u
#define EVENT_WEATHER 8u /* the weather section */
#define EVENT_PLAYER_HIT 9u /* attacker refid (0: a player), victim client, health, fatigue */
#define PLAYER_DEATH 8u     /* to server and peers: the player died */
#define PLAYER_ALIVE 10u    /* to server and peers: the player respawned */
#define EQUIP_ITEMS 24u
#define EQUIP_ID 32u
#define EQUIP_PERIOD_US 500000u
#define ACTOR_EQUIPMENT 0x58 /* list: head +0x8; node: next +0x4, stack +0x8, object first */
#define OBJECT_GET_ID 0x20   /* vtable offset */

typedef const char *(__attribute__((thiscall)) *fn_object_id)(const void *object);

static struct {
    u32 client, used, generation, count, staged, next_part;
    char ids[EQUIP_ITEMS][EQUIP_ID], stage[EQUIP_ITEMS][EQUIP_ID];
} looks[PEERS];
static u32 look_clock;

static u32 equipment_ids(const u8 *ref, char ids[][EQUIP_ID])
{
    const u8 *actor = *(const u8 *const *)(ref + 0x28), *node, *stack, *object;
    void *const *vtable;
    const char *id;
    u32 n = 0, guard, k;

    if (!plausible(actor))
        return 0;
    node = *(const u8 *const *)(actor + ACTOR_EQUIPMENT + 8);
    for (guard = 0; plausible(node) && guard < 64 && n < EQUIP_ITEMS;
         node = *(const u8 *const *)(node + 4), guard++) {
        if (!plausible(stack = *(const u8 *const *)(node + 8)) ||
            !plausible(object = *(const u8 *const *)stack) ||
            !plausible(vtable = *(void *const *const *)object))
            continue;
        id = ((fn_object_id)vtable[OBJECT_GET_ID / 4])(object);
        if (!mapped(id) || !*id)
            continue;
        for (k = 0; id[k] && k < EQUIP_ID - 1; k++)
            ids[n][k] = id[k];
        ids[n++][k] = 0;
    }
    return n;
}

static int has_id(char ids[][EQUIP_ID], u32 n, const char *id)
{
    u32 i, k;

    for (i = 0; i < n; i++) {
        for (k = 0; ids[i][k] && ids[i][k] == id[k]; k++)
            ;
        if (ids[i][k] == id[k])
            return 1;
    }
    return 0;
}

/* Game thread, in the world: sends the player's set when it differs from the last one sent in
 * this session, whole or not at all. */
static void equipment_send(const u8 *ref)
{
    static u32 last_check, sent_hash, sent_welcome;
    char ids[EQUIP_ITEMS][EQUIP_ID];
    u8 parts[EQUIP_ITEMS][EVENT_DATA];
    u32 lengths[EQUIP_ITEMS], n, i, k, count = 0, hash = 2166136261u, flags, room, now = now_us();

    if (ses.state != SESSION_JOINED || now - last_check < EQUIP_PERIOD_US)
        return;
    last_check = now;
    n = equipment_ids(ref, ids);
    for (i = 0; i < n; i++) {
        for (k = 0; ids[i][k]; k++)
            hash = (hash ^ (u8)ids[i][k]) * 16777619u;
        hash *= 16777619u; /* the terminating zero */
    }
    if (hash == sent_hash && sent_welcome == ses.welcomes)
        return;
    lengths[0] = 2;
    for (i = 0; i < n; i++) {
        for (k = 0; ids[i][k]; k++)
            ;
        if (lengths[count] + k + 1 > EVENT_DATA)
            lengths[++count] = 2;
        copy(parts[count] + lengths[count], (const u8 *)ids[i], k + 1);
        lengths[count] += k + 1;
    }
    count++;
    flags = lock();
    room = EVENTS_OUT - (rel.out_next - rel.out_first);
    unlock(flags);
    if (room < count)
        return;
    for (i = 0; i < count; i++) {
        parts[i][0] = (u8)i;
        parts[i][1] = (u8)count;
        event_queue(EVENT_EQUIPMENT, parts[i], lengths[i]);
    }
    sent_hash = hash;
    sent_welcome = ses.welcomes;
    equip_sent++;
    tes3x_log_hex3("net.equipment_sent", n, count, hash);
}

static u32 look_of(u32 client)
{
    u32 i, j, pick = 0;

    for (i = 0; i < PEERS; i++)
        if (looks[i].client == client)
            return i;
    /* Otherwise the least recently used entry of a client that is not a peer now. */
    for (i = 0; i < PEERS; i++) {
        for (j = 0; j < PEERS && peers[j].client != looks[i].client; j++)
            ;
        if ((j == PEERS || !looks[i].client) && looks[i].used <= looks[pick].used)
            pick = i;
    }
    looks[pick].client = client;
    looks[pick].generation = looks[pick].count = looks[pick].staged = looks[pick].next_part = 0;
    return pick;
}

static void equipment_event(const struct event *e)
{
    u32 l, off = 2, k;

    if (e->length < 2)
        return;
    for (off = 2; off < e->length; off++)
        if ((e->data[off] && e->data[off] < 0x20) || e->data[off] == '"') {
            refused_events++;
            return;
        }
    off = 2;
    l = look_of(e->origin);
    looks[l].used = ++look_clock;
    if (e->data[0] == 0)
        looks[l].staged = looks[l].next_part = 0;
    else if (e->data[0] != looks[l].next_part)
        return;
    looks[l].next_part++;
    while (off < e->length && looks[l].staged < EQUIP_ITEMS) {
        for (k = 0; off + k < e->length && e->data[off + k] && k < EQUIP_ID - 1; k++)
            looks[l].stage[looks[l].staged][k] = (char)e->data[off + k];
        looks[l].stage[looks[l].staged++][k] = 0;
        while (off < e->length && e->data[off])
            off++;
        off++;
    }
    if (looks[l].next_part != e->data[1])
        return;
    copy((u8 *)looks[l].ids, (const u8 *)looks[l].stage, sizeof(looks[l].ids));
    looks[l].count = looks[l].staged;
    looks[l].generation = look_clock;
    equip_received++;
    tes3x_log_hex3("net.equipment", e->origin, looks[l].count, looks[l].generation);
}

static void ghost_item(u32 i, const char *verb, const char *id, const char *tail)
{
    char line[128];
    char *p = put_text(put_text(put_ghost(line, i), verb), " \"");

    p = put_text(put_text(put_text(p, id), "\""), tail);
    *p = 0;
    run_script(line);
}

static void body_parts_update(u8 *ref);

/* Once per new set, on a placed ghost. */
static void equipment_apply(u32 i, u8 *ref)
{
    char worn[EQUIP_ITEMS][EQUIP_ID];
    u32 l, n, k, removed = 0, added = 0;

    for (l = 0; l < PEERS && looks[l].client != ghosts[i].client; l++)
        ;
    if (l == PEERS || !looks[l].generation || looks[l].generation == ghosts[i].look)
        return;
    ghosts[i].look = looks[l].generation;
    looks[l].used = ++look_clock;
    n = equipment_ids(ref, worn);
    for (k = 0; k < n; k++)
        if (!has_id(looks[l].ids, looks[l].count, worn[k])) {
            ghost_item(i, "RemoveItem", worn[k], " 1");
            removed++;
        }
    for (k = 0; k < looks[l].count; k++)
        if (!has_id(worn, n, looks[l].ids[k])) {
            ghost_item(i, "Equip", looks[l].ids[k], "");
            added++;
        }
    if (removed || added)
        ((fn_ref_update)TES3X_NET_UPDATE_BIPED_PARTS)(ref);
    equip_applied++;
    tes3x_log_hex3("net.ghost_equip", i + 1, removed << 16 | added,
                   equipment_ids(ref, worn) << 16 | looks[l].count);
}

/* A ghost's plugin NPC is only a placeholder. Each player sends the character name, sex and the
 * record ids that choose its race, head and hair. The server retains the two reliable parts and
 * replays them after WELCOME. The name lives in its identity slot because the ghost NPC points at
 * it after the event has gone away; normal data-file NPCs do not retain the player's linked ids. */
#define IDENTITY_PARTS 2u
#define IDENTITY_TEXT 32u
#define NPC_FLAGS 0x34
#define NPC_BASE 0x6C
#define NPC_NAME 0x70
#define NPC_LINKS 0x78
#define RACE_ID 0x10
#define NPC_RACE 0xB0
#define NPC_HEAD 0xBC
#define NPC_HAIR 0xC0
#define NPC_FEMALE 1u
#define ATTACHMENT_BODY_PARTS 1u

typedef void(__attribute__((thiscall)) *fn_body_part_update)(void *manager, void *ref);
typedef u8 *(__attribute__((thiscall)) *fn_scene_node)(void *object);
typedef void(__attribute__((thiscall)) *fn_reset_visual)(void *object, void *node);
typedef void(__attribute__((thiscall)) *fn_attach_child)(void *parent, void *child, u8 first);
typedef void(__attribute__((thiscall)) *fn_node_call)(void *node);
typedef void(__attribute__((thiscall)) *fn_mobile_simulation)(void *mobile, u8 entering);
typedef void(__attribute__((thiscall)) *fn_ghost_add_mob)(void *mobs, void *ref);

#define OBJECT_GET_SCENE_NODE 0x2Cu
#define OBJECT_RESET_VISUAL_NODE 0x11Cu
#define NODE_PARENT 0x18u
#define NODE_ATTACH_CHILD 0x94u
#define MOBILE_ENTER_SIMULATION 0x70u

static struct {
    u32 client, used, generation, have, female;
    char name[IDENTITY_TEXT], race[IDENTITY_TEXT], head[IDENTITY_TEXT], hair[IDENTITY_TEXT];
} identities[PEERS];
static u32 identity_clock;

static u32 identity_of(u32 client)
{
    u32 i, j, pick = 0;

    for (i = 0; i < PEERS; i++)
        if (identities[i].client == client)
            return i;
    for (i = 0; i < PEERS; i++) {
        for (j = 0; j < PEERS && peers[j].client != identities[i].client; j++)
            ;
        if ((j == PEERS || !identities[i].client) && identities[i].used <= identities[pick].used)
            pick = i;
    }
    identities[pick].client = client;
    identities[pick].generation = identities[pick].have = 0;
    return pick;
}

/* Copy the two zero-terminated strings after an identity part's three-byte header. */
static int identity_pair(const struct event *e, char *first, char *second)
{
    char *out = first;
    u32 off = 3, n = 0, which;

    if (e->length < 5 || e->data[1] != IDENTITY_PARTS || e->data[0] >= IDENTITY_PARTS)
        return 0;
    first[0] = second[0] = 0;
    for (which = 0; which < 2; which++) {
        n = 0;
        while (off < e->length && e->data[off]) {
            if (e->data[off] < 0x20 || n >= IDENTITY_TEXT - 1)
                return 0;
            out[n++] = (char)e->data[off++];
        }
        if (off >= e->length || !n)
            return 0;
        out[n] = 0;
        off++;
        out = second;
    }
    return off == e->length;
}

static void identity_event(const struct event *e)
{
    char first[IDENTITY_TEXT], second[IDENTITY_TEXT];
    u32 slot;

    if (!e->origin || !identity_pair(e, first, second)) {
        identity_refused++;
        return;
    }
    slot = identity_of(e->origin);
    identities[slot].used = ++identity_clock;
    if (!e->data[0]) {
        identities[slot].have = 1;
        identities[slot].female = e->data[2] != 0;
        copy((u8 *)identities[slot].name, (const u8 *)first, IDENTITY_TEXT);
        copy((u8 *)identities[slot].race, (const u8 *)second, IDENTITY_TEXT);
        return;
    }
    if (!(identities[slot].have & 1) || identities[slot].female != (e->data[2] != 0)) {
        identity_refused++;
        return;
    }
    copy((u8 *)identities[slot].head, (const u8 *)first, IDENTITY_TEXT);
    copy((u8 *)identities[slot].hair, (const u8 *)second, IDENTITY_TEXT);
    identities[slot].have = 3;
    identities[slot].generation = identity_clock;
    identity_received++;
    tes3x_log_hex3("net.identity", e->origin, identities[slot].female,
                   identities[slot].generation);
    log_text("net.identity_name", identities[slot].name);
}

static u32 identity_put(u8 *part, u32 off, const char *text)
{
    u32 n;

    if (!mapped(text))
        return 0;
    for (n = 0; n < IDENTITY_TEXT - 1 && text[n]; n++)
        part[off + n] = (u8)text[n];
    if (!n)
        return 0;
    part[off + n] = 0;
    return off + n + 1;
}

/* Game thread: send a complete identity after each WELCOME and whenever chargen changes it. */
static void identity_send(const u8 *ref)
{
    static u32 last_check, sent_hash, sent_welcome;
    const u8 *instance, *npc, *race, *head, *hair;
    const char *fields[4];
    u8 parts[2][EVENT_DATA];
    u32 lengths[2], female, hash = 2166136261u, i, k, flags, room, now = now_us();

    if (ses.state != SESSION_JOINED || now - last_check < EQUIP_PERIOD_US)
        return;
    last_check = now;
    if (!plausible(instance = *(const u8 *const *)(ref + 0x28)) ||
        !plausible(npc = *(const u8 *const *)(instance + NPC_BASE)) ||
        !plausible(race = *(const u8 *const *)(npc + NPC_RACE)) ||
        !plausible(head = *(const u8 *const *)(npc + NPC_HEAD)) ||
        !plausible(hair = *(const u8 *const *)(npc + NPC_HAIR)))
        return;
    fields[0] = *(const char *const *)(npc + NPC_NAME);
    fields[1] = (const char *)race + RACE_ID;
    fields[2] = ((fn_object_id)(*(void *const *const *)head)[OBJECT_GET_ID / 4])(head);
    fields[3] = ((fn_object_id)(*(void *const *const *)hair)[OBJECT_GET_ID / 4])(hair);
    female = (*(const u32 *)(instance + NPC_FLAGS) & NPC_FEMALE) != 0;
    for (i = 0; i < 2; i++) {
        parts[i][0] = (u8)i;
        parts[i][1] = IDENTITY_PARTS;
        parts[i][2] = (u8)female;
        lengths[i] = identity_put(parts[i], 3, fields[2 * i]);
        if (!lengths[i] || !(lengths[i] = identity_put(parts[i], lengths[i], fields[2 * i + 1])))
            return;
        for (k = 0; k < lengths[i]; k++)
            hash = (hash ^ parts[i][k]) * 16777619u;
    }
    if (hash == sent_hash && sent_welcome == ses.welcomes)
        return;
    flags = lock();
    room = EVENTS_OUT - (rel.out_next - rel.out_first);
    unlock(flags);
    if (room < IDENTITY_PARTS)
        return;
    for (i = 0; i < IDENTITY_PARTS; i++)
        event_queue(EVENT_IDENTITY, parts[i], lengths[i]);
    sent_hash = hash;
    sent_welcome = ses.welcomes;
    identity_sent++;
    tes3x_log_hex3("net.identity_sent", female, hash, ses.welcomes);
}

static u8 *resolve_object(const char *id);
static u8 *find_record(u32 finder, const char *id);

/* Changing an NPC record does not replace a body already in the scene. Rebuild that reference's
 * visual branch as Reference::reloadAnimation does, then put its mobile back in the mob manager. */
static int ghost_model_rebuild(u8 *ref)
{
    u8 *node = *(u8 **)(ref + REF_NODE), *parent, *mobile, *world, *mobs;
    void **vtable;

    if (!plausible(node) || !plausible(parent = *(u8 **)(node + NODE_PARENT)) ||
        !plausible(vtable = *(void ***)ref))
        return 0;
    ((fn_reset_visual)vtable[OBJECT_RESET_VISUAL_NODE / 4])(ref, 0);
    vtable = *(void ***)ref;
    if (!plausible(vtable) ||
        !plausible(node = ((fn_scene_node)vtable[OBJECT_GET_SCENE_NODE / 4])(ref)) ||
        !plausible(vtable = *(void ***)parent))
        return 0;
    ((fn_attach_child)vtable[NODE_ATTACH_CHILD / 4])(parent, node, 1);
    ((fn_node_update)TES3X_NET_NODE_UPDATE)(parent, 0.0f, 0, 1);
    ((fn_node_call)TES3X_NET_NODE_UPDATE_EFFECTS)(node);
    ((fn_node_call)TES3X_NET_NODE_UPDATE_PROPERTIES)(node);
    ((fn_node_update)TES3X_NET_NODE_UPDATE)(node, 0.0f, 0, 1);

    mobile = ref_mobile(ref);
    world = *(u8 **)TES3X_NET_WORLD;
    if (plausible(mobile) && plausible(vtable = *(void ***)mobile))
        ((fn_mobile_simulation)vtable[MOBILE_ENTER_SIMULATION / 4])(mobile, 1);
    if (plausible(world) && plausible(mobs = *(u8 **)(world + 0x5C)))
        ((fn_ghost_add_mob)TES3X_NET_ADD_MOB)(mobs, ref);
    if (plausible(mobile))
        *(u32 *)(mobile + MOBILE_FLAGS) &= ~MOBILE_SIMULATED;
    return 1;
}

static void identity_apply(u32 i, u8 *ref)
{
    u8 *instance, *npc, *race, *head, *hair, *attachment, *manager = 0;
    u32 slot, guard, generation, reason = 0, *flags;

    for (slot = 0; slot < PEERS && identities[slot].client != ghosts[i].client; slot++)
        ;
    if (slot == PEERS || identities[slot].have != 3 ||
        !(generation = identities[slot].generation) || generation == ghosts[i].identity)
        return;
    if (!plausible(instance = *(u8 **)(ref + 0x28)))
        reason = 1;
    else if (!plausible(npc = *(u8 **)(instance + NPC_BASE)))
        reason = 2;
    else if (!plausible(race = find_record(TES3X_NET_FIND_RACE, identities[slot].race)))
        reason = 3;
    else if (!plausible(head = resolve_object(identities[slot].head)))
        reason = 4;
    else if (!plausible(hair = resolve_object(identities[slot].hair)))
        reason = 5;
    if (reason) {
        if (ghosts[i].identity_seen != generation) {
            ghosts[i].identity_seen = generation;
            tes3x_log_hex3("net.ghost_identity_bad", i + 1, ghosts[i].client, reason);
        }
        return;
    }
    attachment = *(u8 **)(ref + REF_ATTACHMENTS);
    for (guard = 0; plausible(attachment) && guard < 32;
         attachment = *(u8 **)(attachment + 4), guard++)
        if (*(u32 *)attachment == ATTACHMENT_BODY_PARTS) {
            manager = *(u8 **)(attachment + 8);
            break;
        }
    if (!plausible(manager)) {
        if (ghosts[i].identity_seen != generation) {
            ghosts[i].identity_seen = generation;
            tes3x_log_hex3("net.ghost_identity_bad", i + 1, ghosts[i].client, 6);
        }
        return;
    }
    *(char **)(npc + NPC_NAME) = identities[slot].name;
    *(u8 **)(npc + NPC_RACE) = race;
    *(u8 **)(npc + NPC_HEAD) = head;
    *(u8 **)(npc + NPC_HAIR) = hair;
    flags = (u32 *)(npc + NPC_FLAGS);
    *flags = (*flags & ~NPC_FEMALE) | (identities[slot].female ? NPC_FEMALE : 0);
    flags = (u32 *)(instance + NPC_FLAGS);
    *flags = (*flags & ~NPC_FEMALE) | (identities[slot].female ? NPC_FEMALE : 0);
    if (!ghost_model_rebuild(ref)) {
        ((fn_body_part_update)TES3X_NET_BODY_PART_UPDATE)(manager, ref);
        if (ghosts[i].identity_seen != generation) {
            ghosts[i].identity_seen = generation;
            tes3x_log_hex3("net.ghost_identity_bad", i + 1, ghosts[i].client, 7);
        }
        return;
    }
    ghosts[i].look = 0; /* the new branch starts in the plugin's default clothes */
    ghosts[i].identity = ghosts[i].identity_seen = generation;
    identities[slot].used = ++identity_clock;
    identity_applied++;
    tes3x_log_hex3("net.ghost_identity", i + 1, ghosts[i].client, ghosts[i].identity);
}

/* NPC equipment is authority-owned just like the actor pose. At most one authority actor is
 * inspected per frame; unchanged sets are reconsidered once a second. The server retains each
 * actor's latest complete set for followers and late joiners. */
#define ACTOR_LOOKS 64u
#define ACTOR_EQUIP_SENT 256u
#define ACTOR_EQUIP_PERIOD_US 1000000u
#define ACTOR_EQUIP_HEAD 6u /* refid, part, parts */

static struct {
    u32 refid, used, generation, count, staged, next_part;
    char ids[EQUIP_ITEMS][EQUIP_ID];
} actor_looks[ACTOR_LOOKS];
static struct {
    u32 refid, hash, next, used;
} actor_looks_sent[ACTOR_EQUIP_SENT];
static u32 actor_look_clock, actor_equip_budget;

static u32 actor_look_of(u32 refid)
{
    u32 i, pick = 0;

    for (i = 0; i < ACTOR_LOOKS; i++)
        if (actor_looks[i].refid == refid)
            return i;
    for (i = 0; i < ACTOR_LOOKS; i++)
        if (!actor_looks[i].refid || actor_looks[i].used <= actor_looks[pick].used)
            pick = i;
    actor_looks[pick].refid = refid;
    actor_looks[pick].generation = actor_looks[pick].count = actor_looks[pick].staged = 0;
    actor_looks[pick].next_part = 0;
    return pick;
}

static void actor_equipment_event(const struct event *e)
{
    u32 refid, slot, part, parts, off, k;

    if (e->length < ACTOR_EQUIP_HEAD || !(refid = get32le(e->data)) ||
        !(parts = e->data[5]) || (part = e->data[4]) >= parts) {
        actor_equip_refused++;
        return;
    }
    for (off = ACTOR_EQUIP_HEAD; off < e->length; off++)
        if ((e->data[off] && e->data[off] < 0x20) || e->data[off] == '"') {
            actor_equip_refused++;
            return;
        }
    slot = actor_look_of(refid);
    actor_looks[slot].used = ++actor_look_clock;
    if (!part) {
        actor_looks[slot].generation = 0;
        actor_looks[slot].staged = actor_looks[slot].next_part = 0;
    } else if (part != actor_looks[slot].next_part)
        return;
    actor_looks[slot].next_part++;
    off = ACTOR_EQUIP_HEAD;
    while (off < e->length && actor_looks[slot].staged < EQUIP_ITEMS) {
        for (k = 0; off + k < e->length && e->data[off + k] && k < EQUIP_ID - 1; k++)
            actor_looks[slot].ids[actor_looks[slot].staged][k] = (char)e->data[off + k];
        if (off + k >= e->length || e->data[off + k]) {
            actor_equip_refused++;
            return;
        }
        actor_looks[slot].ids[actor_looks[slot].staged++][k] = 0;
        off += k + 1;
    }
    if (off != e->length) {
        actor_equip_refused++;
        return;
    }
    if (actor_looks[slot].next_part != parts)
        return;
    actor_looks[slot].count = actor_looks[slot].staged;
    actor_looks[slot].generation = actor_look_clock;
    actor_equip_received++;
    tes3x_log_hex3("net.actor_equipment", refid, actor_looks[slot].count,
                   actor_looks[slot].generation);
}

static void actor_item(u8 *ref, const char *verb, const char *id, const char *tail)
{
    char line[96];
    char *p = put_text(put_text(line, verb), " \"");

    p = put_text(put_text(put_text(p, id), "\""), tail);
    *p = 0;
    run_script_on(line, ref);
}

static void actor_equipment_apply(u32 refid, u8 *ref, u32 *applied)
{
    char worn[EQUIP_ITEMS][EQUIP_ID];
    u32 slot, n, k, removed = 0, added = 0;

    for (slot = 0; slot < ACTOR_LOOKS && actor_looks[slot].refid != refid; slot++)
        ;
    if (slot == ACTOR_LOOKS || !actor_looks[slot].generation ||
        actor_looks[slot].generation == *applied)
        return;
    *applied = actor_looks[slot].generation;
    actor_looks[slot].used = ++actor_look_clock;
    n = equipment_ids(ref, worn);
    for (k = 0; k < n; k++)
        if (!has_id(actor_looks[slot].ids, actor_looks[slot].count, worn[k])) {
            actor_item(ref, "RemoveItem", worn[k], " 1");
            removed++;
        }
    for (k = 0; k < actor_looks[slot].count; k++)
        if (!has_id(worn, n, actor_looks[slot].ids[k])) {
            actor_item(ref, "Equip", actor_looks[slot].ids[k], "");
            added++;
        }
    actor_equip_applied++;
    tes3x_log_hex3("net.actor_equip_apply", refid, removed << 16 | added,
                   equipment_ids(ref, worn) << 16 | actor_looks[slot].count);
}

static void actor_equipment_begin_frame(void)
{
    actor_equip_budget = 1;
}

/* Return after inspecting one due actor, whether or not its set changed. */
static void actor_equipment_send(u8 *ref, u32 refid)
{
    char ids[EQUIP_ITEMS][EQUIP_ID];
    u8 parts[EQUIP_ITEMS][EVENT_DATA];
    u32 lengths[EQUIP_ITEMS], n, i, k, slot = ACTOR_EQUIP_SENT, pick = 0, count = 0;
    u32 hash = 2166136261u, flags, room, now = now_us();

    if (!actor_equip_budget || ses.state != SESSION_JOINED)
        return;
    for (i = 0; i < ACTOR_EQUIP_SENT; i++) {
        if (actor_looks_sent[i].refid == refid) {
            slot = i;
            break;
        }
        if (!actor_looks_sent[i].refid || actor_looks_sent[i].used <= actor_looks_sent[pick].used)
            pick = i;
    }
    if (slot == ACTOR_EQUIP_SENT)
        slot = pick;
    if (actor_looks_sent[slot].refid == refid &&
        (int)(actor_looks_sent[slot].next - now) > 0)
        return;
    actor_equip_budget = 0;
    if (actor_looks_sent[slot].refid != refid)
        actor_looks_sent[slot].hash = 0;
    actor_looks_sent[slot].refid = refid;
    actor_looks_sent[slot].used = ++actor_look_clock;
    actor_looks_sent[slot].next = now + ACTOR_EQUIP_PERIOD_US;
    n = equipment_ids(ref, ids);
    for (i = 0; i < n; i++) {
        for (k = 0; ids[i][k]; k++)
            hash = (hash ^ (u8)ids[i][k]) * 16777619u;
        hash *= 16777619u;
    }
    if (actor_looks_sent[slot].hash == hash)
        return;
    lengths[0] = ACTOR_EQUIP_HEAD;
    for (i = 0; i < n; i++) {
        for (k = 0; ids[i][k]; k++)
            ;
        if (lengths[count] + k + 1 > EVENT_DATA)
            lengths[++count] = ACTOR_EQUIP_HEAD;
        copy(parts[count] + lengths[count], (const u8 *)ids[i], k + 1);
        lengths[count] += k + 1;
    }
    count++;
    flags = lock();
    room = EVENTS_OUT - (rel.out_next - rel.out_first);
    unlock(flags);
    if (room < count) {
        actor_looks_sent[slot].next = now + 100000u;
        return;
    }
    for (i = 0; i < count; i++) {
        put32le(parts[i], refid);
        parts[i][4] = (u8)i;
        parts[i][5] = (u8)count;
        event_queue(EVENT_ACTOR_EQUIPMENT, parts[i], lengths[i]);
    }
    actor_looks_sent[slot].hash = hash;
    actor_equip_sent++;
    tes3x_log_hex3("net.actor_equip_sent", refid, n, hash);
}
