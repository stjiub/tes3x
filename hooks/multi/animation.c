/* The drawn weapon and readied spell. A simulated actor changes them from its animation's text
 * keys, which a mirrored animation skips, so they are changed here with the same calls: the
 * mobile's ready-weapon virtual (at "Equip Attach": sets the bit, attaches the mesh),
 * unreadyWeapon (at "Unequip Detach"), and for a spell the bit and the hands update. */
#define MOBILE_WEAPON_DRAWN 0x2000u
#define MOBILE_SPELL_READIED 0x4000u
#define MOBILE_READY_WEAPON 0xF0 /* vtable offset */

typedef void(__attribute__((thiscall)) *fn_mobile_call)(void *mobile);

static u32 stance_of(const u8 *mobile)
{
    u32 f = plausible(mobile) ? *(const u32 *)(mobile + MOBILE_FLAGS) : 0;

    return (f & MOBILE_WEAPON_DRAWN ? STANCE_WEAPON : 0) |
           (f & MOBILE_SPELL_READIED ? STANCE_SPELL : 0);
}

static void stance_apply(u8 *ref, u32 stance)
{
    u8 *mobile = ref_mobile(ref);
    u32 *flags, have;

    if (!plausible(mobile))
        return;
    flags = (u32 *)(mobile + MOBILE_FLAGS);
    stance &= STANCE_WEAPON | STANCE_SPELL;
    have = stance_of(mobile);
    if (have == stance)
        return;
    if ((have & STANCE_WEAPON) && !(stance & STANCE_WEAPON))
        ((fn_mobile_call)TES3X_NET_UNREADY_WEAPON)(mobile);
    if (have & STANCE_SPELL && !(stance & STANCE_SPELL)) {
        *flags &= ~MOBILE_SPELL_READIED;
        ((fn_mobile_call)TES3X_NET_MOBILE_HANDS)(mobile);
    }
    if (stance & STANCE_WEAPON && !(*flags & MOBILE_WEAPON_DRAWN))
        ((fn_mobile_call)(*(void *const *const *)mobile)[MOBILE_READY_WEAPON / 4])(mobile);
    if (stance & STANCE_SPELL && !(*flags & MOBILE_SPELL_READIED)) {
        *flags |= MOBILE_SPELL_READIED;
        ((fn_mobile_call)TES3X_NET_MOBILE_HANDS)(mobile);
    }
    stance_changes++;
    if (stance_of(mobile) != stance)
        stance_refused++;
}

/* A reference's AnimationData is its attachment of kind 0. For each layer (lower body, upper
 * body, arm) it holds the group +0x38, the key reached +0x3C, the loops left +0x48 and the time
 * in the group +0x58. Sent as the three groups, a pad byte, the three keys, a pad byte, and the
 * three times. */
#define ANIM_GROUP 0x38
#define ANIM_KEY 0x3C
#define ANIM_LOOPS 0x48
#define ANIM_TIMING 0x58
#define ANIM_LAYERS 3u

static u8 *ref_animation(const u8 *ref)
{
    return (u8 *)((fn_ref_part)TES3X_NET_REF_ANIMATION)(ref);
}

static void anim_read(const u8 *a, u8 *out)
{
    u32 l;

    for (l = 0; l < ANIM_BYTES; l++)
        out[l] = 0;
    for (l = 0; l < ANIM_LAYERS; l++) {
        out[l] = 0xFF;
        if (!plausible(a))
            continue;
        out[l] = a[ANIM_GROUP + l];
        out[4 + l] = (u8) * (const u32 *)(a + ANIM_KEY + 4 * l);
        copy(out + 8 + 4 * l, a + ANIM_TIMING + 4 * l, 4);
    }
}

static void anim_capture(const u8 *ref, u8 *out)
{
    anim_read(ref_animation(ref), out);
}

/* In first person the engine runs only the first-person reference's AnimationData (the one its
 * controller holds) and leaves the third-person one still. The player's groups and keys are read
 * from the one it runs, and each time moved to the same point between the same two keys of the
 * group on the third-person model, whose timeline the ghosts share. */
#define MOBILE_ANIM_CONTROLLER 0x244
#define CONTROLLER_ANIMATION 0x3C
#define ANIM_GROUP_OBJECTS 0x68
#define ANIM_GROUP_COUNT 150u
#define ANIM_SEQUENCES 0x2C4
#define ANIM_SEQUENCE_LAYERS 0x308
#define SEQUENCE_TIME_OFFSET 0x54
#define GROUP_KEY_COUNT 0x14
#define GROUP_KEY_TIMES 0x1C

static const float *anim_keys(const u8 *a, u32 g, u32 *n)
{
    const u8 *group;
    const float *keys;

    if (g >= ANIM_GROUP_COUNT ||
        !plausible(group = *(const u8 *const *)(a + ANIM_GROUP_OBJECTS + 4 * g)) ||
        !(*n = *(const u32 *)(group + GROUP_KEY_COUNT)) ||
        !plausible(keys = *(const float *const *)(group + GROUP_KEY_TIMES)))
        return 0;
    return keys;
}

/* Start modes select an action section; they are not the current semantic key. */
static int anim_start_mode(u32 group, u32 key)
{
    switch (((const u32 *)TES3X_NET_ANIM_GROUP_TYPES)[group]) {
    case 3: return key < 3 ? 3 : key < 6 ? 4 : 5;
    case 5: return key < 3 ? 3 : key < 6 ? 4 : 1;
    case 6: return key < 3 ? 3 : key < 6 ? 4 : key < 9 ? 9 : key < 12 ? 10 : 11;
    case 7: return key < 3 ? 3 : key < 6 ? 4 : key < 17 ? 5 : key < 28 ? 6 : 7;
    default: return 1;
    }
}

/* The controller jumps to a strength-specific follow without advancing the light-follow key. */
static void anim_follow_key(const u8 *mobile, const u8 *controller, u8 *out)
{
    u32 state = mobile[0xDD], attack = mobile[0xE0], group = controller[8], l, end;

    if (state < 5 || state > 7 || attack < 1 || attack > 3 ||
        group >= ANIM_GROUP_COUNT || ((const u32 *)TES3X_NET_ANIM_GROUP_TYPES)[group] != 7)
        return;
    end = 12 + 11 * (attack - 1) + 2 * (state - 5);
    for (l = 0; l < ANIM_LAYERS; l++)
        if (out[l] == group)
            out[4 + l] = (u8)end;
}

/* Keys are semantic slots (draw, swing, hit, follow), not a sorted timeline. */
static int anim_retime(const u8 *from, const u8 *to, u32 g, u8 *key, float *t)
{
    const float *kf, *kt;
    u32 nf, nt, end = *key, start;
    float span, frac;

    if (!(kf = anim_keys(from, g, &nf)) || !(kt = anim_keys(to, g, &nt)) ||
        end >= nf || end >= nt)
        return 0;
    start = end ? end - 1 : end;
    span = kf[end] - kf[start];
    frac = span > 0 ? (*t - kf[start]) / span : 1;
    frac = frac < 0 ? 0 : frac > 1 ? 1 : frac;
    *t = kt[start] + (kt[end] - kt[start]) * frac;
    return 1;
}

/* First-person models can omit crouch and swim groups. Use the full model's loop and native
 * movement flags, leaving attack and cast layers for semantic retiming. */
#define MOBILE_MOVEMENT 0x8
#define MOVE_RUN 0x200u
#define MOVE_SNEAK 0x400u
#define GROUP_IDLE_SNEAK 16u
#define GROUP_SNEAK_WALK 63u
#define MOVE_SWIM 0x800u
#define GROUP_IDLE_SWIM 13u
#define GROUP_SWIM_WALK 43u /* forward, back, left, right; running 4 further */
#define GROUP_MOVE_FIRST 53u /* WalkForward to SneakRight */
#define GROUP_MOVE_LAST 66u

typedef u8(__attribute__((thiscall)) *fn_mobile_test)(const void *mobile);

static u32 locomotion_capture(const u8 *mobile, const u8 *own, u8 *out)
{
    u32 f = *(const u16 *)(mobile + MOBILE_MOVEMENT), g, n, l, span, at, layers = 0;
    const float *keys;
    float t;

    if (!(f & MOVE_SNEAK) && (!(f & MOVE_SWIM) ||
        !((fn_mobile_test)TES3X_NET_BASE_UNDERWATER)(mobile)))
        return 0;
    g = f & 1 ? 0 : f & 2 ? 1 : f & 4 ? 2 : f & 8 ? 3 : 4;
    if (f & MOVE_SNEAK)
        g = g == 4 ? GROUP_IDLE_SNEAK : GROUP_SNEAK_WALK + g;
    else
        g = g == 4 ? GROUP_IDLE_SWIM : GROUP_SWIM_WALK + (f & MOVE_RUN ? 4 : 0) + g;
    if (!(keys = anim_keys(own, g, &n)) || n < 4 || keys[3] <= keys[2])
        return 0;
    span = (u32)((keys[3] - keys[2]) * 1000000.0f);
    at = span ? now_us() % span : 0;
    t = keys[2] + (float)at / 1000000.0f;
    for (l = 0; l < ANIM_LAYERS; l++) {
        if (out[l] > GROUP_IDLE_SNEAK &&
            (out[l] < GROUP_MOVE_FIRST || out[l] > GROUP_MOVE_LAST))
            continue;
        layers |= 1u << l;
        out[l] = (u8)g;
        out[4 + l] = 3;
        copy(out + 8 + 4 * l, (const u8 *)&t, 4);
    }
    return layers;
}

static void player_anim_capture(const u8 *ref, u8 *out)
{
    const u8 *own = ref_animation(ref), *mobile = ref_mobile(ref), *controller, *run;
    float t;
    u32 l, locomotion;

    if (!plausible(own) || !plausible(mobile) ||
        !plausible(controller = *(const u8 *const *)(mobile + MOBILE_ANIM_CONTROLLER)) ||
        !plausible(run = *(const u8 *const *)(controller + CONTROLLER_ANIMATION)) || run == own) {
        anim_capture(ref, out);
        return;
    }
    anim_read(run, out);
    anim_follow_key(mobile, controller, out);
    first_person_states++;
    locomotion = locomotion_capture(mobile, own, out);
    for (l = 0; l < ANIM_LAYERS; l++) {
        if (out[l] == 0xFF || (locomotion & (1u << l)))
            continue;
        copy((u8 *)&t, out + 8 + 4 * l, 4);
        if (anim_retime(run, own, out[l], out + 4 + l, &t))
            copy(out + 8 + 4 * l, (const u8 *)&t, 4);
        else
            out[l] = 0xFF;
    }
}

static void player_state(const u8 *ref, u8 *state)
{
    const u8 *handler = *(const u8 **)TES3X_NET_DATA_HANDLER, *cell = 0;
    const char *name;
    u32 i, flags = STATE_IN_WORLD;

    for (i = 0; i < STATE_BYTES; i++)
        state[i] = 0;
    if (plausible(handler) && plausible(cell = *(const u8 **)(handler + 0xAC))) {
        flags |= STATE_INTERIOR;
        name = *(const char **)(cell + 0x14); /* 0x10 on PC */
        for (i = 0; mapped(name) && name[i] && i < CELL_NAME - 1; i++)
            state[20 + i] = (u8)name[i];
    }
    put32le(state, flags | stance_of(ref_mobile(ref)));
    copy(state + 4, ref + 0x38, 12); /* position */
    copy(state + 16, ref + 0x34, 4); /* orientation z */
    player_anim_capture(ref, state + STATE_ANIM);
}
