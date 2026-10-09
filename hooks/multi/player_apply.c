static void player_set(u8 *ref, const char *what, const char *name, int value)
{
    char line[48], *p = put_text(put_text(line, what), name);

    p = put_int(put_text(p, " "), value);
    *p = 0;
    run_script_on(line, ref);
}

/* Commands update dependent fields; direct base writes retain fractional damage. */
static void level_apply(u8 *ref, const u8 *body)
{
    static const u32 stats[3] = {MOBILE_HEALTH_STAT, MOBILE_MAGICKA_STAT, MOBILE_FATIGUE_STAT};
    u8 *mobile = player_mobile(), *npc = player_npc(ref);
    float value, *stat;
    u32 i;
    int level = body[0] | body[1] << 8;

    if (!plausible(mobile) || !npc || !float_within(body + 15, 3 + ATTRIBUTES, STAT_LIMIT)) {
        player_apply_failures++;
        return;
    }
    if (*(const short *)(npc + NPC_LEVEL) != level)
        player_set(ref, "SetLevel", "", level);
    for (i = 0; i < ATTRIBUTES; i++) {
        copy((u8 *)&value, body + 27 + 4 * i, 4);
        if (*(const float *)(mobile + MOBILE_ATTRIBUTES + 0xC * i + STAT_BASE) != value)
            player_set(ref, "Set", attribute_names[i], round_int(value));
        copy(mobile + MOBILE_ATTRIBUTES + 0xC * i + STAT_BASE, body + 27 + 4 * i, 4);
        replay_stat_keep(i, 0, body + 27 + 4 * i);
    }
    for (i = 0; i < 3; i++) {
        stat = (float *)(mobile + stats[i]);
        copy((u8 *)&stat[1], body + 15 + 4 * i, 4);
        if (stat[2] > stat[1])
            stat[2] = stat[1];
    }
    *(int *)(mobile + PLAYER_LEVEL_PROGRESS) = body[2] | body[3] << 8;
    for (i = 0; i < 11; i++)
        ((int *)(mobile + PLAYER_LEVELUPS))[i] = body[4 + i];
    player_stats_in++;
}

static void skills_apply(u8 *ref, const u8 *p, u32 length)
{
    u8 *mobile = player_mobile();
    u32 i, skill;
    float base;

    if (!plausible(mobile)) {
        player_apply_failures++;
        return;
    }
    for (i = 0; i < p[1] && 2 + (i + 1) * SKILL_BYTES <= length; i++) {
        skill = p[2 + i * SKILL_BYTES];
        if (skill >= SKILLS || !float_within(p + 3 + i * SKILL_BYTES, 2, STAT_LIMIT))
            continue;
        copy((u8 *)&base, p + 3 + i * SKILL_BYTES, 4);
        if (*(const float *)(mobile + MOBILE_SKILLS + 0x10 * skill + STAT_BASE) != base)
            player_set(ref, "Set", skill_names[skill], round_int(base));
        copy(mobile + MOBILE_SKILLS + 0x10 * skill + STAT_BASE, p + 3 + i * SKILL_BYTES, 4);
        replay_stat_keep(ATTRIBUTES + skill, 0, p + 3 + i * SKILL_BYTES);
        copy(mobile + PLAYER_SKILL_PROGRESS + 4 * skill, p + 7 + i * SKILL_BYTES, 4);
    }
    player_stats_in++;
}

static void modifiers_apply(const u8 *p, u32 length)
{
    u8 *mobile = player_mobile(), *stat;
    u32 i, index;

    if (!plausible(mobile)) {
        player_apply_failures++;
        return;
    }
    for (i = 0; i < p[1] && 2 + (i + 1) * MODIFIER_BYTES <= length; i++) {
        index = p[2 + i * MODIFIER_BYTES];
        if (index >= MODIFIERS || !float_within(p + 3 + i * MODIFIER_BYTES, 1, STAT_LIMIT))
            continue;
        stat = mobile + (index < ATTRIBUTES ? MOBILE_ATTRIBUTES + 0xC * index
                                             : MOBILE_SKILLS + 0x10 * (index - ATTRIBUTES));
        copy(stat + STAT_BASE + 4, p + 3 + i * MODIFIER_BYTES, 4);
        replay_stat_keep(index, 1, p + 3 + i * MODIFIER_BYTES);
    }
    player_stats_in++;
}

/* Through ModCurrent*, which keeps the HUD in step. Death is not the stream's to cause: health
 * stays at 1 or more. */
static void vitals_apply(u8 *ref, const u8 *body)
{
    static const char *const names[3] = {"Health", "Magicka", "Fatigue"};
    static const u32 current[3] = {MOBILE_HEALTH, MOBILE_MAGICKA, MOBILE_FATIGUE};
    u8 *mobile = player_mobile();
    float want;
    u32 i;
    int delta;

    if (!plausible(mobile) || !float_within(body, 3, STAT_LIMIT)) {
        player_apply_failures++;
        return;
    }
    for (i = 0; i < 3; i++) {
        copy((u8 *)&want, body + 4 * i, 4);
        if (i == 0 && want < 1.0f)
            want = 1.0f;
        if ((delta = round_int(want - *(const float *)(mobile + current[i]))) != 0)
            player_set(ref, "ModCurrent", names[i], delta);
    }
    player_stats_in++;
}

/* The checkpoint was made somewhere else: go where the server last saw the player, so a power cut
 * is no way out of a place. */
static void place_apply(const u8 *body)
{
    char line[160], *q;
    u32 flags = get32le(body);
    float heading;

    if (!(flags & STATE_IN_WORLD) || !float_within(body + 4, 3, POSITION_LIMIT) ||
        !float_within(body + 16, 1, ANGLE_LIMIT) ||
        ((flags & STATE_INTERIOR) && !script_safe(body + 20, CELL_NAME))) {
        player_apply_failures++;
        return;
    }
    copy((u8 *)&heading, body + 16, 4);
    q = put_xyz(put_text(line, "Player->PositionCell "), (const float *)(body + 4));
    q = put_int(put_text(q, " "), round_int(heading * (180.0f / PI)));
    q = put_text(q, " \"");
    q = put_text(q, flags & STATE_INTERIOR ? interior_name(body + 20) : GHOST_EXTERIOR);
    q = put_text(q, "\"");
    *q = 0;
    run_script(line);
    log_text("net.player_place", flags & STATE_INTERIOR ? interior_name(body + 20) : "(exterior)");
    player_stats_in++;
}

static void player_spell_command(u8 *ref, const char *verb, const char *id)
{
    char line[64], *p = put_text(put_text(put_text(line, verb), " \""), id);

    *put_text(p, "\"") = 0;
    run_script_on(line, ref);
}

static void player_bounty_apply(const u8 *body)
{
    u8 *mobile = player_mobile();
    char line[48];
    int bounty = (int)get32le(body);

    if (!plausible(mobile) || bounty < 0) {
        player_apply_failures++;
        return;
    }
    if (((fn_get_bounty)TES3X_NET_GET_BOUNTY)(mobile) != bounty) {
        *put_int(put_text(line, "SetPCCrimeLevel "), bounty) = 0;
        run_script(line);
    }
    bounty_sent = bounty;
    player_bounty_replayed = ses.welcomes;
    tes3x_log("net.player_bounty", (u32)bounty);
}

/* Reconcile the checkpoint's list with the server's absolute snapshot. */
static void player_spells_apply(u8 *ref)
{
    u8 *npc = player_npc(ref);
    const char *id;
    u32 count, i;

    if (!npc) {
        player_apply_failures++;
        return;
    }
    count = player_spells_read(npc, player_spells_now);
    for (i = 0; i < count; i++) {
        id = player_spell_id(player_spells_now[i]);
        if (id && !spell_name_in(id, player_spells_stage, player_spells_stage_count))
            player_spell_command(ref, "RemoveSpell", id);
    }
    for (i = 0; i < player_spells_stage_count; i++) {
        u32 k;
        for (k = 0; k < count; k++) {
            id = player_spell_id(player_spells_now[k]);
            if (id && same_name(id, player_spells_stage[i]))
                break;
        }
        if (k == count)
            player_spell_command(ref, "AddSpell", player_spells_stage[i]);
    }
    player_spells_in++;
    tes3x_log("net.player_spells_applied", player_spells_stage_count);
}

static void player_spells_event(u8 *ref, const struct event *e)
{
    const u8 *p = e->data;
    u32 part = p[2], parts = p[3], off = 4, start, n;

    if (p[1] != SPELLS_SNAPSHOT || !parts || part >= parts ||
        (part == 0 ? 0 : part != player_spells_part)) {
        player_apply_failures++;
        return;
    }
    if (!part)
        player_spells_stage_count = player_spells_part = 0;
    while (off < e->length) {
        start = off;
        while (off < e->length && p[off])
            off++;
        n = off - start;
        if (off == e->length || !n || n >= SPAWN_ID ||
            !script_safe(p + start, n + 1) || player_spells_stage_count == PLAYER_SPELLS_MAX) {
            player_apply_failures++;
            player_spells_part = 0;
            return;
        }
        copy((u8 *)player_spells_stage[player_spells_stage_count], p + start, n + 1);
        if (!spell_name_in(player_spells_stage[player_spells_stage_count], player_spells_stage,
                           player_spells_stage_count))
            player_spells_stage_count++;
        off++;
    }
    player_spells_part = part + 1;
    if (player_spells_part == parts) {
        player_spells_apply(ref);
        player_spells_replayed = ses.welcomes;
        player_spells_part = 0;
    }
}
