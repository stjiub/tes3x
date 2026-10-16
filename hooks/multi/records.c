/* Records made in play. The spell maker (and every other caller of NonDynamicData::addNewObject)
 * gives its object an id the engine rolls, so a spell made on one console is unknown to the rest.
 * Every call site of addNewObject leads to add_object_hook; a spell it adds goes to the server as
 * RECORD, which keeps it and sends it whole to the other consoles, and to each console that joins
 * before its character's spells. A console builds the record under the id it came with. */
#define EVENT_RECORD 43u /* part, parts, then a chunk of the record */
#define RECORD_SPELL 1u
#define RECORD_HEAD 10u /* type, cast type, cost u16, flags u32, effect count, 0 */
#define RECORD_ID 32u
#define RECORD_BLOB (RECORD_HEAD + 2 * RECORD_ID + SOURCE_MAX_EFFECTS * EFFECT_BYTES)
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

typedef u32(__attribute__((thiscall)) *fn_add_object)(void *records, void *object);
typedef void(__cdecl *fn_string_slot)(void *slot, const char *text);

#define record_sites ((const u32[])TES3X_NET_ADD_NEW_OBJECT_SITES)
static struct {
    u8 data[RECORD_BLOB];
    u32 length;
} record_out[RECORDS_OUT];
static u32 record_first, record_count, record_hooked;
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

static void record_seen(const u8 *object)
{
    u8 blob[RECORD_BLOB];
    u32 length, slot;

    if (ses.state != SESSION_JOINED || player_mode != 3 || !plausible(object) ||
        *(const u32 *)(object + OBJECT_TYPE) != TYPE_SPELL)
        return;
    if (!(length = record_spell_blob(blob, object)))
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
                       TES3X_NET_ADD_NEW_OBJECT, (const void *)add_object_hook))
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

/* Build the record a whole blob describes; 1 if it exists here afterwards. */
static int record_apply(const u8 *blob, u32 length)
{
    static const char file[] = "tes3xmulti.c";
    const char *id, *name;
    u32 count, idlen, namelen, flags;
    u8 *spell, *records = records_ptr();

    if (length <= RECORD_HEAD || blob[0] != RECORD_SPELL || blob[1] > 5 || !records)
        return 0;
    flags = get32le(blob + 4);
    count = blob[8];
    id = (const char *)blob + RECORD_HEAD;
    for (idlen = 0; RECORD_HEAD + idlen < length && id[idlen]; idlen++)
        ;
    if (!idlen || idlen >= RECORD_ID || RECORD_HEAD + idlen + 1 >= length)
        return 0;
    name = id + idlen + 1;
    for (namelen = 0; RECORD_HEAD + idlen + 1 + namelen < length && name[namelen]; namelen++)
        if ((u8)name[namelen] < 0x20 || (u8)name[namelen] == 0x7F)
            return 0;
    if (namelen >= RECORD_ID || !count || count > SOURCE_MAX_EFFECTS || flags > 7 ||
        RECORD_HEAD + idlen + 1 + namelen + 1 + count * EFFECT_BYTES != length ||
        !script_safe((const u8 *)id, RECORD_ID) ||
        !record_effects_ok((const u8 *)name + namelen + 1, count))
        return 0;
    if (resolve_object(id)) {
        records_known++;
        return 1;
    }
    spell = ((fn_engine_allocate)TES3X_NET_ENGINE_ALLOCATE)((void *)TES3X_NET_HEAP, SPELL_SIZE,
                                                              file, 0);
    if (!plausible(spell))
        return 0;
    ((fn_object_call)TES3X_NET_SPELL_NEW)(spell);
    ((fn_object_text)vtable_slot(spell, 0x28))(spell, id);
    ((fn_string_slot)TES3X_NET_SET_STRING_SLOT)(spell + SPELL_NAME, name);
    spell[SPELL_CAST] = blob[1];
    spell[SPELL_COST] = blob[2];
    spell[SPELL_COST + 1] = blob[3];
    copy(spell + SPELL_EFFECTS, (const u8 *)name + namelen + 1, count * EFFECT_BYTES);
    *(u32 *)(spell + SPELL_FLAGS) = flags;
    if (!(((fn_add_object)TES3X_NET_ADD_NEW_OBJECT)(records, spell) & 0xFF)) {
        ((void(__attribute__((thiscall)) *)(void *, u32))vtable_slot(spell, 0))(spell, 1);
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

static void records_stat(void)
{
    tes3x_log_hex3("net.records", records_made, records_sent, records_full);
    tes3x_log_hex3("net.records_in", records_received, records_known, records_bad);
    tes3x_log_hex3("net.record_hook_state", record_hooked, record_count, 0);
}
