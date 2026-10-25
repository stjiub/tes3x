#define PLAYER_MARK 22u
#define MARK_NONE 0u
#define MARK_INTERIOR 1u
#define MARK_EXTERIOR 2u
#define MARK_HEAD 26u /* kind, flags, grid x, grid y, position (3 f32), heading; then the name */
#define MOBILE_MARK 0x678 /* MarkData: cell +0, position +4, heading +0x10; 0x14 bytes */
#define MARK_SIZE 0x14
#define MARK_NAME 64u /* an interior's whole name; STATE's CELL_NAME cuts it at 31 */

static u8 mark_sent[MARK_HEAD + MARK_NAME];
static u32 mark_sent_length, mark_known, mark_out, mark_in, mark_bad;

/* The Mark spell's place as an event body; its length. */
static u32 mark_read(const u8 *mobile, u8 *out)
{
    const u8 *mark = *(const u8 *const *)(mobile + MOBILE_MARK), *cell, *name;
    u32 i;

    for (i = 0; i < MARK_HEAD + MARK_NAME; i++)
        out[i] = 0;
    out[0] = PLAYER_MARK;
    if (!plausible(mark) || !plausible(cell = *(const u8 *const *)mark))
        return MARK_HEAD + 1;
    if (*(const u32 *)(cell + CELL_FLAGS) & CELL_INTERIOR) {
        if (!mapped(name = *(const u8 *const *)(cell + CELL_NAME_PTR)) || !name[0])
            return MARK_HEAD + 1;
        for (i = 0; i < MARK_NAME - 1 && name[i]; i++)
            out[MARK_HEAD + i] = name[i];
        out[1] = MARK_INTERIOR;
    } else {
        copy(out + 2, cell + CELL_GRID_X, 4);
        copy(out + 6, cell + CELL_GRID_Y, 4);
        out[1] = MARK_EXTERIOR;
    }
    copy(out + 10, mark + 4, 16);
    return MARK_HEAD + 1 + tes3x_strlen((const char *)out + MARK_HEAD);
}

/* Sent when it changes. Unsent, a scan only takes the baseline. */
static void mark_scan(const u8 *mobile, int send)
{
    u8 now[MARK_HEAD + MARK_NAME];
    u32 length = mark_read(mobile, now), i;

    if (mark_known && length == mark_sent_length) {
        for (i = 0; i < length && now[i] == mark_sent[i]; i++)
            ;
        if (i == length)
            return;
    }
    if (send && !event_queue(EVENT_PLAYER, now, length)) {
        snapshot_deferred++;
        return;
    }
    copy(mark_sent, now, length);
    mark_sent_length = length;
    mark_known = 1;
    mark_out += send;
}

static const u8 *mark_cell(u32 flags, int gx, int gy, const u8 *name)
{
    const u8 *node, *cell, *cname;
    u32 i, guard;

    for (node = cells_head(), guard = 0; plausible(node) && guard < 65536;
         node = *(const u8 *const *)(node + 8), guard++) {
        if (!plausible(cell = *(const u8 *const *)node))
            continue;
        if (flags == MARK_EXTERIOR) {
            if (!(*(const u32 *)(cell + CELL_FLAGS) & CELL_INTERIOR) &&
                *(const int *)(cell + CELL_GRID_X) == gx && *(const int *)(cell + CELL_GRID_Y) == gy)
                return cell;
        } else if ((*(const u32 *)(cell + CELL_FLAGS) & CELL_INTERIOR) &&
                   mapped(cname = *(const u8 *const *)(cell + CELL_NAME_PTR))) {
            for (i = 0; cname[i] && cname[i] == name[i]; i++)
                ;
            if (!cname[i] && !name[i])
                return cell;
        }
    }
    return 0;
}

/* The engine's mark, as a save load writes it: a null mark is how it means none. */
static int mark_write(u8 *mobile, const u8 *cell, const u8 *at)
{
    u8 *mark = *(u8 **)(mobile + MOBILE_MARK);

    if (!cell) {
        *(u8 **)(mobile + MOBILE_MARK) = 0;
        return 1;
    }
    if (!plausible(mark)) {
        mark = ((fn_engine_allocate)TES3X_NET_ENGINE_ALLOCATE)((void *)TES3X_NET_HEAP, MARK_SIZE,
                                                               "tes3xmulti.c", 0);
        if (!plausible(mark))
            return 0;
        *(u8 **)(mobile + MOBILE_MARK) = mark;
    }
    *(const u8 **)mark = cell;
    copy(mark + 4, at, 16);
    return 1;
}

/* A mark in a cell this build lacks is left as it is. */
static void mark_apply(u8 *mobile, const u8 *data, u32 length)
{
    const u8 *cell = 0;
    u32 flags;

    if (length < MARK_HEAD + 1 || length > MARK_HEAD + MARK_NAME || (flags = data[1]) > MARK_EXTERIOR ||
        !script_safe(data + MARK_HEAD, length - MARK_HEAD) ||
        !float_within(data + 10, 3, POSITION_LIMIT) || !float_within(data + 22, 1, ANGLE_LIMIT) ||
        (flags == MARK_INTERIOR && !data[MARK_HEAD])) {
        mark_bad++;
        return;
    }
    if (flags != MARK_NONE &&
        !(cell = mark_cell(flags, (int)get32le(data + 2), (int)get32le(data + 6), data + MARK_HEAD))) {
        mark_bad++;
        log_text("net.mark_cell_unknown", flags == MARK_INTERIOR ? (const char *)data + MARK_HEAD :
                                                                   "(exterior)");
        return;
    }
    if (!mark_write(mobile, cell, data + 10)) {
        mark_bad++;
        return;
    }
    copy(mark_sent, data, length);
    mark_sent_length = length;
    mark_known = 1;
    mark_in++;
    tes3x_log("net.mark_applied", flags);
}

/* tes3xnet markhere: what the Mark spell does, without sending; a scan then finds the change. */
static void mark_here(void)
{
    const u8 *ref = player_reference(), *handler = *(const u8 *const *)TES3X_NET_DATA_HANDLER, *cell;
    u8 *mobile = player_mobile(), at[16];
    float x, y;

    if (!plausible(ref) || !plausible(mobile) || !plausible(handler))
        return;
    copy(at, ref + REF_POSITION, 12);
    copy(at + 12, ref + 0x34, 4);
    copy((u8 *)&x, at, 4);
    copy((u8 *)&y, at + 4, 4);
    cell = *(const u8 *const *)(handler + 0xAC);
    if (!plausible(cell))
        cell = mark_cell(MARK_EXTERIOR, (int)(x / 8192.0f) - (x < 0.0f), (int)(y / 8192.0f) - (y < 0.0f), 0);
    if (cell && mark_write(mobile, cell, at))
        tes3x_log("net.mark_here", 1);
}

static void player_mark_event(const struct event *e)
{
    u8 *mobile = player_mobile();

    if (plausible(mobile))
        mark_apply(mobile, e->data, e->length);
    else
        mark_bad++;
}

/* tes3xnet mark [x|y|z|gx|gy]: the kind (0 none, 1 interior, 2 exterior) or one number of the mark as
 * the engine has it, a position in hundreds (an assert reads only 7 characters); an interior's name
 * is logged. */
static void mark_query(const char *what)
{
    u8 *mobile = player_mobile(), now[MARK_HEAD + MARK_NAME];
    float value = now[1];

    if (!plausible(mobile))
        return;
    mark_read(mobile, now);
    value = now[1];
    if (word(what, "x") || word(what, "y") || word(what, "z"))
        copy((u8 *)&value, now + 10 + 4 * (what[0] - 'x'), 4), value /= 100.0f;
    else if (word(what, "gx") || word(what, "gy"))
        value = (float)(int)get32le(now + 2 + 4 * (what[1] - 'x'));
    tes3x_log_hex3("net.mark", now[1], get32le(now + 2), get32le(now + 6));
    if (now[1] == MARK_INTERIOR)
        log_text("net.mark_cell", (const char *)now + MARK_HEAD);
    if (HAS_CONSOLE)
        tes3x_console_reply("mark >> %.2f", (double)value);
}
