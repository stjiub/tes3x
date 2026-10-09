/* Containers. A container reference reads its contents from [ref+0x28]: the base container, shared
 * by every reference of it, until the reference is first opened, when the base is cloned into an
 * instance for that reference and its leveled lists are rolled. Four times a second each console
 * reads the instances in its active cells, and sends one whose contents changed as CONTENTS (in
 * parts): each stack's count, and for a stack's item data its condition and charge. The first
 * reading of an instance, its roll, is marked ROLLED: the server keeps what it already has for
 * that container and sends it back, so the first console's roll holds everywhere. On a cell change
 * a console asks for the containers of its active cells (WANT) and writes what comes into the
 * container's instance, cloning it first, through the inventory calls the Contents menu uses. */
#define EVENT_CONTENTS 16u /* refid, cell u16, part, parts, flags, then entries */
#define EVENT_WANT 17u     /* count, then cell indices u16 */
#define CONTENTS_HEAD 9u
#define CONTENTS_ROLLED 1u
#define ENTRY_DATA 1u /* entry: count i32, flags, [condition, charge], id */
#define BOXES 128u
#define BOX_ENTRIES 96u
#define ACTIVE_CELLS 16u
#define CONTAINER_CLONE 0x164 /* vtable offset: (reference) */
#define OBJECT_INVENTORY 0x3C /* flags, then the stacks' list: first node at +0xC */
#define INVENTORY_FIRST 0xC
#define STACK_VARIABLES 8 /* item data array: pointers +0x4, count +0xC */

typedef void(__attribute__((thiscall)) *fn_clone)(void *object, void *ref);
typedef void(__attribute__((thiscall)) *fn_inventory_add)(void *inventory, void *mobile,
                                                          void *object, int count, int overwrite,
                                                          u8 **data);
typedef void(__attribute__((thiscall)) *fn_inventory_remove)(void *inventory, void *mobile,
                                                             void *object, void *data, int count,
                                                             int drop_array);
typedef void(__attribute__((thiscall)) *fn_heap_free)(void *heap, void *p);

struct entry {
    int count;
    u32 flags, condition, charge;
    char id[SPAWN_ID];
    u8 *data; /* the item data read, not sent or hashed */
};

static struct {
    u32 refid, hash;
} boxes[BOXES];
static struct entry box_in[BOX_ENTRIES];
static u32 box_in_count, box_in_part, box_in_refid, boxes_welcome, boxes_want;
static u32 boxes_sent, boxes_received, boxes_applied, box_failures, boxes_full;
static u32 items_worn, items_taken;

static const u8 *vtable_of(const u8 *object)
{
    return plausible(object) ? *(const u8 *const *)object : 0;
}

static const char *object_id(const u8 *object)
{
    const char *id = ((fn_object_id)(*(void *const *const *)object)[OBJECT_GET_ID / 4])(object);
    return mapped(id) ? id : 0;
}

/* An object's inventory as entries: per stack its item data, one entry each, then what is left;
 * with only, the stacks of that item. */
static u32 contents_read(const u8 *object, struct entry *out, u32 max, const char *only)
{
    const u8 *node = *(const u8 *const *)(object + OBJECT_INVENTORY + INVENTORY_FIRST), *stack;
    const u8 *item, *vars, *const *data;
    const char *id;
    u32 n = 0, guard, i, k, filled;
    int total, used;

    for (guard = 0; plausible(node) && guard < 256 && n < max;
         node = *(const u8 *const *)(node + 4), guard++) {
        if (!plausible(stack = *(const u8 *const *)(node + 8)) ||
            !plausible(item = *(const u8 *const *)(stack + 4)) || !(id = object_id(item)) ||
            (only && !same_id(id, only)))
            continue;
        total = *(const int *)stack;
        used = 0;
        vars = *(const u8 *const *)(stack + STACK_VARIABLES);
        filled = plausible(vars) ? *(const u32 *)(vars + 0xC) : 0;
        data = filled ? *(const u8 *const *const *)(vars + 4) : 0;
        for (i = 0; plausible(data) && i < filled && n < max; i++) {
            if (!plausible(data[i]))
                continue;
            out[n].count = 1;
            out[n].flags = ENTRY_DATA;
            out[n].condition = *(const u32 *)(data[i] + ITEM_CONDITION);
            out[n].charge = *(const u32 *)(data[i] + ITEM_CHARGE);
            out[n].data = (u8 *)data[i];
            for (k = 0; id[k] && k < SPAWN_ID - 1; k++)
                out[n].id[k] = id[k];
            out[n++].id[k] = 0;
            used++;
        }
        if (total < 0 ? total + used : total - used) {
            if (n == max)
                break;
            out[n].count = total < 0 ? total + used : total - used;
            out[n].flags = out[n].condition = out[n].charge = 0;
            out[n].data = 0;
            for (k = 0; id[k] && k < SPAWN_ID - 1; k++)
                out[n].id[k] = id[k];
            out[n++].id[k] = 0;
        }
    }
    return n;
}

static u32 contents_hash(const struct entry *e, u32 n)
{
    u32 hash = 2166136261u, i, k;
    const u8 *p;

    for (i = 0; i < n; i++) {
        p = (const u8 *)&e[i];
        for (k = 0; k < 16; k++)
            hash = (hash ^ p[k]) * 16777619u;
        for (k = 0; e[i].id[k]; k++)
            hash = (hash ^ (u8)e[i].id[k]) * 16777619u;
        hash *= 16777619u;
    }
    return hash;
}

/* Game thread: a container's contents as CONTENTS parts, all or none. */
static int contents_send(u32 refid, u32 cell, u32 flags, const struct entry *e, u32 n)
{
    static u8 parts[BOX_ENTRIES + 1][EVENT_DATA];
    static u32 lengths[BOX_ENTRIES + 1];
    u32 count = 0, i, k, size, lk, room;

    lengths[0] = CONTENTS_HEAD;
    for (i = 0; i < n; i++) {
        for (k = 0; e[i].id[k]; k++)
            ;
        size = 5 + (e[i].flags & ENTRY_DATA ? 8 : 0) + k + 1;
        if (lengths[count] + size > EVENT_DATA)
            lengths[++count] = CONTENTS_HEAD;
        {
            u8 *p = parts[count] + lengths[count];
            put32le(p, (u32)e[i].count);
            p[4] = (u8)e[i].flags;
            p += 5;
            if (e[i].flags & ENTRY_DATA) {
                put32le(p, e[i].condition);
                put32le(p + 4, e[i].charge);
                p += 8;
            }
            copy(p, (const u8 *)e[i].id, k + 1);
        }
        lengths[count] += size;
    }
    count++;
    lk = lock();
    room = EVENTS_OUT - (rel.out_next - rel.out_first);
    unlock(lk);
    if (room < count)
        return 0;
    for (i = 0; i < count; i++) {
        put32le(parts[i], refid);
        parts[i][4] = (u8)cell;
        parts[i][5] = (u8)(cell >> 8);
        parts[i][6] = (u8)i;
        parts[i][7] = (u8)count;
        parts[i][8] = (u8)flags;
        event_queue(EVENT_CONTENTS, parts[i], lengths[i]);
    }
    boxes_sent++;
    return 1;
}

/* The active cells (the current interior, or the 3x3) with their index in the cells list. */
static u32 active_cells(u8 **cells, u32 *indices)
{
    const u8 *node = cells_head();
    u32 i, n = 0;

    for (i = 0; plausible(node) && i < OBJECT_NO_CELL && n < ACTIVE_CELLS;
         i++, node = *(const u8 *const *)(node + 8))
        if (cell_active(*(u8 *const *)node)) {
            cells[n] = *(u8 *const *)node;
            indices[n++] = i;
        }
    return n;
}

/* A cell's reference with this refid, or 0. */
static u8 *cell_reference(u8 *cell, u32 refid)
{
    static const u32 lists[2] = {0x2C, 0x3C};
    const u8 *temporary;
    u8 *ref;
    u32 i;

    for (i = 0; i < 3; i++) {
        if (i < 2)
            ref = *(u8 **)(cell + lists[i] + LIST_HEAD);
        else if (plausible(temporary = *(const u8 *const *)(cell + CELL_TEMPORARY)))
            ref = *(u8 *const *)(temporary + 8 + LIST_HEAD);
        else
            break;
        for (; plausible(ref); ref = *(u8 **)(ref + REF_NEXT))
            if (*(const u32 *)(ref + REF_ID) == refid)
                return ref;
    }
    return 0;
}

static u32 *box_hash(u32 refid, int create)
{
    u32 i, free = BOXES;

    for (i = 0; i < BOXES; i++) {
        if (boxes[i].refid == refid)
            return &boxes[i].hash;
        if (!boxes[i].refid && free == BOXES)
            free = i;
    }
    if (!create)
        return 0;
    if (free == BOXES) {
        boxes_full++;
        free = refid % BOXES; /* forgetting one only sends it again */
    }
    boxes[free].refid = refid;
    boxes[free].hash = 0;
    return &boxes[free].hash;
}

/* Actors' inventories. A mobile gives its actor a copy of the object, and the copy holds the
 * inventory at +0x3C as a container instance does; looting a corpse, pickpocketing and barter change
 * it. The scan reads the actors of the active cells too (not the player or a ghost), keyed by
 * actor_id, and received contents are applied as the difference, so a living actor keeps what it
 * wears. */
static int inventory_actor(const u8 *ref)
{
    u8 *object = *(u8 *const *)(ref + REF_BASE);

    return actor_object(object) && actor_base(object) != object &&
           ref != player_reference() && !is_ghost(ref);
}

static int entry_same(const struct entry *a, const struct entry *b)
{
    return a->flags == b->flags && same_id(a->id, b->id) &&
           (!(a->flags & ENTRY_DATA) || (a->condition == b->condition && a->charge == b->charge));
}

/* Take an item out of an actor's inventory with the script command, which unequips it first: the
 * worn list points into the stacks. */
static void inventory_take(u8 *ref, const char *id, int count)
{
    char line[64], *p = put_text(line, "RemoveItem \"");

    p = put_int(put_text(put_text(p, id), "\" "), count);
    *p = 0;
    run_script_on(line, ref);
    items_taken++;
    log_text("net.inventory_take", id);
}

/* Add and remove what makes the actor's inventory hold the entries. An item that differs only in
 * its condition or charge (a weapon worn by a blow) is changed in place: taking it out would
 * unequip it. */
static int inventory_apply(u8 *ref, const struct entry *want, u32 n, const char *only, u8 *mobile)
{
    static struct entry have[BOX_ENTRIES];
    static u8 matched[BOX_ENTRIES];
    static u32 pair[BOX_ENTRIES];
    u8 *object = *(u8 **)(ref + REF_BASE), *inventory = object + OBJECT_INVENTORY, *item, *data;
    u32 h = contents_read(object, have, BOX_ENTRIES, only), i, k;
    int delta;

    object_applying = 1;
    for (k = 0; k < h; k++)
        matched[k] = 0;
    for (i = 0; i < n; i++) {
        for (k = 0; k < h && (matched[k] || !entry_same(&want[i], &have[k])); k++)
            ;
        if (k < h)
            matched[k] = 1;
        pair[i] = k;
    }
    for (i = 0; i < n; i++) {
        if (pair[i] < h)
            continue;
        for (k = 0; k < h && (matched[k] || have[k].count != want[i].count ||
                              !same_id(have[k].id, want[i].id)); k++)
            ;
        if (k == h)
            continue;
        matched[k] = 1;
        pair[i] = k;
        if ((want[i].flags & ENTRY_DATA) && plausible(have[k].data)) {
            *(u32 *)(have[k].data + ITEM_CONDITION) = want[i].condition;
            *(u32 *)(have[k].data + ITEM_CHARGE) = want[i].charge;
            items_worn++;
        }
    }
    for (i = 0; i < n; i++) {
        delta = want[i].count - (pair[i] < h ? have[pair[i]].count : 0);
        if (!delta)
            continue;
        if (!(item = resolve_object(want[i].id))) {
            box_failures++;
            continue;
        }
        if (delta < 0) {
            inventory_take(ref, want[i].id, -delta);
            continue;
        }
        data = 0;
        if ((want[i].flags & ENTRY_DATA) &&
            plausible(data = ((fn_item_data_new)TES3X_NET_ITEM_DATA_NEW)(item))) {
            *(u32 *)(data + ITEM_CONDITION) = want[i].condition;
            *(u32 *)(data + ITEM_CHARGE) = want[i].charge;
        }
        ((fn_inventory_add)TES3X_NET_INVENTORY_ADD)(inventory, mobile, item, delta, 0,
                                                    data ? &data : 0);
    }
    for (k = 0; k < h; k++)
        if (!matched[k])
            inventory_take(ref, have[k].id, have[k].count < 0 ? -have[k].count : have[k].count);
    ((fn_set_modified)TES3X_NET_REF_MODIFIED)(ref, 1);
    object_applying = 0;
    return 1;
}

/* The actor with this id in an active cell, living or dead. */
static u8 *inventory_owner(u32 refid)
{
    static const u32 lists[2] = {0x2C, 0x3C};
    u8 *cells[ACTIVE_CELLS], *ref;
    const u8 *temporary;
    u32 indices[ACTIVE_CELLS], n, c, l;

    n = active_cells(cells, indices);
    for (c = 0; c < n; c++)
        for (l = 0; l < 3; l++) {
            if (l < 2)
                ref = *(u8 **)(cells[c] + lists[l] + LIST_HEAD);
            else if (plausible(temporary = *(const u8 *const *)(cells[c] + CELL_TEMPORARY)))
                ref = *(u8 *const *)(temporary + 8 + LIST_HEAD);
            else
                break;
            for (; plausible(ref); ref = *(u8 **)(ref + REF_NEXT))
                if (inventory_actor(ref) && actor_id(ref) == refid)
                    return ref;
        }
    return 0;
}

/* Every SPAWN_WATCH_US: the container instances of the active cells whose contents changed. */
static void containers_scan(void)
{
    static struct entry entries[BOX_ENTRIES];
    u8 *cells[ACTIVE_CELLS], *ref, *object;
    const u8 *temporary;
    u32 indices[ACTIVE_CELLS], n, c, l, count, hash, *known, refid;
    static const u32 lists[2] = {0x2C, 0x3C};

    if (leveled_none_epoch != spawns_settled) {
        leveled_none_epoch = spawns_settled;
        leveled_none_count = 0; /* a list that rolled none rolls again on the next visit */
    }
    n = active_cells(cells, indices);
    for (c = 0; c < n; c++)
        for (l = 0; l < 3; l++) {
            if (l < 2)
                ref = *(u8 **)(cells[c] + lists[l] + LIST_HEAD);
            else if (plausible(temporary = *(const u8 *const *)(cells[c] + CELL_TEMPORARY)))
                ref = *(u8 *const *)(temporary + 8 + LIST_HEAD);
            else
                break;
            for (; plausible(ref); ref = *(u8 **)(ref + REF_NEXT)) {
                object = *(u8 **)(ref + REF_BASE);
                if (plausible(object) && *(const u32 *)(object + 4) == TAG_LEVC) {
                    leveled_seen(ref, indices[c], cells[c]);
                    continue;
                }
                if ((u32)vtable_of(object) == TES3X_NET_CONTAINER_INSTANCE_VTABLE)
                    refid = *(const u32 *)(ref + REF_ID);
                else if (inventory_actor(ref))
                    refid = actor_id(ref);
                else
                    continue;
                if (!refid)
                    continue;
                count = contents_read(object, entries, BOX_ENTRIES, 0);
                hash = contents_hash(entries, count) | 1;
                known = box_hash(refid, 0);
                if (known && *known == hash)
                    continue;
                if (contents_send(refid, indices[c], known ? 0 : CONTENTS_ROLLED, entries,
                                  count)) {
                    *box_hash(refid, 1) = hash;
                    tes3x_log_hex3("net.contents_sent", refid, count, known ? 0 : 1);
                }
            }
        }
}

static void wants_send(void)
{
    u8 data[EVENT_DATA], *cells[ACTIVE_CELLS];
    u32 indices[ACTIVE_CELLS], n, i;

    n = active_cells(cells, indices);
    if (!n)
        return;
    data[0] = (u8)n;
    for (i = 0; i < n; i++) {
        data[1 + 2 * i] = (u8)indices[i];
        data[2 + 2 * i] = (u8)(indices[i] >> 8);
    }
    if (event_queue(EVENT_WANT, data, 1 + 2 * n))
        boxes_want = 0;
}

/* Every stack out, item data destroyed. Nothing may be worn: the worn list points into them. */
static void inventory_empty(u8 *inventory)
{
    u8 *node, *stack, *item, *vars, *data;
    u32 guard;
    int count;

    for (guard = 0; plausible(node = *(u8 **)(inventory + INVENTORY_FIRST)) && guard < 512;
         guard++) {
        stack = *(u8 **)(node + 8);
        item = *(u8 **)(stack + 4);
        vars = *(u8 **)(stack + STACK_VARIABLES);
        if (plausible(vars) && *(const u32 *)(vars + 0xC) &&
            plausible(data = **(u8 ***)(vars + 4))) {
            ((fn_inventory_remove)TES3X_NET_INVENTORY_REMOVE)(inventory, 0, item, data, 1, 1);
            vars = *(u8 **)(stack + STACK_VARIABLES);
            if (*(u8 **)(inventory + INVENTORY_FIRST) == node && *(u8 **)(node + 8) == stack &&
                plausible(vars) && *(const u32 *)(vars + 0xC) && **(u8 ***)(vars + 4) == data)
                break; /* not removed: freeing it would leave the stack pointing at it */
            ((fn_mobile_call)TES3X_NET_ITEM_DATA_DESTROY)(data);
            ((fn_heap_free)TES3X_NET_HEAP_FREE)((void *)TES3X_NET_HEAP, data);
            continue;
        }
        count = *(const int *)stack;
        ((fn_inventory_remove)TES3X_NET_INVENTORY_REMOVE)(inventory, 0, item, 0,
                                                          count < 0 ? -count : count ? count : 1,
                                                          1);
        if (*(u8 **)(inventory + INVENTORY_FIRST) == node && *(u8 **)(node + 8) == stack &&
            *(const int *)stack == count)
            break; /* not removed: stop rather than loop */
    }
}

/* Empty the reference's container instance, cloning it first, and fill it with the entries. */
static int contents_apply(u8 *ref, const struct entry *e, u32 n)
{
    u8 *object = *(u8 **)(ref + REF_BASE), *inventory, *item, *data;
    u32 i;

    if ((u32)vtable_of(object) == TES3X_NET_CONTAINER_VTABLE) {
        ((fn_clone)(*(void *const *const *)object)[CONTAINER_CLONE / 4])(object, ref);
        object = *(u8 **)(ref + REF_BASE);
    }
    if ((u32)vtable_of(object) != TES3X_NET_CONTAINER_INSTANCE_VTABLE)
        return 0;
    inventory = object + OBJECT_INVENTORY;
    object_applying = 1;
    inventory_empty(inventory);
    for (i = 0; i < n; i++) {
        if (!(item = resolve_object(e[i].id))) {
            box_failures++;
            log_text("net.contents_unknown", e[i].id);
            continue;
        }
        data = 0;
        if ((e[i].flags & ENTRY_DATA) &&
            plausible(data = ((fn_item_data_new)TES3X_NET_ITEM_DATA_NEW)(item))) {
            *(u32 *)(data + ITEM_CONDITION) = e[i].condition;
            *(u32 *)(data + ITEM_CHARGE) = e[i].charge;
        }
        ((fn_inventory_add)TES3X_NET_INVENTORY_ADD)(inventory, 0, item, e[i].count, 0,
                                                    data ? &data : 0);
    }
    ((fn_set_modified)TES3X_NET_REF_MODIFIED)(ref, 1);
    object_applying = 0;
    return 1;
}

static void contents_event(const struct event *e)
{
    static struct entry read_back[BOX_ENTRIES];
    const u8 *p = e->data;
    u32 refid, cell, off = CONTENTS_HEAD, k, n;
    u8 *cell_ptr, *ref;

    if (e->length < CONTENTS_HEAD)
        return;
    refid = get32le(p);
    cell = p[4] | (u32)p[5] << 8;
    if (p[6] == 0) {
        box_in_count = box_in_part = 0;
        box_in_refid = refid;
    } else if (p[6] != box_in_part || refid != box_in_refid) {
        return;
    }
    box_in_part++;
    while (off + 5 < e->length && box_in_count < BOX_ENTRIES) {
        struct entry *x = &box_in[box_in_count++];
        x->count = (int)get32le(p + off);
        x->flags = p[off + 4];
        off += 5;
        x->condition = x->charge = 0;
        if (x->flags & ENTRY_DATA) {
            x->condition = get32le(p + off);
            x->charge = get32le(p + off + 4);
            off += 8;
        }
        for (k = 0; off + k < e->length && p[off + k] && k < SPAWN_ID - 1; k++)
            x->id[k] = (char)p[off + k];
        x->id[k] = 0;
        if (!script_safe((const u8 *)x->id, SPAWN_ID)) {
            box_in_count = box_in_part = 0;
            refused_events++;
            return;
        }
        while (off < e->length && p[off])
            off++;
        off++;
    }
    if (box_in_part != p[7])
        return;
    boxes_received++;
    /* Not loaded here: WANT asks for it when its cell is. */
    if (!cell_active(cell_ptr = cell_at(cell)) || !(ref = cell_reference(cell_ptr, refid)))
        ref = inventory_owner(refid);
    if (!ref)
        return;
    if (inventory_actor(ref) ? !inventory_apply(ref, box_in, box_in_count, 0, 0)
                             : !contents_apply(ref, box_in, box_in_count)) {
        box_failures++;
        tes3x_log_hex3("net.contents_failed", refid, (u32)vtable_of(*(u8 **)(ref + REF_BASE)), 0);
        return;
    }
    n = contents_read(*(u8 **)(ref + REF_BASE), read_back, BOX_ENTRIES, 0);
    *box_hash(refid, 1) = contents_hash(read_back, n) | 1;
    boxes_applied++;
    tes3x_log_hex3("net.contents_applied", refid, box_in_count, n);
}

/* Game thread, in the world. */
static void containers_frame(void)
{
    static u32 scanned, signature;
    u32 now = now_us(), i;

    if (ses.state != SESSION_JOINED)
        return;
    if (boxes_welcome != ses.welcomes) {
        boxes_welcome = ses.welcomes;
        for (i = 0; i < BOXES; i++)
            boxes[i].refid = 0;
        boxes_want = 1;
    }
    if (signature != spawns_settled) {
        signature = spawns_settled;
        boxes_want = 1;
    }
    /* Once the cells have loaded their references. */
    if (now - spawns_settled < SPAWN_SETTLE_US)
        return;
    if (boxes_want)
        wants_send();
    if (now - scanned < SPAWN_WATCH_US)
        return;
    scanned = now;
    containers_scan();
}

static void containers_stat(void)
{
    tes3x_log_hex3("net.contents", boxes_sent, boxes_received, boxes_applied);
    tes3x_log_hex3("net.contents_bad", box_failures, boxes_full, 0);
    tes3x_log_hex3("net.inventory_items", items_worn, items_taken, 0);
}
