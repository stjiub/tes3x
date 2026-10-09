/* References made at run time: an item dropped, or an object PlaceAtPC, PlaceItem or a script
 * placed. Every such path marks the new reference modified, so the same hook names it; it has no
 * refid, and the server gives it one. The maker sends it as SPAWN with a token in place of the id,
 * and the server sends it back with an id to every client, the maker too, which knows its own by
 * base object, cell and position. Another console makes the reference when its cell is loaded.
 * Removal (a pickup, Disable) goes to the server as REMOVE; the server keeps removed ones and sends
 * them as SPAWN marked removed, so a console that still has one, from its save or an unloaded
 * cell, removes it. Actors are left out: who runs them needs cell authority. */
#define EVENT_SPAWN 14u  /* id, cell index u16, count u16, position, orientation, item data,
                            base id */
#define EVENT_REMOVE 15u /* count, then ids */
#define SPAWN_BYTES 40u
#define SPAWN_REMOVED 0x8000u /* in the count */
#define SPAWN_DATA 0x4000u    /* in the count: it has item data, whose condition and charge follow */
#define SPAWN_LEVELED 0x2000u /* in the count: a leveled creature; its placeholder's refid follows */
#define SPAWN_SUMMON 0x1000u  /* in the count: a summon, run by the console that made it */
#define SPAWN_COUNT 0x0FFFu
#define SPAWN_ID 32u
#define SPAWNS 256u
#define SPAWN_NEAR 1.0f
#define REMOVES_PER_EVENT ((EVENT_DATA - 1) / 4)
#define ATTACH_ITEM_DATA 6u /* data: count, then +0xC condition, uses or time, +0x10 charge */
#define ITEM_CONDITION 0xC
#define ITEM_CHARGE 0x10
#define CELL_INTERIOR 1u
#define CELL_GRID_X 0x24
#define CELL_GRID_Y 0x28

typedef u8 *(__attribute__((thiscall)) *fn_create_reference)(void *records, void *object,
                                                              const float *position,
                                                              const float *orientation, int insert,
                                                              void *ref);
typedef void(__attribute__((thiscall)) *fn_cell_insert)(void *cell, void *ref);
typedef void *(__attribute__((thiscall)) *fn_cell_part)(void *cell);
typedef void(__attribute__((thiscall)) *fn_attach_scene)(void *handler, void *ref, void *node,
                                                         void *activators, int a4);
typedef u8 *(__cdecl *fn_item_data_new)(void *object);
typedef void(__attribute__((thiscall)) *fn_attach_item_data)(void *ref, void *data);
typedef void(__attribute__((thiscall)) *fn_update_lighting)(void *handler, void *ref);

/* An actor's entry is found by its reference, not its place: actors move. leveled is its
 * placeholder's refid; owner, for a summon, the client that runs it (else the cell's authority). A stale entry is an actor's from before the last WELCOME, kept until the
 * server's replay names it again so the reference is not lost. fresh: the server's creature for
 * the placeholder is a new one, to be made rather than taken from what is linked now. */
static struct spawn {
    u32 sid, token, cell, count, used, leveled, owner;
    float pos[3], rot[3];
    u32 condition, charge; /* raw: an int or a float by the item's type */
    char id[SPAWN_ID];
    u8 *base, *ref, *cell_ptr;
    u32 hold_until;
    u8 send, removed, applied, unresolved, misses, actor, stale, fresh, summon, held;
} spawns[SPAWNS];
static u32 spawns_welcome, spawns_sent, spawns_received, spawns_made, spawns_removed;
static u32 spawns_full, spawn_failures, spawns_logged, spawn_clock, spawns_gone;
static u32 spawns_watched, spawn_tokens;

static int spawn_near(const float *a, const float *b)
{
    u32 i;

    for (i = 0; i < 3; i++)
        if (a[i] - b[i] > SPAWN_NEAR || b[i] - a[i] > SPAWN_NEAR)
            return 0;
    return 1;
}

static int same_id(const char *a, const char *b)
{
    for (; *a && *a == *b; a++, b++)
        ;
    return *a == *b;
}

/* A reference made at run time that is not an actor or a projectile: its cell index, its stack
 * count (SPAWN_DATA with item data, whose condition and charge go to data) and whether it is gone. */
static int spawn_read(const u8 *ref, u32 *cell, u32 *count, u32 *gone, u32 *data)
{
    const u8 *base, *list, *att, *item;
    u32 tag;
    int n;

    if (!plausible(ref) || *(const u32 *)(ref + 4) != TAG_REFR || *(const u32 *)(ref + REF_ID) ||
        !plausible(base = *(const u8 *const *)(ref + REF_BASE)) || ref_mobile(ref))
        return 0;
    tag = *(const u32 *)(base + 4);
    if (tag == TAG_NPC || tag == TAG_CREA || !plausible(list = *(const u8 *const *)(ref + REF_LIST)) ||
        (*cell = cell_index(*(const u8 *const *)(list + 0xC))) == OBJECT_NO_CELL)
        return 0;
    *gone = (*(const u32 *)(ref + REF_FLAGS) & (REF_DELETED | REF_DISABLED)) != 0;
    *count = 1;
    for (att = *(const u8 *const *)(ref + REF_ATTACHMENTS); plausible(att);
         att = *(const u8 *const *)(att + 4))
        if (*(const u32 *)att == ATTACH_ITEM_DATA && plausible(item = *(const u8 *const *)(att + 8))) {
            n = *(const int *)item;
            *count = SPAWN_DATA | (n < 1 ? 1 : n > (int)SPAWN_COUNT ? SPAWN_COUNT : (u32)n);
            data[0] = *(const u32 *)(item + ITEM_CONDITION);
            data[1] = *(const u32 *)(item + ITEM_CHARGE);
            break;
        }
    return 1;
}

static struct spawn *spawn_by_sid(u32 sid)
{
    u32 i;

    for (i = 0; i < SPAWNS; i++)
        if (spawns[i].used && spawns[i].sid == sid)
            return &spawns[i];
    return 0;
}

/* A free entry, else the oldest stale or removed one whose removal is not still to be sent. */
static struct spawn *spawn_slot(void)
{
    struct spawn *pick = 0;
    u32 i;

    for (i = 0; i < SPAWNS; i++) {
        if (!spawns[i].used) {
            pick = &spawns[i];
            break;
        }
        if ((spawns[i].stale || (spawns[i].removed && !(spawns[i].sid && spawns[i].send))) &&
            (!pick || spawns[i].used < pick->used))
            pick = &spawns[i];
    }
    if (!pick) {
        spawns_full++;
        return 0;
    }
    pick->used = ++spawn_clock;
    pick->sid = pick->leveled = pick->owner = 0;
    pick->ref = pick->base = pick->cell_ptr = 0;
    pick->send = pick->removed = pick->applied = pick->unresolved = pick->misses = 0;
    pick->actor = pick->stale = pick->fresh = pick->summon = pick->held = 0;
    return pick;
}

static int spawn_is(const u8 *ref, const struct spawn *s);

/* The entry a reference here stands for: the one that last had it, else one of the same cell, base
 * object and place that has no other reference here. */
static struct spawn *spawn_match(u32 cell, const u8 *base, const float *pos, const u8 *ref)
{
    u32 i;

    for (i = 0; i < SPAWNS; i++)
        if (spawns[i].used && ref && spawns[i].ref == ref && spawn_is(ref, &spawns[i]))
            return &spawns[i];
    for (i = 0; i < SPAWNS; i++)
        if (spawns[i].used && spawns[i].cell == cell && spawns[i].base == base &&
            spawn_near(spawns[i].pos, pos) && !spawn_is(spawns[i].ref, &spawns[i]))
            return &spawns[i];
    return 0;
}

/* Whether another entry has ref. */
static int spawn_claimed(const u8 *ref, const struct spawn *s)
{
    u32 i;

    for (i = 0; i < SPAWNS; i++)
        if (spawns[i].used && &spawns[i] != s && spawns[i].ref == ref)
            return 1;
    return 0;
}

static u8 *resolve_object(const char *id)
{
    const u8 *handler = *(const u8 **)TES3X_NET_DATA_HANDLER;
    void *records;

    if (!plausible(handler) || !plausible(records = *(void **)handler))
        return 0;
    return ((fn_resolve_object)TES3X_NET_RESOLVE_OBJECT)(records, id);
}

/* Names a new spawn to the server apart from another made in the same place; the half from the
 * clock keeps a relaunch from reusing a token the server still holds. */
static u32 spawn_token(void)
{
    u32 token;

    if (!spawn_tokens)
        spawn_tokens = (now_us() & 0xFFFF) << 16 | 1;
    token = spawn_tokens;
    spawn_tokens = (spawn_tokens & 0xFFFF0000u) | ((spawn_tokens + 1) & 0xFFFF);
    return token;
}

static void actor_local(u8 *ref, u32 summon, u32 player);
static void spawn_hold(struct spawn *s, u32 player);

/* Game thread: a reference made or changed here that has no refid; player if a player's own
 * action made it. */
static void spawn_local(u8 *ref, u32 player)
{
    const u8 *base = *(const u8 *const *)(ref + REF_BASE);
    const char *id;
    struct spawn *s;
    u32 cell, count, gone, n, data[2] = {0, 0};

    if (plausible(base) && (*(const u32 *)(base + 4) == TAG_NPC ||
                            *(const u32 *)(base + 4) == TAG_CREA)) {
        actor_local(ref, 0, player);
        return;
    }
    if (!spawn_read(ref, &cell, &count, &gone, data))
        return;
    if ((s = spawn_match(cell, base, (const float *)(ref + REF_POSITION), ref))) {
        s->ref = ref;
        if (gone && !s->removed && !s->sid && s->send)
            s->used = 0; /* never sent: nobody else knows it */
        else if (gone && !s->removed)
            s->removed = s->applied = s->send = 1;
        return;
    }
    id = ((fn_object_id)(*(void *const *const *)base)[OBJECT_GET_ID / 4])(base);
    if (gone || !mapped(id) || !*id || !(s = spawn_slot()))
        return;
    for (n = 0; id[n] && n < SPAWN_ID - 1; n++)
        s->id[n] = id[n];
    s->id[n] = 0;
    s->cell = cell;
    s->count = count;
    s->condition = data[0];
    s->charge = data[1];
    copy((u8 *)s->pos, ref + REF_POSITION, 12);
    copy((u8 *)s->rot, ref + REF_ORIENTATION, 12);
    s->base = (u8 *)base;
    s->ref = ref;
    s->cell_ptr = *(u8 *const *)(*(const u8 *const *)(ref + REF_LIST) + 0xC);
    s->send = s->applied = 1;
    s->token = spawn_token();
    spawn_hold(s, player);
}

/* Whether ref is still the entry's reference. */
static int spawn_is(const u8 *ref, const struct spawn *s)
{
    return plausible(ref) && *(const u32 *)(ref + 4) == TAG_REFR && !*(const u32 *)(ref + REF_ID) &&
           *(u8 *const *)(ref + REF_BASE) == s->base &&
           !(*(const u32 *)(ref + REF_FLAGS) & REF_DELETED) &&
           spawn_near((const float *)(ref + REF_POSITION), s->pos);
}

static u8 *spawn_find(struct spawn *s, u8 *cell)
{
    static const u32 lists[2] = {0x2C, 0x3C};
    const u8 *temporary;
    u8 *ref;
    u32 i;

    if (spawn_is(s->ref, s))
        return s->ref;
    for (i = 0; i < 3; i++) {
        if (i < 2)
            ref = *(u8 **)(cell + lists[i] + LIST_HEAD);
        else if (plausible(temporary = *(const u8 *const *)(cell + CELL_TEMPORARY)))
            ref = *(u8 *const *)(temporary + 8 + LIST_HEAD);
        else
            break;
        for (; plausible(ref); ref = *(u8 **)(ref + REF_NEXT))
            if (spawn_is(ref, s) && !spawn_claimed(ref, s))
                return ref;
    }
    return 0;
}

/* The current interior, or an exterior cell of the player's 3x3. */
static int cell_active(const u8 *cell)
{
    const u8 *handler = *(const u8 *const *)TES3X_NET_DATA_HANDLER;
    int dx, dy;

    if (!plausible(cell) || !plausible(handler) ||
        !(*(const u32 *)(cell + CELL_FLAGS) & CELL_REFS_LOADED))
        return 0;
    if (*(const u32 *)(cell + CELL_FLAGS) & CELL_INTERIOR)
        return *(const u8 *const *)(handler + 0xAC) == cell;
    if (*(const u8 *const *)(handler + 0xAC))
        return 0;
    dx = *(const int *)(cell + CELL_GRID_X) - *(const int *)(handler + 0xA0);
    dy = *(const int *)(cell + CELL_GRID_Y) - *(const int *)(handler + 0xA4);
    return dx >= -1 && dx <= 1 && dy >= -1 && dy <= 1;
}

/* The reference as the leveled creature spawn makes one: made, put in its cell, given its stack
 * and attached to the cell's scene, as DropItem does. */
static u8 *spawn_make(struct spawn *s, u8 *cell)
{
    u8 *handler = *(u8 **)TES3X_NET_DATA_HANDLER, *ref, *data;

    object_applying = 1;
    ref = ((fn_create_reference)TES3X_NET_CREATE_REFERENCE)(*(void **)handler, s->base, s->pos,
                                                             s->rot, 0, 0);
    if (plausible(ref)) {
        ((fn_cell_insert)TES3X_NET_CELL_INSERT)(cell, ref);
        if ((s->count & SPAWN_DATA) &&
            plausible(data = ((fn_item_data_new)TES3X_NET_ITEM_DATA_NEW)(s->base))) {
            *(int *)data = (int)(s->count & SPAWN_COUNT);
            *(u32 *)(data + ITEM_CONDITION) = s->condition;
            *(u32 *)(data + ITEM_CHARGE) = s->charge;
            ((fn_attach_item_data)TES3X_NET_ATTACH_ITEM_DATA)(ref, data);
        }
        ((fn_attach_scene)TES3X_NET_ATTACH_SCENE)(
            handler, ref, ((fn_cell_part)TES3X_NET_CELL_NODE)(cell),
            ((fn_cell_part)TES3X_NET_CELL_ACTIVATORS)(cell), 0);
        ((fn_set_modified)TES3X_NET_REF_MODIFIED)(ref, 1);
        ((fn_update_lighting)TES3X_NET_UPDATE_LIGHTING)(handler, ref);
        spawns_made++;
    } else {
        ref = 0;
        spawn_failures++;
    }
    object_applying = 0;
    return ref;
}

/* As a pickup: disabled and deleted. */
static void spawn_remove(u8 *ref)
{
    object_applying = 1;
    run_script_on("Disable", ref);
    *(u32 *)(ref + REF_FLAGS) |= REF_DELETED;
    ((fn_set_modified)TES3X_NET_REF_MODIFIED)(ref, 1);
    object_applying = 0;
    spawns_removed++;
}

static const char *object_id(const u8 *object);

/* Leveled creatures. The engine rolls a placeholder's list when the placeholder enters the scene
 * with no creature linked to it (LeveledCreature's spawn, through its vtable), makes the creature
 * and links the two both ways. While joined only the placeholder cell's authority rolls: another
 * console runs the spawn with the list's chance of none at 100, so it links nothing, when the
 * server already has a creature for the placeholder or another client runs the cell. Four times a
 * second each console looks at the placeholders of its active cells: one the server has a
 * creature for gets that creature (the one linked, if it is the same object, else a new one); the
 * authority sends what it rolled, or what its save linked, as a SPAWN that names the placeholder.
 * The server keeps the first creature for a placeholder, answers a later roll with it, and takes
 * a new one only once the old one is dead. */
#define TAG_LEVC 0x4356454Cu
#define LEVELED_CHANCE_NONE 0x40 /* signed byte, percent */
#define WORLD_MOBS 0x5C
#define ACTOR_INSTANCE_BASE 0x6C
#define CELL_NAME_PTR 0x14
#define LEVELED_NONE 32u
#define LEVELED_TRIES 3u
#define STALE_US 5000000u /* after WELCOME: long enough for the server's replay */

typedef u8 *(__attribute__((thiscall)) *fn_leveled_spawn)(void *list, void *placeholder);
typedef u8 *(__attribute__((thiscall)) *fn_leveled_resolve)(void *list);
typedef void(__attribute__((thiscall)) *fn_link)(void *ref, void *other);
typedef void(__attribute__((thiscall)) *fn_add_mob)(void *mobs, void *ref);

static u32 leveled_hooked, leveled_withheld, leveled_rolled, leveled_made, leveled_adopted;
static u32 leveled_replaced, actor_failures, spawns_welcomed;
static u32 leveled_none[LEVELED_NONE], leveled_none_count, leveled_none_epoch;

static struct spawn *spawn_leveled(u32 placeholder)
{
    u32 i;

    for (i = 0; i < SPAWNS; i++)
        if (spawns[i].used && spawns[i].leveled == placeholder)
            return &spawns[i];
    return 0;
}

/* The object an actor was made from. A mobile gives its reference a copy of the object, named
 * with a number after the object's id, which keeps the object at +0x6C as a container instance
 * does. */
static u8 *actor_base(u8 *object)
{
    u8 *base;
    const char *a, *b;

    if (!plausible(object) || !plausible(base = *(u8 **)(object + ACTOR_INSTANCE_BASE)) ||
        base == object || *(const u32 *)(base + 4) != *(const u32 *)(object + 4) ||
        !(a = object_id(object)) || !(b = object_id(base)))
        return object;
    for (; *b && *a == *b; a++, b++)
        ;
    return *b ? object : base;
}

/* Whether ref is still an actor entry's reference: its object, and not deleted. */
static int actor_is(const u8 *ref, const struct spawn *s)
{
    return plausible(ref) && *(const u32 *)(ref + 4) == TAG_REFR && !*(const u32 *)(ref + REF_ID) &&
           actor_base(*(u8 *const *)(ref + REF_BASE)) == s->base &&
           !(*(const u32 *)(ref + REF_FLAGS) & REF_DELETED);
}

/* An actor as the other consoles name it: its refid, or the server's id for one made at run time;
 * 0 if it has neither. */
static u32 actor_id(const u8 *ref)
{
    u32 i, refid;

    if (!plausible(ref))
        return 0;
    if ((refid = *(const u32 *)(ref + REF_ID)))
        return refid;
    for (i = 0; i < SPAWNS; i++)
        if (spawns[i].used && spawns[i].actor && spawns[i].sid && !spawns[i].stale &&
            !spawns[i].removed && spawns[i].ref == ref)
            return spawns[i].sid;
    return 0;
}

static void cell_key_of(const u8 *cell, struct cell_key *k)
{
    const char *name = 0;
    u32 i;

    k->kind = *(const u32 *)(cell + CELL_FLAGS) & CELL_INTERIOR ? KEY_INTERIOR : KEY_EXTERIOR;
    k->gx = k->kind == KEY_EXTERIOR ? *(const int *)(cell + CELL_GRID_X) : 0;
    k->gy = k->kind == KEY_EXTERIOR ? *(const int *)(cell + CELL_GRID_Y) : 0;
    if (k->kind == KEY_INTERIOR)
        name = *(const char *const *)(cell + CELL_NAME_PTR);
    for (i = 0; i < CELL_NAME; i++)
        k->name[i] = 0;
    for (i = 0; mapped(name) && name[i] && i < CELL_NAME - 1; i++)
        k->name[i] = (u8)name[i];
}

/* An interior's whole name from STATE's, which keeps CELL_NAME - 1 characters: the first interior
 * that begins with them. A name that was not cut is returned as it is. */
static const char *interior_name(const u8 *cut)
{
    const u8 *node, *cell;
    const char *name;
    u32 i, guard;

    for (i = 0; i < CELL_NAME - 1 && cut[i]; i++)
        ;
    if (i < CELL_NAME - 1)
        return (const char *)cut;
    for (node = cells_head(), guard = 0; plausible(node) && guard < 65536;
         node = *(const u8 *const *)(node + 8), guard++) {
        if (!plausible(cell = *(const u8 *const *)node) ||
            !(*(const u32 *)(cell + CELL_FLAGS) & CELL_INTERIOR) ||
            !mapped(name = *(const char *const *)(cell + CELL_NAME_PTR)))
            continue;
        for (i = 0; i < CELL_NAME - 1 && name[i] == (char)cut[i]; i++)
            ;
        if (i == CELL_NAME - 1)
            return name;
    }
    return (const char *)cut;
}

static u32 cell_authority(const u8 *cell)
{
    struct cell_key k;

    if (!plausible(cell))
        return 0;
    cell_key_of(cell, &k);
    return authority_of(&k);
}

static u8 *leveled_linked(const u8 *placeholder)
{
    u8 *linked = ((fn_ref_part)TES3X_NET_LEVELED_LINKED)(placeholder);
    return plausible(linked) ? linked : 0;
}

/* Whether this console leaves the placeholder's roll to another. */
static int leveled_withhold(const u8 *placeholder)
{
    const u8 *list;
    u32 refid, owner;

    if (ses.state != SESSION_JOINED || !(refid = *(const u32 *)(placeholder + REF_ID)) ||
        leveled_linked(placeholder))
        return 0;
    if (spawn_leveled(refid))
        return 1;
    list = *(const u8 *const *)(placeholder + REF_LIST);
    owner = plausible(list) ? cell_authority(*(const u8 *const *)(list + 0xC)) : 0;
    return owner && owner != ses.client;
}

static u8 *__attribute__((thiscall)) leveled_spawn_hook(u8 *list, u8 *placeholder)
{
    u8 *node;
    char chance;

    if (!plausible(placeholder) || !leveled_withhold(placeholder))
        return ((fn_leveled_spawn)TES3X_NET_LEVELED_SPAWN)(list, placeholder);
    chance = (char)list[LEVELED_CHANCE_NONE];
    list[LEVELED_CHANCE_NONE] = 100;
    node = ((fn_leveled_spawn)TES3X_NET_LEVELED_SPAWN)(list, placeholder);
    list[LEVELED_CHANCE_NONE] = (u8)chance;
    leveled_withheld++;
    return node;
}

/* Points a vtable slot that holds expected at hook; 0 if it holds something else. */
static int swap_slot(u32 *slot, u32 expected, const void *hook)
{
    u32 cr0, flags;

    if (*slot != expected) {
        tes3x_log_hex3("net.slot_unexpected", (u32)slot, *slot, expected);
        return 0;
    }
    flags = lock();
    __asm__ volatile("movl %%cr0, %0" : "=r"(cr0));
    __asm__ volatile("movl %0, %%cr0" : : "r"(cr0 & ~CR0_WP) : "memory");
    *slot = (u32)hook;
    __asm__ volatile("movl %0, %%cr0" : : "r"(cr0) : "memory");
    unlock(flags);
    return 1;
}

static void leveled_hook_install(void)
{
    if (leveled_hooked)
        return;
    leveled_hooked = 1 + swap_slot((u32 *)TES3X_NET_LEVELED_SPAWN_SLOT, TES3X_NET_LEVELED_SPAWN,
                                   (const void *)leveled_spawn_hook);
}

/* An actor made as the leveled spawn makes one, linked to its placeholder when it has one, then
 * given its mobile as PlaceAtPC does, since no cell load will. */
static u8 *actor_make(struct spawn *s, u8 *cell, u8 *placeholder)
{
    u8 *handler = *(u8 **)TES3X_NET_DATA_HANDLER, *world = *(u8 **)TES3X_NET_WORLD, *mobs, *ref;
    u8 *mobile;

    if (!plausible(world) || !plausible(mobs = *(u8 **)(world + WORLD_MOBS)) ||
        !plausible(handler)) {
        actor_failures++;
        return 0;
    }
    object_applying = 1;
    ref = ((fn_create_reference)TES3X_NET_CREATE_REFERENCE)(*(void **)handler, s->base, s->pos,
                                                             s->rot, 0, 0);
    if (plausible(ref)) {
        ((fn_cell_insert)TES3X_NET_CELL_INSERT)(cell, ref);
        ((fn_attach_scene)TES3X_NET_ATTACH_SCENE)(
            handler, ref, ((fn_cell_part)TES3X_NET_CELL_NODE)(cell),
            ((fn_cell_part)TES3X_NET_CELL_ACTIVATORS)(cell), 0);
        if (placeholder) {
            ((fn_link)TES3X_NET_LEVELED_LINK)(ref, placeholder);
            ((fn_link)TES3X_NET_LEVELED_LINK)(placeholder, ref);
            ((fn_set_modified)TES3X_NET_REF_MODIFIED)(placeholder, 1);
        }
        ((fn_set_modified)TES3X_NET_REF_MODIFIED)(ref, 1);
        ((fn_add_mob)TES3X_NET_ADD_MOB)(mobs, ref);
        /* Into the simulation or out of it by its distance, as PlaceAtPC does after addMob. */
        if (plausible(mobile = ref_mobile(ref)))
            ((fn_mobile_call)TES3X_NET_SIMULATE)(mobile);
    } else {
        ref = 0;
        actor_failures++;
    }
    object_applying = 0;
    return ref;
}

static void spawn_name(struct spawn *s, const u8 *object)
{
    const char *id = object_id(object);
    u32 n;

    for (n = 0; id && id[n] && n < SPAWN_ID - 1; n++)
        s->id[n] = id[n];
    s->id[n] = 0;
}

static int leveled_none_has(u32 refid)
{
    u32 i;

    for (i = 0; i < leveled_none_count; i++)
        if (leveled_none[i] == refid)
            return 1;
    return 0;
}

/* The scan's look at one placeholder of an active cell. */
static void leveled_seen(u8 *placeholder, u32 index, u8 *cell)
{
    u32 refid = *(const u32 *)(placeholder + REF_ID);
    u8 *linked = leveled_linked(placeholder), *object;
    struct spawn *s = spawn_leveled(refid);
    const u8 *from;

    if (!refid || (s && s->stale))
        return;
    if (s && s->sid) {
        if (linked && actor_is(linked, s) && (linked == s->ref || !s->fresh)) {
            if (linked != s->ref) {
                leveled_adopted++;
                tes3x_log_hex3("net.leveled_adopted", s->sid, refid, (u32)linked);
            }
            s->ref = linked;
            s->applied = 1;
            s->fresh = 0;
            return;
        }
        if ((s->applied && actor_is(s->ref, s)) || s->unresolved || s->misses >= LEVELED_TRIES)
            return;
        /* Disabled, not deleted: the AI may still hold a deleted actor's mobile. */
        if (linked) {
            object_applying = 1;
            run_script_on("Disable", linked);
            object_applying = 0;
            leveled_replaced++;
        }
        s->misses++;
        if ((s->ref = actor_make(s, cell, placeholder))) {
            s->applied = 1;
            s->fresh = 0;
            leveled_made++;
        }
        tes3x_log_hex3("net.leveled_made", s->sid, refid, (u32)s->ref);
        return;
    }
    if (s || cell_authority(cell) != ses.client || (!linked && leveled_none_has(refid)))
        return;
    if (!linked) {
        object = ((fn_leveled_resolve)TES3X_NET_LEVELED_RESOLVE)(*(u8 **)(placeholder + REF_BASE));
        if (!plausible(object)) {
            if (leveled_none_count < LEVELED_NONE)
                leveled_none[leveled_none_count++] = refid;
            return;
        }
    } else {
        object = actor_base(*(u8 **)(linked + REF_BASE));
    }
    if (!(s = spawn_slot()))
        return;
    from = linked ? linked : placeholder;
    s->leveled = refid;
    s->actor = 1;
    s->base = object;
    s->cell = index;
    s->count = 1;
    s->condition = s->charge = 0;
    copy((u8 *)s->pos, from + REF_POSITION, 12);
    copy((u8 *)s->rot, from + REF_ORIENTATION, 12);
    spawn_name(s, object);
    if (!linked) {
        if (!(linked = actor_make(s, cell, placeholder))) {
            s->used = 0;
            return;
        }
        leveled_rolled++;
    }
    s->ref = linked;
    s->cell_ptr = cell;
    s->send = s->applied = 1;
    s->token = spawn_token();
    tes3x_log_hex3("net.leveled_rolled", refid, (u32)linked, index);
    log_text("net.leveled_id", s->id);
}

/* The server's creature for a placeholder, or its removal once a new one replaced it. */
static void leveled_event(u32 sid, u32 leveled, u32 cell, u32 count, const u8 *p, const char *id)
{
    struct spawn *s = spawn_by_sid(sid);

    if (count & SPAWN_REMOVED) {
        if (s)
            s->used = 0;
        return;
    }
    if (!s)
        s = spawn_leveled(leveled);
    if (!s && !(s = spawn_slot()))
        return;
    if ((s->sid && s->sid != sid) || (s->used && s->base && !same_id(s->id, id)))
        s->fresh = 1; /* a respawn, or another console's roll in place of ours */
    if (!s->base || !same_id(s->id, id)) {
        copy((u8 *)s->id, (const u8 *)id, SPAWN_ID);
        s->unresolved = !(s->base = resolve_object(id));
    }
    if (s->fresh)
        s->applied = s->misses = 0;
    s->sid = sid;
    s->leveled = leveled;
    s->actor = 1;
    s->stale = s->send = s->removed = 0;
    s->cell = cell;
    s->cell_ptr = 0;
    s->count = count & SPAWN_COUNT;
    copy((u8 *)s->pos, p + 8, 24);
    if (spawns_logged < 32) {
        spawns_logged++;
        log_text("net.leveled_spawn_id", id);
        tes3x_log_hex3("net.leveled_spawn", sid, leveled, s->fresh);
    }
}

/* Actors made at run time other than leveled creatures: what PlaceAtPC, PlaceAtMe, a script or a
 * summon makes. The maker sends it as SPAWN; the others make it when its cell is active, or take
 * the one of the same object in that cell after a relaunch. The cell's authority runs it, and a
 * summon its maker, whose copy's end (disabled or deleted, as a summon's end and Disable leave
 * it) goes out as REMOVE. */
typedef void(__cdecl *fn_summon)(void *instance, void *data, int index, const char *id);

static const u32 summon_sites[] = TES3X_NET_SUMMON_SITES;
static u32 summon_hooked, summons_sent, actors_made, actors_removed;

static int actor_object(const u8 *object)
{
    return plausible(object) &&
           (*(const u32 *)(object + 4) == TAG_NPC || *(const u32 *)(object + 4) == TAG_CREA);
}

/* Scripts run on every console that has their reference loaded, and global ones everywhere, so a
 * spawn a script made here goes out at once only from the console that runs its cell, or for a
 * cell nobody runs the lowest client. The others hold theirs HOLD_US for that console's copy,
 * which then takes theirs over, and send it themselves if none comes: a script may run here only.
 * A player's own action (the console, a dialogue result, a drop) goes out at once. */
#define HOLD_US 5000000u
#define HOLD_NEAR 512.0f

typedef u32(__attribute__((thiscall)) *fn_drop)(void *mobile, void *item, void *data, int count,
                                                 int flag);

static const u32 player_script_sites[] = TES3X_NET_PLAYER_SCRIPT_SITES;
static const u32 player_drop_sites[] = TES3X_NET_PLAYER_DROP_SITES;
static u32 player_hooked, spawns_held, spawns_taken_over, spawns_released;

static int lowest_client(void)
{
    u32 i;

    for (i = 0; i < PEERS; i++)
        if (peers[i].client && peers[i].client < ses.client)
            return 0;
    return 1;
}

static void spawn_hold(struct spawn *s, u32 player)
{
    u32 owner;

    if (player)
        return;
    owner = cell_authority(s->cell_ptr);
    if (owner ? owner == ses.client : lowest_client())
        return;
    s->held = 1;
    s->hold_until = now_us() + HOLD_US;
    spawns_held++;
}

/* A held spawn of ours that an incoming one of the same object in the same cell stands for. */
static int spawn_takes_over(const struct spawn *s, u32 cell, const char *id, const float *pos)
{
    const float *at = s->pos;
    float dx = at[0] - pos[0], dy = at[1] - pos[1];

    return s->used && s->held && !s->sid && s->cell == cell && same_id(s->id, id) &&
           dx * dx + dy * dy <= HOLD_NEAR * HOLD_NEAR;
}

static int __attribute__((thiscall)) player_script_hook(void *self, void *scratch, const char *text,
                                                       int a2, int ref, int a4, int a5, int a6)
{
    int r;

    player_acting++;
    r = ((fn_compile_run)TES3X_NET_COMPILE_RUN)(self, scratch, text, a2, ref, a4, a5, a6);
    player_acting--;
    return r;
}

static u32 __attribute__((thiscall)) player_drop_hook(void *mobile, void *item, void *data,
                                                      int count, int flag)
{
    u32 r;

    player_acting++;
    r = ((fn_drop)TES3X_NET_DROP_ITEM)(mobile, item, data, count, flag);
    player_acting--;
    return r;
}

static void player_hook_install(void)
{
    if (player_hooked)
        return;
    player_hooked = 1;
    if (redirect_calls(player_script_sites,
                       sizeof(player_script_sites) / sizeof(player_script_sites[0]),
                       TES3X_NET_COMPILE_RUN, (const void *)player_script_hook))
        player_hooked |= 2;
    if (redirect_calls(player_drop_sites, sizeof(player_drop_sites) / sizeof(player_drop_sites[0]),
                       TES3X_NET_DROP_ITEM, (const void *)player_drop_hook))
        player_hooked |= 4;
}

static u32 actor_owner(u32 id, u32 cell_owner)
{
    struct spawn *s;
    u32 i;

    if ((id & 0xFF000000u) == 0xFF000000u && (s = spawn_by_sid(id)) && s->owner)
        return s->owner;
    for (i = 0; i < owner_count; i++)
        if (owners[i].id == id)
            return owners[i].client;
    return cell_owner;
}

/* Game thread: an actor made here with no refid, not linked to a leveled placeholder. */
static void actor_local(u8 *ref, u32 summon, u32 player)
{
    const u8 *list;
    u8 *object;
    struct spawn *s;
    u32 i, cell;

    if (ses.state != SESSION_JOINED || !plausible(ref) || *(const u32 *)(ref + 4) != TAG_REFR ||
        *(const u32 *)(ref + REF_ID) || is_ghost(ref) || leveled_linked(ref) ||
        (*(const u32 *)(ref + REF_FLAGS) & (REF_DELETED | REF_DISABLED)) ||
        !actor_object(object = actor_base(*(u8 **)(ref + REF_BASE))) ||
        !plausible(list = *(const u8 *const *)(ref + REF_LIST)) ||
        (cell = cell_index(*(const u8 *const *)(list + 0xC))) == OBJECT_NO_CELL)
        return;
    for (i = 0; i < SPAWNS; i++)
        if (spawns[i].used && spawns[i].actor && spawns[i].ref == ref)
            return;
    if (!(s = spawn_slot()))
        return;
    s->actor = 1;
    s->summon = (u8)summon;
    s->owner = summon ? ses.client : 0;
    s->base = object;
    s->ref = ref;
    s->cell = cell;
    s->cell_ptr = *(u8 *const *)(list + 0xC);
    s->count = 1;
    s->condition = s->charge = 0;
    copy((u8 *)s->pos, ref + REF_POSITION, 12);
    copy((u8 *)s->rot, ref + REF_ORIENTATION, 12);
    spawn_name(s, object);
    s->send = s->applied = 1;
    s->token = spawn_token();
    summons_sent += summon;
    spawn_hold(s, summon || player);
    log_text("net.actor_spawn_sent", s->id);
    tes3x_log_hex3("net.actor_spawn_at", cell, summon | s->held << 1, (u32)ref);
}

/* The creature is the actor the call marks modified, as every run-time creation does. */
static void __cdecl summon_hook(u8 *instance, void *data, int index, const char *id)
{
    const u8 *target = plausible(data) ? *(const u8 *const *)data : 0;
    u32 own, i, before = objects_dirty_count;

    ((fn_summon)TES3X_NET_SUMMON)(instance, data, index, id);
    if (ses.state != SESSION_JOINED || !plausible(target) || ref_owner(target, &own))
        return;
    for (i = before; i < objects_dirty_count; i++)
        actor_local(objects_dirty[i], 1, 1);
}

static void summon_hook_install(void)
{
    if (summon_hooked)
        return;
    summon_hooked = 1 + redirect_calls(summon_sites, sizeof(summon_sites) / sizeof(summon_sites[0]),
                                       TES3X_NET_SUMMON, (const void *)summon_hook);
}

/* One of a cell's actors made at run time with the entry's object that no entry has. */
static u8 *actor_find(struct spawn *s, u8 *cell)
{
    static const u32 lists[2] = {0x2C, 0x3C};
    const u8 *temporary;
    u8 *ref;
    u32 i, k;

    for (i = 0; i < 3; i++) {
        if (i < 2)
            ref = *(u8 **)(cell + lists[i] + LIST_HEAD);
        else if (plausible(temporary = *(const u8 *const *)(cell + CELL_TEMPORARY)))
            ref = *(u8 *const *)(temporary + 8 + LIST_HEAD);
        else
            break;
        for (; plausible(ref); ref = *(u8 **)(ref + REF_NEXT)) {
            if (!actor_is(ref, s) || (*(const u32 *)(ref + REF_FLAGS) & REF_DISABLED) ||
                leveled_linked(ref))
                continue;
            for (k = 0; k < SPAWNS && !(spawns[k].used && spawns[k].ref == ref); k++)
                ;
            if (k == SPAWNS)
                return ref;
        }
    }
    return 0;
}

static void actor_disable(u8 *ref)
{
    object_applying = 1;
    run_script_on("Disable", ref);
    object_applying = 0;
    actors_removed++;
}

/* Game thread: the server's actor, made here once its cell is active. */
static void actor_apply(struct spawn *s)
{
    u8 *cell, *ref;

    if (!s->used || !s->sid || s->stale || s->applied || s->unresolved)
        return;
    if (!s->cell_ptr)
        s->cell_ptr = cell_at(s->cell);
    if (!cell_active(cell = s->cell_ptr))
        return;
    if (s->removed) {
        if (actor_is(s->ref, s))
            actor_disable(s->ref);
        s->used = 0;
        return;
    }
    if (!actor_is(s->ref, s) && !(s->ref = actor_find(s, cell))) {
        if (s->misses >= LEVELED_TRIES)
            return;
        s->misses++;
        if ((s->ref = actor_make(s, cell, 0)))
            actors_made++;
        tes3x_log_hex3("net.actor_made", s->sid, (u32)s->ref, s->owner);
    }
    s->applied = s->ref != 0;
}

/* Every SPAWN_WATCH_US: an actor this console runs that is gone goes out as REMOVE. */
static void actor_watch(struct spawn *s)
{
    const u8 *ref = s->ref;
    u32 runs;

    if (!s->sid || s->stale || !s->applied)
        return;
    runs = actor_owner(s->sid, cell_authority(s->cell_ptr)) == ses.client;
    if (!runs || (plausible(ref) && *(const u32 *)(ref + 4) == TAG_REFR &&
                  !(*(const u32 *)(ref + REF_FLAGS) & (REF_DELETED | REF_DISABLED))))
        return;
    s->removed = s->send = 1;
    tes3x_log_hex3("net.actor_gone", s->sid, s->owner, (u32)ref);
}

static void actor_event(u32 sid, u32 cell, u32 count, const u8 *p, const char *id, u32 origin)
{
    struct spawn *s = spawn_by_sid(sid);
    float pos[3];
    u32 i;

    copy((u8 *)pos, p + 8, 12);
    for (i = 0; i < SPAWNS && !s; i++) /* our own, back with its id, or held for this one */
        if ((spawns[i].used && spawns[i].actor && !spawns[i].sid && !spawns[i].leveled &&
             spawns[i].cell == cell && same_id(spawns[i].id, id) &&
             spawn_near(spawns[i].pos, pos)) ||
            (spawns[i].actor && spawn_takes_over(&spawns[i], cell, id, pos)))
            s = &spawns[i];
    if (s && s->held) {
        s->held = 0;
        spawns_taken_over++;
        tes3x_log_hex3("net.spawn_taken_over", sid, origin, (u32)s->ref);
    }
    if (count & SPAWN_REMOVED) {
        if (s && s->sid) {
            s->removed = 1;
            s->applied = 0;
        } else if (s) {
            s->used = 0;
        }
        return;
    }
    if (!s) {
        if (!(s = spawn_slot()))
            return;
        s->actor = 1;
        s->cell = cell;
        copy((u8 *)s->pos, p + 8, 24);
        copy((u8 *)s->id, (const u8 *)id, SPAWN_ID);
        s->unresolved = !(s->base = resolve_object(id));
    }
    s->sid = sid;
    s->stale = s->send = 0;
    s->summon = (count & SPAWN_SUMMON) != 0;
    s->owner = s->summon ? origin : 0;
    s->count = count & SPAWN_COUNT;
    spawns_pass = 1;
    if (spawns_logged < 32) {
        spawns_logged++;
        log_text("net.actor_spawn_id", id);
        tes3x_log_hex3("net.actor_spawn", sid, origin, count);
    }
}

static void spawn_event(const struct event *e)
{
    const u8 *p = e->data;
    struct spawn *s;
    char id[SPAWN_ID];
    u32 sid, cell, count, n, i, head = SPAWN_BYTES, leveled = 0;
    float pos[3];

    if (e->length < SPAWN_BYTES + 2)
        return;
    sid = get32le(p);
    cell = p[4] | (u32)p[5] << 8;
    count = p[6] | (u32)p[7] << 8;
    if (!float_within(p + 8, 3, POSITION_LIMIT) || !float_within(p + 20, 3, ANGLE_LIMIT)) {
        refused_events++;
        return;
    }
    copy((u8 *)pos, p + 8, 12);
    if (count & SPAWN_LEVELED) {
        if (e->length < SPAWN_BYTES + 6)
            return;
        leveled = get32le(p + SPAWN_BYTES);
        head += 4;
    }
    for (n = 0; n < SPAWN_ID - 1 && head + n < e->length && p[head + n]; n++)
        id[n] = (char)p[head + n];
    id[n] = 0;
    spawns_received++;
    if (!sid)
        return;
    if (leveled) {
        leveled_event(sid, leveled, cell, count, p, id);
        return;
    }
    if ((count & SPAWN_SUMMON) || actor_object(resolve_object(id))) {
        actor_event(sid, cell, count, p, id, e->origin);
        return;
    }
    if (!(s = spawn_by_sid(sid)))
        for (i = 0; i < SPAWNS && !s; i++) /* our own, back with its id, or held for this one */
            if ((spawns[i].used && !spawns[i].sid && spawns[i].cell == cell &&
                 same_id(spawns[i].id, id) && spawn_near(spawns[i].pos, pos)) ||
                (!spawns[i].actor && spawn_takes_over(&spawns[i], cell, id, pos)))
                s = &spawns[i];
    if (s && !s->sid) {
        if (s->held) {
            s->held = 0;
            spawns_taken_over++;
            tes3x_log_hex3("net.spawn_taken_over", sid, e->origin, (u32)s->ref);
        }
        s->sid = sid;
        s->send = s->removed; /* a removal waiting for the id */
    } else if (!s) {
        if (!(s = spawn_slot()))
            return;
        s->sid = sid;
        s->cell = cell;
        s->count = count & ~SPAWN_REMOVED;
        copy((u8 *)s->pos, p + 8, 24);
        s->condition = get32le(p + 32);
        s->charge = get32le(p + 36);
        copy((u8 *)s->id, (const u8 *)id, SPAWN_ID);
        if (!(s->base = resolve_object(id)))
            s->unresolved = 1;
    }
    if ((count & SPAWN_REMOVED) && !s->removed) {
        s->removed = 1;
        s->applied = 0;
    }
    spawns_pass = 1;
    if (spawns_logged < 32) {
        spawns_logged++;
        log_text("net.spawn_id", id);
        tes3x_log_hex3("net.spawn", sid, e->origin, cell << 16 | count);
    }
}

static void spawns_session(void)
{
    u32 i;

    if (spawns_welcome == ses.welcomes)
        return;
    spawns_welcome = ses.welcomes;
    spawns_welcomed = now_us();
    /* The server sends what it knows again; what it has not answered yet goes again. */
    for (i = 0; i < SPAWNS; i++) {
        if (spawns[i].used && spawns[i].sid && spawns[i].actor)
            spawns[i].stale = 1;
        else if (spawns[i].used && spawns[i].sid)
            spawns[i].used = 0;
        else if (spawns[i].used)
            spawns[i].send = 1;
    }
}

/* A pickup deletes a reference made at run time outright, without marking it, so each one this
 * console has in an active cell is looked for now and then: missing twice, it is gone. Not while
 * cells may still be loading their references. */
#define SPAWN_WATCH_US 250000u
#define SPAWN_SETTLE_US 2000000u
#define SPAWN_MISSES 2u

static void spawns_watch(void)
{
    struct spawn *s;
    u8 *ref;
    u32 i, now = now_us();

    if (now - spawns_watched < SPAWN_WATCH_US || now - spawns_settled < SPAWN_SETTLE_US)
        return;
    spawns_watched = now;
    for (i = 0; i < SPAWNS; i++) {
        s = &spawns[i];
        if (s->used && s->actor && !s->leveled && !s->removed) {
            actor_watch(s);
            continue;
        }
        if (!s->used || s->actor || s->removed || !s->applied || !s->ref ||
            !cell_active(s->cell_ptr))
            continue;
        if (spawn_is(s->ref, s) || (ref = spawn_find(s, s->cell_ptr))) {
            if (!spawn_is(s->ref, s))
                s->ref = ref;
            s->misses = 0;
            continue;
        }
        if (++s->misses < SPAWN_MISSES)
            continue;
        spawns_gone++;
        tes3x_log_hex3("net.spawn_gone", s->sid, s->cell, s->send);
        if (!s->sid && s->send)
            s->used = 0; /* never sent: nobody else knows it */
        else
            s->removed = s->send = 1;
    }
}

/* Game thread, in the world, after the frame's events and objects. */
static void spawns_frame(void)
{
    u8 data[EVENT_DATA], *cell, *ref;
    struct spawn *s, *batch[REMOVES_PER_EVENT];
    u32 i, n, head, count;

    if (ses.state != SESSION_JOINED)
        return;
    spawns_watch();
    /* Actors the server's replay did not name again. */
    for (i = 0; i < SPAWNS && now_us() - spawns_welcomed > STALE_US; i++)
        if (spawns[i].used && spawns[i].stale)
            spawns[i].used = 0;
    for (i = 0; i < SPAWNS; i++) {
        s = &spawns[i];
        if (!s->used || !s->send || s->sid || s->removed)
            continue;
        if (s->held) {
            if ((int)(now_us() - s->hold_until) < 0)
                continue;
            s->held = 0; /* no other console made it */
            spawns_released++;
        }
        count = s->count | (s->leveled ? SPAWN_LEVELED : 0) | (s->summon ? SPAWN_SUMMON : 0);
        put32le(data, s->token);
        data[4] = (u8)s->cell;
        data[5] = (u8)(s->cell >> 8);
        data[6] = (u8)count;
        data[7] = (u8)(count >> 8);
        copy(data + 8, (const u8 *)s->pos, 24);
        put32le(data + 32, s->condition);
        put32le(data + 36, s->charge);
        head = SPAWN_BYTES;
        if (s->leveled) {
            put32le(data + head, s->leveled);
            head += 4;
        }
        for (n = 0; s->id[n]; n++)
            data[head + n] = (u8)s->id[n];
        data[head + n] = 0;
        if (!event_queue(EVENT_SPAWN, data, head + n + 1))
            break;
        s->send = 0;
        spawns_sent++;
        if (spawns_logged < 32) {
            spawns_logged++;
            log_text("net.spawn_sent", s->id);
            tes3x_log_hex3("net.spawn_at", s->cell, s->count, s->condition);
        }
    }
    for (;;) {
        for (i = n = 0; i < SPAWNS && n < REMOVES_PER_EVENT; i++)
            if (spawns[i].used && spawns[i].removed && spawns[i].send && spawns[i].sid)
                batch[n++] = &spawns[i];
        if (!n)
            break;
        data[0] = (u8)n;
        for (i = 0; i < n; i++)
            put32le(data + 1 + 4 * i, batch[i]->sid);
        if (!event_queue(EVENT_REMOVE, data, 1 + 4 * n))
            break;
        for (i = 0; i < n; i++)
            batch[i]->used = 0;
        tes3x_log_hex3("net.spawn_remove_sent", n, batch[0]->sid, 0);
    }
    if (!spawns_pass)
        return;
    spawns_pass = 0;
    for (i = 0; i < SPAWNS; i++) {
        s = &spawns[i];
        if (s->actor && !s->leveled) {
            actor_apply(s);
            continue;
        }
        if (!s->used || !s->sid || s->applied || s->unresolved || s->actor)
            continue;
        if (!s->cell_ptr)
            s->cell_ptr = cell_at(s->cell);
        if (!cell_active(cell = s->cell_ptr))
            continue;
        ref = spawn_find(s, cell);
        if (s->removed) {
            if (ref)
                spawn_remove(ref);
            s->used = 0;
            continue;
        }
        s->ref = ref ? ref : spawn_make(s, cell);
        s->applied = 1;
        if (!ref && spawns_logged < 32) {
            spawns_logged++;
            tes3x_log_hex3("net.spawn_made", s->sid, (u32)s->ref, s->cell);
            if (s->count & SPAWN_DATA)
                tes3x_log_hex3("net.spawn_data", s->count & SPAWN_COUNT, s->condition, s->charge);
        }
    }
}

static void spawns_stat(void)
{
    u32 i, n = 0;

    for (i = 0; i < SPAWNS; i++)
        n += spawns[i].used != 0;
    tes3x_log_hex3("net.spawns", n, spawns_sent, spawns_received);
    tes3x_log_hex3("net.spawns_made", spawns_made, spawns_removed, spawn_failures);
    tes3x_log_hex3("net.spawns_full", spawns_full, spawns_gone, 0);
    tes3x_log_hex3("net.leveled", leveled_withheld, leveled_rolled, leveled_made);
    tes3x_log_hex3("net.leveled_links", leveled_adopted, leveled_replaced, actor_failures);
    tes3x_log("net.leveled_hook", leveled_hooked);
    tes3x_log_hex3("net.actors_made", actors_made, actors_removed, summons_sent);
    tes3x_log("net.summon_hook", summon_hooked);
    tes3x_log_hex3("net.spawn_holds", spawns_held, spawns_taken_over, spawns_released);
    tes3x_log("net.player_hook", player_hooked);
}
