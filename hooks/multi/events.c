/* The welcome waits for the player to be in the world as the character, then stays up until A. */
static char welcome_text[EVENT_DATA + 1];
static u32 welcome_pending;

static void welcome_event(const struct event *e)
{
    if (!e->length)
        return;
    copy((u8 *)welcome_text, e->data, e->length);
    welcome_text[e->length] = 0;
    welcome_pending = 1;
    log_text("net.welcome", welcome_text);
}

static void event_handle(const struct event *e)
{
    char text[EVENT_DATA + 1], line[EVENT_DATA + 16];

    /* Only the server sends these, with no origin; one with an origin came from another console
     * through a server that relays everything. */
    if (e->origin && e->kind < 64 && (1ull << e->kind & (1ull << EVENT_WELCOME |
            1ull << EVENT_AUTHORITY | 1ull << EVENT_OWNERS | 1ull << EVENT_SAVE |
            1ull << EVENT_LOAD | 1ull << EVENT_CHARS | 1ull << EVENT_NEWCHAR | 1ull << EVENT_RUN))) {
        tes3x_log_hex3("net.event_forged", e->kind, e->origin, e->length);
        return;
    }
    if (e->kind == EVENT_TEXT) {
        copy((u8 *)text, e->data, e->length);
        text[e->length] = 0;
        tes3x_log_hex3("net.text_from", e->origin, e->seq, 0);
        log_text("net.text", text);
        /* The server's own notices (deaths) show on screen; players' lines only in the log. */
        if (!e->origin && player_reference()) {
            tes3x_log_hex3("net.notice", 3, e->length, 0);
            notice(text);
        }
    } else if ((e->kind >= EVENT_AUTHORITY && e->kind <= EVENT_DEATH) || e->kind == EVENT_OWNERS) {
        authority_event(e);
    } else if (e->kind == EVENT_WELCOME) {
        welcome_event(e);
    } else if (e->kind == EVENT_EQUIPMENT) {
        equipment_event(e);
    } else if (e->kind == EVENT_IDENTITY) {
        identity_event(e);
    } else if (e->kind == EVENT_ACTOR_EQUIPMENT) {
        actor_equipment_event(e);
    } else if (e->kind == EVENT_WEATHER) {
        weather_event(e);
    } else if (e->kind == EVENT_PLAYER_HIT) {
        player_hit_event(e);
    } else if (e->kind == EVENT_SPELL) {
        spell_event(e);
    } else if (e->kind == EVENT_CAST) {
        cast_event(e);
    } else if (e->kind == EVENT_SHOT) {
        shot_event(e);
    } else if (e->kind == EVENT_OBJECTS && e->length >= 1) {
        objects_event(e);
    } else if (e->kind == EVENT_SPAWN) {
        spawn_event(e);
    } else if (e->kind == EVENT_CONTENTS) {
        contents_event(e);
    } else if (e->kind == EVENT_AFFECT) {
        affect_event(e);
    } else if (e->kind == EVENT_STATUS) {
        status_event(e);
    } else if (e->kind == EVENT_BOUNTY) {
        bounty_event(e);
    } else if (e->kind == EVENT_OFFER) {
        bulk_offer(e);
    } else if (e->kind == EVENT_BUSY) {
        busy_event(e);
    } else if (e->kind == EVENT_SNAPSHOT && e->length == 4) {
        if (snapshot_wait && get32le(e->data) == snapshot_wait) {
            snapshot_confirmed = snapshot_wait;
            snapshot_wait = 0;
            tes3x_log("net.snapshot_saved", snapshot_confirmed);
        }
    } else if (e->kind == EVENT_SAVE) {
        diagnostic_save = e->length == 1 && e->data[0] == 1;
        snapshot_request = e->length == 4 ? get32le(e->data) : 0;
        save_requested = 1;
    } else if (e->kind == EVENT_LOAD) {
        load_event(e);
    } else if (e->kind == EVENT_PLAYER) {
        player_event(e);
    } else if (e->kind == EVENT_CHARS) {
        chars_event(e);
    } else if (e->kind == EVENT_NEWCHAR) {
        newchar_event(e);
    } else if (e->kind == EVENT_RUN) {
        run_event(e);
    } else {
        tes3x_log_hex3("net.event_unknown", e->kind, e->origin, e->length);
    }
}

/* Events the receive DPC delivered, handled in the game thread. */
static void events_frame(void)
{
    struct event e;
    u32 flags;

    for (;;) {
        flags = lock();
        /* A WELCOME since authority_session: these belong to a session not yet reset for. */
        if (!rel.in_count || authority_welcome != ses.welcomes) {
            unlock(flags);
            return;
        }
        copy((u8 *)&e, (const u8 *)&rel.in[rel.in_head], sizeof(e));
        rel.in_head = (rel.in_head + 1) % EVENTS_IN;
        rel.in_count--;
        rel.handled++;
        unlock(flags);
        event_handle(&e);
    }
}
