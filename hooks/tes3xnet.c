/* Shared Xbox network layer: NIC ownership, ARP, IPv4 and UDP. Consumers register one local UDP
 * port and callbacks; multiplayer and the GUI agent therefore have independent sessions while
 * sharing the one physical adapter and the firmware-shutdown guard. */

#include "tes3x_thunks.h"
#include "tes3xnt.h"
#include "tes3xnet.h"

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
#define RX_ERRORS 0x1F80u
#define RX_AVAIL 0x8000u

/* xemu stops reading its tunnel if a burst fills the ring. */
#define RX_RING 32u
#define TX_RING 4u
#define BUF 2048u
#define POOL_BYTES (4096u + (RX_RING + TX_RING) * BUF)
#define ETH_ARP 0x0806u
#define ETH_IP 0x0800u
#define MIN_FRAME 60u
#define TICK_MS 250u

struct descriptor {
    u32 paddr;
    u16 length;
    u16 flags;
} __attribute__((packed));

struct tes3x_net_info tes3x_net;
static struct tes3x_net_channel *channels[TES3X_NET_CHANNELS];
static u32 channel_count;
static u32 timer[0x28 / 4], tick_dpc[0x1C / 4];
static u8 mac[6], *pool, *rx_buf, *tx_buf;
static volatile struct descriptor *rx_ring, *tx_ring;
static u32 rx_head, tx_tail, interrupt[0x70 / 4], dpc[0x1C / 4];
static fn_HalReturnToFirmware firmware_original;
static u32 probe_ip, probe_hits;

u32 tes3x_net_lock(void)
{
    u32 flags;
    __asm__ volatile("pushfl\n\tpopl %0\n\tcli" : "=r"(flags) : : "memory");
    return flags;
}

void tes3x_net_unlock(u32 flags)
{
    __asm__ volatile("pushl %0\n\tpopfl" : : "r"(flags) : "memory", "cc");
}

static u32 get16(const u8 *p) { return (u32)p[0] << 8 | p[1]; }
static u32 get32(const u8 *p)
{
    return (u32)p[0] << 24 | (u32)p[1] << 16 | (u32)p[2] << 8 | p[3];
}
static void put16(u8 *p, u32 v) { p[0] = (u8)(v >> 8); p[1] = (u8)v; }
static void put32(u8 *p, u32 v)
{
    p[0] = (u8)(v >> 24); p[1] = (u8)(v >> 16); p[2] = (u8)(v >> 8); p[3] = (u8)v;
}
static void put32le(u8 *p, u32 v)
{
    p[0] = (u8)v; p[1] = (u8)(v >> 8); p[2] = (u8)(v >> 16); p[3] = (u8)(v >> 24);
}
static void copy(u8 *d, const u8 *s, u32 n) { while (n--) *d++ = *s++; }

static void ip_checksum(u8 *ip)
{
    u32 sum = 0, i;
    ip[10] = ip[11] = 0;
    for (i = 0; i < 20; i += 2)
        sum += get16(ip + i);
    while (sum >> 16)
        sum = (sum & 0xFFFF) + (sum >> 16);
    put16(ip + 10, ~sum);
}

u32 tes3x_net_now_us(void)
{
    u32 lo, hi;
    __asm__ volatile("rdtsc" : "=a"(lo), "=d"(hi));
    return (u32)(((u64)(hi << 22 | lo >> 10) * 5722) >> 12);
}

void tes3x_net_mac(u8 out[6]) { copy(out, mac, 6); }

static void sample(void)
{
    u32 i;
    for (i = 0; i < channel_count; i++)
        if (channels[i]->sample)
            channels[i]->sample();
}

int tes3x_net_register(struct tes3x_net_channel *channel)
{
    u32 i;
    if (!channel || !channel->port || channel_count >= TES3X_NET_CHANNELS)
        return 0;
    for (i = 0; i < channel_count; i++)
        if (channels[i]->port == channel->port)
            return 0;
    channel->route.hop = channel->route.known = 0;
    channels[channel_count++] = channel;
    return 1;
}

static void *kernel_export(u32 ordinal)
{
    u8 *base = (u8 *)0x80010000u, *nt;
    u32 *dir, *functions;
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
    u32 cr0, flags = tes3x_net_lock();
    __asm__ volatile("movl %%cr0, %0" : "=r"(cr0));
    __asm__ volatile("movl %0, %%cr0" : : "r"(cr0 & ~CR0_WP) : "memory");
    *(void **)slot = fn;
    __asm__ volatile("movl %0, %%cr0" : : "r"(cr0) : "memory");
    tes3x_net_unlock(flags);
}

static u8 *tx_begin(void)
{
    volatile struct descriptor *d = &tx_ring[tx_tail];
    if (d->flags & TX_VALID) {
        tes3x_net.tx_full++;
        return 0;
    }
    if (d->flags & TX_ERROR)
        tes3x_net.tx_errors++;
    return tx_buf + tx_tail * BUF;
}

static void tx_commit(u32 length)
{
    volatile struct descriptor *d = &tx_ring[tx_tail];
    u32 padded = length < MIN_FRAME ? MIN_FRAME : length;
    while (length < padded)
        tx_buf[tx_tail * BUF + length++] = 0;
    d->paddr = MmGetPhysicalAddress(tx_buf + tx_tail * BUF);
    d->length = padded - 1;
    d->flags = TX_VALID | TX_LASTPACKET;
    tx_tail = (tx_tail + 1) % TX_RING;
    NIC(REG_TXRX_CONTROL) = TXRX_KICK;
    tes3x_net.tx++;
}

void tes3x_net_arp(u32 target)
{
    u8 *o = tx_begin();
    u32 i;
    if (!o)
        return;
    for (i = 0; i < 6; i++) {
        o[i] = 0xFF;
        o[6 + i] = mac[i];
    }
    put16(o + 12, ETH_ARP);
    put16(o + 14, 1); put16(o + 16, ETH_IP); o[18] = 6; o[19] = 4; put16(o + 20, 1);
    copy(o + 22, mac, 6); put32(o + 28, tes3x_net.ip);
    for (i = 0; i < 6; i++) o[32 + i] = 0;
    put32(o + 38, target);
    tx_commit(42);
}

void tes3x_net_announce(void)
{
    tes3x_net_arp(tes3x_net.ip);
}

void tes3x_net_route(struct tes3x_net_channel *channel, u32 target, u32 gateway)
{
    u32 hop = (target ^ tes3x_net.ip) & tes3x_net.mask && gateway ? gateway : target;
    if (hop != channel->route.hop) {
        channel->route.hop = hop;
        channel->route.known = 0;
    }
}

static void rx_arp(const u8 *f, u32 length)
{
    u8 *o;
    u32 i, source;
    if (length < 42 || get16(f + 14) != 1 || get16(f + 16) != ETH_IP || f[18] != 6 ||
        f[19] != 4)
        return;
    source = get32(f + 28);
    for (i = 0; i < channel_count; i++) {
        if (channels[i]->route.hop && source == channels[i]->route.hop) {
            copy(channels[i]->route.mac, f + 22, 6);
            channels[i]->route.known = 1;
        }
    }
    if (probe_ip && source == probe_ip)
        probe_hits++;
    if (get16(f + 20) != 1 || !tes3x_net.ip || get32(f + 38) != tes3x_net.ip)
        return;
    tes3x_net.arp++;
    if (!(o = tx_begin()))
        return;
    copy(o, f + 6, 6); copy(o + 6, mac, 6); put16(o + 12, ETH_ARP);
    put16(o + 14, 1); put16(o + 16, ETH_IP); o[18] = 6; o[19] = 4; put16(o + 20, 2);
    copy(o + 22, mac, 6); put32(o + 28, tes3x_net.ip);
    copy(o + 32, f + 22, 6); copy(o + 38, f + 28, 4);
    tx_commit(42);
}

static int send_udp(u32 source_port, const u8 *destination_mac, u32 destination, u32 port,
                    const u8 *payload, u32 length)
{
    u8 *o;
    if (length > BUF - 42 || !(o = tx_begin()))
        return 0;
    copy(o, destination_mac, 6); copy(o + 6, mac, 6); put16(o + 12, ETH_IP);
    o[14] = 0x45; o[15] = 0; put16(o + 16, 20 + 8 + length);
    put16(o + 18, tes3x_net.tx); put16(o + 20, 0); o[22] = 64; o[23] = 17;
    put32(o + 26, tes3x_net.ip); put32(o + 30, destination); ip_checksum(o + 14);
    put16(o + 34, source_port); put16(o + 36, port); put16(o + 38, 8 + length);
    put16(o + 40, 0); copy(o + 42, payload, length); tx_commit(42 + length);
    return 1;
}

int tes3x_net_send(struct tes3x_net_channel *channel, u32 destination, u32 port,
                   const u8 *payload, u32 length)
{
    if (!channel || !channel->route.known)
        return 0;
    return send_udp(channel->port, channel->route.mac, destination, port, payload, length);
}

int tes3x_net_send_broadcast(u32 source_port, u32 destination_port,
                             const u8 *payload, u32 length)
{
    static const u8 broadcast_mac[6] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};
    return send_udp(source_port, broadcast_mac, 0xFFFFFFFFu, destination_port, payload, length);
}

static void echo(const u8 *f, const u8 *ip, const u8 *udp, u32 total, u32 ihl)
{
    u8 *o;
    if (!(o = tx_begin()))
        return;
    copy(o, f + 6, 6); copy(o + 6, mac, 6); put16(o + 12, ETH_IP);
    o[14] = 0x45; o[15] = 0; put16(o + 16, total - ihl + 20);
    put16(o + 18, get16(ip + 4)); put16(o + 20, 0); o[22] = 64; o[23] = 17;
    put32(o + 26, tes3x_net.ip); copy(o + 30, ip + 12, 4); ip_checksum(o + 14);
    copy(o + 34, udp + 2, 2); copy(o + 36, udp, 2); copy(o + 38, udp + 4, 2);
    put16(o + 40, 0); copy(o + 42, udp + 8, total - ihl - 8);
    o[48] = 'O'; o[49] = 'N';
    tx_commit(14 + 20 + total - ihl);
}

static void rx_ip(const u8 *f, u32 length)
{
    static const u8 ping[8] = {'T', 'E', 'S', '3', 'X', 'P', 'N', 'G'};
    const u8 *ip = f + 14, *udp, *payload;
    u32 ihl, total, destination, source, sport, dport, body, i, j;
    if (length < 34 || (ip[0] >> 4) != 4 || ip[9] != 17)
        return;
    ihl = (ip[0] & 0x0F) * 4;
    total = get16(ip + 2);
    destination = get32(ip + 16);
    if (ihl < 20 || total > length - 14 || total < ihl + 8)
        return;
    udp = ip + ihl;
    body = get16(udp + 4);
    if (body < 8 || body > total - ihl)
        return;
    body -= 8;
    payload = udp + 8;
    if (destination != tes3x_net.ip && destination != 0xFFFFFFFFu)
        return;
    source = get32(ip + 12); sport = get16(udp); dport = get16(udp + 2);
    if (body >= 8) {
        for (j = 0; j < 8 && payload[j] == ping[j]; j++)
            ;
        if (j == 8) {
            tes3x_net.echo++;
            echo(f, ip, udp, total, ihl);
            return;
        }
    }
    for (i = 0; i < channel_count; i++)
        if (channels[i]->port == dport && channels[i]->receive)
            channels[i]->receive(source, sport, payload, body);
}

static void rx_drain(void)
{
    u32 i, found, filled = 0;
    for (i = 0; i < RX_RING; i++)
        filled += !(rx_ring[i].flags & RX_AVAIL);
    if (filled > tes3x_net.rx_peak)
        tes3x_net.rx_peak = filled;
    do {
        found = 0;
        for (i = 0; i < RX_RING; i++) {
            u32 slot = (rx_head + i) % RX_RING;
            volatile struct descriptor *desc = &rx_ring[slot];
            const u8 *frame = rx_buf + slot * BUF;
            u32 flags = desc->flags;
            if (flags & RX_AVAIL)
                continue;
            if ((flags & RX_DESCRIPTORVALID) && !(flags & RX_ERRORS) && desc->length >= 14) {
                u32 type = get16(frame + 12);
                tes3x_net.rx++;
                if (type == ETH_ARP) rx_arp(frame, desc->length);
                else if (type == ETH_IP) rx_ip(frame, desc->length);
            } else {
                tes3x_net.rx_errors++;
            }
            desc->length = BUF; desc->flags = RX_AVAIL;
            rx_head = (slot + 1) % RX_RING; found = 1; break;
        }
    } while (found);
    NIC(REG_TXRX_CONTROL) = TXRX_GET;
}

static u8 __stdcall nic_isr(void *object, void *context)
{
    (void)object; (void)context;
    if (!NIC(REG_IRQ_STATUS) && !NIC(REG_MII_STATUS))
        return 0;
    NIC(REG_IRQ_MASK) = 0;
    tes3x_net.irqs++;
    sample();
    KeInsertQueueDpc(dpc, 0, 0);
    return 1;
}

static void __stdcall nic_dpc(void *item, void *context, void *a, void *b)
{
    u32 irq, mii, flags;
    (void)item; (void)context; (void)a; (void)b;
    flags = tes3x_net_lock();
    tes3x_net.dpcs++;
    sample();
    for (;;) {
        irq = NIC(REG_IRQ_STATUS); mii = NIC(REG_MII_STATUS);
        if (!irq && !mii) break;
        NIC(REG_MII_STATUS) = mii; NIC(REG_IRQ_STATUS) = irq;
        if (irq & IRQ_RX_NOBUF) tes3x_net.rx_nobuf++;
        rx_drain();
    }
    if (tes3x_net.up) NIC(REG_IRQ_MASK) = IRQ_ENABLED;
    tes3x_net_unlock(flags);
}

static void arm_timer(void) { KeSetTimer(timer, -(long long)TICK_MS * 10000, tick_dpc); }

static void __stdcall tick(void *item, void *context, void *a, void *b)
{
    u32 flags, i;
    (void)item; (void)context; (void)a; (void)b;
    flags = tes3x_net_lock();
    sample();
    for (i = 0; i < channel_count; i++)
        if (channels[i]->tick)
            channels[i]->tick();
    tes3x_net_unlock(flags);
    if (tes3x_net.up) arm_timer();
}

static void stop_txrx(void)
{
    u32 i;
    NIC(REG_RX_CONTROL) &= ~1u; NIC(REG_TX_CONTROL) &= ~1u;
    for (i = 0; i < 5000 && ((NIC(REG_RX_STATUS) | NIC(REG_TX_STATUS)) & 1u); i++)
        KeStallExecutionProcessor(10);
    NIC(REG_TXRX_CONTROL) = TXRX_DISABLE;
    for (i = 0; i < 1000 && !(NIC(REG_TXRX_CONTROL) & TXRX_IDLE); i++)
        KeStallExecutionProcessor(50);
    NIC(REG_TXRX_CONTROL) = 0;
}

static void nic_reset(void)
{
    stop_txrx(); NIC(REG_TXRX_CONTROL) = TXRX_DISABLE | TXRX_RESET;
    KeStallExecutionProcessor(10); NIC(REG_TXRX_CONTROL) = TXRX_DISABLE;
    NIC(REG_TX_RING) = 0; NIC(REG_RX_RING) = 0;
}

void tes3x_net_stop(void)
{
    u32 flags, i;
    if (tes3x_net.up) {
        flags = tes3x_net_lock();
        for (i = 0; i < channel_count; i++)
            if (channels[i]->closing) channels[i]->closing();
        tes3x_net_unlock(flags);
        for (i = 0; i < 1000 && (tx_ring[(tx_tail + TX_RING - 1) % TX_RING].flags & TX_VALID); i++)
            KeStallExecutionProcessor(5);
        KeCancelTimer(timer); KeRemoveQueueDpc(tick_dpc);
    }
    NIC(REG_IRQ_MASK) = 0;
    if (tes3x_net.up) {
        for (i = 0; i < channel_count; i++)
            if (channels[i]->stopping) channels[i]->stopping();
        KeDisconnectInterrupt(interrupt); KeRemoveQueueDpc(dpc);
        set_thunk(THUNK_HalReturnToFirmware, (void *)firmware_original);
    }
    nic_reset(); NIC(REG_ADAPTER) = 0;
    if (pool) MmFreeContiguousMemory(pool);
    pool = 0; tes3x_net.up = 0;
    tes3x_log("net.stopped", 0);
}

static void __stdcall firmware_hook(u32 routine)
{
    fn_HalReturnToFirmware original = firmware_original;
    if (tes3x_net.up) {
        tes3x_net_stop();
        tes3x_log("net.stopped_for_firmware", routine);
    }
    original(routine);
}

void tes3x_net_set_mask(u32 mask) { tes3x_net.mask = mask; }

int tes3x_net_start(u32 ip, int irq)
{
    fn_PhyInitialize phy_init = (fn_PhyInitialize)kernel_export(ORD_PHY_INITIALIZE);
    fn_PhyGetLinkState phy_link = (fn_PhyGetLinkState)kernel_export(ORD_PHY_GET_LINK_STATE);
    u32 type, i, link, vector, tx_phys, next;
    u8 irql;
    if (!phy_init || !phy_link) { tes3x_log("net.no_phy_export", 0); return 0; }
    if (ExQueryNonVolatileSetting(XC_FACTORY_ETHERNET_ADDR, &type, mac, 6, 0) & 0x80000000u) {
        tes3x_log("net.no_mac", 0); return 0;
    }
    tes3x_log_hex3("net.mac", mac[0] << 24 | mac[1] << 16 | mac[2] << 8 | mac[3],
                   mac[4] << 8 | mac[5], ip);
    pool = MmAllocateContiguousMemoryEx(POOL_BYTES, 0, 0xFFFFFFFFu, 0, PAGE_READWRITE);
    if (!pool) { tes3x_log("net.no_memory", POOL_BYTES); return 0; }
    for (i = 0; i < 4096; i++) pool[i] = 0;
    rx_ring = (volatile struct descriptor *)pool; tx_ring = rx_ring + RX_RING;
    rx_buf = pool + 4096; tx_buf = rx_buf + RX_RING * BUF; rx_head = tx_tail = 0;
    tes3x_net.ip = ip;
    NIC(REG_PACKET_FILTER) = 0; NIC(REG_TX_CONTROL) = 0; NIC(REG_RX_CONTROL) = 0;
    NIC(REG_ADAPTER) = 0; KeStallExecutionProcessor(50); nic_reset();
    NIC(REG_TXRX_CONTROL) = 0; NIC(REG_MII_MASK) = 0; NIC(REG_IRQ_MASK) = 0;
    NIC(REG_WAKEUP) = 0; NIC(REG_POLLING) = 0; NIC(REG_TX_POLL) = 0; NIC(REG_LINK_SPEED) = 0;
    NIC(REG_TX_STATUS) = NIC(REG_TX_STATUS); NIC(REG_RX_STATUS) = NIC(REG_RX_STATUS);
    NIC(REG_IRQ_STATUS) = NIC(REG_IRQ_STATUS); NIC(REG_MII_STATUS) = NIC(REG_MII_STATUS);
    NIC(REG_MAC_A) = mac[0] | mac[1] << 8 | mac[2] << 16 | (u32)mac[3] << 24;
    NIC(REG_MAC_B) = mac[4] | mac[5] << 8;
    NIC(REG_MCAST_ADDR_A) = NIC(REG_MCAST_MASK_A) = 0xFFFFFFFFu;
    NIC(REG_MCAST_ADDR_B) = NIC(REG_MCAST_MASK_B) = 0xFFFFu;
    NIC(REG_OFFLOAD) = 0x5EE; NIC(REG_PACKET_FILTER) = 0x7F0020;
    NIC(REG_DUPLEX) = 0x003B0F3E; NIC(REG_SLOT_TIME) = 0x7F00 | mac[5];
    NIC(REG_TX_DEFERRAL) = 0x16070F; NIC(REG_RX_DEFERRAL) = 0x16;
    NIC(REG_SETUP7) = NIC(REG_TX_WATERMARK) = 0x300010;
    NIC(REG_TX_RING) = MmGetPhysicalAddress((void *)tx_ring);
    NIC(REG_RX_RING) = MmGetPhysicalAddress((void *)rx_ring);
    NIC(REG_RING_SIZES) = (RX_RING - 1) << 16 | (TX_RING - 1);
    NIC(REG_TXRX_CONTROL) = TXRX_DISABLE | TXRX_RESET; KeStallExecutionProcessor(10);
    NIC(REG_TXRX_CONTROL) = TXRX_DISABLE; KeStallExecutionProcessor(10); NIC(REG_TXRX_CONTROL) = 0;
    NIC(REG_ADAPTER) = 1u << 24 | ADAPTER_PHYVALID; NIC(REG_MII_SPEED) = 1u << 8 | 5;
    NIC(REG_MII_MASK) = 0x08; KeStallExecutionProcessor(50);
    if (phy_init(0, 0) != 0) { tes3x_log("net.phy_fail", 0); tes3x_net_stop(); return 0; }
    NIC(REG_ADAPTER) |= ADAPTER_RUNNING; KeStallExecutionProcessor(50);
    link = phy_link(0); tes3x_log_hex("net.link", link);
    if (link & LINK_FULL_DUPLEX) NIC(REG_DUPLEX) &= ~2u; else NIC(REG_DUPLEX) |= 2u;
    NIC(REG_LINK_SPEED) = (link & LINK_10MBPS ? 1000 : 100) | 0x10000;
    tx_phys = MmGetPhysicalAddress((void *)tx_ring); next = NIC(REG_TX_CURRENT_DESC);
    if (next >= tx_phys && next < tx_phys + TX_RING * 8 && !((next - tx_phys) & 7))
        tx_tail = (next - tx_phys) / 8;
    tes3x_log_hex3("net.tx_desc", tx_phys, next, NIC(REG_TX_NEXT_DESC));
    tes3x_log_hex3("net.rx_desc", MmGetPhysicalAddress((void *)rx_ring),
                   NIC(REG_RX_CURRENT_DESC), NIC(REG_RX_NEXT_DESC));
    if (irq) {
        for (i = 0; i < RX_RING; i++) {
            rx_ring[i].paddr = MmGetPhysicalAddress(rx_buf + i * BUF);
            rx_ring[i].length = BUF; rx_ring[i].flags = RX_AVAIL;
        }
        vector = HalGetInterruptVector(NIC_IRQ_LINE, &irql);
        KeInitializeInterrupt(interrupt, (void *)nic_isr, 0, vector, irql, LEVEL_SENSITIVE, 1);
        KeInitializeDpc(dpc, (void *)nic_dpc, 0);
        if (!KeConnectInterrupt(interrupt)) {
            tes3x_log("net.no_interrupt", vector); tes3x_net_stop(); return 0;
        }
        firmware_original = *(fn_HalReturnToFirmware *)THUNK_HalReturnToFirmware;
        set_thunk(THUNK_HalReturnToFirmware, (void *)firmware_hook);
        tes3x_net.up = 1;
        for (i = 0; i < channel_count; i++) if (channels[i]->started) channels[i]->started();
        NIC(REG_IRQ_MASK) = IRQ_ENABLED; NIC(REG_RX_CONTROL) |= 1u;
        KeInitializeTimerEx(timer, 0); KeInitializeDpc(tick_dpc, (void *)tick, 0); arm_timer();
    }
    NIC(REG_TX_CONTROL) |= 1u; NIC(REG_TXRX_CONTROL) = TXRX_KICK | TXRX_GET;
    return 1;
}

void tes3x_net_probe(u32 target)
{
    u32 i, flags, rx = tes3x_net.rx, nobuf = tes3x_net.rx_nobuf, full = tes3x_net.tx_full;
    probe_ip = target; probe_hits = 0;
    for (i = 0; i < 3; i++) {
        flags = tes3x_net_lock(); tes3x_net_arp(target); tes3x_net_unlock(flags);
        KeStallExecutionProcessor(30000);
    }
    for (i = 0; i < 20 && probe_hits < 3; i++) KeStallExecutionProcessor(10000);
    tes3x_log_hex3("net.probe", probe_hits, tes3x_net.rx - rx, 0);
    tes3x_log_hex3("net.probe_errors", tes3x_net.rx_nobuf - nobuf,
                   tes3x_net.tx_full - full, tes3x_net.rx_errors);
    probe_ip = 0;
}

void tes3x_net_broadcast(u32 count)
{
    static const char magic[8] = "TES3XNET";
    int temporary = !tes3x_net.up;
    u32 i, j, sent = 0, flags, slot;
    u8 body[22], *frame;
    static const u8 broadcast_mac[6] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};
    if (temporary && !tes3x_net_start(0, 0)) return;
    for (i = 0; i < count; i++) {
        flags = tes3x_net_lock(); slot = tx_tail;
        copy(body, (const u8 *)magic, 8); put32le(body + 8, i); put32le(body + 12, count);
        copy(body + 16, mac, 6);
        frame = tx_begin();
        if (frame) {
            /* Broadcasts deliberately retain the old fixed IP packet and sequence value. */
            copy(frame, broadcast_mac, 6); copy(frame + 6, mac, 6); put16(frame + 12, ETH_IP);
            frame[14] = 0x45; frame[15] = 0; put16(frame + 16, 50); put16(frame + 18, i);
            put16(frame + 20, 0); frame[22] = 64; frame[23] = 17;
            put32(frame + 26, tes3x_net.ip); put32(frame + 30, 0xFFFFFFFFu); ip_checksum(frame + 14);
            put16(frame + 34, TES3X_NET_PORT); put16(frame + 36, TES3X_NET_PORT);
            put16(frame + 38, 30); put16(frame + 40, 0); copy(frame + 42, body, 22); tx_commit(64);
        }
        tes3x_net_unlock(flags);
        if (!frame) { tes3x_log("net.tx_full", i); break; }
        for (j = 0; j < 20000 && (tx_ring[slot].flags & TX_VALID); j++)
            KeStallExecutionProcessor(5);
        if (tx_ring[slot].flags & TX_VALID) { tes3x_log("net.tx_timeout", i); break; }
        tes3x_log_hex3("net.tx_done", i, tx_ring[slot].flags, j * 5); sent++;
    }
    tes3x_log("net.sent", sent);
    if (temporary) tes3x_net_stop();
}

void tes3x_net_stat(void)
{
    u32 i, flags, ring = 0;
    tes3x_log("net.up", tes3x_net.up);
    if (tes3x_net.up) {
        for (i = 0; i < RX_RING; i++) ring += (rx_ring[i].flags & RX_AVAIL) != 0;
        tes3x_log_hex3("net.regs", NIC(REG_IRQ_STATUS), NIC(REG_RX_CONTROL), NIC(REG_RX_STATUS));
        tes3x_log_hex3("net.ring", ring, NIC(REG_IRQ_MASK), NIC(REG_ADAPTER));
        tes3x_log_hex3("net.rx_desc", MmGetPhysicalAddress((void *)rx_ring),
                       NIC(REG_RX_CURRENT_DESC), NIC(REG_RX_NEXT_DESC));
        flags = tes3x_net_lock(); rx_drain(); tes3x_net_unlock(flags);
    }
    tes3x_log_hex3("net.rx", tes3x_net.rx, tes3x_net.rx_errors, tes3x_net.rx_nobuf);
    tes3x_log_hex3("net.irq", tes3x_net.irqs, tes3x_net.dpcs, tes3x_net.rx_peak);
    tes3x_log_hex3("net.answered", tes3x_net.arp, tes3x_net.echo, 0);
    tes3x_log_hex3("net.tx", tes3x_net.tx, tes3x_net.tx_full, tes3x_net.tx_errors);
}

#ifdef TES3X_MULTIPLAYER
void tes3x_multi_entry(void);
#endif

void tes3x_net_entry(void)
{
#ifdef TES3X_MULTIPLAYER
    tes3x_multi_entry();
#endif
}

void tes3x_net_frame(void)
{
    u32 i;
    for (i = 0; i < channel_count; i++)
        if (channels[i]->frame)
            channels[i]->frame();
}

int tes3x_net_command(const char *text)
{
    u32 i;
    for (i = 0; i < channel_count; i++)
        if (channels[i]->command && channels[i]->command(text))
            return 1;
    return 0;
}
