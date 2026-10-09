/* Death while joined. The engine's death ends in "load the most recent save?", which would load a
 * stale copy of the character: while joined, MobilePlayer::onDeath returns before it and the
 * server is told (PLAYER_DEATH). Its RESPAWN sets a delay, a place and the gold lost; then the
 * player is resurrected at the closest TempleMarker or DivineMarker, as the Intervention spells
 * find them, and the console sends PLAYER_ALIVE. Without an answer it respawns anyway. */
#define PLAYER_RESPAWN 9u /* from the server: delay ms u32, RESPAWN_*, gold to lose u32 */
#define RESPAWN_TEMPLE 0u
#define RESPAWN_SHRINE 1u
#define RESPAWN_NEAREST 2u
#define RESPAWN_UNANSWERED_US 15000000u
#define RESPAWN_DELAY_MAX 600000u /* ms */
#define WORLD_MAGIC 0x6C          /* WorldController: magic instances; +4 stops them */
#define HANDLER_INTERIOR 0xAC
#define HANDLER_LAST_EXTERIOR 0xB8 /* grid x, y; 0x7FFFFFFF when none */
#define HANDLER_DEATH_FLAG 0xB518  /* set by the death; read in the stat */
#define ANIM_THIRD_PERSON 0xE8 /* PlayerAnimationController */
typedef const u8 *(__attribute__((thiscall)) *fn_find_marker)(void *data_handler,
                                                               const void *object);
typedef void(__cdecl *fn_fill_bar)(u32 bar, float current, float base);
static u32 dead_since, respawn_told, respawn_at, respawn_where, respawn_gold;
static u32 deaths_here, respawns_done, deaths_unjoined, death_hooked;
static u32 player_view_valid, player_view_third;

int __cdecl tes3x_net_death(void)
{
    u8 data = PLAYER_DEATH;

    if (ses.state != SESSION_JOINED) {
        deaths_unjoined++;
        return 0;
    }
    player_dead = 1;
    dead_since = now_us();
    respawn_told = 0;
    deaths_here++;
    event_queue(EVENT_PLAYER, &data, 1);
    tes3x_log("net.player_died", deaths_here);
    return 1;
}

/* Called in place of onDeath's `mov eax, [DataHandler]`; a death taken here returns from onDeath
 * through its epilogue (pop edi, pop esi, add esp 8, ret), checked by the payload builder. */
__attribute__((naked)) void tes3x_net_death_gate(void)
{
    __asm__ volatile("call _tes3x_net_death\n\t"
                     "testl %eax, %eax\n\t"
                     "jnz 1f\n\t"
                     "movl " TES3X_NET_STR(TES3X_NET_DATA_HANDLER) ", %eax\n\t"
                     "ret\n\t"
                     "1:\n\t"
                     "addl $4, %esp\n\t"
                     "popl %edi\n\t"
                     "popl %esi\n\t"
                     "addl $8, %esp\n\t"
                     "ret\n\t");
}

static void death_hook_install(void)
{
    u8 *site = (u8 *)TES3X_NET_DEATH_SITE;
    u32 cr0, flags;

    if (death_hooked)
        return;
    death_hooked = 1;
    if (site[0] != 0xA1 || *(const u32 *)(site + 1) != TES3X_NET_DATA_HANDLER) {
        tes3x_log_hex3("net.call_site_unexpected", (u32)site, *(const u32 *)site, 0);
        return;
    }
    flags = lock();
    __asm__ volatile("movl %%cr0, %0" : "=r"(cr0));
    __asm__ volatile("movl %0, %%cr0" : : "r"(cr0 & ~CR0_WP) : "memory");
    *(u32 *)(site + 1) = (u32)tes3x_net_death_gate - ((u32)site + 5);
    site[0] = 0xE8;
    __asm__ volatile("movl %0, %%cr0" : : "r"(cr0) : "memory");
    unlock(flags);
    death_hooked = 2;
}

static void respawn_event(const u8 *p)
{
    u32 delay = get32le(p);

    /* After a relaunch the character died in, the checkpoint's player is alive but still due. */
    if (!player_dead)
        dead_since = now_us();
    player_dead = 1;
    respawn_told = 1;
    respawn_at = now_us() + (delay < RESPAWN_DELAY_MAX ? delay : RESPAWN_DELAY_MAX) * 1000u;
    respawn_where = p[4];
    respawn_gold = get32le(p + 5);
    tes3x_log_hex3("net.respawn_told", delay, respawn_where, respawn_gold);
}

static float flat_distance2(const float *a, float x, float y)
{
    return (a[0] - x) * (a[0] - x) + (a[1] - y) * (a[1] - y);
}

/* The closest marker of the kinds asked for, measured as the engine searches: from the last
 * exterior cell when the player is inside. */
static const u8 *respawn_marker(u8 *handler, const u8 *player)
{
    const void *temple = *(const void *const *)TES3X_NET_TEMPLE_MARKER;
    const void *shrine = *(const void *const *)TES3X_NET_DIVINE_MARKER;
    const u8 *a = 0, *b = 0;
    const int *grid = (const int *)(handler + HANDLER_LAST_EXTERIOR);
    float x = *(const float *)(player + REF_POSITION);
    float y = *(const float *)(player + REF_POSITION + 4);

    if (respawn_where != RESPAWN_SHRINE && plausible(temple))
        a = ((fn_find_marker)TES3X_NET_FIND_MARKER)(handler, temple);
    if (respawn_where != RESPAWN_TEMPLE && plausible(shrine))
        b = ((fn_find_marker)TES3X_NET_FIND_MARKER)(handler, shrine);
    if (!plausible(a))
        return plausible(b) ? b : 0;
    if (!plausible(b))
        return a;
    if (*(void *const *)(handler + HANDLER_INTERIOR) && grid[0] != 0x7FFFFFFF &&
        grid[1] != 0x7FFFFFFF) {
        x = (float)(grid[0] << 13);
        y = (float)(grid[1] << 13);
    }
    return flat_distance2((const float *)(a + REF_POSITION), x, y) <=
                   flat_distance2((const float *)(b + REF_POSITION), x, y)
               ? a
               : b;
}

static void respawn(void)
{
    u8 *handler = *(u8 **)TES3X_NET_DATA_HANDLER, *world = *(u8 **)TES3X_NET_WORLD, *magic;
    static const char *const names[3] = {"Health", "Magicka", "Fatigue"};
    static const u32 stats[3] = {MOBILE_HEALTH_STAT, MOBILE_MAGICKA_STAT, MOBILE_FATIGUE_STAT};
    u8 *mobile = player_mobile(), data = PLAYER_ALIVE;
    const u8 *ref = player_reference(), *marker, *stat;
    char line[96], *q;
    int bounty, delta;
    u32 i;

    if (!plausible(handler) || !plausible(world) || !plausible(mobile) || !plausible(ref))
        return;
    bounty = ((fn_get_bounty)TES3X_NET_GET_BOUNTY)(mobile);
    marker = respawn_marker(handler, ref);
    run_script("Player->Resurrect");
    for (i = 0; i < 3; i++) { /* a living player (a relaunch) is not resurrected: refill */
        stat = mobile + stats[i];
        if ((delta = round_int(*(const float *)(stat + STAT_BASE) -
                               *(const float *)(stat + STAT_BASE + 4))) > 0)
            player_set((u8 *)ref, "ModCurrent", names[i], delta);
    }
    ((fn_fill_bar)TES3X_NET_FILL_BAR)(*(const u16 *)TES3X_NET_HEALTH_BAR,
                                      *(const float *)(mobile + MOBILE_HEALTH),
                                      *(const float *)(mobile + MOBILE_HEALTH_STAT + STAT_BASE));
    if (plausible(magic = *(u8 **)(world + WORLD_MAGIC)))
        magic[4] = 0;
    /* Resurrect clears the bounty, but actors already fighting the player keep their target.
     * For three seconds peace_check stops only fights their base disposition would not start. */
    player_peace_until = now_us() + PEACE_US;
    if (respawn_gold) {
        *put_int(put_text(line, "Player->RemoveItem gold_001 "), (int)respawn_gold) = 0;
        run_script(line);
    }
    if (marker) {
        q = put_xyz(put_text(line, "Player->Position "), (const float *)(marker + REF_POSITION));
        q = put_int(put_text(q, " "),
                    round_int(*(const float *)(marker + REF_ORIENTATION + 8) * (180.0f / PI)));
        *q = 0;
        run_script(line);
    }
    if (player_view_valid && !player_view_third)
        run_script("TogglePOV");
    {
        static const char *const messages[] = {
            "Death could not hold you. ", "The gods return you to life. ",
            "You awaken at sanctuary. "};
        q = put_text(line, messages[deaths_here % 3]);
        if (respawn_gold)
            q = put_text(put_int(put_text(q, "Penalty: "), (int)respawn_gold), " gold lost.");
        else
            q = put_text(q, "Penalty: none.");
        *q = 0;
        notice(line);
    }
    player_dead = 0;
    respawns_done++;
    event_queue(EVENT_PLAYER, &data, 1);
    tes3x_log_hex3("net.respawned", marker ? *(const u32 *)(marker + REF_ID) : 0, (u32)bounty,
                   respawn_gold);
}

static void player_view_frame(void)
{
    const u8 *mobile = player_mobile();
    const u8 *anim = plausible(mobile) ? *(const u8 *const *)(mobile + MOBILE_ANIM_CONTROLLER) : 0;

    if (!player_dead && plausible(anim)) {
        player_view_third = anim[ANIM_THIRD_PERSON] != 0;
        player_view_valid = 1;
    }
}

static void respawn_frame(void)
{
    u32 now = now_us();

    if (!player_dead)
        return;
    if (respawn_told ? (int)(now - respawn_at) >= 0 : now - dead_since >= RESPAWN_UNANSWERED_US)
        respawn();
}

static void death_stat(void)
{
    const u8 *handler = *(const u8 *const *)TES3X_NET_DATA_HANDLER, *mobile = player_mobile();
    const u8 *anim = plausible(mobile) ? *(const u8 *const *)(mobile + MOBILE_ANIM_CONTROLLER) : 0;

    tes3x_log_hex3("net.player_deaths", deaths_here, respawns_done, deaths_unjoined);
    if (plausible(anim))
        tes3x_log_hex3("net.player_view", anim[ANIM_THIRD_PERSON], anim[ANIM_THIRD_PERSON + 1],
                       (u32)round_int(*(const float *)(mobile + MOBILE_HEALTH)));
    tes3x_log_hex3("net.player_dead", player_dead, death_hooked,
                   plausible(handler) ? handler[HANDLER_DEATH_FLAG] : 0xFF);
    tes3x_log_hex3("net.ghost_deaths", ghost_deaths, ghost_respawns, 0);
}
