/* A New Game from a character file: at the [PreLoad] read the player's reference, mobile and
 * body already exist, built from the default record. The identity is written and the body
 * rebuilt, the default gear goes, the items are added and the worn list put on, all before the
 * first frame. The place picks the start cell (preload_cell_find); the rest comes with the replay
 * once joined. */
static int preload_character(void)
{
    static char path[16 + BULK_NAME];
    static void *worn[WORN_MAX][2];
    u8 *ref = (u8 *)player_reference(), *mobile = player_mobile(), *instance, head[4];
    IO_STATUS_BLOCK iosb;
    u64 offset = 0;
    struct event e;
    u32 status, n, i, records = 0, emptied = 0;
    void *h;

    *put_text(put_text(path, "U:\\TES3X\\"), game_loaded) = 0;
    if (!ref || !plausible(mobile) || !plausible(instance = *(u8 **)(ref + REF_BASE))) {
        log_text("net.preload_bad", game_loaded);
        return 0;
    }
    if ((status = bulk_open(path, GENERIC_READ, FILE_OPEN, 0, &h)) != 0) {
        tes3x_log_hex3("net.preload_file", status, 0, 0);
        return 0;
    }
    if (NtReadFile(h, 0, 0, 0, &iosb, head, 4, &offset) || iosb.Information != 4 ||
        get32le(head) != CHAR_MAGIC) {
        NtClose(h);
        log_text("net.preload_bad", game_loaded);
        return 0;
    }
    offset = 4;
    preloading = 1;
    e.seq = e.origin = 0;
    e.kind = EVENT_PLAYER;
    for (;;) {
        if (NtReadFile(h, 0, 0, 0, &iosb, head, 2, &offset) || iosb.Information != 2)
            e.length = 0;
        else
            e.length = head[0] | head[1] << 8, offset += 2;
        if (e.length > EVENT_DATA ||
            (e.length && (NtReadFile(h, 0, 0, 0, &iosb, e.data, e.length, &offset) ||
                          iosb.Information != e.length))) {
            player_apply_failures++;
            e.length = 0;
        }
        /* The identity's body rebuild re-equips the default record's gear, so it goes after */
        if (!emptied && (!e.length || e.data[0] != PLAYER_IDENTITY)) {
            emptied = 1;
            n = worn_read(instance, worn);
            for (i = 0; i < n; i++)
                ((fn_unequip_item)TES3X_NET_UNEQUIP_ITEM)(instance, worn[i][0], 1, mobile, 0,
                                                          worn[i][1]);
            /* without the mobile, whose removal re-picks equipment from what is left */
            inventory_empty(instance + OBJECT_INVENTORY);
            ((float *)(mobile + MOBILE_ENCUMBRANCE))[2] = 0.0f;
        }
        if (!e.length)
            break;
        offset += e.length;
        records++;
        if (e.data[0] == PLAYER_PLACE && e.length >= 1 + sizeof(preload_place))
            copy(preload_place, e.data + 1, sizeof(preload_place));
        else if (e.data[0] == PLAYER_IDENTITY || e.data[0] == PLAYER_ITEMS ||
                 e.data[0] == PLAYER_WORN)
            player_event(&e);
    }
    NtClose(h);
    preloading = 0;
    tes3x_log_hex3("net.preload", records, player_identity_applied, worn_applied);
    return 1;
}

/* The first world frame: the exact spot within the start cell, and what finishing chargen
 * would have done; a character from a file is long past it. */
static void preload_frame(void)
{
    u32 i;

    if (arrival_start != ARRIVAL_CLAIMED || !game_loaded[0] || preload_finished || !world_idle())
        return;
    preload_finished = 1;
    for (i = 0; i < sizeof(chargen_finish) / sizeof(*chargen_finish); i++)
        run_script(chargen_finish[i]);
    if (get32le(preload_place) & STATE_IN_WORLD)
        place_apply(preload_place);
    tes3x_log_hex3("net.preload_finished", get32le(preload_place), preload_asked, 0);
}

static void player_stat(void)
{
    tes3x_log_hex3("net.player_out", player_items_out, player_stats_out, player_journal_out);
    tes3x_log_hex3("net.player_in", player_items_in, player_stats_in, player_journal_in);
    tes3x_log_hex3("net.player_bad", player_apply_failures, player_too_many, player_mode);
    tes3x_log_hex3("net.player_effects", player_effects_out, player_effects_in, player_effects_bad);
    if (magic_controller())
        tes3x_log_hex3("net.player_effect_clock", magic_controller()[4],
                       *(u32 *)(magic_controller() + 0x10),
                       *(u32 *)(player_effect_out + 100));
    tes3x_log_hex3("net.player_spells", player_spells_out, player_spells_in,
                   player_spells_omitted);
    tes3x_log_hex3("net.player_topics", topics_out, topics_in, topics_bad);
    tes3x_log_hex3("net.player_identity_stat", player_identity_out, player_identity_applied,
                   player_classes_made);
    tes3x_log_hex3("net.player_worn_stat", worn_out, worn_applied, worn_removed);
}
