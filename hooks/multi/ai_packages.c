/* AI packages. AITravel, AIWander, AIEscort and AIFollow end in the actor's setAIPackage, which
 * keeps the config on the actor's instance and hands it to its planner. A package this console's
 * player gave (dialogue result or console) or a world script it runs gave goes out as
 * AI_PACKAGE; every console gives it to its copy of the actor, so whichever console runs the
 * actor later runs it too. A player as the target is named by client: the server sends "you" to
 * that player's console and the client to the others, whose ghost of it stands in. While that
 * player is away the actor waits where it stands (AIWander 0) and takes the package up again
 * when they come back. */
#define EVENT_AI_PACKAGE 40u /* refid, type, target, cell u16, client, config +4..+0x14, reset, id */
#define AI_HEAD 29u
#define AI_ID 32u
#define AI_PACKAGES 128u
#define AI_WANDER 2u
#define AI_ESCORT 3u
#define AI_FOLLOW 4u
#define AI_TARGET_NONE 0u
#define AI_TARGET_PLAYER 1u /* from a console: its player; to one: its own player */
#define AI_TARGET_CLIENT 2u /* that client's player: its ghost here */
#define AI_TARGET_OBJECT 3u /* an actor by id */
#define CONFIG_ACTOR 0x14
#define CONFIG_CELL 0x18
#define CONFIG_RESET 0x1C
#define ACTOR_GET_AI 0x180 /* Actor vtable: getAIPackageConfig() */
#define ACTOR_SET_AI 0x184 /* Actor vtable: setAIPackage(config, reference) */
#define GHOST_ID "tes3x_ghost"
#define FINITE_LIMIT 0x7F7FFFFFu /* AIFollow's default destination is FLT_MAX */

typedef void(__attribute__((thiscall)) *fn_set_ai)(void *actor, void *config, void *ref);
typedef u8 *(__attribute__((thiscall)) *fn_get_ai)(void *actor);

static struct {
    u32 refid;
    u8 data[AI_HEAD + AI_ID];
    u8 pending, waiting; /* waiting: given AIWander 0 while its target player is away */
    u8 early; /* a follow or escort given before READY or the actor's mobile: given again */
} ai_packages[AI_PACKAGES];
static u32 ai_count, ai_applying, packages_hooked, ai_signature, ai_looked;
static u32 ai_out, ai_in, ai_applied, ai_full;

static void ai_zero(u8 *d, u32 n)
{
    while (n--)
        *d++ = 0;
}

static int id_is(const char *id, const char *name)
{
    for (; *id && *name && (*id | 32) == (*name | 32); id++, name++)
        ;
    return !*name && !*id;
}

/* 1 and the client for a ghost's base (tes3x_ghostN), else 0. */
static int ghost_client(const char *id, u32 *client)
{
    const char *g = GHOST_ID;
    u32 slot = 0;

    for (; *g && *id == *g; id++, g++)
        ;
    if (*g || !*id)
        return 0;
    /* An instance's id is its base's with "00000000" after it. */
    for (; *id >= '0' && *id <= '9' && !(id[0] == '0' && id_is(id, "00000000")); id++)
        slot = slot * 10 + (u32)(*id - '0');
    if (id_is(id, "00000000"))
        id += 8;
    if (*id || !slot || slot > PEERS || !ghosts[slot - 1].client)
        return 0;
    *client = ghosts[slot - 1].client;
    return 1;
}

/* The table's entry for refid, made if need be; AI_PACKAGES when full. */
static u32 ai_entry(u32 refid)
{
    u32 i;

    for (i = 0; i < ai_count && ai_packages[i].refid != refid; i++)
        ;
    if (i == ai_count) {
        if (ai_count == AI_PACKAGES) {
            ai_full++;
            return AI_PACKAGES;
        }
        ai_count++;
        ai_packages[i].refid = refid;
        ai_packages[i].pending = ai_packages[i].waiting = ai_packages[i].early = 0;
        ai_zero(ai_packages[i].data, sizeof(ai_packages[i].data));
    }
    return i;
}

static int ai_same(const u8 *a, const u8 *b)
{
    u32 i;

    for (i = 0; i < AI_HEAD + AI_ID && a[i] == b[i]; i++)
        ;
    return i == AI_HEAD + AI_ID;
}

/* A script that gives the same package every frame sends it once. */
static void ai_send(const u8 *config, const u8 *ref)
{
    const u8 *player = player_reference(), *actor;
    u8 data[AI_HEAD + AI_ID];
    u32 type = *(const u32 *)config, refid = *(const u32 *)(ref + REF_ID), client = 0, n = AI_HEAD;
    u32 kind = AI_TARGET_NONE, cell = OBJECT_NO_CELL, i;
    const char *id;

    if (type < 1 || type > AI_FOLLOW)
        return;
    ai_zero(data, sizeof(data));
    if (type >= AI_ESCORT) {
        actor = *(const u8 *const *)(config + CONFIG_ACTOR);
        if (!plausible(actor) || !(id = object_id(actor)))
            return;
        if ((plausible(player) && actor == *(const u8 *const *)(player + REF_BASE)) ||
            id_is(id, "player"))
            kind = AI_TARGET_PLAYER;
        else if (ghost_client(id, &client))
            kind = AI_TARGET_CLIENT;
        else {
            kind = AI_TARGET_OBJECT;
            for (; *id && n < AI_HEAD + AI_ID - 1; id++)
                data[n++] = (u8)*id;
        }
        if (plausible(*(const u8 *const *)(config + CONFIG_CELL)))
            cell = cell_index(*(const u8 *const *)(config + CONFIG_CELL));
        data[28] = config[CONFIG_RESET];
    }
    put32le(data, refid);
    data[4] = (u8)type;
    data[5] = (u8)kind;
    data[6] = (u8)cell;
    data[7] = (u8)(cell >> 8);
    put32le(data + 8, client);
    copy(data + 12, config + 4, 13); /* the rest of the 16 is padding */
    if ((i = ai_entry(refid)) < AI_PACKAGES && ai_same(ai_packages[i].data, data))
        return;
    if (!event_queue(EVENT_AI_PACKAGE, data, n + 1)) {
        tes3x_log("net.event_full", EVENT_AI_PACKAGE);
        return;
    }
    if (i < AI_PACKAGES) {
        copy(ai_packages[i].data, data, sizeof(data));
        ai_packages[i].pending = 0;
    }
    ai_out++;
    tes3x_log_hex3("net.ai_sent", refid, type | kind << 8, client);
}

static void ai_seen(u8 *config, u8 *ref)
{
    const u8 *base;
    u32 tag;

    if (ai_applying || ses.state != SESSION_JOINED || player_mode != 3 || !plausible(config) ||
        !plausible(ref) || ref == player_reference() || is_ghost(ref) ||
        !*(const u32 *)(ref + REF_ID) || !plausible(base = *(const u8 *const *)(ref + REF_BASE)))
        return;
    tag = *(const u32 *)(base + 4);
    if ((tag == TAG_NPC || tag == TAG_CREA) && (player_acting || world_script_now()))
        ai_send(config, ref);
}

static void __attribute__((thiscall)) npc_ai_hook(void *actor, u8 *config, u8 *ref)
{
    ai_seen(config, ref);
    ((fn_set_ai)TES3X_NET_NPC_SET_AI)(actor, config, ref);
}

static void __attribute__((thiscall)) creature_ai_hook(void *actor, u8 *config, u8 *ref)
{
    ai_seen(config, ref);
    ((fn_set_ai)TES3X_NET_CREATURE_SET_AI)(actor, config, ref);
}

static void ai_packages_hook_install(void)
{
    if (packages_hooked)
        return;
    packages_hooked = 1 + (swap_slot((u32 *)TES3X_NET_NPC_SET_AI_SLOT, TES3X_NET_NPC_SET_AI,
                               (const void *)npc_ai_hook) &
                     swap_slot((u32 *)TES3X_NET_CREATURE_SET_AI_SLOT, TES3X_NET_CREATURE_SET_AI,
                               (const void *)creature_ai_hook));
}

/* A data-file reference with this refid in any cell's persistent lists, or a loaded temporary one. */
static u8 *ref_anywhere(u32 refid)
{
    const u8 *node = cells_head();
    u32 i;
    u8 *ref;

    for (i = 0; plausible(node) && i < OBJECT_NO_CELL; i++, node = *(const u8 *const *)(node + 8))
        if ((ref = move_reference(*(u8 *const *)node, refid)))
            return ref;
    return 0;
}

/* The actor a follow or escort names here: this player, a ghost or a record. 0 until known. */
static u8 *ai_target(const u8 *data)
{
    const u8 *ghost;
    u32 client = get32le(data + 8), g;

    if (data[5] == AI_TARGET_PLAYER) /* the record a script's "player" names */
        return resolve_object("player");
    if (data[5] == AI_TARGET_OBJECT)
        return resolve_object((const char *)data + AI_HEAD);
    for (g = 0; g < PEERS && ghosts[g].client != client; g++)
        ;
    if (g == PEERS || !client || !plausible(ghost = ghost_ref(g)))
        return 0;
    return *(u8 *const *)(ghost + REF_BASE);
}

static int peer_present(u32 client)
{
    u32 i;

    for (i = 0; i < PEERS; i++)
        if (client && peers[i].client == client)
            return 1;
    return 0;
}

/* Give the actor its package once it and its target can be found, or AIWander 0 while the player
 * it names is away; 1 when given. */
static int ai_try(u32 i)
{
    static const char file[] = "tes3xmulti.c";
    u8 wait[AI_HEAD + AI_ID];
    const u8 *data = ai_packages[i].data;
    u32 type, cell, away = data[5] == AI_TARGET_CLIENT && !peer_present(get32le(data + 8));
    u8 *ref = ref_anywhere(ai_packages[i].refid), *actor, *target = 0, *config;

    if (away) {
        ai_zero(wait, sizeof(wait));
        wait[4] = AI_WANDER;
        wait[24] = 1; /* reset, at config +0x10 */
        data = wait;
    }
    type = data[4];
    cell = data[6] | (u32)data[7] << 8;

    if (!ref || !plausible(actor = *(u8 **)(ref + REF_BASE)) ||
        (type >= AI_ESCORT && !plausible(target = ai_target(data))))
        return 0;
    config = ((fn_engine_allocate)TES3X_NET_ENGINE_ALLOCATE)((void *)TES3X_NET_HEAP,
                                                              type >= AI_ESCORT ? 0x20 : 0x14,
                                                              file, 0);
    if (!plausible(config))
        return 0;
    ai_zero(config, type >= AI_ESCORT ? 0x20 : 0x14);
    *(u32 *)config = type;
    copy(config + 4, data + 12, 16);
    /* Reset, so the package replaces what the actor runs here (its data-file AI after a load). */
    if (type >= AI_ESCORT) {
        *(u8 **)(config + CONFIG_ACTOR) = target;
        *(u8 **)(config + CONFIG_CELL) = cell == OBJECT_NO_CELL ? 0 : cell_at(cell);
        config[CONFIG_RESET] = 1;
    } else {
        config[0x10] = 1;
    }
    ai_applying = 1;
    ((fn_set_ai)vtable_slot(actor, ACTOR_SET_AI))(actor, config, ref);
    ai_applying = 0;
    ai_packages[i].waiting = (u8)away;
    /* A follow resolves its target's mobile when the planner builds it, which before READY
     * or before the actor is in the world finds none. */
    ai_packages[i].early = (u8)(type >= AI_ESCORT && (player_mode != 3 || !ref_mobile(ref)));
    ai_applied++;
    tes3x_log_hex3("net.ai_applied", ai_packages[i].refid, type | (u32)data[5] << 8,
                   get32le(data + 8));
    return 1;
}

static void ai_packages_event(const struct event *e)
{
    u32 refid, i;

    if (e->length <= AI_HEAD || e->length > AI_HEAD + AI_ID || e->data[e->length - 1] ||
        !(refid = get32le(e->data)) || e->data[4] < 1 || e->data[4] > AI_FOLLOW ||
        e->data[5] > AI_TARGET_OBJECT ||
        (e->data[4] != AI_WANDER && !float_within(e->data + 12, 3, FINITE_LIMIT)))
        return;
    if ((i = ai_entry(refid)) == AI_PACKAGES)
        return;
    ai_zero(ai_packages[i].data, sizeof(ai_packages[i].data));
    copy(ai_packages[i].data, e->data, e->length);
    ai_in++;
    ai_packages[i].pending = !ai_try(i);
}

/* Game thread, in the world, after a cell change and once a second: the packages not given yet,
 * and those whose target player has left or come back. */
static void ai_packages_frame(void)
{
    const u8 *handler = *(const u8 *const *)TES3X_NET_DATA_HANDLER;
    u32 signature = 0, i, now = now_us();
    u8 *ref;

    if (ses.state != SESSION_JOINED || !ai_count)
        return;
    if (plausible(handler))
        signature = *(const u32 *)(handler + 0xAC) ^ *(const u32 *)(handler + 0xA0) * 31 ^
                    *(const u32 *)(handler + 0xA4) * 1009;
    if (signature == ai_signature && now - ai_looked < 1000000u)
        return;
    ai_signature = signature;
    ai_looked = now;
    for (i = 0; i < ai_count; i++)
        if (ai_packages[i].pending ||
            (ai_packages[i].early && player_mode == 3 &&
             (ref = ref_anywhere(ai_packages[i].refid)) && ref_mobile(ref)) ||
            (ai_packages[i].data[5] == AI_TARGET_CLIENT &&
             ai_packages[i].waiting == peer_present(get32le(ai_packages[i].data + 8))))
            ai_packages[i].pending = !ai_try(i);
}

/* The server replays its packages before each READY. */
static void ai_packages_session(void)
{
    static u32 welcome;

    if (welcome == ses.welcomes)
        return;
    welcome = ses.welcomes;
    ai_count = 0;
    ai_signature = 0;
}

/* tes3xnet act LINE: a console line run as this player's own, as a dialogue result is. */
static void act_command(const char *text)
{
    player_acting++;
    run_script(text);
    player_acting--;
}

/* tes3xnet ai 0xREFID: the actor's package config type times ten, plus 1 for a target that is
 * this player and 2 for a ghost; -1 without one. */
static void ai_query(const char *text)
{
    u32 refid = 0, digit, client, i;
    const u8 *ref, *config, *target;
    u8 *actor;
    const char *id;
    int value = -1;

    if (text[0] != '0' || (text[1] | 32) != 'x')
        return;
    for (i = 2; text[i] && text[i] != ' '; i++) {
        digit = (u32)(text[i] | 32);
        refid = refid << 4 | (digit <= '9' ? digit - '0' : digit - 'a' + 10);
    }
    if ((ref = ref_anywhere(refid)) && plausible(actor = *(u8 *const *)(ref + REF_BASE)) &&
        plausible(config = ((fn_get_ai)vtable_slot(actor, ACTOR_GET_AI))(actor))) {
        value = (int)*(const u32 *)config * 10;
        if (*(const u32 *)config >= AI_ESCORT &&
            plausible(target = *(const u8 *const *)(config + CONFIG_ACTOR))) {
            if ((id = object_id(target)) && id_is(id, "player"))
                value += 1;
            else if (id && ghost_client(id, &client))
                value += 2;
        }
    }
    tes3x_log_hex3("net.ai", refid, (u32)value, 0);
#ifdef TES3X_CONSOLE
    tes3x_console_reply("%s >> %.2f", text, (double)value);
#endif
}

static void ai_packages_stat(void)
{
    u32 i, pending = 0;

    for (i = 0; i < ai_count; i++)
        pending += ai_packages[i].pending;
    tes3x_log_hex3("net.ai_packages", ai_out, ai_in, ai_applied);
    tes3x_log_hex3("net.ai_table", ai_count, pending, ai_full | packages_hooked << 16);
}
