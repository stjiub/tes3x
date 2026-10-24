/* Records made in play. The spell maker and the alchemy menu (and every other caller of
 * NonDynamicData::addNewObject) give their object an id the engine rolls, so a spell or a potion
 * made on one console is unknown to the rest. Every call site of addNewObject leads to
 * add_object_hook; a spell, potion or enchantment it adds goes to the server as RECORD, which
 * keeps it and sends it whole to the other consoles, and to each console that joins before its
 * character's spells and items. Enchanting also copies an item: that copy goes as a record naming
 * its base item (the enchanting function's ebx at the call that adds the enchantment) and the
 * enchantment. A console builds the record under the id it came with. */
#define EVENT_RECORD 43u /* part, parts, then a chunk of the record */
#define RECORD_SPELL 1u
#define RECORD_POTION 2u /* then the mesh, the icon and the weight as a float after the name */
#define RECORD_ENCHANT 3u /* an empty name, then the charge as a u32 */
#define RECORD_ITEM 4u /* cast byte: the kind; after the name the base item's id, the enchantment's */
#define RECORD_HEAD 10u /* type, cast type, cost (value) u16, flags u32, effect count, 0 */
#define RECORD_ID 32u
#define RECORD_PATH 64u
#define RECORD_BLOB (RECORD_HEAD + 2 * RECORD_ID + 2 * RECORD_PATH + 4 + SOURCE_MAX_EFFECTS * EFFECT_BYTES)
#define RECORD_CHUNK (EVENT_DATA - 2)
#define RECORD_PARTS ((RECORD_BLOB + RECORD_CHUNK - 1) / RECORD_CHUNK)
#define RECORDS_OUT 4u
#define SPELL_SIZE 0xF8
#define SPELL_NAME 0x2C /* a string slot */
#define SPELL_CAST 0x30
#define SPELL_COST 0x32
#define SPELL_EFFECTS 0x34
#define SPELL_FLAGS 0xF4
#define RECORD_VALUE_MAX 10000000u
#define TYPE_ALCHEMY 0x48434C41u /* 'ALCH' */
#define ALCH_SIZE 0x110
#define ALCH_NAME 0x34 /* a string slot, then the script, the mesh and the icon (slots) */
#define ALCH_MESH 0x3C
#define ALCH_ICON 0x40
#define ALCH_WEIGHT 0x44
#define ALCH_VALUE 0x48
#define ALCH_EFFECTS 0x4C
#define ALCH_FLAGS 0x10C
#define TYPE_ENCHANTMENT 0x48434E45u /* 'ENCH' */
#define TYPE_ARMOR 0x4F4D5241u       /* 'ARMO' */
#define TYPE_WEAPON 0x50414557u      /* 'WEAP' */
#define TYPE_CLOTHING 0x544F4C43u    /* 'CLOT' */
#define ENCH_SIZE 0xF8
#define ENCH_CAST 0x2C /* a byte; then the cost and the charge as u16 */
#define ENCH_COST 0x2E
#define ENCH_CHARGE 0x30
#define ENCH_EFFECTS 0x34
#define ENCH_FLAGS 0xF4 /* a u16 */
#define ITEM_NAME 0x34  /* a string slot, as in a potion */
#define ITEM_KINDS 3u
#define VT_ITEM_COPY 0x24
#define VT_SET_ID 0x28
#define VT_SET_NAME 0x10C
#define VT_SET_ENCHANTMENT 0xD4

typedef u32(__attribute__((thiscall)) *fn_add_object)(void *records, void *object);
typedef void(__cdecl *fn_string_slot)(void *slot, const char *text);

#define record_sites ((const u32[])TES3X_NET_ADD_NEW_OBJECT_SITES)
static struct {
    u8 data[RECORD_BLOB];
    u32 length;
} record_out[RECORDS_OUT];
static u32 record_first, record_count, record_hooked;
static u32 record_caller_ebx;
static const u8 *enchant_made, *enchant_base; /* the last enchantment and the item it is for */
static u8 record_in[RECORD_BLOB];
static u32 record_in_size, record_in_next, record_in_parts;
static u32 records_sent, records_received, records_made, records_known, records_bad, records_full;

/* A spell's blob for the server, or 0 if it is not one this console can share. */
static u32 record_spell_blob(u8 *blob, const u8 *spell)
{
    const char *id = object_id(spell), *name;
    const u8 *effect;
    u32 n = RECORD_HEAD, count = 0, i;

    name = *(const char *const *)(spell + SPELL_NAME);
    if (!id || !mapped(name) || !*id || !script_safe((const u8 *)id, RECORD_ID))
        return 0;
    for (; count < SOURCE_MAX_EFFECTS; count++)
        if (*(const short *)(spell + SPELL_EFFECTS + count * EFFECT_BYTES) == -1)
            break;
    if (!count)
        return 0;
    for (i = 0; i < RECORD_BLOB; i++)
        blob[i] = 0;
    blob[0] = RECORD_SPELL;
    blob[1] = spell[SPELL_CAST];
    blob[2] = spell[SPELL_COST];
    blob[3] = spell[SPELL_COST + 1];
    put32le(blob + 4, *(const u32 *)(spell + SPELL_FLAGS));
    blob[8] = (u8)count;
    for (i = 0; id[i]; i++)
        blob[n++] = (u8)id[i];
    blob[n++] = 0;
    for (i = 0; name[i]; i++) {
        if (i >= RECORD_ID - 1 || (u8)name[i] < 0x20 || (u8)name[i] == 0x7F)
            return 0;
        blob[n++] = (u8)name[i];
    }
    blob[n++] = 0;
    effect = spell + SPELL_EFFECTS;
    copy(blob + n, effect, count * EFFECT_BYTES);
    return n + count * EFFECT_BYTES;
}

static u32 record_text(u8 *blob, u32 n, const char *text, u32 limit)
{
    u32 i;

    if (!mapped(text))
        return 0;
    for (i = 0; text[i]; i++) {
        if (i >= limit - 1 || (u8)text[i] < 0x20 || (u8)text[i] == 0x7F)
            return 0;
        blob[n++] = (u8)text[i];
    }
    blob[n++] = 0;
    return n;
}

/* A potion's blob for the server, or 0 if it is not one this console can share. */
static u32 record_potion_blob(u8 *blob, const u8 *potion)
{
    const char *id = object_id(potion);
    u32 n = RECORD_HEAD, count = 0, i;
    u32 value = *(const u16 *)(potion + ALCH_VALUE), flags = *(const u16 *)(potion + ALCH_FLAGS);

    if (!id || !*id || !script_safe((const u8 *)id, RECORD_ID) || flags > 7)
        return 0;
    for (; count < SOURCE_MAX_EFFECTS; count++)
        if (*(const short *)(potion + ALCH_EFFECTS + count * EFFECT_BYTES) == -1)
            break;
    if (!count)
        return 0;
    for (i = 0; i < RECORD_BLOB; i++)
        blob[i] = 0;
    blob[0] = RECORD_POTION;
    blob[2] = (u8)value;
    blob[3] = (u8)(value >> 8);
    put32le(blob + 4, flags);
    blob[8] = (u8)count;
    for (i = 0; id[i]; i++)
        blob[n++] = (u8)id[i];
    blob[n++] = 0;
    if (!(n = record_text(blob, n, *(const char *const *)(potion + ALCH_NAME), RECORD_ID)) ||
        !(n = record_text(blob, n, *(const char *const *)(potion + ALCH_MESH), RECORD_PATH)) ||
        !(n = record_text(blob, n, *(const char *const *)(potion + ALCH_ICON), RECORD_PATH)))
        return 0;
    copy(blob + n, potion + ALCH_WEIGHT, 4);
    n += 4;
    copy(blob + n, potion + ALCH_EFFECTS, count * EFFECT_BYTES);
    return n + count * EFFECT_BYTES;
}

static u32 item_kind_type(u32 kind)
{
    return kind == 0 ? TYPE_ARMOR : kind == 1 ? TYPE_WEAPON : TYPE_CLOTHING;
}

/* An enchantment's blob for the server, or 0. */
static u32 record_enchant_blob(u8 *blob, const u8 *ench)
{
    const char *id = object_id(ench);
    u32 n = RECORD_HEAD, count = 0, i, flags = *(const u16 *)(ench + ENCH_FLAGS);

    if (!id || !*id || !script_safe((const u8 *)id, RECORD_ID) || flags > 7 ||
        ench[ENCH_CAST] > 3)
        return 0;
    for (; count < SOURCE_MAX_EFFECTS; count++)
        if (*(const short *)(ench + ENCH_EFFECTS + count * EFFECT_BYTES) == -1)
            break;
    if (!count)
        return 0;
    for (i = 0; i < RECORD_BLOB; i++)
        blob[i] = 0;
    blob[0] = RECORD_ENCHANT;
    blob[1] = ench[ENCH_CAST];
    blob[2] = ench[ENCH_COST];
    blob[3] = ench[ENCH_COST + 1];
    put32le(blob + 4, flags);
    blob[8] = (u8)count;
    for (i = 0; id[i]; i++)
        blob[n++] = (u8)id[i];
    blob[n++] = 0;
    blob[n++] = 0; /* no name */
    put32le(blob + n, *(const u16 *)(ench + ENCH_CHARGE));
    n += 4;
    copy(blob + n, ench + ENCH_EFFECTS, count * EFFECT_BYTES);
    return n + count * EFFECT_BYTES;
}

/* The blob of an item copy made by enchanting, or 0. */
static u32 record_item_blob(u8 *blob, const u8 *item, u32 kind, const u8 *base, const u8 *ench)
{
    const char *id = object_id(item), *base_id = object_id(base), *ench_id = object_id(ench);
    u32 n = RECORD_HEAD, i;

    if (!id || !*id || !base_id || !*base_id || !ench_id || !*ench_id ||
        !script_safe((const u8 *)id, RECORD_ID) || !script_safe((const u8 *)base_id, RECORD_ID) ||
        !script_safe((const u8 *)ench_id, RECORD_ID))
        return 0;
    for (i = 0; i < RECORD_BLOB; i++)
        blob[i] = 0;
    blob[0] = RECORD_ITEM;
    blob[1] = (u8)kind;
    for (i = 0; id[i]; i++)
        blob[n++] = (u8)id[i];
    blob[n++] = 0;
    if (!(n = record_text(blob, n, *(const char *const *)(item + ITEM_NAME), RECORD_ID)))
        return 0;
    for (i = 0; base_id[i]; i++)
        blob[n++] = (u8)base_id[i];
    blob[n++] = 0;
    for (i = 0; ench_id[i]; i++)
        blob[n++] = (u8)ench_id[i];
    blob[n++] = 0;
    return n;
}

static void record_seen(const u8 *object)
{
    u8 blob[RECORD_BLOB];
    u32 length, slot, type;

    if (ses.state != SESSION_JOINED || player_mode != 3 || !plausible(object))
        return;
    type = *(const u32 *)(object + OBJECT_TYPE);
    if (type == TYPE_SPELL)
        length = record_spell_blob(blob, object);
    else if (type == TYPE_ALCHEMY)
        length = record_potion_blob(blob, object);
    else if (type == TYPE_ENCHANTMENT) {
        const u8 *base = (const u8 *)record_caller_ebx;

        enchant_made = object;
        enchant_base = 0;
        if (plausible(base) && plausible(*(const void *const *)base)) {
            slot = *(const u32 *)(base + OBJECT_TYPE);
            if (slot == TYPE_ARMOR || slot == TYPE_WEAPON || slot == TYPE_CLOTHING)
                enchant_base = base;
        }
        length = record_enchant_blob(blob, object);
    } else if (enchant_made && enchant_base &&
               (type == TYPE_ARMOR || type == TYPE_WEAPON || type == TYPE_CLOTHING) &&
               type == *(const u32 *)(enchant_base + OBJECT_TYPE)) {
        length = record_item_blob(blob, object, type == TYPE_ARMOR ? 0 : type == TYPE_WEAPON ? 1 : 2,
                                  enchant_base, enchant_made);
        enchant_made = enchant_base = 0;
    } else
        return;
    if (!length)
        return;
    if (record_count == RECORDS_OUT) {
        records_full++;
        return;
    }
    slot = (record_first + record_count++) % RECORDS_OUT;
    copy(record_out[slot].data, blob, length);
    record_out[slot].length = length;
    records_made++;
    log_text("net.record_made", object_id(object));
}

static u32 __attribute__((thiscall)) add_object_hook(void *records, u8 *object);

/* The enchanting function adds its enchantment with the item it is for in ebx. */
static u32 __attribute__((thiscall, noinline)) add_object_entry(void *records, u8 *object)
{
    __asm__ volatile("movl %%ebx, %0" : "=m"(record_caller_ebx));
    return add_object_hook(records, object);
}

static u32 __attribute__((thiscall)) add_object_hook(void *records, u8 *object)
{
    u32 ok = ((fn_add_object)TES3X_NET_ADD_NEW_OBJECT)(records, object);

    if (ok & 0xFF)
        record_seen(object);
    return ok;
}

static void records_hook_install(void)
{
    if (record_hooked)
        return;
    record_hooked = 1;
    if (redirect_calls(record_sites, sizeof(record_sites) / sizeof(record_sites[0]),
                       TES3X_NET_ADD_NEW_OBJECT, (const void *)add_object_entry))
        record_hooked |= 2;
    tes3x_log("net.record_hook", record_hooked);
}

/* Game thread, each frame: a whole record or none, as many as the queue has room for. */
static void records_frame(void)
{
    u8 data[EVENT_DATA];
    u32 slot, parts, part, n, length;

    while (ses.state == SESSION_JOINED && record_count) {
        slot = record_first;
        length = record_out[slot].length;
        parts = (length + RECORD_CHUNK - 1) / RECORD_CHUNK;
        if (events_room() < parts + 2)
            return;
        for (part = 0; part < parts; part++) {
            n = length - part * RECORD_CHUNK;
            if (n > RECORD_CHUNK)
                n = RECORD_CHUNK;
            data[0] = (u8)part;
            data[1] = (u8)parts;
            copy(data + 2, record_out[slot].data + part * RECORD_CHUNK, n);
            if (!event_queue(EVENT_RECORD, data, n + 2))
                return; /* sent again from its first part */
        }
        record_first = (record_first + 1) % RECORDS_OUT;
        record_count--;
        records_sent++;
    }
}

static int record_effects_ok(const u8 *effect, u32 count)
{
    u32 i, j;
    int id, skill, attribute;

    for (i = 0; i < count; i++, effect += EFFECT_BYTES) {
        id = (short)(effect[0] | effect[1] << 8);
        skill = (signed char)effect[2];
        attribute = (signed char)effect[3];
        if (id < 0 || id > 142 || skill < -1 || skill >= (int)SKILLS || attribute < -1 ||
            attribute >= (int)ATTRIBUTES || get32le(effect + 4) > 2)
            return 0;
        for (j = 8; j < EFFECT_BYTES; j += 4)
            if (get32le(effect + j) > RECORD_VALUE_MAX)
                return 0;
        if (get32le(effect + 16) > get32le(effect + 20))
            return 0;
    }
    return 1;
}

/* The text at blob + at, ending in a zero inside the blob; its length, or -1. */
static int record_string(const u8 *blob, u32 length, u32 at, u32 limit)
{
    u32 n;

    for (n = 0; at + n < length && blob[at + n]; n++)
        if (n >= limit - 1 || blob[at + n] < 0x20 || blob[at + n] == 0x7F)
            return -1;
    return at + n < length ? (int)n : -1;
}

/* The item an enchanting made: a copy of its base item with the enchantment, as the menu builds it. */
static int record_item_apply(const char *id, const char *name, const char *base_id,
                             const char *ench_id, u32 kind)
{
    static const char file[] = "tes3xmulti.c";
    u8 *base, *ench, *item, *records = records_ptr();

    if (kind >= ITEM_KINDS || !records || !script_safe((const u8 *)id, RECORD_ID) ||
        !script_safe((const u8 *)base_id, RECORD_ID) || !script_safe((const u8 *)ench_id, RECORD_ID))
        return 0;
    if (resolve_object(id)) {
        records_known++;
        return 1;
    }
    base = resolve_object(base_id);
    ench = resolve_object(ench_id);
    if (!plausible(base) || !plausible(ench) ||
        *(const u32 *)(base + OBJECT_TYPE) != item_kind_type(kind) ||
        *(const u32 *)(ench + OBJECT_TYPE) != TYPE_ENCHANTMENT)
        return 0;
    item = ((fn_engine_allocate)TES3X_NET_ENGINE_ALLOCATE)(
        (void *)TES3X_NET_HEAP, kind == 0 ? 0xB4 : kind == 1 ? 0x68 : 0xA8, file, 0);
    if (!plausible(item))
        return 0;
    ((fn_object_call)(kind == 0 ? TES3X_NET_ARMOR_NEW : kind == 1 ? TES3X_NET_WEAPON_NEW
                                                                    : TES3X_NET_CLOTHING_NEW))(item);
    ((void(__attribute__((thiscall)) *)(void *, const void *, u32))vtable_slot(
        item, VT_ITEM_COPY))(item, base, 1);
    ((fn_object_text)vtable_slot(item, VT_SET_NAME))(item, name);
    ((fn_object_text)vtable_slot(item, VT_SET_ID))(item, id);
    ((fn_object_text)vtable_slot(item, VT_SET_ENCHANTMENT))(item, (const char *)ench);
    if (!(((fn_add_object)TES3X_NET_ADD_NEW_OBJECT)(records, item) & 0xFF)) {
        ((void(__attribute__((thiscall)) *)(void *, u32))vtable_slot(item, 0))(item, 1);
        return 0;
    }
    log_text("net.record_applied", id);
    return 1;
}

/* Build the record a whole blob describes; 1 if it exists here afterwards. */
static int record_apply(const u8 *blob, u32 length)
{
    static const char file[] = "tes3xmulti.c";
    const char *id, *name, *mesh = 0, *icon = 0;
    const u8 *effects;
    u32 count, flags, potion, enchant, at;
    int idlen, namelen, meshlen, iconlen;
    u8 *object, *records = records_ptr();

    if (length <= RECORD_HEAD || blob[0] < RECORD_SPELL || blob[0] > RECORD_ITEM || blob[1] > 5 ||
        !records)
        return 0;
    potion = blob[0] == RECORD_POTION;
    enchant = blob[0] == RECORD_ENCHANT;
    flags = get32le(blob + 4);
    count = blob[8];
    id = (const char *)blob + RECORD_HEAD;
    if ((idlen = record_string(blob, length, RECORD_HEAD, RECORD_ID)) <= 0)
        return 0;
    at = RECORD_HEAD + idlen + 1;
    name = (const char *)blob + at;
    if ((namelen = record_string(blob, length, at, RECORD_ID)) < 0)
        return 0;
    at += namelen + 1;
    if (blob[0] == RECORD_ITEM) {
        if ((meshlen = record_string(blob, length, at, RECORD_ID)) <= 0)
            return 0;
        mesh = (const char *)blob + at;
        at += meshlen + 1;
        if ((iconlen = record_string(blob, length, at, RECORD_ID)) <= 0 ||
            at + iconlen + 1 != length)
            return 0;
        return record_item_apply(id, name, mesh, (const char *)blob + at, blob[1]);
    }
    if (enchant) {
        if (namelen || at + 4 > length)
            return 0;
        at += 4;
    }
    if (potion) {
        mesh = (const char *)blob + at;
        if ((meshlen = record_string(blob, length, at, RECORD_PATH)) < 0)
            return 0;
        at += meshlen + 1;
        icon = (const char *)blob + at;
        if ((iconlen = record_string(blob, length, at, RECORD_PATH)) < 0)
            return 0;
        at += iconlen + 1 + 4;
    }
    effects = blob + at;
    if (!count || count > SOURCE_MAX_EFFECTS || flags > 7 || at > length ||
        at + count * EFFECT_BYTES != length || !script_safe((const u8 *)id, RECORD_ID) ||
        !record_effects_ok(effects, count))
        return 0;
    if (resolve_object(id)) {
        records_known++;
        return 1;
    }
    object = ((fn_engine_allocate)TES3X_NET_ENGINE_ALLOCATE)(
        (void *)TES3X_NET_HEAP, potion ? ALCH_SIZE : enchant ? ENCH_SIZE : SPELL_SIZE, file, 0);
    if (!plausible(object))
        return 0;
    if (enchant) {
        ((fn_object_call)TES3X_NET_ENCHANTMENT_NEW)(object);
        ((fn_object_text)vtable_slot(object, VT_SET_ID))(object, id);
        object[ENCH_CAST] = blob[1];
        object[ENCH_COST] = blob[2];
        object[ENCH_COST + 1] = blob[3];
        object[ENCH_CHARGE] = effects[-4];
        object[ENCH_CHARGE + 1] = effects[-3];
        copy(object + ENCH_EFFECTS, effects, count * EFFECT_BYTES);
        object[ENCH_FLAGS] = (u8)flags;
        object[ENCH_FLAGS + 1] = 0;
    } else if (potion) {
        ((fn_object_call)TES3X_NET_ALCHEMY_NEW)(object);
        ((fn_object_text)vtable_slot(object, 0x28))(object, id);
        ((fn_string_slot)TES3X_NET_SET_STRING_SLOT)(object + ALCH_NAME, name);
        ((fn_string_slot)TES3X_NET_SET_STRING_SLOT)(object + ALCH_MESH, mesh);
        ((fn_string_slot)TES3X_NET_SET_STRING_SLOT)(object + ALCH_ICON, icon);
        copy(object + ALCH_WEIGHT, effects - 4, 4);
        object[ALCH_VALUE] = blob[2];
        object[ALCH_VALUE + 1] = blob[3];
        copy(object + ALCH_EFFECTS, effects, count * EFFECT_BYTES);
        object[ALCH_FLAGS] = (u8)flags;
        object[ALCH_FLAGS + 1] = 0;
    } else {
        ((fn_object_call)TES3X_NET_SPELL_NEW)(object);
        ((fn_object_text)vtable_slot(object, 0x28))(object, id);
        ((fn_string_slot)TES3X_NET_SET_STRING_SLOT)(object + SPELL_NAME, name);
        object[SPELL_CAST] = blob[1];
        object[SPELL_COST] = blob[2];
        object[SPELL_COST + 1] = blob[3];
        copy(object + SPELL_EFFECTS, effects, count * EFFECT_BYTES);
        *(u32 *)(object + SPELL_FLAGS) = flags;
    }
    if (!(((fn_add_object)TES3X_NET_ADD_NEW_OBJECT)(records, object) & 0xFF)) {
        ((void(__attribute__((thiscall)) *)(void *, u32))vtable_slot(object, 0))(object, 1);
        return 0;
    }
    log_text("net.record_applied", id);
    return 1;
}

static void records_event(const struct event *e)
{
    u32 part, parts, n;

    if (e->length <= 2)
        return;
    part = e->data[0];
    parts = e->data[1];
    n = e->length - 2;
    if (!parts || parts > RECORD_PARTS || part >= parts) {
        records_bad++;
        record_in_parts = 0;
        return;
    }
    if (!part) {
        record_in_size = record_in_next = 0;
        record_in_parts = parts;
    }
    if (parts != record_in_parts || part != record_in_next ||
        record_in_size + n > RECORD_BLOB) {
        records_bad++;
        record_in_parts = 0;
        return;
    }
    copy(record_in + record_in_size, e->data + 2, n);
    record_in_size += n;
    record_in_next++;
    if (record_in_next < parts)
        return;
    record_in_parts = 0;
    records_received++;
    if (!record_apply(record_in, record_in_size))
        records_bad++;
}

/* tes3xnet spellmake ID EFFECT MAGNITUDE NAME: a spell as the spell maker adds it, with an id. */
static void spellmake_command(const char *text)
{
    static const char file[] = "tes3xmulti.c";
    char id[RECORD_ID], name[RECORD_ID];
    u32 effect, magnitude, n = 0;
    u8 *spell, *records = records_ptr();

    while (*text && *text != ' ' && n < RECORD_ID - 1)
        id[n++] = *text++;
    id[n] = 0;
    text = number(skip(text), &effect);
    if (!text || !(text = number(skip(text), &magnitude)))
        return;
    for (n = 0, text = skip(text); *text && n < RECORD_ID - 1; n++)
        name[n] = *text++;
    name[n] = 0;
    if (!n || !*id || effect > 142 || !records)
        return;
    spell = ((fn_engine_allocate)TES3X_NET_ENGINE_ALLOCATE)((void *)TES3X_NET_HEAP, SPELL_SIZE,
                                                              file, 0);
    if (!plausible(spell))
        return;
    ((fn_object_call)TES3X_NET_SPELL_NEW)(spell);
    ((fn_object_text)vtable_slot(spell, 0x28))(spell, id);
    ((fn_string_slot)TES3X_NET_SET_STRING_SLOT)(spell + SPELL_NAME, name);
    spell[SPELL_COST] = 5;
    *(short *)(spell + SPELL_EFFECTS) = (short)effect;
    spell[SPELL_EFFECTS + 2] = 0xFF;
    spell[SPELL_EFFECTS + 3] = 0xFF;
    *(u32 *)(spell + SPELL_EFFECTS + 12) = 10;
    *(u32 *)(spell + SPELL_EFFECTS + 16) = magnitude;
    *(u32 *)(spell + SPELL_EFFECTS + 20) = magnitude;
    tes3x_log_hex3("net.spellmake", add_object_hook(records, spell), 0, 0);
}

/* tes3xnet potionmake ID EFFECT MAGNITUDE NAME: a potion as the alchemy menu adds it, with an id. */
static void potionmake_command(const char *text)
{
    static const char file[] = "tes3xmulti.c";
    char id[RECORD_ID], name[RECORD_ID];
    u32 effect, magnitude, n = 0;
    u8 *potion, *records = records_ptr();

    while (*text && *text != ' ' && n < RECORD_ID - 1)
        id[n++] = *text++;
    id[n] = 0;
    text = number(skip(text), &effect);
    if (!text || !(text = number(skip(text), &magnitude)))
        return;
    for (n = 0, text = skip(text); *text && n < RECORD_ID - 1; n++)
        name[n] = *text++;
    name[n] = 0;
    if (!n || !*id || effect > 142 || !records)
        return;
    potion = ((fn_engine_allocate)TES3X_NET_ENGINE_ALLOCATE)((void *)TES3X_NET_HEAP, ALCH_SIZE,
                                                               file, 0);
    if (!plausible(potion))
        return;
    ((fn_object_call)TES3X_NET_ALCHEMY_NEW)(potion);
    ((fn_object_text)vtable_slot(potion, 0x28))(potion, id);
    ((fn_string_slot)TES3X_NET_SET_STRING_SLOT)(potion + ALCH_NAME, name);
    ((fn_string_slot)TES3X_NET_SET_STRING_SLOT)(potion + ALCH_MESH, "m\\Misc_Potion_Cheap_01.nif");
    ((fn_string_slot)TES3X_NET_SET_STRING_SLOT)(potion + ALCH_ICON, "m\\Tx_Potion_Cheap_01.tga");
    *(float *)(potion + ALCH_WEIGHT) = 0.5f;
    *(u16 *)(potion + ALCH_VALUE) = 20;
    *(short *)(potion + ALCH_EFFECTS) = (short)effect;
    potion[ALCH_EFFECTS + 2] = 0xFF;
    potion[ALCH_EFFECTS + 3] = 0xFF;
    *(u32 *)(potion + ALCH_EFFECTS + 12) = 10;
    *(u32 *)(potion + ALCH_EFFECTS + 16) = magnitude;
    *(u32 *)(potion + ALCH_EFFECTS + 20) = magnitude;
    tes3x_log_hex3("net.potionmake", add_object_hook(records, potion), 0, 0);
}

/* tes3xnet enchantmake ENCH ITEM BASE EFFECT MAGNITUDE NAME: an enchantment and a copy of the base
 * item that has it, added as the enchanting menu does: the base item in ebx at the first call. */
static void enchantmake_command(const char *text)
{
    static const char file[] = "tes3xmulti.c";
    char ench_id[RECORD_ID], item_id[RECORD_ID], base_id[RECORD_ID], name[RECORD_ID];
    u32 effect, magnitude, n, type, kind;
    u8 *ench, *item, *base, *records = records_ptr();

    for (n = 0; *text && *text != ' ' && n < RECORD_ID - 1;)
        ench_id[n++] = *text++;
    ench_id[n] = 0;
    for (n = 0, text = skip(text); *text && *text != ' ' && n < RECORD_ID - 1;)
        item_id[n++] = *text++;
    item_id[n] = 0;
    for (n = 0, text = skip(text); *text && *text != ' ' && n < RECORD_ID - 1;)
        base_id[n++] = *text++;
    base_id[n] = 0;
    text = number(skip(text), &effect);
    if (!text || !(text = number(skip(text), &magnitude)))
        return;
    for (n = 0, text = skip(text); *text && n < RECORD_ID - 1; n++)
        name[n] = *text++;
    name[n] = 0;
    base = resolve_object(base_id);
    if (!n || !*ench_id || !*item_id || effect > 142 || !records || !plausible(base))
        return;
    type = *(const u32 *)(base + OBJECT_TYPE);
    kind = type == TYPE_ARMOR ? 0 : type == TYPE_WEAPON ? 1 : type == TYPE_CLOTHING ? 2 : 3;
    if (kind == 3)
        return;
    ench = ((fn_engine_allocate)TES3X_NET_ENGINE_ALLOCATE)((void *)TES3X_NET_HEAP, ENCH_SIZE,
                                                            file, 0);
    if (!plausible(ench))
        return;
    ((fn_object_call)TES3X_NET_ENCHANTMENT_NEW)(ench);
    ((fn_object_text)vtable_slot(ench, VT_SET_ID))(ench, ench_id);
    ench[ENCH_CAST] = 1;
    *(u16 *)(ench + ENCH_COST) = 10;
    *(u16 *)(ench + ENCH_CHARGE) = 100;
    *(short *)(ench + ENCH_EFFECTS) = (short)effect;
    ench[ENCH_EFFECTS + 2] = 0xFF;
    ench[ENCH_EFFECTS + 3] = 0xFF;
    *(u32 *)(ench + ENCH_EFFECTS + 4) = 1;
    *(u32 *)(ench + ENCH_EFFECTS + 12) = 10;
    *(u32 *)(ench + ENCH_EFFECTS + 16) = magnitude;
    *(u32 *)(ench + ENCH_EFFECTS + 20) = magnitude;
    record_caller_ebx = (u32)base;
    tes3x_log_hex3("net.enchantmake", add_object_hook(records, ench), 0, 0);
    item = ((fn_engine_allocate)TES3X_NET_ENGINE_ALLOCATE)(
        (void *)TES3X_NET_HEAP, kind == 0 ? 0xB4 : kind == 1 ? 0x68 : 0xA8, file, 0);
    if (!plausible(item))
        return;
    ((fn_object_call)(kind == 0 ? TES3X_NET_ARMOR_NEW : kind == 1 ? TES3X_NET_WEAPON_NEW
                                                                    : TES3X_NET_CLOTHING_NEW))(item);
    ((void(__attribute__((thiscall)) *)(void *, const void *, u32))vtable_slot(
        item, VT_ITEM_COPY))(item, base, 1);
    ((fn_object_text)vtable_slot(item, VT_SET_NAME))(item, name);
    ((fn_object_text)vtable_slot(item, VT_SET_ID))(item, item_id);
    ((fn_object_text)vtable_slot(item, VT_SET_ENCHANTMENT))(item, (const char *)ench);
    tes3x_log_hex3("net.enchantmake_item", add_object_hook(records, item), 0, 0);
}

static void records_stat(void)
{
    tes3x_log_hex3("net.records", records_made, records_sent, records_full);
    tes3x_log_hex3("net.records_in", records_received, records_known, records_bad);
    tes3x_log_hex3("net.record_hook_state", record_hooked, record_count, 0);
}
