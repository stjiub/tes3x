/* Network driver for the NIC, after nxdk's nvnetdrv: interrupt-driven receive, a polled transmit
 * ring, ARP, a UDP echo responder on port 26500 and a session with a PC server.
 *
 * Console commands:
 *   tes3xnet up A.B.C.D[/BITS] [SERVER[:PORT] [GATEWAY]]
 *                         bring the NIC up with that address, answer ARP and echo requests, and
 *                         with a server keep a session: HELLO until WELCOME, then a heartbeat
 *                         each second; five silent seconds start over
 *   tes3xnet probe A.B.C.D  ARP for an address three times and log the replies
 *   tes3xnet stat         log the counters
 *   tes3xnet down         stop the NIC
 *   tes3xnet [count]      broadcast count "TES3XNET" datagrams (default 8), from 0.0.0.0 unless up
 *
 * With [Xbox] NetAddress set, the first frame brings the NIC up as `up` would, from NetAddress,
 * NetServer and NetGateway; every launch and relaunch joins by itself. While joined, each frame
 * sends the player's state, and the server relays the other clients' states back.
 *
 * While up, the HalReturnToFirmware thunk points at a wrapper that stops the NIC first: a quick
 * reboot keeps the kernel, which would otherwise keep a connected interrupt object and a live DMA
 * ring inside the next title's memory.
 */

#include "tes3x_thunks.h"
#include "tes3xnt.h"

#ifndef TES3X_NET_WORLD
#error "define TES3X_NET_WORLD to the WorldController pointer"
#endif
#ifndef TES3X_NET_DATA_HANDLER
#error "define TES3X_NET_DATA_HANDLER to the DataHandler pointer"
#endif

typedef unsigned short u16;

typedef void *(__stdcall *fn_MmAllocateContiguousMemoryEx)(u32, u32, u32, u32, u32);
typedef void(__stdcall *fn_MmFreeContiguousMemory)(void *);
typedef u32(__stdcall *fn_MmGetPhysicalAddress)(void *);
typedef u32(__stdcall *fn_ExQueryNonVolatileSetting)(u32, u32 *, void *, u32, u32 *);
typedef void(__stdcall *fn_KeStallExecutionProcessor)(u32);
typedef u32(__stdcall *fn_HalGetInterruptVector)(u32, u8 *);
typedef void(__stdcall *fn_KeInitializeInterrupt)(void *, void *, void *, u32, u32, u32, u32);
typedef u8(__stdcall *fn_KeConnectInterrupt)(void *);
typedef u8(__stdcall *fn_KeDisconnectInterrupt)(void *);
typedef void(__stdcall *fn_KeInitializeDpc)(void *, void *, void *);
typedef u8(__stdcall *fn_KeInsertQueueDpc)(void *, void *, void *);
typedef u8(__stdcall *fn_KeRemoveQueueDpc)(void *);
typedef void(__stdcall *fn_HalReturnToFirmware)(u32);
typedef void(__stdcall *fn_KeInitializeTimerEx)(void *, u32);
typedef u8(__stdcall *fn_KeSetTimer)(void *, long long, void *);
typedef u8(__stdcall *fn_KeCancelTimer)(void *);
typedef u32(__stdcall *fn_PhyInitialize)(u8, void *);
typedef u32(__stdcall *fn_PhyGetLinkState)(u8);

#define MmAllocateContiguousMemoryEx \
    KFN(THUNK_MmAllocateContiguousMemoryEx, fn_MmAllocateContiguousMemoryEx)
#define MmFreeContiguousMemory KFN(THUNK_MmFreeContiguousMemory, fn_MmFreeContiguousMemory)
#define MmGetPhysicalAddress KFN(THUNK_MmGetPhysicalAddress, fn_MmGetPhysicalAddress)
#define ExQueryNonVolatileSetting \
    KFN(THUNK_ExQueryNonVolatileSetting, fn_ExQueryNonVolatileSetting)
#define KeStallExecutionProcessor \
    KFN(THUNK_KeStallExecutionProcessor, fn_KeStallExecutionProcessor)
#define HalGetInterruptVector KFN(THUNK_HalGetInterruptVector, fn_HalGetInterruptVector)
#define KeInitializeInterrupt KFN(THUNK_KeInitializeInterrupt, fn_KeInitializeInterrupt)
#define KeConnectInterrupt KFN(THUNK_KeConnectInterrupt, fn_KeConnectInterrupt)
#define KeDisconnectInterrupt KFN(THUNK_KeDisconnectInterrupt, fn_KeDisconnectInterrupt)
#define KeInitializeDpc KFN(THUNK_KeInitializeDpc, fn_KeInitializeDpc)
#define KeInsertQueueDpc KFN(THUNK_KeInsertQueueDpc, fn_KeInsertQueueDpc)
#define KeRemoveQueueDpc KFN(THUNK_KeRemoveQueueDpc, fn_KeRemoveQueueDpc)
#define KeInitializeTimerEx KFN(THUNK_KeInitializeTimerEx, fn_KeInitializeTimerEx)
#define KeSetTimer KFN(THUNK_KeSetTimer, fn_KeSetTimer)
#define KeCancelTimer KFN(THUNK_KeCancelTimer, fn_KeCancelTimer)

#define XC_FACTORY_ETHERNET_ADDR 0x101u
#define PAGE_READWRITE 0x04u
#define ORD_PHY_GET_LINK_STATE 252u
#define ORD_PHY_INITIALIZE 253u
#define LINK_10MBPS 0x04u
#define LINK_FULL_DUPLEX 0x08u
#define NIC_IRQ_LINE 4u
#define LEVEL_SENSITIVE 0u
#define CR0_WP 0x10000u

#define NIC(off) (*(volatile u32 *)(0xFEF00000u + (off)))
#define REG_IRQ_STATUS 0x000u
#define REG_IRQ_MASK 0x004u
#define REG_POLLING 0x008u
#define REG_DUPLEX 0x080u
#define REG_TX_CONTROL 0x084u
#define REG_TX_STATUS 0x088u
#define REG_PACKET_FILTER 0x08Cu
#define REG_OFFLOAD 0x090u
#define REG_RX_CONTROL 0x094u
#define REG_RX_STATUS 0x098u
#define REG_SLOT_TIME 0x09Cu
#define REG_TX_DEFERRAL 0x0A0u
#define REG_RX_DEFERRAL 0x0A4u
#define REG_MAC_A 0x0A8u
#define REG_MAC_B 0x0ACu
#define REG_MCAST_ADDR_A 0x0B0u
#define REG_MCAST_ADDR_B 0x0B4u
#define REG_MCAST_MASK_A 0x0B8u
#define REG_MCAST_MASK_B 0x0BCu
#define REG_TX_RING 0x100u
#define REG_RX_RING 0x104u
#define REG_RING_SIZES 0x108u
#define REG_TX_POLL 0x10Cu
#define REG_LINK_SPEED 0x110u
#define REG_TX_CURRENT_DESC 0x11Cu
#define REG_RX_CURRENT_DESC 0x120u
#define REG_TX_NEXT_DESC 0x134u
#define REG_RX_NEXT_DESC 0x138u
#define REG_TX_WATERMARK 0x13Cu
#define REG_SETUP7 0x140u
#define REG_TXRX_CONTROL 0x144u
#define REG_MII_STATUS 0x180u
#define REG_MII_MASK 0x184u
#define REG_ADAPTER 0x188u
#define REG_MII_SPEED 0x18Cu
#define REG_WAKEUP 0x200u

#define IRQ_RX_ERROR 0x01u
#define IRQ_RX 0x02u
#define IRQ_RX_NOBUF 0x04u
#define IRQ_LINK 0x40u
#define IRQ_RX_FORCED 0x80u
#define IRQ_ENABLED (IRQ_RX_ERROR | IRQ_RX | IRQ_RX_NOBUF | IRQ_LINK | IRQ_RX_FORCED)
#define TXRX_KICK 0x01u
#define TXRX_GET 0x02u
#define TXRX_DISABLE 0x04u
#define TXRX_IDLE 0x08u
#define TXRX_RESET 0x10u
#define ADAPTER_PHYVALID 0x40000u
#define ADAPTER_RUNNING 0x100000u
#define TX_LASTPACKET 0x0001u
#define TX_ERROR 0x4000u
#define TX_VALID 0x8000u
#define RX_DESCRIPTORVALID 0x0001u
#define RX_ERRORS 0x1F80u /* ERROR1-4, CRC, overflow */
#define RX_AVAIL 0x8000u

#define RX_RING 8u
#define TX_RING 4u
#define BUF 2048u
#define POOL_BYTES (4096u + (RX_RING + TX_RING) * BUF)
#define PORT 26500u
#define ETH_ARP 0x0806u
#define ETH_IP 0x0800u
#define MIN_FRAME 60u

struct descriptor {
    u32 paddr;
    u16 length;
    u16 flags;
} __attribute__((packed));

#define TICK_MS 250u
#define HELLO_TICKS 4u
#define HEARTBEAT_TICKS 4u
#define TIMEOUT_TICKS 20u
#define CPU_MHZ 733u

/* Session packet: "T3MP", version, type, then session, seq, ack, time and echoed peer time. */
#define T3MP_VERSION 1u
#define T3MP_HEADER 28u
#define T3MP_HELLO 1u
#define T3MP_WELCOME 2u
#define T3MP_HEARTBEAT 3u
#define T3MP_BYE 4u
#define T3MP_STATE 5u
#define T3MP_PEER 6u
#define T3MP_GONE 7u
#define SESSION_IDLE 0u
#define SESSION_ARP 1u
#define SESSION_HELLO 2u
#define SESSION_JOINED 3u

static struct {
    u32 up, ip, mask, irqs, dpcs, rx, rx_errors, rx_nobuf, arp, echo, tx, tx_full, tx_errors;
} net;
static struct {
    u32 state, server, port, gateway, hop, hop_known, id, client, seq, peer_seq, peer_time;
    u32 ticks, quiet, hellos, welcomes, beats_out, beats_in, timeouts, gaps;
    u32 rtt_last, rtt_min, rtt_max, rtt_sum, rtt_count;
    u32 states_out, peers_in;
    u8 hop_mac[6];
} ses;

/* The player's state: flags, position, heading (orientation z), then the interior cell's name.
 * Exterior cells are left empty; the grid follows from the position. */
#define STATE_IN_WORLD 1u
#define STATE_INTERIOR 2u
#define STATE_BYTES 52u
#define CELL_NAME 32u
#define PEERS 8u
#define PEER_TIMEOUT_US 5000000u

/* The last relayed state of each other client, by the server's client id. */
static struct {
    u32 client, seq, time;
    u8 state[STATE_BYTES];
} peers[PEERS];
static u32 ini_checked;
static u32 probe_ip, probe_hits;
static u32 timer[0x28 / 4];
static u32 tick_dpc[0x1C / 4];
static u8 mac[6];
static u8 *pool;
static volatile struct descriptor *rx_ring, *tx_ring;
static u8 *rx_buf, *tx_buf;
static u32 rx_head, tx_tail;
static u32 interrupt[0x70 / 4];
static u32 dpc[0x1C / 4];
static fn_HalReturnToFirmware firmware_original;

static void nic_stop(void);

static u32 lock(void)
{
    u32 flags;
    __asm__ volatile("pushfl\n\tpopl %0\n\tcli" : "=r"(flags) : : "memory");
    return flags;
}

static void unlock(u32 flags)
{
    __asm__ volatile("pushl %0\n\tpopfl" : : "r"(flags) : "memory", "cc");
}

/* The kernel's own image is mapped at 0x80010000 with its PE headers intact. */
static void *kernel_export(u32 ordinal)
{
    u8 *base = (u8 *)0x80010000u;
    u32 *dir, *functions;
    u8 *nt;

    if (*(u16 *)base != 0x5A4D)
        return 0;
    nt = base + *(u32 *)(base + 0x3C);
    if (*(u32 *)nt != 0x4550)
        return 0;
    dir = (u32 *)(base + *(u32 *)(nt + 0x78));
    if (ordinal < dir[4] || ordinal - dir[4] >= dir[5])
        return 0;
    functions = (u32 *)(base + dir[7]);
    return base + functions[ordinal - dir[4]];
}

static void set_thunk(u32 slot, void *fn)
{
    u32 cr0, flags = lock();
    __asm__ volatile("movl %%cr0, %0" : "=r"(cr0));
    __asm__ volatile("movl %0, %%cr0" : : "r"(cr0 & ~CR0_WP) : "memory");
    *(void **)slot = fn;
    __asm__ volatile("movl %0, %%cr0" : : "r"(cr0) : "memory");
    unlock(flags);
}

static void __stdcall firmware_hook(u32 routine)
{
    fn_HalReturnToFirmware original = firmware_original;

    if (net.up) {
        nic_stop();
        tes3x_log("net.stopped_for_firmware", routine);
    }
    original(routine);
}

static u32 get16(const u8 *p)
{
    return (u32)p[0] << 8 | p[1];
}

static u32 get32(const u8 *p)
{
    return (u32)p[0] << 24 | (u32)p[1] << 16 | (u32)p[2] << 8 | p[3];
}

static void put16(u8 *p, u32 v)
{
    p[0] = (u8)(v >> 8);
    p[1] = (u8)v;
}

static void put32(u8 *p, u32 v)
{
    p[0] = (u8)(v >> 24);
    p[1] = (u8)(v >> 16);
    p[2] = (u8)(v >> 8);
    p[3] = (u8)v;
}

static void put32le(u8 *p, u32 v)
{
    p[0] = (u8)v;
    p[1] = (u8)(v >> 8);
    p[2] = (u8)(v >> 16);
    p[3] = (u8)(v >> 24);
}

static void copy(u8 *d, const u8 *s, u32 n)
{
    while (n--)
        *d++ = *s++;
}

static void ip_checksum(u8 *ip)
{
    u32 i, sum = 0;

    put16(ip + 10, 0);
    for (i = 0; i < 20; i += 2)
        sum += get16(ip + i);
    while (sum >> 16)
        sum = (sum & 0xFFFF) + (sum >> 16);
    put16(ip + 10, ~sum & 0xFFFF);
}

/* A free transmit slot, or 0. The NIC completes descriptors in ring order. */
static u8 *tx_begin(void)
{
    if (tx_ring[tx_tail].flags & TX_VALID) {
        net.tx_full++;
        return 0;
    }
    if (tx_ring[tx_tail].flags & TX_ERROR)
        net.tx_errors++;
    return tx_buf + tx_tail * BUF;
}

static void tx_commit(u32 len)
{
    volatile struct descriptor *d = &tx_ring[tx_tail];
    u8 *f = tx_buf + tx_tail * BUF;

    for (; len < MIN_FRAME; len++)
        f[len] = 0;
    d->paddr = MmGetPhysicalAddress(f);
    d->length = (u16)(len - 1);
    d->flags = TX_LASTPACKET | TX_VALID;
    NIC(REG_TXRX_CONTROL) = TXRX_KICK;
    tx_tail = (tx_tail + 1) % TX_RING;
    net.tx++;
}

static void rx_arp(const u8 *f, u32 len)
{
    u8 *o;

    if (len < 42 || get16(f + 14) != 1 || get16(f + 16) != ETH_IP)
        return;
    if (probe_ip && get32(f + 28) == probe_ip && get16(f + 20) == 2)
        probe_hits++;
    if (ses.hop && get32(f + 28) == ses.hop) {
        copy(ses.hop_mac, f + 22, 6);
        ses.hop_known = 1;
    }
    if (get16(f + 20) != 1 || !net.ip || get32(f + 38) != net.ip)
        return;
    net.arp++;
    if (!(o = tx_begin()))
        return;
    copy(o, f + 6, 6);
    copy(o + 6, mac, 6);
    put16(o + 12, ETH_ARP);
    copy(o + 14, f + 14, 6);
    put16(o + 20, 2);
    copy(o + 22, mac, 6);
    put32(o + 28, net.ip);
    copy(o + 32, f + 22, 10); /* the requester's MAC and address */
    tx_commit(42);
}

static u32 get32le(const u8 *p)
{
    return p[0] | (u32)p[1] << 8 | (u32)p[2] << 16 | (u32)p[3] << 24;
}

/* Microseconds from the TSC, without a 64-bit division: cycles / 1024 * 1024 / 733. */
static u32 now_us(void)
{
    u32 lo, hi;

    __asm__ volatile("rdtsc" : "=a"(lo), "=d"(hi));
    return (u32)(((u64)(hi << 22 | lo >> 10) * 5722) >> 12);
}

/* Caller holds the lock. Asking for our own address is a gratuitous ARP. */
static void arp_request(u32 target)
{
    u32 i;
    u8 *o = tx_begin();

    if (!o)
        return;
    for (i = 0; i < 6; i++) {
        o[i] = 0xFF;
        o[32 + i] = 0;
    }
    copy(o + 6, mac, 6);
    put16(o + 12, ETH_ARP);
    put16(o + 14, 1);
    put16(o + 16, ETH_IP);
    o[18] = 6;
    o[19] = 4;
    put16(o + 20, 1);
    copy(o + 22, mac, 6);
    put32(o + 28, net.ip);
    put32(o + 38, target);
    tx_commit(42);
}

static void announce(void)
{
    u32 flags = lock();
    arp_request(net.ip);
    unlock(flags);
}

/* Caller holds the lock. Goes to the next hop's MAC, so it waits for ARP. */
static void udp_send(u32 dst, u32 port, const u8 *payload, u32 n)
{
    u8 *o;

    if (!ses.hop_known || n > BUF - 42 || !(o = tx_begin()))
        return;
    copy(o, ses.hop_mac, 6);
    copy(o + 6, mac, 6);
    put16(o + 12, ETH_IP);
    o[14] = 0x45;
    o[15] = 0;
    put16(o + 16, 28 + n);
    put16(o + 18, ses.seq);
    put16(o + 20, 0);
    o[22] = 64;
    o[23] = 17;
    put32(o + 26, net.ip);
    put32(o + 30, dst);
    ip_checksum(o + 14);
    put16(o + 34, PORT);
    put16(o + 36, port);
    put16(o + 38, 8 + n);
    put16(o + 40, 0);
    copy(o + 42, payload, n);
    tx_commit(42 + n);
}

static void session_send(u32 type, const u8 *body, u32 n)
{
    u8 p[T3MP_HEADER + STATE_BYTES];

    p[0] = 'T';
    p[1] = '3';
    p[2] = 'M';
    p[3] = 'P';
    p[4] = T3MP_VERSION;
    p[5] = (u8)type;
    p[6] = p[7] = 0;
    put32le(p + 8, ses.id);
    put32le(p + 12, ++ses.seq);
    put32le(p + 16, ses.peer_seq);
    put32le(p + 20, now_us());
    put32le(p + 24, ses.peer_time);
    copy(p + T3MP_HEADER, body, n);
    udp_send(ses.server, ses.port, p, T3MP_HEADER + n);
}

/* Latest state wins: one server sequences everything it sends us, so an older seq is stale. */
static void peer_rx(u32 client, u32 seq, const u8 *state)
{
    u32 i, slot = PEERS;

    for (i = 0; i < PEERS; i++) {
        if (peers[i].client == client) {
            slot = i;
            break;
        }
        if (slot == PEERS && !peers[i].client)
            slot = i;
    }
    if (slot == PEERS || (peers[slot].client == client && seq <= peers[slot].seq))
        return;
    peers[slot].client = client;
    peers[slot].seq = seq;
    peers[slot].time = now_us();
    copy(peers[slot].state, state, STATE_BYTES);
    ses.peers_in++;
}

/* Caller holds the lock (the receive DPC). */
static void session_rx(const u8 *p, u32 n)
{
    u32 type, seq, echo, rtt, i;

    if (n < T3MP_HEADER || p[4] != T3MP_VERSION)
        return;
    type = p[5];
    seq = get32le(p + 12);
    echo = get32le(p + 24);
    if (type == T3MP_WELCOME) {
        if (ses.state != SESSION_HELLO || n < T3MP_HEADER + 4)
            return;
        ses.id = get32le(p + 8);
        ses.client = get32le(p + 28);
        ses.state = SESSION_JOINED;
        ses.ticks = 0;
        ses.peer_seq = seq;
        ses.welcomes++;
        for (i = 0; i < PEERS; i++)
            peers[i].client = 0;
    } else {
        if (ses.state != SESSION_JOINED || get32le(p + 8) != ses.id)
            return;
        if (type == T3MP_BYE) {
            ses.state = SESSION_HELLO;
            ses.ticks = HELLO_TICKS;
            return;
        }
        if (type == T3MP_HEARTBEAT)
            ses.beats_in++;
        if (type == T3MP_PEER && n >= T3MP_HEADER + 4 + STATE_BYTES)
            peer_rx(get32le(p + T3MP_HEADER), seq, p + T3MP_HEADER + 4);
        if (type == T3MP_GONE && n >= T3MP_HEADER + 4)
            for (i = 0; i < PEERS; i++)
                if (peers[i].client == get32le(p + T3MP_HEADER))
                    peers[i].client = 0;
        if (seq > ses.peer_seq + 1)
            ses.gaps += seq - ses.peer_seq - 1;
        if (seq > ses.peer_seq)
            ses.peer_seq = seq;
    }
    ses.peer_time = get32le(p + 20);
    ses.quiet = 0;
    rtt = now_us() - echo;
    if (echo && rtt < 10000000u) {
        ses.rtt_last = rtt;
        if (!ses.rtt_count || rtt < ses.rtt_min)
            ses.rtt_min = rtt;
        if (rtt > ses.rtt_max)
            ses.rtt_max = rtt;
        ses.rtt_sum += rtt;
        ses.rtt_count++;
    }
}

/* Every TICK_MS from the timer DPC; caller holds the lock. */
static void session_tick(void)
{
    u8 hello[10];

    ses.ticks++;
    ses.quiet++;
    if (ses.state == SESSION_ARP) {
        if (ses.hop_known) {
            ses.state = SESSION_HELLO;
            ses.ticks = HELLO_TICKS;
        } else if (ses.ticks % HELLO_TICKS == 1) {
            arp_request(ses.hop);
        }
    }
    if (ses.state == SESSION_HELLO && ses.ticks >= HELLO_TICKS) {
        ses.ticks = 0;
        ses.id = 0;
        ses.peer_seq = 0;
        ses.peer_time = 0;
        copy(hello, mac, 6);
        put32le(hello + 6, TES3X_BUILD_ID);
        session_send(T3MP_HELLO, hello, sizeof(hello));
        ses.hellos++;
    } else if (ses.state == SESSION_JOINED) {
        if (ses.quiet >= TIMEOUT_TICKS) {
            ses.timeouts++;
            ses.state = SESSION_HELLO;
            ses.ticks = HELLO_TICKS - 1;
        } else if (ses.ticks >= HEARTBEAT_TICKS) {
            ses.ticks = 0;
            session_send(T3MP_HEARTBEAT, 0, 0);
            ses.beats_out++;
        }
    }
}

static void arm_timer(void)
{
    KeSetTimer(timer, -(long long)TICK_MS * 10000, tick_dpc);
}

static void __stdcall tick(void *d, void *context, void *a, void *b)
{
    u32 flags;

    (void)d;
    (void)context;
    (void)a;
    (void)b;
    flags = lock();
    if (net.up && ses.state != SESSION_IDLE)
        session_tick();
    unlock(flags);
    if (net.up)
        arm_timer();
}

static void rx_ip(const u8 *f, u32 len)
{
    static const u8 ping[8] = {'T', 'E', 'S', '3', 'X', 'P', 'N', 'G'};
    const u8 *ip = f + 14, *udp;
    u32 ihl, total, dst, i;
    u8 *o;

    if (len < 34 || (ip[0] >> 4) != 4 || ip[9] != 17)
        return;
    ihl = (ip[0] & 0x0F) * 4;
    total = get16(ip + 2);
    dst = get32(ip + 16);
    if (total > len - 14 || total < ihl + 8 + 8 || (dst != net.ip && dst != 0xFFFFFFFFu))
        return;
    udp = ip + ihl;
    if (get16(udp + 2) != PORT)
        return;
    if (udp[8] == 'T' && udp[9] == '3' && udp[10] == 'M' && udp[11] == 'P') {
        if (ses.state != SESSION_IDLE && get32(ip + 12) == ses.server &&
            get16(udp) == ses.port)
            session_rx(udp + 8, total - ihl - 8);
        return;
    }
    for (i = 0; i < 8; i++)
        if (udp[8 + i] != ping[i])
            return;
    net.echo++;
    if (!(o = tx_begin()))
        return;
    /* Reply with a fixed 20-byte header, the ports swapped and the payload echoed. */
    copy(o, f + 6, 6);
    copy(o + 6, mac, 6);
    put16(o + 12, ETH_IP);
    o[14] = 0x45;
    o[15] = 0;
    put16(o + 16, total - ihl + 20);
    put16(o + 18, get16(ip + 4));
    put16(o + 20, 0);
    o[22] = 64;
    o[23] = 17;
    put32(o + 26, net.ip);
    copy(o + 30, ip + 12, 4);
    ip_checksum(o + 14);
    put16(o + 34, PORT);
    copy(o + 36, udp, 2);
    copy(o + 38, udp + 4, 2);
    put16(o + 40, 0);
    copy(o + 42, udp + 8, total - ihl - 8);
    o[42 + 6] = 'O'; /* TES3XPON */
    o[42 + 7] = 'N';
    tx_commit(14 + 20 + total - ihl);
}

/* Scans every slot from rx_head on: the NIC keeps its ring position across a reset, so after a
 * restart it can fill slots in an order the driver did not start from. */
static void rx_drain(void)
{
    u32 i, found;

    do {
        found = 0;
        for (i = 0; i < RX_RING; i++) {
            u32 slot = (rx_head + i) % RX_RING;
            volatile struct descriptor *d = &rx_ring[slot];
            u32 flags = d->flags;
            const u8 *f = rx_buf + slot * BUF;

            if (flags & RX_AVAIL)
                continue;
            if ((flags & RX_DESCRIPTORVALID) && !(flags & RX_ERRORS) && d->length >= 14) {
                u32 len = d->length, type = get16(f + 12);
                net.rx++;
                if (type == ETH_ARP)
                    rx_arp(f, len);
                else if (type == ETH_IP)
                    rx_ip(f, len);
            } else {
                net.rx_errors++;
            }
            d->length = BUF;
            d->flags = RX_AVAIL;
            rx_head = (slot + 1) % RX_RING;
            found = 1;
            break;
        }
    } while (found);
    NIC(REG_TXRX_CONTROL) = TXRX_GET;
}

static u8 __stdcall nic_isr(void *interrupt_object, void *context)
{
    (void)interrupt_object;
    (void)context;
    if (!NIC(REG_IRQ_STATUS) && !NIC(REG_MII_STATUS))
        return 0;
    NIC(REG_IRQ_MASK) = 0;
    net.irqs++;
    KeInsertQueueDpc(dpc, 0, 0);
    return 1;
}

static void __stdcall nic_dpc(void *d, void *context, void *a, void *b)
{
    u32 irq, mii, flags;

    (void)d;
    (void)context;
    (void)a;
    (void)b;
    flags = lock();
    net.dpcs++;
    for (;;) {
        irq = NIC(REG_IRQ_STATUS);
        mii = NIC(REG_MII_STATUS);
        if (!irq && !mii)
            break;
        NIC(REG_MII_STATUS) = mii;
        NIC(REG_IRQ_STATUS) = irq;
        if (irq & IRQ_RX_NOBUF)
            net.rx_nobuf++;
        rx_drain();
    }
    if (net.up)
        NIC(REG_IRQ_MASK) = IRQ_ENABLED;
    unlock(flags);
}

static void stop_txrx(void)
{
    u32 i;

    NIC(REG_RX_CONTROL) &= ~1u;
    NIC(REG_TX_CONTROL) &= ~1u;
    for (i = 0; i < 5000 && ((NIC(REG_RX_STATUS) | NIC(REG_TX_STATUS)) & 1u); i++)
        KeStallExecutionProcessor(10);
    NIC(REG_TXRX_CONTROL) = TXRX_DISABLE;
    for (i = 0; i < 1000 && !(NIC(REG_TXRX_CONTROL) & TXRX_IDLE); i++)
        KeStallExecutionProcessor(50);
    NIC(REG_TXRX_CONTROL) = 0;
}

static void nic_reset(void)
{
    stop_txrx();
    NIC(REG_TXRX_CONTROL) = TXRX_DISABLE | TXRX_RESET;
    KeStallExecutionProcessor(10);
    NIC(REG_TXRX_CONTROL) = TXRX_DISABLE;
    NIC(REG_TX_RING) = 0;
    NIC(REG_RX_RING) = 0;
}

static void nic_stop(void)
{
    u32 flags, i;

    if (net.up) {
        flags = lock();
        if (ses.state == SESSION_JOINED)
            session_send(T3MP_BYE, 0, 0);
        ses.state = SESSION_IDLE;
        unlock(flags);
        for (i = 0; i < 1000 && (tx_ring[(tx_tail + TX_RING - 1) % TX_RING].flags & TX_VALID);
             i++)
            KeStallExecutionProcessor(5);
        KeCancelTimer(timer);
        KeRemoveQueueDpc(tick_dpc);
    }
    NIC(REG_IRQ_MASK) = 0;
    if (net.up) {
        KeDisconnectInterrupt(interrupt);
        KeRemoveQueueDpc(dpc);
        set_thunk(THUNK_HalReturnToFirmware, (void *)firmware_original);
    }
    nic_reset();
    NIC(REG_ADAPTER) = 0;
    if (pool)
        MmFreeContiguousMemory(pool);
    pool = 0;
    net.up = 0;
    tes3x_log("net.stopped", 0);
}

/* 1 on success. With irq, receive is interrupt-driven and the firmware hook is installed. */
static int nic_start(u32 ip, int irq)
{
    fn_PhyInitialize phy_init = (fn_PhyInitialize)kernel_export(ORD_PHY_INITIALIZE);
    fn_PhyGetLinkState phy_link = (fn_PhyGetLinkState)kernel_export(ORD_PHY_GET_LINK_STATE);
    u32 type, i, link, vector, tx_phys, next;
    u8 irql;

    if (!phy_init || !phy_link) {
        tes3x_log("net.no_phy_export", 0);
        return 0;
    }
    if (ExQueryNonVolatileSetting(XC_FACTORY_ETHERNET_ADDR, &type, mac, 6, 0) & 0x80000000u) {
        tes3x_log("net.no_mac", 0);
        return 0;
    }
    tes3x_log_hex3("net.mac", mac[0] << 24 | mac[1] << 16 | mac[2] << 8 | mac[3],
                   mac[4] << 8 | mac[5], ip);
    pool = MmAllocateContiguousMemoryEx(POOL_BYTES, 0, 0xFFFFFFFFu, 0, PAGE_READWRITE);
    if (!pool) {
        tes3x_log("net.no_memory", POOL_BYTES);
        return 0;
    }
    for (i = 0; i < 4096; i++)
        pool[i] = 0;
    rx_ring = (volatile struct descriptor *)pool;
    tx_ring = rx_ring + RX_RING;
    rx_buf = pool + 4096;
    tx_buf = rx_buf + RX_RING * BUF;
    rx_head = tx_tail = 0;
    net.ip = ip;

    /* Erase what a previous driver left, as forcedeth's open does: with the adapter still marked
     * running from our own last start, the NIC reported no receive buffers in the next process. */
    NIC(REG_PACKET_FILTER) = 0;
    NIC(REG_TX_CONTROL) = 0;
    NIC(REG_RX_CONTROL) = 0;
    NIC(REG_ADAPTER) = 0;
    KeStallExecutionProcessor(50);
    nic_reset();
    NIC(REG_TXRX_CONTROL) = 0;
    NIC(REG_MII_MASK) = 0;
    NIC(REG_IRQ_MASK) = 0;
    NIC(REG_WAKEUP) = 0;
    NIC(REG_POLLING) = 0;
    NIC(REG_TX_POLL) = 0;
    NIC(REG_LINK_SPEED) = 0;
    NIC(REG_TX_STATUS) = NIC(REG_TX_STATUS);
    NIC(REG_RX_STATUS) = NIC(REG_RX_STATUS);
    NIC(REG_IRQ_STATUS) = NIC(REG_IRQ_STATUS);
    NIC(REG_MII_STATUS) = NIC(REG_MII_STATUS);

    NIC(REG_MAC_A) = mac[0] | mac[1] << 8 | mac[2] << 16 | (u32)mac[3] << 24;
    NIC(REG_MAC_B) = mac[4] | mac[5] << 8;
    NIC(REG_MCAST_ADDR_A) = 0xFFFFFFFFu;
    NIC(REG_MCAST_ADDR_B) = 0xFFFFu;
    NIC(REG_MCAST_MASK_A) = 0xFFFFFFFFu;
    NIC(REG_MCAST_MASK_B) = 0xFFFFu;
    NIC(REG_OFFLOAD) = 0x5EE;
    NIC(REG_PACKET_FILTER) = 0x7F0020;
    NIC(REG_DUPLEX) = 0x003B0F3E;
    NIC(REG_SLOT_TIME) = 0x7F00 | mac[5];
    NIC(REG_TX_DEFERRAL) = 0x16070F;
    NIC(REG_RX_DEFERRAL) = 0x16;
    NIC(REG_SETUP7) = 0x300010;
    NIC(REG_TX_WATERMARK) = 0x300010;
    NIC(REG_TX_RING) = MmGetPhysicalAddress((void *)tx_ring);
    NIC(REG_RX_RING) = MmGetPhysicalAddress((void *)rx_ring);
    NIC(REG_RING_SIZES) = (RX_RING - 1) << 16 | (TX_RING - 1);
    /* The NIC loads its descriptor pointers from the ring registers only on a reset: without
     * this one, most restarts after our own stop received nothing and wedged transmit. */
    NIC(REG_TXRX_CONTROL) = TXRX_DISABLE | TXRX_RESET;
    KeStallExecutionProcessor(10);
    NIC(REG_TXRX_CONTROL) = TXRX_DISABLE;
    KeStallExecutionProcessor(10);
    NIC(REG_TXRX_CONTROL) = 0;

    NIC(REG_ADAPTER) = 1u << 24 | ADAPTER_PHYVALID;
    NIC(REG_MII_SPEED) = 1u << 8 | 5;
    NIC(REG_MII_MASK) = 0x08;
    KeStallExecutionProcessor(50);
    if (phy_init(0, 0) != 0) {
        tes3x_log("net.phy_fail", 0);
        nic_stop();
        return 0;
    }
    NIC(REG_ADAPTER) |= ADAPTER_RUNNING;
    KeStallExecutionProcessor(50);

    link = phy_link(0);
    tes3x_log_hex("net.link", link);
    if (link & LINK_FULL_DUPLEX)
        NIC(REG_DUPLEX) &= ~2u;
    else
        NIC(REG_DUPLEX) |= 2u;
    NIC(REG_LINK_SPEED) = (link & LINK_10MBPS ? 1000 : 100) | 0x10000;

    /* Start transmitting at the descriptor the NIC will fetch; after the reset it reads as the
     * ring base, but a restart that lands elsewhere would otherwise jam the ring. */
    tx_phys = MmGetPhysicalAddress((void *)tx_ring);
    next = NIC(REG_TX_CURRENT_DESC);
    if (next >= tx_phys && next < tx_phys + TX_RING * 8 && !((next - tx_phys) & 7))
        tx_tail = (next - tx_phys) / 8;
    tes3x_log_hex3("net.tx_desc", tx_phys, next, NIC(REG_TX_NEXT_DESC));
    tes3x_log_hex3("net.rx_desc", MmGetPhysicalAddress((void *)rx_ring),
                   NIC(REG_RX_CURRENT_DESC), NIC(REG_RX_NEXT_DESC));

    if (irq) {
        for (i = 0; i < RX_RING; i++) {
            rx_ring[i].paddr = MmGetPhysicalAddress(rx_buf + i * BUF);
            rx_ring[i].length = BUF;
            rx_ring[i].flags = RX_AVAIL;
        }
        vector = HalGetInterruptVector(NIC_IRQ_LINE, &irql);
        KeInitializeInterrupt(interrupt, (void *)nic_isr, 0, vector, irql, LEVEL_SENSITIVE, 1);
        KeInitializeDpc(dpc, (void *)nic_dpc, 0);
        if (!KeConnectInterrupt(interrupt)) {
            tes3x_log("net.no_interrupt", vector);
            nic_stop();
            return 0;
        }
        firmware_original = *(fn_HalReturnToFirmware *)THUNK_HalReturnToFirmware;
        set_thunk(THUNK_HalReturnToFirmware, (void *)firmware_hook);
        net.up = 1;
        NIC(REG_IRQ_MASK) = IRQ_ENABLED;
        NIC(REG_RX_CONTROL) |= 1u;
        KeInitializeTimerEx(timer, 0);
        KeInitializeDpc(tick_dpc, (void *)tick, 0);
        arm_timer();
    }
    NIC(REG_TX_CONTROL) |= 1u;
    NIC(REG_TXRX_CONTROL) = TXRX_KICK | TXRX_GET;
    return 1;
}

static void broadcast(u32 count)
{
    static const char magic[8] = "TES3XNET";
    int temporary = !net.up;
    u32 i, j, sent = 0, flags, slot;
    u8 *f;

    if (temporary && !nic_start(0, 0))
        return;
    for (i = 0; i < count; i++) {
        flags = lock();
        slot = tx_tail;
        f = tx_begin();
        if (f) {
            for (j = 0; j < 6; j++) {
                f[j] = 0xFF;
                f[6 + j] = mac[j];
            }
            put16(f + 12, ETH_IP);
            f[14] = 0x45;
            f[15] = 0;
            put16(f + 16, 20 + 8 + 22);
            put16(f + 18, i);
            put16(f + 20, 0);
            f[22] = 64;
            f[23] = 17;
            put32(f + 26, net.ip);
            put32(f + 30, 0xFFFFFFFFu);
            ip_checksum(f + 14);
            put16(f + 34, PORT);
            put16(f + 36, PORT);
            put16(f + 38, 8 + 22);
            put16(f + 40, 0);
            copy(f + 42, (const u8 *)magic, 8);
            put32le(f + 50, i);
            put32le(f + 54, count);
            copy(f + 58, mac, 6);
            tx_commit(64);
        }
        unlock(flags);
        if (!f) {
            tes3x_log("net.tx_full", i);
            break;
        }
        for (j = 0; j < 20000 && (tx_ring[slot].flags & TX_VALID); j++)
            KeStallExecutionProcessor(5);
        if (tx_ring[slot].flags & TX_VALID) {
            tes3x_log("net.tx_timeout", i);
            break;
        }
        tes3x_log_hex3("net.tx_done", i, tx_ring[slot].flags, j * 5);
        sent++;
    }
    tes3x_log("net.sent", sent);
    if (temporary)
        nic_stop();
}

static void stat(void)
{
    u32 i, flags, ring = 0;

    tes3x_log("net.up", net.up);
    if (net.up) {
        for (i = 0; i < RX_RING; i++)
            ring = ring << 4 | (rx_ring[i].flags >> 12);
        tes3x_log_hex3("net.regs", NIC(REG_IRQ_STATUS), NIC(REG_RX_CONTROL),
                       NIC(REG_RX_STATUS));
        tes3x_log_hex3("net.ring", ring, NIC(REG_IRQ_MASK), NIC(REG_ADAPTER));
        tes3x_log_hex3("net.rx_desc", MmGetPhysicalAddress((void *)rx_ring),
                       NIC(REG_RX_CURRENT_DESC), NIC(REG_RX_NEXT_DESC));
        /* Drains anything the interrupt path missed. */
        flags = lock();
        rx_drain();
        unlock(flags);
    }
    tes3x_log_hex3("net.rx", net.rx, net.rx_errors, net.rx_nobuf);
    tes3x_log_hex3("net.irq", net.irqs, net.dpcs, 0);
    tes3x_log_hex3("net.answered", net.arp, net.echo, 0);
    tes3x_log_hex3("net.tx", net.tx, net.tx_full, net.tx_errors);
    if (ses.server) {
        tes3x_log_hex3("net.session", ses.state, ses.id, ses.client);
        tes3x_log_hex3("net.joins", ses.hellos, ses.welcomes, ses.timeouts);
        tes3x_log_hex3("net.beats", ses.beats_out, ses.beats_in, ses.gaps);
        tes3x_log_hex3("net.rtt_us", ses.rtt_min, ses.rtt_count ? ses.rtt_sum / ses.rtt_count : 0,
                       ses.rtt_max);
        tes3x_log_hex3("net.states", ses.states_out, ses.peers_in, 0);
        for (i = 0; i < PEERS; i++)
            if (peers[i].client)
                tes3x_log_hex3("net.peer_state", peers[i].client, peers[i].seq,
                               now_us() - peers[i].time);
    }
}

static const char *word(const char *text, const char *w)
{
    for (; *w; text++, w++) {
        char c = *text;
        if (c >= 'A' && c <= 'Z')
            c += 'a' - 'A';
        if (c != *w)
            return 0;
    }
    return *text == ' ' || !*text ? text : 0;
}

static const char *skip(const char *text)
{
    while (*text == ' ')
        text++;
    return text;
}

static const char *number(const char *text, u32 *value)
{
    *value = 0;
    if (*text < '0' || *text > '9')
        return 0;
    while (*text >= '0' && *text <= '9')
        *value = *value * 10 + (u32)(*text++ - '0');
    return text;
}

/* A.B.C.D into *ip, host order; the text after it, or 0 if malformed. */
static const char *address(const char *text, u32 *ip)
{
    u32 part, i;

    *ip = 0;
    for (i = 0; i < 4; i++) {
        if (!(text = number(text, &part)) || part > 255)
            return 0;
        *ip = *ip << 8 | part;
        if (i < 3 && *text++ != '.')
            return 0;
    }
    return *ip ? text : 0;
}

/* up A.B.C.D[/BITS] [SERVER[:PORT] [GATEWAY]] */
static void command_up(const char *text)
{
    u32 ip, bits = 24, server = 0, port = PORT, gateway = 0;

    if (!(text = address(skip(text), &ip)))
        goto usage;
    if (*text == '/' && (!(text = number(text + 1, &bits)) || bits < 1 || bits > 30))
        goto usage;
    text = skip(text);
    if (*text) {
        if (!(text = address(text, &server)))
            goto usage;
        if (*text == ':' && (!(text = number(text + 1, &port)) || !port || port > 65535))
            goto usage;
        text = skip(text);
        if (*text && !(text = address(text, &gateway)))
            goto usage;
        if (*skip(text))
            goto usage;
    }
    if (!nic_start(ip, 1))
        return;
    net.mask = 0xFFFFFFFFu << (32 - bits);
    announce();
    tes3x_log_hex3("net.up", ip, bits, server);
    if (server) {
        u32 flags = lock();
        u32 *w = (u32 *)&ses;
        u32 n;

        for (n = 0; n < sizeof(ses) / 4; n++)
            w[n] = 0;
        ses.server = server;
        ses.port = port;
        ses.gateway = gateway;
        /* Off the subnet the frames go to the gateway's MAC. */
        ses.hop = (server ^ ip) & net.mask && gateway ? gateway : server;
        if ((server ^ ip) & net.mask && !gateway)
            tes3x_log("net.no_gateway", server);
        ses.state = SESSION_ARP;
        unlock(flags);
        tes3x_log_hex3("net.session_start", server, port, ses.hop);
    }
    return;
usage:
    tes3x_log("net.usage", 0);
}

/* ARP for an address three times and log the replies with the receive and transmit counters,
 * so a restart can be checked against any host that answers ARP, such as the router. */
static void probe(const char *text)
{
    u32 target, i, flags, rx = net.rx, nobuf = net.rx_nobuf, full = net.tx_full;

    if (!net.up || !(text = address(text, &target)) || *skip(text)) {
        tes3x_log("net.usage", 0);
        return;
    }
    probe_ip = target;
    probe_hits = 0;
    for (i = 0; i < 3; i++) {
        flags = lock();
        arp_request(target);
        unlock(flags);
        KeStallExecutionProcessor(30000);
    }
    for (i = 0; i < 20 && probe_hits < 3; i++)
        KeStallExecutionProcessor(10000);
    tes3x_log_hex3("net.probe", probe_hits, net.rx - rx, 0);
    tes3x_log_hex3("net.probe_errors", net.rx_nobuf - nobuf, net.tx_full - full, net.rx_errors);
    probe_ip = 0;
}

typedef int(__cdecl *fn_ini_get_string)(const char *, const char *, const char *,
                                        char *, int, const char *);

static u32 ini_text(const char *key, char *out, u32 size)
{
    fn_ini_get_string get = (fn_ini_get_string)TES3X_INI_GET_STRING;
    u32 i;

    for (i = 0; i < size; i++)
        out[i] = 0;
    get("Xbox", key, "", out, (int)size - 1, (const char *)TES3X_INI_PATH);
    for (i = 0; out[i]; i++)
        ;
    while (i && out[i - 1] == ' ')
        out[--i] = 0;
    return i;
}

/* The ini reader needs the game drive, which is not mounted at process entry. */
static void autostart(void)
{
    char line[80];
    u32 n;

    if (!(n = ini_text("NetAddress", line, 24)))
        return;
    line[n++] = ' ';
    n += ini_text("NetServer", line + n, 24);
    line[n++] = ' ';
    ini_text("NetGateway", line + n, 24);
    tes3x_log("net.autostart", 1);
    command_up(line);
}

static int mapped(const void *p)
{
    return (u32)p >= 0x10000u && (u32)p < 0x80000000u;
}

static int plausible(const void *p)
{
    return mapped(p) && !((u32)p & 3);
}

/* WorldController -> MobController -> MobilePlayer -> Reference, or 0 outside the world. */
static const u8 *player_reference(void)
{
    const u8 *world = *(const u8 **)TES3X_NET_WORLD, *mobs, *mobile, *const *list, *ref;

    if (!plausible(world) || !plausible(mobs = *(const u8 **)(world + 0x5C)))
        return 0;
    list = *(const u8 *const **)(mobs + 0x24);
    if (!plausible(list) || !plausible(mobile = *list))
        return 0;
    ref = *(const u8 **)(mobile + 0x14);
    return plausible(ref) ? ref : 0;
}

static void log_text(const char *tag, const char *text)
{
    char line[64];
    u32 n = 0;

    while (*tag && n < 24)
        line[n++] = *tag++;
    line[n++] = ' ';
    while (text && *text && n < sizeof(line) - 1)
        line[n++] = *text++;
    line[n++] = '\n';
    tes3x_log_raw(line, n);
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
    put32le(state, flags);
    copy(state + 4, ref + 0x38, 12); /* position */
    copy(state + 16, ref + 0x34, 4); /* orientation z */
}

/* Once per frame, from the Game::Update hook. */
void tes3x_net_frame(void)
{
    static u8 last_cell[CELL_NAME];
    static u32 logged_player, known[PEERS];
    u8 state[STATE_BYTES];
    const u8 *ref;
    u32 i, flags;

    if (!ini_checked) {
        ini_checked = 1;
        autostart();
    }
    if (!net.up || !(ref = player_reference()))
        return;
    player_state(ref, state);
    if (!logged_player) {
        const u8 *base = *(const u8 **)(ref + 0x28);
        logged_player = 1;
        log_text("net.player", plausible(base) && mapped(*(const char **)(base + 0x2C))
                 ? *(const char **)(base + 0x2C) : 0);
    }
    for (i = 0; i < CELL_NAME && state[20 + i] == last_cell[i]; i++)
        ;
    if (i < CELL_NAME) {
        copy(last_cell, state + 20, CELL_NAME);
        log_text("net.cell", state[20] ? (const char *)state + 20 : "(exterior)");
    }

    flags = lock();
    if (ses.state == SESSION_JOINED) {
        session_send(T3MP_STATE, state, STATE_BYTES);
        ses.states_out++;
    }
    for (i = 0; i < PEERS; i++) {
        u32 client = peers[i].client, was = known[i];
        /* A peer whose leave notice was lost drops out once it goes quiet. */
        if (client && now_us() - peers[i].time > PEER_TIMEOUT_US)
            peers[i].client = client = 0;
        if (client == was)
            continue;
        known[i] = client;
        unlock(flags);
        if (was)
            tes3x_log("net.peer_left", was);
        if (client)
            tes3x_log("net.peer", client);
        flags = lock();
    }
    unlock(flags);
}

int tes3x_net_command(const char *text)
{
    const char *rest;
    u32 value;

    if (!(text = word(text, "tes3xnet")))
        return 0;
    text = skip(text);
    if ((rest = word(text, "up"))) {
        if (net.up)
            tes3x_log("net.already_up", net.ip);
        else
            command_up(rest);
    } else if ((rest = word(text, "down")) && !*skip(rest)) {
        if (net.up)
            nic_stop();
    } else if ((rest = word(text, "probe"))) {
        probe(skip(rest));
    } else if ((rest = word(text, "stat")) && !*skip(rest)) {
        stat();
    } else if (!*text) {
        broadcast(8);
    } else if ((rest = number(text, &value)) && !*skip(rest) && value) {
        broadcast(value);
    } else {
        tes3x_log("net.usage", 0);
    }
    return 1;
}
