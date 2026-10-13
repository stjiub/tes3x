typedef char(__attribute__((thiscall)) *fn_journal_add)(void *dialogue, int index, void *actor);

/* A quest only moves forward. The replay before READY adds entries without the "journal updated"
 * toast the Journal command shows; another player's progress later shows it. */
static void journal_apply(const u8 *p, u32 length)
{
    char line[64], *out;
    u8 *node, *dialogue = 0, *mobile;
    const char *id, *name;
    u32 off = 2, i, k, guard, max;
    int index, quiet = player_mode != 3 || player_welcome != ses.welcomes;

    for (i = 0; i < p[1] && off + 3 <= length; i++) {
        index = p[off] | p[off + 1] << 8;
        id = (const char *)p + off + 2;
        max = length - off - 2 < SPAWN_ID ? length - off - 2 : SPAWN_ID;
        if (!script_safe(p + off + 2, max))
            return;
        for (off += 2; p[off]; off++)
            ;
        off++;
        for (node = (u8 *)dialogues_head(), guard = k = 0; plausible(node) && guard < 65536;
             node = *(u8 **)(node + 4), guard++) {
            if (!plausible(dialogue = *(u8 **)(node + 8)) ||
                dialogue[DIALOGUE_TYPE] != DIALOGUE_JOURNAL)
                continue;
            if ((name = dialogue_name(dialogue)) && same_name(name, id))
                break;
            k++;
        }
        if (!plausible(node)) {
            player_apply_failures++;
            log_text("net.player_quest_unknown", id);
            continue;
        }
        if (*(const short *)(dialogue + DIALOGUE_INDEX) >= index)
            continue;
        if (quiet && plausible(mobile = player_mobile())) {
            ((fn_journal_add)TES3X_NET_JOURNAL_ADD)(dialogue, index, mobile);
        } else {
            out = put_int(put_text(put_text(put_text(line, "Journal \""), id), "\" "), index);
            *out = 0;
            run_script(line);
        }
        if (k < JOURNALS) /* not sent back */
            journal_sent[k] = (u16)*(const short *)(dialogue + DIALOGUE_INDEX);
        player_journal_in++;
    }
}

static void player_items_event(u8 *ref, const struct event *e)
{
    const u8 *p = e->data;
    u32 off, k, max = e->length - 3 < SPAWN_ID ? e->length - 3 : SPAWN_ID;

    if (e->length < 4 || !script_safe(p + 3, max))
        return;
    if (p[1] == 0) {
        player_in_count = player_in_part = 0;
        for (k = 0; p[3 + k]; k++)
            player_in_id[k] = (char)p[3 + k];
        player_in_id[k] = 0;
    } else if (p[1] != player_in_part || !same_id((const char *)p + 3, player_in_id)) {
        return;
    }
    player_in_part++;
    for (off = 3; p[off]; off++)
        ;
    off++;
    while (off + 5 <= e->length && player_in_count < BOX_ENTRIES) {
        struct entry *x = &player_in[player_in_count];
        x->count = (int)get32le(p + off);
        x->flags = p[off + 4] & ENTRY_DATA;
        x->condition = x->charge = 0;
        off += 5;
        if (x->flags & ENTRY_DATA) {
            if (off + 8 > e->length)
                break;
            x->condition = get32le(p + off);
            x->charge = get32le(p + off + 4);
            off += 8;
        }
        for (k = 0; player_in_id[k]; k++)
            x->id[k] = player_in_id[k];
        x->id[k] = 0;
        player_in_count++;
    }
    if (player_in_part != p[2])
        return;
    if (inventory_apply(ref, player_in, player_in_count, player_in_id, player_mobile()))
        player_items_in++;
    else
        player_apply_failures++;
    log_text("net.player_item", player_in_id);
}

static void player_event(const struct event *e)
{
    u8 *ref = (u8 *)player_reference();

    if (e->length < 1)
        return;
    if (e->origin && (e->data[0] == PLAYER_DEATH || e->data[0] == PLAYER_ALIVE)) {
        ghost_life_event(e);
        return;
    }
    if (e->data[0] == PLAYER_READY) {
        player_effect_session();
        replay_stat_session();
        if (replay_stat_known[0][0] || replay_stat_known[0][1] ||
            replay_stat_known[1][0] || replay_stat_known[1][1] || player_effect_pending)
            replay_stat_wait = 4;
        if (worn_pending && ref) {
            worn_apply(ref, worn_in, worn_in_length);
            worn_pending = 0;
        }
        player_mode = e->length >= 2 && e->data[1] ? 2 : 1;
        player_welcome = ses.welcomes;
        return;
    }
    if (!ref) {
        player_apply_failures++;
        return;
    }
    if (e->data[0] == PLAYER_ITEMS)
        player_items_event(ref, e);
    else if (e->data[0] == PLAYER_LEVEL && e->length >= 1 + LEVEL_BYTES)
        level_apply(ref, e->data + 1);
    else if (e->data[0] == PLAYER_SKILLS && e->length >= 2)
        skills_apply(ref, e->data, e->length);
    else if (e->data[0] == PLAYER_MODIFIERS && e->length >= 2)
        modifiers_apply(e->data, e->length);
    else if (e->data[0] == PLAYER_EFFECTS && e->length >= 5)
        player_effect_event(e);
    else if (e->data[0] == PLAYER_JOURNAL && e->length >= 2)
        journal_apply(e->data, e->length);
    else if (e->data[0] == PLAYER_TOPICS && e->length >= 2)
        topics_apply(e->data, e->length);
    else if (e->data[0] == PLAYER_FACTIONS && e->length >= 2)
        factions_apply(e->data, e->length);
    else if (e->data[0] == PLAYER_GLOBALS && e->length >= 2)
        globals_apply(&player_globals, e->data, e->length);
    else if (e->data[0] == WORLD_GLOBALS && e->length >= 2)
        globals_apply(&world_globals, e->data, e->length);
    else if (e->data[0] == PLAYER_VITALS && e->length >= 1 + 12)
        vitals_apply(ref, e->data + 1);
    else if (e->data[0] == PLAYER_PLACE && e->length >= 1 + PLACE_BYTES)
        place_apply(e->data + 1);
    else if (e->data[0] == PLAYER_SPELLS && e->length >= 4)
        player_spells_event(ref, e);
    else if (e->data[0] == PLAYER_BOUNTY && e->length >= 5)
        player_bounty_apply(e->data + 1);
    else if (e->data[0] == PLAYER_IDENTITY && e->length >= 4)
        player_identity_event(ref, e);
    else if (e->data[0] == PLAYER_WORN && e->length >= 3)
        worn_event(ref, e);
    else if (e->data[0] == PLAYER_RESPAWN && e->length >= 1 + 9)
        respawn_event(e->data + 1);
}
