/* Ghosts: each peer slot drives one persistent NPC of the ghost plugin (tes3x net plugin),
 * moved into a cell through the engine's script compiler, as the console does, and within it by
 * writing its position. Each is drawn GHOST_DELAY_US in the past, between the two states around
 * that moment, so jitter and a lost state do not show. */
#define GHOST_DELAY_US 100000
#define GHOST_EXTRAPOLATE_US 200000
#define GHOST_SNAP 1024.0f /* a longer step between two states is a teleport, not a walk */
#define GHOST_CELL "TES3X Ghosts"
/* Any exterior cell's name: PositionCell then finds the exterior cell from the coordinates.
 * Position alone leaves a ghost that has never been loaded outside the loaded cells. */
#define GHOST_EXTERIOR "Seyda Neen"
#define GHOST_SETTLE_FRAMES 10 /* after the local player changes cell */
#define WORLD_SCRIPT 0x54 /* the compiler CompileAndRun is a method on */
#define WORLD_MENUS 0x2C0
#define MENUS_SCRATCH 0x20
#define REF_NODE 0x10
#define REF_ORIENTATION 0x2C
#define REF_POSITION 0x38
#define NODE_ROTATION 0x2C /* NiAVObject's rotation pointer, then its translation */
#define NODE_TRANSLATE 0x30
#define PI 3.14159265f
#define GROUP_IDLE 0u
#define GROUP_NONE 0xFFu
#define GROUP_LOOPS 100000 /* LoopGroup's count: until the next group */
#define MOBILE_SCRIPTED 0x10000000u /* PlayGroup's mark: the group is not the AI's to change */

typedef int(__attribute__((thiscall)) *fn_compile_run)(void *self, void *scratch,
                                                       const char *text, int a2, int ref,
                                                       int a4, int a5, int a6);
typedef u8 *(__attribute__((thiscall)) *fn_find_reference)(void *records, const char *id);
typedef float *(__attribute__((thiscall)) *fn_ref_rotation)(void *ref, float *matrix, int a1);
typedef void(__attribute__((thiscall)) *fn_node_set_rotation)(void *slot, const float *matrix);
typedef void(__attribute__((thiscall)) *fn_node_update)(void *node, float time, int a1, int a2);
typedef u8(__attribute__((thiscall)) *fn_has_group)(void *animation, int group);
typedef void(__attribute__((thiscall)) *fn_play_group)(void *animation, int group, int layer,
                                                       int flags, int loops);

struct pose {
    u32 flags;
    float x, y, z, heading;
    u8 cell[CELL_NAME];
    u8 anim[ANIM_BYTES];
};

/* A received position, heading and animation and when it arrived. */
struct timed {
    u32 time, flags;
    float p[4];
    u8 anim[ANIM_BYTES];
};

static struct {
    u32 client, placed, flags;
    int gx, gy;
    float x, y, z, heading;
    u8 cell[CELL_NAME];
    u8 *ref;
    u32 identity;      /* the identity generation applied to its base NPC */
    u32 identity_seen; /* the generation whose first apply failure was logged */
    u32 look;  /* the equipment generation it wears */
    u32 armed; /* its health is set to GHOST_HEALTH: a drop from there is a hit */
    u32 dead;  /* 0 alive, 1 dead, 2 not yet reconciled with the server */
} ghosts[PEERS];
static u32 ghosts_parked, ghost_settle;
static struct {
    u32 client, dead;
} ghost_lives[PEERS];
static u32 ghost_deaths, ghost_respawns;
__attribute__((weak)) int _fltused; /* tes3xscript.c may define it too */

/* With ref, the text runs on that reference, as on the console's selected one. */
static void run_script_on(const char *text, void *ref)
{
    u8 *world = *(u8 **)TES3X_NET_WORLD, *menus;
    void *script, *scratch;

    if (!plausible(world) || !plausible(script = *(void **)(world + WORLD_SCRIPT)) ||
        !plausible(menus = *(u8 **)(world + WORLD_MENUS)) ||
        !plausible(scratch = *(void **)(menus + MENUS_SCRATCH))) {
        ghost_failures++;
        return;
    }
    ((fn_compile_run)TES3X_NET_COMPILE_RUN)(script, scratch, text, 1, (int)ref, 0, 0, 0);
}

static void run_script(const char *text)
{
    run_script_on(text, 0);
}

/* Messages on screen, one at a time, as the engine's own transient messages. A held message is
 * shown again each time it expires, so the player can still walk and open the pause menu. */
#define NOTICES 4u
#define NOTICE_LENGTH 96u
#define NOTICE_SHOW_US 3500000u
static char notice_text[NOTICES][NOTICE_LENGTH];
static u32 notice_head, notice_count, notice_shown;
static char notice_held[NOTICE_LENGTH];

static void notice(const char *text)
{
    char *slot;
    u32 i;

    if (notice_count == NOTICES) {
        notice_head = (notice_head + 1) % NOTICES;
        notice_count--;
    }
    slot = notice_text[(notice_head + notice_count++) % NOTICES];
    for (i = 0; text[i] && i < NOTICE_LENGTH - 1; i++)
        slot[i] = text[i];
    slot[i] = 0;
}

/* A message that stays until released, and comes before the queued ones. */
static void notice_hold(const char *text)
{
    u32 i;

    for (i = 0; text[i] && i < NOTICE_LENGTH - 1; i++)
        notice_held[i] = text[i];
    notice_held[i] = 0;
    notice_holding = 1;
}

static void notice_release(void)
{
    notice_holding = 0;
    notice_shown = now_us() - NOTICE_SHOW_US;
}

static void notice_frame(void)
{
    static u32 menu_id;
    u8 *menu;
    u32 visible;

    if (!menu_id)
        menu_id = ((fn_ui_id)TES3X_NET_UI_ID)("MenuMessage");
    menu = ((fn_find_menu)TES3X_NET_FIND_MENU)(menu_id);
    visible = menu && menu[MENU_VISIBLE];
    if (visible || !player_reference())
        return;
    if (notice_holding) {
        if (now_us() - notice_shown >= NOTICE_SHOW_US) {
            ((void(__cdecl *)(const char *, int, int))TES3X_NET_SHOW_MESSAGE)(notice_held, 0, 1);
            notice_shown = now_us();
        }
    } else if (notice_count && now_us() - notice_shown >= NOTICE_SHOW_US) {
        /* the engine's own transient message: off to the side, expires itself, holds no input */
        ((void(__cdecl *)(const char *, int, int))TES3X_NET_SHOW_MESSAGE)(
            notice_text[notice_head], 0, 1);
        notice_head = (notice_head + 1) % NOTICES;
        notice_count--;
        notice_shown = now_us();
    }
}

static char *put_text(char *out, const char *text)
{
    while (*text)
        *out++ = *text++;
    return out;
}

static char *put_int(char *out, int v)
{
    char digits[12];
    u32 n = 0, u = v < 0 ? (u32)-v : (u32)v;

    if (v < 0)
        *out++ = '-';
    do
        digits[n++] = (char)('0' + u % 10);
    while (u /= 10);
    while (n)
        *out++ = digits[--n];
    return out;
}

static int round_int(float f)
{
    return (int)(f < 0 ? f - 0.5f : f + 0.5f);
}

static char *put_xyz(char *out, const float *xyz)
{
    out = put_int(out, round_int(xyz[0]));
    out = put_int(put_text(out, " "), round_int(xyz[1]));
    return put_int(put_text(out, " "), round_int(xyz[2]));
}

/* "tes3x_ghostN"-> for slot i */
static char *put_ghost(char *out, u32 i)
{
    out = put_int(put_text(out, "\"tes3x_ghost"), (int)i + 1);
    return put_text(out, "\"->");
}

static void ghost_command(u32 i, const char *verb, int value, const char *tail)
{
    char line[96];
    char *p = put_text(put_int(put_text(put_ghost(line, i), verb), value), tail);

    *p = 0;
    run_script(line);
}

static void ghost_action(u32 i, const char *verb)
{
    char line[64];
    char *p = put_text(put_ghost(line, i), verb);

    *p = 0;
    run_script(line);
}

static void ghost_park(u32 i)
{
    ghost_command(i, "PositionCell ", 128 * ((int)i + 1), " 0 0 0 \"" GHOST_CELL "\"");
    ghosts[i].placed = ghosts[i].armed = 0;
}

static int grid(float f)
{
    int g = (int)(f / 8192.0f);
    return f < 0 && (float)g * 8192.0f != f ? g - 1 : g;
}

static float wrap_angle(float a)
{
    while (a > PI)
        a -= 2 * PI;
    while (a < -PI)
        a += 2 * PI;
    return a;
}

/* SetPos and SetAngle z as their handlers do them: the position on the reference and its node,
 * the heading in the reference's orientation attachment and as the node's rotation, and a node
 * update unless the reference has animation data (attachment kind 0), as actors do. NPCs build
 * that rotation from the reference's own orientation rather than the attachment's, so the heading
 * goes there too. */
static void place_ref(u8 *ref, const float *xyzh)
{
    u8 *node = *(u8 **)(ref + REF_NODE);
    float matrix[12], heading = wrap_angle(xyzh[3]);

    if (heading < 0)
        heading += 2 * PI;
    copy(ref + REF_POSITION, (const u8 *)xyzh, 12);
    *(float *)(ref + REF_ORIENTATION + 8) = heading;
    ((float *)((fn_ref_part)TES3X_NET_REF_ORIENTATION)(ref))[2] = heading;
    if (!plausible(node))
        return;
    copy(node + NODE_TRANSLATE, (const u8 *)xyzh, 12);
    ((fn_node_set_rotation)TES3X_NET_NODE_SET_ROTATION)(
        node + NODE_ROTATION, ((fn_ref_rotation)TES3X_NET_REF_ROTATION)(ref, matrix, 1));
    if (!((fn_ref_part)TES3X_NET_REF_ANIMATION)(ref))
        ((fn_node_update)TES3X_NET_NODE_UPDATE)(node, 0.0f, 0, 1);
}

/* Ghosts and followed actors are placed, not simulated, so the engine never animates them by
 * itself; they mirror the animation their source sends. When a layer's group changes it is
 * played as LoopGroup plays it and the mobile marked as PlayGroup marks it; every frame the key
 * and the time are set between the two states the actor is drawn between (frac of the way), and
 * the engine's update poses the actor there. */
static void anim_apply(u8 *ref, const u8 *older, const u8 *newer, float frac)
{
    u8 *a = ref_animation(ref), *mobile = ref_mobile(ref);
    u32 l, g, keys;
    int layer;
    u8 *sequence;
    float from, to;

    if (!plausible(a))
        return;
    if (plausible(mobile))
        *(u32 *)(mobile + MOBILE_FLAGS) |= MOBILE_SCRIPTED;
    for (l = 0; l < ANIM_LAYERS; l++) {
        if ((g = newer[l]) == GROUP_NONE)
            continue;
        if (g >= ANIM_GROUP_COUNT || !float_within(newer + 8 + 4 * l, 1, ANIM_TIME_LIMIT)) {
            refused_anims++;
            continue;
        }
        if (a[ANIM_GROUP + l] != g) {
            if (!((fn_has_group)TES3X_NET_ANIM_HAS_GROUP)(a, (int)g))
                continue;
            ((fn_play_group)TES3X_NET_ANIM_PLAY_GROUP)(a, (int)g, (int)l,
                anim_start_mode(g, newer[4 + l]), GROUP_LOOPS);
            if (a[ANIM_GROUP + l] != g)
                continue;
        }
        if (!anim_keys(a, g, &keys) || newer[4 + l] >= keys) {
            refused_anims++;
            continue;
        }
        copy((u8 *)&to, newer + 8 + 4 * l, 4);
        if (older[l] == g && older[4 + l] == newer[4 + l]) {
            copy((u8 *)&from, older + 8 + 4 * l, 4);
            if (from <= to)
                to = from + (to - from) * frac;
        }
        *(u32 *)(a + ANIM_KEY + 4 * l) = newer[4 + l];
        *(u32 *)(a + ANIM_LOOPS + 4 * l) = GROUP_LOOPS;
        *(float *)(a + ANIM_TIMING + 4 * l) = to;
    }
    /* Held mobiles take the frozen update path, which does not set upper/arm offsets. */
    for (l = 1; l < ANIM_LAYERS; l++) {
        layer = *(const int *)(a + ANIM_SEQUENCE_LAYERS + 4 * l);
        if (layer < 0 || layer >= (int)ANIM_GROUP_COUNT ||
            !plausible(sequence = *(u8 **)(a + ANIM_SEQUENCES + 12 * layer + 4 * l)))
            continue;
        *(float *)(sequence + SEQUENCE_TIME_OFFSET) =
            *(const float *)(a + ANIM_TIMING + 4 * l) - *(const float *)(a + ANIM_TIMING);
    }
}

/* Idle on every layer, as PlayGroup Idle: the actor's own animation takes over again. */
static void anim_release(u8 *ref)
{
    u8 *a = ref_animation(ref), *mobile = ref_mobile(ref);
    int l;

    if (plausible(mobile))
        *(u32 *)(mobile + MOBILE_FLAGS) &= ~MOBILE_SCRIPTED;
    if (plausible(a))
        for (l = 0; l < (int)ANIM_LAYERS; l++)
            ((fn_play_group)TES3X_NET_ANIM_PLAY_GROUP)(a, GROUP_IDLE, l, 1, -1);
}

/* Whether a placed reference stands more than a unit or 0.01 rad off xyzh. */
static int off_pose(const u8 *ref, const float *xyzh)
{
    const float *at = (const float *)(ref + REF_POSITION);
    float turn = wrap_angle(xyzh[3] - *(const float *)(ref + REF_ORIENTATION + 8));
    u32 i;

    for (i = 0; i < 3; i++)
        if (at[i] - xyzh[i] > 1 || xyzh[i] - at[i] > 1)
            return 1;
    return turn > 0.01f || turn < -0.01f;
}

/* The pose delay microseconds ago from n samples, oldest first: between the two around that
 * moment, carried on past the newest for up to GHOST_EXTRAPOLATE_US, or held at one sample when
 * there is no pair or the pair lies too far apart to be a walk. Returns the index of the newer
 * sample of the pair, or of the sample held; frac is how far between the pair, at most 1. */
static u32 track_pose(const struct timed *s, u32 n, u32 delay, float *out, float *frac)
{
    int target = (int)(now_us() - delay), from = -1, span, into;
    u32 i;
    float t, dx, dy;

    for (i = 0; i < n; i++)
        if ((int)(s[i].time - (u32)target) <= 0)
            from = (int)i;
    *frac = 1;
    if (from < 0 || n == 1) {
        i = from < 0 ? 0 : (u32)from;
        copy((u8 *)out, (const u8 *)s[i].p, 16);
        return i;
    }
    i = (u32)from + 1 < n ? (u32)from : n - 2;
    copy((u8 *)out, (const u8 *)s[i + 1].p, 16);
    span = (int)(s[i + 1].time - s[i].time);
    dx = s[i + 1].p[0] - s[i].p[0];
    dy = s[i + 1].p[1] - s[i].p[1];
    if (span <= 0 || dx * dx + dy * dy > GHOST_SNAP * GHOST_SNAP)
        return i + 1;
    into = target - (int)s[i].time;
    if (into > span + GHOST_EXTRAPOLATE_US)
        into = span + GHOST_EXTRAPOLATE_US;
    t = (float)into / (float)span;
    *frac = t < 0 ? 0 : t > 1 ? 1 : t;
    out[0] = s[i].p[0] + dx * t;
    out[1] = s[i].p[1] + dy * t;
    out[2] = s[i].p[2] + (s[i + 1].p[2] - s[i].p[2]) * t;
    out[3] = s[i].p[3] + wrap_angle(s[i + 1].p[3] - s[i].p[3]) * (t > 1 ? 1 : t);
    return i + 1;
}

static int is_ghost(const u8 *ref)
{
    const u8 *base = *(const u8 *const *)(ref + 0x28);
    const char *id, *g = "tes3x_ghost";

    if (!plausible(base) || !mapped(id = *(const char *const *)(base + 0x2C)))
        return 0;
    for (; *g && *id == *g; id++, g++)
        ;
    return !*g;
}

/* tes3x_ghostN's reference for slot i, looked up once per launch. */
static u8 *ghost_ref(u32 i)
{
    const u8 *handler = *(const u8 **)TES3X_NET_DATA_HANDLER;
    void *records;
    char id[16];

    if (!ghosts[i].ref && plausible(handler) && plausible(records = *(void **)handler)) {
        *put_int(put_text(id, "tes3x_ghost"), (int)i + 1) = 0;
        ghosts[i].ref = ((fn_find_reference)TES3X_NET_FIND_REFERENCE)(records, id);
        if (ghosts[i].ref)
            tes3x_log_hex3("net.ghost_ref", i + 1, (u32)ghosts[i].ref, 0);
    }
    if (plausible(ghosts[i].ref) && is_ghost(ghosts[i].ref))
        return ghosts[i].ref;
    ghost_failures++;
    return 0;
}

static void read_pose(const u8 *state, struct pose *p)
{
    p->flags = get32le(state);
    copy((u8 *)&p->x, state + 4, 16);
    copy(p->cell, state + 20, CELL_NAME);
    p->cell[CELL_NAME - 1] = 0;
    copy(p->anim, state + STATE_ANIM, ANIM_BYTES);
}

static int same_place(const struct pose *a, const struct pose *b)
{
    u32 i;

    if ((a->flags ^ b->flags) & (STATE_IN_WORLD | STATE_INTERIOR))
        return 0;
    for (i = 0; i < CELL_NAME - 1 && a->cell[i] && a->cell[i] == b->cell[i]; i++)
        ;
    return a->cell[i] == b->cell[i];
}

/* The slot's pose GHOST_DELAY_US ago, from a copy of its states, with the older state's
 * animation and how far past it; 0 if it has none. */
static int ghost_pose(u32 slot, struct pose *out, u8 *older, float *frac)
{
    static struct {
        u32 time;
        u8 state[STATE_BYTES];
    } ring[SAMPLES];
    struct pose poses[SAMPLES];
    struct timed track[SAMPLES];
    float at[4];
    u32 head, count, flags, i, k;

    flags = lock();
    head = peers[slot].head;
    count = peers[slot].count;
    copy((u8 *)ring, (const u8 *)peers[slot].samples, sizeof(ring));
    unlock(flags);
    if (!count)
        return 0;
    for (i = 0; i < count; i++) {
        k = (head + SAMPLES - count + i) % SAMPLES;
        read_pose(ring[k].state, &poses[i]);
        track[i].time = ring[k].time;
        copy((u8 *)track[i].p, (const u8 *)&poses[i].x, 16);
    }
    i = track_pose(track, count, GHOST_DELAY_US, at, frac);
    *out = poses[i];
    copy(older, poses[i ? i - 1 : i].anim, ANIM_BYTES);
    /* Across a cell change the newer state stands alone. */
    if (i && same_place(&poses[i - 1], &poses[i]) && (poses[i - 1].flags & STATE_IN_WORLD))
        copy((u8 *)&out->x, (const u8 *)at, 16);
    else
        *frac = 1;
    return 1;
}

static const char *interior_name(const u8 *cut);

static void ghost_place(u32 i, const struct pose *p)
{
    char line[160];
    char *q = put_xyz(put_text(put_ghost(line, i), "PositionCell "), &p->x);

    q = put_text(q, " 0 \"");
    q = put_text(q, p->flags & STATE_INTERIOR ? interior_name(p->cell) : GHOST_EXTERIOR);
    q = put_text(q, "\"");
    *q = 0;
    run_script(line);
    ghosts[i].placed = 1;
    ghosts[i].armed = 0;
    ghosts[i].flags = p->flags;
    ghosts[i].gx = grid(p->x);
    ghosts[i].gy = grid(p->y);
    copy(ghosts[i].cell, p->cell, CELL_NAME);
    ghosts[i].x = p->x;
    ghosts[i].y = p->y;
    ghosts[i].z = p->z;
    ghosts[i].heading = 1000; /* not an angle: the next frame turns it */
    ghost_places++;
    log_text("net.ghost_cell", p->flags & STATE_INTERIOR ? (const char *)p->cell : "(exterior)");
}

/* Whether a ghost at p would stand in a loaded cell: the same interior, or an exterior cell next
 * to the local player's. */
static int near(const struct pose *p, const struct pose *local)
{
    int dx = grid(p->x) - grid(local->x), dy = grid(p->y) - grid(local->y);

    if (!same_place(p, local))
        return 0;
    return (p->flags & STATE_INTERIOR) || (dx >= -1 && dx <= 1 && dy >= -1 && dy <= 1);
}
