/* Object state: whether a data-file reference that is not an actor is disabled or deleted (taken),
 * and its lock. The engine marks every reference it will save through the Reference vtable's
 * setObjectModified, so a hook there names what changed; the frame reads the new state and sends
 * it as OBJECTS records, which the server keeps and replays after each WELCOME. A received state is
 * applied to the reference when its cell is loaded, and again after each cell change. A cell is
 * named by its index in the cells list, which one load order makes the same everywhere. */
#define EVENT_OBJECTS 13u /* count, then records */
#define OBJECT_BYTES 8u   /* refid, cell index u16, state, lock level */
#define OBJECTS_PER_EVENT ((EVENT_DATA - 1) / OBJECT_BYTES)
#define OBJECT_DISABLED 1u
#define OBJECT_DELETED 2u
#define OBJECT_LOCK 4u
#define OBJECT_LOCKED 8u
#define OBJECTS 512u
#define OBJECTS_DIRTY 32u
#define OBJECT_NO_CELL 0xFFFFu
#define REF_FLAGS 0x08
#define REF_MODIFIED_FLAG 0x2u
#define REF_DELETED 0x20u
#define REF_DISABLED 0x800u
#define REF_LIST 0x14 /* the list it is in; the list's cell is at +0xC */
#define REF_BASE 0x28
#define REF_NEXT 0x20
#define ATTACH_LOCK 3u /* data: level, key, trap, locked byte at +0xC */
#define CELL_FLAGS 0x18
#define CELL_REFS_LOADED 0x10u
#define CELL_TEMPORARY 0x10 /* an object whose +8 is the temporary references' list */
#define LIST_HEAD 4         /* of a {count, head, tail, cell} list */
#define RECORDS_CELLS 0xB270 /* {count, head, tail}; nodes {cell, prev, next} */
#define TAG_REFR 0x52464552u
#define TAG_NPC 0x5F43504Eu
#define TAG_CREA 0x41455243u

typedef void(__attribute__((thiscall)) *fn_set_modified)(void *ref, u32 on);

static struct object {
    u32 refid, cell, state, level;
    u8 *cell_ptr;
    u8 pending, applied; /* to send; applied since the last cell change */
} objects[OBJECTS];
static u8 *objects_dirty[OBJECTS_DIRTY];
static u8 objects_dirty_player[OBJECTS_DIRTY]; /* marked during a player's own action */
static u32 player_acting;
static u32 objects_count, objects_dirty_count, objects_dirty_lost, objects_hooked;
static u32 objects_sent, objects_received, objects_applied, objects_full, objects_logged;
static u32 object_applying, objects_signature, objects_pass, spawns_pass, spawns_settled;

static void spawn_local(u8 *ref, u32 player);

static void __attribute__((thiscall)) ref_modified_hook(u8 *ref, u32 on)
{
    u32 i;

    ((fn_set_modified)TES3X_NET_REF_MODIFIED)(ref, on);
    if (!(on & 0xFF) || object_applying || ses.state != SESSION_JOINED)
        return;
    for (i = 0; i < objects_dirty_count; i++)
        if (objects_dirty[i] == ref) {
            objects_dirty_player[i] |= player_acting != 0;
            return;
        }
    if (objects_dirty_count < OBJECTS_DIRTY) {
        objects_dirty_player[objects_dirty_count] = player_acting != 0;
        objects_dirty[objects_dirty_count++] = ref;
    } else {
        objects_dirty_lost++;
    }
}

static void objects_hook_install(void)
{
    u32 *slot = (u32 *)TES3X_NET_REF_MODIFIED_SLOT, cr0, flags;

    if (objects_hooked)
        return;
    objects_hooked = 1;
    if (*slot != TES3X_NET_REF_MODIFIED) {
        tes3x_log_hex3("net.ref_modified_unexpected", (u32)slot, *slot, 0);
        return;
    }
    flags = lock();
    __asm__ volatile("movl %%cr0, %0" : "=r"(cr0));
    __asm__ volatile("movl %0, %%cr0" : : "r"(cr0 & ~CR0_WP) : "memory");
    *slot = (u32)ref_modified_hook;
    __asm__ volatile("movl %0, %%cr0" : : "r"(cr0) : "memory");
    unlock(flags);
    objects_hooked = 2;
}

static const u8 *cells_head(void)
{
    const u8 *handler = *(const u8 *const *)TES3X_NET_DATA_HANDLER, *records, *list;

    if (!plausible(handler) || !plausible(records = *(const u8 *const *)handler) ||
        !plausible(list = *(const u8 *const *)(records + RECORDS_CELLS)))
        return 0;
    return *(const u8 *const *)(list + LIST_HEAD);
}

static u32 cell_index(const u8 *cell)
{
    const u8 *node = cells_head();
    u32 i;

    for (i = 0; plausible(node) && i < OBJECT_NO_CELL; i++, node = *(const u8 *const *)(node + 8))
        if (*(const u8 *const *)node == cell)
            return i;
    return OBJECT_NO_CELL;
}

static u8 *cell_at(u32 index)
{
    const u8 *node = cells_head();

    while (plausible(node) && index--)
        node = *(const u8 *const *)(node + 8);
    return plausible(node) ? *(u8 *const *)node : 0;
}

/* A data-file reference that is not an actor: its cell index, state and lock level. */
static int object_read(const u8 *ref, u32 *cell, u32 *state, u32 *level)
{
    const u8 *base, *list, *att, *lock;
    u32 flags, tag;

    if (!plausible(ref) || *(const u32 *)(ref + 4) != TAG_REFR || !*(const u32 *)(ref + REF_ID) ||
        !plausible(base = *(const u8 *const *)(ref + REF_BASE)))
        return 0;
    tag = *(const u32 *)(base + 4);
    if (tag == TAG_NPC || tag == TAG_CREA)
        return 0;
    if (cell) {
        if (!plausible(list = *(const u8 *const *)(ref + REF_LIST)) ||
            (*cell = cell_index(*(const u8 *const *)(list + 0xC))) == OBJECT_NO_CELL)
            return 0;
    }
    /* Taken covers disabled: applying a take disables the reference too. */
    flags = *(const u32 *)(ref + REF_FLAGS);
    *state = flags & REF_DELETED ? OBJECT_DELETED : flags & REF_DISABLED ? OBJECT_DISABLED : 0;
    *level = 0;
    for (att = *(const u8 *const *)(ref + REF_ATTACHMENTS); plausible(att);
         att = *(const u8 *const *)(att + 4)) {
        if (*(const u32 *)att != ATTACH_LOCK || !plausible(lock = *(const u8 *const *)(att + 8)))
            continue;
        *state |= OBJECT_LOCK | (lock[0xC] ? OBJECT_LOCKED : 0);
        *level = *(const int *)lock < 0 ? 0 : *(const int *)lock > 255 ? 255 : *(const u32 *)lock;
        break;
    }
    return 1;
}

/* The lock level counts only while locked. */
static int object_same(u32 state, u32 level, const struct object *o)
{
    return state == o->state && (!(state & OBJECT_LOCKED) || level == o->level);
}

static struct object *object_find(u32 refid, int create)
{
    u32 i;

    for (i = 0; i < objects_count; i++)
        if (objects[i].refid == refid)
            return &objects[i];
    if (!create || objects_count >= OBJECTS) {
        if (create)
            objects_full++;
        return 0;
    }
    objects[objects_count].refid = refid;
    objects[objects_count].cell_ptr = 0;
    return &objects[objects_count++];
}

static u8 *object_reference(struct object *o)
{
    static const u32 lists[2] = {0x2C, 0x3C};
    const u8 *temporary;
    u8 *ref;
    u32 i;

    if (!o->cell_ptr && !(o->cell_ptr = cell_at(o->cell)))
        return 0;
    for (i = 0; i < 3; i++) {
        if (i < 2)
            ref = *(u8 **)(o->cell_ptr + lists[i] + LIST_HEAD);
        else if ((*(const u32 *)(o->cell_ptr + CELL_FLAGS) & CELL_REFS_LOADED) &&
                 plausible(temporary = *(const u8 *const *)(o->cell_ptr + CELL_TEMPORARY)))
            ref = *(u8 *const *)(temporary + 8 + LIST_HEAD);
        else
            break;
        for (; plausible(ref); ref = *(u8 **)(ref + REF_NEXT))
            if (*(const u32 *)(ref + REF_ID) == o->refid)
                return ref;
    }
    return 0;
}

static void object_apply(u8 *ref, const struct object *o)
{
    char text[24], *end;
    u32 state, level, gone;

    if (!object_read(ref, 0, &state, &level) || object_same(state, level, o))
        return;
    object_applying = 1;
    gone = OBJECT_DISABLED | OBJECT_DELETED;
    if ((o->state & gone) && !(state & gone))
        run_script_on("Disable", ref);
    else if (!(o->state & gone) && (state & OBJECT_DISABLED) && !(state & OBJECT_DELETED))
        run_script_on("Enable", ref);
    if ((o->state & OBJECT_DELETED) && !(state & OBJECT_DELETED)) {
        *(u32 *)(ref + REF_FLAGS) |= REF_DELETED;
        ((fn_set_modified)TES3X_NET_REF_MODIFIED)(ref, 1);
    }
    if ((o->state & OBJECT_LOCKED) && (!(state & OBJECT_LOCKED) || level != o->level)) {
        end = put_int(put_text(text, "Lock "), (int)o->level);
        *end = 0;
        run_script_on(text, ref);
    } else if ((o->state & OBJECT_LOCK) && !(o->state & OBJECT_LOCKED) && (state & OBJECT_LOCKED)) {
        run_script_on("Unlock", ref);
    }
    object_applying = 0;
    objects_applied++;
    if (objects_logged < 32) {
        objects_logged++;
        tes3x_log_hex3("net.object_applied", o->refid, o->state, o->level);
    }
}

static void objects_event(const struct event *e)
{
    const u8 *p = e->data + 1;
    struct object *o;
    u32 i, refid, cell, state, level;

    for (i = 0; i < e->data[0] && 1 + (i + 1) * OBJECT_BYTES <= e->length; i++, p += OBJECT_BYTES) {
        refid = get32le(p);
        cell = p[4] | (u32)p[5] << 8;
        state = p[6] & OBJECT_DELETED ? p[6] & ~OBJECT_DISABLED : p[6];
        level = p[7];
        objects_received++;
        if (!(o = object_find(refid, 1)))
            continue;
        if (o->cell != cell)
            o->cell_ptr = 0;
        o->cell = cell;
        o->state = state;
        o->level = level;
        o->pending = 0;
        o->applied = 0;
        objects_pass = 1;
    }
}

/* Game thread, in the world. */
static void objects_frame(void)
{
    const u8 *handler = *(const u8 *const *)TES3X_NET_DATA_HANDLER;
    u8 data[1 + OBJECTS_PER_EVENT * OBJECT_BYTES], *p;
    struct object *o, *batch[OBJECTS_PER_EVENT];
    u32 i, n, cell, state, level, signature;

    if (ses.state != SESSION_JOINED) {
        objects_dirty_count = 0;
        return;
    }
    for (i = 0; i < objects_dirty_count; i++) {
        if (!*(const u32 *)(objects_dirty[i] + REF_ID)) {
            spawn_local(objects_dirty[i], objects_dirty_player[i]);
            continue;
        }
        /* Opening a door marks it too: a reference in no state worth sharing gets no entry. */
        if (!object_read(objects_dirty[i], &cell, &state, &level) ||
            !(o = object_find(*(const u32 *)(objects_dirty[i] + REF_ID), state != 0)))
            continue;
        if (o->cell_ptr && o->cell == cell && object_same(state, level, o))
            continue;
        if (o->cell != cell)
            o->cell_ptr = 0;
        o->cell = cell;
        o->state = state;
        o->level = level;
        o->pending = o->applied = 1;
        if (!o->cell_ptr)
            o->cell_ptr = cell_at(cell);
    }
    objects_dirty_count = 0;
    for (;;) {
        for (i = n = 0; i < objects_count && n < OBJECTS_PER_EVENT; i++)
            if (objects[i].pending)
                batch[n++] = &objects[i];
        if (!n)
            break;
        data[0] = (u8)n;
        for (i = 0, p = data + 1; i < n; i++, p += OBJECT_BYTES) {
            put32le(p, batch[i]->refid);
            p[4] = (u8)batch[i]->cell;
            p[5] = (u8)(batch[i]->cell >> 8);
            p[6] = (u8)batch[i]->state;
            p[7] = (u8)batch[i]->level;
        }
        if (!event_queue(EVENT_OBJECTS, data, 1 + n * OBJECT_BYTES))
            break;
        for (i = 0; i < n; i++)
            batch[i]->pending = 0;
        objects_sent += n;
    }
    /* A cell change may have loaded cells, or loaded them again from the data files: the interior
     * (DataHandler +0xAC) or the exterior grid cell (+0xA0, +0xA4) changed. */
    signature = 0;
    if (plausible(handler))
        signature = *(const u32 *)(handler + 0xAC) ^ *(const u32 *)(handler + 0xA0) * 31 ^
                    *(const u32 *)(handler + 0xA4) * 1009;
    if (signature != objects_signature) {
        objects_signature = signature;
        for (i = 0; i < objects_count; i++)
            objects[i].applied = 0;
        objects_pass = spawns_pass = 1;
        spawns_settled = now_us();
    }
    if (!objects_pass)
        return;
    objects_pass = 0;
    for (i = 0; i < objects_count; i++) {
        u8 *ref;
        if (objects[i].applied || objects[i].pending)
            continue;
        if ((ref = object_reference(&objects[i]))) {
            object_apply(ref, &objects[i]);
            objects[i].applied = 1;
        }
    }
}

static void objects_stat(void)
{
    tes3x_log_hex3("net.objects", objects_count, objects_sent, objects_received);
    tes3x_log_hex3("net.objects_apply", objects_applied, objects_full, objects_dirty_lost);
    tes3x_log("net.objects_hook", objects_hooked);
}
