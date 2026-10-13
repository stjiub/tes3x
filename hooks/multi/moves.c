/* Actor moves. Position and PositionCell move a data-file actor through relocateReference; the
 * move goes out as ACTOR_MOVE when it is this console's to share: its player's dialogue result or
 * console, a world script it runs, or another script moving an actor no other console runs into
 * another cell (every console with the cell, or the global script, makes that move too). Each
 * console keeps the latest move of each actor and makes it once it finds the actor in the cell it
 * left, the one an earlier move put it in, or the one it names: a persistent actor at once,
 * another when that cell is loaded. A move within a cell by another script reaches the others
 * through ACTORS. */
#define EVENT_ACTOR_MOVE 39u /* refid, cell left u16, cell u16, x, y, z, rotation as relocated */
#define MOVE_BYTES 24u
#define MOVES 128u
#define MOVE_ROTATION_LIMIT 0x45610000u /* 3600 */

typedef void(__cdecl *fn_relocate)(void *ref, void *cell, const float *position, float rotation);

static const u32 relocate_sites[] = TES3X_NET_RELOCATE_SITES;
static struct {
    u32 refid;
    u8 *from, *to, *last; /* last: the cell a move put it in here */
    float p[4];
    u8 pending, made; /* made: this move was made here; a cell change does not repeat it */
} moves[MOVES];
static u32 move_count, move_applying, moves_hooked, moves_signature;
static u32 moves_out, moves_in, moves_applied, moves_full, moves_found_there;

static u8 *ref_cell(const u8 *ref)
{
    const u8 *list = *(const u8 *const *)(ref + REF_LIST);

    return plausible(list) ? *(u8 *const *)(list + 0xC) : 0;
}

static int actor_followed(u32 refid)
{
    u32 i;

    for (i = 0; i < ACTORS; i++)
        if (followed[i].refid == refid && followed[i].held)
            return 1;
    return 0;
}

static void __cdecl relocate_hook(u8 *ref, u8 *cell, const float *position, float rotation)
{
    u8 data[MOVE_BYTES], *from = plausible(ref) ? ref_cell(ref) : 0;
    const u8 *base;
    u32 refid, tag, to_index;

    ((fn_relocate)TES3X_NET_RELOCATE)(ref, cell, position, rotation);
    if (move_applying || ses.state != SESSION_JOINED || player_mode != 3 || !plausible(ref) ||
        !plausible(cell) || !plausible(position) || ref == player_reference() || is_ghost(ref) ||
        !(refid = *(const u32 *)(ref + REF_ID)) ||
        !plausible(base = *(const u8 *const *)(ref + REF_BASE)))
        return;
    tag = *(const u32 *)(base + 4);
    if (tag != TAG_NPC && tag != TAG_CREA)
        return;
    if (!player_acting && !world_script_now() && (cell == from || actor_followed(refid)))
        return;
    if ((to_index = cell_index(cell)) == OBJECT_NO_CELL)
        return;
    put32le(data, refid);
    put32le(data + 4, (plausible(from) ? cell_index(from) : OBJECT_NO_CELL) | to_index << 16);
    copy(data + 8, (const u8 *)position, 12);
    copy(data + 20, (const u8 *)&rotation, 4);
    if (!event_queue(EVENT_ACTOR_MOVE, data, MOVE_BYTES)) {
        tes3x_log("net.event_full", EVENT_ACTOR_MOVE);
        return;
    }
    moves_out++;
    tes3x_log_hex3("net.move_sent", refid, to_index, (u32)round_int(position[0]));
}

static void moves_hook_install(void)
{
    if (moves_hooked)
        return;
    moves_hooked = 1;
    if (redirect_calls(relocate_sites, sizeof(relocate_sites) / sizeof(relocate_sites[0]),
                       TES3X_NET_RELOCATE, (const void *)relocate_hook))
        moves_hooked = 2;
}

/* A cell's reference with this refid: its persistent lists, and its temporary one if loaded. */
static u8 *move_reference(u8 *cell, u32 refid)
{
    static const u32 lists[2] = {0x2C, 0x3C};
    const u8 *temporary;
    u8 *ref;
    u32 i;

    for (i = 0; i < 3; i++) {
        if (i < 2)
            ref = *(u8 **)(cell + lists[i] + LIST_HEAD);
        else if ((*(const u32 *)(cell + CELL_FLAGS) & CELL_REFS_LOADED) &&
                 plausible(temporary = *(const u8 *const *)(cell + CELL_TEMPORARY)))
            ref = *(u8 *const *)(temporary + 8 + LIST_HEAD);
        else
            break;
        for (; plausible(ref); ref = *(u8 **)(ref + REF_NEXT))
            if (*(const u32 *)(ref + REF_ID) == refid)
                return ref;
    }
    return 0;
}

/* Make a kept move if the actor is where it was, or in the cell the move names but not yet put
 * there by it (the data files may start it in that cell); 1 once it is where the move put it. */
static int move_try(u32 i)
{
    u8 *target = moves[i].to, *ref = 0, *at[2] = {moves[i].last, moves[i].from};
    u32 k;

    if (!plausible(target))
        return 0;
    for (k = 0; k < 2 && !ref; k++)
        if (plausible(at[k]) && at[k] != target)
            ref = move_reference(at[k], moves[i].refid);
    if (!ref) {
        if (!(ref = move_reference(target, moves[i].refid)))
            return 0;
        moves[i].last = moves[i].to;
        if (moves[i].made)
            return 1;
        moves_found_there++;
    }
    move_applying = 1;
    ((fn_relocate)TES3X_NET_RELOCATE)(ref, target, moves[i].p, moves[i].p[3]);
    move_applying = 0;
    moves[i].last = moves[i].to;
    moves[i].made = 1;
    moves_applied++;
    tes3x_log_hex3("net.move_applied", moves[i].refid, cell_index(target),
                   (u32)round_int(moves[i].p[0]));
    return 1;
}

static void moves_event(const struct event *e)
{
    u32 refid, i;

    if (e->length != MOVE_BYTES || !(refid = get32le(e->data)) ||
        !float_within(e->data + 8, 3, POSITION_LIMIT) ||
        !float_within(e->data + 20, 1, MOVE_ROTATION_LIMIT))
        return;
    for (i = 0; i < move_count && moves[i].refid != refid; i++)
        ;
    if (i == move_count) {
        if (move_count == MOVES) {
            moves_full++;
            return;
        }
        move_count++;
        moves[i].refid = refid;
        moves[i].last = 0;
    }
    moves[i].from = cell_at(e->data[4] | (u32)e->data[5] << 8);
    moves[i].to = cell_at(e->data[6] | (u32)e->data[7] << 8);
    copy((u8 *)moves[i].p, e->data + 8, 16);
    moves[i].made = 0;
    moves_in++;
    moves[i].pending = !move_try(i);
}

/* Game thread, in the world: after a cell change, the moves of actors found where they were. */
static void moves_frame(void)
{
    const u8 *handler = *(const u8 *const *)TES3X_NET_DATA_HANDLER;
    u32 signature = 0, i;

    if (ses.state != SESSION_JOINED || !move_count)
        return;
    if (plausible(handler))
        signature = *(const u32 *)(handler + 0xAC) ^ *(const u32 *)(handler + 0xA0) * 31 ^
                    *(const u32 *)(handler + 0xA4) * 1009;
    if (signature == moves_signature)
        return;
    moves_signature = signature;
    for (i = 0; i < move_count; i++)
        moves[i].pending = !move_try(i);
}

/* The server replays its moves after each WELCOME. */
static void moves_session(void)
{
    static u32 welcome;

    if (welcome == ses.welcomes)
        return;
    welcome = ses.welcomes;
    move_count = 0;
    moves_signature = 0;
}

/* tes3xnet where 0xREFID: the x of a data-file reference in whatever cell holds it, loaded or
 * not (the console's own lookup does not find every one). */
static void where_query(const char *text)
{
    const u8 *node = cells_head();
    u32 refid = 0, digit, i;
    u8 *ref;
    float x;

    if (text[0] != '0' || (text[1] | 32) != 'x')
        return;
    for (i = 2; text[i] && text[i] != ' '; i++) {
        digit = (u32)(text[i] | 32);
        refid = refid << 4 | (digit <= '9' ? digit - '0' : digit - 'a' + 10);
    }
    for (i = 0; plausible(node) && i < OBJECT_NO_CELL; i++, node = *(const u8 *const *)(node + 8))
        if ((ref = move_reference(*(u8 *const *)node, refid))) {
            copy((u8 *)&x, ref + REF_POSITION, 4);
            tes3x_log_hex3("net.where", refid, i, (u32)round_int(x));
#ifdef TES3X_CONSOLE
            tes3x_console_reply("%s >> %.2f", text, (double)x);
#endif
            return;
        }
    tes3x_log("net.where_unknown", refid);
}

static void moves_stat(void)
{
    u32 i, pending = 0;

    for (i = 0; i < move_count; i++)
        pending += moves[i].pending;
    tes3x_log_hex3("net.moves", moves_out, moves_in, moves_applied);
    tes3x_log_hex3("net.moves_table", move_count, pending, moves_full | moves_found_there << 16);
}
