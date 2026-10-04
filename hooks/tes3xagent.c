/* Authenticated in-game status and log channel to the TES3X GUI. */

#include "monocypher.h"
#include "tes3x_thunks.h"
#include "tes3xnt.h"
#include "tes3xnoise.h"
#include "tes3xnet.h"

#ifndef TES3X_INI_GET_STRING
#error "define TES3X_INI_GET_STRING to the VA of the ini string reader"
#endif
#ifndef TES3X_INI_PATH
#error "define TES3X_INI_PATH to the VA of the engine's ini filename string"
#endif

typedef unsigned short u16;
typedef int(__cdecl *fn_ini_get_string)(const char *, const char *, const char *, char *, int,
                                        const char *);
typedef u32(__stdcall *fn_MmQueryStatistics)(void *);

#define MmQueryStatistics KFN(THUNK_MmQueryStatistics, fn_MmQueryStatistics)
#define AGENT_PORT 26501u
#define AGENT_VERSION 1u
#define AGENT_HEADER 16u
#define AGENT_MAX 1400u
#define AGENT_FINGERPRINT 16u
#define AGENT_RETRY_TICKS 4u
#define AGENT_RETRIES 5u
#define AGENT_LOG_SLOTS 32u
#define AGENT_LOG_BYTES 192u
#define ENTROPY_RING 64u
#define ENTROPY_NEEDED 256u

#define AGENT_HANDSHAKE1 1u
#define AGENT_HANDSHAKE2 2u
#define AGENT_HANDSHAKE3 3u
#define AGENT_SEALED 4u
#define AGENT_WELCOME 0u
#define AGENT_HEARTBEAT 1u
#define AGENT_LOG 2u
#define AGENT_GOODBYE 3u

#define HS_IDLE 0u
#define HS_WANT 1u
#define HS_SENT1 2u
#define HS_GOT2 3u
#define HS_SENT3 4u
#define HS_CONNECTED 5u
#define HS_UNTRUSTED 6u

static const u8 prologue[] = "TES3X in-game agent v1";
static struct tes3x_net_channel agent_channel;
static u32 configured, target, target_port, gateway, session, ticks, tries, phase;
static u32 outgoing, incoming, has_incoming, last_frame_us, last_beat_us;
static u8 pinned[AGENT_FINGERPRINT], send_key[NOISE_KEY], receive_key[NOISE_KEY];
static struct noise handshake;
static u8 handshake_packet[AGENT_HEADER + NOISE_MSG3 + 16];
static u32 handshake_packet_n;
static u8 handshake2[NOISE_MSG2];

static volatile u32 entropy_ring[ENTROPY_RING], entropy_in;
static struct {
    u32 out, mixed, drawn;
    u8 pool[64];
} entropy;

static struct {
    volatile u32 busy;
    u32 head, count, dropped, partial_n;
    u16 length[AGENT_LOG_SLOTS];
    u8 line[AGENT_LOG_SLOTS][AGENT_LOG_BYTES];
    u8 partial[AGENT_LOG_BYTES];
} logs;

static u32 get32le(const u8 *p)
{
    return p[0] | (u32)p[1] << 8 | (u32)p[2] << 16 | (u32)p[3] << 24;
}

static void put16le(u8 *p, u32 v)
{
    p[0] = (u8)v;
    p[1] = (u8)(v >> 8);
}

static void put32le(u8 *p, u32 v)
{
    p[0] = (u8)v;
    p[1] = (u8)(v >> 8);
    p[2] = (u8)(v >> 16);
    p[3] = (u8)(v >> 24);
}

static void copy(u8 *to, const u8 *from, u32 n)
{
    while (n--)
        *to++ = *from++;
}

static int equal(const u8 *a, const u8 *b, u32 n)
{
    u8 different = 0;
    while (n--)
        different |= *a++ ^ *b++;
    return different == 0;
}

static void entropy_add(void)
{
    u32 lo, hi;
    __asm__ volatile("rdtsc" : "=a"(lo), "=d"(hi));
    entropy_ring[entropy_in++ % ENTROPY_RING] = lo ^ hi;
}

static void entropy_mix(void)
{
    crypto_blake2b_ctx ctx;
    u32 in = entropy_in, n = in - entropy.out, i;
    u8 sample[4];

    if (!n)
        return;
    if (n > ENTROPY_RING)
        n = ENTROPY_RING;
    crypto_blake2b_init(&ctx, sizeof(entropy.pool));
    crypto_blake2b_update(&ctx, entropy.pool, sizeof(entropy.pool));
    for (i = in - n; i != in; i++) {
        put32le(sample, entropy_ring[i % ENTROPY_RING]);
        crypto_blake2b_update(&ctx, sample, sizeof(sample));
    }
    crypto_blake2b_final(&ctx, entropy.pool);
    entropy.mixed += n;
    entropy.out = in;
}

static int random_bytes(u8 *out, u32 n)
{
    u8 block[64], label[8] = {0, 0, 0, 0, 'o', 'u', 't', 0};

    entropy_add();
    entropy_mix();
    if (entropy.mixed < ENTROPY_NEEDED || n > sizeof(block))
        return 0;
    put32le(label, ++entropy.drawn);
    crypto_blake2b_keyed(block, sizeof(block), entropy.pool, sizeof(entropy.pool), label, 8);
    copy(out, block, n);
    label[4] = 'n'; label[5] = 'e'; label[6] = 'x'; label[7] = 't';
    crypto_blake2b_keyed(block, sizeof(block), entropy.pool, sizeof(entropy.pool), label, 8);
    copy(entropy.pool, block, sizeof(block));
    crypto_wipe(block, sizeof(block));
    return 1;
}

static void header(u8 *p, u32 kind, u32 body, u32 id, u32 sequence)
{
    p[0] = 'T'; p[1] = '3'; p[2] = 'A'; p[3] = 'G';
    p[4] = AGENT_VERSION; p[5] = (u8)kind;
    put16le(p + 6, body); put32le(p + 8, id); put32le(p + 12, sequence);
}

static void udp_send(const u8 *p, u32 n)
{
    tes3x_net_send(&agent_channel, target, target_port, p, n);
}

/* Caller holds the network lock. */
static void sealed_send(u32 kind, const u8 *body, u32 n)
{
    static u8 packet[AGENT_MAX], plain[AGENT_MAX - AGENT_HEADER - NOISE_TAG];
    u32 sequence;

    if (phase != HS_CONNECTED || n + 1 > sizeof(plain))
        return;
    sequence = outgoing++;
    plain[0] = (u8)kind;
    copy(plain + 1, body, n);
    header(packet, AGENT_SEALED, n + 1 + NOISE_TAG, session, sequence);
    noise_seal(send_key, sequence, packet, AGENT_HEADER, plain, n + 1,
               packet + AGENT_HEADER);
    udp_send(packet, AGENT_HEADER + n + 1 + NOISE_TAG);
}

static int hex_digit(char c)
{
    return c >= '0' && c <= '9' ? c - '0' : c >= 'a' && c <= 'f' ? c - 'a' + 10 :
           c >= 'A' && c <= 'F' ? c - 'A' + 10 : -1;
}

static int hex_read(const char *text, u8 *out, u32 n)
{
    u32 i;
    int hi, lo;
    for (i = 0; i < n; i++) {
        hi = hex_digit(text[2 * i]); lo = hex_digit(text[2 * i + 1]);
        if (hi < 0 || lo < 0)
            return 0;
        out[i] = (u8)(hi << 4 | lo);
    }
    return 1;
}

static const char *address(const char *text, u32 *out)
{
    u32 value = 0, part, i;
    for (i = 0; i < 4; i++) {
        if (*text < '0' || *text > '9')
            return 0;
        part = 0;
        while (*text >= '0' && *text <= '9') {
            part = part * 10 + (*text++ - '0');
            if (part > 255)
                return 0;
        }
        value = value << 8 | part;
        if (i != 3 && *text++ != '.')
            return 0;
    }
    *out = value;
    return text;
}

static u32 ini_text(const char *key, char *out, u32 size)
{
    fn_ini_get_string get = (fn_ini_get_string)TES3X_INI_GET_STRING;
    u32 i;
    for (i = 0; i < size; i++)
        out[i] = 0;
    get("Xbox", key, "", out, (int)size - 1, (const char *)TES3X_INI_PATH);
    for (i = 0; out[i]; i++)
        ;
    while (i && (out[i - 1] == ' ' || out[i - 1] == '\t'))
        out[--i] = 0;
    return i;
}

static void configure(void)
{
    char spec[96], gateway_text[24];
    const char *p;
    u32 i, n;

    configured = 1;
    if (!(n = ini_text("NetAgent", spec, sizeof(spec))))
        return;
    if (!(p = address(spec, &target))) {
        tes3x_log("agent.bad_host", 0);
        return;
    }
    target_port = AGENT_PORT;
    if (*p == ':') {
        target_port = 0;
        for (p++; *p >= '0' && *p <= '9'; p++) {
            u32 digit = *p - '0';
            if (target_port > 6553 || (target_port == 6553 && digit > 5)) {
                tes3x_log("agent.bad_port", target_port);
                return;
            }
            target_port = target_port * 10 + digit;
        }
        if (!target_port || target_port > 65535) {
            tes3x_log("agent.bad_port", target_port);
            return;
        }
    }
    if (*p++ != '#' || !hex_read(p, pinned, sizeof(pinned))) {
        tes3x_log("agent.no_fingerprint", 0);
        return;
    }
    p += 2 * sizeof(pinned);
    if (*p) {
        tes3x_log("agent.bad_setting", 0);
        return;
    }
    ini_text("NetGateway", gateway_text, sizeof(gateway_text));
    if (gateway_text[0]) {
        p = address(gateway_text, &gateway);
        if (!p || *p) {
            tes3x_log("agent.bad_gateway", 0);
            return;
        }
    }
#ifndef TES3X_MULTIPLAYER
    if (!tes3x_net_autostart())
        return;
    if (!gateway)
        gateway = tes3x_net_gateway();
#endif
    if (!tes3x_net.up)
        return;
    tes3x_net_route(&agent_channel, target, gateway);
    for (i = 0; i < sizeof(handshake_packet); i++)
        handshake_packet[i] = 0;
    phase = HS_WANT;
    tes3x_log_hex3("agent.target", target, target_port, agent_channel.route.hop);
}

static void handshake_start(void)
{
    u8 secrets[64], id[4];
    u32 flags;

    if (!random_bytes(secrets, sizeof(secrets)) || !random_bytes(id, sizeof(id)))
        return;
    session = get32le(id) | 1;
    noise_start(&handshake, prologue, sizeof(prologue) - 1, secrets, secrets + 32);
    crypto_wipe(secrets, sizeof(secrets));
    flags = tes3x_net_lock();
    if (phase == HS_WANT) {
        header(handshake_packet, AGENT_HANDSHAKE1, NOISE_MSG1, session, 0);
        noise_write1(&handshake, handshake_packet + AGENT_HEADER, 0, 0);
        handshake_packet_n = AGENT_HEADER + NOISE_MSG1;
        phase = HS_SENT1; ticks = tries = 0;
        udp_send(handshake_packet, handshake_packet_n);
    }
    tes3x_net_unlock(flags);
}

static void handshake_finish(void)
{
    u8 fingerprint[AGENT_FINGERPRINT], none[1], hello[4];
    u32 flags;

    if (noise_read2(&handshake, handshake2, sizeof(handshake2), none) != 0) {
        phase = HS_WANT;
        return;
    }
    crypto_blake2b(fingerprint, sizeof(fingerprint), handshake.rs, NOISE_KEY);
    if (!equal(fingerprint, pinned, sizeof(pinned))) {
        crypto_wipe(&handshake, sizeof(handshake));
        phase = HS_UNTRUSTED;
        tes3x_log("agent.untrusted", 0);
        return;
    }
    put32le(hello, TES3X_BUILD_ID);
    noise_write3(&handshake, handshake_packet + AGENT_HEADER, hello, sizeof(hello));
    noise_split(&handshake, send_key, receive_key);
    flags = tes3x_net_lock();
    header(handshake_packet, AGENT_HANDSHAKE3, NOISE_MSG3 + sizeof(hello), session, 0);
    handshake_packet_n = AGENT_HEADER + NOISE_MSG3 + sizeof(hello);
    phase = HS_SENT3; ticks = tries = outgoing = has_incoming = 0;
    udp_send(handshake_packet, handshake_packet_n);
    tes3x_net_unlock(flags);
}

static void agent_receive(u32 source, u32 port, const u8 *p, u32 n)
{
    static u8 plain[AGENT_MAX];
    u32 kind, size, id, sequence;

    if (source != target || port != target_port || n < AGENT_HEADER || n > AGENT_MAX ||
        p[0] != 'T' || p[1] != '3' || p[2] != 'A' || p[3] != 'G' ||
        p[4] != AGENT_VERSION || (size = p[6] | (u32)p[7] << 8) != n - AGENT_HEADER ||
        (id = get32le(p + 8)) != session)
        return;
    kind = p[5]; sequence = get32le(p + 12);
    if (kind == AGENT_HANDSHAKE2 && phase == HS_SENT1 && !sequence && size == NOISE_MSG2) {
        copy(handshake2, p + AGENT_HEADER, sizeof(handshake2));
        phase = HS_GOT2;
        return;
    }
    if (kind != AGENT_SEALED || phase < HS_SENT3 || size < NOISE_TAG + 1 ||
        (has_incoming && (int)(sequence - incoming) <= 0) ||
        noise_open(receive_key, sequence, p, AGENT_HEADER, p + AGENT_HEADER, size, plain))
        return;
    incoming = sequence; has_incoming = 1;
    if (plain[0] == AGENT_WELCOME && phase == HS_SENT3) {
        phase = HS_CONNECTED;
        last_beat_us = 0;
    }
}

static void agent_tick(void)
{
    entropy_add();
    if (!tes3x_net.up || phase == HS_IDLE || phase == HS_CONNECTED || phase == HS_UNTRUSTED)
        return;
    if (!agent_channel.route.known) {
        if (++ticks % AGENT_RETRY_TICKS == 1)
            tes3x_net_arp(agent_channel.route.hop);
        return;
    }
    if (phase == HS_WANT || phase == HS_GOT2)
        return;
    if (++ticks >= AGENT_RETRY_TICKS) {
        ticks = 0;
        if (++tries >= AGENT_RETRIES) {
            phase = HS_WANT;
            tries = 0;
        } else {
            udp_send(handshake_packet, handshake_packet_n);
        }
    }
}

static u32 free_kb(void)
{
    struct {
        u32 length, total, available, committed, reserved, cache, pool, stack, image;
    } st;
    st.length = sizeof(st);
    return MmQueryStatistics(&st) == 0 ? st.available * 4 : 0;
}

static int log_take(u8 *out, u32 *n)
{
    u32 flags, slot;
    flags = tes3x_net_lock();
    if (!logs.count) {
        tes3x_net_unlock(flags);
        return 0;
    }
    slot = logs.head;
    *n = logs.length[slot];
    copy(out, logs.line[slot], *n);
    logs.head = (logs.head + 1) % AGENT_LOG_SLOTS;
    logs.count--;
    tes3x_net_unlock(flags);
    return 1;
}

void tes3x_agent_log_raw(const char *text, u32 n)
{
    u32 i, slot;

    if (__sync_lock_test_and_set(&logs.busy, 1)) {
        __sync_fetch_and_add(&logs.dropped, 1);
        return;
    }
    for (i = 0; i < n; i++) {
        char c = text[i];
        if (c != '\r' && c != '\n' && logs.partial_n < AGENT_LOG_BYTES)
            logs.partial[logs.partial_n++] = (u8)c;
        if (c == '\n' && logs.partial_n) {
            if (logs.count == AGENT_LOG_SLOTS) {
                logs.head = (logs.head + 1) % AGENT_LOG_SLOTS;
                logs.count--;
                __sync_fetch_and_add(&logs.dropped, 1);
            }
            slot = (logs.head + logs.count++) % AGENT_LOG_SLOTS;
            logs.length[slot] = (u16)logs.partial_n;
            copy(logs.line[slot], logs.partial, logs.partial_n);
            logs.partial_n = 0;
        }
    }
    __sync_lock_release(&logs.busy);
}

static void agent_frame(void)
{
    u8 body[AGENT_LOG_BYTES];
    u32 now, n, flags;

    entropy_add(); entropy_mix();
    if (!configured)
        configure();
    if (!target || !tes3x_net.up || !tes3x_net.ip)
        return;
    if (!gateway)
        gateway = tes3x_net_gateway();
    if (!agent_channel.route.known) {
        tes3x_net_route(&agent_channel, target, gateway);
        return;
    }
    if (phase == HS_WANT)
        handshake_start();
    else if (phase == HS_GOT2)
        handshake_finish();
    if (phase != HS_CONNECTED)
        return;
    now = tes3x_net_now_us();
    if (!last_beat_us || now - last_beat_us >= 1000000u) {
        put32le(body, now);
        put32le(body + 4, last_frame_us ? now - last_frame_us : 0);
        put32le(body + 8, free_kb());
        put32le(body + 12, logs.dropped);
        flags = tes3x_net_lock(); sealed_send(AGENT_HEARTBEAT, body, 16); tes3x_net_unlock(flags);
        last_beat_us = now;
    }
    last_frame_us = now;
    if (log_take(body, &n)) {
        flags = tes3x_net_lock(); sealed_send(AGENT_LOG, body, n); tes3x_net_unlock(flags);
    }
}

static void agent_closing(void)
{
    sealed_send(AGENT_GOODBYE, 0, 0);
    phase = HS_IDLE;
}

void tes3x_agent_entry(void)
{
    agent_channel.port = AGENT_PORT;
    agent_channel.receive = agent_receive;
    agent_channel.tick = agent_tick;
    agent_channel.sample = entropy_add;
    agent_channel.frame = agent_frame;
    agent_channel.closing = agent_closing;
    if (!tes3x_net_register(&agent_channel))
        tes3x_log("agent.channel_failed", 0);
}
