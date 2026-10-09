/* The handshake: Noise XX with the server (tes3xnoise.c). HANDSHAKE1 is padded to at least the
 * size of the server's HANDSHAKE2, so a forged source address gains nothing. HANDSHAKE3 carries
 * HELLO; the WELCOME that answers it is the first sealed packet. Each message is resent every
 * second, HANDSHAKE_TRIES times, then the handshake starts over. The tick and the receive DPC
 * only resend and copy: the X25519 work runs in the game thread. */
#define HANDSHAKE_PAD 128u
#define HANDSHAKE_TRIES 5u
#define HS_IDLE 0u
#define HS_WANT 1u  /* the tick asks the game thread for a HANDSHAKE1 */
#define HS_SENT1 2u
#define HS_GOT2 3u  /* the DPC has a HANDSHAKE2 for the game thread */
#define HS_SENT3 4u /* keyed, waiting for WELCOME */

static const u8 prologue[] = "TES3X T3MP 11";
static struct {
    u32 phase, id, tries, packet_n, started, completed, failed, start_us, finish_us;
    u8 packet[T3MP_OUTER + NOISE_MSG3 + HELLO_BYTES + PASSWORD_MAX + NOISE_TAG];
    u8 in[NOISE_MSG2];
    struct noise noise;
} hs;

/* Caller holds the lock. */
static void handshake_reset(void)
{
    if (hs.phase == HS_SENT3)
        hs.completed++;
    hs.phase = HS_IDLE;
    hs.tries = 0;
}

/* Every second while the session wants to join; caller holds the lock. */
static void handshake_tick(void)
{
    if (hs.phase == HS_SENT1 || hs.phase == HS_SENT3) {
        if (++hs.tries >= HANDSHAKE_TRIES) {
            hs.phase = HS_WANT;
            sec.keyed = 0;
        } else {
            udp_send(ses.server, ses.port, hs.packet, hs.packet_n);
            ses.hellos++;
        }
    } else if (hs.phase == HS_IDLE) {
        hs.phase = HS_WANT;
    }
}

/* Caller holds the lock (the receive DPC). */
static void handshake_rx(const u8 *p, u32 n)
{
    if (hs.phase == HS_SENT1 && n == T3MP_OUTER + NOISE_MSG2 && get32le(p + 8) == hs.id) {
        copy(hs.in, p + T3MP_OUTER, NOISE_MSG2);
        hs.phase = HS_GOT2;
    }
}

static void handshake_start(void)
{
    u8 e[NOISE_KEY], id[4];
    u32 flags, i, t = now_us();

    trust_load();
    if (!trust.has_client) {
        if (!random_bytes(trust.client, NOISE_KEY))
            return;
        trust.has_client = trust.dirty = 1;
    }
    if (!random_bytes(e, NOISE_KEY) || !random_bytes(id, sizeof(id)))
        return;
    noise_start(&hs.noise, prologue, sizeof(prologue) - 1, trust.client, e);
    crypto_wipe(e, sizeof(e));
    flags = lock();
    if (hs.phase == HS_WANT && ses.state == SESSION_HELLO) {
        hs.id = get32le(id) | 1;
        for (i = 0; i < HANDSHAKE_PAD; i++)
            hs.packet[i] = 0;
        t3mp_outer(hs.packet, T3MP_HANDSHAKE1, hs.id, 0);
        noise_write1(&hs.noise, hs.packet + T3MP_OUTER, 0, 0);
        hs.packet_n = HANDSHAKE_PAD;
        hs.phase = HS_SENT1;
        hs.tries = 0;
        sec.keyed = 0;
        udp_send(ses.server, ses.port, hs.packet, hs.packet_n);
        ses.hellos++;
        hs.started++;
    }
    unlock(flags);
    hs.start_us = now_us() - t;
}

static void handshake_finish(void)
{
    u8 fingerprint[TRUST_FINGERPRINT], hello[HELLO_BYTES + PASSWORD_MAX + 2], none[1];
    u32 flags, i, password, diff = 0, t = now_us();

    if (noise_read2(&hs.noise, hs.in, NOISE_MSG2, none) != 0) {
        hs.failed++;
        tes3x_log("net.handshake_failed", hs.id);
        flags = lock();
        hs.phase = HS_WANT;
        unlock(flags);
        return;
    }
    crypto_blake2b(fingerprint, sizeof(fingerprint), hs.noise.rs, NOISE_KEY);
    for (i = 0; trust.has_fingerprint && i < TRUST_FINGERPRINT; i++)
        diff |= fingerprint[i] ^ trust.fingerprint[i];
    for (i = 0; trust.has_server && i < NOISE_KEY; i++)
        diff |= hs.noise.rs[i] ^ trust.server[i];
    if (diff) {
        tes3x_log_hex3("net.server_untrusted", get32(fingerprint), get32(fingerprint + 4),
                       trust.has_server);
        crypto_wipe(&hs.noise, sizeof(hs.noise));
        flags = lock();
        ses.state = SESSION_UNTRUSTED;
        hs.phase = HS_IDLE;
        unlock(flags);
        return;
    }
    if (!trust.has_server) {
        copy(trust.server, hs.noise.rs, NOISE_KEY);
        trust.has_server = trust.dirty = 1;
        tes3x_log_hex3("net.server_pinned", get32(fingerprint), get32(fingerprint + 4),
                       get32(fingerprint + 8));
    }
    flags = lock();
    copy(hello, mac, 6);
    put32le(hello + 6, TES3X_BUILD_ID);
    put32le(hello + 10, ses.plugins_hash);
    put32le(hello + 14, ses.plugins | (lobby ? LOBBY_PLUGINS : 0));
    copy(hello + 18, game_clock.local, CLOCK_BYTES);
    copy(hello + 18 + CLOCK_BYTES, build_id, BUILD_ID);
    unlock(flags);
    if ((password = trust.password_n))
        copy(hello + HELLO_BYTES, (const u8 *)trust.password, password);
    else
        copy(hello + HELLO_BYTES, (const u8 *)net_password, password = net_password_n);
    noise_write3(&hs.noise, hs.packet + T3MP_OUTER, hello, HELLO_BYTES + password);
    crypto_wipe(hello, sizeof(hello));
    flags = lock();
    noise_split(&hs.noise, sec.send, sec.receive);
    t3mp_outer(hs.packet, T3MP_HANDSHAKE3, hs.id, 0);
    hs.packet_n = T3MP_OUTER + NOISE_MSG3 + HELLO_BYTES + password;
    sec.keyed = 1;
    sec.top = sec.seen = 0;
    ses.id = hs.id;
    ses.seq = ses.peer_seq = ses.peer_time = 0;
    hs.phase = HS_SENT3;
    hs.tries = 0;
    udp_send(ses.server, ses.port, hs.packet, hs.packet_n);
    ses.hellos++;
    unlock(flags);
    hs.finish_us = now_us() - t;
}

/* Game thread, each frame while up. */
static void handshake_frame(void)
{
    entropy_add();
    entropy_mix();
    /* A servers.ini write in hand would be read back stale. */
    if (hs.phase == HS_WANT && ses.state == SESSION_HELLO && !trust_job.pending)
        handshake_start();
    else if (hs.phase == HS_GOT2)
        handshake_finish();
}

static void handshake_stat(void)
{
    tes3x_log_hex3("net.handshake", hs.started, hs.completed, hs.failed);
    tes3x_log_hex3("net.handshake_us", hs.start_us, hs.finish_us, hs.phase);
    tes3x_log_hex3("net.sealed", sec.sealed, sec.opened, sec.keyed);
    tes3x_log_hex3("net.rejected", sec.forged, sec.replayed, 0);
    tes3x_log_hex3("net.entropy", entropy.mixed, entropy.drawn, 0);
    tes3x_log_hex3("net.worker", worker.running, worker.lost, trust_job.pending);
    tes3x_log_hex3("net.refused_values", refused_states, refused_events, refused_anims);
}
