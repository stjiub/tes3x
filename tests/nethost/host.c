/* The payload's receive path on the PC. tests/test_net_host.py copies the network sources beside
 * this file with inline assembly removed and the NIC's registers moved into host_nic. Frames come
 * on stdin as a mode byte, a little-endian length and the frame, placed at an unreadable page. */

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

unsigned char host_nic[0x1000];
void *host_thunks[64];
static int verbose;

#include "tes3xnet.c"

void tes3x_ini_xbox(const char *key, const char *dflt, char *out, u32 size)
{
    (void)key;
    if (size) {
        strncpy(out, dflt, size - 1);
        out[size - 1] = 0;
    }
}

void tes3x_launch(const char *path)
{
    (void)path;
}

void tes3x_launch_data(const char *path, const void *data, u32 size)
{
    (void)path;
    (void)data;
    (void)size;
}

/* The production sources are separate translation units. Give the few file-local helpers with
 * the same names distinct ones while this harness includes both to reach their parser state. */
#define get16 multi_get16
#define get32 multi_get32
#define put16 multi_put16
#define put32 multi_put32
#define put32le multi_put32le
#define copy multi_copy
#define mac multi_mac
#define BUF MULTI_BUF
#define TICK_MS MULTI_TICK_MS
#define CR0_WP MULTI_CR0_WP
#include "tes3xmulti.c"
#undef get16
#undef get32
#undef put16
#undef put32
#undef put32le
#undef copy
#undef mac
#undef BUF
#undef TICK_MS
#undef CR0_WP

#define HOST_MTU 1518u
#define HOST_SESSION 0x11223344u
#define HOST_XID 0x01020304u
#define HOST_DNS_ID 0x4242u
#define MODE_JOINED 0
#define MODE_HANDSHAKE 1
#define MODE_RESOLVE 2
#define MODE_BOUND 3

static volatile struct descriptor host_tx[TX_RING];
static u8 host_tx_buf[TX_RING * 2048u];

static u32 __stdcall host_physical(void *p)
{
    return (u32)(uintptr_t)p;
}

static void __stdcall host_stall(u32 us)
{
    (void)us;
}

static u32 host_failure(void)
{
    return 0xC0000001u; /* STATUS_UNSUCCESSFUL: no file exists on the host */
}

static u32 host_zero(void)
{
    return 0;
}

static void host_unexpected(void)
{
    fprintf(stderr, "a kernel call the receive path should not make\n");
    exit(3);
}

void tes3x_log_prepare(void)
{
}

void tes3x_log(const char *tag, u32 value)
{
    if (verbose)
        printf("%s %u\n", tag, value);
}

void tes3x_log_hex(const char *tag, u32 value)
{
    if (verbose)
        printf("%s 0x%08X\n", tag, value);
}

void tes3x_log_hex3(const char *tag, u32 a, u32 b, u32 c)
{
    if (verbose)
        printf("%s 0x%08X 0x%08X 0x%08X\n", tag, a, b, c);
}

void tes3x_log_raw(const char *buf, u32 len)
{
    if (verbose)
        printf("%.*s\n", (int)len, buf);
}

static void host_setup(void)
{
    u32 i;

    for (i = 0; i < sizeof(host_thunks) / sizeof(*host_thunks); i++)
        host_thunks[i] = (void *)host_unexpected;
    *(void **)THUNK_MmGetPhysicalAddress = (void *)host_physical;
    *(void **)THUNK_KeStallExecutionProcessor = (void *)host_stall;
    *(void **)THUNK_KeInsertQueueDpc = (void *)host_zero;
    *(void **)THUNK_NtCreateFile = (void *)host_failure;
    tx_ring = host_tx;
    tx_buf = host_tx_buf;
    mac[0] = 2;
    mac[4] = 0x24;
    mac[5] = 0x99;
    memcpy(multi_mac, mac, 6);
    multi_channel.port = PORT;
    multi_channel.receive = multi_receive;
    dhcp_channel.port = DHCP_CLIENT;
    dhcp_channel.receive = dhcp_receive;
    tes3x_net_register(&multi_channel);
    tes3x_net_register(&dhcp_channel);
    for (i = 0; i < NOISE_KEY; i++) {
        sec.send[i] = 0x11;
        sec.receive[i] = 0x22;
    }
}

/* The state a frame meets; reset each time so every frame is tried against the same one. */
static void host_prepare(int mode)
{
    u32 i;

    for (i = 0; i < TX_RING; i++)
        host_tx[i].flags = 0;
    net.up = 1; /* a DHCP NAK or a new lease changes these */
    net.ip = 0x0A00020Fu;
    net.mask = 0xFFFFFF00u;
    ses.server = ses.dns = ses.gateway = multi_channel.route.hop = 0x0A000202u;
    ses.port = PORT;
    multi_channel.route.known = 1;
    ses.state = mode == MODE_HANDSHAKE ? SESSION_HELLO :
                mode == MODE_RESOLVE ? SESSION_RESOLVE : SESSION_JOINED;
    ses.id = HOST_SESSION;
    ses.dns_id = HOST_DNS_ID;
    sec.keyed = 1;
    hs.phase = mode == MODE_HANDSHAKE ? HS_SENT1 : HS_IDLE;
    hs.id = HOST_SESSION;
    dhcp.state = mode == MODE_BOUND ? DHCP_BOUND : DHCP_DISCOVER;
    dhcp.xid = HOST_XID;
    rel.in_next = 1;
    rel.in_count = 0;
    bulk.id = 7;
    bulk.state = BULK_RECEIVING;
    bulk.total = 100000;
    bulk.chunks = (bulk.total + BULK_CHUNK - 1) / BULK_CHUNK;
    bulk.next = 0;
    bulk.filled = 0;
}

unsigned char *guard_end(void);
void binary_stdin(void);

static int host_stats_replay(void)
{
    u8 mobile[0x800] = {0};
    float strength = 51.0f, damaged = 36.524f, hand = 20.0f;
    float *attribute = (float *)(mobile + MOBILE_ATTRIBUTES + STAT_BASE);
    float *skill = (float *)(mobile + MOBILE_SKILLS + 5 * 0x10 + STAT_BASE);
    float *last = (float *)(mobile + MOBILE_SKILLS + 26 * 0x10 + STAT_BASE);

    ses.welcomes = 1;
    replay_stat_keep(0, 0, (const u8 *)&strength);
    replay_stat_keep(0, 1, (const u8 *)&strength);
    replay_stat_keep(ATTRIBUTES + 5, 0, (const u8 *)&damaged);
    replay_stat_keep(ATTRIBUTES + 5, 1, (const u8 *)&damaged);
    replay_stat_keep(ATTRIBUTES + 26, 1, (const u8 *)&hand);
    attribute[0] = attribute[1] = 62.0f;
    skill[0] = skill[1] = 37.0f;
    last[0] = 10.0f;
    replay_stat_wait = 2;
    if (!replay_stat_frame(mobile) || attribute[0] != 62.0f)
        return 4;
    if (replay_stat_frame(mobile) || attribute[0] != strength || attribute[1] != strength ||
        skill[0] != damaged || skill[1] != damaged || last[0] != 10.0f || last[1] != hand)
        return 5;
    attribute[1] = 45.0f;
    if (replay_stat_frame(mobile) || attribute[1] != 45.0f)
        return 6;
    replay_stat_wait = 2;
    ses.welcomes++;
    if (replay_stat_frame(mobile) || replay_stat_wait || attribute[1] != 45.0f)
        return 7;
    puts("ok stat replay");
    return 0;
}

static int host_effect_parts(void)
{
    struct event e = {0};
    u32 offset = 0, part = 0, n;
    u32 parts = (PLAYER_EFFECT_BODY + EVENT_DATA - 6) / (EVENT_DATA - 5);

    u8 definition[PLAYER_EFFECT_BYTES] = {0};
    definition[4] = 1;
    for (n = 0; n < 8; n++)
        definition[120 + n * EFFECT_BYTES] = definition[121 + n * EFFECT_BYTES] = 0xFF;
    definition[120] = 79;
    definition[121] = 0;
    definition[122] = 0xFF;
    if (!player_effect_definition(definition))
        return 13;
    definition[124] = 3;
    if (player_effect_definition(definition))
        return 14;
    definition[124] = 0;
    definition[122] = 27;
    if (player_effect_definition(definition))
        return 15;
    ses.welcomes = 1;
    while (offset < PLAYER_EFFECT_BODY) {
        n = PLAYER_EFFECT_BODY - offset;
        if (n > EVENT_DATA - 5)
            n = EVENT_DATA - 5;
        e.length = n + 5;
        e.data[0] = PLAYER_EFFECTS;
        e.data[1] = (u8)part;
        e.data[2] = (u8)(part >> 8);
        e.data[3] = (u8)parts;
        e.data[4] = (u8)(parts >> 8);
        memset(e.data + 5, 0x42, n);
        player_effect_event(&e);
        offset += n;
        part++;
        if (offset < PLAYER_EFFECT_BODY && player_effect_pending)
            return 8;
    }
    if (!player_effect_pending || player_effect_in_size != PLAYER_EFFECT_BODY ||
        player_effect_in[PLAYER_EFFECT_BODY - 1] != 0x42)
        return 9;
    /* A short packet and an out-of-order part must not replace the completed snapshot. */
    e.length = 4;
    player_effect_event(&e);
    if (!player_effect_pending || player_effect_in_size != PLAYER_EFFECT_BODY)
        return 10;
    e.length = 5;
    e.data[1] = 1;
    e.data[2] = 0;
    player_effect_event(&e);
    if (!player_effect_pending || player_effect_in_size != PLAYER_EFFECT_BODY)
        return 11;
    e.data[1] = 0;
    e.data[3] = 1;
    e.data[4] = 0;
    player_effect_event(&e);
    if (!player_effect_pending || player_effect_in_size != 0)
        return 12;
    puts("ok effect parts");
    return 0;
}

static int host_snapshot_parts(void)
{
    u32 seq, expected = 0, i;
    host_setup();
    ses.state = SESSION_JOINED;
    ses.welcomes = player_effect_welcome = 1;
    rel.out_first = 1;
    rel.out_next = 1 + EVENTS_OUT;
    snapshot_stage = 10;
    snapshot_frame(0, 0, 0, 0);
    if (snapshot_stage != 10)
        return 21;
    player_effect_size = PLAYER_EFFECT_BODY;
    memset(player_effect_out, 0x42, player_effect_size);
    player_effect_parts = (player_effect_size + EVENT_DATA - 6) / (EVENT_DATA - 5);
    player_effect_part = 0;
    snapshot_stage = 11;
    snapshot_frame(0, 0, 0, 0);
    if (snapshot_stage != 11 || player_effect_part)
        return 22;
    rel.out_first = rel.out_next;
    while (snapshot_stage == 11) {
        seq = rel.out_next;
        snapshot_frame(0, 0, 0, 0);
        for (; seq != rel.out_next; seq++) {
            const struct event *e = &rel.out[seq % EVENTS_OUT];
            if (e->kind != EVENT_PLAYER || e->data[0] != PLAYER_EFFECTS ||
                (e->data[1] | e->data[2] << 8) != expected++)
                return 23;
            for (i = 5; i < e->length; i++)
                if (e->data[i] != 0x42)
                    return 24;
        }
        if (player_effect_parts && snapshot_stage != 11)
            return 25;
        rel.out_first = rel.out_next;
    }
    if (snapshot_stage != 12 || expected <= EVENTS_OUT || player_effect_parts)
        return 26;
    puts("ok snapshot parts");
    return 0;
}

static int host_snapshot_ack(void)
{
    struct event e = {0};
    snapshot_wait = 17;
    e.kind = EVENT_SNAPSHOT;
    e.length = 4;
    put32le(e.data, 16);
    event_handle(&e);
    if (snapshot_wait != 17 || snapshot_confirmed)
        return 16;
    put32le(e.data, 17);
    e.length = 3;
    event_handle(&e);
    if (snapshot_wait != 17 || snapshot_confirmed)
        return 17;
    e.length = 4;
    event_handle(&e);
    if (snapshot_wait || snapshot_confirmed != 17 || welcome_pending)
        return 18;
    e.kind = EVENT_LOAD;
    e.length = 8;
    memcpy(e.data, "old.ess", 8);
    load_event(&e);
    if (load_wanted)
        return 19;
    memcpy(e.data, "new.t3c", 8);
    load_event(&e);
    if (!load_wanted)
        return 20;
    puts("ok snapshot ack");
    return 0;
}

static int host_topics(void)
{
    u8 mobile[0x800] = {0};
    u8 malformed[] = {PLAYER_TOPICS, 2, 'o', 'k', 0, 'b', 'a', 'd'};
    u32 bad;
    const struct event *e;

    host_setup();
    ses.state = SESSION_JOINED;
    rel.out_first = 1;
    rel.out_next = 1 + EVENTS_OUT;
    snapshot_stage = 13;
    snapshot_frame(0, mobile, 0, 0);
    if (snapshot_stage != 13 || topics_known)
        return 31;
    rel.out_first++;
    snapshot_frame(0, mobile, 0, 0);
    e = &rel.out[(rel.out_next - 1) % EVENTS_OUT];
    if (snapshot_stage != 14 || !topics_known || e->kind != EVENT_PLAYER ||
        e->length != 2 || e->data[0] != PLAYER_TOPICS || e->data[1])
        return 32;
    bad = topics_bad;
    topics_apply(malformed, sizeof(malformed));
    if (topics_bad != bad + 1 || topics_in)
        return 33;
    puts("ok topics");
    return 0;
}

int main(int argc, char **argv)
{
    static u8 frame[HOST_MTU];
    u8 *end = guard_end(), *f;
    u32 frames = 0, handshakes = 0, len;
    int mode, lo, hi;

    if (argc == 2 && !strcmp(argv[1], "--stats-replay"))
        return host_stats_replay();
    if (argc == 2 && !strcmp(argv[1], "--topics"))
        return host_topics();
    if (argc == 2 && !strcmp(argv[1], "--effect-parts"))
        return host_effect_parts();
    if (argc == 2 && !strcmp(argv[1], "--snapshot-parts"))
        return host_snapshot_parts();
    if (argc == 2 && !strcmp(argv[1], "--snapshot-ack"))
        return host_snapshot_ack();
    verbose = argc > 1;
    binary_stdin();
    if (!end)
        return 2;
    host_setup();
    while ((mode = getchar()) != EOF) {
        lo = getchar();
        hi = getchar();
        if (hi == EOF || (len = (u32)(lo | hi << 8)) > HOST_MTU ||
            fread(frame, 1, len, stdin) != len)
            return 2;
        f = end - len;
        memcpy(f, frame, len);
        host_prepare(mode);
        if (len >= 14) {
            net.rx++;
            if (get16(f + 12) == ETH_ARP)
                rx_arp(f, len);
            else if (get16(f + 12) == ETH_IP)
                rx_ip(f, len);
        }
        handshakes += hs.phase == HS_GOT2;
        frames++;
    }
    printf("ok %u frames; sealed opened %u forged %u replayed %u; events %u stale %u; peers %u; "
           "chunks %u; handshake2 %u; dhcp offers %u acks %u; dns answers %u; echo %u arp %u\n",
           frames, sec.opened, sec.forged, sec.replayed, rel.delivered, rel.stale, ses.peers_in,
           bulk.arrived, handshakes, dhcp.offers, dhcp.acks, ses.dns_answers, net.echo, net.arp);
    return 0;
}
