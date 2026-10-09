/* Complete snapshots keep removals and timers together; native pointers never cross the wire. */
#define PLAYER_EFFECT_BYTES 608u
#define PLAYER_EFFECTS_MAX 64u
#define PLAYER_EFFECT_BODY (PLAYER_EFFECT_BYTES * PLAYER_EFFECTS_MAX)
#define MOBILE_ACTIVE_EFFECTS 0x1C4

struct effect_value { u32 words[14]; };
typedef u8 *(__attribute__((thiscall)) *fn_source_effects)(void *combo);
typedef void(__attribute__((thiscall)) *fn_effect_insert)(void *map, const char *key,
                                                         struct effect_value value);
typedef u8 *(__attribute__((thiscall)) *fn_active_node)(void *list, void *next, void *previous,
                                                       const void *value);
typedef void(__attribute__((thiscall)) *fn_magic_process)(void *instance, float dt);
typedef void(__attribute__((thiscall)) *fn_retire_effects)(void *instance, void *reference);
typedef void(__cdecl *fn_effect_callback)(void *instance, float dt, void *effect, int index);

static u8 player_effect_out[PLAYER_EFFECT_BODY], player_effect_in[PLAYER_EFFECT_BODY];
static u32 player_effect_size, player_effect_part, player_effect_parts, player_effect_hash;
static u32 player_effect_welcome, player_effect_known, player_effect_in_size;
static u32 player_effect_in_part, player_effect_in_parts, player_effect_pending;
static u32 player_effects_out, player_effects_in, player_effects_bad, player_effects_applied;

static u8 *magic_controller(void)
{
    u8 *world = *(u8 **)TES3X_NET_WORLD, *controller;
    return plausible(world) && plausible(controller = *(u8 **)(world + WORLD_MAGIC))
               ? controller : 0;
}

/* Hash-map values are inline, after their key; spellHit and the save loader use the same map. */
static u8 *player_effect_value(u8 *instance, u32 index, const u8 *ref)
{
    u8 *map = instance + 0x1C + index * 0x10, **buckets, *node;
    u32 count = *(u32 *)(map + 8), i, guard;
    if (count > 4096 || !plausible(buckets = *(u8 ***)(map + 0xC)))
        return 0;
    for (i = 0; i < count; i++)
        for (node = buckets[i], guard = 0; plausible(node) && guard < 4096;
             node = *(u8 **)(node + 0x3C), guard++)
            if (*(const u8 **)(node + 4) == ref)
                return node + 4;
    return 0;
}

static int player_effect_id(u8 *out, const u8 *object)
{
    const char *id = plausible(object) ? object_id(object) : 0;
    u32 i;
    for (i = 0; i < 32; i++)
        out[i] = 0;
    if (!id)
        return 0;
    for (i = 0; i < 31 && id[i]; i++)
        out[i] = (u8)id[i];
    return id[i] == 0;
}

static void player_effect_session(void)
{
    if (player_effect_welcome == ses.welcomes)
        return;
    player_effect_welcome = ses.welcomes;
    player_effect_known = player_effect_parts = player_effect_pending = 0;
    player_effect_in_part = player_effect_in_size = player_effect_in_parts = 0;
}

/* Drain a frozen snapshot over several frames without filling the reliable queue. */
static void player_effect_send(void)
{
    u8 data[EVENT_DATA];
    u32 offset, n;
    player_effect_session();
    while (player_effect_part < player_effect_parts && events_room() > 2) {
        offset = player_effect_part * (EVENT_DATA - 5);
        n = player_effect_size - offset;
        if (n > EVENT_DATA - 5)
            n = EVENT_DATA - 5;
        data[0] = PLAYER_EFFECTS;
        data[1] = (u8)player_effect_part;
        data[2] = (u8)(player_effect_part >> 8);
        data[3] = (u8)player_effect_parts;
        data[4] = (u8)(player_effect_parts >> 8);
        copy(data + 5, player_effect_out + offset, n);
        if (!event_queue(EVENT_PLAYER, data, n + 5))
            return;
        player_effect_part++;
    }
    if (player_effect_parts && player_effect_part == player_effect_parts) {
        player_effect_parts = 0;
        player_effects_out++;
    }
}

static void player_effect_scan(u8 *mobile, int send)
{
    const u8 *ref = player_reference(), *caster, *head, *node, *active, *condition;
    u8 *controller = magic_controller(), *instance, *value, *p;
    u32 n = 0, guard, serial, index, i, hash = 2166136261u, peer;
    player_effect_session();
    if (!controller || player_effect_parts)
        return;
    head = *(const u8 **)(mobile + MOBILE_ACTIVE_EFFECTS + 4);
    if (!plausible(head))
        return;
    for (node = *(const u8 **)head, guard = 0; node != head && plausible(node) && guard < 512;
         node = *(const u8 **)node, guard++) {
        active = node + 8;
        serial = get32le(active);
        index = active[4];
        if (index >= SOURCE_MAX_EFFECTS ||
            !plausible(instance = ((fn_magic_instance)TES3X_NET_MAGIC_INSTANCE)(controller,
                                                                                 serial)) ||
            !(value = player_effect_value(instance, index, ref)))
            continue;
        if (*(u32 *)(value + 0x14) != INSTANCE_WORKING)
            continue;
        if (n == PLAYER_EFFECTS_MAX) {
            player_effects_bad++;
            return; /* Never publish a truncated replacement snapshot. */
        }
        p = player_effect_out + n * PLAYER_EFFECT_BYTES;
        for (i = 0; i < PLAYER_EFFECT_BYTES; i++)
            p[i] = 0;
        put32le(p, serial);
        p[4] = instance[INSTANCE_SOURCE + 4];
        p[5] = (u8)index;
        p[7] = instance[0x18] ? 1 : 0;
        caster = *(const u8 **)(instance + INSTANCE_CASTER);
        if (caster == ref)
            p[6] = 1;
        else if (plausible(caster)) {
            for (peer = 0; peer < PEERS; peer++)
                if (ghosts[peer].placed && ghost_ref(peer) == caster)
                    break;
            if (peer < PEERS) {
                p[6] = 3;
                put32le(p + 8, ghosts[peer].client);
            } else {
                p[6] = 2;
                put32le(p + 8, *(const u32 *)(caster + REF_ID));
            }
        }
        copy(p + 12, instance + 0xAC, 4);
        if (!player_effect_id(p + 16, *(const u8 **)(instance + INSTANCE_SOURCE))) {
            player_effects_bad++;
            return;
        }
        player_effect_id(p + 48, *(const u8 **)(instance + 0xBC));
        copy(p + 80, active + 4, 12);
        p[81] = p[91] = 0;
        copy(p + 92, value + 4, 20);
        if (*(short *)(active + 6) >= 120 && *(short *)(active + 6) <= 131 &&
            *(short *)(active + 6) != 126) {
            for (i = 0; i < 5; i++) {
                const u8 *stack = *(const u8 **)(value + 0x24 + i * 4);
                const u8 *data;
                u8 *kept = p + 388 + i * 44;
                if (!stack)
                    continue;
                if (!plausible(stack) || !player_effect_id(kept, *(const u8 **)stack)) {
                    player_effects_bad++;
                    return;
                }
                data = *(const u8 **)(stack + 4);
                if (data) {
                    if (!plausible(data)) {
                        player_effects_bad++;
                        return;
                    }
                    put32le(kept + 32, ENTRY_DATA);
                    copy(kept + 36, data + ITEM_CONDITION, 4);
                    copy(kept + 40, data + ITEM_CHARGE, 4);
                }
            }
        }
        if (plausible(condition = *(const u8 **)(instance + 0xC0))) {
            p[7] |= 2;
            copy(p + 112, condition + ITEM_CONDITION, 4);
            copy(p + 116, condition + ITEM_CHARGE, 4);
        }
        value = ((fn_source_effects)TES3X_NET_SOURCE_EFFECTS)(instance + INSTANCE_SOURCE);
        copy(p + 120, value, 192);
        if (p[4] == 3) {
            const u8 *alchemy = *(const u8 **)(instance + INSTANCE_SOURCE);
            const char *name = *(const char **)(alchemy + 0x34);
            if (mapped(name))
                for (i = 0; i < 63 && name[i]; i++)
                    p[312 + i] = (u8)name[i];
            copy(p + 376, alchemy + 0x44, 6);
            copy(p + 382, alchemy + 0x10C, 2);
        }
        n++;
    }
    if (node != head) {
        player_effects_bad++;
        return;
    }
    player_effect_size = n * PLAYER_EFFECT_BYTES;
    for (i = 0; i < player_effect_size; i++)
        hash = (hash ^ player_effect_out[i]) * 16777619u;
    if (send && (!player_effect_known || hash != player_effect_hash)) {
        player_effect_parts = (player_effect_size + EVENT_DATA - 6) / (EVENT_DATA - 5);
        if (!player_effect_parts)
            player_effect_parts = 1;
        player_effect_part = 0;
    }
    player_effect_hash = hash;
    player_effect_known = 1;
}

static void player_effect_event(const struct event *e)
{
    u32 part, parts, n;
    if (e->length < 5)
        return;
    part = e->data[1] | e->data[2] << 8;
    parts = e->data[3] | e->data[4] << 8;
    n = e->length - 5;
    player_effect_session();
    if (!part) {
        player_effect_in_size = player_effect_in_part = player_effect_pending = 0;
        player_effect_in_parts = parts;
    }
    if (!parts || part >= parts || part != player_effect_in_part ||
        parts != player_effect_in_parts || player_effect_in_size + n > PLAYER_EFFECT_BODY) {
        player_effect_in_parts = player_effect_in_part = 0;
        player_effects_bad++;
        return;
    }
    copy(player_effect_in + player_effect_in_size, e->data + 5, n);
    player_effect_in_size += n;
    player_effect_in_part++;
    if (player_effect_in_part == parts) {
        player_effect_in_part = 0;
        if (player_effect_in_size % PLAYER_EFFECT_BYTES) {
            player_effects_bad++;
            return;
        }
        player_effect_pending = 1;
        player_effects_in++;
    }
}

/* Consuming even a fixed potion makes a private ALCH with adjusted effects. */
static int player_effect_same(const u8 *a, const u8 *b)
{
    u32 i;
    for (i = 0; i < EFFECT_BYTES; i++)
        if (a[i] != b[i])
            return 0;
    return 1;
}

static int player_effect_definition(const u8 *p)
{
    u32 i, j, low, high;
    int id, skill, attribute;
    for (i = 0; i < 8; i++) {
        const u8 *d = p + 120 + i * EFFECT_BYTES;
        id = (short)(d[0] | d[1] << 8);
        if (id == -1)
            continue;
        skill = (signed char)d[2];
        attribute = (signed char)d[3];
        low = get32le(d + 16);
        high = get32le(d + 20);
        if (id < 0 || id > 142 || skill < -1 || skill >= (int)SKILLS ||
            attribute < -1 || attribute >= (int)ATTRIBUTES || get32le(d + 4) > 2 || low > high)
            return 0;
        for (j = 8; j < EFFECT_BYTES; j += 4)
            if (get32le(d + j) > 10000000u)
                return 0;
    }
    return p[4] != 3 || (float_within(p + 376, 1, STAT_LIMIT) &&
                        *(const float *)(p + 376) >= 0.0f);
}

static u8 *player_effect_source(const u8 *p)
{
    static const char file[] = "tes3xmulti.c";
    u8 *source = resolve_object((const char *)p + 16), *records = records_ptr();
    typedef int(__attribute__((thiscall)) *fn_add_object)(void *records, void *object);
    typedef void(__cdecl *fn_string_slot)(void *slot, const char *text);
    if (source || p[4] != 3 || !records)
        return source;
    source = ((fn_engine_allocate)TES3X_NET_ENGINE_ALLOCATE)((void *)TES3X_NET_HEAP,
                                                               0x110, file, 0);
    if (!plausible(source))
        return 0;
    ((fn_object_call)TES3X_NET_ALCHEMY_NEW)(source);
    ((fn_object_text)vtable_slot(source, 0x28))(source, (const char *)p + 16);
    ((fn_string_slot)TES3X_NET_SET_STRING_SLOT)(source + 0x34, (const char *)p + 312);
    copy(source + 0x4C, p + 120, 192);
    copy(source + 0x44, p + 376, 6);
    copy(source + 0x10C, p + 382, 2);
    if (!((fn_add_object)TES3X_NET_ADD_NEW_OBJECT)(records, source)) {
        ((void(__attribute__((thiscall)) *)(void *, u32))vtable_slot(source, 0))(source, 1);
        return 0;
    }
    return source;
}

/* Bound callbacks create these GMST-selected items and their constant enchantments. */
static u32 player_bound_items(const char **ids)
{
    u32 off, effect, first, last, i, n = 0;
    const char *id;
    for (off = 0; off < player_effect_in_size; off += PLAYER_EFFECT_BYTES) {
        effect = player_effect_in[off + 82] | player_effect_in[off + 83] << 8;
        if (effect >= 120 && effect <= 125)
            first = last = 0x5CD + effect - 120;
        else if (effect >= 127 && effect <= 130)
            first = last = 0x5D3 + effect - 127;
        else if (effect == 131)
            first = 0x5D7, last = 0x5D8;
        else
            continue;
        for (; first <= last; first++) {
            id = ((fn_gmst_text)TES3X_NET_GMST_TEXT)(*(void **)TES3X_NET_WORLD, first);
            if (!mapped(id) || !script_safe((const u8 *)id, SPAWN_ID))
                continue;
            for (i = 0; i < n && !same_name(ids[i], id); i++)
                ;
            if (i == n && n < 12)
                ids[n++] = id;
        }
    }
    return n;
}

static int player_bound_item(const char *id)
{
    const char *ids[12];
    u32 i, n = player_bound_items(ids);
    for (i = 0; i < n; i++)
        if (same_name(ids[i], id))
            return 1;
    return 0;
}

static u8 *player_worn_enchantment(const char *id)
{
    u32 off;
    u8 *source;
    if (!id)
        return 0;
    for (off = 0; off < player_effect_in_size; off += PLAYER_EFFECT_BYTES)
        if (player_effect_in[off + 4] == 2 &&
            script_safe(player_effect_in + off + 16, 32) &&
            script_safe(player_effect_in + off + 48, 32) &&
            same_name((const char *)player_effect_in + off + 48, id) &&
            plausible(source = resolve_object((const char *)player_effect_in + off + 16)) &&
            *(u32 *)(source + OBJECT_TYPE) == 0x48434E45u && source[0x2C] == 3)
            return source;
    return 0;
}

/* Native retirement owns and frees these stack holders. */
static int player_effect_previous(const u8 *p, const u8 *ref, struct effect_value *value)
{
    void *items[5], *data[5];
    u8 *stacks[5];
    u32 i, j;
    int bound = *(short *)(p + 82) >= 120 && *(short *)(p + 82) <= 131 &&
                *(short *)(p + 82) != 126;
    for (i = 0; i < 5; i++) {
        const u8 *kept = p + 388 + i * 44;
        items[i] = data[i] = 0;
        stacks[i] = 0;
        if (!script_safe(kept, 32) || get32le(kept + 32) & ~ENTRY_DATA ||
            (!bound && (kept[0] || get32le(kept + 32) || get32le(kept + 36) ||
                        get32le(kept + 40))))
            return 0;
        if (!kept[0]) {
            if (get32le(kept + 32) || get32le(kept + 36) || get32le(kept + 40))
                return 0;
            continue;
        }
        items[i] = resolve_object((const char *)kept);
        if (!items[i] || !worn_find(*(const u8 **)(ref + REF_BASE), items[i],
                                    (const char *)kept, get32le(kept + 32),
                                    get32le(kept + 36), get32le(kept + 40),
                                    &data[i], 0, 0))
            return 0;
    }
    if (!value || !bound)
        return 1;
    for (i = 0; i < 5; i++)
        if (items[i]) {
            stacks[i] = ((fn_engine_allocate)TES3X_NET_ENGINE_ALLOCATE)(
                (void *)TES3X_NET_HEAP, 8, "tes3xmulti.c", 0);
            if (!plausible(stacks[i])) {
                for (j = 0; j < i; j++)
                    if (stacks[j])
                        ((fn_heap_free)TES3X_NET_HEAP_FREE)((void *)TES3X_NET_HEAP, stacks[j]);
                return 0;
            }
            *(void **)stacks[i] = items[i];
            *(void **)(stacks[i] + 4) = data[i];
        }
    for (i = 0; i < 5; i++) {
        if (value->words[9 + i])
            ((fn_heap_free)TES3X_NET_HEAP_FREE)((void *)TES3X_NET_HEAP,
                                               (void *)value->words[9 + i]);
        value->words[9 + i] = (u32)stacks[i];
    }
    return 1;
}

/* Finalise native effects before absolute statistics are reapplied. */
static void player_effect_apply(u8 *mobile)
{
    u8 *ref, *controller, *instance, *source;
    u8 *head, *node, *value, *effects, *definition, *caster, *item, *data, active[16];
    u8 *instances[PLAYER_EFFECTS_MAX];
    u32 serials[PLAYER_EFFECTS_MAX], used = 0;
    struct effect_value restored;
    struct { u8 *source; u32 type; } combo;
    struct { u8 *item, *data; } casting;
    u32 count, off, index, serial, i, group, low, high;
    int effect_id;
    const u8 *p;
    const char *bound_ids[12];
    u32 bound_count, preparing = player_effect_pending == 1;

    if (!player_effect_pending)
        return;
    ref = (u8 *)player_reference();
    controller = magic_controller();
    if (!ref || !controller)
        return;
    player_effect_pending = 0;
    /* Validate all entries before retiring any local effects. */
    for (off = 0; off < player_effect_in_size; off += PLAYER_EFFECT_BYTES) {
        p = player_effect_in + off;
        effect_id = (short)(p[82] | p[83] << 8);
        combo.source = 0;
        combo.type = p[4];
        if (!get32le(p) || p[4] < 1 || p[4] > 3 || p[5] >= 8 || p[6] > 3 || p[7] & ~3 ||
            p[80] != p[5] || effect_id < 0 || effect_id > 142 || get32le(p + 108) != 5 ||
            !float_within(p + 12, 1, STAT_LIMIT) || !float_within(p + 92, 1, STAT_LIMIT) ||
            !float_within(p + 100, 2, STAT_LIMIT) || !script_safe(p + 16, 32) ||
            !script_safe(p + 312, 64) || !script_safe(p + 48, 32) ||
            !player_effect_definition(p) || !player_effect_previous(p, ref, 0) ||
            *(const float *)(p + 12) < 0.0f ||
            *(const float *)(p + 100) < 0.0f || (int)get32le(p + 96) < 0 ||
            !plausible(source = combo.source = player_effect_source(p)) ||
            *(u32 *)(source + OBJECT_TYPE) != (p[4] == 1 ? TYPE_SPELL :
                                                       p[4] == 2 ? 0x48434E45u : 0x48434C41u) ||
            !plausible(effects = ((fn_source_effects)TES3X_NET_SOURCE_EFFECTS)(&combo)) ||
            *(short *)(effects + p[5] * EFFECT_BYTES) != effect_id ||
            !player_effect_same(effects + p[5] * EFFECT_BYTES,
                                p + 120 + p[5] * EFFECT_BYTES)) {
            player_effects_bad++;
            return;
        }
    }
    head = *(u8 **)(mobile + MOBILE_ACTIVE_EFFECTS + 4);
    if (!plausible(head))
        return;
    for (count = 0; preparing && *(u8 **)head != head && count < 512; count++) {
        node = *(u8 **)head;
        serial = get32le(node + 8);
        instance = ((fn_magic_instance)TES3X_NET_MAGIC_INSTANCE)(controller, serial);
        if (!plausible(instance))
            break;
        ((fn_retire_effects)TES3X_NET_RETIRE_EFFECTS)(instance, ref);
        ((fn_magic_process)TES3X_NET_MAGIC_PROCESS)(instance, 0.0f);
        if (*(u32 *)(instance + INSTANCE_STATE) == 7)
            ((void(__attribute__((thiscall)) *)(void *, u32))TES3X_NET_RETIRE_MAGIC_SERIAL)(
                controller, serial); /* Clear the item-data mapping before reactivation. */
    }
    bound_count = player_bound_items(bound_ids);
    for (i = 0; preparing && i < bound_count; i++)
        actor_item(ref, "RemoveItem", bound_ids[i], " 1");
    for (off = 0; off < player_effect_in_size; off += PLAYER_EFFECT_BYTES) {
        p = player_effect_in + off;
        if (p[4] == 2) {
            for (i = 0; i < bound_count && !same_name(bound_ids[i], (const char *)p + 48); i++)
                ;
            if (i < bound_count)
                continue; /* Re-equipping the recreated bound item adds its enchantment. */
        }
        effect_id = (short)(p[82] | p[83] << 8);
        if ((effect_id >= 120 && effect_id <= 131 && effect_id != 126) != preparing)
            continue;
        serial = get32le(p);
        index = p[5];
        source = player_effect_source(p);
        combo.source = source;
        combo.type = p[4];
        for (group = 0; group < used && serials[group] != serial; group++)
            ;
        if (group == used) {
            item = p[48] ? resolve_object((const char *)p + 48) : 0;
            casting.item = item ? item : source;
            data = 0;
            if (p[7] & 2) {
                if (p[4] == 2 && item)
                    worn_find(*(u8 **)(ref + REF_BASE), item, (const char *)p + 48,
                              ENTRY_DATA, get32le(p + 112), get32le(p + 116),
                              (void **)&data, 0, 0);
                else if (item && plausible(data = ((fn_item_data_new)TES3X_NET_ITEM_DATA_NEW)(item))) {
                    copy(data + ITEM_CONDITION, p + 112, 4);
                    copy(data + ITEM_CHARGE, p + 116, 4);
                }
            }
            casting.data = data;
            instance = ((fn_magic_instance)TES3X_NET_MAGIC_INSTANCE)(controller,
                ((fn_activate_spell)TES3X_NET_ACTIVATE_SPELL)(controller, 0,
                                                               p[4] == 1 ? 0 : &casting, &combo));
            if (!plausible(instance)) {
                player_effects_bad++;
                continue;
            }
            caster = p[6] == 1 ? ref : p[6] == 2 ? actor_ref(get32le(p + 8)) : 0;
            if (p[6] == 3)
                for (i = 0; i < PEERS; i++)
                    if (ghosts[i].client == get32le(p + 8) && ghosts[i].placed)
                        caster = ghost_ref(i);
            *(u8 **)(instance + INSTANCE_CASTER) = caster;
            *(u8 **)(instance + 0xBC) = item;
            *(u32 *)(instance + INSTANCE_STATE) = INSTANCE_WORKING;
            copy(instance + 0xAC, p + 12, 4);
            serials[used] = serial;
            instances[used++] = instance;
        } else {
            instance = instances[group];
        }
        for (i = 0; i < 14; i++)
            restored.words[i] = 0;
        restored.words[0] = (u32)ref;
        copy((u8 *)restored.words + 4, p + 92, 20);
        restored.words[3] = restored.words[4] = 0;
        restored.words[5] = 4;
        active[0] = active[1] = active[2] = active[3] = 0;
        put32le(active, *(u32 *)(instance + 0xA8));
        copy(active + 4, p + 80, 12);
        node = ((fn_active_node)TES3X_NET_ACTIVE_EFFECT_NODE)(mobile + MOBILE_ACTIVE_EFFECTS,
                                                               head, *(u8 **)(head + 4), active);
        if (!plausible(node)) {
            player_effects_bad++;
            continue;
        }
        **(u8 ***)(head + 4) = node;
        *(u8 **)(head + 4) = node;
        (*(u32 *)(mobile + MOBILE_ACTIVE_EFFECTS + 8))++;
        effects = ((fn_source_effects)TES3X_NET_SOURCE_EFFECTS)(instance + INSTANCE_SOURCE);
        definition = effects + index * EFFECT_BYTES;
        effect_id = *(short *)definition;
        /* The callback rolls from the source range. Pin it only for this synchronous call. */
        low = *(u32 *)(definition + 0x10);
        high = *(u32 *)(definition + 0x14);
        *(u32 *)(definition + 0x10) = *(u32 *)(definition + 0x14) = get32le(p + 96);
        instance[0x18] = 1; /* Keep the saved resistance result. */
        ((fn_effect_insert)TES3X_NET_EFFECT_INSERT)(instance + 0x1C + index * 0x10,
                                                     object_id(ref), restored);
        ((fn_effect_callback *)TES3X_NET_EFFECT_CALLBACKS)[effect_id](instance, 0.0f,
                                                                      &restored, index);
        *(u32 *)(definition + 0x10) = low;
        *(u32 *)(definition + 0x14) = high;
        instance[0x18] = p[7] & 1;
        copy(instance + 0xAC, p + 12, 4); /* Corprus Beginning resets this counter. */
        if (!player_effect_previous(p, ref, &restored))
            player_effects_bad++;
        copy((u8 *)restored.words + 4, p + 92, 20);
        ((fn_effect_insert)TES3X_NET_EFFECT_INSERT)(instance + 0x1C + index * 0x10,
                                                     object_id(ref), restored);
        value = player_effect_value(instance, index, ref);
        if (value)
            player_effects_applied++;
    }
    player_effect_pending = preparing ? 2 : 0;
    ((void(__cdecl *)(void))TES3X_NET_EFFECT_ICONS)();
    tes3x_log_hex3("net.player_effects_restored", player_effect_in_size / PLAYER_EFFECT_BYTES,
                   player_effects_applied, player_effects_bad);
}


static void replay_stat_session(void)
{
    u32 i;

    if (replay_stat_welcome == ses.welcomes)
        return;
    replay_stat_welcome = ses.welcomes;
    replay_stat_wait = 0;
    for (i = 0; i < 2; i++)
        replay_stat_known[i][0] = replay_stat_known[i][1] = 0;
}

static void replay_stat_keep(u32 index, u32 current, const u8 *value)
{
    replay_stat_session();
    replay_stats[index][current] = get32le(value);
    replay_stat_known[current][index >> 5] |= 1u << (index & 31);
}

/* Let newly added abilities run before finalising the absolute stat snapshots. */
static int replay_stat_frame(u8 *mobile)
{
    u8 *stat;
    u32 i, current;

    replay_stat_session();
    if (!replay_stat_wait)
        return 0;
    if (--replay_stat_wait) {
        if (replay_stat_wait <= 2)
            player_effect_apply(mobile);
        return 1;
    }
    for (i = 0; i < MODIFIERS; i++) {
        stat = mobile + (i < ATTRIBUTES ? MOBILE_ATTRIBUTES + 0xC * i
                                         : MOBILE_SKILLS + 0x10 * (i - ATTRIBUTES));
        for (current = 0; current < 2; current++)
            if (replay_stat_known[current][i >> 5] >> (i & 31) & 1)
                put32le(stat + STAT_BASE + 4 * current, replay_stats[i][current]);
    }
    tes3x_log("net.player_stats_final", ses.welcomes);
    return 0;
}

/* Each scanner retries under queue pressure; the barrier follows every reliable part. */
static void snapshot_frame(const u8 *ref, u8 *mobile, u8 *npc, u8 *object)
{
    u32 full = rel.full, deferred = snapshot_deferred, bad;
    u8 data[4 + STATE_BYTES];

    switch (snapshot_stage) {
    case 1: player_identity_scan(mobile, npc, 1); break;
    case 2: carried_scan(object, 1); break;
    case 3: worn_scan(object, 1); break;
    case 4: level_scan(mobile, npc, 1); break;
    case 5: skills_scan(mobile, 1); break;
    case 6: modifiers_scan(mobile, 1); break;
    case 7: vitals_scan(mobile, 1); break;
    case 8: journal_scan(1); break;
    case 9: player_spells_scan(npc, 1); break;
    case 10:
        player_effect_send();
        if (player_effect_parts)
            return;
        if (events_room() < 2 + (SKILLS + SKILLS_PER_EVENT - 1) / SKILLS_PER_EVENT +
                            (MODIFIERS + MODIFIERS_PER_EVENT - 1) / MODIFIERS_PER_EVENT)
            return;
        bad = player_effects_bad;
        player_effect_known = 0;
        player_effect_scan(mobile, 1);
        if (bad != player_effects_bad)
            return; /* An invalid effect snapshot cannot be confirmed as a save. */
        /* Freeze stat pairs with the effects that own their modifiers. */
        level_known = skills_known = vitals_known = 0;
        modifiers_known[0] = modifiers_known[1] = 0;
        level_scan(mobile, npc, 1);
        skills_scan(mobile, 1);
        modifiers_scan(mobile, 1);
        vitals_scan(mobile, 1);
        snapshot_stage++;
        return;
    case 11:
        player_effect_send();
        if (player_effect_parts)
            return;
        break;
    case 12: bounty_sent = -1; bounty_frame(); break;
    case 13:
        put32le(data, snapshot_token);
        player_state(ref, data + 4);
        if (!event_queue(EVENT_SNAPSHOT, data, 4 + PLACE_BYTES))
            return;
        snapshot_wait = snapshot_token;
        snapshot_stage = 0;
        tes3x_log("net.snapshot_sent", snapshot_token);
        return;
    default: return;
    }
    if (full == rel.full && deferred == snapshot_deferred)
        snapshot_stage++;
}

/* Game thread, in the world: after READY, the changes once a second. */
static void player_frame(const u8 *ref)
{
    u8 *mobile = player_mobile(), *npc = player_npc(ref), *object = *(u8 *const *)(ref + REF_BASE);
    u32 now = now_us(), i, send = 1;

    if (ses.state != SESSION_JOINED || player_welcome != ses.welcomes || !player_mode ||
        !plausible(mobile) || !npc || !plausible(object))
        return;
    if (snapshot_welcome != ses.welcomes) {
        snapshot_stage = snapshot_wait = 0;
        snapshot_welcome = ses.welcomes;
        if (leave_state == LEAVE_UPLOAD) {
            snapshot_begin();
            leave_upload = snapshot_token;
        }
    }
    if (replay_stat_frame(mobile))
        return;
    if ((snapshot_stage || snapshot_wait) && now - snapshot_since >= LEAVE_TIMEOUT_US) {
        tes3x_log("net.snapshot_timeout", snapshot_token);
        snapshot_stage = snapshot_wait = 0;
    }
    if (player_mode == 3 && (snapshot_stage || snapshot_wait)) {
        if (snapshot_stage)
            snapshot_frame(ref, mobile, npc, object);
        return;
    }
    player_effect_send();
    if (player_mode != 3) {
        /* After a replay what the console has is what the server keeps; otherwise send it all. */
        for (i = 0; i < CARRIED; i++)
            carried[i].item = 0;
        for (i = 0; i < JOURNALS; i++)
            journal_sent[i] = 0;
        level_known = skills_known = vitals_known = player_spells_known = 0;
        modifiers_known[0] = modifiers_known[1] = 0;
        player_identity_known = worn_known = 0;
        send = player_mode == 1;
        if (player_bounty_replayed != ses.welcomes)
            bounty_sent = -1;
        tes3x_log_hex3("net.player_ready", player_mode, ses.welcomes, 0);
        player_mode = 3;
    } else if (now - player_polled < PLAYER_POLL_US) {
        return;
    }
    player_polled = now;
    player_identity_scan(mobile, npc, send);
    carried_scan(object, send);
    worn_scan(object, send);
    level_scan(mobile, npc, send);
    skills_scan(mobile, send);
    modifiers_scan(mobile, send);
    vitals_scan(mobile, send);
    player_effect_scan(mobile, send);
    journal_scan(send);
    player_spells_scan(npc, send || player_spells_replayed != ses.welcomes);
}
