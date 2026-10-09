/* Stream changed character fields once a second; a crash loses only the polling interval.
 * The console compares its inventory (per item object), level,
 * attributes, skills, journal and known spells with what it last sent and sends the changes as PLAYER events;
 * the server keeps the latest of each per character. Current health, magicka and fatigue go out
 * only on a change of a point, or for fatigue of FATIGUE_STEP, so regeneration stays quiet. When a launch runs the character's
 * checkpoint the server sends them back and then READY. Nothing goes out before READY, so a
 * checkpoint's older values never overwrite the server's. */
#define EVENT_PLAYER 25u
#define PLAYER_ITEMS 1u   /* part, parts, item id, then entries as in CONTENTS */
#define PLAYER_LEVEL 2u   /* LEVEL_BYTES */
#define PLAYER_SKILLS 3u  /* count, then (skill u8, base f32, progress f32) */
#define PLAYER_JOURNAL 4u /* count, then (index u16, quest id) */
#define PLAYER_READY 5u   /* from the server: 1 once it replayed what it keeps, 0 to send it all */
#define PLAYER_VITALS 6u  /* current health, magicka, fatigue as f32 */
#define PLAYER_PLACE 7u   /* from the server: where the player last was, as STATE's first bytes */
#define PLAYER_SPELLS 11u /* mode, part, parts, then spell ids ending in zero */
#define PLAYER_BOUNTY 12u /* from the server: the character's last bounty as i32 */
#define PLAYER_IDENTITY 13u /* part, parts, then a slice of IDENTITY_BODY */
#define PLAYER_WORN 14u     /* part, parts, then a slice of WORN entries */
#define PLAYER_EFFECTS 16u
#define PLAYER_MODIFIERS 15u /* count, then (attribute/skill index u8, current f32) */
#define SPELLS_SNAPSHOT 0u
#define SPELLS_ADD 1u
#define SPELLS_REMOVE 2u
#define PLACE_BYTES (20 + CELL_NAME)
#define FATIGUE_STEP 8    /* fatigue regenerates: send a change of an eighth of its base */
#define PLAYER_POLL_US 1000000u
#define CARRIED 256u
#define ITEM_PARTS 12u
#define SKILLS 27u
#define SKILL_BYTES 9u
#define MODIFIER_BYTES 5u
#define MODIFIERS (ATTRIBUTES + SKILLS)
#define MODIFIERS_PER_EVENT ((EVENT_DATA - 2) / MODIFIER_BYTES)
#define SKILLS_PER_EVENT ((EVENT_DATA - 2) / SKILL_BYTES)
#define JOURNALS 4096u
#define JOURNAL_ENTRIES 16u
/* level u16, level progress u16, level-ups per attribute 8 u8 and per specialisation 3 u8, base
 * health, magicka, fatigue and the 8 attributes as f32 */
#define LEVEL_BYTES 59u
#define ATTRIBUTES 8u
#define STAT_BASE 4 /* Statistic: vtable, base, current */
#define MOBILE_ATTRIBUTES 0x254
#define MOBILE_HEALTH_STAT 0x2B4 /* the Statistic; MOBILE_HEALTH is its current value */
#define MOBILE_MAGICKA_STAT 0x2C0
#define MOBILE_ENCUMBRANCE 0x2CC /* the Statistic; current is the carried weight */
#define MOBILE_FATIGUE_STAT 0x2D8
#define MOBILE_SKILLS 0x3B0 /* 0x10 each */
#define PLAYER_LEVELUPS 0x56C /* int per attribute, then per specialisation */
#define PLAYER_LEVEL_PROGRESS 0x5E8
#define PLAYER_SKILL_PROGRESS 0x5F4
#define NPC_LEVEL 0x7C
#define RECORDS_DIALOGUES 0x48 /* list: head +0x8; node: next +0x4, dialogue +0x8 */
#define DIALOGUE_NAME 0x10
#define DIALOGUE_TYPE 0x14
#define DIALOGUE_JOURNAL 4
#define DIALOGUE_INDEX 0x1C
#define PLAYER_SPELLS_MAX 256u
#define NPC_SPELLS_HEAD 0xD0 /* NPC +0xC4 SpellList, +4 list, +8 head */
#define SPELL_OBJECT_ID 0x28

static const char *const attribute_names[ATTRIBUTES] = {
    "Strength", "Intelligence", "Willpower", "Agility", "Speed", "Endurance", "Personality",
    "Luck"};
static const char *const skill_names[SKILLS] = {
    "Block",      "Armorer",     "MediumArmor", "HeavyArmor",  "BluntWeapon", "LongBlade",
    "Axe",        "Spear",       "Athletics",   "Enchant",     "Destruction", "Alteration",
    "Illusion",   "Conjuration", "Mysticism",   "Restoration", "Alchemy",     "Unarmored",
    "Security",   "Sneak",       "Acrobatics",  "LightArmor",  "ShortBlade",  "Marksman",
    "Mercantile", "Speechcraft", "HandToHand"};

static struct {
    const u8 *item;
    u32 hash;
} carried[CARRIED];
static u8 carried_seen[CARRIED], level_sent[LEVEL_BYTES];
static u32 skills_sent[SKILLS][2], skills_known, level_known, vitals_known;
static u32 modifiers_sent[MODIFIERS], modifiers_known[2];
static u32 replay_stats[MODIFIERS][2], replay_stat_known[2][2], replay_stat_welcome;
static u32 replay_stat_wait;
static float vitals_sent[3];
static u16 journal_sent[JOURNALS];
static const u8 *player_spells_sent[PLAYER_SPELLS_MAX], *player_spells_now[PLAYER_SPELLS_MAX];
static char player_spells_stage[PLAYER_SPELLS_MAX][SPAWN_ID];
static u32 player_spells_sent_count, player_spells_stage_count, player_spells_part;
static u32 player_spells_known, player_spells_out, player_spells_in, player_spells_omitted;
static u32 player_spells_replayed;
static u32 player_polled;
static u32 player_items_out, player_stats_out, player_journal_out, player_too_many;
static u32 player_items_in, player_stats_in, player_journal_in, player_apply_failures;
static struct entry player_in[BOX_ENTRIES];
static u32 player_in_count, player_in_part;
static char player_in_id[SPAWN_ID];

static u8 *player_mobile(void)
{
    const u8 *world = *(const u8 **)TES3X_NET_WORLD, *mobs;
    u8 *const *list;

    if (!plausible(world) || !plausible(mobs = *(const u8 **)(world + 0x5C)))
        return 0;
    list = *(u8 *const *const *)(mobs + 0x24);
    return plausible(list) && plausible(*list) ? *list : 0;
}

/* The player's base NPC, which holds the level. */
static u8 *player_npc(const u8 *ref)
{
    u8 *instance = *(u8 *const *)(ref + REF_BASE), *npc;

    return plausible(instance) && plausible(npc = *(u8 *const *)(instance + 0x6C)) ? npc : 0;
}

static u32 events_room(void)
{
    u32 flags = lock(), room = EVENTS_OUT - (rel.out_next - rel.out_first);

    unlock(flags);
    return room;
}

/* A stack's count and item data, as contents_read reads them. */
static u32 stack_hash(const u8 *stack)
{
    const u8 *vars = *(const u8 *const *)(stack + STACK_VARIABLES), *const *data;
    u32 hash = (2166136261u ^ *(const u32 *)stack) * 16777619u, filled, i;

    filled = plausible(vars) ? *(const u32 *)(vars + 0xC) : 0;
    data = filled ? *(const u8 *const *const *)(vars + 4) : 0;
    for (i = 0; plausible(data) && i < filled; i++)
        if (plausible(data[i]))
            hash = ((hash ^ *(const u32 *)(data[i] + ITEM_CONDITION)) * 16777619u ^
                    *(const u32 *)(data[i] + ITEM_CHARGE)) * 16777619u;
    return hash | 1;
}

/* Every stack of one item the player carries, as PLAYER_ITEMS parts; none when it is gone. 0 when
 * the queue has no room. */
static int carried_send(const u8 *object, const char *id)
{
    static struct entry e[BOX_ENTRIES];
    static u8 parts[ITEM_PARTS][EVENT_DATA];
    u32 lengths[ITEM_PARTS], n = contents_read(object, e, BOX_ENTRIES, id), count = 0, head, i, k;
    u32 size;

    for (k = 0; id[k]; k++)
        ;
    head = 3 + k + 1;
    lengths[0] = head;
    for (i = 0; i < n; i++) {
        size = 5 + (e[i].flags & ENTRY_DATA ? 8 : 0);
        if (lengths[count] + size > EVENT_DATA) {
            if (++count == ITEM_PARTS) {
                player_too_many++;
                return 1;
            }
            lengths[count] = head;
        }
        put32le(parts[count] + lengths[count], (u32)e[i].count);
        parts[count][lengths[count] + 4] = (u8)e[i].flags;
        if (e[i].flags & ENTRY_DATA) {
            put32le(parts[count] + lengths[count] + 5, e[i].condition);
            put32le(parts[count] + lengths[count] + 9, e[i].charge);
        }
        lengths[count] += size;
    }
    count++;
    if (events_room() < count + 2) {
        snapshot_deferred++;
        return 0;
    }
    for (i = 0; i < count; i++) {
        parts[i][0] = PLAYER_ITEMS;
        parts[i][1] = (u8)i;
        parts[i][2] = (u8)count;
        copy(parts[i] + 3, (const u8 *)id, k + 1);
        event_queue(EVENT_PLAYER, parts[i], lengths[i]);
    }
    player_items_out++;
    return 1;
}

/* The inventory by item object: a stack that changed, or one that is gone, goes out. */
static void carried_scan(const u8 *object, int send)
{
    const u8 *node, *stack, *item;
    const char *id;
    u32 i, free, guard, hash;

    for (i = 0; i < CARRIED; i++)
        carried_seen[i] = 0;
    for (node = *(const u8 *const *)(object + OBJECT_INVENTORY + INVENTORY_FIRST), guard = 0;
         plausible(node) && guard < 512; node = *(const u8 *const *)(node + 4), guard++) {
        if (!plausible(stack = *(const u8 *const *)(node + 8)) ||
            !plausible(item = *(const u8 *const *)(stack + 4)) || !(id = object_id(item)))
            continue;
        hash = stack_hash(stack);
        for (i = 0, free = CARRIED; i < CARRIED && carried[i].item != item; i++)
            if (!carried[i].item && free == CARRIED)
                free = i;
        if (i == CARRIED && (i = free) == CARRIED) {
            player_too_many++;
            continue;
        }
        carried_seen[i] = 1;
        if (carried[i].item == item && carried[i].hash == hash)
            continue;
        if (send && !carried_send(object, id)) {
            carried_seen[i] = carried[i].item == item;
            continue;
        }
        carried[i].item = item;
        carried[i].hash = hash;
    }
    for (i = 0; i < CARRIED; i++)
        if (carried[i].item && !carried_seen[i] &&
            (!send || ((id = object_id(carried[i].item)) && carried_send(object, id))))
            carried[i].item = 0;
}

static void level_read(const u8 *mobile, const u8 *npc, u8 *out)
{
    static const u32 stats[3] = {MOBILE_HEALTH_STAT, MOBILE_MAGICKA_STAT, MOBILE_FATIGUE_STAT};
    u32 i;
    int v;

    out[0] = npc[NPC_LEVEL];
    out[1] = npc[NPC_LEVEL + 1];
    v = *(const int *)(mobile + PLAYER_LEVEL_PROGRESS);
    out[2] = (u8)v;
    out[3] = (u8)(v >> 8);
    for (i = 0; i < 11; i++) {
        v = ((const int *)(mobile + PLAYER_LEVELUPS))[i];
        out[4 + i] = (u8)(v < 0 ? 0 : v > 255 ? 255 : v);
    }
    for (i = 0; i < 3; i++)
        copy(out + 15 + 4 * i, mobile + stats[i] + STAT_BASE, 4);
    for (i = 0; i < ATTRIBUTES; i++)
        copy(out + 27 + 4 * i, mobile + MOBILE_ATTRIBUTES + 0xC * i + STAT_BASE, 4);
}

static void level_scan(const u8 *mobile, const u8 *npc, int send)
{
    u8 data[1 + LEVEL_BYTES];
    u32 i;

    level_read(mobile, npc, data + 1);
    for (i = 0; level_known && i < LEVEL_BYTES && data[1 + i] == level_sent[i]; i++)
        ;
    if (level_known && i == LEVEL_BYTES)
        return;
    data[0] = PLAYER_LEVEL;
    if (send && !event_queue(EVENT_PLAYER, data, sizeof(data)))
        return;
    copy(level_sent, data + 1, LEVEL_BYTES);
    level_known = 1;
    player_stats_out += send;
}

/* Skills whose base or progress changed, SKILLS_PER_EVENT to an event. */
static void skills_scan(const u8 *mobile, int send)
{
    u8 data[EVENT_DATA];
    u32 i, n = 0, k, base, progress, pending[SKILLS_PER_EVENT];

    for (i = 0; i <= SKILLS; i++) {
        if (i < SKILLS) {
            base = *(const u32 *)(mobile + MOBILE_SKILLS + 0x10 * i + STAT_BASE);
            progress = ((const u32 *)(mobile + PLAYER_SKILL_PROGRESS))[i];
            if ((skills_known >> i & 1) && skills_sent[i][0] == base &&
                skills_sent[i][1] == progress)
                continue;
            data[2 + n * SKILL_BYTES] = (u8)i;
            put32le(data + 3 + n * SKILL_BYTES, base);
            put32le(data + 7 + n * SKILL_BYTES, progress);
            pending[n++] = i;
        }
        if (!n || (n < SKILLS_PER_EVENT && i < SKILLS))
            continue;
        data[0] = PLAYER_SKILLS;
        data[1] = (u8)n;
        if (send && !event_queue(EVENT_PLAYER, data, 2 + n * SKILL_BYTES))
            return;
        for (k = 0; k < n; k++) {
            skills_sent[pending[k]][0] = get32le(data + 3 + k * SKILL_BYTES);
            skills_sent[pending[k]][1] = get32le(data + 7 + k * SKILL_BYTES);
            skills_known |= 1u << pending[k];
        }
        player_stats_out += send;
        n = 0;
    }
}

/* Current attribute and skill values, including fortify, drain and damage. Base values and skill
 * progress have their own events. */
static void modifiers_scan(const u8 *mobile, int send)
{
    u8 data[EVENT_DATA];
    const u8 *stat;
    u32 i, n = 0, k, value, pending[MODIFIERS_PER_EVENT];

    for (i = 0; i <= MODIFIERS; i++) {
        if (i < MODIFIERS) {
            stat = mobile + (i < ATTRIBUTES ? MOBILE_ATTRIBUTES + 0xC * i
                                             : MOBILE_SKILLS + 0x10 * (i - ATTRIBUTES));
            value = *(const u32 *)(stat + STAT_BASE + 4);
            if ((modifiers_known[i >> 5] >> (i & 31) & 1) && modifiers_sent[i] == value)
                continue;
            data[2 + n * MODIFIER_BYTES] = (u8)i;
            put32le(data + 3 + n * MODIFIER_BYTES, value);
            pending[n++] = i;
        }
        if (!n || (n < MODIFIERS_PER_EVENT && i < MODIFIERS))
            continue;
        data[0] = PLAYER_MODIFIERS;
        data[1] = (u8)n;
        if (send && !event_queue(EVENT_PLAYER, data, 2 + n * MODIFIER_BYTES))
            return;
        for (k = 0; k < n; k++) {
            modifiers_sent[pending[k]] = get32le(data + 3 + k * MODIFIER_BYTES);
            modifiers_known[pending[k] >> 5] |= 1u << (pending[k] & 31);
        }
        player_stats_out += send;
        n = 0;
    }
}

static int vitals_moved(const float *now, float fatigue_base)
{
    float step, d;
    u32 i;

    for (i = 0; i < 3; i++) {
        step = i == 2 && fatigue_base > FATIGUE_STEP ? fatigue_base / FATIGUE_STEP : 1.0f;
        d = now[i] - vitals_sent[i];
        if (d >= step || -d >= step)
            return 1;
    }
    return 0;
}

static void vitals_scan(const u8 *mobile, int send)
{
    u8 data[1 + 12];
    float now[3];

    now[0] = *(const float *)(mobile + MOBILE_HEALTH);
    now[1] = *(const float *)(mobile + MOBILE_MAGICKA);
    now[2] = *(const float *)(mobile + MOBILE_FATIGUE);
    if (vitals_known &&
        !vitals_moved(now, *(const float *)(mobile + MOBILE_FATIGUE_STAT + STAT_BASE)))
        return;
    data[0] = PLAYER_VITALS;
    copy(data + 1, (const u8 *)now, 12);
    if (send && !event_queue(EVENT_PLAYER, data, sizeof(data)))
        return;
    copy((u8 *)vitals_sent, (const u8 *)now, 12);
    vitals_known = 1;
    player_stats_out += send;
}

/* The first node of the dialogue list. */
static const u8 *dialogues_head(void)
{
    const u8 *handler = *(const u8 **)TES3X_NET_DATA_HANDLER, *records, *list;

    if (!plausible(handler) || !plausible(records = *(const u8 *const *)handler) ||
        !plausible(list = *(const u8 *const *)(records + RECORDS_DIALOGUES)))
        return 0;
    return *(const u8 *const *)(list + 8);
}

static const char *dialogue_name(const u8 *dialogue)
{
    const char *name = *(const char *const *)(dialogue + DIALOGUE_NAME);

    return mapped(name) ? name : 0;
}

/* Journal indices, kept by the quest's place among the journals and sent as (index, id). */
static void journal_scan(int send)
{
    u8 data[EVENT_DATA];
    const u8 *node = dialogues_head(), *dialogue;
    const char *name;
    u32 k, n = 0, length = 2, size, guard, i, pending[JOURNAL_ENTRIES], values[JOURNAL_ENTRIES];
    int index;

    for (guard = k = 0; k <= JOURNALS && guard < 65536; guard++) {
        dialogue = plausible(node) ? *(const u8 *const *)(node + 8) : 0;
        if (dialogue && (!plausible(dialogue) || dialogue[DIALOGUE_TYPE] != DIALOGUE_JOURNAL)) {
            node = *(const u8 *const *)(node + 4);
            continue;
        }
        size = 0;
        index = 0;
        if (dialogue && k < JOURNALS) {
            index = *(const int *)(dialogue + DIALOGUE_INDEX);
            index = index < 0 ? 0 : index > 0xFFFF ? 0xFFFF : index;
            name = dialogue_name(dialogue);
            for (size = 0; name && name[size] && size < SPAWN_ID - 1; size++)
                ;
            if (journal_sent[k] == (u16)index || !name || name[size] ||
                !script_safe((const u8 *)name, SPAWN_ID)) {
                k++;
                node = *(const u8 *const *)(node + 4);
                continue;
            }
        }
        /* the event is full, or this was the end of the list */
        if (n && (!dialogue || k == JOURNALS || length + 3 + size > EVENT_DATA ||
                  n == JOURNAL_ENTRIES)) {
            data[0] = PLAYER_JOURNAL;
            data[1] = (u8)n;
            if (send && !event_queue(EVENT_PLAYER, data, length))
                return;
            for (i = 0; i < n; i++)
                journal_sent[pending[i]] = (u16)values[i];
            player_journal_out += send;
            n = 0;
            length = 2;
        }
        if (!dialogue || k == JOURNALS)
            return;
        data[length] = (u8)index;
        data[length + 1] = (u8)(index >> 8);
        copy(data + length + 2, (const u8 *)name, size + 1);
        length += 3 + size;
        pending[n] = k++;
        values[n++] = (u32)index;
        node = *(const u8 *const *)(node + 4);
    }
}

static const char *player_spell_id(const u8 *spell)
{
    const char *id;

    return plausible(spell) && mapped(id = *(const char *const *)(spell + SPELL_OBJECT_ID)) ? id : 0;
}

static int spell_object_in(const u8 *spell, const u8 *const *list, u32 count)
{
    u32 i;

    for (i = 0; i < count; i++)
        if (list[i] == spell)
            return 1;
    return 0;
}

static int spell_name_in(const char *id, char list[][SPAWN_ID], u32 count)
{
    u32 i;

    for (i = 0; i < count; i++)
        if (same_name(id, list[i]))
            return 1;
    return 0;
}

/* The player's NPC record owns its learned-spell list. Invalid or overlong ids cannot safely go
 * through AddSpell, and a list above the bound leaves its tail to the next checkpoint. */
static u32 player_spells_read(const u8 *npc, const u8 **out)
{
    const u8 *node = *(const u8 *const *)(npc + NPC_SPELLS_HEAD), *spell;
    const char *id;
    u32 n = 0, guard, length;

    for (guard = 0; plausible(node) && guard < 1024; guard++) {
        spell = *(const u8 *const *)(node + 8);
        id = player_spell_id(spell);
        for (length = 0; id && id[length] && length < SPAWN_ID; length++)
            ;
        if (id && length && length < SPAWN_ID && script_safe((const u8 *)id, SPAWN_ID) &&
            !spell_object_in(spell, out, n)) {
            if (n < PLAYER_SPELLS_MAX)
                out[n++] = spell;
            else
                player_spells_omitted++;
        }
        node = *(const u8 *const *)(node + 4);
    }
    return n;
}

static void player_spell_forget(const u8 *spell)
{
    u32 i;

    for (i = 0; i < player_spells_sent_count && player_spells_sent[i] != spell; i++)
        ;
    if (i < player_spells_sent_count) {
        for (; i + 1 < player_spells_sent_count; i++)
            player_spells_sent[i] = player_spells_sent[i + 1];
        player_spells_sent_count--;
    }
}

/* Send one event of additions or removals. More than fits waits for the next one-second poll. */
static void player_spells_delta(u32 mode, const u8 *const *now, u32 count)
{
    const u8 *spell, *pending[EVENT_DATA / 2];
    const char *id;
    u8 data[EVENT_DATA];
    u32 source_count, i, n = 0, length = 4, size;

    source_count = mode == SPELLS_ADD ? count : player_spells_sent_count;
    for (i = 0; i < source_count; i++) {
        spell = mode == SPELLS_ADD ? now[i] : player_spells_sent[i];
        if ((mode == SPELLS_ADD && spell_object_in(spell, player_spells_sent,
                                                   player_spells_sent_count)) ||
            (mode == SPELLS_REMOVE && spell_object_in(spell, now, count)))
            continue;
        id = player_spell_id(spell);
        for (size = 0; id && id[size] && size < SPAWN_ID; size++)
            ;
        if (!id || !size || size == SPAWN_ID)
            continue;
        if (length + size + 1 > EVENT_DATA) {
            snapshot_deferred++;
            continue;
        }
        copy(data + length, (const u8 *)id, size + 1);
        length += size + 1;
        pending[n++] = spell;
    }
    if (!n)
        return;
    data[0] = PLAYER_SPELLS;
    data[1] = (u8)mode;
    data[2] = 0;
    data[3] = 1;
    if (!event_queue(EVENT_PLAYER, data, length))
        return;
    for (i = 0; i < n; i++) {
        if (mode == SPELLS_ADD && player_spells_sent_count < PLAYER_SPELLS_MAX)
            player_spells_sent[player_spells_sent_count++] = pending[i];
        else if (mode == SPELLS_REMOVE)
            player_spell_forget(pending[i]);
    }
    player_spells_out++;
}

static void player_spells_scan(const u8 *npc, int send)
{
    u32 n = player_spells_read(npc, player_spells_now), i;
    u8 empty[4] = {PLAYER_SPELLS, SPELLS_ADD, 0, 1};

    if (!send) {
        for (i = 0; i < n; i++)
            player_spells_sent[i] = player_spells_now[i];
        player_spells_sent_count = n;
        player_spells_known = 1;
        return;
    }
    if (!player_spells_known) {
        player_spells_sent_count = 0;
        player_spells_known = 1;
        if (!n && !event_queue(EVENT_PLAYER, empty, sizeof(empty)))
            return;
    }
    player_spells_delta(SPELLS_REMOVE, player_spells_now, n);
    player_spells_delta(SPELLS_ADD, player_spells_now, n);
}
