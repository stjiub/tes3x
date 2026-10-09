/* Arrows, bolts and thrown weapons. An actor nocks one from its readied ammunition (nock, at
 * "Shoot Attach") and releases it through its vtable's shoot slot; the new projectile rolls to hit
 * each actor it reaches. A shot by the player or an actor run here goes to the other consoles as
 * SHOT, and there the stand-in nocks and releases one too. A stand-in's projectile always misses:
 * what it hits on its own console reaches the victim as a hit already. */
#define EVENT_SHOT 12u /* firer refid, the firer's two shot values, firer is a player, ammo id */
#define SHOT_BYTES 13u
#define MOBILE_NOCKED 0x100 /* the projectile in hand */
#define MOBILE_SHOT_VALUES 0xD0 /* two floats, 8 apart, the shoot slot passes on */
#define MOBILE_AMMO 0x38C /* the readied ammunition's stack, object first */

typedef u8(__cdecl *fn_hit_roll)(void *attacker, void *projectile, int a2);

static const u32 shoot_slots[] = TES3X_NET_SHOOT_SLOTS, shot_roll_sites[] = TES3X_NET_SHOT_ROLL_SITES;
static u32 shot_hooked, shots_sent, shots_received, shots_replayed, shots_missed, shot_failures;

/* What the release about to happen sends as SHOT, or 0 when it sends nothing. */
static u32 shot_read(u8 *mobile, u8 *data)
{
    const u8 *nocked = *(const u8 *const *)(mobile + MOBILE_NOCKED), *ref, *object, *player;
    const u8 *firer = *(const u8 *const *)(mobile + MOBILE_REFERENCE);
    const char *id;
    u32 n, own;

    if (ses.state != SESSION_JOINED || !plausible(nocked) || !plausible(firer) ||
        ref_owner(firer, &own) || !plausible(ref = *(const u8 *const *)(nocked + MOBILE_REFERENCE)) ||
        !plausible(object = *(const u8 *const *)(ref + 0x28)) ||
        !mapped(id = ((fn_object_id)(*(void *const *const *)object)[OBJECT_GET_ID / 4])(object)))
        return 0;
    player = player_reference();
    put32le(data, firer != player ? actor_id(firer) : 0);
    copy(data + 4, mobile + MOBILE_SHOT_VALUES, 4);
    copy(data + 8, mobile + MOBILE_SHOT_VALUES + 8, 4);
    data[12] = firer == player;
    for (n = 0; id[n] && n < EQUIP_ID - 1; n++)
        data[SHOT_BYTES + n] = (u8)id[n];
    data[SHOT_BYTES + n] = 0;
    return n + SHOT_BYTES + 1;
}

static void shot_send(const u8 *data, u32 n)
{
    if (!n)
        return;
    if (!event_queue(EVENT_SHOT, data, n)) {
        tes3x_log("net.event_full", EVENT_SHOT);
        return;
    }
    shots_sent++;
    log_text("net.shot_sent", (const char *)data + SHOT_BYTES);
}

static void __attribute__((thiscall)) shoot_hook(u8 *mobile)
{
    u8 data[SHOT_BYTES + EQUIP_ID];
    u32 n = shot_read(mobile, data);

    ((fn_mobile_call)TES3X_NET_SHOOT)(mobile);
    shot_send(data, n);
}

/* The player's slot holds its own release, which settles the ammunition stack and then runs the
 * shared one. */
static void __attribute__((thiscall)) player_shoot_hook(u8 *mobile)
{
    u8 data[SHOT_BYTES + EQUIP_ID];
    u32 n = shot_read(mobile, data);

    ((fn_mobile_call)TES3X_NET_PLAYER_SHOOT)(mobile);
    shot_send(data, n);
}

static u8 __cdecl shot_roll_hook(u8 *attacker, void *projectile, int a2)
{
    u32 own;

    if (ses.state == SESSION_JOINED && plausible(attacker) &&
        ref_owner(*(const u8 *const *)(attacker + MOBILE_REFERENCE), &own)) {
        shots_missed++;
        return 0;
    }
    return ((fn_hit_roll)TES3X_NET_HIT_ROLL)(attacker, projectile, a2);
}

static void shot_hook_install(void)
{
    u32 i, cr0, flags, n = sizeof(shoot_slots) / sizeof(shoot_slots[0]);

    if (shot_hooked)
        return;
    shot_hooked = 1;
    for (i = 0; i < n; i++)
        if (*(const u32 *)shoot_slots[i] != TES3X_NET_SHOOT) {
            tes3x_log_hex3("net.shoot_slot_unexpected", shoot_slots[i],
                           *(const u32 *)shoot_slots[i], 0);
            return;
        }
    if (*(const u32 *)TES3X_NET_PLAYER_SHOOT_SLOT != TES3X_NET_PLAYER_SHOOT) {
        tes3x_log_hex3("net.shoot_slot_unexpected", TES3X_NET_PLAYER_SHOOT_SLOT,
                       *(const u32 *)TES3X_NET_PLAYER_SHOOT_SLOT, 0);
        return;
    }
    if (!redirect_calls(shot_roll_sites, sizeof(shot_roll_sites) / sizeof(shot_roll_sites[0]),
                        TES3X_NET_HIT_ROLL, (const void *)shot_roll_hook))
        return;
    flags = lock();
    __asm__ volatile("movl %%cr0, %0" : "=r"(cr0));
    __asm__ volatile("movl %0, %%cr0" : : "r"(cr0 & ~CR0_WP) : "memory");
    for (i = 0; i < n; i++)
        *(u32 *)shoot_slots[i] = (u32)shoot_hook;
    *(u32 *)TES3X_NET_PLAYER_SHOOT_SLOT = (u32)player_shoot_hook;
    __asm__ volatile("movl %0, %%cr0" : : "r"(cr0) : "memory");
    unlock(flags);
    shot_hooked = 3;
    tes3x_log("net.shot_hook", n + 1);
}

/* Another console's firer shot: the stand-in nocks the same ammunition and releases it. A ghost is
 * given one more of it first, since the equipment it copies holds one. */
static void shot_event(const struct event *e)
{
    u8 *firer, *mobile;
    char id[EQUIP_ID];
    u32 i, n, own, ghost = PEERS;

    if (e->length < SHOT_BYTES + 1)
        return;
    shots_received++;
    for (n = 0; n < EQUIP_ID - 1 && SHOT_BYTES + n < e->length && e->data[SHOT_BYTES + n]; n++)
        id[n] = (char)e->data[SHOT_BYTES + n];
    id[n] = 0;
    if (!script_safe((const u8 *)id, EQUIP_ID)) {
        refused_events++;
        return;
    }
    log_text("net.shot", id);
    firer = 0;
    if (!e->data[12])
        firer = actor_ref(get32le(e->data));
    for (i = 0; e->data[12] && i < PEERS; i++)
        if (ghosts[i].client == e->origin && ghosts[i].placed && (firer = ghost_ref(i)))
            ghost = i;
    if (!firer || !ref_owner(firer, &own) || !plausible(mobile = ref_mobile(firer)))
        return; /* not here, or run here: not a stand-in */
    if (ghost < PEERS) {
        ghost_item(ghost, "AddItem", id, " 1");
        if (!*(const u32 *)(mobile + MOBILE_AMMO))
            ghost_item(ghost, "Equip", id, "");
    }
    if (!*(const u32 *)(mobile + MOBILE_NOCKED))
        ((fn_mobile_call)TES3X_NET_NOCK)(mobile);
    if (!*(const u32 *)(mobile + MOBILE_NOCKED)) {
        shot_failures++;
        tes3x_log_hex3("net.shot_failed", (u32)firer, *(const u32 *)(mobile + MOBILE_AMMO), 0);
        return;
    }
    copy(mobile + MOBILE_SHOT_VALUES, e->data + 4, 4);
    copy(mobile + MOBILE_SHOT_VALUES + 8, e->data + 8, 4);
    ((fn_mobile_call)TES3X_NET_SHOOT)(mobile);
    shots_replayed++;
    tes3x_log_hex3("net.shot_replayed", (u32)firer, get32le(e->data + 4), get32le(e->data + 8));
}

static void shot_stat(void)
{
    tes3x_log_hex3("net.shots", shots_sent, shots_received, shots_replayed);
    tes3x_log_hex3("net.shots_missed", shots_missed, shot_failures, shot_hooked);
}
