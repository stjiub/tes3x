/* The payload's receive path on the PC. tests/test_net_host.py copies tes3xnet.c beside this file
 * with its inline assembly removed and the NIC's registers moved into host_nic, and writes a
 * tes3x_thunks.h whose slots are host_thunks. Frames come on stdin as a mode byte, a little-endian
 * length and the frame, placed so they end at an unreadable page, and go to rx_arp or rx_ip as
 * the receive DPC's drain sends them. The mode picks the session state the frame meets. */

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

unsigned char host_nic[0x1000];
void *host_thunks[64];
static int verbose;

#include "tes3xnet.c"

#define HOST_MTU 1518u
#define HOST_SESSION 0x11223344u
#define HOST_XID 0x01020304u
#define HOST_DNS_ID 0x4242u
#define MODE_JOINED 0
#define MODE_HANDSHAKE 1
#define MODE_RESOLVE 2
#define MODE_BOUND 3

static volatile struct descriptor host_tx[TX_RING];
static u8 host_tx_buf[TX_RING * BUF];

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
    ses.server = ses.dns = ses.gateway = ses.hop = 0x0A000202u;
    ses.port = PORT;
    ses.hop_known = 1;
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

int main(int argc, char **argv)
{
    static u8 frame[HOST_MTU];
    u8 *end = guard_end(), *f;
    u32 frames = 0, handshakes = 0, len;
    int mode, lo, hi;

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
