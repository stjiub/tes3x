/* Who the character is: sex, the class's attributes, specialisation and skills, then name, race,
 * head, hair, birthsign, class id and class name, each ending in zero. Applied, it turns whatever
 * the console runs into the kept character. Chargen's own class (NEWCLASSID_CHARGEN) lives only in
 * the save that made it, so a missing class is made as MenuCreateClass makes it. A new race, sex,
 * head or hair needs the model rebuilt, since the BodyPartManager sets that base layer only when it
 * is made: the race menu's OK does that for both views, with its chargen flag held set so it leaves
 * menu mode alone. It destroys MenuInventory for the new race; the per-frame menu check would make
 * it again and leave it on screen, so it is made here as that check makes it and hidden unless the
 * world is in menu mode. It also unequips everything and lets the engine choose again, which drops
 * rings, so what was worn is put back: extras through unequipItem, as unequipAllItems removes them,
 * and the missing with their own item data through Actor::equipItem and the hands update, the
 * branch of MobileActor::wearItem that does not go through the inventory menu (its player branch,
 * like the Equip command, opens it). That does not keep a beast race out of footwear or a closed
 * helmet, as the inventory does, so those stay off as MWSE's isUsableByBeasts decides; then both
 * views take the equipment's body parts. */
#define WORN_MAX 32u
#define UI_EVENT_MENU_TAB 0xFFFF8035u /* what the per-frame menu check passes */
#define RACE_FLAGS 0xD8
#define RACE_BEAST 2u
#define WEARABLE_PARTS 0x44 /* Armor, Clothing (PC +0x54): 7 x (part byte, -1 unused; male, female) */
#define WEARABLE_PART_COUNT 7u
#define PART_HEAD 0u
#define PART_RIGHT_FOOT 15u
#define PART_LEFT_FOOT 16u
#define TAG_ARMO 0x4F4D5241u
#define TAG_CLOT 0x544F4C43u
#define IDENTITY_STRINGS 7u
#define IDENTITY_STATS (1 + 13 * 4)
#define IDENTITY_BODY (IDENTITY_STATS + IDENTITY_STRINGS * 32)
#define IDENTITY_PER_EVENT (EVENT_DATA - 3)
#define IDENTITY_BIRTHSIGN 4u /* the one string that may be empty */
#define RECORDS_CLASSES 0x30
#define NPC_CLASS 0xB4
#define CLASS_ID 0x10
#define CLASS_NAME 0x30
#define CLASS_STATS 0x50 /* attributes 2, specialisation, skills 10, as int */
#define CLASS_PLAYABLE 0x84
#define CLASS_SIZE 0x94
#define CUSTOM_CLASS_TEXT 0x327 /* the GMST chargen gives a made class as its description */
#define PLAYER_FIRST_PERSON_REF 0x660
#define PLAYER_FIRST_PERSON 0x664
#define PLAYER_BIRTHSIGN 0x670
#define OBJECT_SET_MODIFIED 0x14 /* vtable offsets */
#define OBJECT_SET_NAME 0x10C
#define MENUS_FLAGS 0x10 /* [WorldController+WORLD_MENUS]: chargen progress flags */
#define FLAGS_RACE_DONE 0xAC
#define LINK_RACE 0
#define LINK_CLASS 4
#define LINK_HEAD 12
#define LINK_HAIR 16

typedef u8 *(__attribute__((thiscall)) *fn_find_record)(void *records, const char *id);
typedef void *(__attribute__((thiscall)) *fn_engine_allocate)(void *heap, u32 size,
                                                               const char *file, u32 line);
typedef void(__attribute__((thiscall)) *fn_object_call)(void *object);
typedef void(__attribute__((thiscall)) *fn_object_text)(void *object, const char *text);
typedef void *(__attribute__((thiscall)) *fn_object_get)(void *object);
typedef void(__attribute__((thiscall)) *fn_list_append)(void *list, void *item);
typedef u8(__cdecl *fn_race_sex_ok)(void *widget, u32 event, u32 data0, u32 data1, void *source);
typedef void(__cdecl *fn_inventory_build)(void);
typedef u8(__cdecl *fn_menu_tab)(void *menu, u32 event, u32 data0, u32 data1, void *source);
typedef void(__attribute__((thiscall)) *fn_menu_visible)(void *element, int on);
typedef void *(__attribute__((thiscall)) *fn_equip_item)(void *actor, void *object, void *item_data,
                                                         void **stack, void *mobile);
typedef void *(__attribute__((thiscall)) *fn_unequip_item)(void *actor, void *object, int remove,
                                                            void *mobile, int update_gui,
                                                            void *item_data);
typedef const char *(__attribute__((thiscall)) *fn_gmst_text)(void *game, u32 index);

static u8 player_identity_sent[IDENTITY_BODY], player_identity_in[IDENTITY_BODY];
static u32 player_identity_sent_length, player_identity_known, player_identity_in_length;
static u32 player_identity_in_part, player_identity_out, player_identity_applied;
static u32 player_classes_made, player_models_rebuilt, player_worn_fixed;

static void *records_ptr(void)
{
    void *const *handler = *(void *const *const *)TES3X_NET_DATA_HANDLER;

    return plausible(handler) && plausible(*handler) ? *handler : 0;
}

static u8 *find_record(u32 finder, const char *id)
{
    void *records = records_ptr();

    return records ? ((fn_find_record)finder)(records, id) : 0;
}

static void *vtable_slot(const void *object, u32 offset)
{
    return (*(void *const *const *)object)[offset / 4];
}

static u32 player_identity_read(const u8 *mobile, const u8 *npc, u8 *out)
{
    const u8 *class_ = *(const u8 *const *)(npc + NPC_CLASS);
    const u8 *race = *(const u8 *const *)(npc + NPC_RACE);
    const u8 *head = *(const u8 *const *)(npc + NPC_HEAD);
    const u8 *hair = *(const u8 *const *)(npc + NPC_HAIR);
    const u8 *sign = *(const u8 *const *)(mobile + PLAYER_BIRTHSIGN);
    const char *texts[IDENTITY_STRINGS];
    u32 n = IDENTITY_STATS, i, k;

    if (!plausible(class_) || !plausible(race) || !plausible(head) || !plausible(hair))
        return 0;
    texts[0] = *(const char *const *)(npc + NPC_NAME);
    texts[1] = (const char *)race + RACE_ID;
    texts[2] = object_id(head);
    texts[3] = object_id(hair);
    texts[4] = plausible(sign) ? object_id(sign) : "";
    texts[5] = (const char *)class_ + CLASS_ID;
    texts[6] = (const char *)class_ + CLASS_NAME;
    out[0] = (*(const u32 *)(npc + NPC_FLAGS) & NPC_FEMALE) != 0;
    copy(out + 1, class_ + CLASS_STATS, 13 * 4);
    for (i = 0; i < IDENTITY_STRINGS; i++) {
        if (!mapped(texts[i]))
            return 0;
        for (k = 0; k < 31 && texts[i][k]; k++)
            out[n + k] = (u8)texts[i][k];
        if (texts[i][k] || (!k && i != IDENTITY_BIRTHSIGN))
            return 0;
        out[n + k] = 0;
        n += k + 1;
    }
    return n;
}

static void player_identity_scan(const u8 *mobile, const u8 *npc, int send)
{
    u8 body[IDENTITY_BODY], data[EVENT_DATA];
    u32 n = player_identity_read(mobile, npc, body), parts, i, size;

    if (!n)
        return;
    for (i = 0; player_identity_known && n == player_identity_sent_length && i < n &&
                body[i] == player_identity_sent[i];
         i++)
        ;
    if (player_identity_known && i == n)
        return;
    parts = (n + IDENTITY_PER_EVENT - 1) / IDENTITY_PER_EVENT;
    if (send) {
        if (events_room() < parts + 2) {
            snapshot_deferred++;
            return;
        }
        for (i = 0; i < parts; i++) {
            size = n - i * IDENTITY_PER_EVENT;
            size = size < IDENTITY_PER_EVENT ? size : IDENTITY_PER_EVENT;
            data[0] = PLAYER_IDENTITY;
            data[1] = (u8)i;
            data[2] = (u8)parts;
            copy(data + 3, body + i * IDENTITY_PER_EVENT, size);
            event_queue(EVENT_PLAYER, data, 3 + size);
        }
        player_identity_out++;
    }
    copy(player_identity_sent, body, n);
    player_identity_sent_length = n;
    player_identity_known = 1;
}

/* The class by id, its numbers and name made to match; a missing one is made and listed. */
static u8 *player_class(const char *id, const char *name, const u8 *stats)
{
    static const char file[] = "tes3xmulti.c";
    u8 *class_ = find_record(TES3X_NET_FIND_CLASS, id), *records = records_ptr(), *list;
    const char *text;
    u32 i, changed = 0;

    if (!plausible(class_)) {
        if (!records || !plausible(list = *(u8 **)(records + RECORDS_CLASSES)) ||
            !plausible(class_ = ((fn_engine_allocate)TES3X_NET_ENGINE_ALLOCATE)(
                           (void *)TES3X_NET_HEAP, CLASS_SIZE, file, 0)))
            return 0;
        ((fn_object_call)TES3X_NET_CLASS_NEW)(class_);
        ((fn_object_text)TES3X_NET_CLASS_SET_ID)(class_, id);
        class_[CLASS_PLAYABLE] = 0;
        if (!((fn_object_get)TES3X_NET_CLASS_DESCRIPTION)(class_) &&
            plausible(*(void **)TES3X_NET_WORLD) &&
            mapped(text = ((fn_gmst_text)TES3X_NET_GMST_TEXT)(*(void **)TES3X_NET_WORLD,
                                                               CUSTOM_CLASS_TEXT)))
            ((fn_object_text)TES3X_NET_CLASS_SET_DESCRIPTION)(class_, text);
        ((fn_list_append)TES3X_NET_LIST_APPEND)(list, class_);
        player_classes_made++;
    }
    for (i = 0; i < 13 * 4 && class_[CLASS_STATS + i] == stats[i]; i++)
        ;
    if (i < 13 * 4) {
        copy(class_ + CLASS_STATS, stats, 13 * 4);
        changed = 1;
    }
    if (!same_id((const char *)class_ + CLASS_NAME, name)) {
        for (i = 0; name[i] && i < 31; i++)
            class_[CLASS_NAME + i] = (u8)name[i];
        class_[CLASS_NAME + i] = 0;
        changed = 1;
    }
    if (changed)
        ((fn_set_modified)vtable_slot(class_, OBJECT_SET_MODIFIED))(class_, 1);
    return class_;
}

/* The link table's ids are 32-byte buffers the save writes from. */
static void link_set(u8 *links, u32 at, const char *id)
{
    char *slot = *(char **)(links + at);
    u32 i;

    if (!plausible(slot))
        return;
    for (i = 0; id[i] && i < 31; i++)
        slot[i] = id[i];
    slot[i] = 0;
}

static void sex_set(u8 *object, u32 female)
{
    u32 *flags = (u32 *)(object + NPC_FLAGS);

    *flags = (*flags & ~NPC_FEMALE) | (female ? NPC_FEMALE : 0);
}

/* The equipped stacks as (object, item data) pairs. */
static u32 worn_read(const u8 *actor, void *worn[][2])
{
    const u8 *node = *(const u8 *const *)(actor + ACTOR_EQUIPMENT + 8), *stack;
    u32 n = 0, guard;

    for (guard = 0; plausible(node) && guard < 64 && n < WORN_MAX;
         node = *(const u8 *const *)(node + 4), guard++)
        if (plausible(stack = *(const u8 *const *)(node + 8)) &&
            plausible(*(void *const *)stack)) {
            worn[n][0] = *(void *const *)stack;
            worn[n++][1] = *(void *const *)(stack + 4);
        }
    return n;
}

static int worn_has(void *worn[][2], u32 n, void *object, void *data)
{
    u32 i;

    for (i = 0; i < n; i++)
        if (worn[i][0] == object && worn[i][1] == data)
            return 1;
    return 0;
}

static int beast_can_wear(const u8 *object)
{
    u32 tag = *(const u32 *)(object + OBJECT_TYPE), i, part;

    if (tag != TAG_ARMO && tag != TAG_CLOT)
        return 1;
    for (i = 0; i < WEARABLE_PART_COUNT; i++) {
        part = object[WEARABLE_PARTS + 0xC * i];
        if (part == PART_RIGHT_FOOT || part == PART_LEFT_FOOT ||
            (tag == TAG_ARMO && part == PART_HEAD))
            return 0;
    }
    return 1;
}

static void body_parts_update(u8 *ref)
{
    u8 *attachment;
    u32 guard;

    for (attachment = plausible(ref) ? *(u8 **)(ref + REF_ATTACHMENTS) : 0, guard = 0;
         plausible(attachment) && guard < 32; attachment = *(u8 **)(attachment + 4), guard++)
        if (*(u32 *)attachment == ATTACHMENT_BODY_PARTS) {
            if (plausible(*(u8 **)(attachment + 8)))
                ((fn_body_part_update)TES3X_NET_BODY_PART_UPDATE)(*(u8 **)(attachment + 8), ref);
            return;
        }
}

static void player_model_rebuild(u8 *ref, u8 *instance, u8 *mobile, const u8 *race)
{
    static void *before[WORN_MAX][2], *after[WORN_MAX][2];
    u8 *world = *(u8 **)TES3X_NET_WORLD, *menus, *flags, done;
    u32 n, m, i, fixed = player_worn_fixed, inventory_id;
    void *inventory;

    if (!plausible(world) || !plausible(menus = *(u8 **)(world + WORLD_MENUS)) ||
        !plausible(flags = *(u8 **)(menus + MENUS_FLAGS)))
        return;
    n = worn_read(instance, before);
    done = flags[FLAGS_RACE_DONE];
    flags[FLAGS_RACE_DONE] = 1;
    ((fn_race_sex_ok)TES3X_NET_RACE_SEX_OK)((void *)1, 0, 0, 0, 0);
    flags[FLAGS_RACE_DONE] = done;
    inventory_id = ((fn_ui_id)TES3X_NET_UI_ID)("MenuInventory");
    if (!preloading && !((fn_find_menu)TES3X_NET_FIND_MENU)(inventory_id)) {
        ((fn_inventory_build)TES3X_NET_INVENTORY_BUILD)();
        if ((inventory = ((fn_find_menu)TES3X_NET_FIND_MENU)(inventory_id)) != 0) {
            ((fn_menu_tab)TES3X_NET_MENU_TAB)(inventory, UI_EVENT_MENU_TAB, 0, 0, 0);
            if (!world[WORLD_MENU_MODE])
                ((fn_menu_visible)TES3X_NET_SET_VISIBLE)(inventory, 0);
        }
    }
    m = worn_read(instance, after);
    for (i = 0; i < m; i++)
        if (!worn_has(before, n, after[i][0], after[i][1])) {
            ((fn_unequip_item)TES3X_NET_UNEQUIP_ITEM)(instance, after[i][0], 1, mobile, 0,
                                                      after[i][1]);
            log_text("net.player_unworn", object_id(after[i][0]));
            player_worn_fixed++;
        }
    m = worn_read(instance, after);
    for (i = 0; i < n; i++)
        if (!worn_has(after, m, before[i][0], before[i][1])) {
            if ((*(const u32 *)(race + RACE_FLAGS) & RACE_BEAST) && !beast_can_wear(before[i][0])) {
                log_text("net.player_beast_bare", object_id(before[i][0]));
                continue;
            }
            ((fn_equip_item)TES3X_NET_EQUIP_ITEM)(instance, before[i][0], before[i][1], 0, mobile);
            ((fn_mobile_call)TES3X_NET_MOBILE_HANDS)(mobile);
            log_text("net.player_rewear", object_id(before[i][0]));
            player_worn_fixed++;
        }
    if (player_worn_fixed != fixed) {
        body_parts_update(ref);
        body_parts_update(*(u8 **)(mobile + PLAYER_FIRST_PERSON_REF));
    }
    player_models_rebuilt++;
}

struct identity {
    const char *texts[IDENTITY_STRINGS];
    u8 *race, *head, *hair, *sign, *class_;
    u32 female;
};

/* The body's records found, and its class made to match; 0 if any is missing. */
static int identity_resolve(const u8 *body, u32 length, struct identity *id)
{
    u32 off = IDENTITY_STATS, i, k;

    for (i = 0; i < IDENTITY_STRINGS; i++) {
        id->texts[i] = (const char *)body + off;
        for (k = 0; off + k < length && body[off + k] >= 0x20; k++)
            ;
        if (off + k == length || body[off + k] || k > 31 || (!k && i != IDENTITY_BIRTHSIGN))
            return 0;
        off += k + 1;
    }
    id->female = body[0] != 0;
    id->race = find_record(TES3X_NET_FIND_RACE, id->texts[1]);
    id->head = resolve_object(id->texts[2]);
    id->hair = resolve_object(id->texts[3]);
    id->sign = id->texts[IDENTITY_BIRTHSIGN][0]
                   ? find_record(TES3X_NET_FIND_BIRTHSIGN, id->texts[IDENTITY_BIRTHSIGN])
                   : 0;
    id->class_ = 0;
    if (off != length || !plausible(id->race) || !plausible(id->head) || !plausible(id->hair) ||
        (id->texts[IDENTITY_BIRTHSIGN][0] && !plausible(id->sign)))
        return 0;
    return (id->class_ = player_class(id->texts[5], id->texts[6], body + 1)) != 0;
}

/* Writes the identity into the player's records; 1 if race, sex, head or hair changed. The
 * instance and mobile may not exist yet. */
static u32 identity_write(u8 *npc, u8 *instance, u8 *mobile, const struct identity *id)
{
    u8 *links, *first;
    u32 looks = *(u8 **)(npc + NPC_RACE) != id->race || *(u8 **)(npc + NPC_HEAD) != id->head ||
                *(u8 **)(npc + NPC_HAIR) != id->hair ||
                ((*(const u32 *)(npc + NPC_FLAGS) & NPC_FEMALE) != 0) != id->female;

    if (!mapped(*(const char *const *)(npc + NPC_NAME)) ||
        !same_id(*(const char *const *)(npc + NPC_NAME), id->texts[0]))
        ((fn_object_text)vtable_slot(npc, OBJECT_SET_NAME))(npc, id->texts[0]);
    if (plausible(links = *(u8 **)(npc + NPC_LINKS))) {
        link_set(links, LINK_RACE, id->texts[1]);
        link_set(links, LINK_CLASS, id->texts[5]);
        link_set(links, LINK_HEAD, id->texts[2]);
        link_set(links, LINK_HAIR, id->texts[3]);
    }
    *(u8 **)(npc + NPC_RACE) = id->race;
    *(u8 **)(npc + NPC_CLASS) = id->class_;
    *(u8 **)(npc + NPC_HEAD) = id->head;
    *(u8 **)(npc + NPC_HAIR) = id->hair;
    sex_set(npc, id->female);
    if (instance)
        sex_set(instance, id->female);
    if (!mobile)
        return looks;
    first = *(u8 **)(mobile + PLAYER_FIRST_PERSON);
    if (plausible(first) && *(void **)first == *(void **)npc) {
        *(u8 **)(first + NPC_RACE) = id->race;
        sex_set(first, id->female);
    }
    if (id->sign)
        *(u8 **)(mobile + PLAYER_BIRTHSIGN) = id->sign;
    return looks;
}

static void player_identity_apply(u8 *ref, const u8 *body, u32 length)
{
    struct identity id;
    u8 *mobile = player_mobile(), *instance = *(u8 **)(ref + REF_BASE), *npc;
    u32 looks;

    if (!plausible(mobile) || !plausible(instance) ||
        !plausible(npc = *(u8 **)(instance + NPC_BASE)) || !identity_resolve(body, length, &id)) {
        player_apply_failures++;
        log_text("net.player_identity_bad", (const char *)body + IDENTITY_STATS);
        return;
    }
    looks = identity_write(npc, instance, mobile, &id);
    if (looks)
        player_model_rebuild(ref, instance, mobile, id.race);
    player_identity_applied++;
    log_text("net.player_identity", id.texts[0]);
    tes3x_log_hex3("net.player_class", player_classes_made, id.female, (u32)id.class_);
    tes3x_log_hex3("net.player_model", looks, player_models_rebuilt, player_worn_fixed);
}

static void player_identity_event(u8 *ref, const struct event *e)
{
    const u8 *p = e->data;
    u32 part = p[1], parts = p[2], size = e->length - 3;

    if (!parts || part >= parts || (part && part != player_identity_in_part) ||
        (part ? player_identity_in_length : 0) + size > IDENTITY_BODY) {
        player_apply_failures++;
        player_identity_in_part = 0;
        return;
    }
    if (!part)
        player_identity_in_length = 0;
    copy(player_identity_in + player_identity_in_length, p + 3, size);
    player_identity_in_length += size;
    player_identity_in_part = part + 1;
    if (player_identity_in_part == parts) {
        player_identity_in_part = 0;
        player_identity_apply(ref, player_identity_in, player_identity_in_length);
    }
}

/* Every worn stack as a WORN entry: flags, condition and charge if it has item data, id. */
#define WORN_BODY (WORN_MAX * (1 + 8 + SPAWN_ID))
static u8 worn_sent[WORN_BODY], worn_in[WORN_BODY];
static u32 worn_sent_length, worn_known, worn_in_length, worn_in_part;
static u32 worn_out, worn_applied, worn_equipped, worn_removed;

/* The body's length plus one, so an empty list reads as known; 0 when an id cannot be read. */
static u32 worn_body(const u8 *instance, u8 *out)
{
    static void *worn[WORN_MAX][2];
    const char *id;
    u32 n = worn_read(instance, worn), i, k, at = 0;

    for (i = 0; i < n; i++) {
        if (!(id = object_id(worn[i][0])))
            return 0;
        out[at++] = worn[i][1] ? ENTRY_DATA : 0;
        if (worn[i][1]) {
            put32le(out + at, *(const u32 *)((const u8 *)worn[i][1] + ITEM_CONDITION));
            put32le(out + at + 4, *(const u32 *)((const u8 *)worn[i][1] + ITEM_CHARGE));
            at += 8;
        }
        for (k = 0; id[k] && k < SPAWN_ID - 1; k++)
            out[at + k] = (u8)id[k];
        out[at + k] = 0;
        at += k + 1;
    }
    return at + 1;
}

static void worn_scan(const u8 *instance, int send)
{
    static u8 body[WORN_BODY];
    u8 data[EVENT_DATA];
    u32 n = worn_body(instance, body), parts, i, size;

    if (!n--)
        return;
    for (i = 0; worn_known && n == worn_sent_length && i < n && body[i] == worn_sent[i]; i++)
        ;
    if (worn_known && n == worn_sent_length && i == n)
        return;
    parts = n ? (n + IDENTITY_PER_EVENT - 1) / IDENTITY_PER_EVENT : 1;
    if (send) {
        if (events_room() < parts + 2) {
            snapshot_deferred++;
            return;
        }
        for (i = 0; i < parts; i++) {
            size = n - i * IDENTITY_PER_EVENT;
            size = size < IDENTITY_PER_EVENT ? size : IDENTITY_PER_EVENT;
            data[0] = PLAYER_WORN;
            data[1] = (u8)i;
            data[2] = (u8)parts;
            copy(data + 3, body + i * IDENTITY_PER_EVENT, size);
            event_queue(EVENT_PLAYER, data, 3 + size);
        }
        worn_out++;
    }
    copy(worn_sent, body, n);
    worn_sent_length = n;
    worn_known = 1;
}

/* The carried stack a WORN entry names, not yet taken: the item data with its condition and
 * charge, or none for a plain stack. 0 when the player has no such stack. */
static int worn_find(const u8 *instance, void *object, const char *id, u32 flags, u32 condition,
                     u32 charge, void **data, void *taken[][2], u32 taken_n)
{
    static struct entry e[BOX_ENTRIES];
    u32 n = contents_read(instance, e, BOX_ENTRIES, id), i;

    for (i = 0; i < n; i++)
        if (flags & ENTRY_DATA
                ? (e[i].flags & ENTRY_DATA) && e[i].condition == condition &&
                      e[i].charge == charge && !worn_has(taken, taken_n, object, e[i].data)
                : !(e[i].flags & ENTRY_DATA) && e[i].count) {
            *data = e[i].data;
            return 1;
        }
    return 0;
}

static int player_bound_item(const char *id);
static u8 *player_worn_enchantment(const char *id);
static u32 worn_pending;

/* Wear what the body lists and nothing else. */
static void worn_apply(u8 *ref, const u8 *body, u32 length)
{
    static void *want[WORN_MAX][2], *have[WORN_MAX][2];
    u8 *instance = *(u8 **)(ref + REF_BASE), *mobile = player_mobile(), *npc, *race, *object;
    const char *id;
    u32 off = 0, n = 0, m, i, k, flags, condition = 0, charge = 0, changed = 0;
    void *data;
    u8 *enchantment;

    if (!plausible(instance) || !plausible(mobile) ||
        !plausible(npc = *(u8 **)(instance + NPC_BASE)) ||
        !plausible(race = *(u8 **)(npc + NPC_RACE))) {
        player_apply_failures++;
        return;
    }
    while (off < length && n < WORN_MAX) {
        flags = body[off++];
        if (flags & ENTRY_DATA) {
            if (off + 8 > length)
                break;
            condition = get32le(body + off);
            charge = get32le(body + off + 4);
            off += 8;
        }
        id = (const char *)body + off;
        for (k = 0; off + k < length && body[off + k]; k++)
            ;
        if (off + k == length || !k || k >= SPAWN_ID)
            break;
        off += k + 1;
        if (player_bound_item(id))
            continue; /* Its effect callback recreates and equips it. */
        data = 0;
        if (!plausible(object = resolve_object(id)) ||
            !worn_find(instance, object, id, flags, condition, charge, &data, want, n)) {
            log_text("net.player_worn_missing", id);
            player_apply_failures++;
            continue;
        }
        want[n][0] = object;
        want[n++][1] = data;
    }
    m = worn_read(instance, have);
    for (i = 0; i < m; i++)
        if (!worn_has(want, n, have[i][0], have[i][1])) {
            ((fn_unequip_item)TES3X_NET_UNEQUIP_ITEM)(instance, have[i][0], 1, mobile, 0,
                                                      have[i][1]);
            worn_removed++, changed++;
        }
    for (i = 0; i < n; i++)
        if (!worn_has(have, m, want[i][0], want[i][1])) {
            if ((*(const u32 *)(race + RACE_FLAGS) & RACE_BEAST) && !beast_can_wear(want[i][0])) {
                log_text("net.player_beast_bare", object_id(want[i][0]));
                continue;
            }
            enchantment = player_worn_enchantment(object_id(want[i][0]));
            if (enchantment)
                enchantment[0x2C] = 2; /* Its retained instance supplies the constant effects. */
            ((fn_equip_item)TES3X_NET_EQUIP_ITEM)(instance, want[i][0], want[i][1], 0, mobile);
            if (enchantment)
                enchantment[0x2C] = 3;
            ((fn_mobile_call)TES3X_NET_MOBILE_HANDS)(mobile);
            worn_equipped++, changed++;
        }
    if (changed) {
        body_parts_update(ref);
        body_parts_update(*(u8 **)(mobile + PLAYER_FIRST_PERSON_REF));
    }
    worn_applied++;
    tes3x_log_hex3("net.player_worn", n, worn_equipped, worn_removed);
}

static void worn_event(u8 *ref, const struct event *e)
{
    const u8 *p = e->data;
    u32 part = p[1], parts = p[2], size = e->length - 3;

    if (!parts || part >= parts || (part && part != worn_in_part) ||
        (part ? worn_in_length : 0) + size > WORN_BODY) {
        player_apply_failures++;
        worn_in_part = 0;
        return;
    }
    if (!part)
        worn_in_length = 0;
    copy(worn_in + worn_in_length, p + 3, size);
    worn_in_length += size;
    worn_in_part = part + 1;
    if (worn_in_part == parts) {
        worn_in_part = 0;
        worn_pending = 1;
    }
}
