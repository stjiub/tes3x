/* Network test: bring the NIC up with a polled transmit ring and no interrupts, broadcast UDP
 * datagrams, and stop it again, so nothing runs across a title relaunch.
 *
 * The console command `tes3xnet [count]` sends count datagrams (default 8) from 0.0.0.0 to
 * 255.255.255.255, port 26500. Each carries "TES3XNET", its sequence number and the count.
 * Register layout and bring-up order follow nxdk's nvnetdrv.
 */

#include "tes3x_thunks.h"
#include "tes3xnt.h"

typedef unsigned short u16;

typedef void *(__stdcall *fn_MmAllocateContiguousMemoryEx)(u32, u32, u32, u32, u32);
typedef void(__stdcall *fn_MmFreeContiguousMemory)(void *);
typedef u32(__stdcall *fn_MmGetPhysicalAddress)(void *);
typedef u32(__stdcall *fn_ExQueryNonVolatileSetting)(u32, u32 *, void *, u32, u32 *);
typedef u32(__stdcall *fn_KeDelayExecutionThread)(u32, unsigned char, long long *);
typedef u32(__stdcall *fn_PhyInitialize)(unsigned char, void *);
typedef u32(__stdcall *fn_PhyGetLinkState)(unsigned char);

#define MmAllocateContiguousMemoryEx \
    KFN(THUNK_MmAllocateContiguousMemoryEx, fn_MmAllocateContiguousMemoryEx)
#define MmFreeContiguousMemory KFN(THUNK_MmFreeContiguousMemory, fn_MmFreeContiguousMemory)
#define MmGetPhysicalAddress KFN(THUNK_MmGetPhysicalAddress, fn_MmGetPhysicalAddress)
#define ExQueryNonVolatileSetting \
    KFN(THUNK_ExQueryNonVolatileSetting, fn_ExQueryNonVolatileSetting)
#define KeDelayExecutionThread KFN(THUNK_KeDelayExecutionThread, fn_KeDelayExecutionThread)

#define XC_FACTORY_ETHERNET_ADDR 0x101u
#define PAGE_READWRITE 0x04u
#define ORD_PHY_GET_LINK_STATE 252u
#define ORD_PHY_INITIALIZE 253u
#define LINK_ACTIVE 0x01u
#define LINK_10MBPS 0x04u
#define LINK_FULL_DUPLEX 0x08u

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
#define REG_TX_WATERMARK 0x13Cu
#define REG_SETUP7 0x140u
#define REG_TXRX_CONTROL 0x144u
#define REG_MII_STATUS 0x180u
#define REG_MII_MASK 0x184u
#define REG_ADAPTER 0x188u
#define REG_MII_SPEED 0x18Cu
#define REG_WAKEUP 0x200u

#define TXRX_KICK 0x01u
#define TXRX_GET 0x02u
#define TXRX_DISABLE 0x04u
#define TXRX_IDLE 0x08u
#define TXRX_RESET 0x10u
#define ADAPTER_PHYVALID 0x40000u
#define ADAPTER_RUNNING 0x100000u
#define TX_LASTPACKET 0x0001u
#define TX_VALID 0x8000u

#define RX_RING 2u
#define TX_RING 4u
#define FRAME_SLOT 128u
#define PORT 26500u

struct descriptor {
    u32 paddr;
    u16 length;
    u16 flags;
} __attribute__((packed));

static void delay_us(u32 us)
{
    long long interval = -(long long)us * 10;
    KeDelayExecutionThread(0, 0, &interval);
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

static void stop_txrx(void)
{
    u32 i;

    NIC(REG_RX_CONTROL) &= ~1u;
    NIC(REG_TX_CONTROL) &= ~1u;
    for (i = 0; i < 5000 && ((NIC(REG_RX_STATUS) | NIC(REG_TX_STATUS)) & 1u); i++)
        delay_us(10);
    NIC(REG_TXRX_CONTROL) = TXRX_DISABLE;
    for (i = 0; i < 1000 && !(NIC(REG_TXRX_CONTROL) & TXRX_IDLE); i++)
        delay_us(50);
    NIC(REG_TXRX_CONTROL) = 0;
}

static void put16(u8 *p, u32 v)
{
    p[0] = (u8)(v >> 8);
    p[1] = (u8)v;
}

static void put32le(u8 *p, u32 v)
{
    p[0] = (u8)v;
    p[1] = (u8)(v >> 8);
    p[2] = (u8)(v >> 16);
    p[3] = (u8)(v >> 24);
}

static u32 frame(u8 *f, const u8 *mac, u32 seq, u32 count)
{
    static const char magic[8] = "TES3XNET";
    u32 i, sum = 0, payload = 8 + 4 + 4 + 6;
    u32 udp = 8 + payload, ip = 20 + udp;

    for (i = 0; i < 6; i++) {
        f[i] = 0xFF;
        f[6 + i] = mac[i];
    }
    put16(f + 12, 0x0800);
    f[14] = 0x45;
    f[15] = 0;
    put16(f + 16, ip);
    put16(f + 18, seq);
    put16(f + 20, 0);
    f[22] = 64;
    f[23] = 17;
    put16(f + 24, 0);
    for (i = 0; i < 4; i++) {
        f[26 + i] = 0;
        f[30 + i] = 0xFF;
    }
    for (i = 0; i < 20; i += 2)
        sum += (f[14 + i] << 8) | f[15 + i];
    while (sum >> 16)
        sum = (sum & 0xFFFF) + (sum >> 16);
    put16(f + 24, ~sum & 0xFFFF);
    put16(f + 34, PORT);
    put16(f + 36, PORT);
    put16(f + 38, udp);
    put16(f + 40, 0); /* optional in IPv4 */
    for (i = 0; i < 8; i++)
        f[42 + i] = (u8)magic[i];
    put32le(f + 50, seq);
    put32le(f + 54, count);
    for (i = 0; i < 6; i++)
        f[58 + i] = mac[i];
    return 14 + ip;
}

static void net_test(u32 count)
{
    fn_PhyInitialize phy_init = (fn_PhyInitialize)kernel_export(ORD_PHY_INITIALIZE);
    fn_PhyGetLinkState phy_link = (fn_PhyGetLinkState)kernel_export(ORD_PHY_GET_LINK_STATE);
    volatile struct descriptor *rx, *tx;
    u8 mac[6], *page, *buf;
    u32 type, i, j, link, len, sent = 0;

    if (!phy_init || !phy_link) {
        tes3x_log("net.no_phy_export", 0);
        return;
    }
    if (ExQueryNonVolatileSetting(XC_FACTORY_ETHERNET_ADDR, &type, mac, 6, 0) & 0x80000000u) {
        tes3x_log("net.no_mac", 0);
        return;
    }
    tes3x_log_hex3("net.mac", mac[0] << 24 | mac[1] << 16 | mac[2] << 8 | mac[3],
                   mac[4] << 8 | mac[5], 0);
    page = MmAllocateContiguousMemoryEx(4096, 0, 0xFFFFFFFFu, 0, PAGE_READWRITE);
    if (!page) {
        tes3x_log("net.no_memory", 0);
        return;
    }
    for (i = 0; i < 4096; i++)
        page[i] = 0;
    rx = (volatile struct descriptor *)page;
    tx = rx + RX_RING;
    buf = page + 256;

    stop_txrx();
    NIC(REG_TXRX_CONTROL) = TXRX_RESET;
    delay_us(10);
    NIC(REG_TXRX_CONTROL) = 0;
    delay_us(10);
    NIC(REG_MII_MASK) = 0;
    NIC(REG_IRQ_MASK) = 0;
    NIC(REG_WAKEUP) = 0;
    NIC(REG_POLLING) = 0;
    NIC(REG_TX_RING) = 0;
    NIC(REG_RX_RING) = 0;
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
    NIC(REG_TX_RING) = MmGetPhysicalAddress((void *)tx);
    NIC(REG_RX_RING) = MmGetPhysicalAddress((void *)rx);
    NIC(REG_RING_SIZES) = (RX_RING - 1) << 16 | (TX_RING - 1);

    NIC(REG_ADAPTER) = 1u << 24 | ADAPTER_PHYVALID;
    NIC(REG_MII_SPEED) = 1u << 8 | 5;
    delay_us(50);
    if (phy_init(0, 0) != 0) {
        tes3x_log("net.phy_fail", 0);
        goto out;
    }
    NIC(REG_ADAPTER) |= ADAPTER_RUNNING;
    delay_us(50);

    link = phy_link(0);
    tes3x_log_hex("net.link", link);
    if (link & LINK_FULL_DUPLEX)
        NIC(REG_DUPLEX) &= ~2u;
    else
        NIC(REG_DUPLEX) |= 2u;
    NIC(REG_LINK_SPEED) = (link & LINK_10MBPS ? 1000 : 100) | 0x10000;
    NIC(REG_TX_CONTROL) |= 1u; /* the receiver stays off */
    NIC(REG_TXRX_CONTROL) = TXRX_KICK | TXRX_GET;

    for (i = 0; i < count; i++) {
        volatile struct descriptor *d = &tx[i % TX_RING];
        u8 *f = buf + (i % TX_RING) * FRAME_SLOT;

        len = frame(f, mac, i, count);
        d->paddr = MmGetPhysicalAddress(f);
        d->length = (u16)(len - 1);
        d->flags = TX_LASTPACKET | TX_VALID;
        NIC(REG_TXRX_CONTROL) = TXRX_KICK;
        for (j = 0; j < 2000 && (d->flags & TX_VALID); j++)
            delay_us(50);
        if (d->flags & TX_VALID) {
            tes3x_log("net.tx_timeout", i);
            break;
        }
        tes3x_log_hex3("net.tx_done", i, d->flags, j);
        sent++;
    }
    tes3x_log("net.sent", sent);

out:
    stop_txrx();
    NIC(REG_TXRX_CONTROL) = TXRX_DISABLE | TXRX_RESET;
    delay_us(10);
    NIC(REG_TXRX_CONTROL) = TXRX_DISABLE;
    NIC(REG_TX_RING) = 0;
    NIC(REG_RX_RING) = 0;
    MmFreeContiguousMemory(page);
    tes3x_log("net.stopped", 0);
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

int tes3x_net_command(const char *text)
{
    u32 count = 0;

    if (!(text = word(text, "tes3xnet")))
        return 0;
    while (*text == ' ')
        text++;
    while (*text >= '0' && *text <= '9')
        count = count * 10 + (u32)(*text++ - '0');
    if (*text) {
        tes3x_log("net.usage", 0);
        return 1;
    }
    net_test(count ? count : 8);
    return 1;
}
