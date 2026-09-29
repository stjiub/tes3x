/* Network driver for the NIC, after nxdk's nvnetdrv: interrupt-driven receive, a polled transmit
 * ring, ARP, a UDP echo responder on port 26500 and a session with a PC server.
 *
 * Console commands:
 *   tes3xnet up A.B.C.D[/BITS] [SERVER[:PORT] [GATEWAY|- [DNS]]]
 *                         bring the NIC up with that address, answer ARP and echo requests, and
 *                         with a server keep a session: HELLO until WELCOME, then a heartbeat
 *                         each second; five silent seconds start over. A SERVER that is a name
 *                         is looked up at DNS (default GATEWAY) first, and again after a timeout
 *   tes3xnet probe A.B.C.D  ARP for an address three times and log the replies
 *   tes3xnet stat         log the counters
 *   tes3xnet say TEXT     send TEXT to the other clients as a reliable event
 *   tes3xnet menusim 0|1|auto  force the world paused or running under menus, or follow the
 *                         session (the default: running while joined)
 *   tes3xnet down         stop the NIC
 *   tes3xnet [count]      broadcast count "TES3XNET" datagrams (default 8), from 0.0.0.0 unless up
 *
 * With [Xbox] NetAddress set, the first frame brings the NIC up as `up` would, from NetAddress,
 * NetServer, NetGateway and NetDns; every launch and relaunch joins by itself. While joined, each frame
 * sends the player's state, and the server relays the other clients' states back. Each other
 * client near the player is drawn as a ghost NPC from the plugin the pipeline adds. While joined,
 * menus do not pause the world, the rest menu closes as it opens, and the server's clock sets the
 * time globals; a console joins only once a game is loaded, offering its own clock. The server names
 * one client the authority for each loaded cell: it runs those actors and sends their states, and
 * the others take them out of the simulation and place them from the states.
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
#ifndef TES3X_NET_COMPILE_RUN
#error "define TES3X_NET_COMPILE_RUN to the VA of CompileAndRun"
#endif
#ifndef TES3X_NET_MENU_GATE
#error "define TES3X_NET_MENU_GATE to the menu mode jne in mainLoopBeforeInput"
#endif
#ifndef TES3X_NET_MOB_GATE
#error "define TES3X_NET_MOB_GATE to the menu mode jne before ProcessMobs in Game::Update"
#endif
#ifndef TES3X_NET_SERVICE_ACTOR
#error "define TES3X_NET_SERVICE_ACTOR to ui::getServiceActor"
#endif
#if !defined(TES3X_NET_FIND_MENU) || !defined(TES3X_NET_UI_ID) || !defined(TES3X_NET_TRIGGER_EVENT)
#error "define TES3X_NET_FIND_MENU, TES3X_NET_UI_ID and TES3X_NET_TRIGGER_EVENT to the UI functions"
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
#define T3MP_VERSION 4u
#define T3MP_HEADER 28u
#define T3MP_HELLO 1u
#define T3MP_WELCOME 2u
#define T3MP_HEARTBEAT 3u
#define T3MP_BYE 4u
#define T3MP_STATE 5u
#define T3MP_PEER 6u
#define T3MP_GONE 7u
#define T3MP_EVENTS 8u
#define T3MP_REFUSE 9u
#define T3MP_CLOCK 10u
#define T3MP_ACTORS 11u
#define SESSION_IDLE 0u
#define SESSION_ARP 1u
#define SESSION_HELLO 2u
#define SESSION_JOINED 3u
#define SESSION_RESOLVE 4u
#define SESSION_REFUSED 5u
/* The clock: GameHour, Day, Month, Year, DaysPassed and TimeScale, the globals' raw floats. HELLO
 * carries the client's own, CLOCK the server's. */
#define CLOCK_GLOBALS 6u
#define CLOCK_BYTES (CLOCK_GLOBALS * 4u)
#define HELLO_BYTES (18u + CLOCK_BYTES)
#define DNS_PORT 53u
#define HOST_NAME 64u

static struct {
    u32 up, ip, mask, irqs, dpcs, rx, rx_errors, rx_nobuf, arp, echo, tx, tx_full, tx_errors;
} net;
static struct {
    u32 state, server, port, gateway, hop, hop_known, id, client, seq, peer_seq, peer_time;
    u32 ticks, quiet, hellos, welcomes, beats_out, beats_in, timeouts, gaps;
    u32 rtt_last, rtt_min, rtt_max, rtt_sum, rtt_count;
    u32 states_out, peers_in;
    u32 dns, dns_id, dns_queries, dns_answers;
    u32 plugins, plugins_hash, refused_hash, refused_plugins;
    char host[HOST_NAME]; /* the server's name, if it is not an address */
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

#define SAMPLES 8u

/* The recent relayed states of each other client, by the server's client id. The receive DPC
 * only copies bytes: it runs without the game's floating-point state saved. */
static struct {
    u32 client, seq, time, head, count;
    struct {
        u32 time;
        u8 state[STATE_BYTES];
    } samples[SAMPLES];
} peers[PEERS];
/* Reliable events: each side numbers its events from 1 per session and resends every unacked one
 * each tick; the receiver takes only the next number, so delivery is in order. An EVENTS packet
 * is the other side's last delivered number, a count, then events of EVENT_HEADER + length. */
#define EVENT_TEXT 1u
#define EVENT_HEADER 12u /* seq, kind, length, origin client */
#define EVENT_DATA 64u
#define EVENTS_OUT 16u
#define EVENTS_IN 16u
#define EVENTS_BYTES 512u

struct event {
    u32 seq, kind, length, origin;
    u8 data[EVENT_DATA];
};

static struct {
    struct event out[EVENTS_OUT]; /* by seq % EVENTS_OUT */
    struct event in[EVENTS_IN];   /* delivered, for the game thread */
    u32 out_first, out_next, out_sent; /* oldest unacked, next to assign, next never sent */
    u32 in_next, in_head, in_count;
    u32 queued, sent, resent, acked, delivered, handled, stale, full, dropped;
} rel;

/* local: this console's clock, copied by the game thread for HELLO; server: the latest CLOCK,
 * copied by the receive DPC. Bytes only on both sides of the lock. */
static struct {
    u8 local[CLOCK_BYTES], server[CLOCK_BYTES];
    u32 local_valid, received, applied;
} game_clock;

static u32 ghost_places, ghost_moves, ghost_failures;
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
static void log_text(const char *tag, const char *text);
static void load_order(void);
static int plausible(const void *p);
static void actors_rx(u32 origin, u32 seq, const u8 *p, u32 n);
static void actors_reset(void);

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
    u8 p[T3MP_HEADER + EVENTS_BYTES];

    if (n > EVENTS_BYTES)
        return;
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
    if (peers[slot].client != client)
        peers[slot].count = 0;
    peers[slot].client = client;
    peers[slot].seq = seq;
    peers[slot].time = now_us();
    peers[slot].samples[peers[slot].head].time = peers[slot].time;
    copy(peers[slot].samples[peers[slot].head].state, state, STATE_BYTES);
    peers[slot].head = (peers[slot].head + 1) % SAMPLES;
    if (peers[slot].count < SAMPLES)
        peers[slot].count++;
    ses.peers_in++;
}

static void events_reset(void)
{
    rel.dropped += rel.out_next - rel.out_first;
    rel.out_first = rel.out_next = rel.out_sent = 1;
    rel.in_next = 1;
    rel.in_count = 0;
}

/* The ack, then with resend every unacked event that fits; caller holds the lock. */
static void events_send(int resend)
{
    u8 body[EVENTS_BYTES];
    u32 n = 8, count = 0, seq;

    put32le(body, rel.in_next - 1);
    for (seq = rel.out_first; resend && seq != rel.out_next; seq++) {
        const struct event *e = &rel.out[seq % EVENTS_OUT];
        if (n + EVENT_HEADER + e->length > EVENTS_BYTES)
            break;
        put32le(body + n, e->seq);
        body[n + 4] = (u8)e->kind;
        body[n + 5] = (u8)(e->kind >> 8);
        body[n + 6] = (u8)e->length;
        body[n + 7] = (u8)(e->length >> 8);
        put32le(body + n + 8, 0);
        copy(body + n + EVENT_HEADER, e->data, e->length);
        n += EVENT_HEADER + e->length;
        count++;
        if ((int)(seq - rel.out_sent) < 0)
            rel.resent++;
        else
            rel.out_sent = seq + 1;
        rel.sent++;
    }
    body[4] = (u8)count;
    body[5] = body[6] = body[7] = 0;
    session_send(T3MP_EVENTS, body, n);
}

/* Game thread. 0 if the queue is full or there is no session. */
static int event_queue(u32 kind, const u8 *data, u32 length)
{
    u32 flags = lock();
    struct event *e;
    int ok = ses.state == SESSION_JOINED && rel.out_next - rel.out_first < EVENTS_OUT &&
             length <= EVENT_DATA;

    if (ok) {
        e = &rel.out[rel.out_next % EVENTS_OUT];
        e->seq = rel.out_next++;
        e->kind = kind;
        e->length = length;
        e->origin = 0;
        copy(e->data, data, length);
        rel.queued++;
        events_send(1);
    } else {
        rel.full++;
    }
    unlock(flags);
    return ok;
}

/* Caller holds the lock (the receive DPC). */
static void events_rx(const u8 *p, u32 n)
{
    u32 ack, count, off = 8, seq, length;
    struct event *e;

    if (n < 8)
        return;
    ack = get32le(p);
    while (rel.out_first != rel.out_next && (int)(ack - rel.out_first) >= 0) {
        rel.out_first++;
        rel.acked++;
    }
    count = p[4];
    if (!count)
        return;
    while (count-- && off + EVENT_HEADER <= n) {
        seq = get32le(p + off);
        length = p[off + 6] | (u32)p[off + 7] << 8;
        if (off + EVENT_HEADER + length > n)
            break;
        if (seq != rel.in_next || length > EVENT_DATA || rel.in_count == EVENTS_IN) {
            if ((int)(seq - rel.in_next) < 0)
                rel.stale++;
        } else {
            e = &rel.in[(rel.in_head + rel.in_count++) % EVENTS_IN];
            e->seq = seq;
            e->kind = p[off + 4] | (u32)p[off + 5] << 8;
            e->length = length;
            e->origin = get32le(p + off + 8);
            copy(e->data, p + off + EVENT_HEADER, length);
            rel.in_next++;
            rel.delivered++;
        }
        off += EVENT_HEADER + length;
    }
    events_send(0);
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
    if (type == T3MP_REFUSE) {
        if (ses.state != SESSION_HELLO || n < T3MP_HEADER + 8)
            return;
        ses.state = SESSION_REFUSED;
        ses.refused_hash = get32le(p + 28);
        ses.refused_plugins = get32le(p + 32);
        return;
    }
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
        events_reset();
        actors_reset();
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
        if (type == T3MP_EVENTS)
            events_rx(p + T3MP_HEADER, n - T3MP_HEADER);
        if (type == T3MP_ACTORS && n >= T3MP_HEADER + 8)
            actors_rx(get32le(p + T3MP_HEADER), seq, p + T3MP_HEADER + 4, n - T3MP_HEADER - 4);
        if (type == T3MP_CLOCK && n >= T3MP_HEADER + CLOCK_BYTES && seq > ses.peer_seq) {
            copy(game_clock.server, p + T3MP_HEADER, CLOCK_BYTES);
            game_clock.received++;
        }
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
    /* Only a heartbeat is answered at once; a relayed state echoes whatever we sent last. */
    if (type == T3MP_HEARTBEAT && echo && rtt < 10000000u) {
        ses.rtt_last = rtt;
        if (!ses.rtt_count || rtt < ses.rtt_min)
            ses.rtt_min = rtt;
        if (rtt > ses.rtt_max)
            ses.rtt_max = rtt;
        ses.rtt_sum += rtt;
        ses.rtt_count++;
    }
}

/* Off the subnet, frames go to the gateway's MAC. Caller holds the lock. */
static void route_to(u32 target)
{
    u32 hop = (target ^ net.ip) & net.mask && ses.gateway ? ses.gateway : target;

    if (hop != ses.hop) {
        ses.hop = hop;
        ses.hop_known = 0;
    }
    ses.state = SESSION_ARP;
    ses.ticks = 0;
}

/* An A query for ses.host; caller holds the lock. */
static void dns_query(void)
{
    u8 q[12 + HOST_NAME + 2 + 4];
    u32 n = 12, label = 12, i;

    ses.dns_id = (now_us() & 0xFFFF) | 1;
    for (i = 0; i < 12; i++)
        q[i] = 0;
    put16(q, ses.dns_id);
    q[2] = 0x01; /* recursion desired */
    q[5] = 1;    /* one question */
    for (i = 0; ses.host[i]; i++) {
        if (ses.host[i] == '.') {
            q[label] = (u8)(n - label);
            label = ++n;
        } else {
            q[++n] = (u8)ses.host[i];
        }
    }
    q[label] = (u8)(n - label);
    q[++n] = 0;
    put16(q + n + 1, 1); /* type A */
    put16(q + n + 3, 1); /* class IN */
    udp_send(ses.dns, DNS_PORT, q, n + 5);
    ses.dns_queries++;
}

/* Offset past a possibly compressed name, or 0 if it runs off the end. */
static u32 dns_skip_name(const u8 *p, u32 n, u32 off)
{
    while (off < n) {
        if (!p[off])
            return off + 1;
        if ((p[off] & 0xC0) == 0xC0)
            return off + 2 <= n ? off + 2 : 0;
        off += p[off] + 1u;
    }
    return 0;
}

/* The first A record of a reply to our query; caller holds the lock (the receive DPC). */
static void dns_rx(const u8 *p, u32 n)
{
    u32 off = 12, questions, answers;

    if (ses.state != SESSION_RESOLVE || n < 12 || get16(p) != ses.dns_id || !(p[2] & 0x80) ||
        (p[3] & 0x0F))
        return;
    questions = get16(p + 4);
    answers = get16(p + 6);
    while (questions--)
        if (!(off = dns_skip_name(p, n, off)) || (off += 4) > n)
            return;
    while (answers--) {
        if (!(off = dns_skip_name(p, n, off)) || off + 10 > n)
            return;
        if (get16(p + off) == 1 && get16(p + off + 2) == 1 && get16(p + off + 8) == 4 &&
            off + 14 <= n && get32(p + off + 10)) {
            ses.server = get32(p + off + 10);
            ses.dns_answers++;
            route_to(ses.server);
            return;
        }
        off += 10 + get16(p + off + 8);
    }
}

/* Every TICK_MS from the timer DPC; caller holds the lock. */
static void session_tick(void)
{
    u8 hello[HELLO_BYTES];

    ses.ticks++;
    ses.quiet++;
    if (ses.state == SESSION_ARP) {
        if (ses.hop_known) {
            ses.state = ses.server ? SESSION_HELLO : SESSION_RESOLVE;
            ses.ticks = HELLO_TICKS;
        } else if (ses.ticks % HELLO_TICKS == 1) {
            arp_request(ses.hop);
        }
    }
    if (ses.state == SESSION_RESOLVE && ses.ticks >= HELLO_TICKS) {
        ses.ticks = 0;
        dns_query();
    }
    /* Joining waits for a clock to offer, which the game has only once a game is loaded. */
    if (ses.state == SESSION_HELLO && ses.ticks >= HELLO_TICKS && game_clock.local_valid) {
        ses.ticks = 0;
        ses.id = 0;
        ses.peer_seq = 0;
        ses.peer_time = 0;
        copy(hello, mac, 6);
        put32le(hello + 6, TES3X_BUILD_ID);
        put32le(hello + 10, ses.plugins_hash);
        put32le(hello + 14, ses.plugins);
        copy(hello + 18, game_clock.local, CLOCK_BYTES);
        session_send(T3MP_HELLO, hello, sizeof(hello));
        ses.hellos++;
    } else if (ses.state == SESSION_JOINED) {
        if (rel.out_first != rel.out_next)
            events_send(1);
        if (ses.quiet >= TIMEOUT_TICKS) {
            ses.timeouts++;
            ses.state = SESSION_HELLO;
            ses.ticks = HELLO_TICKS - 1;
            if (ses.host[0]) { /* the name may point elsewhere now */
                ses.server = 0;
                route_to(ses.dns);
            }
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
    if (get16(udp) == DNS_PORT && ses.dns && get32(ip + 12) == ses.dns) {
        dns_rx(udp + 8, total - ihl - 8);
        return;
    }
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
    if (ses.host[0]) {
        log_text("net.host", ses.host);
        tes3x_log_hex3("net.dns", ses.dns, ses.dns_queries, ses.dns_answers);
    }
    if (ses.server || ses.host[0]) {
        tes3x_log_hex3("net.session", ses.state, ses.id, ses.client);
        tes3x_log_hex3("net.joins", ses.hellos, ses.welcomes, ses.timeouts);
        tes3x_log_hex3("net.beats", ses.beats_out, ses.beats_in, ses.gaps);
        tes3x_log_hex3("net.rtt_us", ses.rtt_min, ses.rtt_count ? ses.rtt_sum / ses.rtt_count : 0,
                       ses.rtt_max);
        tes3x_log_hex3("net.states", ses.states_out, ses.peers_in, 0);
        tes3x_log_hex3("net.load_order", ses.plugins, ses.plugins_hash, 0);
        tes3x_log_hex3("net.events_out", rel.queued, rel.sent, rel.resent);
        tes3x_log_hex3("net.events_ack", rel.acked, rel.full, rel.dropped);
        tes3x_log_hex3("net.events_in", rel.delivered, rel.handled, rel.stale);
        for (i = 0; i < PEERS; i++)
            if (peers[i].client)
                tes3x_log_hex3("net.peer_state", peers[i].client, peers[i].seq,
                               now_us() - peers[i].time);
        tes3x_log_hex3("net.ghosts", ghost_places, ghost_moves, ghost_failures);
    }
}

/* Two 6-byte jnes skip the world while a menu is open: one in mainLoopBeforeInput (simulation
 * clock, controllers), one in Game::Update (ProcessMobs, idles, cell loading, weather). NOPs let
 * the world run under menus. The simulation clock is the fld operand 0x1A bytes past the first. */
#define WORLD_MENU_MODE 0xD2
#define GATE_CLOCK 0x1A
static u8 *const gates[2] = {(u8 *)TES3X_NET_MENU_GATE, (u8 *)TES3X_NET_MOB_GATE};
static u8 gate_original[2][6];
#define GATES_UNEXPECTED 2
static u32 gate_saved, gates_open;
/* `menusim`: 0 follows the session, else 1 + the forced state. */
static u32 menu_forced;

static void menu_sim(u32 on)
{
    u32 cr0, flags, i, g;

    if (on == gates_open || gate_saved == GATES_UNEXPECTED)
        return;
    if (!gate_saved) {
        for (g = 0; g < 2; g++)
            if (gates[g][0] != 0x0F || gates[g][1] != 0x85) {
                tes3x_log_hex3("net.menu_gate_unexpected", g, gates[g][0], gates[g][1]);
                gate_saved = GATES_UNEXPECTED;
                return;
            }
        for (g = 0; g < 2; g++)
            copy(gate_original[g], gates[g], 6);
        gate_saved = 1;
    }
    flags = lock();
    __asm__ volatile("movl %%cr0, %0" : "=r"(cr0));
    __asm__ volatile("movl %0, %%cr0" : : "r"(cr0 & ~CR0_WP) : "memory");
    for (g = 0; g < 2; g++)
        for (i = 0; i < 6; i++)
            gates[g][i] = on ? 0x90 : gate_original[g][i];
    __asm__ volatile("movl %0, %%cr0" : : "r"(cr0) : "memory");
    unlock(flags);
    gates_open = on;
    tes3x_log("net.menusim", on);
}

typedef void *(__cdecl *fn_find_menu)(u32 id);
typedef u32(__cdecl *fn_ui_id)(const char *name);
typedef void(__attribute__((thiscall)) *fn_trigger_event)(void *element, u32 event, int d0,
                                                          int d1, void *source);
#define MENU_VISIBLE 0x7E
#define EVENT_PAD_B 0xFFFF8081

static void run_script(const char *text);
static u32 rest_blocked;

/* Resting and waiting advance the clock, which is shared while joined: the rest menu is closed
 * as soon as it opens, from a bed, the pad or ShowRestMenu alike. */
static void rest_block(void)
{
    static u32 menu_id;
    u8 *menu;

    if (!menu_id)
        menu_id = ((fn_ui_id)TES3X_NET_UI_ID)("MenuRestWait");
    menu = ((fn_find_menu)TES3X_NET_FIND_MENU)(menu_id);
    if (!menu || !menu[MENU_VISIBLE])
        return;
    /* The Xbox menu has no cancel button; B closes it. */
    ((fn_trigger_event)TES3X_NET_TRIGGER_EVENT)(menu, EVENT_PAD_B, 0, 0, menu);
    run_script("MessageBox \"You cannot rest or wait in a multiplayer session.\"");
    tes3x_log("net.rest_blocked", ++rest_blocked);
}

/* The NPC a dialogue is with stays put while the world runs: it leaves the simulation, as actors
 * outside the loaded cells do, until the dialogue closes. Combat or a drop in its health releases
 * it and closes the dialogue, so holding an NPC in conversation cannot set it up to be hit. */
#define MOBILE_FLAGS 0x10
#define MOBILE_SIMULATED 0x4u /* ActiveInSimulation, MWSE's activeAI */
#define MOBILE_IN_COMBAT 0x10000u
#define MOBILE_HEALTH 0x2BC /* the current value of the health statistic */

typedef u8 *(__cdecl *fn_service_actor)(void);

static u8 *held;
static u32 held_simulated, holds, hold_breaks;
static float held_health;

static void hold_release(void)
{
    if (held && held_simulated)
        *(u32 *)(held + MOBILE_FLAGS) |= MOBILE_SIMULATED;
    held = 0;
}

/* Services open on top of the dialogue; B closes them one at a time, then the dialogue. */
static void dialogue_close(void)
{
    static const char *const names[] = {
        "MenuBarter",     "MenuService",       "MenuServiceSpells", "MenuServiceTraining",
        "MenuServiceRepair", "MenuServiceTravel", "MenuSpellmaking", "MenuEnchantment",
        "MenuDialog"};
    static u32 ids[sizeof(names) / sizeof(names[0])];
    u32 i;
    u8 *menu;

    for (i = 0; i < sizeof(names) / sizeof(names[0]); i++) {
        if (!ids[i])
            ids[i] = ((fn_ui_id)TES3X_NET_UI_ID)(names[i]);
        menu = ((fn_find_menu)TES3X_NET_FIND_MENU)(ids[i]);
        if (menu && menu[MENU_VISIBLE]) {
            ((fn_trigger_event)TES3X_NET_TRIGGER_EVENT)(menu, EVENT_PAD_B, 0, 0, menu);
            return;
        }
    }
}

static int hold_remote(u8 *actor);

static void hold_frame(void)
{
    u8 *actor = gates_open ? ((fn_service_actor)TES3X_NET_SERVICE_ACTOR)() : 0;
    u32 *flags;
    float health;

    if (actor != held)
        hold_release();
    if (hold_remote(plausible(actor) ? actor : 0) || !plausible(actor))
        return;
    flags = (u32 *)(actor + MOBILE_FLAGS);
    health = *(const float *)(actor + MOBILE_HEALTH);
    if (!held && !(*flags & MOBILE_IN_COMBAT)) {
        held = actor;
        held_simulated = *flags & MOBILE_SIMULATED;
        held_health = health;
        tes3x_log_hex3("net.hold", ++holds, (u32)(int)health, held_simulated);
    }
    if ((*flags & MOBILE_IN_COMBAT) || (held && health < held_health)) {
        if (held)
            tes3x_log_hex3("net.hold_broken", ++hold_breaks, *flags & MOBILE_IN_COMBAT,
                           (u32)(int)health);
        hold_release();
        dialogue_close();
        return;
    }
    *flags &= ~MOBILE_SIMULATED;
}

/* While joined, the world runs under menus as it does for the other players, and nobody rests.
 * Only in the world: the main menu at boot has no player to simulate. */
static void menu_frame(int in_world)
{
    u32 joined = ses.state == SESSION_JOINED && in_world;

    menu_sim(menu_forced ? menu_forced - 1 : joined);
    if (joined)
        rest_block();
    hold_frame();
}

static void menu_stat(void)
{
    const u8 *world = *(const u8 **)TES3X_NET_WORLD, *gate = (const u8 *)TES3X_NET_MENU_GATE;
    const float *clock = *(const float *const *)(gate + GATE_CLOCK);

    if (plausible(world) && gate[GATE_CLOCK - 2] == 0xD9 && gate[GATE_CLOCK - 1] == 0x05 &&
        plausible(clock))
        tes3x_log_hex3("net.menu_mode", world[WORLD_MENU_MODE], (u32)(int)(*clock * 1000.0f),
                       gate[0] == 0x90);
    tes3x_log_hex3("net.menu_sim", gates_open, menu_forced, rest_blocked);
    tes3x_log_hex3("net.holds", holds, hold_breaks, held != 0);
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

/* A host name into out (HOST_NAME bytes): letters, digits, hyphens and dots between labels. */
static const char *host_name(const char *text, char *out)
{
    u32 n = 0, label = 0;

    for (;; text++) {
        char c = *text;
        if (c == '.' && label && label <= 63) {
            label = 0;
        } else if ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') ||
                   c == '-') {
            label++;
        } else {
            break;
        }
        if (n >= HOST_NAME - 1)
            return 0;
        out[n++] = c;
    }
    out[n] = 0;
    return n && label && label <= 63 ? text : 0;
}

/* up A.B.C.D[/BITS] [SERVER[:PORT] [GATEWAY [DNS]]], where SERVER is an address or a name */
static void command_up(const char *text)
{
    u32 ip, bits = 24, server = 0, port = PORT, gateway = 0, dns = 0;
    char host[HOST_NAME];
    const char *after;

    host[0] = 0;
    if (!(text = address(skip(text), &ip)))
        goto usage;
    if (*text == '/' && (!(text = number(text + 1, &bits)) || bits < 1 || bits > 30))
        goto usage;
    text = skip(text);
    if (*text) {
        if ((after = address(text, &server)) && (*after == ':' || *after == ' ' || !*after))
            text = after;
        else if (!(text = host_name(text, host)))
            goto usage;
        if (*text == ':' && (!(text = number(text + 1, &port)) || !port || port > 65535))
            goto usage;
        text = skip(text);
        if (*text == '-') /* no gateway, a DNS server follows */
            text++;
        else if (*text && !(text = address(text, &gateway)))
            goto usage;
        text = skip(text);
        if (*text && !(text = address(text, &dns)))
            goto usage;
        if (*skip(text))
            goto usage;
        if (host[0])
            server = 0;
        if (host[0] && !dns && !(dns = gateway)) {
            tes3x_log("net.no_dns", 0);
            goto usage;
        }
    }
    if (!nic_start(ip, 1))
        return;
    net.mask = 0xFFFFFFFFu << (32 - bits);
    announce();
    tes3x_log_hex3("net.up", ip, bits, server);
    if (server || host[0]) {
        u32 flags = lock();
        u32 *w = (u32 *)&ses;
        u32 n;

        for (n = 0; n < sizeof(ses) / 4; n++)
            w[n] = 0;
        ses.server = server;
        ses.port = port;
        ses.gateway = gateway;
        ses.dns = dns;
        copy((u8 *)ses.host, (const u8 *)host, HOST_NAME);
        unlock(flags);
        load_order();
        flags = lock();
        route_to(server ? server : dns);
        unlock(flags);
        if ((ses.hop ^ ip) & net.mask)
            tes3x_log("net.no_gateway", ses.hop);
        if (host[0])
            log_text("net.resolving", host);
        tes3x_log_hex3("net.session_start", server ? server : dns, port, ses.hop);
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
    char line[24 + HOST_NAME + 8 + 2 * 24];
    u32 n, server;

    if (!(n = ini_text("NetAddress", line, 24)))
        return;
    line[n++] = ' ';
    if ((server = ini_text("NetServer", line + n, HOST_NAME + 8))) {
        n += server;
        line[n++] = ' ';
        if (!(server = ini_text("NetGateway", line + n, 24)))
            line[n++] = '-';
        n += server;
        line[n++] = ' ';
        ini_text("NetDns", line + n, 24);
    }
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

/* FNV-1a over the loaded plugins' names in load order, lowercased and each ended by a zero, as
 * tes3x_net.py load_order_hash. The file list is [DataHandler]: count +0xC, files +0xAE70; a
 * file's name is inline at +0xC. */
#define FILES_COUNT 0xC
#define FILES_ARRAY 0xAE70
#define FILE_NAME 0xC
#define FILE_NAME_MAX 260u

static void load_order(void)
{
    const u8 *handler = *(const u8 **)TES3X_NET_DATA_HANDLER, *list, *file;
    const char *name;
    u32 hash = 2166136261u, count, i, j;

    ses.plugins = ses.plugins_hash = 0;
    if (!plausible(handler) || !plausible(list = *(const u8 **)handler))
        return;
    count = *(const u32 *)(list + FILES_COUNT);
    if (count > 256)
        return;
    for (i = 0; i < count; i++) {
        if (!plausible(file = ((const u8 *const *)(list + FILES_ARRAY))[i]))
            return;
        name = (const char *)file + FILE_NAME;
        for (j = 0; j < FILE_NAME_MAX && name[j]; j++) {
            char c = name[j];
            if (c >= 'A' && c <= 'Z')
                c += 'a' - 'A';
            hash = (hash ^ (u8)c) * 16777619u;
        }
        hash *= 16777619u; /* the terminating zero */
        log_text("net.plugin", name);
    }
    ses.plugins = count;
    ses.plugins_hash = hash;
    tes3x_log_hex3("net.load_order", count, hash, 0);
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

/* Ghosts: each peer slot drives one persistent NPC of the ghost plugin (tes3x_net.py plugin)
 * through the engine's script compiler, as the console does. Each is drawn GHOST_DELAY_US in the
 * past, between the two states around that moment, so jitter and a lost state do not show. */
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
#define PI 3.14159265f

typedef int(__attribute__((thiscall)) *fn_compile_run)(void *self, void *scratch,
                                                       const char *text, int a2, int ref,
                                                       int a4, int a5, int a6);

struct pose {
    u32 flags;
    float x, y, z, heading;
    u8 cell[CELL_NAME];
};

static struct {
    u32 client, placed, flags;
    int gx, gy;
    float x, y, z, heading;
    u8 cell[CELL_NAME];
} ghosts[PEERS];
static u32 ghosts_parked, ghost_settle;
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

static void ghost_park(u32 i)
{
    ghost_command(i, "PositionCell ", 128 * ((int)i + 1), " 0 0 0 \"" GHOST_CELL "\"");
    ghosts[i].placed = 0;
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

static void read_pose(const u8 *state, struct pose *p)
{
    p->flags = get32le(state);
    copy((u8 *)&p->x, state + 4, 16);
    copy(p->cell, state + 20, CELL_NAME);
    p->cell[CELL_NAME - 1] = 0;
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

/* The slot's pose GHOST_DELAY_US ago, from a copy of its states; 0 if it has none. */
static int ghost_pose(u32 slot, struct pose *out)
{
    static struct {
        u32 time;
        u8 state[STATE_BYTES];
    } ring[SAMPLES];
    struct pose older, newer;
    u32 head, count, flags, i, first, now = now_us();
    int target, span, from = -1;
    float t, dx, dy;

    flags = lock();
    head = peers[slot].head;
    count = peers[slot].count;
    copy((u8 *)ring, (const u8 *)peers[slot].samples, sizeof(ring));
    unlock(flags);
    if (!count)
        return 0;
    first = head + SAMPLES - count; /* sample k, oldest first, is ring[(first + k) % SAMPLES] */
    target = (int)(now - GHOST_DELAY_US);
    for (i = 0; i < count; i++)
        if ((int)(ring[(first + i) % SAMPLES].time - (u32)target) <= 0)
            from = (int)i;
    if (from < 0 || count == 1) { /* nothing old enough to interpolate from: hold */
        read_pose(ring[(from < 0 ? first : first + (u32)from) % SAMPLES].state, out);
        return 1;
    }
    /* Past the newest state, carry the last two on for a moment. */
    i = (first + ((u32)from + 1 < count ? (u32)from : count - 2)) % SAMPLES;
    read_pose(ring[i].state, &older);
    read_pose(ring[(i + 1) % SAMPLES].state, &newer);
    span = (int)(ring[(i + 1) % SAMPLES].time - ring[i].time);
    dx = newer.x - older.x;
    dy = newer.y - older.y;
    *out = newer;
    if (span <= 0 || !same_place(&older, &newer) || !(older.flags & STATE_IN_WORLD) ||
        dx * dx + dy * dy > GHOST_SNAP * GHOST_SNAP)
        return 1;
    target -= (int)ring[i].time;
    if (target > span + GHOST_EXTRAPOLATE_US)
        target = span + GHOST_EXTRAPOLATE_US;
    t = (float)target / (float)span;
    out->x = older.x + dx * t;
    out->y = older.y + dy * t;
    out->z = older.z + (newer.z - older.z) * t;
    out->heading = older.heading + wrap_angle(newer.heading - older.heading) * (t > 1 ? 1 : t);
    return 1;
}

static void ghost_place(u32 i, const struct pose *p)
{
    char line[128];
    char *q = put_xyz(put_text(put_ghost(line, i), "PositionCell "), &p->x);

    q = put_text(q, " 0 \"");
    q = put_text(q, p->flags & STATE_INTERIOR ? (const char *)p->cell : GHOST_EXTERIOR);
    q = put_text(q, "\"");
    *q = 0;
    run_script(line);
    ghosts[i].placed = 1;
    ghosts[i].flags = p->flags;
    ghosts[i].gx = grid(p->x);
    ghosts[i].gy = grid(p->y);
    copy(ghosts[i].cell, p->cell, CELL_NAME);
    ghosts[i].x = p->x;
    ghosts[i].y = p->y;
    ghosts[i].z = p->z;
    ghosts[i].heading = 1000; /* not an angle: SetAngle follows */
    ghost_places++;
    log_text("net.ghost_cell", p->flags & STATE_INTERIOR ? (const char *)p->cell : "(exterior)");
}

static int differs(float a, float b, float by)
{
    return a - b > by || b - a > by;
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

static void ghost_update(u32 i, const struct pose *local)
{
    struct pose p, placed;
    u32 client = peers[i].client;

    if (client != ghosts[i].client) {
        if (ghosts[i].placed)
            ghost_park(i);
        ghosts[i].client = client;
        if (client)
            tes3x_log_hex3("net.ghost", i + 1, client, 0);
    }
    if (!client || !ghost_pose(i, &p))
        return;
    if (!(p.flags & STATE_IN_WORLD) || !near(&p, local)) {
        if (ghosts[i].placed)
            ghost_park(i);
        return;
    }
    placed.flags = ghosts[i].flags;
    copy(placed.cell, ghosts[i].cell, CELL_NAME);
    if (!ghosts[i].placed || !same_place(&p, &placed) ||
        (!(p.flags & STATE_INTERIOR) && (grid(p.x) != ghosts[i].gx || grid(p.y) != ghosts[i].gy))) {
        ghost_place(i, &p);
    } else if (differs(p.x, ghosts[i].x, 1) || differs(p.y, ghosts[i].y, 1) ||
               differs(p.z, ghosts[i].z, 1)) {
        ghost_command(i, "SetPos x ", round_int(p.x), "");
        ghost_command(i, "SetPos y ", round_int(p.y), "");
        ghost_command(i, "SetPos z ", round_int(p.z), "");
        ghosts[i].x = p.x;
        ghosts[i].y = p.y;
        ghosts[i].z = p.z;
        ghost_moves++;
    }
    if (differs(p.heading, ghosts[i].heading, 0.02f)) {
        float degrees = p.heading * (180.0f / PI);
        while (degrees < 0)
            degrees += 360;
        while (degrees >= 360)
            degrees -= 360;
        ghost_command(i, "SetAngle z ", round_int(degrees), "");
        ghosts[i].heading = p.heading;
    }
}

static void ghosts_frame(const u8 *state)
{
    static int gx, gy;
    struct pose local;
    u32 i;

    read_pose(state, &local);
    /* A save can hold a ghost wherever it stood; start every launch with all of them parked. */
    if (!ghosts_parked) {
        ghosts_parked = 1;
        for (i = 0; i < PEERS; i++)
            ghost_park(i);
    }
    if (grid(local.x) != gx || grid(local.y) != gy) {
        gx = grid(local.x);
        gy = grid(local.y);
        ghost_settle = GHOST_SETTLE_FRAMES;
    }
    if (ghost_settle) {
        ghost_settle--;
        return;
    }
    for (i = 0; i < PEERS; i++)
        ghost_update(i, &local);
}

/* Cell authority. The server names one client per loaded cell to run the actors there (AUTHORITY
 * events). It sends their states about 10 times a second; every other client takes those actors
 * out of the simulation, as the dialogue hold does, and places them from the states. A hit or a
 * held dialogue on a followed actor goes to its authority as an event. Only references from the
 * data files take part: their mod index and refnum name one object under one load order. */
#define EVENT_AUTHORITY 2u   /* cell key, client */
#define EVENT_HOLD 3u        /* refid, authority, on */
#define EVENT_HOLD_BROKEN 4u /* refid, holder, reason */
#define EVENT_HIT 5u         /* refid, authority, damage */
#define KEY_EXTERIOR 1u
#define KEY_INTERIOR 2u
#define KEY_BYTES (12u + CELL_NAME) /* kind, grid x, grid y, interior name */
#define ACTOR_BYTES 28u /* refid, x, y, z, heading, health, flags */
#define ACTORS_PER_PACKET 18u
#define ACTOR_PERIOD_US 100000u
#define ACTOR_DEAD 1u
#define ACTOR_IN_COMBAT 2u
#define AUTHORITIES 16u
#define ACTORS 64u
#define REMOTE_HOLDS 8u
#define HITS 8u
#define REF_ID 0x48 /* mod index << 24 | refnum; 0 for a reference made at run time */
#define MOBILE_REFERENCE 0x14
#define MOBILE_ACTION 0xDD /* 0x12 dying, 0x13 dead */
#define MOB_PROCESS 0x24   /* MobController -> ProcessManager: player, then the AI planners */
#define PROCESS_PLANNERS 0xC
#define PLANNER_MOBILE 4

struct cell_key {
    u32 kind;
    int gx, gy;
    u8 name[CELL_NAME];
};

/* The latest state of each actor from its authority; the receive DPC writes, bytes only. */
static struct {
    u32 refid, origin, seq, time;
    u8 state[ACTOR_BYTES];
} actors_in[ACTORS];
static struct {
    struct cell_key key;
    u32 client;
} authority[AUTHORITIES];
static u32 authorities, authority_welcome;
/* Actors this console places for another authority, and whether the engine had them simulated. */
static struct {
    u32 refid, owner, simulated, seq, seen;
    u8 *mobile;
    float health;
} followed[ACTORS];
/* Actors this console runs that another client is talking to. */
static struct {
    u32 refid, holder, simulated, found, release;
    u8 *mobile;
    float health;
} remote_holds[REMOTE_HOLDS];
static struct {
    u32 refid;
    float damage;
} hits[HITS];
static u32 hit_count, talk_refid, talk_owner, talk_broken;
static u32 actor_states_out, actor_states_in, actor_moves, follows;
static u32 hits_out, hits_in, remote_holds_in, remote_breaks_out, remote_breaks_in;

static void actors_reset(void)
{
    u32 i;

    for (i = 0; i < ACTORS; i++)
        actors_in[i].refid = 0;
}

/* Caller holds the lock (the receive DPC): origin client, count, then the states. */
static void actors_rx(u32 origin, u32 seq, const u8 *p, u32 n)
{
    u32 count = get32le(p), i, j, refid, slot, oldest;

    for (i = 0; i < count && 4 + (i + 1) * ACTOR_BYTES <= n; i++) {
        const u8 *a = p + 4 + i * ACTOR_BYTES;
        refid = get32le(a);
        slot = ACTORS;
        for (j = 0; j < ACTORS && slot == ACTORS; j++)
            if (actors_in[j].refid == refid)
                slot = j;
        if (slot < ACTORS && actors_in[slot].origin == origin && seq <= actors_in[slot].seq)
            continue;
        for (j = 0, oldest = 0; j < ACTORS && slot == ACTORS; j++) {
            if (!actors_in[j].refid)
                slot = j;
            else if ((int)(actors_in[j].time - actors_in[oldest].time) < 0)
                oldest = j;
        }
        if (slot == ACTORS)
            slot = oldest;
        actors_in[slot].refid = refid;
        actors_in[slot].origin = origin;
        actors_in[slot].seq = seq;
        actors_in[slot].time = now_us();
        copy(actors_in[slot].state, a, ACTOR_BYTES);
        actor_states_in++;
    }
}

static int key_equal(const struct cell_key *a, const struct cell_key *b)
{
    u32 i;

    if (a->kind != b->kind)
        return 0;
    if (a->kind == KEY_EXTERIOR)
        return a->gx == b->gx && a->gy == b->gy;
    for (i = 0; i < CELL_NAME - 1 && a->name[i] && a->name[i] == b->name[i]; i++)
        ;
    return a->name[i] == b->name[i];
}

/* An actor's cell: the local interior, or the exterior grid cell it stands in. */
static void actor_key(const struct pose *local, float x, float y, struct cell_key *k)
{
    u32 i;

    k->kind = local->flags & STATE_INTERIOR ? KEY_INTERIOR : KEY_EXTERIOR;
    k->gx = k->kind == KEY_EXTERIOR ? grid(x) : 0;
    k->gy = k->kind == KEY_EXTERIOR ? grid(y) : 0;
    for (i = 0; i < CELL_NAME; i++)
        k->name[i] = k->kind == KEY_INTERIOR ? local->cell[i] : 0;
}

static int key_loaded(const struct cell_key *k, const struct pose *local)
{
    struct cell_key mine;
    int dx, dy;

    actor_key(local, local->x, local->y, &mine);
    if (k->kind != mine.kind)
        return 0;
    if (k->kind == KEY_INTERIOR)
        return key_equal(k, &mine);
    dx = k->gx - mine.gx;
    dy = k->gy - mine.gy;
    return dx >= -1 && dx <= 1 && dy >= -1 && dy <= 1;
}

static u32 authority_of(const struct cell_key *k)
{
    u32 i;

    for (i = 0; i < authorities; i++)
        if (key_equal(&authority[i].key, k))
            return authority[i].client;
    return 0;
}

static void authority_remove(u32 i)
{
    authority[i] = authority[--authorities];
}

static void authority_set(const struct cell_key *k, u32 client)
{
    u32 i;

    if (k->kind == KEY_INTERIOR)
        log_text("net.authority_cell", (const char *)k->name);
    tes3x_log_hex3("net.authority", client, (u32)k->gx, (u32)k->gy);
    for (i = 0; i < authorities; i++)
        if (key_equal(&authority[i].key, k))
            break;
    if (i < authorities && !client)
        authority_remove(i);
    if (!client)
        return;
    if (i == authorities) {
        if (authorities == AUTHORITIES)
            i = 0; /* more cells than a console loads: the table is stale */
        else
            authorities++;
    }
    authority[i].key = *k;
    authority[i].client = client;
}

static void event_words(u32 kind, u32 a, u32 b, const void *c)
{
    u8 data[12];

    put32le(data, a);
    put32le(data + 4, b);
    copy(data + 8, (const u8 *)c, 4);
    if (!event_queue(kind, data, sizeof(data)))
        tes3x_log("net.event_full", kind);
}

static void actor_command(void *ref, const char *verb, int value)
{
    char line[48];
    char *p = put_int(put_text(line, verb), value);

    *p = 0;
    run_script_on(line, ref);
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

static void unfollow(u32 i, int restore)
{
    if (restore && followed[i].simulated)
        *(u32 *)(followed[i].mobile + MOBILE_FLAGS) |= MOBILE_SIMULATED;
    followed[i].refid = 0;
}

/* Another client runs this actor: keep it out of the simulation, place it from the latest state,
 * and send a drop in its health to the authority as a hit. */
static void follow(u8 *mobile, u8 *ref, u32 refid, u32 owner)
{
    u32 *flags = (u32 *)(mobile + MOBILE_FLAGS), i, slot = ACTORS, seq = 0, lk;
    float health = *(const float *)(mobile + MOBILE_HEALTH), damage, heading;
    u8 state[ACTOR_BYTES];
    const float *at = (const float *)(ref + 0x38), *to = (const float *)(state + 4);

    for (i = 0; i < ACTORS && slot == ACTORS; i++)
        if (followed[i].refid == refid)
            slot = i;
    if (slot < ACTORS && followed[slot].mobile != mobile)
        unfollow(slot, 0); /* the same reference in a new mobile: a reload */
    if (slot == ACTORS || !followed[slot].refid) {
        for (i = 0, slot = ACTORS; i < ACTORS && slot == ACTORS; i++)
            if (!followed[i].refid)
                slot = i;
        if (slot == ACTORS)
            return;
        followed[slot].refid = refid;
        followed[slot].mobile = mobile;
        followed[slot].simulated = *flags & MOBILE_SIMULATED;
        followed[slot].seq = 0;
        followed[slot].health = health;
        follows++;
        tes3x_log_hex3("net.follow", refid, owner, followed[slot].simulated);
    }
    followed[slot].owner = owner;
    followed[slot].seen = 1;
    *flags &= ~MOBILE_SIMULATED;
    if (health < followed[slot].health - 0.5f) {
        damage = followed[slot].health - health;
        event_words(EVENT_HIT, refid, owner, &damage);
        hits_out++;
        tes3x_log_hex3("net.hit_sent", refid, owner, (u32)(int)damage);
    }
    followed[slot].health = health;
    lk = lock();
    for (i = 0; i < ACTORS; i++)
        if (actors_in[i].refid == refid && actors_in[i].origin == owner) {
            seq = actors_in[i].seq;
            copy(state, actors_in[i].state, ACTOR_BYTES);
            break;
        }
    unlock(lk);
    if (!seq || seq == followed[slot].seq)
        return;
    followed[slot].seq = seq;
    if (differs(to[0], at[0], 1) || differs(to[1], at[1], 1) || differs(to[2], at[2], 1)) {
        actor_command(ref, "SetPos x ", round_int(to[0]));
        actor_command(ref, "SetPos y ", round_int(to[1]));
        actor_command(ref, "SetPos z ", round_int(to[2]));
        actor_moves++;
    }
    if (differs(to[3], *(const float *)(ref + 0x34), 0.02f)) {
        heading = to[3] * (180.0f / PI);
        while (heading < 0)
            heading += 360;
        while (heading >= 360)
            heading -= 360;
        actor_command(ref, "SetAngle z ", round_int(heading));
    }
}

/* On the authority: another client's dialogue holds this actor until it ends, the actor enters
 * combat or its health drops, from anyone's hit. */
static void remote_hold_apply(u8 *mobile, u32 refid)
{
    u32 *flags = (u32 *)(mobile + MOBILE_FLAGS), i, reason;
    float health = *(const float *)(mobile + MOBILE_HEALTH);

    for (i = 0; i < REMOTE_HOLDS; i++) {
        if (remote_holds[i].refid != refid)
            continue;
        remote_holds[i].found = 1;
        if (remote_holds[i].mobile != mobile) {
            remote_holds[i].mobile = mobile;
            remote_holds[i].simulated = *flags & MOBILE_SIMULATED;
            remote_holds[i].health = health;
            tes3x_log_hex3("net.remote_hold", refid, remote_holds[i].holder, (u32)(int)health);
        }
        reason = *flags & MOBILE_IN_COMBAT ? 1 : health < remote_holds[i].health ? 2 : 0;
        if (reason && !remote_holds[i].release) {
            event_words(EVENT_HOLD_BROKEN, refid, remote_holds[i].holder, &reason);
            remote_breaks_out++;
            tes3x_log_hex3("net.remote_hold_broken", refid, remote_holds[i].holder, reason);
        }
        if (reason || remote_holds[i].release) {
            if (remote_holds[i].simulated)
                *flags |= MOBILE_SIMULATED;
            remote_holds[i].refid = 0;
        } else {
            *flags &= ~MOBILE_SIMULATED;
        }
    }
}

static void hits_apply(void *ref, u32 refid)
{
    u32 i;

    for (i = 0; i < hit_count; i++)
        if (hits[i].refid == refid) {
            actor_command(ref, "ModCurrentHealth ", -round_int(hits[i].damage));
            hits[i--] = hits[--hit_count];
        }
}

/* The talker's side of a hold on a followed actor: HOLD on while the dialogue is open, off when
 * it closes; HOLD_BROKEN from the authority closes it. 1 if the actor is followed. */
static int hold_remote(u8 *actor)
{
    u32 i, refid = 0, owner = 0, on;

    for (i = 0; actor && i < ACTORS; i++)
        if (followed[i].refid && followed[i].mobile == actor) {
            refid = followed[i].refid;
            owner = followed[i].owner;
        }
    if (talk_refid && talk_refid != refid) {
        on = 0;
        event_words(EVENT_HOLD, talk_refid, talk_owner, &on);
        talk_refid = 0;
    }
    if (!refid)
        return 0;
    if (!talk_refid) {
        on = 1;
        talk_refid = refid;
        talk_owner = owner;
        talk_broken = 0;
        event_words(EVENT_HOLD, refid, owner, &on);
        tes3x_log_hex3("net.hold_remote", refid, owner, 0);
    }
    if (talk_broken)
        dialogue_close();
    return 1;
}

/* Once per frame in the world: follow, send or hold each actor the AI planners hold. */
static void authority_frame(const u8 *player, const u8 *state)
{
    static u32 last_send;
    const u8 *world = *(const u8 **)TES3X_NET_WORLD, *mobs, *process, *node, *planner;
    u8 out[4 + ACTORS_PER_PACKET * ACTOR_BYTES], *mobile, *ref, *a;
    struct pose local;
    struct cell_key key;
    u32 i, n = 0, guard, refid, owner, now = now_us(), lk, send;

    read_pose(state, &local);
    if (ses.state != SESSION_JOINED || authority_welcome != ses.welcomes) {
        authority_welcome = ses.welcomes;
        authorities = 0;
        hit_count = 0;
        for (i = 0; i < REMOTE_HOLDS; i++)
            remote_holds[i].release = 1;
        talk_refid = 0;
    }
    for (i = 0; i < authorities; i++)
        if (!key_loaded(&authority[i].key, &local))
            authority_remove(i--);
    send = ses.state == SESSION_JOINED && now - last_send >= ACTOR_PERIOD_US;
    if (send)
        last_send = now;
    for (i = 0; i < ACTORS; i++)
        followed[i].seen = 0;
    for (i = 0; i < REMOTE_HOLDS; i++)
        remote_holds[i].found = 0;
    if (!plausible(world) || !plausible(mobs = *(const u8 **)(world + 0x5C)) ||
        !plausible(process = *(const u8 **)(mobs + MOB_PROCESS)))
        return;
    node = *(const u8 *const *)(process + PROCESS_PLANNERS);
    for (guard = 0; plausible(node) && guard < 512; node = *(const u8 *const *)(node + 4), guard++) {
        if (!plausible(planner = *(const u8 *const *)(node + 8)) ||
            !plausible(mobile = *(u8 *const *)(planner + PLANNER_MOBILE)) ||
            !plausible(ref = *(u8 **)(mobile + MOBILE_REFERENCE)) || ref == player ||
            !(refid = *(const u32 *)(ref + REF_ID)) || is_ghost(ref))
            continue;
        actor_key(&local, *(const float *)(ref + 0x38), *(const float *)(ref + 0x3C), &key);
        owner = authority_of(&key);
        if (owner && owner != ses.client) {
            follow(mobile, ref, refid, owner);
            continue;
        }
        for (i = 0; i < ACTORS; i++)
            if (followed[i].refid == refid)
                unfollow(i, followed[i].mobile == mobile);
        if (owner != ses.client)
            continue;
        remote_hold_apply(mobile, refid);
        hits_apply(ref, refid);
        if (!send)
            continue;
        a = out + 4 + n * ACTOR_BYTES;
        put32le(a, refid);
        copy(a + 4, ref + 0x38, 12);
        copy(a + 16, ref + 0x34, 4);
        copy(a + 20, mobile + MOBILE_HEALTH, 4);
        put32le(a + 24, (mobile[MOBILE_ACTION] == 0x12 || mobile[MOBILE_ACTION] == 0x13
                         ? ACTOR_DEAD : 0) |
                        (*(const u32 *)(mobile + MOBILE_FLAGS) & MOBILE_IN_COMBAT
                         ? ACTOR_IN_COMBAT : 0));
        if (++n == ACTORS_PER_PACKET) {
            put32le(out, n);
            lk = lock();
            session_send(T3MP_ACTORS, out, 4 + n * ACTOR_BYTES);
            unlock(lk);
            actor_states_out += n;
            n = 0;
        }
    }
    if (n) {
        put32le(out, n);
        lk = lock();
        session_send(T3MP_ACTORS, out, 4 + n * ACTOR_BYTES);
        unlock(lk);
        actor_states_out += n;
    }
    hit_count = 0; /* a hit on an actor not loaded here is lost */
    for (i = 0; i < ACTORS; i++)
        if (followed[i].refid && !followed[i].seen)
            unfollow(i, 0);
    for (i = 0; i < REMOTE_HOLDS; i++)
        if (remote_holds[i].refid && !remote_holds[i].found &&
            (remote_holds[i].release || remote_holds[i].mobile))
            remote_holds[i].refid = 0;
}

static void authority_event(const struct event *e)
{
    struct cell_key key;
    u32 refid = get32le(e->data), target = get32le(e->data + 4), value = get32le(e->data + 8), i;

    if (e->kind == EVENT_AUTHORITY) {
        if (e->length < KEY_BYTES + 4)
            return;
        key.kind = get32le(e->data);
        key.gx = (int)get32le(e->data + 4);
        key.gy = (int)get32le(e->data + 8);
        copy(key.name, e->data + 12, CELL_NAME);
        key.name[CELL_NAME - 1] = 0;
        authority_set(&key, get32le(e->data + KEY_BYTES));
        return;
    }
    if (e->length < 12 || target != ses.client)
        return;
    if (e->kind == EVENT_HOLD) {
        for (i = 0; i < REMOTE_HOLDS; i++)
            if (remote_holds[i].refid == refid)
                break;
        if (i == REMOTE_HOLDS && value)
            for (i = 0; i < REMOTE_HOLDS && remote_holds[i].refid; i++)
                ;
        if (i == REMOTE_HOLDS)
            return;
        remote_holds_in++;
        tes3x_log_hex3("net.hold_request", refid, e->origin, value);
        if (!remote_holds[i].refid) {
            remote_holds[i].refid = refid;
            remote_holds[i].mobile = 0;
        }
        remote_holds[i].holder = e->origin;
        remote_holds[i].release = !value;
    } else if (e->kind == EVENT_HOLD_BROKEN) {
        remote_breaks_in++;
        tes3x_log_hex3("net.hold_broken_remote", refid, e->origin, value);
        if (refid == talk_refid)
            talk_broken = 1;
    } else if (e->kind == EVENT_HIT && hit_count < HITS) {
        hits[hit_count].refid = refid;
        copy((u8 *)&hits[hit_count].damage, e->data + 8, 4);
        tes3x_log_hex3("net.hit", refid, e->origin, (u32)round_int(hits[hit_count].damage));
        hit_count++;
        hits_in++;
    }
}

static void authority_stat(void)
{
    u32 i, n = 0, holds_now = 0;

    for (i = 0; i < ACTORS; i++)
        n += followed[i].refid != 0;
    for (i = 0; i < REMOTE_HOLDS; i++)
        holds_now += remote_holds[i].refid != 0;
    tes3x_log_hex3("net.authorities", authorities, n, holds_now);
    for (i = 0; i < authorities; i++)
        tes3x_log_hex3("net.authority_is", authority[i].client, (u32)authority[i].key.gx,
                       (u32)authority[i].key.gy);
    tes3x_log_hex3("net.actor_states", actor_states_out, actor_states_in, actor_moves);
    tes3x_log_hex3("net.actor_events", follows, hits_out, hits_in);
    tes3x_log_hex3("net.actor_holds", remote_holds_in, remote_breaks_out, remote_breaks_in);
}

static void event_handle(const struct event *e)
{
    char text[EVENT_DATA + 1];

    if (e->kind == EVENT_TEXT) {
        copy((u8 *)text, e->data, e->length);
        text[e->length] = 0;
        tes3x_log_hex3("net.text_from", e->origin, e->seq, 0);
        log_text("net.text", text);
    } else if (e->kind >= EVENT_AUTHORITY && e->kind <= EVENT_HIT) {
        authority_event(e);
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
        if (!rel.in_count) {
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

/* The clock globals hang off the WorldController, in HELLO's order: GameHour +0xA4, Day +0xB0,
 * Month +0xAC, Year +0xA8, DaysPassed +0xB4, TimeScale +0xB8. A global's value is at +0x34. */
static const u32 clock_offsets[CLOCK_GLOBALS] = {0xA4, 0xB0, 0xAC, 0xA8, 0xB4, 0xB8};
#define GLOBAL_VALUE 0x34

static float *clock_global(u32 i)
{
    const u8 *world = *(const u8 **)TES3X_NET_WORLD, *global;

    if (!plausible(world) || !plausible(global = *(const u8 *const *)(world + clock_offsets[i])))
        return 0;
    return (float *)(global + GLOBAL_VALUE);
}

/* Game thread, in the world: while joined the server's clock overwrites the globals, latest
 * wins; either way this console's clock is kept for the next HELLO. */
static void clock_frame(void)
{
    u8 local[CLOCK_BYTES], server[CLOCK_BYTES];
    float *globals[CLOCK_GLOBALS];
    u32 i, flags, received;

    for (i = 0; i < CLOCK_GLOBALS; i++)
        if (!(globals[i] = clock_global(i)))
            return;
    flags = lock();
    received = game_clock.received;
    copy(server, game_clock.server, CLOCK_BYTES);
    unlock(flags);
    if (ses.state == SESSION_JOINED && received != game_clock.applied) {
        for (i = 0; i < CLOCK_GLOBALS; i++)
            copy((u8 *)globals[i], server + 4 * i, 4);
        if (!game_clock.applied)
            tes3x_log_hex3("net.clock_set", (u32)(int)(*globals[0] * 1000.0f),
                           (u32)(int)*globals[1], (u32)(int)*globals[2]);
        game_clock.applied = received;
    }
    for (i = 0; i < CLOCK_GLOBALS; i++)
        copy(local + 4 * i, (const u8 *)globals[i], 4);
    flags = lock();
    copy(game_clock.local, local, CLOCK_BYTES);
    game_clock.local_valid = 1;
    unlock(flags);
}

static void clock_stat(void)
{
    const float *hour = clock_global(0), *day = clock_global(1), *scale = clock_global(5);

    tes3x_log_hex3("net.clock", game_clock.received, game_clock.applied, 0);
    if (hour && day && scale)
        tes3x_log_hex3("net.clock_now", (u32)(int)(*hour * 1000.0f), (u32)(int)*day,
                       (u32)(int)*scale);
}

/* Once per frame, from the Game::Update hook. */
void tes3x_net_frame(void)
{
    static u8 last_cell[CELL_NAME];
    static u32 logged_player, logged_server, logged_refused, known[PEERS];
    u8 state[STATE_BYTES];
    const u8 *ref;
    u32 i, flags;

    if (!ini_checked) {
        ini_checked = 1;
        autostart();
    }
    if (net.up && ses.host[0] && ses.server != logged_server) {
        logged_server = ses.server;
        if (logged_server)
            tes3x_log_hex3("net.resolved", logged_server, ses.dns_queries, ses.dns_answers);
    }
    if ((ses.state == SESSION_REFUSED) != logged_refused) {
        logged_refused = ses.state == SESSION_REFUSED;
        if (logged_refused)
            tes3x_log_hex3("net.refused", ses.plugins_hash, ses.refused_hash,
                           ses.refused_plugins);
    }
    if (net.up)
        events_frame();
    ref = player_reference();
    menu_frame(net.up && ref);
    if (!net.up || !ref)
        return;
    clock_frame();
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
        ghost_settle = GHOST_SETTLE_FRAMES;
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
    ghosts_frame(state);
    authority_frame(ref, state);
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
        menu_stat();
        clock_stat();
        authority_stat();
    } else if ((rest = word(text, "menusim")) && (rest = word(skip(rest), "auto")) &&
               !*skip(rest)) {
        menu_forced = 0;
    } else if ((rest = word(text, "menusim")) && (rest = number(skip(rest), &value)) &&
               !*skip(rest)) {
        menu_forced = 1 + (value != 0);
        menu_sim(value != 0);
    } else if ((rest = word(text, "say")) && *(rest = skip(rest))) {
        for (value = 0; rest[value] && value < EVENT_DATA; value++)
            ;
        if (!event_queue(EVENT_TEXT, (const u8 *)rest, value))
            tes3x_log("net.event_full", rel.out_next - rel.out_first);
    } else if (!*text) {
        broadcast(8);
    } else if ((rest = number(text, &value)) && !*skip(rest) && value) {
        broadcast(value);
    } else {
        tes3x_log("net.usage", 0);
    }
    return 1;
}
