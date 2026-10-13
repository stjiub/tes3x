/* World scripts. A global script started after the first READY of a launch (by a dialogue result,
 * a quest script or a reference's script) is the world's; the ones running at that READY, the
 * data files' start scripts among them, are each console's own. Once a second a console sends the
 * world scripts it runs: RUNNING when one starts, the locals that changed (floats every tenth look
 * and at Save) and STOPPED when it ends. The server keeps each with its first runner's locals and
 * hands one nobody runs any more (its runner left, or after a restart) to a player, locals first;
 * that console starts it, unless it runs it already. A script started for a reference waits until
 * that reference can be found here. */
#define WORLD_SCRIPTS 21u /* op, reference refid, script id ending in zero, count, then entries */
#define SCRIPT_RUNNING 1u
#define SCRIPT_STOPPED 2u
#define SCRIPT_VALUES 3u
#define SCRIPT_PLAYER 1u /* as the reference: the runner's own player */
#define SCRIPT_ID 32u
#define SCRIPT_ENTRY 6u
#define SCRIPTS_BASE 256u
#define SCRIPTS_RUN 32u
#define SCRIPT_VALUES_MAX 64u
#define SCRIPT_FLOAT_LOOKS 10u
#define WORLD_SCRIPTS_LIST 0x314 /* WorldController: list of GlobalScript {reference, script} */
#define SCRIPT_HEADER_NAME 0x10
#define SCRIPT_VARS 0x5C /* short*, long*, float*, laid out as a reference's ScriptVariables */

typedef void(__attribute__((thiscall)) *fn_add_global_script)(void *world, void *script, void *ref);
typedef void(__attribute__((thiscall)) *fn_script_execute)(void *script, void *ref, void *vars,
                                                           void *info, void *ref2);
typedef u8 *(__attribute__((thiscall)) *fn_find_script)(void *records, const char *id);

struct world_script {
    const u8 *script;
    u32 refid, look, values[SCRIPT_VALUES_MAX];
    u16 n[3], total;
    u8 used, sent, assigned;
};

#define global_script_sites ((const u32[])TES3X_NET_GLOBAL_SCRIPT_SITES)
static const u8 *scripts_base[SCRIPTS_BASE];
static struct world_script scripts_run[SCRIPTS_RUN];
static const u8 *script_now; /* the global script processGlobalScripts is running */
static u32 scripts_base_count, scripts_known, scripts_looks, scripts_hooked;
static u32 scripts_out, scripts_in, scripts_started, scripts_ended, scripts_bad, scripts_full;

static void __attribute__((thiscall)) global_script_hook(u8 *script, void *ref, void *vars,
                                                         void *info, void *ref2)
{
    const u8 *was = script_now;

    script_now = script;
    ((fn_script_execute)TES3X_NET_SCRIPT_EXECUTE)(script, ref, vars, info, ref2);
    script_now = was;
}

static void scripts_hook_install(void)
{
    if (scripts_hooked)
        return;
    scripts_hooked = 1;
    if (redirect_calls(global_script_sites,
                       sizeof(global_script_sites) / sizeof(global_script_sites[0]),
                       TES3X_NET_SCRIPT_EXECUTE, (const void *)global_script_hook))
        scripts_hooked = 2;
}

static const u8 *global_scripts_head(void)
{
    const u8 *world = *(const u8 *const *)TES3X_NET_WORLD, *list;

    if (!plausible(world) || !plausible(list = *(const u8 *const *)(world + WORLD_SCRIPTS_LIST)))
        return 0;
    return *(const u8 *const *)(list + 8);
}

static int script_running(const u8 *script)
{
    const u8 *node = global_scripts_head(), *g;
    u32 guard;

    for (guard = 0; plausible(node) && guard < 1024; node = *(const u8 *const *)(node + 4), guard++)
        if (plausible(g = *(const u8 *const *)(node + 8)) && *(const u8 *const *)(g + 4) == script)
            return 1;
    return 0;
}

static int script_based(const u8 *script)
{
    u32 i;

    for (i = 0; i < scripts_base_count; i++)
        if (scripts_base[i] == script)
            return 1;
    return 0;
}

/* Whether the global script running now is the world's: its moves are this console's to send. */
static int world_script_now(void)
{
    return script_now && scripts_known && !script_based(script_now);
}

static struct world_script *script_entry(const u8 *script, int create)
{
    struct world_script *w;
    u32 i, k;

    for (i = 0; i < SCRIPTS_RUN; i++)
        if (scripts_run[i].used && scripts_run[i].script == script)
            return &scripts_run[i];
    if (!create)
        return 0;
    for (i = 0; i < SCRIPTS_RUN && scripts_run[i].used; i++)
        ;
    if (i == SCRIPTS_RUN) {
        scripts_full++;
        return 0;
    }
    w = &scripts_run[i];
    w->used = 1;
    w->script = script;
    w->refid = w->look = 0;
    w->sent = w->assigned = 0;
    w->total = 0;
    for (k = 0; k < 3; k++) {
        u32 count = *(const u32 *)(script + SCRIPT_COUNTS + 4 * k);
        w->n[k] = (u16)(count > LOCAL_INDEX ? 0 : count);
        if (count && !plausible(*(const u8 *const *)(script + SCRIPT_VARS + 4 * k)))
            w->n[k] = 0;
        w->total += w->n[k];
    }
    if (w->total > SCRIPT_VALUES_MAX) {
        scripts_bad++;
        w->total = SCRIPT_VALUES_MAX;
    }
    for (k = 0; k < SCRIPT_VALUES_MAX; k++)
        w->values[k] = 0;
    return w;
}

/* A flat value index as a LOCALS slot, or 0xFFFF past the script's counts. */
static u32 script_slot(const struct world_script *w, u32 i)
{
    u32 kind;

    for (kind = 0; kind < 3; kind++) {
        if (i < w->n[kind])
            return kind << 14 | i;
        i -= w->n[kind];
    }
    return 0xFFFF;
}

static u32 script_flat(const struct world_script *w, u32 slot)
{
    u32 kind = slot >> 14, index = slot & LOCAL_INDEX;

    if (kind > LOCAL_FLOAT || index >= w->n[kind])
        return SCRIPT_VALUES_MAX;
    return (kind > 0 ? w->n[0] : 0) + (kind > 1 ? w->n[1] : 0) + index;
}

static u32 script_get(const struct world_script *w, u32 i)
{
    u32 slot = script_slot(w, i);

    return local_get(w->script + SCRIPT_VARS, slot >> 14, slot & LOCAL_INDEX);
}

static const char *script_id(const u8 *script)
{
    const u8 *name = script + SCRIPT_HEADER_NAME;
    u32 n;

    for (n = 0; n < SCRIPT_ID && name[n]; n++)
        ;
    return n && n < SCRIPT_ID && script_safe(name, n + 1) ? (const char *)name : 0;
}

static u32 script_ref_id(const u8 *ref)
{
    if (!plausible(ref))
        return 0;
    if (ref == player_reference())
        return SCRIPT_PLAYER;
    return *(const u32 *)(ref + REF_ID);
}

/* One or more WORLD_SCRIPTS events: op with these entries (none for RUNNING and STOPPED). */
static int script_send(u32 op, const struct world_script *w, const u16 *slots, const u32 *values,
                       u32 count)
{
    const char *id = script_id(w->script);
    u8 data[EVENT_DATA];
    u32 head, per, n, i = 0, k;

    if (!id)
        return 0;
    for (head = 0; id[head]; head++)
        ;
    head += 7;
    per = (EVENT_DATA - head - 1) / SCRIPT_ENTRY;
    if (events_room() < (count + per - 1) / per + 2)
        return 0;
    data[0] = WORLD_SCRIPTS;
    data[1] = (u8)op;
    put32le(data + 2, w->refid);
    copy(data + 6, (const u8 *)id, head - 6);
    do {
        n = count - i < per ? count - i : per;
        data[head] = (u8)n;
        for (k = 0; k < n; k++) {
            data[head + 1 + k * SCRIPT_ENTRY] = (u8)slots[i + k];
            data[head + 2 + k * SCRIPT_ENTRY] = (u8)(slots[i + k] >> 8);
            put32le(data + head + 3 + k * SCRIPT_ENTRY, values[i + k]);
        }
        event_queue(EVENT_PLAYER, data, head + 1 + n * SCRIPT_ENTRY);
        i += n;
    } while (i < count);
    scripts_out++;
    return 1;
}

/* Start a script the server handed here with the locals it kept, unless it runs here already. */
static void script_start(struct world_script *w)
{
    u8 *world = *(u8 **)TES3X_NET_WORLD, *ref = 0;
    u32 i, slot;

    if (script_running(w->script)) {
        w->assigned = 0;
        w->sent = 1;
        return;
    }
    if (w->refid == SCRIPT_PLAYER)
        ref = (u8 *)player_reference();
    else if (w->refid && !(ref = actor_ref(w->refid)))
        return;
    if (!plausible(world))
        return;
    for (i = 0; i < w->total; i++) {
        slot = script_slot(w, i);
        local_set((u8 *)w->script + SCRIPT_VARS, slot >> 14, slot & LOCAL_INDEX, w->values[i]);
    }
    ((fn_add_global_script)TES3X_NET_ADD_GLOBAL_SCRIPT)(world, (void *)w->script, ref);
    w->assigned = 0;
    w->sent = 1;
    scripts_started++;
    log_text("net.script_started", script_id(w->script));
}

/* Game thread, after READY: the world scripts that started, changed or ended since last look. */
static void world_scripts_scan(int send, int floats_now)
{
    static u16 slots[SCRIPT_VALUES_MAX];
    static u32 changed[SCRIPT_VALUES_MAX];
    const u8 *node = global_scripts_head(), *g, *script;
    struct world_script *w;
    u32 guard, i, n, v, look = ++scripts_looks;
    u32 floats = floats_now || look % SCRIPT_FLOAT_LOOKS == 0;

    for (guard = 0; plausible(node) && guard < 1024;
         node = *(const u8 *const *)(node + 4), guard++) {
        if (!plausible(g = *(const u8 *const *)(node + 8)) ||
            !plausible(script = *(const u8 *const *)(g + 4)))
            continue;
        if (!(w = script_entry(script, 0))) {
            if (!scripts_known) {
                if (scripts_base_count < SCRIPTS_BASE)
                    scripts_base[scripts_base_count++] = script;
                continue;
            }
            if (script_based(script))
                continue;
            if (!script_id(script) || !(w = script_entry(script, 1))) {
                scripts_bad++;
                continue;
            }
            w->refid = script_ref_id(*(const u8 *const *)g);
            for (i = 0; i < w->total; i++)
                w->values[i] = script_get(w, i);
            log_text("net.script_runs", script_id(script));
        }
        w->look = look;
        w->assigned = 0;
        if (!send)
            continue;
        n = 0;
        if (!w->sent) {
            for (i = 0; i < w->total; i++)
                if ((w->values[i] = script_get(w, i))) {
                    slots[n] = (u16)script_slot(w, i);
                    changed[n++] = w->values[i];
                }
            if (!script_send(SCRIPT_RUNNING, w, 0, 0, 0) ||
                (n && !script_send(SCRIPT_VALUES, w, slots, changed, n))) {
                snapshot_deferred++;
                continue;
            }
            w->sent = 1;
            continue;
        }
        for (i = 0; i < w->total; i++) {
            v = script_get(w, i);
            if (v == w->values[i] || (script_slot(w, i) >> 14 == LOCAL_FLOAT && !floats))
                continue;
            slots[n] = (u16)script_slot(w, i);
            changed[n++] = v;
        }
        if (!n)
            continue;
        if (!script_send(SCRIPT_VALUES, w, slots, changed, n)) {
            snapshot_deferred++;
            continue;
        }
        for (i = 0; i < n; i++)
            w->values[script_flat(w, slots[i])] = changed[i];
    }
    scripts_known = 1;
    for (i = 0; i < SCRIPTS_RUN; i++) {
        w = &scripts_run[i];
        if (!w->used || w->look == look)
            continue;
        if (!w->sent) {
            if (w->assigned)
                script_start(w);
            continue;
        }
        if (!send)
            continue;
        if (!script_send(SCRIPT_STOPPED, w, 0, 0, 0)) {
            snapshot_deferred++;
            continue;
        }
        log_text("net.script_ended", script_id(w->script));
        scripts_ended++;
        w->used = 0;
    }
}

/* From the server: VALUES for a script it is about to hand here, then RUNNING. */
static void world_script_event(const u8 *data, u32 length)
{
    void *records = faction_records();
    struct world_script *w;
    u8 *script;
    u32 end, i, count, flat;

    for (end = 6; end < length && data[end]; end++)
        ;
    if (end == length || end == 6 || end - 6 >= SCRIPT_ID || end + 2 > length ||
        length != end + 2 + data[end + 1] * SCRIPT_ENTRY || !records ||
        !script_safe(data + 6, end - 5)) {
        scripts_bad++;
        return;
    }
    script = ((fn_find_script)TES3X_NET_FIND_SCRIPT)(records, (const char *)data + 6);
    if (!plausible(script) || !(w = script_entry(script, 1))) {
        scripts_bad++;
        log_text("net.script_unknown", (const char *)data + 6);
        return;
    }
    scripts_in++;
    w->refid = get32le(data + 2);
    if (data[1] == SCRIPT_VALUES && !script_running(script)) {
        count = data[end + 1];
        for (i = 0; i < count; i++) {
            flat = script_flat(w, data[end + 2 + i * SCRIPT_ENTRY] |
                                  (u32)data[end + 3 + i * SCRIPT_ENTRY] << 8);
            if (flat < w->total)
                w->values[flat] = get32le(data + end + 4 + i * SCRIPT_ENTRY);
        }
    } else if (data[1] == SCRIPT_RUNNING) {
        w->assigned = 1;
        script_start(w);
    }
}

/* tes3xnet script ID [VAR [VALUE]]: 1 if the script runs, or one of its locals, optionally set. */
static void script_query(const char *text)
{
    void *records = faction_records();
    char id[SCRIPT_ID];
    const char *const *names;
    const u8 *script;
    u32 i, t, value, set = 0, n[3];
    float shown;

    for (i = 0; text[i] && text[i] != ' ' && i < SCRIPT_ID - 1; i++)
        id[i] = text[i];
    id[i] = 0;
    text = skip(text + i);
    script = records ? ((fn_find_script)TES3X_NET_FIND_SCRIPT)(records, id) : 0;
    if (!plausible(script)) {
        log_text("net.script_unknown", id);
        return;
    }
    if (!*text) {
        tes3x_log_hex3("net.script", script_running(script), script_based(script),
                       script_entry(script, 0) != 0);
        if (HAS_CONSOLE)
            tes3x_console_reply("%s >> %.2f", id, (double)script_running(script));
        return;
    }
    for (i = 0; text[i] && text[i] != ' '; i++)
        ;
    if (text[i] && number(skip(text + i), &value))
        set = 1;
    for (t = 0; t < 3; t++)
        n[t] = *(const u32 *)(script + SCRIPT_COUNTS + 4 * t);
    for (t = 0; t < 3; t++) {
        names = *(const char *const *const *)(script + SCRIPT_NAMES + 4 * t);
        for (i = 0; plausible(names) && i < n[t] && i <= LOCAL_INDEX; i++) {
            u32 k;
            if (!mapped(names[i]))
                continue;
            for (k = 0; names[i][k] && (names[i][k] | 32) == (text[k] | 32); k++)
                ;
            if (names[i][k] || (text[k] && text[k] != ' '))
                continue;
            if (set) {
                shown = (float)value;
                local_set((u8 *)script + SCRIPT_VARS, t, i, t == LOCAL_FLOAT ? get32le(
                          (const u8 *)&shown) : value);
            }
            value = local_get(script + SCRIPT_VARS, t, i);
            if (t == LOCAL_FLOAT)
                copy((u8 *)&shown, (const u8 *)&value, 4);
            else
                shown = (float)(int)value;
            tes3x_log_hex3("net.script_local", t << 14 | i, value, set);
            if (HAS_CONSOLE)
                tes3x_console_reply("%s.%s >> %.2f", id, names[i], (double)shown);
            (void)shown;
            return;
        }
    }
    log_text("net.script_local_unknown", text);
}

static void world_scripts_stat(void)
{
    u32 i, n = 0;

    for (i = 0; i < SCRIPTS_RUN; i++)
        n += scripts_run[i].used;
    tes3x_log_hex3("net.scripts", scripts_base_count, n, scripts_out);
    tes3x_log_hex3("net.scripts_in", scripts_in, scripts_started, scripts_ended);
    tes3x_log_hex3("net.scripts_bad", scripts_bad, scripts_full, scripts_hooked);
}
