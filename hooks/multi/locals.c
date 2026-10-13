/* Script locals of data-file references. A reference's script variables hang off its item data
 * (attachment kind 6: script +0x14, ScriptVariables +0x18 with short, long and float arrays sized
 * by the script's counts). Once a second the references of the active cells are read; the
 * authority of a reference (its cell's, or the actor's owner) sends what changed since the last
 * read, and any console sends a reference its own player's dialogue result ran on. Floats, mostly
 * timers, are compared every tenth read. Values from the server are mirrored here and written
 * when a reference is first read (a new reference or new variables: its cell loaded) and as they
 * arrive for one already read, which then counts as read so it is not sent back. */
#define EVENT_LOCALS 37u /* refid, count, then (slot u16, value u32) */
#define LOCAL_ENTRY 6u
#define LOCALS_PER_EVENT ((EVENT_DATA - 5) / LOCAL_ENTRY)
#define LOCAL_FLOAT 2u
#define LOCAL_INDEX 0x3FFFu
#define LOCAL_REFS 256u
#define LOCAL_VALUES 2048u
#define LOCALS_KNOWN 1024u
#define LOCALS_TOUCHED 8u
#define LOCALS_POLL_US 1000000u
#define LOCAL_FLOAT_READS 10u
#define ATTACH_VARIABLES 6u
#define ITEM_SCRIPT 0x14
#define ITEM_VARIABLES 0x18
#define SCRIPT_COUNTS 0x30 /* shorts, longs, floats */
#define SCRIPT_NAMES 0x44  /* char ** per kind */

struct local_ref {
    u32 refid;
    const u8 *ref, *vars;
    u16 at, n[3];
};

static struct local_ref local_refs[2][LOCAL_REFS];
static u32 local_values[2][LOCAL_VALUES], local_ref_count[2], local_current;
static struct {
    u32 refid, value;
    u16 slot;
    u8 fresh;
} locals_known[LOCALS_KNOWN];
static u32 locals_known_count, locals_touched[LOCALS_TOUCHED], locals_touched_count;
static u32 locals_polled, locals_reads, locals_out, locals_in, locals_applied, locals_dropped;
static u8 locals_state[STATE_BYTES];
static u32 locals_state_valid;

/* A player's script ran on this reference: its changes go out even from a non-authority. */
static void locals_touched_ref(const u8 *ref)
{
    u32 refid = plausible(ref) ? *(const u32 *)(ref + REF_ID) : 0, i;

    if (!refid)
        return;
    for (i = 0; i < locals_touched_count; i++)
        if (locals_touched[i] == refid)
            return;
    if (locals_touched_count < LOCALS_TOUCHED)
        locals_touched[locals_touched_count++] = refid;
}

static u8 *ref_variables(const u8 *ref, const u8 **script, u16 *n)
{
    const u8 *att, *data;
    u8 *vars;
    u32 guard, k;

    for (att = *(const u8 *const *)(ref + REF_ATTACHMENTS), guard = 0; plausible(att) && guard < 32;
         att = *(const u8 *const *)(att + 4), guard++) {
        if (*(const u32 *)att != ATTACH_VARIABLES)
            continue;
        if (!plausible(data = *(const u8 *const *)(att + 8)) ||
            !plausible(*script = *(const u8 *const *)(data + ITEM_SCRIPT)) ||
            !plausible(vars = *(u8 *const *)(data + ITEM_VARIABLES)))
            return 0;
        for (k = 0; k < 3; k++) {
            u32 count = *(const u32 *)(*script + SCRIPT_COUNTS + 4 * k);
            if (count > LOCAL_INDEX || (count && !plausible(*(u8 *const *)(vars + 4 * k))))
                return 0;
            n[k] = (u16)count;
        }
        return n[0] || n[1] || n[2] ? vars : 0;
    }
    return 0;
}

static u32 local_get(const u8 *vars, u32 kind, u32 index)
{
    const u8 *array = *(const u8 *const *)(vars + 4 * kind);

    return kind ? get32le(array + 4 * index) : (u32)(int)*(const short *)(array + 2 * index);
}

static void local_set(u8 *vars, u32 kind, u32 index, u32 value)
{
    u8 *array = *(u8 *const *)(vars + 4 * kind);

    if (kind)
        put32le(array + 4 * index, value);
    else
        *(short *)(array + 2 * index) = (short)value;
}

/* The first mirrored value of refid, or where it would go. */
static u32 known_find(u32 refid, u32 slot)
{
    u32 lo = 0, hi = locals_known_count, mid;

    while (lo < hi) {
        mid = (lo + hi) / 2;
        if (locals_known[mid].refid < refid ||
            (locals_known[mid].refid == refid && locals_known[mid].slot < slot))
            lo = mid + 1;
        else
            hi = mid;
    }
    return lo;
}

static void known_set(u32 refid, u32 slot, u32 value, u32 fresh)
{
    u32 i = known_find(refid, slot), k;

    if (i == locals_known_count || locals_known[i].refid != refid || locals_known[i].slot != slot) {
        if (locals_known_count == LOCALS_KNOWN) {
            locals_dropped++;
            return;
        }
        for (k = locals_known_count++; k > i; k--)
            locals_known[k] = locals_known[k - 1];
        locals_known[i].refid = refid;
        locals_known[i].slot = (u16)slot;
    }
    locals_known[i].value = value;
    locals_known[i].fresh = (u8)fresh;
}

/* Write the mirrored values of one reference (only those not yet written unless all), and into
 * its last read values (base) so they do not count as changes here. */
static void locals_apply(u32 refid, u8 *vars, const u16 *n, int all, u32 *base)
{
    u32 i, kind, index;

    for (i = known_find(refid, 0); i < locals_known_count && locals_known[i].refid == refid; i++) {
        if (!all && !locals_known[i].fresh)
            continue;
        locals_known[i].fresh = 0;
        kind = locals_known[i].slot >> 14;
        index = locals_known[i].slot & LOCAL_INDEX;
        if (kind > LOCAL_FLOAT || index >= n[kind]) {
            locals_dropped++;
            continue;
        }
        local_set(vars, kind, index, locals_known[i].value);
        if (base)
            base[(kind > 0 ? n[0] : 0) + (kind > 1 ? n[1] : 0) + index] = locals_known[i].value;
        locals_applied++;
        if (locals_applied <= 32)
            tes3x_log_hex3("net.local_applied", refid, locals_known[i].slot, locals_known[i].value);
    }
}

static int locals_send(u32 refid, const u16 *slots, const u32 *values, u32 count)
{
    u8 data[EVENT_DATA];
    u32 i, n, k;

    if (events_room() < (count + LOCALS_PER_EVENT - 1) / LOCALS_PER_EVENT + 2)
        return 0;
    for (i = 0; i < count; i += n) {
        n = count - i < LOCALS_PER_EVENT ? count - i : LOCALS_PER_EVENT;
        put32le(data, refid);
        data[4] = (u8)n;
        for (k = 0; k < n; k++) {
            data[5 + k * LOCAL_ENTRY] = (u8)slots[i + k];
            data[6 + k * LOCAL_ENTRY] = (u8)(slots[i + k] >> 8);
            put32le(data + 7 + k * LOCAL_ENTRY, values[i + k]);
        }
        event_queue(EVENT_LOCALS, data, 5 + n * LOCAL_ENTRY);
    }
    for (i = 0; i < count; i++)
        known_set(refid, slots[i], values[i], 0);
    locals_out += count;
    return 1;
}

/* One read of the active cells' scripted references into the other buffer. */
static void locals_scan(int floats_now)
{
    static const u32 lists[2] = {0x2C, 0x3C};
    static u16 slots[LOCAL_VALUES];
    static u32 changed[LOCAL_VALUES];
    struct local_ref *prev = local_refs[local_current], *next = local_refs[!local_current], *old;
    u32 *before = local_values[local_current], *now = local_values[!local_current];
    u8 *cells[ACTIVE_CELLS], *ref, *vars;
    const u8 *temporary, *script;
    struct pose local;
    struct cell_key key;
    u32 indices[ACTIVE_CELLS], cn, c, l, i, k, t, refid, count = 0, used = 0, total, touched, mine;
    u32 floats = floats_now || ++locals_reads % LOCAL_FLOAT_READS == 0, nchanged;
    u16 n[3];

    if (!locals_state_valid)
        return;
    read_pose(locals_state, &local);
    cn = active_cells(cells, indices);
    for (c = 0; c < cn; c++)
        for (l = 0; l < 3; l++) {
            if (l < 2)
                ref = *(u8 **)(cells[c] + lists[l] + LIST_HEAD);
            else if (plausible(temporary = *(const u8 *const *)(cells[c] + CELL_TEMPORARY)))
                ref = *(u8 *const *)(temporary + 8 + LIST_HEAD);
            else
                break;
            for (; plausible(ref); ref = *(u8 **)(ref + REF_NEXT)) {
                if (!(refid = *(const u32 *)(ref + REF_ID)) || is_ghost(ref) ||
                    !(vars = ref_variables(ref, &script, n)))
                    continue;
                total = (u32)n[0] + n[1] + n[2];
                if (count == LOCAL_REFS || used + total > LOCAL_VALUES) {
                    locals_dropped++;
                    continue;
                }
                for (i = 0, old = 0; i < local_ref_count[local_current]; i++)
                    if (prev[i].refid == refid) {
                        old = &prev[i];
                        break;
                    }
                if (old && (old->ref != ref || old->vars != vars || old->n[0] != n[0] ||
                            old->n[1] != n[1] || old->n[2] != n[2]))
                    old = 0;
                locals_apply(refid, vars, n, !old, old ? before + old->at : 0);
                for (t = 0, k = 0; t < 3; t++)
                    for (i = 0; i < n[t]; i++)
                        now[used + k++] = local_get(vars, t, i);
                if (old) {
                    actor_key(&local, *(const float *)(ref + 0x38), *(const float *)(ref + 0x3C),
                              &key);
                    for (i = 0, touched = 0; i < locals_touched_count; i++)
                        touched |= locals_touched[i] == refid;
                    mine = touched || actor_owner(refid, authority_of(&key)) == ses.client;
                    nchanged = 0;
                    for (t = 0, k = 0; t < 3; t++)
                        for (i = 0; i < n[t]; i++, k++) {
                            if (now[used + k] == before[old->at + k])
                                continue;
                            if (t == LOCAL_FLOAT && !floats && !touched) {
                                now[used + k] = before[old->at + k];
                                continue;
                            }
                            slots[nchanged] = (u16)(t << 14 | i);
                            changed[nchanged++] = now[used + k];
                        }
                    if (nchanged && mine && !locals_send(refid, slots, changed, nchanged)) {
                        snapshot_deferred++;
                        for (k = 0; k < total; k++)
                            now[used + k] = before[old->at + k];
                    }
                }
                next[count].refid = refid;
                next[count].ref = ref;
                next[count].vars = vars;
                next[count].at = (u16)used;
                copy((u8 *)next[count].n, (const u8 *)n, sizeof(n));
                count++;
                used += total;
            }
        }
    local_ref_count[!local_current] = count;
    local_current = !local_current;
    locals_touched_count = 0;
}

static void locals_event(const struct event *e)
{
    const u8 *p = e->data;
    u32 refid, i, slot;

    if (e->length < 5 || e->length != 5 + p[4] * LOCAL_ENTRY || !(refid = get32le(p)))
        return;
    for (i = 0; i < p[4]; i++) {
        slot = p[5 + i * LOCAL_ENTRY] | (u32)p[6 + i * LOCAL_ENTRY] << 8;
        if (slot >> 14 > LOCAL_FLOAT)
            continue;
        known_set(refid, slot, get32le(p + 7 + i * LOCAL_ENTRY), 1);
        locals_in++;
    }
}

/* Game thread, in the world. */
static void locals_frame(const u8 *state)
{
    u32 now = now_us();

    copy(locals_state, state, STATE_BYTES);
    locals_state_valid = 1;
    if (ses.state != SESSION_JOINED || now - locals_polled < LOCALS_POLL_US)
        return;
    locals_polled = now;
    locals_scan(0);
}

/* tes3xnet local ID NAME: a script local of a reference in the active cells, by object id or by
 * refid as 0x hex (an actor's base is its instance, whose id does not match). */
static void local_query(const char *text)
{
    static const u32 lists[2] = {0x2C, 0x3C};
    char id[SPAWN_ID];
    u8 *cells[ACTIVE_CELLS], *ref, *vars;
    const u8 *temporary, *script, *object;
    const char *base, *const *names;
    u32 indices[ACTIVE_CELLS], cn, c, l, i, t, value, refid = 0, digit;
    u16 n[3];
    float shown;

    for (i = 0; text[i] && text[i] != ' ' && i < SPAWN_ID - 1; i++)
        id[i] = text[i];
    id[i] = 0;
    text = skip(text + i);
    if (id[0] == '0' && (id[1] | 32) == 'x')
        for (i = 2; id[i]; i++) {
            digit = (u32)(id[i] | 32);
            refid = refid << 4 | (digit <= '9' ? digit - '0' : digit - 'a' + 10);
        }
    cn = active_cells(cells, indices);
    for (c = 0; c < cn; c++)
        for (l = 0; l < 3; l++) {
            if (l < 2)
                ref = *(u8 **)(cells[c] + lists[l] + LIST_HEAD);
            else if (plausible(temporary = *(const u8 *const *)(cells[c] + CELL_TEMPORARY)))
                ref = *(u8 *const *)(temporary + 8 + LIST_HEAD);
            else
                break;
            for (; plausible(ref); ref = *(u8 **)(ref + REF_NEXT)) {
                if (refid ? *(const u32 *)(ref + REF_ID) != refid
                          : !plausible(object = *(const u8 *const *)(ref + REF_BASE)) ||
                                !(base = object_id(object)) || !same_name(base, id))
                    continue;
                if (!(vars = ref_variables(ref, &script, n)))
                    continue;
                for (t = 0; t < 3; t++) {
                    names = *(const char *const *const *)(script + SCRIPT_NAMES + 4 * t);
                    for (i = 0; plausible(names) && i < n[t]; i++)
                        if (mapped(names[i]) && same_name(names[i], text)) {
                            value = local_get(vars, t, i);
                            tes3x_log_hex3("net.local", *(const u32 *)(ref + REF_ID), t << 14 | i,
                                           value);
                            if (t == LOCAL_FLOAT)
                                copy((u8 *)&shown, (const u8 *)&value, 4);
                            else
                                shown = (float)(int)value;
                            if (HAS_CONSOLE)
                                tes3x_console_reply("%s.%s >> %.2f", id, text, (double)shown);
                            (void)shown;
                            return;
                        }
                }
            }
        }
    log_text("net.local_unknown", id);
}

static void locals_stat(void)
{
    tes3x_log_hex3("net.locals", locals_out, locals_in, locals_applied);
    tes3x_log_hex3("net.locals_table", local_ref_count[local_current], locals_known_count,
                   locals_dropped);
}
