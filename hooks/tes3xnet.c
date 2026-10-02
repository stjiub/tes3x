/* Network driver for the NIC, after nxdk's nvnetdrv: interrupt-driven receive, a polled transmit
 * ring, ARP, a UDP echo responder on port 26500 and a session with a PC server.
 *
 * Console commands:
 *   tes3xnet up A.B.C.D[/BITS]|dhcp [SERVER[:PORT] [GATEWAY|- [DNS]]]
 *                         bring the NIC up with that address, or one leased by DHCP with its
 *                         mask, gateway and DNS server, answer ARP and echo requests, and
 *                         with a server keep a session: HELLO until WELCOME, then a heartbeat
 *                         each second; five silent seconds start over. A SERVER that is a name
 *                         is looked up at DNS (default GATEWAY) first, and again after a timeout
 *   tes3xnet probe A.B.C.D  ARP for an address three times and log the replies
 *   tes3xnet stat         log the counters
 *   tes3xnet say TEXT     send TEXT to the other clients as a reliable event
 *   tes3xnet send NAME    send U:\TES3X\NAME to the server
 *   tes3xnet menusim 0|1|auto  force the world paused or running under menus, or follow the
 *                         session (the default: running while joined)
 *   tes3xnet weather roll 0|1|auto  force this console's own weather rolls off or on, or follow
 *                         the session (the default: only the lowest client id rolls)
 *   tes3xnet down         stop the NIC
 *   tes3xnet [count]      broadcast count "TES3XNET" datagrams (default 8), from 0.0.0.0 unless up
 *
 * With [Xbox] NetAddress set, the first frame brings the NIC up as `up` would, from NetAddress,
 * NetServer, NetGateway and NetDns; every launch and relaunch joins by itself, giving NetPassword
 * if set (a server asks for it only of a console key it has not admitted). While joined, each frame
 * sends the player's state, and the server relays the other clients' states back. Each other
 * client near the player is drawn as a ghost NPC from the plugin the pipeline adds. While joined,
 * menus do not pause the world, the rest menu closes as it opens, and the server's clock sets the
 * time globals; a console joins only once a game is loaded, offering its own clock. Weather is the
 * server's too: one client rolls it, and each region's weather goes through the server. The server
 * names one client the authority for each loaded cell: it runs those actors and sends their states, and
 * the others hold those actors' AI and place them from the states. Ghosts and followed
 * actors mirror their source's animation layers: moving, attacking, casting and the rest. A spell
 * takes effect on the console that runs its target. Items taken, objects disabled and locks go
 * through the server, which replays them to consoles that join later, and so do objects made at
 * run time: dropped items and what the console or a script places. A file the server offers is
 * received in chunks into U:\TES3X, checked against its BLAKE2b hash (Monocypher).
 *
 * While up, the HalReturnToFirmware thunk points at a wrapper that stops the NIC first: a quick
 * reboot keeps the kernel, which would otherwise keep a connected interrupt object and a live DMA
 * ring inside the next title's memory.
 */

#include "tes3x_thunks.h"
#include "tes3xnt.h"
#include "monocypher.h"
#include "tes3xnoise.h"
#ifdef TES3X_CONSOLE
int tes3x_console_text_begin_on(void *menu, const char *initial);
void *tes3x_console_text_field(void);
int tes3x_console_text_poll(char *out, u32 size);
const char *tes3x_console_text_now(void);
#endif

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
#ifndef TES3X_NET_WEATHER_ROLL
#error "define TES3X_NET_WEATHER_ROLL to the Region::randomizeWeather call in the region loop"
#endif
#ifndef TES3X_NET_SERVICE_ACTOR
#error "define TES3X_NET_SERVICE_ACTOR to ui::getServiceActor"
#endif
#if !defined(TES3X_NET_FIND_MENU) || !defined(TES3X_NET_UI_ID) || !defined(TES3X_NET_TRIGGER_EVENT)
#error "define TES3X_NET_FIND_MENU, TES3X_NET_UI_ID and TES3X_NET_TRIGGER_EVENT to the UI functions"
#endif
#if !defined(TES3X_NET_FIND_REFERENCE) || !defined(TES3X_NET_REF_ANIMATION) || \
    !defined(TES3X_NET_REF_ORIENTATION) || !defined(TES3X_NET_REF_ROTATION) || \
    !defined(TES3X_NET_NODE_SET_ROTATION) || !defined(TES3X_NET_NODE_UPDATE) || \
    !defined(TES3X_NET_ANIM_HAS_GROUP) || !defined(TES3X_NET_ANIM_PLAY_GROUP) || \
    !defined(TES3X_NET_UNREADY_WEAPON) || !defined(TES3X_NET_MOBILE_HANDS) ||     !defined(TES3X_NET_APPLY_HEALTH_DAMAGE) || !defined(TES3X_NET_APPLY_FATIGUE_DAMAGE) ||     !defined(TES3X_NET_HIT_STUN)
#error "define the TES3X_NET_ functions SetPos, SetAngle, PlayGroup, weapon readying and damage"
#endif
#if !defined(TES3X_NET_SPELL_HIT) || !defined(TES3X_NET_SPELL_HIT_SITES) || \
    !defined(TES3X_NET_ACTIVATE_SPELL) || !defined(TES3X_NET_MAGIC_INSTANCE) || \
    !defined(TES3X_NET_RESOLVE_OBJECT) || !defined(TES3X_NET_CAST_BOLT) || \
    !defined(TES3X_NET_CAST_BOLT_SITES)
#error "define TES3X_NET_SPELL_HIT, TES3X_NET_CAST_BOLT, their call sites and the spell functions"
#endif
#if !defined(TES3X_NET_SHOOT) || !defined(TES3X_NET_SHOOT_SLOTS) || !defined(TES3X_NET_NOCK) || \
    !defined(TES3X_NET_HIT_ROLL) || !defined(TES3X_NET_SHOT_ROLL_SITES) || !defined(TES3X_NET_BLOOD) || \
    !defined(TES3X_NET_ACTIVATION_TARGET) || !defined(TES3X_NET_ACTIVATION_TARGET_SITES)
#error "define TES3X_NET_SHOOT, its vtable slots, TES3X_NET_NOCK, the hit roll and the blood"
#endif
#if !defined(TES3X_NET_AI_STEP) || !defined(TES3X_NET_AI_STEP_SLOTS)
#error "define TES3X_NET_AI_STEP, an actor's AI step, and its vtable slots"
#endif
#if !defined(TES3X_NET_REF_MODIFIED) || !defined(TES3X_NET_REF_MODIFIED_SLOT)
#error "define TES3X_NET_REF_MODIFIED, Reference::setObjectModified, and its vtable slot"
#endif
#if !defined(TES3X_NET_CREATE_REFERENCE) || !defined(TES3X_NET_CELL_INSERT) || \
    !defined(TES3X_NET_CELL_NODE) || !defined(TES3X_NET_CELL_ACTIVATORS) || \
    !defined(TES3X_NET_ATTACH_SCENE) || !defined(TES3X_NET_ITEM_DATA_NEW) || \
    !defined(TES3X_NET_ATTACH_ITEM_DATA) || !defined(TES3X_NET_UPDATE_LIGHTING)
#error "define the TES3X_NET_ functions that make a reference at run time"
#endif
#if !defined(TES3X_NET_CONTAINER_VTABLE) || !defined(TES3X_NET_CONTAINER_INSTANCE_VTABLE) || \
    !defined(TES3X_NET_INVENTORY_ADD) || !defined(TES3X_NET_INVENTORY_REMOVE) || \
    !defined(TES3X_NET_ITEM_DATA_DESTROY) || !defined(TES3X_NET_HEAP_FREE) || \
    !defined(TES3X_NET_HEAP)
#error "define the TES3X_NET_ container vtables and inventory functions"
#endif
#if !defined(TES3X_NET_LEVELED_SPAWN) || !defined(TES3X_NET_LEVELED_SPAWN_SLOT) || \
    !defined(TES3X_NET_LEVELED_RESOLVE) || !defined(TES3X_NET_LEVELED_LINKED) || \
    !defined(TES3X_NET_LEVELED_LINK) || !defined(TES3X_NET_ADD_MOB) || !defined(TES3X_NET_SIMULATE) || \
    !defined(TES3X_NET_SUMMON) || !defined(TES3X_NET_SUMMON_SITES) || \
    !defined(TES3X_NET_PLAYER_SCRIPT_SITES) || !defined(TES3X_NET_DROP_ITEM) || \
    !defined(TES3X_NET_PLAYER_DROP_SITES) || !defined(TES3X_NET_START_COMBAT)
#error "define the TES3X_NET_ leveled creature spawn, its vtable slot and the actor functions"
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
typedef u32(__stdcall *fn_PsCreateSystemThreadEx)(void **, u32, u32, u32, void **,
                                                  void(__stdcall *)(void *), void *,
                                                  unsigned char, unsigned char, void *);
typedef void(__stdcall *fn_PsTerminateSystemThread)(u32);
typedef u32(__stdcall *fn_KeDelayExecutionThread)(u32, unsigned char, long long *);
typedef long(__stdcall *fn_KeSetBasePriorityThread)(void *, long);
typedef long(__stdcall *fn_KeQueryBasePriorityThread)(void *);

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
#define PsCreateSystemThreadEx KFN(THUNK_PsCreateSystemThreadEx, fn_PsCreateSystemThreadEx)
#define PsTerminateSystemThread KFN(THUNK_PsTerminateSystemThread, fn_PsTerminateSystemThread)
#define KeDelayExecutionThread KFN(THUNK_KeDelayExecutionThread, fn_KeDelayExecutionThread)
#define KeSetBasePriorityThread KFN(THUNK_KeSetBasePriorityThread, fn_KeSetBasePriorityThread)
#define KeQueryBasePriorityThread \
    KFN(THUNK_KeQueryBasePriorityThread, fn_KeQueryBasePriorityThread)

#define XC_FACTORY_ETHERNET_ADDR 0x101u
#define PAGE_READWRITE 0x04u
#define ORD_PHY_GET_LINK_STATE 252u
#define ORD_PHY_INITIALIZE 253u
#define LINK_10MBPS 0x04u
#define LINK_FULL_DUPLEX 0x08u
#define NIC_IRQ_LINE 4u
#define LEVEL_SENSITIVE 0u
#define CR0_WP 0x10000u
#define TES3X_NET_STR_(x) #x
#define TES3X_NET_STR(x) TES3X_NET_STR_(x)

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

/* xemu's NIC never resumes reading its tunnel once a frame finds the next slot full, so the ring
 * is sized for bursts rather than for the rate. */
#define RX_RING 32u
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

/* Session packet: "T3MP", version, type, then session, seq, ack, time and echoed peer time. On
 * the wire only the handshake is not sealed: a SEALED packet keeps "T3MP", version, its type,
 * session and seq in the clear (T3MP_OUTER, the AEAD's associated data) and seals the real type,
 * ack, times and body under the session's key, seq being the nonce. The receiver rebuilds the
 * T3MP_HEADER layout after opening it. */
#define T3MP_VERSION 16u
#define T3MP_HEADER 28u
#define T3MP_OUTER 16u
#define T3MP_INNER 16u
#define T3MP_HANDSHAKE1 20u
#define T3MP_HANDSHAKE2 21u
#define T3MP_HANDSHAKE3 22u
#define T3MP_SEALED 23u
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
#define T3MP_CHUNK 16u
#define T3MP_BULK_ACK 17u
#define SESSION_IDLE 0u
#define SESSION_ARP 1u
#define SESSION_HELLO 2u
#define SESSION_JOINED 3u
#define SESSION_RESOLVE 4u
#define SESSION_REFUSED 5u
#define SESSION_UNTRUSTED 7u /* the server's key is not the one pinned */
#define TRUST_FINGERPRINT 16u /* BLAKE2b of the server's static key, as NetServer's #HEX */
/* The clock: GameHour, Day, Month, Year, DaysPassed and TimeScale, the globals' raw floats. HELLO
 * carries the client's own, CLOCK the server's. */
#define CLOCK_GLOBALS 6u
#define CLOCK_BYTES (CLOCK_GLOBALS * 4u)
#define HELLO_BYTES (18u + CLOCK_BYTES)
#define PASSWORD_MAX 64u /* NetPassword, after HELLO */
static char net_password[PASSWORD_MAX + 2]; /* read with the other keys: an ini read costs ms */
static u32 net_password_n;
#define DNS_PORT 53u
#define HOST_NAME 64u
#define JOIN_NAME (HOST_NAME + 8 + 1 + 2 * TRUST_FINGERPRINT) /* NetServer's text */
static char join_server[JOIN_NAME + 1]; /* the server a main menu Join chose */
/* A session from the main menu, with no game loaded: the server sends it no world, only the
 * character list or the start points. Marked in HELLO's plugin count. */
#define LOBBY_PLUGINS 0x80000000u
static u32 lobby;

static struct {
    u32 up, ip, mask, irqs, dpcs, rx, rx_errors, rx_nobuf, rx_peak, arp, echo;
    u32 tx, tx_full, tx_errors;
} net;
static struct {
    u32 state, server, port, gateway, hop, hop_known, id, client, seq, peer_seq, peer_time;
    u32 ticks, quiet, hellos, welcomes, beats_out, beats_in, timeouts, gaps;
    u32 rtt_last, rtt_min, rtt_max, rtt_sum, rtt_count;
    u32 states_out, peers_in;
    u32 dns, dns_id, dns_queries, dns_answers;
    u32 plugins, plugins_hash, refused_hash, refused_plugins, refused_reason;
    char host[HOST_NAME]; /* the server's name, if it is not an address */
    u8 hop_mac[6];
} ses;
#define SESSION_DHCP 6u /* waiting for an address */

/* DHCP, when NetAddress is "dhcp": the address, mask, gateway and DNS server come from the
 * network. The lease is renewed at half its time and dropped at its end or on a NAK. */
#define DHCP_CLIENT 68u
#define DHCP_SERVER 67u
#define DHCP_IDLE 0u
#define DHCP_DISCOVER 1u
#define DHCP_REQUEST 2u
#define DHCP_BOUND 3u
static struct {
    u32 state, xid, ticks, tries, offered, server, lease, keep_gateway, keep_dns;
    u32 discovers, requests, offers, acks, naks, renewals, expiries;
} dhcp;

/* The player's state: flags, position, heading (orientation z), the interior cell's name, then
 * the animation (anim_capture). Exterior cells are left empty; the grid follows from the
 * position. */
#define STATE_IN_WORLD 1u
#define STATE_INTERIOR 2u
#define STANCE_WEAPON 4u /* in STATE's flags and ACTOR's */
#define STANCE_SPELL 8u
#define STATE_ANIM 52u
#define ANIM_BYTES 20u
#define STATE_BYTES (STATE_ANIM + ANIM_BYTES)
#define CELL_NAME 32u
#define PEERS 8u
#define PEER_TIMEOUT_US 5000000u

#define SAMPLES 8u

/* The recent relayed states of each other client, by the server's client id. The receive DPC
 * only copies bytes: it runs without the game's floating-point state saved. */
static struct {
    u32 client, seq, time, head, count, busy;
    struct {
        u32 time;
        u8 state[STATE_BYTES];
    } samples[SAMPLES];
} peers[PEERS];
/* Reliable events: each side numbers its events from 1 per session and resends every unacked one
 * each tick; the receiver takes only the next number, so delivery is in order. An EVENTS packet
 * is the other side's last delivered number, a count, then events of EVENT_HEADER + length. */
#define EVENT_TEXT 1u
#define EVENT_OFFER 32u /* id, size, hash, then the file name */
#define EVENT_HEADER 12u /* seq, kind, length, origin client */
#define EVENT_DATA 80u
#define EVENTS_OUT 16u
#define EVENTS_IN 16u
#define EVENTS_BYTES 512u
#define SEND_BYTES 1032u /* the largest body: a CHUNK's id, index and 1024 bytes */

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

static u32 ghost_places, ghost_moves, ghost_failures, player_hits_out, player_hits_in;
static void ghost_heading_stat(void);
static u32 equip_sent, equip_received, equip_applied, stance_changes, stance_refused;
static u32 first_person_states; /* player states whose animation came from the first person */
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
static void worker_start(void);
static void worker_stop(void);
static void log_text(const char *tag, const char *text);
static void load_order(void);
static int plausible(const void *p);
static void actors_rx(u32 origin, u32 seq, const u8 *p, u32 n);
static void actors_reset(void);
static void weather_event(const struct event *e);
static void bulk_chunk_rx(const u8 *p, u32 n);
static void up_ack_rx(const u8 *p, u32 n);
static void up_pump(void);
static void handshake_tick(void);
static void handshake_rx(const u8 *p, u32 n);
static void handshake_reset(void);
static void entropy_add(void);
static int hex_read(const char *text, u8 *out, u32 n);
static void trust_configure(const char *name, u32 n, const u8 *fingerprint);
static void bulk_tick(void);

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

/* Received numbers are checked before the engine sees them, on their bits: a float's magnitude
 * bits order as its values do, and NaN and the infinities lie above every finite limit. Integer
 * only, because the receive DPC must not touch the FPU state of the thread it interrupts. The
 * limits are tes3x_net.py's placeable and sane_clock. */
#define POSITION_LIMIT 0x4B189680u /* 1e7 */
#define ANGLE_LIMIT 0x42800000u    /* 64 */
#define STAT_LIMIT 0x4B189680u     /* 1e7, statistics and damage */
#define ANIM_TIME_LIMIT 0x461C4000u /* 1e4 seconds into a group */
static u32 refused_states, refused_events, refused_anims;

static int float_within(const u8 *p, u32 count, u32 limit)
{
    u32 i;

    for (i = 0; i < count; i++)
        if ((get32le(p + 4 * i) & 0x7FFFFFFFu) > limit)
            return 0;
    return 1;
}

/* Text that goes into a quoted script argument: ended within max bytes, and with no quote or
 * control character, which would end the string or the line. */
static int script_safe(const u8 *text, u32 max)
{
    u32 i;

    for (i = 0; i < max && text[i]; i++)
        if (text[i] < 0x20 || text[i] == '"')
            return 0;
    return i < max;
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

/* The session's keys from the handshake, and the replay window: the highest seq opened and a
 * bitmap of the 32 up to it. */
static struct {
    u32 keyed, top, seen, sealed, opened, forged, replayed;
    u8 send[NOISE_KEY], receive[NOISE_KEY];
} sec;

static void t3mp_outer(u8 *p, u32 type, u32 session, u32 seq)
{
    p[0] = 'T';
    p[1] = '3';
    p[2] = 'M';
    p[3] = 'P';
    p[4] = T3MP_VERSION;
    p[5] = (u8)type;
    p[6] = p[7] = 0;
    put32le(p + 8, session);
    put32le(p + 12, seq);
}

/* Caller holds the lock. Nothing is sent before the handshake has keyed the session. */
static void session_send(u32 type, const u8 *body, u32 n)
{
    static u8 inner[T3MP_INNER + SEND_BYTES], p[T3MP_OUTER + sizeof(inner) + NOISE_TAG];

    if (n > SEND_BYTES || !sec.keyed)
        return;
    t3mp_outer(p, T3MP_SEALED, ses.id, ++ses.seq);
    inner[0] = (u8)type;
    inner[1] = inner[2] = inner[3] = 0;
    put32le(inner + 4, ses.peer_seq);
    put32le(inner + 8, now_us());
    put32le(inner + 12, ses.peer_time);
    copy(inner + T3MP_INNER, body, n);
    noise_seal(sec.send, ses.seq, p, T3MP_OUTER, inner, T3MP_INNER + n, p + T3MP_OUTER);
    udp_send(ses.server, ses.port, p, T3MP_OUTER + T3MP_INNER + n + NOISE_TAG);
    sec.sealed++;
}

/* Latest state wins: one server sequences everything it sends us, so an older seq is stale. */
static void peer_rx(u32 client, u32 seq, const u8 *state)
{
    u32 i, slot = PEERS;

    if (!float_within(state + 4, 3, POSITION_LIMIT) || !float_within(state + 16, 1, ANGLE_LIMIT) ||
        !script_safe(state + 20, CELL_NAME)) {
        refused_states++;
        return;
    }
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
        peers[slot].count = peers[slot].busy = 0;
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

/* The date globals index tables and the engine rolls the hours over one day at a time, so a
 * CLOCK outside these (as float bits, none negative) is dropped. */
static const u32 clock_limits[CLOCK_GLOBALS][2] = {
    {0, 0x41C00000u},          /* GameHour 0-24 */
    {0x3F800000u, 0x41F80000u}, /* Day 1-31 */
    {0, 0x41300000u},          /* Month 0-11 */
    {0, 0x47C35000u},          /* Year to 100000 */
    {0, 0x4B189680u},          /* DaysPassed to 1e7 */
    {0, 0x461C4000u},          /* TimeScale to 10000 */
};

static int clock_sane(const u8 *p)
{
    u32 i, v;

    for (i = 0; i < CLOCK_GLOBALS; i++)
        if ((v = get32le(p + 4 * i)) < clock_limits[i][0] || v > clock_limits[i][1]) {
            refused_states++;
            return 0;
        }
    return 1;
}

/* Caller holds the lock (the receive DPC). p is an opened packet in the T3MP_HEADER layout. */
static void session_rx_plain(const u8 *p, u32 n)
{
    u32 type, seq, echo, rtt, i;

    if (n < T3MP_HEADER || p[4] != T3MP_VERSION)
        return;
    type = p[5];
    seq = get32le(p + 12);
    echo = get32le(p + 24);
    /* At join, or later as a kick or a ban. */
    if (type == T3MP_REFUSE) {
        if ((ses.state != SESSION_HELLO && ses.state != SESSION_JOINED) || n < T3MP_HEADER + 8)
            return;
        ses.state = SESSION_REFUSED;
        sec.keyed = 0;
        for (i = 0; i < PEERS; i++)
            peers[i].client = 0;
        ses.refused_hash = get32le(p + 28);
        ses.refused_plugins = get32le(p + 32);
        ses.refused_reason = n >= T3MP_HEADER + 12 ? get32le(p + 36) : 0;
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
        handshake_reset();
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
            sec.keyed = 0;
            return;
        }
        if (type == T3MP_HEARTBEAT)
            ses.beats_in++;
        if (type == T3MP_PEER && n >= T3MP_HEADER + 4 + STATE_BYTES)
            peer_rx(get32le(p + T3MP_HEADER), seq, p + T3MP_HEADER + 4);
        if (type == T3MP_EVENTS)
            events_rx(p + T3MP_HEADER, n - T3MP_HEADER);
        if (type == T3MP_CHUNK)
            bulk_chunk_rx(p + T3MP_HEADER, n - T3MP_HEADER);
        if (type == T3MP_BULK_ACK)
            up_ack_rx(p + T3MP_HEADER, n - T3MP_HEADER);
        if (type == T3MP_ACTORS && n >= T3MP_HEADER + 8)
            actors_rx(get32le(p + T3MP_HEADER), seq, p + T3MP_HEADER + 4, n - T3MP_HEADER - 4);
        if (type == T3MP_CLOCK && n >= T3MP_HEADER + CLOCK_BYTES && seq > ses.peer_seq &&
            clock_sane(p + T3MP_HEADER)) {
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

/* Caller holds the lock (the receive DPC). Only a handshake reply or a packet sealed under this
 * session's key and not seen before gets past here. */
static void session_rx(const u8 *p, u32 n)
{
    static u8 plain[12 + BUF];
    u32 seq, type, age;

    if (n < T3MP_OUTER || p[4] != T3MP_VERSION)
        return;
    if (p[5] == T3MP_HANDSHAKE2) {
        handshake_rx(p, n);
        return;
    }
    if (p[5] != T3MP_SEALED || !sec.keyed || get32le(p + 8) != ses.id ||
        n < T3MP_OUTER + T3MP_INNER + NOISE_TAG || n - T3MP_OUTER - NOISE_TAG > BUF)
        return;
    seq = get32le(p + 12);
    age = sec.top - seq;
    if (!seq || ((int)age >= 0 && (age >= 32 || sec.seen >> age & 1))) {
        sec.replayed++;
        return;
    }
    if (noise_open(sec.receive, seq, p, T3MP_OUTER, p + T3MP_OUTER, n - T3MP_OUTER, plain + 12)) {
        sec.forged++;
        return;
    }
    if ((int)age < 0) {
        sec.seen = -age >= 32 ? 1 : sec.seen << -age | 1;
        sec.top = seq;
    } else {
        sec.seen |= 1u << age;
    }
    sec.opened++;
    type = plain[12];
    t3mp_outer(plain, type, ses.id, seq);
    session_rx_plain(plain, 12 + n - T3MP_OUTER - NOISE_TAG);
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

/* A DISCOVER or REQUEST, broadcast; caller holds the lock. A first REQUEST names the offer and
 * its server, a renewal the bound address. */
static void dhcp_send(u32 type)
{
    static const u8 options[] = {55, 4, 1, 3, 6, 51, 12, 5, 'T', 'E', 'S', '3', 'X'};
    u8 *o = tx_begin(), *b = o + 42;
    u32 i, n = 240;

    if (!o)
        return;
    for (i = 0; i < 42 + 300; i++)
        o[i] = 0;
    for (i = 0; i < 6; i++)
        o[i] = 0xFF;
    copy(o + 6, mac, 6);
    put16(o + 12, ETH_IP);
    o[14] = 0x45;
    put16(o + 16, 28 + 300);
    o[22] = 64;
    o[23] = 17;
    put32(o + 26, dhcp.state == DHCP_BOUND ? net.ip : 0);
    put32(o + 30, 0xFFFFFFFFu);
    ip_checksum(o + 14);
    put16(o + 34, DHCP_CLIENT);
    put16(o + 36, DHCP_SERVER);
    put16(o + 38, 8 + 300);
    b[0] = 1; /* request, Ethernet, 6-byte address */
    b[1] = 1;
    b[2] = 6;
    put32(b + 4, dhcp.xid);
    put16(b + 10, 0x8000); /* reply by broadcast: there is no address to reply to yet */
    if (dhcp.state == DHCP_BOUND)
        put32(b + 12, net.ip);
    copy(b + 28, mac, 6);
    put32(b + 236, 0x63825363u);
    b[n++] = 53;
    b[n++] = 1;
    b[n++] = (u8)type;
    if (dhcp.state == DHCP_REQUEST) {
        b[n++] = 50;
        b[n++] = 4;
        put32(b + n, dhcp.offered);
        n += 4;
        b[n++] = 54;
        b[n++] = 4;
        put32(b + n, dhcp.server);
        n += 4;
    }
    copy(b + n, options, sizeof(options));
    n += sizeof(options);
    b[n] = 255;
    tx_commit(42 + 300);
}

/* Caller holds the lock. */
static void dhcp_discover(void)
{
    dhcp.state = DHCP_DISCOVER;
    dhcp.xid = now_us() ^ (u32)mac[4] << 24 ^ (u32)mac[5] << 16;
    dhcp.ticks = 0;
    dhcp_send(1);
    dhcp.discovers++;
}

/* The lease ended or was refused: the session waits for a new address. */
static void dhcp_lost(void)
{
    net.ip = 0;
    if (ses.state != SESSION_IDLE)
        ses.state = SESSION_DHCP;
    dhcp_discover();
}

static void dhcp_bind(u32 ip, u32 mask, u32 router, u32 dns, u32 lease)
{
    u32 fresh = ip != net.ip;

    net.ip = ip;
    net.mask = mask ? mask : 0xFFFFFF00u;
    if (!dhcp.keep_gateway)
        ses.gateway = router;
    if (!dhcp.keep_dns)
        ses.dns = dns ? dns : ses.gateway;
    if (!lease || lease > 7 * 86400u)
        lease = 7 * 86400u;
    dhcp.lease = (lease < 16 ? 16 : lease) * (1000 / TICK_MS);
    dhcp.state = DHCP_BOUND;
    dhcp.ticks = 0;
    if (!fresh)
        return;
    arp_request(net.ip);
    if (ses.state == SESSION_DHCP) {
        if (ses.server || ses.host[0])
            route_to(ses.server ? ses.server : ses.dns);
        else
            ses.state = SESSION_IDLE;
    }
}

/* A reply to our transaction; caller holds the lock (the receive DPC). */
static void dhcp_rx(const u8 *p, u32 n)
{
    u32 off = 240, type = 0, mask = 0, router = 0, dns = 0, lease = 0, server = 0, i;

    if (n < 240 || p[0] != 2 || !dhcp.state || get32(p + 4) != dhcp.xid ||
        get32(p + 236) != 0x63825363u)
        return;
    for (i = 0; i < 6; i++)
        if (p[28 + i] != mac[i])
            return;
    while (off < n && p[off] != 255) {
        const u8 *v = p + off + 2;
        u32 code = p[off], len;

        if (!code) {
            off++;
            continue;
        }
        if (off + 2 > n || off + 2 + (len = p[off + 1]) > n)
            return;
        if (code == 53 && len >= 1)
            type = v[0];
        else if (len >= 4 && code == 1)
            mask = get32(v);
        else if (len >= 4 && code == 3)
            router = get32(v);
        else if (len >= 4 && code == 6)
            dns = get32(v);
        else if (len >= 4 && code == 51)
            lease = get32(v);
        else if (len >= 4 && code == 54)
            server = get32(v);
        off += 2 + len;
    }
    if (type == 2 && dhcp.state == DHCP_DISCOVER && get32(p + 16)) {
        dhcp.offers++;
        dhcp.offered = get32(p + 16);
        dhcp.server = server ? server : get32(p + 20);
        dhcp.state = DHCP_REQUEST;
        dhcp.ticks = dhcp.tries = 0;
        dhcp_send(3);
        dhcp.requests++;
        return;
    }
    if (dhcp.state == DHCP_DISCOVER || (server && dhcp.server && server != dhcp.server))
        return;
    if (type == 5 && get32(p + 16)) {
        dhcp.acks++;
        dhcp_bind(get32(p + 16), mask, router, dns, lease);
    } else if (type == 6) {
        dhcp.naks++;
        dhcp_lost();
    }
}

/* Every TICK_MS; caller holds the lock. Discovery every 2 s, a request every second (four, then
 * discovery again), and once bound a renewal every 4 s from half the lease on. */
static void dhcp_tick(void)
{
    dhcp.ticks++;
    if (dhcp.state == DHCP_DISCOVER && dhcp.ticks >= 8) {
        dhcp.ticks = 0;
        dhcp_send(1);
        dhcp.discovers++;
    } else if (dhcp.state == DHCP_REQUEST && dhcp.ticks >= 4) {
        dhcp.ticks = 0;
        if (++dhcp.tries >= 4) {
            dhcp_discover();
        } else {
            dhcp_send(3);
            dhcp.requests++;
        }
    } else if (dhcp.state == DHCP_BOUND) {
        if (dhcp.ticks >= dhcp.lease) {
            dhcp.expiries++;
            dhcp_lost();
        } else if (dhcp.ticks >= dhcp.lease / 2 && (dhcp.ticks - dhcp.lease / 2) % 16 == 0) {
            dhcp_send(3);
            dhcp.renewals++;
        }
    }
}

/* Every TICK_MS from the timer DPC; caller holds the lock. */
static void session_tick(void)
{
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
    /* Joining waits for a clock to offer, which the game has only once a game is loaded. HELLO
     * goes inside the handshake's last message. */
    if (ses.state == SESSION_HELLO && ses.ticks >= HELLO_TICKS &&
        (game_clock.local_valid || lobby)) {
        ses.ticks = 0;
        handshake_tick();
    } else if (ses.state == SESSION_JOINED) {
        if (rel.out_first != rel.out_next)
            events_send(1);
        bulk_tick();
        up_pump();
        if (ses.quiet >= TIMEOUT_TICKS) {
            ses.timeouts++;
            ses.state = SESSION_HELLO;
            ses.ticks = HELLO_TICKS - 1;
            sec.keyed = 0;
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
    entropy_add();
    if (net.up && dhcp.state)
        dhcp_tick();
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
    if (ihl < 20 || total > len - 14 || total < ihl + 8 + 8)
        return;
    udp = ip + ihl;
    if (get16(udp) == DHCP_SERVER && get16(udp + 2) == DHCP_CLIENT) {
        dhcp_rx(udp + 8, total - ihl - 8);
        return;
    }
    if (dst != net.ip && dst != 0xFFFFFFFFu)
        return;
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
    u32 i, found, filled = 0;

    for (i = 0; i < RX_RING; i++)
        filled += !(rx_ring[i].flags & RX_AVAIL);
    if (filled > net.rx_peak)
        net.rx_peak = filled;
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
    entropy_add();
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
    entropy_add();
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
        worker_stop();
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
        worker_start();
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
            ring += (rx_ring[i].flags & RX_AVAIL) != 0;
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
    tes3x_log_hex3("net.irq", net.irqs, net.dpcs, net.rx_peak);
    tes3x_log_hex3("net.answered", net.arp, net.echo, 0);
    tes3x_log_hex3("net.tx", net.tx, net.tx_full, net.tx_errors);
    if (dhcp.state) {
        tes3x_log_hex3("net.dhcp_stat", dhcp.discovers, dhcp.offers, dhcp.acks);
        tes3x_log_hex3("net.dhcp_lease", dhcp.state, dhcp.ticks / (1000 / TICK_MS),
                       dhcp.lease / (1000 / TICK_MS));
        tes3x_log_hex3("net.dhcp_lost", dhcp.naks, dhcp.expiries, dhcp.renewals);
    }
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
        ghost_heading_stat();
        tes3x_log_hex3("net.equipment_stat", equip_sent, equip_received, equip_applied);
        tes3x_log_hex3("net.stances", stance_changes, stance_refused, first_person_states);
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
#define MOBILE_MAGICKA 0x2C8
#define MOBILE_FATIGUE 0x2E0
#define MOBILE_FIGHT 0x350 /* int, as SetFight sets it */

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

/* With the world running under a menu the player's controls would read the pad the menu is
 * using, so a press that equips an item also swings the weapon. While a menu is open the player's
 * controller runs with DisablePlayerControls' byte set, for that call only: the menu button's
 * toggle refuses to act while it is set. Nor is the activation target looked for, whose name
 * would stay up over the menu. */
#define PLAYER_CONTROLS_OFF 0x5B0 /* MobilePlayer, the byte DisablePlayerControls sets */
#define CONTROLLER_MOBILE 0x38

typedef void(__attribute__((thiscall)) *fn_game_call)(void *game);
typedef void(__attribute__((thiscall)) *fn_controller_update)(void *controller, u32 time);

static int redirect_calls(const u32 *sites, u32 n, u32 original, const void *hook);
static const u32 target_sites[] = TES3X_NET_ACTIVATION_TARGET_SITES;
static u32 target_hooked, control_hooked, controls_held;

static int menu_open(void)
{
    const u8 *world = *(const u8 **)TES3X_NET_WORLD;

    return gates_open && plausible(world) && world[WORLD_MENU_MODE];
}

static void __attribute__((thiscall)) activation_target_hook(void *game)
{
    if (!menu_open())
        ((fn_game_call)TES3X_NET_ACTIVATION_TARGET)(game);
}

/* The player's own activation (the pad's A) does nothing on a ghost. Its noPickUp script cannot
 * swallow it: a ghost moved in from its never-loaded cell has no script variables attachment,
 * without which activation takes the default path and opens the dialogue. */
typedef void(__attribute__((thiscall)) *fn_activate)(void *target, void *activator, int a2);
static int is_ghost(const u8 *ref);
static const u32 activate_sites[] = TES3X_NET_PLAYER_ACTIVATE_SITES;
static u32 ghost_activations;

static void __attribute__((thiscall)) player_activate_hook(u8 *target, void *activator, int a2)
{
    if (plausible(target) && is_ghost(target)) {
        ghost_activations++;
        return;
    }
    ((fn_activate)TES3X_NET_REF_ACTIVATE)(target, activator, a2);
}

static void __attribute__((thiscall)) player_control_hook(u8 *controller, u32 time)
{
    u8 *mobile = *(u8 **)(controller + CONTROLLER_MOBILE), saved;

    if (!menu_open() || !plausible(mobile)) {
        ((fn_controller_update)TES3X_NET_PLAYER_CONTROL)(controller, time);
        return;
    }
    saved = mobile[PLAYER_CONTROLS_OFF];
    mobile[PLAYER_CONTROLS_OFF] = 1;
    ((fn_controller_update)TES3X_NET_PLAYER_CONTROL)(controller, time);
    mobile[PLAYER_CONTROLS_OFF] = saved;
    controls_held++;
}

static void control_hook_install(void)
{
    u32 *slot = (u32 *)TES3X_NET_PLAYER_CONTROL_SLOT, cr0, flags;

    control_hooked = 1;
    if (*slot != TES3X_NET_PLAYER_CONTROL) {
        tes3x_log_hex3("net.control_slot_unexpected", (u32)slot, *slot, 0);
        return;
    }
    flags = lock();
    __asm__ volatile("movl %%cr0, %0" : "=r"(cr0));
    __asm__ volatile("movl %0, %%cr0" : : "r"(cr0 & ~CR0_WP) : "memory");
    *slot = (u32)player_control_hook;
    __asm__ volatile("movl %0, %%cr0" : : "r"(cr0) : "memory");
    unlock(flags);
    control_hooked = 2;
}

/* While joined, the world runs under menus as it does for the other players, and nobody rests.
 * Only in the world: the main menu at boot has no player to simulate. */
static void menu_frame(int in_world)
{
    u32 joined = ses.state == SESSION_JOINED && in_world;

    if (!target_hooked) {
        target_hooked = 1;
        if (redirect_calls(target_sites, sizeof(target_sites) / sizeof(target_sites[0]),
                           TES3X_NET_ACTIVATION_TARGET, (const void *)activation_target_hook))
            target_hooked = 2;
    }
    if (!control_hooked) {
        control_hook_install();
        if (redirect_calls(activate_sites, sizeof(activate_sites) / sizeof(activate_sites[0]),
                           TES3X_NET_REF_ACTIVATE, (const void *)player_activate_hook))
            control_hooked |= 4;
    }
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
    tes3x_log_hex3("net.menu_controls", controls_held, control_hooked, target_hooked);
    tes3x_log_hex3("net.ghost_activations", ghost_activations, 0, 0);
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

/* up A.B.C.D[/BITS]|dhcp [SERVER[:PORT] [GATEWAY [DNS]]], where SERVER is an address or a name;
 * with dhcp a GATEWAY or DNS given here overrides the lease's. With the NIC already up only the
 * session is opened again, to the server given. */
static void command_up(const char *text)
{
    u32 ip = 0, bits = 24, server = 0, port = PORT, gateway = 0, dns = 0, flags, named = 0;
    u32 pinned = 0, again = net.up;
    char host[HOST_NAME];
    const char *after, *name = 0;
    u8 fingerprint[TRUST_FINGERPRINT];
    int lease = 0;

    host[0] = 0;
    if ((after = word(skip(text), "dhcp"))) {
        lease = 1;
        bits = 0;
        text = after;
    } else if (!(text = address(skip(text), &ip))) {
        goto usage;
    }
    if (!lease && *text == '/' &&
        (!(text = number(text + 1, &bits)) || bits < 1 || bits > 30))
        goto usage;
    text = skip(text);
    if (*text) {
        name = text;
        if ((after = address(text, &server)) &&
            (*after == ':' || *after == '#' || *after == ' ' || !*after))
            text = after;
        else if (!(text = host_name(text, host)))
            goto usage;
        if (*text == ':' && (!(text = number(text + 1, &port)) || !port || port > 65535))
            goto usage;
        named = (u32)(text - name);
        if (*text == '#') { /* the server key's fingerprint, to pin before first contact */
            if (!hex_read(text + 1, fingerprint, TRUST_FINGERPRINT))
                goto usage;
            pinned = 1;
            text += 1 + 2 * TRUST_FINGERPRINT;
        }
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
        if (host[0] && !lease && !dns && !(dns = gateway)) {
            tes3x_log("net.no_dns", 0);
            goto usage;
        }
    }
    if (!again) {
        if (!nic_start(ip, 1))
            return;
        net.mask = lease ? 0 : 0xFFFFFFFFu << (32 - bits);
        if (!lease)
            announce();
    }
    tes3x_log_hex3(again ? "net.reopen" : "net.up", ip, bits, server);
    if (server || host[0]) {
        u32 *w = (u32 *)&ses;
        u32 n;

        flags = lock();
        if (again && ses.state == SESSION_JOINED)
            session_send(T3MP_BYE, 0, 0);
        if (again && lease) { /* what the lease gave */
            gateway = gateway ? gateway : ses.gateway;
            dns = dns ? dns : ses.dns;
        }
        for (n = 0; n < sizeof(ses) / 4; n++)
            w[n] = 0;
        ses.server = server;
        ses.port = port;
        ses.gateway = gateway;
        ses.dns = dns;
        copy((u8 *)ses.host, (const u8 *)host, HOST_NAME);
        handshake_reset();
        sec.keyed = 0;
        unlock(flags);
        trust_configure(name, named, pinned ? fingerprint : 0);
        load_order();
        flags = lock();
        if (lease && !(again && dhcp.state == DHCP_BOUND))
            ses.state = SESSION_DHCP;
        else
            route_to(server ? server : dns);
        unlock(flags);
        if (!lease && !again && (ses.hop ^ ip) & net.mask)
            tes3x_log("net.no_gateway", ses.hop);
        if (host[0])
            log_text("net.resolving", host);
        tes3x_log_hex3("net.session_start", server ? server : dns, port, ses.hop);
    }
    if (again)
        return;
    flags = lock();
    dhcp.state = DHCP_IDLE;
    if (lease) {
        u32 *w = (u32 *)&dhcp, n;

        for (n = 0; n < sizeof(dhcp) / 4; n++)
            w[n] = 0;
        dhcp.keep_gateway = gateway != 0;
        dhcp.keep_dns = dns != 0;
        ses.gateway = gateway;
        ses.dns = dns;
        dhcp_discover();
    }
    unlock(flags);
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

/* The ini reader needs the game drive, which is not mounted at process entry. A launch the main
 * menu's Join led to joins its server even without NetAddress (DHCP), and in place of NetServer. */
static void autostart(void)
{
    char line[24 + JOIN_NAME + 2 * 24];
    u32 n, server, i;

    net_password_n = ini_text("NetPassword", net_password, sizeof(net_password));
    if (!(n = ini_text("NetAddress", line, 24))) {
        if (!join_server[0])
            return;
        copy((u8 *)line, (const u8 *)"dhcp", 4);
        n = 4;
    }
    line[n++] = ' ';
    for (i = 0; join_server[i]; i++)
        line[n + i] = join_server[i];
    if (!(server = i))
        server = ini_text("NetServer", line + n, JOIN_NAME);
    if (server) {
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

typedef void *(__attribute__((thiscall)) *fn_ref_part)(const void *ref);

/* A reference's attachments are a list of {kind, next, data...}; its mobile is kind 8. */
#define REF_ATTACHMENTS 0x44
#define ATTACHMENT_MOBILE 8u

static u8 *ref_mobile(const u8 *ref)
{
    const u8 *a = *(const u8 *const *)(ref + REF_ATTACHMENTS);
    u32 guard;

    for (guard = 0; plausible(a) && guard < 32; a = *(const u8 *const *)(a + 4), guard++)
        if (*(const u32 *)a == ATTACHMENT_MOBILE)
            return *(u8 *const *)(a + 8);
    return 0;
}

/* The drawn weapon and readied spell. A simulated actor changes them from its animation's text
 * keys, which a mirrored animation skips, so they are changed here with the same calls: the
 * mobile's ready-weapon virtual (at "Equip Attach": sets the bit, attaches the mesh),
 * unreadyWeapon (at "Unequip Detach"), and for a spell the bit and the hands update. */
#define MOBILE_WEAPON_DRAWN 0x2000u
#define MOBILE_SPELL_READIED 0x4000u
#define MOBILE_READY_WEAPON 0xF0 /* vtable offset */

typedef void(__attribute__((thiscall)) *fn_mobile_call)(void *mobile);

static u32 stance_of(const u8 *mobile)
{
    u32 f = plausible(mobile) ? *(const u32 *)(mobile + MOBILE_FLAGS) : 0;

    return (f & MOBILE_WEAPON_DRAWN ? STANCE_WEAPON : 0) |
           (f & MOBILE_SPELL_READIED ? STANCE_SPELL : 0);
}

static void stance_apply(u8 *ref, u32 stance)
{
    u8 *mobile = ref_mobile(ref);
    u32 *flags, have;

    if (!plausible(mobile))
        return;
    flags = (u32 *)(mobile + MOBILE_FLAGS);
    stance &= STANCE_WEAPON | STANCE_SPELL;
    have = stance_of(mobile);
    if (have == stance)
        return;
    if ((have & STANCE_WEAPON) && !(stance & STANCE_WEAPON))
        ((fn_mobile_call)TES3X_NET_UNREADY_WEAPON)(mobile);
    if (have & STANCE_SPELL && !(stance & STANCE_SPELL)) {
        *flags &= ~MOBILE_SPELL_READIED;
        ((fn_mobile_call)TES3X_NET_MOBILE_HANDS)(mobile);
    }
    if (stance & STANCE_WEAPON && !(*flags & MOBILE_WEAPON_DRAWN))
        ((fn_mobile_call)(*(void *const *const *)mobile)[MOBILE_READY_WEAPON / 4])(mobile);
    if (stance & STANCE_SPELL && !(*flags & MOBILE_SPELL_READIED)) {
        *flags |= MOBILE_SPELL_READIED;
        ((fn_mobile_call)TES3X_NET_MOBILE_HANDS)(mobile);
    }
    stance_changes++;
    if (stance_of(mobile) != stance)
        stance_refused++;
}

/* A reference's AnimationData is its attachment of kind 0. For each layer (lower body, upper
 * body, arm) it holds the group +0x38, the key reached +0x3C, the loops left +0x48 and the time
 * in the group +0x58. Sent as the three groups, a pad byte, the three keys, a pad byte, and the
 * three times. */
#define ANIM_GROUP 0x38
#define ANIM_KEY 0x3C
#define ANIM_LOOPS 0x48
#define ANIM_TIMING 0x58
#define ANIM_LAYERS 3u

static u8 *ref_animation(const u8 *ref)
{
    return (u8 *)((fn_ref_part)TES3X_NET_REF_ANIMATION)(ref);
}

static void anim_read(const u8 *a, u8 *out)
{
    u32 l;

    for (l = 0; l < ANIM_BYTES; l++)
        out[l] = 0;
    for (l = 0; l < ANIM_LAYERS; l++) {
        out[l] = 0xFF;
        if (!plausible(a))
            continue;
        out[l] = a[ANIM_GROUP + l];
        out[4 + l] = (u8) * (const u32 *)(a + ANIM_KEY + 4 * l);
        copy(out + 8 + 4 * l, a + ANIM_TIMING + 4 * l, 4);
    }
}

static void anim_capture(const u8 *ref, u8 *out)
{
    anim_read(ref_animation(ref), out);
}

/* In first person the engine runs only the first-person reference's AnimationData (the one its
 * controller holds) and leaves the third-person one still. The player's groups and keys are read
 * from the one it runs, and each time moved to the same point between the same two keys of the
 * group on the third-person model, whose timeline the ghosts share. */
#define MOBILE_ANIM_CONTROLLER 0x244
#define CONTROLLER_ANIMATION 0x3C
#define ANIM_GROUP_OBJECTS 0x68
#define ANIM_GROUP_COUNT 150u
#define GROUP_KEY_COUNT 0x14
#define GROUP_KEY_TIMES 0x1C

static const float *anim_keys(const u8 *a, u32 g, u32 *n)
{
    const u8 *group;
    const float *keys;

    if (g >= ANIM_GROUP_COUNT ||
        !plausible(group = *(const u8 *const *)(a + ANIM_GROUP_OBJECTS + 4 * g)) ||
        !(*n = *(const u32 *)(group + GROUP_KEY_COUNT)) ||
        !plausible(keys = *(const float *const *)(group + GROUP_KEY_TIMES)))
        return 0;
    return keys;
}

static int anim_retime(const u8 *from, const u8 *to, u32 g, float *t)
{
    const float *kf, *kt;
    u32 nf, nt, i = 0, j = 0;
    float span, frac;

    if (!(kf = anim_keys(from, g, &nf)) || !(kt = anim_keys(to, g, &nt)))
        return 0;
    if (nf == nt) {
        while (i + 2 < nf && *t >= kf[i + 1])
            i++;
        j = i + 1 < nf ? i + 1 : i;
    } else {
        j = nf - 1;
    }
    span = kf[j] - kf[i];
    frac = span > 0 ? (*t - kf[i]) / span : 0;
    frac = frac < 0 ? 0 : frac > 1 ? 1 : frac;
    if (nf != nt)
        j = nt - 1;
    *t = kt[i] + (kt[j] - kt[i]) * frac;
    return 1;
}

/* The first-person model has no swim groups, and the controller picks none for it. The group the
 * third-person model would play is taken from the movement flags (mobile +0x8: forward 1, back 2,
 * left 4, right 8, run 0x200, swim 0x800), looped on its whole timeline. */
#define MOBILE_MOVEMENT 0x8
#define MOVE_RUN 0x200u
#define MOVE_SWIM 0x800u
#define GROUP_IDLE_SWIM 13u
#define GROUP_SWIM_WALK 43u /* forward, back, left, right; running 4 further */
#define GROUP_MOVE_FIRST 53u /* WalkForward to SneakRight */
#define GROUP_MOVE_LAST 66u

static int swim_capture(const u8 *mobile, const u8 *own, u8 *out)
{
    u32 f = *(const u16 *)(mobile + MOBILE_MOVEMENT), g, n, l, span, at;
    const float *keys;
    float t;

    if (!(f & MOVE_SWIM))
        return 0;
    g = f & 1 ? 0 : f & 2 ? 1 : f & 4 ? 2 : f & 8 ? 3 : 4;
    g = g == 4 ? GROUP_IDLE_SWIM : GROUP_SWIM_WALK + (f & MOVE_RUN ? 4 : 0) + g;
    if (!(keys = anim_keys(own, g, &n)))
        return 0;
    span = (u32)((keys[n - 1] - keys[0]) * 1000000.0f);
    at = span ? now_us() % span : 0;
    t = keys[0] + (float)at / 1000000.0f;
    for (l = 0; l < ANIM_LAYERS; l++) {
        if (out[l] && (out[l] < GROUP_MOVE_FIRST || out[l] > GROUP_MOVE_LAST))
            continue;
        out[l] = (u8)g;
        out[4 + l] = 3;
        copy(out + 8 + 4 * l, (const u8 *)&t, 4);
    }
    return 1;
}

static void player_anim_capture(const u8 *ref, u8 *out)
{
    const u8 *own = ref_animation(ref), *mobile = ref_mobile(ref), *controller, *run;
    float t;
    u32 l;

    if (!plausible(own) || !plausible(mobile) ||
        !plausible(controller = *(const u8 *const *)(mobile + MOBILE_ANIM_CONTROLLER)) ||
        !plausible(run = *(const u8 *const *)(controller + CONTROLLER_ANIMATION)) || run == own) {
        anim_capture(ref, out);
        return;
    }
    anim_read(run, out);
    first_person_states++;
    if (swim_capture(mobile, own, out))
        return;
    for (l = 0; l < ANIM_LAYERS; l++) {
        if (out[l] == 0xFF)
            continue;
        copy((u8 *)&t, out + 8 + 4 * l, 4);
        if (anim_retime(run, own, out[l], &t))
            copy(out + 8 + 4 * l, (const u8 *)&t, 4);
        else
            out[l] = 0xFF;
    }
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
    put32le(state, flags | stance_of(ref_mobile(ref)));
    copy(state + 4, ref + 0x38, 12); /* position */
    copy(state + 16, ref + 0x34, 4); /* orientation z */
    player_anim_capture(ref, state + STATE_ANIM);
}

/* Ghosts: each peer slot drives one persistent NPC of the ghost plugin (tes3x_net.py plugin),
 * moved into a cell through the engine's script compiler, as the console does, and within it by
 * writing its position. Each is drawn GHOST_DELAY_US in the past, between the two states around
 * that moment, so jitter and a lost state do not show. */
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
#define REF_NODE 0x10
#define REF_ORIENTATION 0x2C
#define REF_POSITION 0x38
#define NODE_ROTATION 0x2C /* NiAVObject's rotation pointer, then its translation */
#define NODE_TRANSLATE 0x30
#define PI 3.14159265f
#define GROUP_IDLE 0u
#define GROUP_NONE 0xFFu
#define GROUP_LOOPS 100000 /* LoopGroup's count: until the next group */
#define MOBILE_SCRIPTED 0x10000000u /* PlayGroup's mark: the group is not the AI's to change */

typedef int(__attribute__((thiscall)) *fn_compile_run)(void *self, void *scratch,
                                                       const char *text, int a2, int ref,
                                                       int a4, int a5, int a6);
typedef u8 *(__attribute__((thiscall)) *fn_find_reference)(void *records, const char *id);
typedef float *(__attribute__((thiscall)) *fn_ref_rotation)(void *ref, float *matrix, int a1);
typedef void(__attribute__((thiscall)) *fn_node_set_rotation)(void *slot, const float *matrix);
typedef void(__attribute__((thiscall)) *fn_node_update)(void *node, float time, int a1, int a2);
typedef u8(__attribute__((thiscall)) *fn_has_group)(void *animation, int group);
typedef void(__attribute__((thiscall)) *fn_play_group)(void *animation, int group, int layer,
                                                       int flags, int loops);

struct pose {
    u32 flags;
    float x, y, z, heading;
    u8 cell[CELL_NAME];
    u8 anim[ANIM_BYTES];
};

/* A received position, heading and animation and when it arrived. */
struct timed {
    u32 time, flags;
    float p[4];
    u8 anim[ANIM_BYTES];
};

static struct {
    u32 client, placed, flags;
    int gx, gy;
    float x, y, z, heading;
    u8 cell[CELL_NAME];
    u8 *ref;
    u32 look;  /* the equipment generation it wears */
    u32 armed; /* its health is set to GHOST_HEALTH: a drop from there is a hit */
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
    ghosts[i].placed = ghosts[i].armed = 0;
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

/* SetPos and SetAngle z as their handlers do them: the position on the reference and its node,
 * the heading in the reference's orientation attachment and as the node's rotation, and a node
 * update unless the reference has animation data (attachment kind 0), as actors do. NPCs build
 * that rotation from the reference's own orientation rather than the attachment's, so the heading
 * goes there too. */
static void place_ref(u8 *ref, const float *xyzh)
{
    u8 *node = *(u8 **)(ref + REF_NODE);
    float matrix[12], heading = wrap_angle(xyzh[3]);

    if (heading < 0)
        heading += 2 * PI;
    copy(ref + REF_POSITION, (const u8 *)xyzh, 12);
    *(float *)(ref + REF_ORIENTATION + 8) = heading;
    ((float *)((fn_ref_part)TES3X_NET_REF_ORIENTATION)(ref))[2] = heading;
    if (!plausible(node))
        return;
    copy(node + NODE_TRANSLATE, (const u8 *)xyzh, 12);
    ((fn_node_set_rotation)TES3X_NET_NODE_SET_ROTATION)(
        node + NODE_ROTATION, ((fn_ref_rotation)TES3X_NET_REF_ROTATION)(ref, matrix, 1));
    if (!((fn_ref_part)TES3X_NET_REF_ANIMATION)(ref))
        ((fn_node_update)TES3X_NET_NODE_UPDATE)(node, 0.0f, 0, 1);
}

/* Ghosts and followed actors are placed, not simulated, so the engine never animates them by
 * itself; they mirror the animation their source sends. When a layer's group changes it is
 * played as LoopGroup plays it and the mobile marked as PlayGroup marks it; every frame the key
 * and the time are set between the two states the actor is drawn between (frac of the way), and
 * the engine's update poses the actor there. */
static void anim_apply(u8 *ref, const u8 *older, const u8 *newer, float frac)
{
    u8 *a = ref_animation(ref), *mobile = ref_mobile(ref);
    u32 l, g, keys;
    float from, to;

    if (!plausible(a))
        return;
    if (plausible(mobile))
        *(u32 *)(mobile + MOBILE_FLAGS) |= MOBILE_SCRIPTED;
    for (l = 0; l < ANIM_LAYERS; l++) {
        if ((g = newer[l]) == GROUP_NONE)
            continue;
        if (g >= ANIM_GROUP_COUNT || !float_within(newer + 8 + 4 * l, 1, ANIM_TIME_LIMIT)) {
            refused_anims++;
            continue;
        }
        /* Attack and cast groups take the key to start from where others take 1, at once. */
        if (a[ANIM_GROUP + l] != g) {
            if (!((fn_has_group)TES3X_NET_ANIM_HAS_GROUP)(a, (int)g))
                continue;
            ((fn_play_group)TES3X_NET_ANIM_PLAY_GROUP)(a, (int)g, (int)l,
                                                        newer[4 + l] >= 3 ? newer[4 + l] : 1,
                                                        GROUP_LOOPS);
            if (a[ANIM_GROUP + l] != g)
                ((fn_play_group)TES3X_NET_ANIM_PLAY_GROUP)(a, (int)g, (int)l,
                                                            newer[4 + l] >= 3 ? 1 : 3,
                                                            GROUP_LOOPS);
            if (a[ANIM_GROUP + l] != g)
                continue;
        }
        if (!anim_keys(a, g, &keys) || newer[4 + l] >= keys) {
            refused_anims++;
            continue;
        }
        copy((u8 *)&to, newer + 8 + 4 * l, 4);
        if (older[l] == g && older[4 + l] == newer[4 + l]) {
            copy((u8 *)&from, older + 8 + 4 * l, 4);
            if (from <= to)
                to = from + (to - from) * frac;
        }
        *(u32 *)(a + ANIM_KEY + 4 * l) = newer[4 + l];
        *(u32 *)(a + ANIM_LOOPS + 4 * l) = GROUP_LOOPS;
        *(float *)(a + ANIM_TIMING + 4 * l) = to;
    }
}

/* Idle on every layer, as PlayGroup Idle: the actor's own animation takes over again. */
static void anim_release(u8 *ref)
{
    u8 *a = ref_animation(ref), *mobile = ref_mobile(ref);
    int l;

    if (plausible(mobile))
        *(u32 *)(mobile + MOBILE_FLAGS) &= ~MOBILE_SCRIPTED;
    if (plausible(a))
        for (l = 0; l < (int)ANIM_LAYERS; l++)
            ((fn_play_group)TES3X_NET_ANIM_PLAY_GROUP)(a, GROUP_IDLE, l, 1, -1);
}

/* Whether a placed reference stands more than a unit or 0.01 rad off xyzh. */
static int off_pose(const u8 *ref, const float *xyzh)
{
    const float *at = (const float *)(ref + REF_POSITION);
    float turn = wrap_angle(xyzh[3] - *(const float *)(ref + REF_ORIENTATION + 8));
    u32 i;

    for (i = 0; i < 3; i++)
        if (at[i] - xyzh[i] > 1 || xyzh[i] - at[i] > 1)
            return 1;
    return turn > 0.01f || turn < -0.01f;
}

/* The pose delay microseconds ago from n samples, oldest first: between the two around that
 * moment, carried on past the newest for up to GHOST_EXTRAPOLATE_US, or held at one sample when
 * there is no pair or the pair lies too far apart to be a walk. Returns the index of the newer
 * sample of the pair, or of the sample held; frac is how far between the pair, at most 1. */
static u32 track_pose(const struct timed *s, u32 n, u32 delay, float *out, float *frac)
{
    int target = (int)(now_us() - delay), from = -1, span, into;
    u32 i;
    float t, dx, dy;

    for (i = 0; i < n; i++)
        if ((int)(s[i].time - (u32)target) <= 0)
            from = (int)i;
    *frac = 1;
    if (from < 0 || n == 1) {
        i = from < 0 ? 0 : (u32)from;
        copy((u8 *)out, (const u8 *)s[i].p, 16);
        return i;
    }
    i = (u32)from + 1 < n ? (u32)from : n - 2;
    copy((u8 *)out, (const u8 *)s[i + 1].p, 16);
    span = (int)(s[i + 1].time - s[i].time);
    dx = s[i + 1].p[0] - s[i].p[0];
    dy = s[i + 1].p[1] - s[i].p[1];
    if (span <= 0 || dx * dx + dy * dy > GHOST_SNAP * GHOST_SNAP)
        return i + 1;
    into = target - (int)s[i].time;
    if (into > span + GHOST_EXTRAPOLATE_US)
        into = span + GHOST_EXTRAPOLATE_US;
    t = (float)into / (float)span;
    *frac = t < 0 ? 0 : t > 1 ? 1 : t;
    out[0] = s[i].p[0] + dx * t;
    out[1] = s[i].p[1] + dy * t;
    out[2] = s[i].p[2] + (s[i + 1].p[2] - s[i].p[2]) * t;
    out[3] = s[i].p[3] + wrap_angle(s[i + 1].p[3] - s[i].p[3]) * (t > 1 ? 1 : t);
    return i + 1;
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

/* tes3x_ghostN's reference for slot i, looked up once per launch. */
static u8 *ghost_ref(u32 i)
{
    const u8 *handler = *(const u8 **)TES3X_NET_DATA_HANDLER;
    void *records;
    char id[16];

    if (!ghosts[i].ref && plausible(handler) && plausible(records = *(void **)handler)) {
        *put_int(put_text(id, "tes3x_ghost"), (int)i + 1) = 0;
        ghosts[i].ref = ((fn_find_reference)TES3X_NET_FIND_REFERENCE)(records, id);
        if (ghosts[i].ref)
            tes3x_log_hex3("net.ghost_ref", i + 1, (u32)ghosts[i].ref, 0);
    }
    if (plausible(ghosts[i].ref) && is_ghost(ghosts[i].ref))
        return ghosts[i].ref;
    ghost_failures++;
    return 0;
}

static void read_pose(const u8 *state, struct pose *p)
{
    p->flags = get32le(state);
    copy((u8 *)&p->x, state + 4, 16);
    copy(p->cell, state + 20, CELL_NAME);
    p->cell[CELL_NAME - 1] = 0;
    copy(p->anim, state + STATE_ANIM, ANIM_BYTES);
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

/* The slot's pose GHOST_DELAY_US ago, from a copy of its states, with the older state's
 * animation and how far past it; 0 if it has none. */
static int ghost_pose(u32 slot, struct pose *out, u8 *older, float *frac)
{
    static struct {
        u32 time;
        u8 state[STATE_BYTES];
    } ring[SAMPLES];
    struct pose poses[SAMPLES];
    struct timed track[SAMPLES];
    float at[4];
    u32 head, count, flags, i, k;

    flags = lock();
    head = peers[slot].head;
    count = peers[slot].count;
    copy((u8 *)ring, (const u8 *)peers[slot].samples, sizeof(ring));
    unlock(flags);
    if (!count)
        return 0;
    for (i = 0; i < count; i++) {
        k = (head + SAMPLES - count + i) % SAMPLES;
        read_pose(ring[k].state, &poses[i]);
        track[i].time = ring[k].time;
        copy((u8 *)track[i].p, (const u8 *)&poses[i].x, 16);
    }
    i = track_pose(track, count, GHOST_DELAY_US, at, frac);
    *out = poses[i];
    copy(older, poses[i ? i - 1 : i].anim, ANIM_BYTES);
    /* Across a cell change the newer state stands alone. */
    if (i && same_place(&poses[i - 1], &poses[i]) && (poses[i - 1].flags & STATE_IN_WORLD))
        copy((u8 *)&out->x, (const u8 *)at, 16);
    else
        *frac = 1;
    return 1;
}

static const char *interior_name(const u8 *cut);

static void ghost_place(u32 i, const struct pose *p)
{
    char line[160];
    char *q = put_xyz(put_text(put_ghost(line, i), "PositionCell "), &p->x);

    q = put_text(q, " 0 \"");
    q = put_text(q, p->flags & STATE_INTERIOR ? interior_name(p->cell) : GHOST_EXTERIOR);
    q = put_text(q, "\"");
    *q = 0;
    run_script(line);
    ghosts[i].placed = 1;
    ghosts[i].armed = 0;
    ghosts[i].flags = p->flags;
    ghosts[i].gx = grid(p->x);
    ghosts[i].gy = grid(p->y);
    copy(ghosts[i].cell, p->cell, CELL_NAME);
    ghosts[i].x = p->x;
    ghosts[i].y = p->y;
    ghosts[i].z = p->z;
    ghosts[i].heading = 1000; /* not an angle: the next frame turns it */
    ghost_places++;
    log_text("net.ghost_cell", p->flags & STATE_INTERIOR ? (const char *)p->cell : "(exterior)");
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

/* Equipment. Each client sends the ids of what its player has equipped whenever they change, as
 * EQUIPMENT events: part, parts, then ids each ending in a zero. The server keeps every client's
 * latest set and replays it to whoever joins. A ghost is made to wear its peer's set by comparing
 * it with what the ghost has equipped: RemoveItem what it should not wear, Equip what it lacks. */
#define EVENT_EQUIPMENT 7u
#define EVENT_WEATHER 8u /* the weather section */
#define EVENT_PLAYER_HIT 9u /* attacker refid (0: a player), victim client, health, fatigue */
#define EQUIP_ITEMS 24u
#define EQUIP_ID 32u
#define EQUIP_PERIOD_US 500000u
#define ACTOR_EQUIPMENT 0x58 /* list: head +0x8; node: next +0x4, stack +0x8, object first */
#define OBJECT_GET_ID 0x20   /* vtable offset */

typedef const char *(__attribute__((thiscall)) *fn_object_id)(const void *object);

static struct {
    u32 client, used, generation, count, staged, next_part;
    char ids[EQUIP_ITEMS][EQUIP_ID], stage[EQUIP_ITEMS][EQUIP_ID];
} looks[PEERS];
static u32 look_clock;

static u32 equipment_ids(const u8 *ref, char ids[][EQUIP_ID])
{
    const u8 *actor = *(const u8 *const *)(ref + 0x28), *node, *stack, *object;
    void *const *vtable;
    const char *id;
    u32 n = 0, guard, k;

    if (!plausible(actor))
        return 0;
    node = *(const u8 *const *)(actor + ACTOR_EQUIPMENT + 8);
    for (guard = 0; plausible(node) && guard < 64 && n < EQUIP_ITEMS;
         node = *(const u8 *const *)(node + 4), guard++) {
        if (!plausible(stack = *(const u8 *const *)(node + 8)) ||
            !plausible(object = *(const u8 *const *)stack) ||
            !plausible(vtable = *(void *const *const *)object))
            continue;
        id = ((fn_object_id)vtable[OBJECT_GET_ID / 4])(object);
        if (!mapped(id) || !*id)
            continue;
        for (k = 0; id[k] && k < EQUIP_ID - 1; k++)
            ids[n][k] = id[k];
        ids[n++][k] = 0;
    }
    return n;
}

static int has_id(char ids[][EQUIP_ID], u32 n, const char *id)
{
    u32 i, k;

    for (i = 0; i < n; i++) {
        for (k = 0; ids[i][k] && ids[i][k] == id[k]; k++)
            ;
        if (ids[i][k] == id[k])
            return 1;
    }
    return 0;
}

/* Game thread, in the world: sends the player's set when it differs from the last one sent in
 * this session, whole or not at all. */
static void equipment_send(const u8 *ref)
{
    static u32 last_check, sent_hash, sent_welcome;
    char ids[EQUIP_ITEMS][EQUIP_ID];
    u8 parts[EQUIP_ITEMS][EVENT_DATA];
    u32 lengths[EQUIP_ITEMS], n, i, k, count = 0, hash = 2166136261u, flags, room, now = now_us();

    if (ses.state != SESSION_JOINED || now - last_check < EQUIP_PERIOD_US)
        return;
    last_check = now;
    n = equipment_ids(ref, ids);
    for (i = 0; i < n; i++) {
        for (k = 0; ids[i][k]; k++)
            hash = (hash ^ (u8)ids[i][k]) * 16777619u;
        hash *= 16777619u; /* the terminating zero */
    }
    if (hash == sent_hash && sent_welcome == ses.welcomes)
        return;
    lengths[0] = 2;
    for (i = 0; i < n; i++) {
        for (k = 0; ids[i][k]; k++)
            ;
        if (lengths[count] + k + 1 > EVENT_DATA)
            lengths[++count] = 2;
        copy(parts[count] + lengths[count], (const u8 *)ids[i], k + 1);
        lengths[count] += k + 1;
    }
    count++;
    flags = lock();
    room = EVENTS_OUT - (rel.out_next - rel.out_first);
    unlock(flags);
    if (room < count)
        return;
    for (i = 0; i < count; i++) {
        parts[i][0] = (u8)i;
        parts[i][1] = (u8)count;
        event_queue(EVENT_EQUIPMENT, parts[i], lengths[i]);
    }
    sent_hash = hash;
    sent_welcome = ses.welcomes;
    equip_sent++;
    tes3x_log_hex3("net.equipment_sent", n, count, hash);
}

static u32 look_of(u32 client)
{
    u32 i, j, pick = 0;

    for (i = 0; i < PEERS; i++)
        if (looks[i].client == client)
            return i;
    /* Otherwise the least recently used entry of a client that is not a peer now. */
    for (i = 0; i < PEERS; i++) {
        for (j = 0; j < PEERS && peers[j].client != looks[i].client; j++)
            ;
        if ((j == PEERS || !looks[i].client) && looks[i].used <= looks[pick].used)
            pick = i;
    }
    looks[pick].client = client;
    looks[pick].generation = looks[pick].count = looks[pick].staged = looks[pick].next_part = 0;
    return pick;
}

static void equipment_event(const struct event *e)
{
    u32 l, off = 2, k;

    if (e->length < 2)
        return;
    for (off = 2; off < e->length; off++)
        if ((e->data[off] && e->data[off] < 0x20) || e->data[off] == '"') {
            refused_events++;
            return;
        }
    off = 2;
    l = look_of(e->origin);
    looks[l].used = ++look_clock;
    if (e->data[0] == 0)
        looks[l].staged = looks[l].next_part = 0;
    else if (e->data[0] != looks[l].next_part)
        return;
    looks[l].next_part++;
    while (off < e->length && looks[l].staged < EQUIP_ITEMS) {
        for (k = 0; off + k < e->length && e->data[off + k] && k < EQUIP_ID - 1; k++)
            looks[l].stage[looks[l].staged][k] = (char)e->data[off + k];
        looks[l].stage[looks[l].staged++][k] = 0;
        while (off < e->length && e->data[off])
            off++;
        off++;
    }
    if (looks[l].next_part != e->data[1])
        return;
    copy((u8 *)looks[l].ids, (const u8 *)looks[l].stage, sizeof(looks[l].ids));
    looks[l].count = looks[l].staged;
    looks[l].generation = look_clock;
    equip_received++;
    tes3x_log_hex3("net.equipment", e->origin, looks[l].count, looks[l].generation);
}

static void ghost_item(u32 i, const char *verb, const char *id, const char *tail)
{
    char line[128];
    char *p = put_text(put_text(put_ghost(line, i), verb), " \"");

    p = put_text(put_text(put_text(p, id), "\""), tail);
    *p = 0;
    run_script(line);
}

/* Once per new set, on a placed ghost. */
static void equipment_apply(u32 i, u8 *ref)
{
    char worn[EQUIP_ITEMS][EQUIP_ID];
    u32 l, n, k, removed = 0, added = 0;

    for (l = 0; l < PEERS && looks[l].client != ghosts[i].client; l++)
        ;
    if (l == PEERS || !looks[l].generation || looks[l].generation == ghosts[i].look)
        return;
    ghosts[i].look = looks[l].generation;
    looks[l].used = ++look_clock;
    n = equipment_ids(ref, worn);
    for (k = 0; k < n; k++)
        if (!has_id(looks[l].ids, looks[l].count, worn[k])) {
            ghost_item(i, "RemoveItem", worn[k], " 1");
            removed++;
        }
    for (k = 0; k < looks[l].count; k++)
        if (!has_id(worn, n, looks[l].ids[k])) {
            ghost_item(i, "Equip", looks[l].ids[k], "");
            added++;
        }
    equip_applied++;
    tes3x_log_hex3("net.ghost_equip", i + 1, removed << 16 | added,
                   equipment_ids(ref, worn) << 16 | looks[l].count);
}

/* A ghost stands for a player, so damage done to it here, by this console's player or an actor
 * this console runs, goes to that player's console as PLAYER_HIT. The ghost keeps GHOST_HEALTH in
 * health and fatigue, refilled each frame, so no blow kills or fells it. */
#define GHOST_HEALTH 5000.0f

typedef u8(__attribute__((thiscall)) *fn_apply_health)(void *mobile, float damage, u8 player,
                                                        u8 difficulty, u8 keep_health);
typedef float(__attribute__((thiscall)) *fn_apply_fatigue)(void *mobile, float damage, float swing,
                                                           u8 voice);
typedef void(__attribute__((thiscall)) *fn_hit_stun)(void *mobile, float damage, u8 died);

static void event_hit(u32 kind, u32 refid, u32 target, float health, float fatigue)
{
    u8 data[16];

    put32le(data, refid);
    put32le(data + 4, target);
    copy(data + 8, (const u8 *)&health, 4);
    copy(data + 12, (const u8 *)&fatigue, 4);
    if (!event_queue(kind, data, sizeof(data)))
        tes3x_log("net.event_full", kind);
}

static void ghost_health(u32 i, u8 *ref)
{
    u8 *mobile = ref_mobile(ref);
    float *health, *fatigue, damage, tired;

    if (!plausible(mobile))
        return;
    health = (float *)(mobile + MOBILE_HEALTH);
    fatigue = (float *)(mobile + MOBILE_FATIGUE);
    damage = GHOST_HEALTH - *health;
    tired = GHOST_HEALTH - *fatigue;
    if (ghosts[i].armed && (damage > 0.5f || tired > 0.5f)) {
        event_hit(EVENT_PLAYER_HIT, 0, ghosts[i].client, damage > 0 ? damage : 0,
                  tired > 0 ? tired : 0);
        player_hits_out++;
        tes3x_log_hex3("net.player_hit_sent", ghosts[i].client, (u32)round_int(damage),
                       (u32)round_int(tired));
    }
    *health = *fatigue = GHOST_HEALTH;
    ghosts[i].armed = 1;
}

/* Another console's ghost of this player was hit there: the damage as the engine applies a blow,
 * with its sounds, and the stun test, whose flinch the ghost there mirrors. */
static void player_hit_event(const struct event *e)
{
    const u8 *ref = player_reference();
    u8 *mobile = ref ? ref_mobile(ref) : 0;
    float health, fatigue = 0;

    if (e->length < 12 || get32le(e->data + 4) != ses.client || !plausible(mobile))
        return;
    if (!float_within(e->data + 8, e->length >= 16 ? 2 : 1, STAT_LIMIT)) {
        refused_events++;
        return;
    }
    copy((u8 *)&health, e->data + 8, 4);
    if (e->length >= 16)
        copy((u8 *)&fatigue, e->data + 12, 4);
    if (fatigue > 0)
        ((fn_apply_fatigue)TES3X_NET_APPLY_FATIGUE_DAMAGE)(mobile, fatigue, 1.0f, 0);
    if (health > 0)
        ((fn_apply_health)TES3X_NET_APPLY_HEALTH_DAMAGE)(mobile, health, 0, 0, 0);
    ((fn_hit_stun)TES3X_NET_HIT_STUN)(mobile, health > 0 ? health : fatigue, 0);
    player_hits_in++;
    tes3x_log_hex3("net.player_hit", e->origin, (u32)round_int(health), (u32)round_int(fatigue));
}

static void ghost_update(u32 i, const struct pose *local)
{
    struct pose p, placed;
    u32 client = peers[i].client;
    u8 *ref, older[ANIM_BYTES];
    float frac;

    if (client != ghosts[i].client) {
        if (ghosts[i].placed)
            ghost_park(i);
        ghosts[i].client = client;
        ghosts[i].look = 0;
        if (client)
            tes3x_log_hex3("net.ghost", i + 1, client, 0);
    }
    if (!client || !ghost_pose(i, &p, older, &frac))
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
        return;
    }
    if (!(ref = ghost_ref(i)))
        return;
    /* Against where it stands: a ghost that was hit turns to its attacker by itself. */
    if (off_pose(ref, &p.x)) {
        place_ref(ref, &p.x);
        ghosts[i].x = p.x;
        ghosts[i].y = p.y;
        ghosts[i].z = p.z;
        ghosts[i].heading = p.heading;
        ghost_moves++;
    }
    ghost_health(i, ref);
    equipment_apply(i, ref);
    stance_apply(ref, p.flags);
    anim_apply(ref, older, p.anim, frac);
}

/* Each placed ghost's heading in its reference, its orientation attachment and its node's
 * rotation (first row, x1000): the engine may turn one without the others. */
static void ghost_heading_stat(void)
{
    const u8 *ref, *node, *mobile;
    const float *m;
    u32 i;

    for (i = 0; i < PEERS; i++) {
        if (!ghosts[i].placed || !(ref = ghost_ref(i)))
            continue;
        tes3x_log_hex3("net.ghost_heading", i + 1,
                       (u32)round_int(*(const float *)(ref + REF_ORIENTATION + 8) * 1000),
                       (u32)round_int(((const float *)((fn_ref_part)TES3X_NET_REF_ORIENTATION)(
                                          ref))[2] * 1000));
        if (plausible(node = *(const u8 *const *)(ref + REF_NODE)) &&
            plausible(m = *(const float *const *)(node + NODE_ROTATION)))
            tes3x_log_hex3("net.ghost_node", i + 1, (u32)round_int(m[0] * 1000),
                           (u32)round_int(m[1] * 1000));
        if (plausible(mobile = ref_mobile(ref)))
            tes3x_log_hex3("net.ghost_mobile", i + 1, *(const u32 *)(mobile + MOBILE_FLAGS),
                           *(const u32 *)(ref + 8));
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
 * events). It sends their states about 10 times a second; every other client holds those actors'
 * AI and places them from the states. A hit or a held dialogue on a followed actor goes to its
 * authority as an event. Only references from the
 * data files take part: their mod index and refnum name one object under one load order. */
#define EVENT_AUTHORITY 2u   /* cell key, client */
#define EVENT_HOLD 3u        /* refid, authority, on */
#define EVENT_HOLD_BROKEN 4u /* refid, holder, reason */
#define EVENT_HIT 5u         /* refid, authority, health damage, fatigue damage */
#define EVENT_DEATH 6u       /* refid; the server records it and replays it to each joining client */
/* count, then (actor id, client) pairs: the player nearest an actor runs it, since the engine runs
 * actors only within aiDistance of its own player; client 0 hands it back to its cell's authority */
#define EVENT_OWNERS 20u
#define OWNERS 256u
#define KEY_EXTERIOR 1u
#define KEY_INTERIOR 2u
#define KEY_BYTES (12u + CELL_NAME) /* kind, grid x, grid y, interior name */
/* refid, x, y, z, heading, health, flags, magicka, fatigue, combat target, animation */
#define ACTOR_HEALTH 20u
#define ACTOR_MAGICKA 28u
#define ACTOR_FATIGUE 32u
#define ACTOR_TARGET 36u /* a client id for a player, else an actor id; 0 for none */
#define ACTOR_ANIM 40u
#define ACTOR_BYTES (ACTOR_ANIM + ANIM_BYTES)
#define ACTORS_PER_PACKET 8u /* a body of at most EVENTS_BYTES */
#define PLAYER_IDS 0x01000000u /* below: a client id; an actor id has a mod index of at least 1 */
#define MOBILE_TARGET 0xEC
#define ACTOR_PERIOD_US 100000u
#define ACTOR_SAMPLES 4u
#define ACTOR_DELAY_US 200000u /* two periods: a state late by one still has a pair */
#define ACTOR_DEAD 1u
#define ACTOR_IN_COMBAT 2u
#define AUTHORITIES 16u
#define ACTORS 256u /* a full follow table leaves the rest running here too */
#define REMOTE_HOLDS 8u
#define HITS 8u
#define DEATHS 256u
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

/* The latest state of each actor from its authority and its last few positions; the receive DPC
 * writes, bytes only. */
static struct {
    u32 refid, origin, seq, time, head, count;
    u8 state[ACTOR_BYTES];
    struct timed track[ACTOR_SAMPLES];
} actors_in[ACTORS];
static struct {
    struct cell_key key;
    u32 client;
} authority[AUTHORITIES];
static u32 authorities, authority_welcome;
static struct {
    u32 id, client;
} owners[OWNERS];
static u32 owner_count, owners_in, owners_full;
/* Actors this console places for another authority; held while their AI is to be skipped. */
static struct {
    u32 refid, owner, held, seen, animated;
    u8 *mobile;
    float health, fatigue;
} followed[ACTORS];
/* Actors this console runs that another client is talking to. */
static struct {
    u32 refid, holder, simulated, found, release;
    u8 *mobile;
    float health;
} remote_holds[REMOTE_HOLDS];
static struct {
    u32 refid, origin;
    float damage, fatigue;
} hits[HITS];
static u32 hit_count, talk_refid, talk_owner, talk_broken;
static u32 actor_states_out, actor_states_in, actor_moves, follows, follows_full;
static u32 hits_out, hits_in, remote_holds_in, remote_breaks_out, remote_breaks_in, retaliations;
/* Every death this session has seen, reported here or told by the server. */
static u32 deaths[DEATHS], death_count, deaths_reported, deaths_applied;

static u32 actor_id(const u8 *ref);
static u32 actor_owner(u32 id, u32 cell_owner);
static void status_frame(const u8 *mobile, u8 *ref, u32 refid);

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
        if (!float_within(a + 4, 3, POSITION_LIMIT) || !float_within(a + 16, 1, ANGLE_LIMIT) ||
            !float_within(a + ACTOR_HEALTH, 1, STAT_LIMIT) ||
            !float_within(a + ACTOR_MAGICKA, 2, STAT_LIMIT)) {
            refused_states++;
            continue;
        }
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
        if (actors_in[slot].refid != refid || actors_in[slot].origin != origin)
            actors_in[slot].count = 0;
        actors_in[slot].refid = refid;
        actors_in[slot].origin = origin;
        actors_in[slot].seq = seq;
        actors_in[slot].time = now_us();
        copy(actors_in[slot].state, a, ACTOR_BYTES);
        j = actors_in[slot].head;
        actors_in[slot].track[j].time = actors_in[slot].time;
        copy((u8 *)actors_in[slot].track[j].p, a + 4, 16);
        copy(actors_in[slot].track[j].anim, a + ACTOR_ANIM, ANIM_BYTES);
        actors_in[slot].track[j].flags = get32le(a + 24);
        actors_in[slot].head = (j + 1) % ACTOR_SAMPLES;
        if (actors_in[slot].count < ACTOR_SAMPLES)
            actors_in[slot].count++;
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

static void unfollow(u32 i, int restore)
{
    if (restore && followed[i].animated)
        anim_release(*(u8 **)(followed[i].mobile + MOBILE_REFERENCE));
    followed[i].refid = 0;
}

/* A followed actor stays in the simulation, where blows, projectiles and effects reach it, but
 * its AI step runs as with ToggleAI off: no decisions, movement cleared, velocity zero. */
#define WORLD_TOGGLES 0x2C0
#define TOGGLES_FLAGS 0x24
#define TOGGLE_AI_OFF 8u

static const u32 ai_step_slots[] = TES3X_NET_AI_STEP_SLOTS;
static u32 ai_hooked, ai_held;

static void __attribute__((thiscall)) ai_step_hook(u8 *mobile)
{
    u8 *world = *(u8 **)TES3X_NET_WORLD, *toggles;
    u32 i, saved;

    for (i = 0; i < ACTORS; i++)
        if (followed[i].refid && followed[i].held && followed[i].mobile == mobile)
            break;
    if (i == ACTORS || !plausible(world) ||
        !plausible(toggles = *(u8 **)(world + WORLD_TOGGLES))) {
        ((fn_mobile_call)TES3X_NET_AI_STEP)(mobile);
        return;
    }
    saved = *(u32 *)(toggles + TOGGLES_FLAGS);
    *(u32 *)(toggles + TOGGLES_FLAGS) = saved | TOGGLE_AI_OFF;
    ((fn_mobile_call)TES3X_NET_AI_STEP)(mobile);
    *(u32 *)(toggles + TOGGLES_FLAGS) = saved;
    ai_held++;
}

static void ai_hook_install(void)
{
    u32 i, cr0, flags, n = sizeof(ai_step_slots) / sizeof(ai_step_slots[0]);

    if (ai_hooked)
        return;
    ai_hooked = 1;
    for (i = 0; i < n; i++)
        if (*(const u32 *)ai_step_slots[i] != TES3X_NET_AI_STEP) {
            tes3x_log_hex3("net.ai_slot_unexpected", ai_step_slots[i],
                           *(const u32 *)ai_step_slots[i], 0);
            return;
        }
    flags = lock();
    __asm__ volatile("movl %%cr0, %0" : "=r"(cr0));
    __asm__ volatile("movl %0, %%cr0" : : "r"(cr0 & ~CR0_WP) : "memory");
    for (i = 0; i < n; i++)
        *(u32 *)ai_step_slots[i] = (u32)ai_step_hook;
    __asm__ volatile("movl %0, %%cr0" : : "r"(cr0) : "memory");
    unlock(flags);
    ai_hooked = 2;
    tes3x_log("net.ai_hook", n);
}

/* Another client runs this actor: hold its AI, place it ACTOR_DELAY_US behind its authority's
 * states, as ghosts are, and send a drop in its health to the authority as a hit. Its statistics
 * follow the authority's, except a health of 0 or less: DEATH brings that. */
static void follow(u8 *mobile, u8 *ref, u32 refid, u32 owner)
{
    u32 *flags = (u32 *)(mobile + MOBILE_FLAGS), i, k, slot = ACTORS, count = 0, lk;
    float health = *(const float *)(mobile + MOBILE_HEALTH), damage, tired, to[4], frac, stats[3];
    float fatigue = *(const float *)(mobile + MOBILE_FATIGUE);
    struct timed track[ACTOR_SAMPLES];

    for (i = 0; i < ACTORS && slot == ACTORS; i++)
        if (followed[i].refid == refid)
            slot = i;
    if (slot < ACTORS && followed[slot].mobile != mobile)
        unfollow(slot, 0); /* the same reference in a new mobile: a reload */
    if (slot == ACTORS || !followed[slot].refid) {
        for (i = 0, slot = ACTORS; i < ACTORS && slot == ACTORS; i++)
            if (!followed[i].refid)
                slot = i;
        if (slot == ACTORS) {
            follows_full++;
            return;
        }
        followed[slot].refid = refid;
        followed[slot].mobile = mobile;
        followed[slot].health = health;
        followed[slot].fatigue = fatigue;
        followed[slot].animated = 0;
        follows++;
        tes3x_log_hex3("net.follow", refid, owner, *flags & MOBILE_SIMULATED);
    }
    followed[slot].owner = owner;
    followed[slot].seen = 1;
    damage = followed[slot].health - health;
    tired = followed[slot].fatigue - fatigue;
    if (damage > 0.5f || tired > 0.5f) {
        event_hit(EVENT_HIT, refid, owner, damage > 0 ? damage : 0, tired > 0 ? tired : 0);
        hits_out++;
        tes3x_log_hex3("net.hit_sent", refid, (u32)round_int(damage), (u32)round_int(tired));
    }
    followed[slot].health = health;
    followed[slot].fatigue = fatigue;
    lk = lock();
    for (i = 0; i < ACTORS; i++)
        if (actors_in[i].refid == refid && actors_in[i].origin == owner) {
            count = actors_in[i].count;
            copy((u8 *)&stats[0], actors_in[i].state + ACTOR_HEALTH, 4);
            copy((u8 *)&stats[1], actors_in[i].state + ACTOR_MAGICKA, 4);
            copy((u8 *)&stats[2], actors_in[i].state + ACTOR_FATIGUE, 4);
            for (k = 0; k < count; k++)
                track[k] = actors_in[i].track[(actors_in[i].head + ACTOR_SAMPLES - count + k) %
                                              ACTOR_SAMPLES];
            break;
        }
    unlock(lk);
    /* A death plays out in the simulation; a dead actor has nothing left to place. */
    if (mobile[MOBILE_ACTION] == 0x12 || mobile[MOBILE_ACTION] == 0x13 || health <= 0) {
        followed[slot].held = 0;
        *flags &= ~MOBILE_SCRIPTED;
        return;
    }
    followed[slot].held = 1;
    if (!count)
        return;
    if (stats[0] > 0) {
        *(float *)(mobile + MOBILE_HEALTH) = stats[0];
        followed[slot].health = stats[0];
    }
    *(float *)(mobile + MOBILE_MAGICKA) = stats[1];
    *(float *)(mobile + MOBILE_FATIGUE) = stats[2];
    followed[slot].fatigue = stats[2];
    k = track_pose(track, count, ACTOR_DELAY_US, to, &frac);
    if (off_pose(ref, to)) {
        place_ref(ref, to);
        actor_moves++;
    }
    stance_apply(ref, track[k].flags);
    anim_apply(ref, track[k ? k - 1 : k].anim, track[k].anim, frac);
    followed[slot].animated = 1;
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

static int death_known(u32 refid)
{
    u32 i, n = death_count < DEATHS ? death_count : DEATHS;

    for (i = 0; i < n; i++)
        if (deaths[i] == refid)
            return 1;
    return 0;
}

static void death_add(u32 refid)
{
    if (!death_known(refid))
        deaths[death_count++ % DEATHS] = refid;
}

/* Refids seen alive here, open addressing; a full table stops adding. Only their deaths are
 * reported, so a body the data files place dead is not. */
#define ALIVE_SLOTS 1024u
static u32 alive_seen[ALIVE_SLOTS];

static int alive_mark(u32 refid, int add)
{
    u32 i = (refid * 2654435761u) >> 22, n;

    for (n = 0; n < ALIVE_SLOTS; n++, i = (i + 1) & (ALIVE_SLOTS - 1)) {
        if (alive_seen[i] == refid)
            return 1;
        if (!alive_seen[i]) {
            if (add)
                alive_seen[i] = refid;
            return add;
        }
    }
    return 0;
}

/* A death on the authority goes to everyone once; a death told by the server is applied to any
 * living copy here, whoever runs it. */
static void death_frame(u8 *mobile, u8 *ref, u32 refid, u32 owner)
{
    float health = *(const float *)(mobile + MOBILE_HEALTH);
    int dead = mobile[MOBILE_ACTION] == 0x12 || mobile[MOBILE_ACTION] == 0x13 || health <= 0;
    u32 i;

    if (!dead)
        alive_mark(refid, 1);
    /* Not before the actor has its animation controller, which it gets only near the player:
     * the death path reads it. */
    if (!dead && death_known(refid) && plausible(*(void **)(mobile + MOBILE_ANIM_CONTROLLER))) {
        actor_command(ref, "SetHealth ", 0);
        *(u32 *)(mobile + MOBILE_FLAGS) |= MOBILE_SIMULATED;
        for (i = 0; i < ACTORS; i++)
            if (followed[i].refid == refid)
                followed[i].health = 0; /* a told death, not a hit to send back */
        deaths_applied++;
        tes3x_log_hex3("net.actor_killed", refid, owner, 0);
    } else if (dead && owner == ses.client && !death_known(refid) && alive_mark(refid, 0)) {
        death_add(refid);
        if (event_queue(EVENT_DEATH, (const u8 *)&refid, 4))
            deaths_reported++;
        tes3x_log_hex3("net.actor_died", refid, (u32)(int)health, mobile[MOBILE_ACTION]);
    }
}

/* A hit from a follower's player lands as a blow does: the damage with its sounds, the stun test
 * and, from the hitter's ghost, the blood. The actor turns on that ghost unless it is fighting
 * already. */
typedef void(__attribute__((thiscall)) *fn_blood)(void *splashes, void *victim, void *attacker);
#define WORLD_SPLASHES 0x68
static u32 bloodied;

static void hits_apply(u8 *mobile, void *ref, u32 refid)
{
    u8 *world = *(u8 **)TES3X_NET_WORLD, *attacker;
    char line[48];
    u32 i, g;

    for (i = 0; i < hit_count; i++)
        if (hits[i].refid == refid) {
            for (g = 0; g < PEERS && !(ghosts[g].client == hits[i].origin && ghosts[g].placed);
                 g++)
                ;
            if (hits[i].fatigue > 0)
                ((fn_apply_fatigue)TES3X_NET_APPLY_FATIGUE_DAMAGE)(mobile, hits[i].fatigue, 1.0f,
                                                                   0);
            if (hits[i].damage > 0)
                ((fn_apply_health)TES3X_NET_APPLY_HEALTH_DAMAGE)(mobile, hits[i].damage, 0, 0, 0);
            ((fn_hit_stun)TES3X_NET_HIT_STUN)(mobile, hits[i].damage > 0 ? hits[i].damage
                                                                          : hits[i].fatigue, 0);
            if (hits[i].damage > 0 && g < PEERS && plausible(world) &&
                plausible(*(void **)(world + WORLD_SPLASHES)) &&
                ghost_ref(g) && plausible(attacker = ref_mobile(ghost_ref(g)))) {
                ((fn_blood)TES3X_NET_BLOOD)(*(void **)(world + WORLD_SPLASHES), mobile, attacker);
                bloodied++;
            }
            if (g < PEERS && !(*(const u32 *)(mobile + MOBILE_FLAGS) & MOBILE_IN_COMBAT)) {
                *put_text(put_int(put_text(line, "StartCombat \"tes3x_ghost"), (int)g + 1),
                          "\"") = 0;
                run_script_on(line, ref);
                retaliations++;
                tes3x_log_hex3("net.retaliate", refid, hits[i].origin, g + 1);
            }
            hits[i--] = hits[--hit_count];
        }
}

/* An actor that attacks a player on sight attacks a ghost too. The engine only looks for the
 * local player, so on the actor's authority one that is not fighting turns on the nearest placed
 * ghost within HOSTILE_RANGE units that the engine's own test would attack: Fight, plus
 * iFightDistanceBase less fFightDistanceMultiplier per unit, plus fFightDispMult per point of an
 * NPC's disposition under 50, reaching iFightAttack (Morrowind.esm's values). An NPC's Fight counts
 * no higher than its record's: a crime raises the witnesses' against that console's player. */
#define HOSTILE_RANGE 1024.0f
#define FIGHT_ATTACK 100.0f
#define FIGHT_DISTANCE_BASE 20.0f
#define FIGHT_DISTANCE_MULT 0.005f
#define FIGHT_DISP_MULT 0.2f
static u32 hostiles;

#define AI_FIGHT 2 /* in an AIConfig */
#define AI_ALARM 4
static int base_disposition(const u8 *ref);
static int record_ai(const u8 *ref, u32 offset, int value);

static float square_root(float x)
{
    __asm__("fsqrt" : "+t"(x));
    return x;
}

static void ghost_fight_log(const u8 *mobile, const u8 *ref, u32 refid);

static void hostile_check(const u8 *mobile, void *ref, u32 refid)
{
    const float *at = (const float *)((const u8 *)ref + REF_POSITION);
    float dx, dy, dz, d, near, best = HOSTILE_RANGE * HOSTILE_RANGE, fight;
    u32 g, pick = PEERS;
    char line[48];

    if ((*(const u32 *)(mobile + MOBILE_FLAGS) & MOBILE_IN_COMBAT) ||
        mobile[MOBILE_ACTION] == 0x12 || mobile[MOBILE_ACTION] == 0x13 ||
        *(const float *)(mobile + MOBILE_HEALTH) <= 0)
        return;
    fight = (float)record_ai(ref, AI_FIGHT, *(const int *)(mobile + MOBILE_FIGHT)) +
            FIGHT_DISP_MULT * (float)(50 - base_disposition(ref));
    if (fight + FIGHT_DISTANCE_BASE < FIGHT_ATTACK)
        return;
    for (g = 0; g < PEERS; g++) {
        if (!ghosts[g].placed)
            continue;
        dx = ghosts[g].x - at[0];
        dy = ghosts[g].y - at[1];
        dz = ghosts[g].z - at[2];
        if ((d = dx * dx + dy * dy + dz * dz) >= best)
            continue;
        near = FIGHT_DISTANCE_BASE - FIGHT_DISTANCE_MULT * square_root(d);
        if (fight + (near > 0 ? near : 0) >= FIGHT_ATTACK) {
            best = d;
            pick = g;
        }
    }
    if (pick == PEERS)
        return;
    *put_text(put_int(put_text(line, "StartCombat \"tes3x_ghost"), (int)pick + 1), "\"") = 0;
    run_script_on(line, ref);
    hostiles++;
    tes3x_log_hex3("net.hostile", refid, ghosts[pick].client, pick + 1);
}

/* An actor run here that fights a ghost, once a second: its action bytes (+0xDC, +0xDD), its
 * upper-body animation group and how far it stands from the ghost. */
static void ghost_fight_log(const u8 *mobile, const u8 *ref, u32 refid)
{
    static u32 last, lines;
    const u8 *target = *(const u8 *const *)(mobile + MOBILE_TARGET), *tref, *a;
    const float *p, *q;
    float dx, dy, dz;
    u32 now = now_us();

    if (!plausible(target) || !plausible(tref = *(const u8 *const *)(target + MOBILE_REFERENCE)) ||
        !is_ghost(tref))
        return;
    if (now - last >= 1000000u) {
        last = now;
        lines = 0;
    }
    if (lines++ >= 4)
        return;
    p = (const float *)(ref + REF_POSITION);
    q = (const float *)(tref + REF_POSITION);
    dx = p[0] - q[0];
    dy = p[1] - q[1];
    dz = p[2] - q[2];
    a = ref_animation(ref);
    tes3x_log_hex3("net.ghost_fight", refid,
                   mobile[0xDC] | (u32)mobile[0xDD] << 8 |
                       (u32)(plausible(a) ? a[ANIM_GROUP + 1] : 0xFF) << 16,
                   (u32)round_int(square_root(dx * dx + dy * dy + dz * dz)));
    tes3x_log_hex3("net.ghost_fighter", refid, *(const u32 *)(mobile + MOBILE_FLAGS),
                   *(const u32 *)(mobile + 0x244));
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

/* Out of a session, or in a new one, nothing learned in the last one holds. Runs before the frame's
 * events, which may already belong to the new session. */
static void authority_session(void)
{
    u32 i;

    if (ses.state == SESSION_JOINED && authority_welcome == ses.welcomes)
        return;
    authority_welcome = ses.welcomes;
    authorities = 0;
    owner_count = 0;
    hit_count = 0;
    for (i = 0; i < REMOTE_HOLDS; i++)
        remote_holds[i].release = 1;
    talk_refid = 0;
    death_count = 0; /* the server replays its deaths after WELCOME */
}

typedef void(__attribute__((thiscall)) *fn_start_combat)(void *mobile, void *target);
static u32 combats_taken, combats_stopped, combats_lost;
static u8 *actor_ref(u32 refid);

/* What an actor run here fights, as ACTORS sends it. */
static u32 combat_target(const u8 *mobile)
{
    const u8 *target = *(const u8 *const *)(mobile + MOBILE_TARGET), *tref;
    u32 g;

    if (!(*(const u32 *)(mobile + MOBILE_FLAGS) & MOBILE_IN_COMBAT) || !plausible(target) ||
        !plausible(tref = *(const u8 *const *)(target + MOBILE_REFERENCE)))
        return 0;
    if (tref == player_reference())
        return ses.client;
    for (g = 0; g < PEERS; g++)
        if (ghosts[g].ref == tref)
            return ghosts[g].placed ? ghosts[g].client : 0;
    return is_ghost(tref) ? 0 : actor_id(tref);
}

/* An actor this console takes over from another fights what that console's copy last fought, here
 * the player, a ghost or an actor; a fight its held copy picked up meanwhile is stopped. */
static void combat_take(u8 *mobile, void *ref, u32 refid, u32 from)
{
    u8 *tref = 0, *target;
    u32 i, g, lk, flags = 0, id = 0, found = 0;

    lk = lock();
    for (i = 0; i < ACTORS && !found; i++)
        if (actors_in[i].refid == refid && actors_in[i].origin == from && actors_in[i].count) {
            flags = get32le(actors_in[i].state + 24);
            id = get32le(actors_in[i].state + ACTOR_TARGET);
            found = 1;
        }
    unlock(lk);
    if (!found || (flags & ACTOR_DEAD))
        return;
    if (!(flags & ACTOR_IN_COMBAT) || !id) {
        if (*(const u32 *)(mobile + MOBILE_FLAGS) & MOBILE_IN_COMBAT) {
            run_script_on("StopCombat", ref);
            combats_stopped++;
            tes3x_log_hex3("net.combat_stopped", refid, from, 0);
        }
        return;
    }
    if (id == ses.client)
        tref = (u8 *)player_reference();
    else if (id < PLAYER_IDS) {
        for (g = 0; g < PEERS; g++)
            if (ghosts[g].client == id && ghosts[g].placed)
                tref = ghost_ref(g);
    } else
        tref = actor_ref(id);
    if (!plausible(tref) || !plausible(target = ref_mobile(tref)) || target == mobile) {
        combats_lost++;
        tes3x_log_hex3("net.combat_lost", refid, id, from);
        return;
    }
    ((fn_start_combat)TES3X_NET_START_COMBAT)(mobile, target);
    combats_taken++;
    tes3x_log_hex3("net.combat_taken", refid, id, from);
}

static void peace_check(const u8 *mobile, void *ref, u32 refid);

/* Once per frame in the world: follow, send or hold each actor the AI planners hold. */
static void authority_frame(const u8 *player, const u8 *state)
{
    static u32 last_send;
    const u8 *world = *(const u8 **)TES3X_NET_WORLD, *mobs, *process, *node, *planner;
    u8 out[4 + ACTORS_PER_PACKET * ACTOR_BYTES], *mobile, *ref, *a;
    struct pose local;
    struct cell_key key;
    u32 i, n = 0, guard, refid, owner, now = now_us(), lk, send, took;

    read_pose(state, &local);
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
            !(refid = actor_id(ref)) || is_ghost(ref))
            continue;
        actor_key(&local, *(const float *)(ref + 0x38), *(const float *)(ref + 0x3C), &key);
        owner = actor_owner(refid, authority_of(&key));
        death_frame(mobile, ref, refid, owner);
        if (send)
            status_frame(mobile, ref, refid);
        if (owner && owner != ses.client) {
            follow(mobile, ref, refid, owner);
            continue;
        }
        for (i = 0, took = 0; i < ACTORS; i++)
            if (followed[i].refid == refid) {
                took = followed[i].owner;
                unfollow(i, followed[i].mobile == mobile);
            }
        if (owner != ses.client)
            continue;
        if (took)
            combat_take(mobile, ref, refid, took);
        remote_hold_apply(mobile, refid);
        hits_apply(mobile, ref, refid);
        if (!send)
            continue;
        hostile_check(mobile, ref, refid);
        peace_check(mobile, ref, refid);
        ghost_fight_log(mobile, ref, refid);
        a = out + 4 + n * ACTOR_BYTES;
        put32le(a, refid);
        copy(a + 4, ref + 0x38, 12);
        copy(a + 16, ref + 0x34, 4);
        copy(a + ACTOR_HEALTH, mobile + MOBILE_HEALTH, 4);
        put32le(a + 24, (mobile[MOBILE_ACTION] == 0x12 || mobile[MOBILE_ACTION] == 0x13
                         ? ACTOR_DEAD : 0) |
                        (*(const u32 *)(mobile + MOBILE_FLAGS) & MOBILE_IN_COMBAT
                         ? ACTOR_IN_COMBAT : 0) | stance_of(mobile));
        copy(a + ACTOR_MAGICKA, mobile + MOBILE_MAGICKA, 4);
        copy(a + ACTOR_FATIGUE, mobile + MOBILE_FATIGUE, 4);
        put32le(a + ACTOR_TARGET, combat_target(mobile));
        anim_capture(ref, a + ACTOR_ANIM);
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

static void owners_event(const struct event *e)
{
    u32 count = e->length >= 4 ? get32le(e->data) : 0, i, j, id, client;

    for (i = 0; i < count && 8 + i * 8 <= e->length; i++) {
        id = get32le(e->data + 4 + i * 8);
        client = get32le(e->data + 8 + i * 8);
        owners_in++;
        for (j = 0; j < owner_count && owners[j].id != id; j++)
            ;
        if (j < owner_count && !client)
            owners[j] = owners[--owner_count];
        else if (j < owner_count)
            owners[j].client = client;
        else if (client && owner_count < OWNERS) {
            owners[owner_count].id = id;
            owners[owner_count++].client = client;
        } else if (client)
            owners_full++;
        if (owners_in <= 64)
            tes3x_log_hex3("net.owner", id, client, 0);
    }
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
    if (e->kind == EVENT_OWNERS) {
        owners_event(e);
        return;
    }
    if (e->kind == EVENT_DEATH) {
        if (e->length >= 4 && !death_known(refid)) {
            death_add(refid);
            tes3x_log_hex3("net.death", refid, e->origin, 0);
        }
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
        if (!float_within(e->data + 8, e->length >= 16 ? 2 : 1, STAT_LIMIT)) {
            refused_events++;
            return;
        }
        hits[hit_count].refid = refid;
        hits[hit_count].origin = e->origin;
        copy((u8 *)&hits[hit_count].damage, e->data + 8, 4);
        hits[hit_count].fatigue = 0;
        if (e->length >= 16)
            copy((u8 *)&hits[hit_count].fatigue, e->data + 12, 4);
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
    tes3x_log_hex3("net.owners", owner_count, owners_in, owners_full);
    tes3x_log_hex3("net.combats_taken", combats_taken, combats_stopped, combats_lost);
    for (i = 0; i < authorities; i++)
        tes3x_log_hex3("net.authority_is", authority[i].client, (u32)authority[i].key.gx,
                       (u32)authority[i].key.gy);
    tes3x_log_hex3("net.actor_states", actor_states_out, actor_states_in, actor_moves);
    tes3x_log_hex3("net.actor_events", follows, hits_out, hits_in);
    tes3x_log_hex3("net.ai_held", ai_held, ai_hooked, follows_full);
    {
        const u8 *w = *(const u8 **)TES3X_NET_WORLD, *m, *pm;

        if (plausible(w) && plausible(m = *(const u8 **)(w + 0x5C)) &&
            plausible(pm = *(const u8 **)(m + MOB_PROCESS)))
            tes3x_log_hex3("net.ai_distance", (u32)round_int(*(const float *)(pm + 0x830)), 0, 0);
    }
    tes3x_log_hex3("net.player_hits", player_hits_out, player_hits_in, retaliations);
    tes3x_log_hex3("net.hostiles", hostiles, bloodied, 0);
    tes3x_log_hex3("net.actor_deaths", death_count, deaths_reported, deaths_applied);
    tes3x_log_hex3("net.actor_holds", remote_holds_in, remote_breaks_out, remote_breaks_in);
}

/* Spells take effect where their target is run: the local player, or an actor this console runs.
 * The engine applies one effect of a magic source instance to one target at a time (spellHit, from
 * a cast on self, a touch or a projectile's hit), and its call sites lead to spell_hit_hook. A
 * spell cast here that reaches another client's ghost, or an actor another client runs, is sent to
 * that client as SPELL instead, which applies it to the real target with the caster's stand-in as
 * caster. A stand-in's own casts apply nowhere: its console sends what they hit. Sources other than
 * spells (enchantments, potions) apply here as before, so their damage still goes out as a hit. */
#define EVENT_SPELL 10u /* caster refid, target client, target refid (0: its player), source,
                           caster is a player, spell id */
#define SPELL_BYTES 14u
#define SPELL_ID 32u
#define SOURCE_SPELL 1u
#define TYPE_SPELL 0x4C455053u /* 'SPEL' */
#define OBJECT_TYPE 4
#define WORLD_MAGIC 0x6C /* the MagicInstanceController */
#define INSTANCE_CHANCE 0x10
#define INSTANCE_TARGET 0x14 /* touch and target effects go straight to it, as a trap's do */
#define INSTANCE_SOURCE 0xA0 /* object, then the source type byte */
#define INSTANCE_STATE 0xB4
#define INSTANCE_CASTER 0xB8
#define INSTANCE_WORKING 5 /* cast and working its effects */
#define SOURCE_EFFECTS 0x34 /* eight of EFFECT_BYTES; an id of -1 ends them */
#define EFFECT_BYTES 0x18
#define EFFECT_RANGE 4
#define RANGE_SELF 0
#define SOURCE_MAX_EFFECTS 8
#define NOBODY 0xFFFFFFFFu
#define SPELLS_SENT 8u

typedef void(__attribute__((thiscall)) *fn_spell_hit)(void *instance, void *target, int effect);
typedef int(__attribute__((thiscall)) *fn_activate_spell)(void *controller, void *caster, void *item,
                                                          void *combo);
typedef u8 *(__attribute__((thiscall)) *fn_magic_instance)(void *controller, int serial);
typedef u8 *(__attribute__((thiscall)) *fn_resolve_object)(void *records, const char *id);

static const u32 spell_sites[] = TES3X_NET_SPELL_HIT_SITES;
static u32 spell_hooked, spells_sent, spells_received, spells_applied, spells_withheld;
static u32 spell_failures;
static const u8 *spell_withheld_last;
/* Instance and target pairs sent this frame: spellHit comes once per effect. */
static struct {
    const u8 *instance, *target;
} spell_sent[SPELLS_SENT];
static u32 spell_sent_count;

/* Which console applies what happens to ref: 0 this one, NOBODY none (a parked ghost), else the
 * client that runs it. A followed actor's refid goes to *refid. */
static u32 ref_owner(const u8 *ref, u32 *refid)
{
    const u8 *mobile;
    u32 i;

    *refid = 0;
    if (!plausible(ref) || ref == player_reference() || !plausible(mobile = ref_mobile(ref)))
        return 0;
    for (i = 0; i < PEERS; i++)
        if (ghosts[i].ref == ref)
            return ghosts[i].placed && ghosts[i].client ? ghosts[i].client : NOBODY;
    if (is_ghost(ref))
        return NOBODY;
    for (i = 0; i < ACTORS; i++)
        if (followed[i].refid && followed[i].mobile == mobile) {
            *refid = followed[i].refid;
            return followed[i].owner;
        }
    return 0;
}

/* An actor the AI planners hold, by refid. */
static u8 *actor_ref(u32 refid)
{
    const u8 *world = *(const u8 **)TES3X_NET_WORLD, *mobs, *process, *node, *planner, *mobile;
    u8 *ref;
    u32 guard;

    if (!refid || !plausible(world) || !plausible(mobs = *(const u8 **)(world + 0x5C)) ||
        !plausible(process = *(const u8 **)(mobs + MOB_PROCESS)))
        return 0;
    node = *(const u8 *const *)(process + PROCESS_PLANNERS);
    for (guard = 0; plausible(node) && guard < 512; node = *(const u8 *const *)(node + 4), guard++)
        if (plausible(planner = *(const u8 *const *)(node + 8)) &&
            plausible(mobile = *(const u8 *const *)(planner + PLANNER_MOBILE)) &&
            plausible(ref = *(u8 *const *)(mobile + MOBILE_REFERENCE)) && actor_id(ref) == refid)
            return ref;
    return 0;
}

/* The id of an instance's spell, or 0 if its source is not a spell. */
static const char *spell_id(const u8 *instance)
{
    const u8 *source = *(const u8 *const *)(instance + INSTANCE_SOURCE);
    const char *id;

    if (instance[INSTANCE_SOURCE + 4] != SOURCE_SPELL || !plausible(source))
        return 0;
    id = ((fn_object_id)(*(void *const *const *)source)[OBJECT_GET_ID / 4])(source);
    return mapped(id) && *id ? id : 0;
}

/* SPELL or CAST data for an instance's spell cast by caster; its length, or 0 if the source is not
 * a spell. */
static u32 spell_data(u8 *data, const u8 *instance, const u8 *caster, u32 client, u32 refid)
{
    const u8 *player;
    const char *id;
    u32 n;

    if (!(id = spell_id(instance)))
        return 0;
    player = player_reference();
    put32le(data, plausible(caster) && caster != player ? actor_id(caster) : 0);
    put32le(data + 4, client);
    put32le(data + 8, refid);
    data[12] = SOURCE_SPELL;
    data[13] = caster == player;
    for (n = 0; id[n] && n < SPELL_ID - 1; n++)
        data[SPELL_BYTES + n] = (u8)id[n];
    data[SPELL_BYTES + n] = 0;
    return SPELL_BYTES + n + 1;
}

/* 1 if the hit went out, or went out already for another effect; 0 if it cannot be shared. */
static int spell_send(const u8 *instance, const u8 *caster, const u8 *target, u32 owner,
                      u32 refid)
{
    u8 data[SPELL_BYTES + SPELL_ID];
    u32 i, n;

    for (i = 0; i < spell_sent_count; i++)
        if (spell_sent[i].instance == instance && spell_sent[i].target == target)
            return 1;
    if (!(n = spell_data(data, instance, caster, owner, refid)))
        return 0;
    if (spell_sent_count < SPELLS_SENT) {
        spell_sent[spell_sent_count].instance = instance;
        spell_sent[spell_sent_count++].target = target;
    }
    if (!event_queue(EVENT_SPELL, data, n)) {
        tes3x_log("net.event_full", EVENT_SPELL);
        return 1;
    }
    spells_sent++;
    log_text("net.spell_sent", (const char *)data + SPELL_BYTES);
    tes3x_log_hex3("net.spell_to", owner, refid, get32le(data));
    return 1;
}

/* A reference as the other consoles can find it: the client that runs it, and its refid unless it
 * is that client's player. 0 if it cannot be named. */
static u32 ref_name(const u8 *ref, u32 *refid)
{
    u32 owner = ref_owner(ref, refid);

    if (owner == NOBODY || !plausible(ref))
        return 0;
    if (!owner && ref != player_reference() && !(*refid = actor_id(ref)))
        return 0;
    return owner ? owner : ses.client;
}

/* The reference a name from another console stands for here, or 0. */
static u8 *ref_named(u32 client, u32 refid)
{
    u32 i;

    if (refid)
        return actor_ref(refid);
    if (client == ses.client)
        return (u8 *)player_reference();
    for (i = 0; i < PEERS; i++)
        if (ghosts[i].client == client && ghosts[i].placed)
            return ghost_ref(i);
    return 0;
}

/* Casts. When a caster run here casts a spell with target effects, the spell goes to the other
 * consoles as CAST, with its target when the instance has one, and they replay it from the
 * caster's stand-in so the bolt flies there too. Every effect of a stand-in's cast is withheld,
 * so the replay only shows; what it does arrives as SPELL. */
#define EVENT_CAST 11u /* as SPELL; the target is optional */
#define INSTANCE_CASTING 1

typedef void(__cdecl *fn_cast_bolt)(void *instance, int effect, int index, int count);

static const u32 bolt_sites[] = TES3X_NET_CAST_BOLT_SITES;
static u32 casts_sent, casts_received, casts_replayed, casts_bolted;
static const u8 *cast_sent_last;

/* With no target, the engine launches an actor's bolt only when the actor has a combat target,
 * and then aims it there. A replayed cast without one borrows the stand-in itself as combat
 * target past that test, and gives it back before the bolt is aimed, so it flies where the
 * stand-in faces; or after a few frames if the cast never gets that far. */
#define CAST_AIM_FRAMES 8
static struct {
    u8 *mobile;
    const u8 *instance;
    u32 frames;
} cast_aim;

static void cast_aim_end(void)
{
    if (cast_aim.mobile && *(u8 **)(cast_aim.mobile + MOBILE_TARGET) == cast_aim.mobile)
        *(u8 **)(cast_aim.mobile + MOBILE_TARGET) = 0;
    cast_aim.mobile = 0;
    cast_aim.instance = 0;
}

static void cast_aim_frame(void)
{
    if (cast_aim.mobile && !--cast_aim.frames)
        cast_aim_end();
}

/* The caster's three layer groups and keys as the stream sends them (the player's, in first
 * person, from the model it runs), for comparing a cast's animation on both consoles. */
static void cast_anim_log(const char *what, const u8 *caster)
{
    u8 anim[ANIM_BYTES];

    if (caster == player_reference())
        player_anim_capture(caster, anim);
    else
        anim_capture(caster, anim);
    tes3x_log_hex3(what, (u32)anim[0] | (u32)anim[1] << 8 | (u32)anim[2] << 16,
                   (u32)anim[4] | (u32)anim[5] << 8 | (u32)anim[6] << 16, 0);
}

static void __cdecl cast_bolt_hook(u8 *instance, int effect, int index, int count)
{
    const u8 *caster = *(const u8 *const *)(instance + INSTANCE_CASTER), *target;
    u8 data[SPELL_BYTES + SPELL_ID];
    u32 client = 0, refid = 0, n;

    if (instance == cast_aim.instance && index + 1 >= count)
        cast_aim_end();
    ((fn_cast_bolt)TES3X_NET_CAST_BOLT)(instance, effect, index, count);
    if (ses.state != SESSION_JOINED)
        return;
    if (ref_owner(caster, &n)) {
        casts_bolted++;
        tes3x_log_hex3("net.cast_bolt", (u32)caster, (u32)index, (u32)count);
        return;
    }
    if (instance == cast_sent_last)
        return;
    cast_sent_last = instance;
    if (plausible(target = *(const u8 *const *)(instance + INSTANCE_TARGET)))
        client = ref_name(target, &refid);
    if (!(n = spell_data(data, instance, caster, client, client ? refid : 0)))
        return;
    if (!event_queue(EVENT_CAST, data, n)) {
        tes3x_log("net.event_full", EVENT_CAST);
        return;
    }
    casts_sent++;
    log_text("net.cast_sent", (const char *)data + SPELL_BYTES);
    tes3x_log_hex3("net.cast_at", client, refid, get32le(data));
    cast_anim_log("net.cast_anim_sent", caster);
}

static void affect_send(const u8 *instance, const u8 *target, u32 effect);

static void __attribute__((thiscall)) spell_hit_hook(u8 *instance, u8 *target, int effect)
{
    const u8 *caster;
    u32 refid, owner;

    if (ses.state == SESSION_JOINED && plausible(instance)) {
        caster = *(const u8 *const *)(instance + INSTANCE_CASTER);
        owner = ref_owner(caster, &refid) ? NOBODY : ref_owner(target, &refid);
        if (owner == NOBODY) {
            if (instance != spell_withheld_last) {
                spell_withheld_last = instance;
                tes3x_log_hex3("net.spell_withheld", (u32)caster, (u32)target, (u32)effect);
            }
            spells_withheld++;
            return;
        }
        if (owner && spell_send(instance, caster, target, owner, refid))
            return;
        if (!owner) {
            ((fn_spell_hit)TES3X_NET_SPELL_HIT)(instance, target, effect);
            affect_send(instance, target, (u32)effect);
            return;
        }
    }
    ((fn_spell_hit)TES3X_NET_SPELL_HIT)(instance, target, effect);
}

/* Points each `call rel32` in sites from original to hook; none if any site is not such a call. */
static int redirect_calls(const u32 *sites, u32 n, u32 original, const void *hook)
{
    u32 i, cr0, flags;
    u8 *site;

    for (i = 0; i < n; i++) {
        site = (u8 *)sites[i];
        if (site[0] != 0xE8 || (u32)site + 5 + *(const u32 *)(site + 1) != original) {
            tes3x_log_hex3("net.call_site_unexpected", (u32)site, site[0], original);
            return 0;
        }
    }
    flags = lock();
    __asm__ volatile("movl %%cr0, %0" : "=r"(cr0));
    __asm__ volatile("movl %0, %%cr0" : : "r"(cr0 & ~CR0_WP) : "memory");
    for (i = 0; i < n; i++) {
        site = (u8 *)sites[i];
        *(u32 *)(site + 1) = (u32)hook - ((u32)site + 5);
    }
    __asm__ volatile("movl %0, %%cr0" : : "r"(cr0) : "memory");
    unlock(flags);
    return 1;
}

static void spell_hook_install(void)
{
    if (spell_hooked)
        return;
    spell_hooked = 1;
    if (redirect_calls(spell_sites, sizeof(spell_sites) / sizeof(spell_sites[0]),
                       TES3X_NET_SPELL_HIT, (const void *)spell_hit_hook))
        spell_hooked |= 2;
    if (redirect_calls(bolt_sites, sizeof(bolt_sites) / sizeof(bolt_sites[0]),
                       TES3X_NET_CAST_BOLT, (const void *)cast_bolt_hook))
        spell_hooked |= 4;
    tes3x_log("net.spell_hook", spell_hooked);
}

static void spell_fail(const char *id, u32 why, u32 refid)
{
    spell_failures++;
    log_text("net.spell_failed", id);
    tes3x_log_hex3("net.spell_failed_why", why, refid, 0);
}

/* The id and names of a SPELL or CAST; 0 if it is too short. */
static int spell_read(const struct event *e, char *id, u32 *caster_id, u32 *client, u32 *refid)
{
    u32 n;

    if (e->length < SPELL_BYTES + 1)
        return 0;
    *caster_id = get32le(e->data);
    *client = get32le(e->data + 4);
    *refid = get32le(e->data + 8);
    for (n = 0; n < SPELL_ID - 1 && SPELL_BYTES + n < e->length && e->data[SPELL_BYTES + n]; n++)
        id[n] = (char)e->data[SPELL_BYTES + n];
    id[n] = 0;
    return 1;
}

/* The caster's stand-in here: the origin's ghost, or the actor. */
static u8 *spell_caster(const struct event *e, u32 caster_id)
{
    u32 i;

    if (!e->data[13])
        return actor_ref(caster_id);
    for (i = 0; i < PEERS; i++)
        if (ghosts[i].client == e->origin && ghosts[i].placed)
            return ghost_ref(i);
    return 0;
}

/* A new instance of the spell id from caster, certain to succeed, aimed at target; 0 on failure,
 * logged with refid. */
static u8 *spell_start(const char *id, u8 *caster, u8 *target, u32 refid)
{
    const u8 *handler = *(const u8 **)TES3X_NET_DATA_HANDLER;
    u8 *world = *(u8 **)TES3X_NET_WORLD, *source, *instance;
    void *records, *controller;
    struct {
        u8 *object;
        u32 type;
    } combo;

    if (!plausible(world) || !plausible(handler) || !plausible(records = *(void **)handler) ||
        !plausible(controller = *(void **)(world + WORLD_MAGIC))) {
        spell_fail(id, 2, refid);
        return 0;
    }
    source = ((fn_resolve_object)TES3X_NET_RESOLVE_OBJECT)(records, id);
    if (!plausible(source) || *(const u32 *)(source + OBJECT_TYPE) != TYPE_SPELL) {
        spell_fail(id, 3, refid); /* a spell made in the caster's game */
        return 0;
    }
    combo.object = source;
    combo.type = SOURCE_SPELL;
    instance = ((fn_magic_instance)TES3X_NET_MAGIC_INSTANCE)(
        controller, ((fn_activate_spell)TES3X_NET_ACTIVATE_SPELL)(controller, caster, 0, &combo));
    if (!plausible(instance)) {
        spell_fail(id, 4, refid);
        return 0;
    }
    *(float *)(instance + INSTANCE_CHANCE) = 100.0f;
    *(u8 **)(instance + INSTANCE_TARGET) = target;
    return instance;
}

/* Another console's spell reached a target this one runs: a new instance of the spell from the
 * caster's stand-in (or the target itself), each touch and target effect applied to the target as
 * a hit applies it, then left to work as a cast one does. */
static void spell_event(const struct event *e)
{
    const u8 *effects;
    u8 *target, *caster, *instance;
    char id[SPELL_ID];
    u32 refid, caster_id, client, i;

    if (!spell_read(e, id, &caster_id, &client, &refid) || client != ses.client)
        return;
    spells_received++;
    log_text("net.spell", id);
    tes3x_log_hex3("net.spell_from", e->origin, refid, caster_id);
    target = refid ? actor_ref(refid) : (u8 *)player_reference();
    if (!target || ref_owner(target, &i)) {
        spell_fail(id, 1, refid); /* not here, or run elsewhere by now */
        return;
    }
    if (!(caster = spell_caster(e, caster_id)))
        caster = target;
    if (!(instance = spell_start(id, caster, target, refid)))
        return;
    effects = *(const u8 *const *)(instance + INSTANCE_SOURCE) + SOURCE_EFFECTS;
    for (i = 0; i < SOURCE_MAX_EFFECTS && *(const short *)(effects + i * EFFECT_BYTES) != -1; i++)
        if (effects[i * EFFECT_BYTES + EFFECT_RANGE] != RANGE_SELF) {
            ((fn_spell_hit)TES3X_NET_SPELL_HIT)(instance, target, (int)i);
            affect_send(instance, target, i);
        }
    *(u32 *)(instance + INSTANCE_STATE) = INSTANCE_WORKING;
    spells_applied++;
    tes3x_log_hex3("net.spell_applied", refid, (u32)caster, i);
}

/* Another console's caster cast: the stand-in casts it here too, at the target when it has one. */
static void cast_event(const struct event *e)
{
    u8 *caster, *target, *instance, *mobile;
    char id[SPELL_ID];
    u32 refid, caster_id, client, own;

    if (!spell_read(e, id, &caster_id, &client, &refid))
        return;
    casts_received++;
    log_text("net.cast", id);
    tes3x_log_hex3("net.cast_from", e->origin, client, refid);
    caster = spell_caster(e, caster_id);
    if (!caster || !ref_owner(caster, &own))
        return; /* not here, or run here: not a stand-in */
    target = client ? ref_named(client, refid) : 0;
    if (!(instance = spell_start(id, caster, target, refid)))
        return;
    *(u32 *)(instance + INSTANCE_STATE) = INSTANCE_CASTING;
    if (!target && plausible(mobile = ref_mobile(caster)) &&
        !*(const u32 *)(mobile + MOBILE_TARGET)) {
        cast_aim_end();
        *(u8 **)(mobile + MOBILE_TARGET) = mobile;
        cast_aim.mobile = mobile;
        cast_aim.instance = instance;
        cast_aim.frames = CAST_AIM_FRAMES;
    }
    casts_replayed++;
    tes3x_log_hex3("net.cast_replayed", (u32)caster, (u32)target, 0);
    cast_anim_log("net.cast_anim_here", caster);
}

static void spell_stat(void)
{
    tes3x_log_hex3("net.spells", spells_sent, spells_received, spells_applied);
    tes3x_log_hex3("net.spells_withheld", spells_withheld, spell_failures, spell_hooked);
    tes3x_log_hex3("net.casts", casts_sent, casts_received, casts_replayed);
    tes3x_log_hex3("net.casts_bolted", casts_bolted, 0, 0);
}

/* Arrows, bolts and thrown weapons. An actor nocks one from its readied ammunition (nock, at
 * "Shoot Attach") and releases it through its vtable's shoot slot; the new projectile rolls to hit
 * each actor it reaches. A shot by the player or an actor run here goes to the other consoles as
 * SHOT, and there the stand-in nocks and releases one too. A stand-in's projectile always misses:
 * what it hits on its own console reaches the victim as a hit already. */
#define EVENT_SHOT 12u /* firer refid, the firer's two shot values, firer is a player, ammo id */
#define SHOT_BYTES 13u
#define MOBILE_NOCKED 0x100 /* the projectile in hand */
#define MOBILE_SHOT_VALUES 0xD0 /* two floats, 8 apart, the shoot slot passes on */
#define MOBILE_AMMO 0x38C /* the readied ammunition's stack, object first */

typedef u8(__cdecl *fn_hit_roll)(void *attacker, void *projectile, int a2);

static const u32 shoot_slots[] = TES3X_NET_SHOOT_SLOTS, shot_roll_sites[] = TES3X_NET_SHOT_ROLL_SITES;
static u32 shot_hooked, shots_sent, shots_received, shots_replayed, shots_missed, shot_failures;

/* What the release about to happen sends as SHOT, or 0 when it sends nothing. */
static u32 shot_read(u8 *mobile, u8 *data)
{
    const u8 *nocked = *(const u8 *const *)(mobile + MOBILE_NOCKED), *ref, *object, *player;
    const u8 *firer = *(const u8 *const *)(mobile + MOBILE_REFERENCE);
    const char *id;
    u32 n, own;

    if (ses.state != SESSION_JOINED || !plausible(nocked) || !plausible(firer) ||
        ref_owner(firer, &own) || !plausible(ref = *(const u8 *const *)(nocked + MOBILE_REFERENCE)) ||
        !plausible(object = *(const u8 *const *)(ref + 0x28)) ||
        !mapped(id = ((fn_object_id)(*(void *const *const *)object)[OBJECT_GET_ID / 4])(object)))
        return 0;
    player = player_reference();
    put32le(data, firer != player ? actor_id(firer) : 0);
    copy(data + 4, mobile + MOBILE_SHOT_VALUES, 4);
    copy(data + 8, mobile + MOBILE_SHOT_VALUES + 8, 4);
    data[12] = firer == player;
    for (n = 0; id[n] && n < EQUIP_ID - 1; n++)
        data[SHOT_BYTES + n] = (u8)id[n];
    data[SHOT_BYTES + n] = 0;
    return n + SHOT_BYTES + 1;
}

static void shot_send(const u8 *data, u32 n)
{
    if (!n)
        return;
    if (!event_queue(EVENT_SHOT, data, n)) {
        tes3x_log("net.event_full", EVENT_SHOT);
        return;
    }
    shots_sent++;
    log_text("net.shot_sent", (const char *)data + SHOT_BYTES);
}

static void __attribute__((thiscall)) shoot_hook(u8 *mobile)
{
    u8 data[SHOT_BYTES + EQUIP_ID];
    u32 n = shot_read(mobile, data);

    ((fn_mobile_call)TES3X_NET_SHOOT)(mobile);
    shot_send(data, n);
}

/* The player's slot holds its own release, which settles the ammunition stack and then runs the
 * shared one. */
static void __attribute__((thiscall)) player_shoot_hook(u8 *mobile)
{
    u8 data[SHOT_BYTES + EQUIP_ID];
    u32 n = shot_read(mobile, data);

    ((fn_mobile_call)TES3X_NET_PLAYER_SHOOT)(mobile);
    shot_send(data, n);
}

static u8 __cdecl shot_roll_hook(u8 *attacker, void *projectile, int a2)
{
    u32 own;

    if (ses.state == SESSION_JOINED && plausible(attacker) &&
        ref_owner(*(const u8 *const *)(attacker + MOBILE_REFERENCE), &own)) {
        shots_missed++;
        return 0;
    }
    return ((fn_hit_roll)TES3X_NET_HIT_ROLL)(attacker, projectile, a2);
}

static void shot_hook_install(void)
{
    u32 i, cr0, flags, n = sizeof(shoot_slots) / sizeof(shoot_slots[0]);

    if (shot_hooked)
        return;
    shot_hooked = 1;
    for (i = 0; i < n; i++)
        if (*(const u32 *)shoot_slots[i] != TES3X_NET_SHOOT) {
            tes3x_log_hex3("net.shoot_slot_unexpected", shoot_slots[i],
                           *(const u32 *)shoot_slots[i], 0);
            return;
        }
    if (*(const u32 *)TES3X_NET_PLAYER_SHOOT_SLOT != TES3X_NET_PLAYER_SHOOT) {
        tes3x_log_hex3("net.shoot_slot_unexpected", TES3X_NET_PLAYER_SHOOT_SLOT,
                       *(const u32 *)TES3X_NET_PLAYER_SHOOT_SLOT, 0);
        return;
    }
    if (!redirect_calls(shot_roll_sites, sizeof(shot_roll_sites) / sizeof(shot_roll_sites[0]),
                        TES3X_NET_HIT_ROLL, (const void *)shot_roll_hook))
        return;
    flags = lock();
    __asm__ volatile("movl %%cr0, %0" : "=r"(cr0));
    __asm__ volatile("movl %0, %%cr0" : : "r"(cr0 & ~CR0_WP) : "memory");
    for (i = 0; i < n; i++)
        *(u32 *)shoot_slots[i] = (u32)shoot_hook;
    *(u32 *)TES3X_NET_PLAYER_SHOOT_SLOT = (u32)player_shoot_hook;
    __asm__ volatile("movl %0, %%cr0" : : "r"(cr0) : "memory");
    unlock(flags);
    shot_hooked = 3;
    tes3x_log("net.shot_hook", n + 1);
}

/* Another console's firer shot: the stand-in nocks the same ammunition and releases it. A ghost is
 * given one more of it first, since the equipment it copies holds one. */
static void shot_event(const struct event *e)
{
    u8 *firer, *mobile;
    char id[EQUIP_ID];
    u32 i, n, own, ghost = PEERS;

    if (e->length < SHOT_BYTES + 1)
        return;
    shots_received++;
    for (n = 0; n < EQUIP_ID - 1 && SHOT_BYTES + n < e->length && e->data[SHOT_BYTES + n]; n++)
        id[n] = (char)e->data[SHOT_BYTES + n];
    id[n] = 0;
    if (!script_safe((const u8 *)id, EQUIP_ID)) {
        refused_events++;
        return;
    }
    log_text("net.shot", id);
    firer = 0;
    if (!e->data[12])
        firer = actor_ref(get32le(e->data));
    for (i = 0; e->data[12] && i < PEERS; i++)
        if (ghosts[i].client == e->origin && ghosts[i].placed && (firer = ghost_ref(i)))
            ghost = i;
    if (!firer || !ref_owner(firer, &own) || !plausible(mobile = ref_mobile(firer)))
        return; /* not here, or run here: not a stand-in */
    if (ghost < PEERS) {
        ghost_item(ghost, "AddItem", id, " 1");
        if (!*(const u32 *)(mobile + MOBILE_AMMO))
            ghost_item(ghost, "Equip", id, "");
    }
    if (!*(const u32 *)(mobile + MOBILE_NOCKED))
        ((fn_mobile_call)TES3X_NET_NOCK)(mobile);
    if (!*(const u32 *)(mobile + MOBILE_NOCKED)) {
        shot_failures++;
        tes3x_log_hex3("net.shot_failed", (u32)firer, *(const u32 *)(mobile + MOBILE_AMMO), 0);
        return;
    }
    copy(mobile + MOBILE_SHOT_VALUES, e->data + 4, 4);
    copy(mobile + MOBILE_SHOT_VALUES + 8, e->data + 8, 4);
    ((fn_mobile_call)TES3X_NET_SHOOT)(mobile);
    shots_replayed++;
    tes3x_log_hex3("net.shot_replayed", (u32)firer, get32le(e->data + 4), get32le(e->data + 8));
}

static void shot_stat(void)
{
    tes3x_log_hex3("net.shots", shots_sent, shots_received, shots_replayed);
    tes3x_log_hex3("net.shots_missed", shots_missed, shot_failures, shot_hooked);
}

/* Object state: whether a data-file reference that is not an actor is disabled or deleted (taken),
 * and its lock. The engine marks every reference it will save through the Reference vtable's
 * setObjectModified, so a hook there names what changed; the frame reads the new state and sends
 * it as OBJECTS records, which the server keeps and replays after each WELCOME. A received state is
 * applied to the reference when its cell is loaded, and again after each cell change. A cell is
 * named by its index in the cells list, which one load order makes the same everywhere. */
#define EVENT_OBJECTS 13u /* count, then records */
#define OBJECT_BYTES 8u   /* refid, cell index u16, state, lock level */
#define OBJECTS_PER_EVENT ((EVENT_DATA - 1) / OBJECT_BYTES)
#define OBJECT_DISABLED 1u
#define OBJECT_DELETED 2u
#define OBJECT_LOCK 4u
#define OBJECT_LOCKED 8u
#define OBJECTS 512u
#define OBJECTS_DIRTY 32u
#define OBJECT_NO_CELL 0xFFFFu
#define REF_FLAGS 0x08
#define REF_MODIFIED_FLAG 0x2u
#define REF_DELETED 0x20u
#define REF_DISABLED 0x800u
#define REF_LIST 0x14 /* the list it is in; the list's cell is at +0xC */
#define REF_BASE 0x28
#define REF_NEXT 0x20
#define ATTACH_LOCK 3u /* data: level, key, trap, locked byte at +0xC */
#define CELL_FLAGS 0x18
#define CELL_REFS_LOADED 0x10u
#define CELL_TEMPORARY 0x10 /* an object whose +8 is the temporary references' list */
#define LIST_HEAD 4         /* of a {count, head, tail, cell} list */
#define RECORDS_CELLS 0xB270 /* {count, head, tail}; nodes {cell, prev, next} */
#define TAG_REFR 0x52464552u
#define TAG_NPC 0x5F43504Eu
#define TAG_CREA 0x41455243u

typedef void(__attribute__((thiscall)) *fn_set_modified)(void *ref, u32 on);

static struct object {
    u32 refid, cell, state, level;
    u8 *cell_ptr;
    u8 pending, applied; /* to send; applied since the last cell change */
} objects[OBJECTS];
static u8 *objects_dirty[OBJECTS_DIRTY];
static u8 objects_dirty_player[OBJECTS_DIRTY]; /* marked during a player's own action */
static u32 player_acting;
static u32 objects_count, objects_dirty_count, objects_dirty_lost, objects_hooked;
static u32 objects_sent, objects_received, objects_applied, objects_full, objects_logged;
static u32 object_applying, objects_signature, objects_pass, spawns_pass, spawns_settled;

static void spawn_local(u8 *ref, u32 player);

static void __attribute__((thiscall)) ref_modified_hook(u8 *ref, u32 on)
{
    u32 i;

    ((fn_set_modified)TES3X_NET_REF_MODIFIED)(ref, on);
    if (!(on & 0xFF) || object_applying || ses.state != SESSION_JOINED)
        return;
    for (i = 0; i < objects_dirty_count; i++)
        if (objects_dirty[i] == ref) {
            objects_dirty_player[i] |= player_acting != 0;
            return;
        }
    if (objects_dirty_count < OBJECTS_DIRTY) {
        objects_dirty_player[objects_dirty_count] = player_acting != 0;
        objects_dirty[objects_dirty_count++] = ref;
    } else {
        objects_dirty_lost++;
    }
}

static void objects_hook_install(void)
{
    u32 *slot = (u32 *)TES3X_NET_REF_MODIFIED_SLOT, cr0, flags;

    if (objects_hooked)
        return;
    objects_hooked = 1;
    if (*slot != TES3X_NET_REF_MODIFIED) {
        tes3x_log_hex3("net.ref_modified_unexpected", (u32)slot, *slot, 0);
        return;
    }
    flags = lock();
    __asm__ volatile("movl %%cr0, %0" : "=r"(cr0));
    __asm__ volatile("movl %0, %%cr0" : : "r"(cr0 & ~CR0_WP) : "memory");
    *slot = (u32)ref_modified_hook;
    __asm__ volatile("movl %0, %%cr0" : : "r"(cr0) : "memory");
    unlock(flags);
    objects_hooked = 2;
}

static const u8 *cells_head(void)
{
    const u8 *handler = *(const u8 *const *)TES3X_NET_DATA_HANDLER, *records, *list;

    if (!plausible(handler) || !plausible(records = *(const u8 *const *)handler) ||
        !plausible(list = *(const u8 *const *)(records + RECORDS_CELLS)))
        return 0;
    return *(const u8 *const *)(list + LIST_HEAD);
}

static u32 cell_index(const u8 *cell)
{
    const u8 *node = cells_head();
    u32 i;

    for (i = 0; plausible(node) && i < OBJECT_NO_CELL; i++, node = *(const u8 *const *)(node + 8))
        if (*(const u8 *const *)node == cell)
            return i;
    return OBJECT_NO_CELL;
}

static u8 *cell_at(u32 index)
{
    const u8 *node = cells_head();

    while (plausible(node) && index--)
        node = *(const u8 *const *)(node + 8);
    return plausible(node) ? *(u8 *const *)node : 0;
}

/* A data-file reference that is not an actor: its cell index, state and lock level. */
static int object_read(const u8 *ref, u32 *cell, u32 *state, u32 *level)
{
    const u8 *base, *list, *att, *lock;
    u32 flags, tag;

    if (!plausible(ref) || *(const u32 *)(ref + 4) != TAG_REFR || !*(const u32 *)(ref + REF_ID) ||
        !plausible(base = *(const u8 *const *)(ref + REF_BASE)))
        return 0;
    tag = *(const u32 *)(base + 4);
    if (tag == TAG_NPC || tag == TAG_CREA)
        return 0;
    if (cell) {
        if (!plausible(list = *(const u8 *const *)(ref + REF_LIST)) ||
            (*cell = cell_index(*(const u8 *const *)(list + 0xC))) == OBJECT_NO_CELL)
            return 0;
    }
    /* Taken covers disabled: applying a take disables the reference too. */
    flags = *(const u32 *)(ref + REF_FLAGS);
    *state = flags & REF_DELETED ? OBJECT_DELETED : flags & REF_DISABLED ? OBJECT_DISABLED : 0;
    *level = 0;
    for (att = *(const u8 *const *)(ref + REF_ATTACHMENTS); plausible(att);
         att = *(const u8 *const *)(att + 4)) {
        if (*(const u32 *)att != ATTACH_LOCK || !plausible(lock = *(const u8 *const *)(att + 8)))
            continue;
        *state |= OBJECT_LOCK | (lock[0xC] ? OBJECT_LOCKED : 0);
        *level = *(const int *)lock < 0 ? 0 : *(const int *)lock > 255 ? 255 : *(const u32 *)lock;
        break;
    }
    return 1;
}

/* The lock level counts only while locked. */
static int object_same(u32 state, u32 level, const struct object *o)
{
    return state == o->state && (!(state & OBJECT_LOCKED) || level == o->level);
}

static struct object *object_find(u32 refid, int create)
{
    u32 i;

    for (i = 0; i < objects_count; i++)
        if (objects[i].refid == refid)
            return &objects[i];
    if (!create || objects_count >= OBJECTS) {
        if (create)
            objects_full++;
        return 0;
    }
    objects[objects_count].refid = refid;
    objects[objects_count].cell_ptr = 0;
    return &objects[objects_count++];
}

static u8 *object_reference(struct object *o)
{
    static const u32 lists[2] = {0x2C, 0x3C};
    const u8 *temporary;
    u8 *ref;
    u32 i;

    if (!o->cell_ptr && !(o->cell_ptr = cell_at(o->cell)))
        return 0;
    for (i = 0; i < 3; i++) {
        if (i < 2)
            ref = *(u8 **)(o->cell_ptr + lists[i] + LIST_HEAD);
        else if ((*(const u32 *)(o->cell_ptr + CELL_FLAGS) & CELL_REFS_LOADED) &&
                 plausible(temporary = *(const u8 *const *)(o->cell_ptr + CELL_TEMPORARY)))
            ref = *(u8 *const *)(temporary + 8 + LIST_HEAD);
        else
            break;
        for (; plausible(ref); ref = *(u8 **)(ref + REF_NEXT))
            if (*(const u32 *)(ref + REF_ID) == o->refid)
                return ref;
    }
    return 0;
}

static void object_apply(u8 *ref, const struct object *o)
{
    char text[24], *end;
    u32 state, level, gone;

    if (!object_read(ref, 0, &state, &level) || object_same(state, level, o))
        return;
    object_applying = 1;
    gone = OBJECT_DISABLED | OBJECT_DELETED;
    if ((o->state & gone) && !(state & gone))
        run_script_on("Disable", ref);
    else if (!(o->state & gone) && (state & OBJECT_DISABLED) && !(state & OBJECT_DELETED))
        run_script_on("Enable", ref);
    if ((o->state & OBJECT_DELETED) && !(state & OBJECT_DELETED)) {
        *(u32 *)(ref + REF_FLAGS) |= REF_DELETED;
        ((fn_set_modified)TES3X_NET_REF_MODIFIED)(ref, 1);
    }
    if ((o->state & OBJECT_LOCKED) && (!(state & OBJECT_LOCKED) || level != o->level)) {
        end = put_int(put_text(text, "Lock "), (int)o->level);
        *end = 0;
        run_script_on(text, ref);
    } else if ((o->state & OBJECT_LOCK) && !(o->state & OBJECT_LOCKED) && (state & OBJECT_LOCKED)) {
        run_script_on("Unlock", ref);
    }
    object_applying = 0;
    objects_applied++;
    if (objects_logged < 32) {
        objects_logged++;
        tes3x_log_hex3("net.object_applied", o->refid, o->state, o->level);
    }
}

static void objects_event(const struct event *e)
{
    const u8 *p = e->data + 1;
    struct object *o;
    u32 i, refid, cell, state, level;

    for (i = 0; i < e->data[0] && 1 + (i + 1) * OBJECT_BYTES <= e->length; i++, p += OBJECT_BYTES) {
        refid = get32le(p);
        cell = p[4] | (u32)p[5] << 8;
        state = p[6] & OBJECT_DELETED ? p[6] & ~OBJECT_DISABLED : p[6];
        level = p[7];
        objects_received++;
        if (!(o = object_find(refid, 1)))
            continue;
        if (o->cell != cell)
            o->cell_ptr = 0;
        o->cell = cell;
        o->state = state;
        o->level = level;
        o->pending = 0;
        o->applied = 0;
        objects_pass = 1;
    }
}

/* Game thread, in the world. */
static void objects_frame(void)
{
    const u8 *handler = *(const u8 *const *)TES3X_NET_DATA_HANDLER;
    u8 data[1 + OBJECTS_PER_EVENT * OBJECT_BYTES], *p;
    struct object *o, *batch[OBJECTS_PER_EVENT];
    u32 i, n, cell, state, level, signature;

    if (ses.state != SESSION_JOINED) {
        objects_dirty_count = 0;
        return;
    }
    for (i = 0; i < objects_dirty_count; i++) {
        if (!*(const u32 *)(objects_dirty[i] + REF_ID)) {
            spawn_local(objects_dirty[i], objects_dirty_player[i]);
            continue;
        }
        /* Opening a door marks it too: a reference in no state worth sharing gets no entry. */
        if (!object_read(objects_dirty[i], &cell, &state, &level) ||
            !(o = object_find(*(const u32 *)(objects_dirty[i] + REF_ID), state != 0)))
            continue;
        if (o->cell_ptr && o->cell == cell && object_same(state, level, o))
            continue;
        if (o->cell != cell)
            o->cell_ptr = 0;
        o->cell = cell;
        o->state = state;
        o->level = level;
        o->pending = o->applied = 1;
        if (!o->cell_ptr)
            o->cell_ptr = cell_at(cell);
    }
    objects_dirty_count = 0;
    for (;;) {
        for (i = n = 0; i < objects_count && n < OBJECTS_PER_EVENT; i++)
            if (objects[i].pending)
                batch[n++] = &objects[i];
        if (!n)
            break;
        data[0] = (u8)n;
        for (i = 0, p = data + 1; i < n; i++, p += OBJECT_BYTES) {
            put32le(p, batch[i]->refid);
            p[4] = (u8)batch[i]->cell;
            p[5] = (u8)(batch[i]->cell >> 8);
            p[6] = (u8)batch[i]->state;
            p[7] = (u8)batch[i]->level;
        }
        if (!event_queue(EVENT_OBJECTS, data, 1 + n * OBJECT_BYTES))
            break;
        for (i = 0; i < n; i++)
            batch[i]->pending = 0;
        objects_sent += n;
    }
    /* A cell change may have loaded cells, or loaded them again from the data files: the interior
     * (DataHandler +0xAC) or the exterior grid cell (+0xA0, +0xA4) changed. */
    signature = 0;
    if (plausible(handler))
        signature = *(const u32 *)(handler + 0xAC) ^ *(const u32 *)(handler + 0xA0) * 31 ^
                    *(const u32 *)(handler + 0xA4) * 1009;
    if (signature != objects_signature) {
        objects_signature = signature;
        for (i = 0; i < objects_count; i++)
            objects[i].applied = 0;
        objects_pass = spawns_pass = 1;
        spawns_settled = now_us();
    }
    if (!objects_pass)
        return;
    objects_pass = 0;
    for (i = 0; i < objects_count; i++) {
        u8 *ref;
        if (objects[i].applied || objects[i].pending)
            continue;
        if ((ref = object_reference(&objects[i]))) {
            object_apply(ref, &objects[i]);
            objects[i].applied = 1;
        }
    }
}

static void objects_stat(void)
{
    tes3x_log_hex3("net.objects", objects_count, objects_sent, objects_received);
    tes3x_log_hex3("net.objects_apply", objects_applied, objects_full, objects_dirty_lost);
    tes3x_log("net.objects_hook", objects_hooked);
}

/* References made at run time: an item dropped, or an object PlaceAtPC, PlaceItem or a script
 * placed. Every such path marks the new reference modified, so the same hook names it; it has no
 * refid, and the server gives it one. The maker sends it as SPAWN with a token in place of the id,
 * and the server sends it back with an id to every client, the maker too, which knows its own by
 * base object, cell and position. Another console makes the reference when its cell is loaded.
 * Removal (a pickup, Disable) goes to the server as REMOVE; the server keeps removed ones and sends
 * them as SPAWN marked removed, so a console that still has one, from its save or an unloaded
 * cell, removes it. Actors are left out: who runs them needs cell authority. */
#define EVENT_SPAWN 14u  /* id, cell index u16, count u16, position, orientation, item data,
                            base id */
#define EVENT_REMOVE 15u /* count, then ids */
#define SPAWN_BYTES 40u
#define SPAWN_REMOVED 0x8000u /* in the count */
#define SPAWN_DATA 0x4000u    /* in the count: it has item data, whose condition and charge follow */
#define SPAWN_LEVELED 0x2000u /* in the count: a leveled creature; its placeholder's refid follows */
#define SPAWN_SUMMON 0x1000u  /* in the count: a summon, run by the console that made it */
#define SPAWN_COUNT 0x0FFFu
#define SPAWN_ID 32u
#define SPAWNS 256u
#define SPAWN_NEAR 1.0f
#define REMOVES_PER_EVENT ((EVENT_DATA - 1) / 4)
#define ATTACH_ITEM_DATA 6u /* data: count, then +0xC condition, uses or time, +0x10 charge */
#define ITEM_CONDITION 0xC
#define ITEM_CHARGE 0x10
#define CELL_INTERIOR 1u
#define CELL_GRID_X 0x24
#define CELL_GRID_Y 0x28

typedef u8 *(__attribute__((thiscall)) *fn_create_reference)(void *records, void *object,
                                                              const float *position,
                                                              const float *orientation, int insert,
                                                              void *ref);
typedef void(__attribute__((thiscall)) *fn_cell_insert)(void *cell, void *ref);
typedef void *(__attribute__((thiscall)) *fn_cell_part)(void *cell);
typedef void(__attribute__((thiscall)) *fn_attach_scene)(void *handler, void *ref, void *node,
                                                         void *activators, int a4);
typedef u8 *(__cdecl *fn_item_data_new)(void *object);
typedef void(__attribute__((thiscall)) *fn_attach_item_data)(void *ref, void *data);
typedef void(__attribute__((thiscall)) *fn_update_lighting)(void *handler, void *ref);

/* An actor's entry is found by its reference, not its place: actors move. leveled is its
 * placeholder's refid; owner, for a summon, the client that runs it (else the cell's authority). A stale entry is an actor's from before the last WELCOME, kept until the
 * server's replay names it again so the reference is not lost. fresh: the server's creature for
 * the placeholder is a new one, to be made rather than taken from what is linked now. */
static struct spawn {
    u32 sid, token, cell, count, used, leveled, owner;
    float pos[3], rot[3];
    u32 condition, charge; /* raw: an int or a float by the item's type */
    char id[SPAWN_ID];
    u8 *base, *ref, *cell_ptr;
    u32 hold_until;
    u8 send, removed, applied, unresolved, misses, actor, stale, fresh, summon, held;
} spawns[SPAWNS];
static u32 spawns_welcome, spawns_sent, spawns_received, spawns_made, spawns_removed;
static u32 spawns_full, spawn_failures, spawns_logged, spawn_clock, spawns_gone;
static u32 spawns_watched, spawn_tokens;

static int spawn_near(const float *a, const float *b)
{
    u32 i;

    for (i = 0; i < 3; i++)
        if (a[i] - b[i] > SPAWN_NEAR || b[i] - a[i] > SPAWN_NEAR)
            return 0;
    return 1;
}

static int same_id(const char *a, const char *b)
{
    for (; *a && *a == *b; a++, b++)
        ;
    return *a == *b;
}

/* A reference made at run time that is not an actor or a projectile: its cell index, its stack
 * count (SPAWN_DATA with item data, whose condition and charge go to data) and whether it is gone. */
static int spawn_read(const u8 *ref, u32 *cell, u32 *count, u32 *gone, u32 *data)
{
    const u8 *base, *list, *att, *item;
    u32 tag;
    int n;

    if (!plausible(ref) || *(const u32 *)(ref + 4) != TAG_REFR || *(const u32 *)(ref + REF_ID) ||
        !plausible(base = *(const u8 *const *)(ref + REF_BASE)) || ref_mobile(ref))
        return 0;
    tag = *(const u32 *)(base + 4);
    if (tag == TAG_NPC || tag == TAG_CREA || !plausible(list = *(const u8 *const *)(ref + REF_LIST)) ||
        (*cell = cell_index(*(const u8 *const *)(list + 0xC))) == OBJECT_NO_CELL)
        return 0;
    *gone = (*(const u32 *)(ref + REF_FLAGS) & (REF_DELETED | REF_DISABLED)) != 0;
    *count = 1;
    for (att = *(const u8 *const *)(ref + REF_ATTACHMENTS); plausible(att);
         att = *(const u8 *const *)(att + 4))
        if (*(const u32 *)att == ATTACH_ITEM_DATA && plausible(item = *(const u8 *const *)(att + 8))) {
            n = *(const int *)item;
            *count = SPAWN_DATA | (n < 1 ? 1 : n > (int)SPAWN_COUNT ? SPAWN_COUNT : (u32)n);
            data[0] = *(const u32 *)(item + ITEM_CONDITION);
            data[1] = *(const u32 *)(item + ITEM_CHARGE);
            break;
        }
    return 1;
}

static struct spawn *spawn_by_sid(u32 sid)
{
    u32 i;

    for (i = 0; i < SPAWNS; i++)
        if (spawns[i].used && spawns[i].sid == sid)
            return &spawns[i];
    return 0;
}

/* A free entry, else the oldest stale or removed one whose removal is not still to be sent. */
static struct spawn *spawn_slot(void)
{
    struct spawn *pick = 0;
    u32 i;

    for (i = 0; i < SPAWNS; i++) {
        if (!spawns[i].used) {
            pick = &spawns[i];
            break;
        }
        if ((spawns[i].stale || (spawns[i].removed && !(spawns[i].sid && spawns[i].send))) &&
            (!pick || spawns[i].used < pick->used))
            pick = &spawns[i];
    }
    if (!pick) {
        spawns_full++;
        return 0;
    }
    pick->used = ++spawn_clock;
    pick->sid = pick->leveled = pick->owner = 0;
    pick->ref = pick->base = pick->cell_ptr = 0;
    pick->send = pick->removed = pick->applied = pick->unresolved = pick->misses = 0;
    pick->actor = pick->stale = pick->fresh = pick->summon = pick->held = 0;
    return pick;
}

static int spawn_is(const u8 *ref, const struct spawn *s);

/* The entry a reference here stands for: the one that last had it, else one of the same cell, base
 * object and place that has no other reference here. */
static struct spawn *spawn_match(u32 cell, const u8 *base, const float *pos, const u8 *ref)
{
    u32 i;

    for (i = 0; i < SPAWNS; i++)
        if (spawns[i].used && ref && spawns[i].ref == ref && spawn_is(ref, &spawns[i]))
            return &spawns[i];
    for (i = 0; i < SPAWNS; i++)
        if (spawns[i].used && spawns[i].cell == cell && spawns[i].base == base &&
            spawn_near(spawns[i].pos, pos) && !spawn_is(spawns[i].ref, &spawns[i]))
            return &spawns[i];
    return 0;
}

/* Whether another entry has ref. */
static int spawn_claimed(const u8 *ref, const struct spawn *s)
{
    u32 i;

    for (i = 0; i < SPAWNS; i++)
        if (spawns[i].used && &spawns[i] != s && spawns[i].ref == ref)
            return 1;
    return 0;
}

static u8 *resolve_object(const char *id)
{
    const u8 *handler = *(const u8 **)TES3X_NET_DATA_HANDLER;
    void *records;

    if (!plausible(handler) || !plausible(records = *(void **)handler))
        return 0;
    return ((fn_resolve_object)TES3X_NET_RESOLVE_OBJECT)(records, id);
}

/* Names a new spawn to the server apart from another made in the same place; the half from the
 * clock keeps a relaunch from reusing a token the server still holds. */
static u32 spawn_token(void)
{
    u32 token;

    if (!spawn_tokens)
        spawn_tokens = (now_us() & 0xFFFF) << 16 | 1;
    token = spawn_tokens;
    spawn_tokens = (spawn_tokens & 0xFFFF0000u) | ((spawn_tokens + 1) & 0xFFFF);
    return token;
}

static void actor_local(u8 *ref, u32 summon, u32 player);
static void spawn_hold(struct spawn *s, u32 player);

/* Game thread: a reference made or changed here that has no refid; player if a player's own
 * action made it. */
static void spawn_local(u8 *ref, u32 player)
{
    const u8 *base = *(const u8 *const *)(ref + REF_BASE);
    const char *id;
    struct spawn *s;
    u32 cell, count, gone, n, data[2] = {0, 0};

    if (plausible(base) && (*(const u32 *)(base + 4) == TAG_NPC ||
                            *(const u32 *)(base + 4) == TAG_CREA)) {
        actor_local(ref, 0, player);
        return;
    }
    if (!spawn_read(ref, &cell, &count, &gone, data))
        return;
    if ((s = spawn_match(cell, base, (const float *)(ref + REF_POSITION), ref))) {
        s->ref = ref;
        if (gone && !s->removed && !s->sid && s->send)
            s->used = 0; /* never sent: nobody else knows it */
        else if (gone && !s->removed)
            s->removed = s->applied = s->send = 1;
        return;
    }
    id = ((fn_object_id)(*(void *const *const *)base)[OBJECT_GET_ID / 4])(base);
    if (gone || !mapped(id) || !*id || !(s = spawn_slot()))
        return;
    for (n = 0; id[n] && n < SPAWN_ID - 1; n++)
        s->id[n] = id[n];
    s->id[n] = 0;
    s->cell = cell;
    s->count = count;
    s->condition = data[0];
    s->charge = data[1];
    copy((u8 *)s->pos, ref + REF_POSITION, 12);
    copy((u8 *)s->rot, ref + REF_ORIENTATION, 12);
    s->base = (u8 *)base;
    s->ref = ref;
    s->cell_ptr = *(u8 *const *)(*(const u8 *const *)(ref + REF_LIST) + 0xC);
    s->send = s->applied = 1;
    s->token = spawn_token();
    spawn_hold(s, player);
}

/* Whether ref is still the entry's reference. */
static int spawn_is(const u8 *ref, const struct spawn *s)
{
    return plausible(ref) && *(const u32 *)(ref + 4) == TAG_REFR && !*(const u32 *)(ref + REF_ID) &&
           *(u8 *const *)(ref + REF_BASE) == s->base &&
           !(*(const u32 *)(ref + REF_FLAGS) & REF_DELETED) &&
           spawn_near((const float *)(ref + REF_POSITION), s->pos);
}

static u8 *spawn_find(struct spawn *s, u8 *cell)
{
    static const u32 lists[2] = {0x2C, 0x3C};
    const u8 *temporary;
    u8 *ref;
    u32 i;

    if (spawn_is(s->ref, s))
        return s->ref;
    for (i = 0; i < 3; i++) {
        if (i < 2)
            ref = *(u8 **)(cell + lists[i] + LIST_HEAD);
        else if (plausible(temporary = *(const u8 *const *)(cell + CELL_TEMPORARY)))
            ref = *(u8 *const *)(temporary + 8 + LIST_HEAD);
        else
            break;
        for (; plausible(ref); ref = *(u8 **)(ref + REF_NEXT))
            if (spawn_is(ref, s) && !spawn_claimed(ref, s))
                return ref;
    }
    return 0;
}

/* The current interior, or an exterior cell of the player's 3x3. */
static int cell_active(const u8 *cell)
{
    const u8 *handler = *(const u8 *const *)TES3X_NET_DATA_HANDLER;
    int dx, dy;

    if (!plausible(cell) || !plausible(handler) ||
        !(*(const u32 *)(cell + CELL_FLAGS) & CELL_REFS_LOADED))
        return 0;
    if (*(const u32 *)(cell + CELL_FLAGS) & CELL_INTERIOR)
        return *(const u8 *const *)(handler + 0xAC) == cell;
    if (*(const u8 *const *)(handler + 0xAC))
        return 0;
    dx = *(const int *)(cell + CELL_GRID_X) - *(const int *)(handler + 0xA0);
    dy = *(const int *)(cell + CELL_GRID_Y) - *(const int *)(handler + 0xA4);
    return dx >= -1 && dx <= 1 && dy >= -1 && dy <= 1;
}

/* The reference as the leveled creature spawn makes one: made, put in its cell, given its stack
 * and attached to the cell's scene, as DropItem does. */
static u8 *spawn_make(struct spawn *s, u8 *cell)
{
    u8 *handler = *(u8 **)TES3X_NET_DATA_HANDLER, *ref, *data;

    object_applying = 1;
    ref = ((fn_create_reference)TES3X_NET_CREATE_REFERENCE)(*(void **)handler, s->base, s->pos,
                                                             s->rot, 0, 0);
    if (plausible(ref)) {
        ((fn_cell_insert)TES3X_NET_CELL_INSERT)(cell, ref);
        if ((s->count & SPAWN_DATA) &&
            plausible(data = ((fn_item_data_new)TES3X_NET_ITEM_DATA_NEW)(s->base))) {
            *(int *)data = (int)(s->count & SPAWN_COUNT);
            *(u32 *)(data + ITEM_CONDITION) = s->condition;
            *(u32 *)(data + ITEM_CHARGE) = s->charge;
            ((fn_attach_item_data)TES3X_NET_ATTACH_ITEM_DATA)(ref, data);
        }
        ((fn_attach_scene)TES3X_NET_ATTACH_SCENE)(
            handler, ref, ((fn_cell_part)TES3X_NET_CELL_NODE)(cell),
            ((fn_cell_part)TES3X_NET_CELL_ACTIVATORS)(cell), 0);
        ((fn_set_modified)TES3X_NET_REF_MODIFIED)(ref, 1);
        ((fn_update_lighting)TES3X_NET_UPDATE_LIGHTING)(handler, ref);
        spawns_made++;
    } else {
        ref = 0;
        spawn_failures++;
    }
    object_applying = 0;
    return ref;
}

/* As a pickup: disabled and deleted. */
static void spawn_remove(u8 *ref)
{
    object_applying = 1;
    run_script_on("Disable", ref);
    *(u32 *)(ref + REF_FLAGS) |= REF_DELETED;
    ((fn_set_modified)TES3X_NET_REF_MODIFIED)(ref, 1);
    object_applying = 0;
    spawns_removed++;
}

static const char *object_id(const u8 *object);

/* Leveled creatures. The engine rolls a placeholder's list when the placeholder enters the scene
 * with no creature linked to it (LeveledCreature's spawn, through its vtable), makes the creature
 * and links the two both ways. While joined only the placeholder cell's authority rolls: another
 * console runs the spawn with the list's chance of none at 100, so it links nothing, when the
 * server already has a creature for the placeholder or another client runs the cell. Four times a
 * second each console looks at the placeholders of its active cells: one the server has a
 * creature for gets that creature (the one linked, if it is the same object, else a new one); the
 * authority sends what it rolled, or what its save linked, as a SPAWN that names the placeholder.
 * The server keeps the first creature for a placeholder, answers a later roll with it, and takes
 * a new one only once the old one is dead. */
#define TAG_LEVC 0x4356454Cu
#define LEVELED_CHANCE_NONE 0x40 /* signed byte, percent */
#define WORLD_MOBS 0x5C
#define ACTOR_INSTANCE_BASE 0x6C
#define CELL_NAME_PTR 0x14
#define LEVELED_NONE 32u
#define LEVELED_TRIES 3u
#define STALE_US 5000000u /* after WELCOME: long enough for the server's replay */

typedef u8 *(__attribute__((thiscall)) *fn_leveled_spawn)(void *list, void *placeholder);
typedef u8 *(__attribute__((thiscall)) *fn_leveled_resolve)(void *list);
typedef void(__attribute__((thiscall)) *fn_link)(void *ref, void *other);
typedef void(__attribute__((thiscall)) *fn_add_mob)(void *mobs, void *ref);

static u32 leveled_hooked, leveled_withheld, leveled_rolled, leveled_made, leveled_adopted;
static u32 leveled_replaced, actor_failures, spawns_welcomed;
static u32 leveled_none[LEVELED_NONE], leveled_none_count, leveled_none_epoch;

static struct spawn *spawn_leveled(u32 placeholder)
{
    u32 i;

    for (i = 0; i < SPAWNS; i++)
        if (spawns[i].used && spawns[i].leveled == placeholder)
            return &spawns[i];
    return 0;
}

/* The object an actor was made from. A mobile gives its reference a copy of the object, named
 * with a number after the object's id, which keeps the object at +0x6C as a container instance
 * does. */
static u8 *actor_base(u8 *object)
{
    u8 *base;
    const char *a, *b;

    if (!plausible(object) || !plausible(base = *(u8 **)(object + ACTOR_INSTANCE_BASE)) ||
        base == object || *(const u32 *)(base + 4) != *(const u32 *)(object + 4) ||
        !(a = object_id(object)) || !(b = object_id(base)))
        return object;
    for (; *b && *a == *b; a++, b++)
        ;
    return *b ? object : base;
}

/* Whether ref is still an actor entry's reference: its object, and not deleted. */
static int actor_is(const u8 *ref, const struct spawn *s)
{
    return plausible(ref) && *(const u32 *)(ref + 4) == TAG_REFR && !*(const u32 *)(ref + REF_ID) &&
           actor_base(*(u8 *const *)(ref + REF_BASE)) == s->base &&
           !(*(const u32 *)(ref + REF_FLAGS) & REF_DELETED);
}

/* An actor as the other consoles name it: its refid, or the server's id for one made at run time;
 * 0 if it has neither. */
static u32 actor_id(const u8 *ref)
{
    u32 i, refid;

    if (!plausible(ref))
        return 0;
    if ((refid = *(const u32 *)(ref + REF_ID)))
        return refid;
    for (i = 0; i < SPAWNS; i++)
        if (spawns[i].used && spawns[i].actor && spawns[i].sid && !spawns[i].stale &&
            !spawns[i].removed && spawns[i].ref == ref)
            return spawns[i].sid;
    return 0;
}

static void cell_key_of(const u8 *cell, struct cell_key *k)
{
    const char *name = 0;
    u32 i;

    k->kind = *(const u32 *)(cell + CELL_FLAGS) & CELL_INTERIOR ? KEY_INTERIOR : KEY_EXTERIOR;
    k->gx = k->kind == KEY_EXTERIOR ? *(const int *)(cell + CELL_GRID_X) : 0;
    k->gy = k->kind == KEY_EXTERIOR ? *(const int *)(cell + CELL_GRID_Y) : 0;
    if (k->kind == KEY_INTERIOR)
        name = *(const char *const *)(cell + CELL_NAME_PTR);
    for (i = 0; i < CELL_NAME; i++)
        k->name[i] = 0;
    for (i = 0; mapped(name) && name[i] && i < CELL_NAME - 1; i++)
        k->name[i] = (u8)name[i];
}

/* An interior's whole name from STATE's, which keeps CELL_NAME - 1 characters: the first interior
 * that begins with them. A name that was not cut is returned as it is. */
static const char *interior_name(const u8 *cut)
{
    const u8 *node, *cell;
    const char *name;
    u32 i, guard;

    for (i = 0; i < CELL_NAME - 1 && cut[i]; i++)
        ;
    if (i < CELL_NAME - 1)
        return (const char *)cut;
    for (node = cells_head(), guard = 0; plausible(node) && guard < 65536;
         node = *(const u8 *const *)(node + 8), guard++) {
        if (!plausible(cell = *(const u8 *const *)node) ||
            !(*(const u32 *)(cell + CELL_FLAGS) & CELL_INTERIOR) ||
            !mapped(name = *(const char *const *)(cell + CELL_NAME_PTR)))
            continue;
        for (i = 0; i < CELL_NAME - 1 && name[i] == (char)cut[i]; i++)
            ;
        if (i == CELL_NAME - 1)
            return name;
    }
    return (const char *)cut;
}

static u32 cell_authority(const u8 *cell)
{
    struct cell_key k;

    if (!plausible(cell))
        return 0;
    cell_key_of(cell, &k);
    return authority_of(&k);
}

static u8 *leveled_linked(const u8 *placeholder)
{
    u8 *linked = ((fn_ref_part)TES3X_NET_LEVELED_LINKED)(placeholder);
    return plausible(linked) ? linked : 0;
}

/* Whether this console leaves the placeholder's roll to another. */
static int leveled_withhold(const u8 *placeholder)
{
    const u8 *list;
    u32 refid, owner;

    if (ses.state != SESSION_JOINED || !(refid = *(const u32 *)(placeholder + REF_ID)) ||
        leveled_linked(placeholder))
        return 0;
    if (spawn_leveled(refid))
        return 1;
    list = *(const u8 *const *)(placeholder + REF_LIST);
    owner = plausible(list) ? cell_authority(*(const u8 *const *)(list + 0xC)) : 0;
    return owner && owner != ses.client;
}

static u8 *__attribute__((thiscall)) leveled_spawn_hook(u8 *list, u8 *placeholder)
{
    u8 *node;
    char chance;

    if (!plausible(placeholder) || !leveled_withhold(placeholder))
        return ((fn_leveled_spawn)TES3X_NET_LEVELED_SPAWN)(list, placeholder);
    chance = (char)list[LEVELED_CHANCE_NONE];
    list[LEVELED_CHANCE_NONE] = 100;
    node = ((fn_leveled_spawn)TES3X_NET_LEVELED_SPAWN)(list, placeholder);
    list[LEVELED_CHANCE_NONE] = (u8)chance;
    leveled_withheld++;
    return node;
}

/* Points a vtable slot that holds expected at hook; 0 if it holds something else. */
static int swap_slot(u32 *slot, u32 expected, const void *hook)
{
    u32 cr0, flags;

    if (*slot != expected) {
        tes3x_log_hex3("net.slot_unexpected", (u32)slot, *slot, expected);
        return 0;
    }
    flags = lock();
    __asm__ volatile("movl %%cr0, %0" : "=r"(cr0));
    __asm__ volatile("movl %0, %%cr0" : : "r"(cr0 & ~CR0_WP) : "memory");
    *slot = (u32)hook;
    __asm__ volatile("movl %0, %%cr0" : : "r"(cr0) : "memory");
    unlock(flags);
    return 1;
}

static void leveled_hook_install(void)
{
    if (leveled_hooked)
        return;
    leveled_hooked = 1 + swap_slot((u32 *)TES3X_NET_LEVELED_SPAWN_SLOT, TES3X_NET_LEVELED_SPAWN,
                                   (const void *)leveled_spawn_hook);
}

/* An actor made as the leveled spawn makes one, linked to its placeholder when it has one, then
 * given its mobile as PlaceAtPC does, since no cell load will. */
static u8 *actor_make(struct spawn *s, u8 *cell, u8 *placeholder)
{
    u8 *handler = *(u8 **)TES3X_NET_DATA_HANDLER, *world = *(u8 **)TES3X_NET_WORLD, *mobs, *ref;
    u8 *mobile;

    if (!plausible(world) || !plausible(mobs = *(u8 **)(world + WORLD_MOBS)) ||
        !plausible(handler)) {
        actor_failures++;
        return 0;
    }
    object_applying = 1;
    ref = ((fn_create_reference)TES3X_NET_CREATE_REFERENCE)(*(void **)handler, s->base, s->pos,
                                                             s->rot, 0, 0);
    if (plausible(ref)) {
        ((fn_cell_insert)TES3X_NET_CELL_INSERT)(cell, ref);
        ((fn_attach_scene)TES3X_NET_ATTACH_SCENE)(
            handler, ref, ((fn_cell_part)TES3X_NET_CELL_NODE)(cell),
            ((fn_cell_part)TES3X_NET_CELL_ACTIVATORS)(cell), 0);
        if (placeholder) {
            ((fn_link)TES3X_NET_LEVELED_LINK)(ref, placeholder);
            ((fn_link)TES3X_NET_LEVELED_LINK)(placeholder, ref);
            ((fn_set_modified)TES3X_NET_REF_MODIFIED)(placeholder, 1);
        }
        ((fn_set_modified)TES3X_NET_REF_MODIFIED)(ref, 1);
        ((fn_add_mob)TES3X_NET_ADD_MOB)(mobs, ref);
        /* Into the simulation or out of it by its distance, as PlaceAtPC does after addMob. */
        if (plausible(mobile = ref_mobile(ref)))
            ((fn_mobile_call)TES3X_NET_SIMULATE)(mobile);
    } else {
        ref = 0;
        actor_failures++;
    }
    object_applying = 0;
    return ref;
}

static void spawn_name(struct spawn *s, const u8 *object)
{
    const char *id = object_id(object);
    u32 n;

    for (n = 0; id && id[n] && n < SPAWN_ID - 1; n++)
        s->id[n] = id[n];
    s->id[n] = 0;
}

static int leveled_none_has(u32 refid)
{
    u32 i;

    for (i = 0; i < leveled_none_count; i++)
        if (leveled_none[i] == refid)
            return 1;
    return 0;
}

/* The scan's look at one placeholder of an active cell. */
static void leveled_seen(u8 *placeholder, u32 index, u8 *cell)
{
    u32 refid = *(const u32 *)(placeholder + REF_ID);
    u8 *linked = leveled_linked(placeholder), *object;
    struct spawn *s = spawn_leveled(refid);
    const u8 *from;

    if (!refid || (s && s->stale))
        return;
    if (s && s->sid) {
        if (linked && actor_is(linked, s) && (linked == s->ref || !s->fresh)) {
            if (linked != s->ref) {
                leveled_adopted++;
                tes3x_log_hex3("net.leveled_adopted", s->sid, refid, (u32)linked);
            }
            s->ref = linked;
            s->applied = 1;
            s->fresh = 0;
            return;
        }
        if ((s->applied && actor_is(s->ref, s)) || s->unresolved || s->misses >= LEVELED_TRIES)
            return;
        /* Disabled, not deleted: the AI may still hold a deleted actor's mobile. */
        if (linked) {
            object_applying = 1;
            run_script_on("Disable", linked);
            object_applying = 0;
            leveled_replaced++;
        }
        s->misses++;
        if ((s->ref = actor_make(s, cell, placeholder))) {
            s->applied = 1;
            s->fresh = 0;
            leveled_made++;
        }
        tes3x_log_hex3("net.leveled_made", s->sid, refid, (u32)s->ref);
        return;
    }
    if (s || cell_authority(cell) != ses.client || (!linked && leveled_none_has(refid)))
        return;
    if (!linked) {
        object = ((fn_leveled_resolve)TES3X_NET_LEVELED_RESOLVE)(*(u8 **)(placeholder + REF_BASE));
        if (!plausible(object)) {
            if (leveled_none_count < LEVELED_NONE)
                leveled_none[leveled_none_count++] = refid;
            return;
        }
    } else {
        object = actor_base(*(u8 **)(linked + REF_BASE));
    }
    if (!(s = spawn_slot()))
        return;
    from = linked ? linked : placeholder;
    s->leveled = refid;
    s->actor = 1;
    s->base = object;
    s->cell = index;
    s->count = 1;
    s->condition = s->charge = 0;
    copy((u8 *)s->pos, from + REF_POSITION, 12);
    copy((u8 *)s->rot, from + REF_ORIENTATION, 12);
    spawn_name(s, object);
    if (!linked) {
        if (!(linked = actor_make(s, cell, placeholder))) {
            s->used = 0;
            return;
        }
        leveled_rolled++;
    }
    s->ref = linked;
    s->cell_ptr = cell;
    s->send = s->applied = 1;
    s->token = spawn_token();
    tes3x_log_hex3("net.leveled_rolled", refid, (u32)linked, index);
    log_text("net.leveled_id", s->id);
}

/* The server's creature for a placeholder, or its removal once a new one replaced it. */
static void leveled_event(u32 sid, u32 leveled, u32 cell, u32 count, const u8 *p, const char *id)
{
    struct spawn *s = spawn_by_sid(sid);

    if (count & SPAWN_REMOVED) {
        if (s)
            s->used = 0;
        return;
    }
    if (!s)
        s = spawn_leveled(leveled);
    if (!s && !(s = spawn_slot()))
        return;
    if ((s->sid && s->sid != sid) || (s->used && s->base && !same_id(s->id, id)))
        s->fresh = 1; /* a respawn, or another console's roll in place of ours */
    if (!s->base || !same_id(s->id, id)) {
        copy((u8 *)s->id, (const u8 *)id, SPAWN_ID);
        s->unresolved = !(s->base = resolve_object(id));
    }
    if (s->fresh)
        s->applied = s->misses = 0;
    s->sid = sid;
    s->leveled = leveled;
    s->actor = 1;
    s->stale = s->send = s->removed = 0;
    s->cell = cell;
    s->cell_ptr = 0;
    s->count = count & SPAWN_COUNT;
    copy((u8 *)s->pos, p + 8, 24);
    if (spawns_logged < 32) {
        spawns_logged++;
        log_text("net.leveled_spawn_id", id);
        tes3x_log_hex3("net.leveled_spawn", sid, leveled, s->fresh);
    }
}

/* Actors made at run time other than leveled creatures: what PlaceAtPC, PlaceAtMe, a script or a
 * summon makes. The maker sends it as SPAWN; the others make it when its cell is active, or take
 * the one of the same object in that cell after a relaunch. The cell's authority runs it, and a
 * summon its maker, whose copy's end (disabled or deleted, as a summon's end and Disable leave
 * it) goes out as REMOVE. */
typedef void(__cdecl *fn_summon)(void *instance, void *data, int index, const char *id);

static const u32 summon_sites[] = TES3X_NET_SUMMON_SITES;
static u32 summon_hooked, summons_sent, actors_made, actors_removed;

static int actor_object(const u8 *object)
{
    return plausible(object) &&
           (*(const u32 *)(object + 4) == TAG_NPC || *(const u32 *)(object + 4) == TAG_CREA);
}

/* Scripts run on every console that has their reference loaded, and global ones everywhere, so a
 * spawn a script made here goes out at once only from the console that runs its cell, or for a
 * cell nobody runs the lowest client. The others hold theirs HOLD_US for that console's copy,
 * which then takes theirs over, and send it themselves if none comes: a script may run here only.
 * A player's own action (the console, a dialogue result, a drop) goes out at once. */
#define HOLD_US 5000000u
#define HOLD_NEAR 512.0f

typedef u32(__attribute__((thiscall)) *fn_drop)(void *mobile, void *item, void *data, int count,
                                                 int flag);

static const u32 player_script_sites[] = TES3X_NET_PLAYER_SCRIPT_SITES;
static const u32 player_drop_sites[] = TES3X_NET_PLAYER_DROP_SITES;
static u32 player_hooked, spawns_held, spawns_taken_over, spawns_released;

static int lowest_client(void)
{
    u32 i;

    for (i = 0; i < PEERS; i++)
        if (peers[i].client && peers[i].client < ses.client)
            return 0;
    return 1;
}

static void spawn_hold(struct spawn *s, u32 player)
{
    u32 owner;

    if (player)
        return;
    owner = cell_authority(s->cell_ptr);
    if (owner ? owner == ses.client : lowest_client())
        return;
    s->held = 1;
    s->hold_until = now_us() + HOLD_US;
    spawns_held++;
}

/* A held spawn of ours that an incoming one of the same object in the same cell stands for. */
static int spawn_takes_over(const struct spawn *s, u32 cell, const char *id, const float *pos)
{
    const float *at = s->pos;
    float dx = at[0] - pos[0], dy = at[1] - pos[1];

    return s->used && s->held && !s->sid && s->cell == cell && same_id(s->id, id) &&
           dx * dx + dy * dy <= HOLD_NEAR * HOLD_NEAR;
}

static int __attribute__((thiscall)) player_script_hook(void *self, void *scratch, const char *text,
                                                       int a2, int ref, int a4, int a5, int a6)
{
    int r;

    player_acting++;
    r = ((fn_compile_run)TES3X_NET_COMPILE_RUN)(self, scratch, text, a2, ref, a4, a5, a6);
    player_acting--;
    return r;
}

static u32 __attribute__((thiscall)) player_drop_hook(void *mobile, void *item, void *data,
                                                      int count, int flag)
{
    u32 r;

    player_acting++;
    r = ((fn_drop)TES3X_NET_DROP_ITEM)(mobile, item, data, count, flag);
    player_acting--;
    return r;
}

static void player_hook_install(void)
{
    if (player_hooked)
        return;
    player_hooked = 1;
    if (redirect_calls(player_script_sites,
                       sizeof(player_script_sites) / sizeof(player_script_sites[0]),
                       TES3X_NET_COMPILE_RUN, (const void *)player_script_hook))
        player_hooked |= 2;
    if (redirect_calls(player_drop_sites, sizeof(player_drop_sites) / sizeof(player_drop_sites[0]),
                       TES3X_NET_DROP_ITEM, (const void *)player_drop_hook))
        player_hooked |= 4;
}

static u32 actor_owner(u32 id, u32 cell_owner)
{
    struct spawn *s;
    u32 i;

    if ((id & 0xFF000000u) == 0xFF000000u && (s = spawn_by_sid(id)) && s->owner)
        return s->owner;
    for (i = 0; i < owner_count; i++)
        if (owners[i].id == id)
            return owners[i].client;
    return cell_owner;
}

/* Game thread: an actor made here with no refid, not linked to a leveled placeholder. */
static void actor_local(u8 *ref, u32 summon, u32 player)
{
    const u8 *list;
    u8 *object;
    struct spawn *s;
    u32 i, cell;

    if (ses.state != SESSION_JOINED || !plausible(ref) || *(const u32 *)(ref + 4) != TAG_REFR ||
        *(const u32 *)(ref + REF_ID) || is_ghost(ref) || leveled_linked(ref) ||
        (*(const u32 *)(ref + REF_FLAGS) & (REF_DELETED | REF_DISABLED)) ||
        !actor_object(object = actor_base(*(u8 **)(ref + REF_BASE))) ||
        !plausible(list = *(const u8 *const *)(ref + REF_LIST)) ||
        (cell = cell_index(*(const u8 *const *)(list + 0xC))) == OBJECT_NO_CELL)
        return;
    for (i = 0; i < SPAWNS; i++)
        if (spawns[i].used && spawns[i].actor && spawns[i].ref == ref)
            return;
    if (!(s = spawn_slot()))
        return;
    s->actor = 1;
    s->summon = (u8)summon;
    s->owner = summon ? ses.client : 0;
    s->base = object;
    s->ref = ref;
    s->cell = cell;
    s->cell_ptr = *(u8 *const *)(list + 0xC);
    s->count = 1;
    s->condition = s->charge = 0;
    copy((u8 *)s->pos, ref + REF_POSITION, 12);
    copy((u8 *)s->rot, ref + REF_ORIENTATION, 12);
    spawn_name(s, object);
    s->send = s->applied = 1;
    s->token = spawn_token();
    summons_sent += summon;
    spawn_hold(s, summon || player);
    log_text("net.actor_spawn_sent", s->id);
    tes3x_log_hex3("net.actor_spawn_at", cell, summon | s->held << 1, (u32)ref);
}

/* The creature is the actor the call marks modified, as every run-time creation does. */
static void __cdecl summon_hook(u8 *instance, void *data, int index, const char *id)
{
    const u8 *caster = plausible(instance) ? *(const u8 *const *)(instance + INSTANCE_CASTER) : 0;
    u32 own, i, before = objects_dirty_count;

    ((fn_summon)TES3X_NET_SUMMON)(instance, data, index, id);
    if (ses.state != SESSION_JOINED || !plausible(caster) || ref_owner(caster, &own))
        return;
    for (i = before; i < objects_dirty_count; i++)
        actor_local(objects_dirty[i], 1, 1);
}

static void summon_hook_install(void)
{
    if (summon_hooked)
        return;
    summon_hooked = 1 + redirect_calls(summon_sites, sizeof(summon_sites) / sizeof(summon_sites[0]),
                                       TES3X_NET_SUMMON, (const void *)summon_hook);
}

/* One of a cell's actors made at run time with the entry's object that no entry has. */
static u8 *actor_find(struct spawn *s, u8 *cell)
{
    static const u32 lists[2] = {0x2C, 0x3C};
    const u8 *temporary;
    u8 *ref;
    u32 i, k;

    for (i = 0; i < 3; i++) {
        if (i < 2)
            ref = *(u8 **)(cell + lists[i] + LIST_HEAD);
        else if (plausible(temporary = *(const u8 *const *)(cell + CELL_TEMPORARY)))
            ref = *(u8 *const *)(temporary + 8 + LIST_HEAD);
        else
            break;
        for (; plausible(ref); ref = *(u8 **)(ref + REF_NEXT)) {
            if (!actor_is(ref, s) || (*(const u32 *)(ref + REF_FLAGS) & REF_DISABLED) ||
                leveled_linked(ref))
                continue;
            for (k = 0; k < SPAWNS && !(spawns[k].used && spawns[k].ref == ref); k++)
                ;
            if (k == SPAWNS)
                return ref;
        }
    }
    return 0;
}

static void actor_disable(u8 *ref)
{
    object_applying = 1;
    run_script_on("Disable", ref);
    object_applying = 0;
    actors_removed++;
}

/* Game thread: the server's actor, made here once its cell is active. */
static void actor_apply(struct spawn *s)
{
    u8 *cell, *ref;

    if (!s->used || !s->sid || s->stale || s->applied || s->unresolved)
        return;
    if (!s->cell_ptr)
        s->cell_ptr = cell_at(s->cell);
    if (!cell_active(cell = s->cell_ptr))
        return;
    if (s->removed) {
        if (actor_is(s->ref, s))
            actor_disable(s->ref);
        s->used = 0;
        return;
    }
    if (!actor_is(s->ref, s) && !(s->ref = actor_find(s, cell))) {
        if (s->misses >= LEVELED_TRIES)
            return;
        s->misses++;
        if ((s->ref = actor_make(s, cell, 0)))
            actors_made++;
        tes3x_log_hex3("net.actor_made", s->sid, (u32)s->ref, s->owner);
    }
    s->applied = s->ref != 0;
}

/* Every SPAWN_WATCH_US: an actor this console runs that is gone goes out as REMOVE. */
static void actor_watch(struct spawn *s)
{
    const u8 *ref = s->ref;
    u32 runs;

    if (!s->sid || s->stale || !s->applied)
        return;
    runs = actor_owner(s->sid, cell_authority(s->cell_ptr)) == ses.client;
    if (!runs || (plausible(ref) && *(const u32 *)(ref + 4) == TAG_REFR &&
                  !(*(const u32 *)(ref + REF_FLAGS) & (REF_DELETED | REF_DISABLED))))
        return;
    s->removed = s->send = 1;
    tes3x_log_hex3("net.actor_gone", s->sid, s->owner, (u32)ref);
}

static void actor_event(u32 sid, u32 cell, u32 count, const u8 *p, const char *id, u32 origin)
{
    struct spawn *s = spawn_by_sid(sid);
    float pos[3];
    u32 i;

    copy((u8 *)pos, p + 8, 12);
    for (i = 0; i < SPAWNS && !s; i++) /* our own, back with its id, or held for this one */
        if ((spawns[i].used && spawns[i].actor && !spawns[i].sid && !spawns[i].leveled &&
             spawns[i].cell == cell && same_id(spawns[i].id, id) &&
             spawn_near(spawns[i].pos, pos)) ||
            (spawns[i].actor && spawn_takes_over(&spawns[i], cell, id, pos)))
            s = &spawns[i];
    if (s && s->held) {
        s->held = 0;
        spawns_taken_over++;
        tes3x_log_hex3("net.spawn_taken_over", sid, origin, (u32)s->ref);
    }
    if (count & SPAWN_REMOVED) {
        if (s && s->sid) {
            s->removed = 1;
            s->applied = 0;
        } else if (s) {
            s->used = 0;
        }
        return;
    }
    if (!s) {
        if (!(s = spawn_slot()))
            return;
        s->actor = 1;
        s->cell = cell;
        copy((u8 *)s->pos, p + 8, 24);
        copy((u8 *)s->id, (const u8 *)id, SPAWN_ID);
        s->unresolved = !(s->base = resolve_object(id));
    }
    s->sid = sid;
    s->stale = s->send = 0;
    s->summon = (count & SPAWN_SUMMON) != 0;
    s->owner = s->summon ? origin : 0;
    s->count = count & SPAWN_COUNT;
    spawns_pass = 1;
    if (spawns_logged < 32) {
        spawns_logged++;
        log_text("net.actor_spawn_id", id);
        tes3x_log_hex3("net.actor_spawn", sid, origin, count);
    }
}

static void spawn_event(const struct event *e)
{
    const u8 *p = e->data;
    struct spawn *s;
    char id[SPAWN_ID];
    u32 sid, cell, count, n, i, head = SPAWN_BYTES, leveled = 0;
    float pos[3];

    if (e->length < SPAWN_BYTES + 2)
        return;
    sid = get32le(p);
    cell = p[4] | (u32)p[5] << 8;
    count = p[6] | (u32)p[7] << 8;
    if (!float_within(p + 8, 3, POSITION_LIMIT) || !float_within(p + 20, 3, ANGLE_LIMIT)) {
        refused_events++;
        return;
    }
    copy((u8 *)pos, p + 8, 12);
    if (count & SPAWN_LEVELED) {
        if (e->length < SPAWN_BYTES + 6)
            return;
        leveled = get32le(p + SPAWN_BYTES);
        head += 4;
    }
    for (n = 0; n < SPAWN_ID - 1 && head + n < e->length && p[head + n]; n++)
        id[n] = (char)p[head + n];
    id[n] = 0;
    spawns_received++;
    if (!sid)
        return;
    if (leveled) {
        leveled_event(sid, leveled, cell, count, p, id);
        return;
    }
    if ((count & SPAWN_SUMMON) || actor_object(resolve_object(id))) {
        actor_event(sid, cell, count, p, id, e->origin);
        return;
    }
    if (!(s = spawn_by_sid(sid)))
        for (i = 0; i < SPAWNS && !s; i++) /* our own, back with its id, or held for this one */
            if ((spawns[i].used && !spawns[i].sid && spawns[i].cell == cell &&
                 same_id(spawns[i].id, id) && spawn_near(spawns[i].pos, pos)) ||
                (!spawns[i].actor && spawn_takes_over(&spawns[i], cell, id, pos)))
                s = &spawns[i];
    if (s && !s->sid) {
        if (s->held) {
            s->held = 0;
            spawns_taken_over++;
            tes3x_log_hex3("net.spawn_taken_over", sid, e->origin, (u32)s->ref);
        }
        s->sid = sid;
        s->send = s->removed; /* a removal waiting for the id */
    } else if (!s) {
        if (!(s = spawn_slot()))
            return;
        s->sid = sid;
        s->cell = cell;
        s->count = count & ~SPAWN_REMOVED;
        copy((u8 *)s->pos, p + 8, 24);
        s->condition = get32le(p + 32);
        s->charge = get32le(p + 36);
        copy((u8 *)s->id, (const u8 *)id, SPAWN_ID);
        if (!(s->base = resolve_object(id)))
            s->unresolved = 1;
    }
    if ((count & SPAWN_REMOVED) && !s->removed) {
        s->removed = 1;
        s->applied = 0;
    }
    spawns_pass = 1;
    if (spawns_logged < 32) {
        spawns_logged++;
        log_text("net.spawn_id", id);
        tes3x_log_hex3("net.spawn", sid, e->origin, cell << 16 | count);
    }
}

static void spawns_session(void)
{
    u32 i;

    if (spawns_welcome == ses.welcomes)
        return;
    spawns_welcome = ses.welcomes;
    spawns_welcomed = now_us();
    /* The server sends what it knows again; what it has not answered yet goes again. */
    for (i = 0; i < SPAWNS; i++) {
        if (spawns[i].used && spawns[i].sid && spawns[i].actor)
            spawns[i].stale = 1;
        else if (spawns[i].used && spawns[i].sid)
            spawns[i].used = 0;
        else if (spawns[i].used)
            spawns[i].send = 1;
    }
}

/* A pickup deletes a reference made at run time outright, without marking it, so each one this
 * console has in an active cell is looked for now and then: missing twice, it is gone. Not while
 * cells may still be loading their references. */
#define SPAWN_WATCH_US 250000u
#define SPAWN_SETTLE_US 2000000u
#define SPAWN_MISSES 2u

static void spawns_watch(void)
{
    struct spawn *s;
    u8 *ref;
    u32 i, now = now_us();

    if (now - spawns_watched < SPAWN_WATCH_US || now - spawns_settled < SPAWN_SETTLE_US)
        return;
    spawns_watched = now;
    for (i = 0; i < SPAWNS; i++) {
        s = &spawns[i];
        if (s->used && s->actor && !s->leveled && !s->removed) {
            actor_watch(s);
            continue;
        }
        if (!s->used || s->actor || s->removed || !s->applied || !s->ref ||
            !cell_active(s->cell_ptr))
            continue;
        if (spawn_is(s->ref, s) || (ref = spawn_find(s, s->cell_ptr))) {
            if (!spawn_is(s->ref, s))
                s->ref = ref;
            s->misses = 0;
            continue;
        }
        if (++s->misses < SPAWN_MISSES)
            continue;
        spawns_gone++;
        tes3x_log_hex3("net.spawn_gone", s->sid, s->cell, s->send);
        if (!s->sid && s->send)
            s->used = 0; /* never sent: nobody else knows it */
        else
            s->removed = s->send = 1;
    }
}

/* Game thread, in the world, after the frame's events and objects. */
static void spawns_frame(void)
{
    u8 data[EVENT_DATA], *cell, *ref;
    struct spawn *s, *batch[REMOVES_PER_EVENT];
    u32 i, n, head, count;

    if (ses.state != SESSION_JOINED)
        return;
    spawns_watch();
    /* Actors the server's replay did not name again. */
    for (i = 0; i < SPAWNS && now_us() - spawns_welcomed > STALE_US; i++)
        if (spawns[i].used && spawns[i].stale)
            spawns[i].used = 0;
    for (i = 0; i < SPAWNS; i++) {
        s = &spawns[i];
        if (!s->used || !s->send || s->sid || s->removed)
            continue;
        if (s->held) {
            if ((int)(now_us() - s->hold_until) < 0)
                continue;
            s->held = 0; /* no other console made it */
            spawns_released++;
        }
        count = s->count | (s->leveled ? SPAWN_LEVELED : 0) | (s->summon ? SPAWN_SUMMON : 0);
        put32le(data, s->token);
        data[4] = (u8)s->cell;
        data[5] = (u8)(s->cell >> 8);
        data[6] = (u8)count;
        data[7] = (u8)(count >> 8);
        copy(data + 8, (const u8 *)s->pos, 24);
        put32le(data + 32, s->condition);
        put32le(data + 36, s->charge);
        head = SPAWN_BYTES;
        if (s->leveled) {
            put32le(data + head, s->leveled);
            head += 4;
        }
        for (n = 0; s->id[n]; n++)
            data[head + n] = (u8)s->id[n];
        data[head + n] = 0;
        if (!event_queue(EVENT_SPAWN, data, head + n + 1))
            break;
        s->send = 0;
        spawns_sent++;
        if (spawns_logged < 32) {
            spawns_logged++;
            log_text("net.spawn_sent", s->id);
            tes3x_log_hex3("net.spawn_at", s->cell, s->count, s->condition);
        }
    }
    for (;;) {
        for (i = n = 0; i < SPAWNS && n < REMOVES_PER_EVENT; i++)
            if (spawns[i].used && spawns[i].removed && spawns[i].send && spawns[i].sid)
                batch[n++] = &spawns[i];
        if (!n)
            break;
        data[0] = (u8)n;
        for (i = 0; i < n; i++)
            put32le(data + 1 + 4 * i, batch[i]->sid);
        if (!event_queue(EVENT_REMOVE, data, 1 + 4 * n))
            break;
        for (i = 0; i < n; i++)
            batch[i]->used = 0;
        tes3x_log_hex3("net.spawn_remove_sent", n, batch[0]->sid, 0);
    }
    if (!spawns_pass)
        return;
    spawns_pass = 0;
    for (i = 0; i < SPAWNS; i++) {
        s = &spawns[i];
        if (s->actor && !s->leveled) {
            actor_apply(s);
            continue;
        }
        if (!s->used || !s->sid || s->applied || s->unresolved || s->actor)
            continue;
        if (!s->cell_ptr)
            s->cell_ptr = cell_at(s->cell);
        if (!cell_active(cell = s->cell_ptr))
            continue;
        ref = spawn_find(s, cell);
        if (s->removed) {
            if (ref)
                spawn_remove(ref);
            s->used = 0;
            continue;
        }
        s->ref = ref ? ref : spawn_make(s, cell);
        s->applied = 1;
        if (!ref && spawns_logged < 32) {
            spawns_logged++;
            tes3x_log_hex3("net.spawn_made", s->sid, (u32)s->ref, s->cell);
            if (s->count & SPAWN_DATA)
                tes3x_log_hex3("net.spawn_data", s->count & SPAWN_COUNT, s->condition, s->charge);
        }
    }
}

static void spawns_stat(void)
{
    u32 i, n = 0;

    for (i = 0; i < SPAWNS; i++)
        n += spawns[i].used != 0;
    tes3x_log_hex3("net.spawns", n, spawns_sent, spawns_received);
    tes3x_log_hex3("net.spawns_made", spawns_made, spawns_removed, spawn_failures);
    tes3x_log_hex3("net.spawns_full", spawns_full, spawns_gone, 0);
    tes3x_log_hex3("net.leveled", leveled_withheld, leveled_rolled, leveled_made);
    tes3x_log_hex3("net.leveled_links", leveled_adopted, leveled_replaced, actor_failures);
    tes3x_log("net.leveled_hook", leveled_hooked);
    tes3x_log_hex3("net.actors_made", actors_made, actors_removed, summons_sent);
    tes3x_log("net.summon_hook", summon_hooked);
    tes3x_log_hex3("net.spawn_holds", spawns_held, spawns_taken_over, spawns_released);
    tes3x_log("net.player_hook", player_hooked);
}

/* Containers. A container reference reads its contents from [ref+0x28]: the base container, shared
 * by every reference of it, until the reference is first opened, when the base is cloned into an
 * instance for that reference and its leveled lists are rolled. Four times a second each console
 * reads the instances in its active cells, and sends one whose contents changed as CONTENTS (in
 * parts): each stack's count, and for a stack's item data its condition and charge. The first
 * reading of an instance, its roll, is marked ROLLED: the server keeps what it already has for
 * that container and sends it back, so the first console's roll holds everywhere. On a cell change
 * a console asks for the containers of its active cells (WANT) and writes what comes into the
 * container's instance, cloning it first, through the inventory calls the Contents menu uses. */
#define EVENT_CONTENTS 16u /* refid, cell u16, part, parts, flags, then entries */
#define EVENT_WANT 17u     /* count, then cell indices u16 */
#define CONTENTS_HEAD 9u
#define CONTENTS_ROLLED 1u
#define ENTRY_DATA 1u /* entry: count i32, flags, [condition, charge], id */
#define BOXES 128u
#define BOX_ENTRIES 96u
#define ACTIVE_CELLS 16u
#define CONTAINER_CLONE 0x164 /* vtable offset: (reference) */
#define OBJECT_INVENTORY 0x3C /* flags, then the stacks' list: first node at +0xC */
#define INVENTORY_FIRST 0xC
#define STACK_VARIABLES 8 /* item data array: pointers +0x4, count +0xC */

typedef void(__attribute__((thiscall)) *fn_clone)(void *object, void *ref);
typedef void(__attribute__((thiscall)) *fn_inventory_add)(void *inventory, void *mobile,
                                                          void *object, int count, int overwrite,
                                                          u8 **data);
typedef void(__attribute__((thiscall)) *fn_inventory_remove)(void *inventory, void *mobile,
                                                             void *object, int count, void *data,
                                                             int drop_array);
typedef void(__attribute__((thiscall)) *fn_heap_free)(void *heap, void *p);

struct entry {
    int count;
    u32 flags, condition, charge;
    char id[SPAWN_ID];
    u8 *data; /* the item data read, not sent or hashed */
};

static struct {
    u32 refid, hash;
} boxes[BOXES];
static struct entry box_in[BOX_ENTRIES];
static u32 box_in_count, box_in_part, box_in_refid, boxes_welcome, boxes_want;
static u32 boxes_sent, boxes_received, boxes_applied, box_failures, boxes_full;
static u32 items_worn, items_taken;

static const u8 *vtable_of(const u8 *object)
{
    return plausible(object) ? *(const u8 *const *)object : 0;
}

static const char *object_id(const u8 *object)
{
    const char *id = ((fn_object_id)(*(void *const *const *)object)[OBJECT_GET_ID / 4])(object);
    return mapped(id) ? id : 0;
}

/* An object's inventory as entries: per stack its item data, one entry each, then what is left;
 * with only, the stacks of that item. */
static u32 contents_read(const u8 *object, struct entry *out, u32 max, const char *only)
{
    const u8 *node = *(const u8 *const *)(object + OBJECT_INVENTORY + INVENTORY_FIRST), *stack;
    const u8 *item, *vars, *const *data;
    const char *id;
    u32 n = 0, guard, i, k, filled;
    int total, used;

    for (guard = 0; plausible(node) && guard < 256 && n < max;
         node = *(const u8 *const *)(node + 4), guard++) {
        if (!plausible(stack = *(const u8 *const *)(node + 8)) ||
            !plausible(item = *(const u8 *const *)(stack + 4)) || !(id = object_id(item)) ||
            (only && !same_id(id, only)))
            continue;
        total = *(const int *)stack;
        used = 0;
        vars = *(const u8 *const *)(stack + STACK_VARIABLES);
        filled = plausible(vars) ? *(const u32 *)(vars + 0xC) : 0;
        data = filled ? *(const u8 *const *const *)(vars + 4) : 0;
        for (i = 0; plausible(data) && i < filled && n < max; i++) {
            if (!plausible(data[i]))
                continue;
            out[n].count = 1;
            out[n].flags = ENTRY_DATA;
            out[n].condition = *(const u32 *)(data[i] + ITEM_CONDITION);
            out[n].charge = *(const u32 *)(data[i] + ITEM_CHARGE);
            out[n].data = (u8 *)data[i];
            for (k = 0; id[k] && k < SPAWN_ID - 1; k++)
                out[n].id[k] = id[k];
            out[n++].id[k] = 0;
            used++;
        }
        if (total < 0 ? total + used : total - used) {
            if (n == max)
                break;
            out[n].count = total < 0 ? total + used : total - used;
            out[n].flags = out[n].condition = out[n].charge = 0;
            out[n].data = 0;
            for (k = 0; id[k] && k < SPAWN_ID - 1; k++)
                out[n].id[k] = id[k];
            out[n++].id[k] = 0;
        }
    }
    return n;
}

static u32 contents_hash(const struct entry *e, u32 n)
{
    u32 hash = 2166136261u, i, k;
    const u8 *p;

    for (i = 0; i < n; i++) {
        p = (const u8 *)&e[i];
        for (k = 0; k < 16; k++)
            hash = (hash ^ p[k]) * 16777619u;
        for (k = 0; e[i].id[k]; k++)
            hash = (hash ^ (u8)e[i].id[k]) * 16777619u;
        hash *= 16777619u;
    }
    return hash;
}

/* Game thread: a container's contents as CONTENTS parts, all or none. */
static int contents_send(u32 refid, u32 cell, u32 flags, const struct entry *e, u32 n)
{
    static u8 parts[BOX_ENTRIES + 1][EVENT_DATA];
    static u32 lengths[BOX_ENTRIES + 1];
    u32 count = 0, i, k, size, lk, room;

    lengths[0] = CONTENTS_HEAD;
    for (i = 0; i < n; i++) {
        for (k = 0; e[i].id[k]; k++)
            ;
        size = 5 + (e[i].flags & ENTRY_DATA ? 8 : 0) + k + 1;
        if (lengths[count] + size > EVENT_DATA)
            lengths[++count] = CONTENTS_HEAD;
        {
            u8 *p = parts[count] + lengths[count];
            put32le(p, (u32)e[i].count);
            p[4] = (u8)e[i].flags;
            p += 5;
            if (e[i].flags & ENTRY_DATA) {
                put32le(p, e[i].condition);
                put32le(p + 4, e[i].charge);
                p += 8;
            }
            copy(p, (const u8 *)e[i].id, k + 1);
        }
        lengths[count] += size;
    }
    count++;
    lk = lock();
    room = EVENTS_OUT - (rel.out_next - rel.out_first);
    unlock(lk);
    if (room < count)
        return 0;
    for (i = 0; i < count; i++) {
        put32le(parts[i], refid);
        parts[i][4] = (u8)cell;
        parts[i][5] = (u8)(cell >> 8);
        parts[i][6] = (u8)i;
        parts[i][7] = (u8)count;
        parts[i][8] = (u8)flags;
        event_queue(EVENT_CONTENTS, parts[i], lengths[i]);
    }
    boxes_sent++;
    return 1;
}

/* The active cells (the current interior, or the 3x3) with their index in the cells list. */
static u32 active_cells(u8 **cells, u32 *indices)
{
    const u8 *node = cells_head();
    u32 i, n = 0;

    for (i = 0; plausible(node) && i < OBJECT_NO_CELL && n < ACTIVE_CELLS;
         i++, node = *(const u8 *const *)(node + 8))
        if (cell_active(*(u8 *const *)node)) {
            cells[n] = *(u8 *const *)node;
            indices[n++] = i;
        }
    return n;
}

/* A cell's reference with this refid, or 0. */
static u8 *cell_reference(u8 *cell, u32 refid)
{
    static const u32 lists[2] = {0x2C, 0x3C};
    const u8 *temporary;
    u8 *ref;
    u32 i;

    for (i = 0; i < 3; i++) {
        if (i < 2)
            ref = *(u8 **)(cell + lists[i] + LIST_HEAD);
        else if (plausible(temporary = *(const u8 *const *)(cell + CELL_TEMPORARY)))
            ref = *(u8 *const *)(temporary + 8 + LIST_HEAD);
        else
            break;
        for (; plausible(ref); ref = *(u8 **)(ref + REF_NEXT))
            if (*(const u32 *)(ref + REF_ID) == refid)
                return ref;
    }
    return 0;
}

static u32 *box_hash(u32 refid, int create)
{
    u32 i, free = BOXES;

    for (i = 0; i < BOXES; i++) {
        if (boxes[i].refid == refid)
            return &boxes[i].hash;
        if (!boxes[i].refid && free == BOXES)
            free = i;
    }
    if (!create)
        return 0;
    if (free == BOXES) {
        boxes_full++;
        free = refid % BOXES; /* forgetting one only sends it again */
    }
    boxes[free].refid = refid;
    boxes[free].hash = 0;
    return &boxes[free].hash;
}

/* Actors' inventories. A mobile gives its actor a copy of the object, and the copy holds the
 * inventory at +0x3C as a container instance does; looting a corpse, pickpocketing and barter change
 * it. The scan reads the actors of the active cells too (not the player or a ghost), keyed by
 * actor_id, and received contents are applied as the difference, so a living actor keeps what it
 * wears. */
static int inventory_actor(const u8 *ref)
{
    u8 *object = *(u8 *const *)(ref + REF_BASE);

    return actor_object(object) && actor_base(object) != object &&
           ref != player_reference() && !is_ghost(ref);
}

static int entry_same(const struct entry *a, const struct entry *b)
{
    return a->flags == b->flags && same_id(a->id, b->id) &&
           (!(a->flags & ENTRY_DATA) || (a->condition == b->condition && a->charge == b->charge));
}

/* Take an item out of an actor's inventory with the script command, which unequips it first: the
 * worn list points into the stacks. */
static void inventory_take(u8 *ref, const char *id, int count)
{
    char line[64], *p = put_text(line, "RemoveItem \"");

    p = put_int(put_text(put_text(p, id), "\" "), count);
    *p = 0;
    run_script_on(line, ref);
    items_taken++;
    log_text("net.inventory_take", id);
}

/* Add and remove what makes the actor's inventory hold the entries. An item that differs only in
 * its condition or charge (a weapon worn by a blow) is changed in place: taking it out would
 * unequip it. */
static int inventory_apply(u8 *ref, const struct entry *want, u32 n, const char *only, u8 *mobile)
{
    static struct entry have[BOX_ENTRIES];
    static u8 matched[BOX_ENTRIES];
    static u32 pair[BOX_ENTRIES];
    u8 *object = *(u8 **)(ref + REF_BASE), *inventory = object + OBJECT_INVENTORY, *item, *data;
    u32 h = contents_read(object, have, BOX_ENTRIES, only), i, k;
    int delta;

    object_applying = 1;
    for (k = 0; k < h; k++)
        matched[k] = 0;
    for (i = 0; i < n; i++) {
        for (k = 0; k < h && (matched[k] || !entry_same(&want[i], &have[k])); k++)
            ;
        if (k < h)
            matched[k] = 1;
        pair[i] = k;
    }
    for (i = 0; i < n; i++) {
        if (pair[i] < h)
            continue;
        for (k = 0; k < h && (matched[k] || have[k].count != want[i].count ||
                              !same_id(have[k].id, want[i].id)); k++)
            ;
        if (k == h)
            continue;
        matched[k] = 1;
        pair[i] = k;
        if ((want[i].flags & ENTRY_DATA) && plausible(have[k].data)) {
            *(u32 *)(have[k].data + ITEM_CONDITION) = want[i].condition;
            *(u32 *)(have[k].data + ITEM_CHARGE) = want[i].charge;
            items_worn++;
        }
    }
    for (i = 0; i < n; i++) {
        delta = want[i].count - (pair[i] < h ? have[pair[i]].count : 0);
        if (!delta)
            continue;
        if (!(item = resolve_object(want[i].id))) {
            box_failures++;
            continue;
        }
        if (delta < 0) {
            inventory_take(ref, want[i].id, -delta);
            continue;
        }
        data = 0;
        if ((want[i].flags & ENTRY_DATA) &&
            plausible(data = ((fn_item_data_new)TES3X_NET_ITEM_DATA_NEW)(item))) {
            *(u32 *)(data + ITEM_CONDITION) = want[i].condition;
            *(u32 *)(data + ITEM_CHARGE) = want[i].charge;
        }
        ((fn_inventory_add)TES3X_NET_INVENTORY_ADD)(inventory, mobile, item, delta, 0,
                                                    data ? &data : 0);
    }
    for (k = 0; k < h; k++)
        if (!matched[k])
            inventory_take(ref, have[k].id, have[k].count < 0 ? -have[k].count : have[k].count);
    ((fn_set_modified)TES3X_NET_REF_MODIFIED)(ref, 1);
    object_applying = 0;
    return 1;
}

/* The actor with this id in an active cell, living or dead. */
static u8 *inventory_owner(u32 refid)
{
    static const u32 lists[2] = {0x2C, 0x3C};
    u8 *cells[ACTIVE_CELLS], *ref;
    const u8 *temporary;
    u32 indices[ACTIVE_CELLS], n, c, l;

    n = active_cells(cells, indices);
    for (c = 0; c < n; c++)
        for (l = 0; l < 3; l++) {
            if (l < 2)
                ref = *(u8 **)(cells[c] + lists[l] + LIST_HEAD);
            else if (plausible(temporary = *(const u8 *const *)(cells[c] + CELL_TEMPORARY)))
                ref = *(u8 *const *)(temporary + 8 + LIST_HEAD);
            else
                break;
            for (; plausible(ref); ref = *(u8 **)(ref + REF_NEXT))
                if (inventory_actor(ref) && actor_id(ref) == refid)
                    return ref;
        }
    return 0;
}

/* Every SPAWN_WATCH_US: the container instances of the active cells whose contents changed. */
static void containers_scan(void)
{
    static struct entry entries[BOX_ENTRIES];
    u8 *cells[ACTIVE_CELLS], *ref, *object;
    const u8 *temporary;
    u32 indices[ACTIVE_CELLS], n, c, l, count, hash, *known, refid;
    static const u32 lists[2] = {0x2C, 0x3C};

    if (leveled_none_epoch != spawns_settled) {
        leveled_none_epoch = spawns_settled;
        leveled_none_count = 0; /* a list that rolled none rolls again on the next visit */
    }
    n = active_cells(cells, indices);
    for (c = 0; c < n; c++)
        for (l = 0; l < 3; l++) {
            if (l < 2)
                ref = *(u8 **)(cells[c] + lists[l] + LIST_HEAD);
            else if (plausible(temporary = *(const u8 *const *)(cells[c] + CELL_TEMPORARY)))
                ref = *(u8 *const *)(temporary + 8 + LIST_HEAD);
            else
                break;
            for (; plausible(ref); ref = *(u8 **)(ref + REF_NEXT)) {
                object = *(u8 **)(ref + REF_BASE);
                if (plausible(object) && *(const u32 *)(object + 4) == TAG_LEVC) {
                    leveled_seen(ref, indices[c], cells[c]);
                    continue;
                }
                if ((u32)vtable_of(object) == TES3X_NET_CONTAINER_INSTANCE_VTABLE)
                    refid = *(const u32 *)(ref + REF_ID);
                else if (inventory_actor(ref))
                    refid = actor_id(ref);
                else
                    continue;
                if (!refid)
                    continue;
                count = contents_read(object, entries, BOX_ENTRIES, 0);
                hash = contents_hash(entries, count) | 1;
                known = box_hash(refid, 0);
                if (known && *known == hash)
                    continue;
                if (contents_send(refid, indices[c], known ? 0 : CONTENTS_ROLLED, entries,
                                  count)) {
                    *box_hash(refid, 1) = hash;
                    tes3x_log_hex3("net.contents_sent", refid, count, known ? 0 : 1);
                }
            }
        }
}

static void wants_send(void)
{
    u8 data[EVENT_DATA], *cells[ACTIVE_CELLS];
    u32 indices[ACTIVE_CELLS], n, i;

    n = active_cells(cells, indices);
    if (!n)
        return;
    data[0] = (u8)n;
    for (i = 0; i < n; i++) {
        data[1 + 2 * i] = (u8)indices[i];
        data[2 + 2 * i] = (u8)(indices[i] >> 8);
    }
    if (event_queue(EVENT_WANT, data, 1 + 2 * n))
        boxes_want = 0;
}

/* Empty the reference's container instance, cloning it first, and fill it with the entries. */
static int contents_apply(u8 *ref, const struct entry *e, u32 n)
{
    u8 *object = *(u8 **)(ref + REF_BASE), *inventory, *node, *stack, *item, *vars, *data;
    u32 i, guard;
    int count;

    if ((u32)vtable_of(object) == TES3X_NET_CONTAINER_VTABLE) {
        ((fn_clone)(*(void *const *const *)object)[CONTAINER_CLONE / 4])(object, ref);
        object = *(u8 **)(ref + REF_BASE);
    }
    if ((u32)vtable_of(object) != TES3X_NET_CONTAINER_INSTANCE_VTABLE)
        return 0;
    inventory = object + OBJECT_INVENTORY;
    object_applying = 1;
    for (guard = 0; plausible(node = *(u8 **)(inventory + INVENTORY_FIRST)) && guard < 512;
         guard++) {
        stack = *(u8 **)(node + 8);
        item = *(u8 **)(stack + 4);
        vars = *(u8 **)(stack + STACK_VARIABLES);
        if (plausible(vars) && *(const u32 *)(vars + 0xC) &&
            plausible(data = **(u8 ***)(vars + 4))) {
            ((fn_inventory_remove)TES3X_NET_INVENTORY_REMOVE)(inventory, 0, item, 1, data, 1);
            ((fn_mobile_call)TES3X_NET_ITEM_DATA_DESTROY)(data);
            ((fn_heap_free)TES3X_NET_HEAP_FREE)((void *)TES3X_NET_HEAP, data);
            continue;
        }
        count = *(const int *)stack;
        ((fn_inventory_remove)TES3X_NET_INVENTORY_REMOVE)(inventory, 0, item,
                                                          count < 0 ? -count : count ? count : 1,
                                                          0, 1);
        if (*(u8 **)(inventory + INVENTORY_FIRST) == node && *(u8 **)(node + 8) == stack &&
            *(const int *)stack == count)
            break; /* not removed: stop rather than loop */
    }
    for (i = 0; i < n; i++) {
        if (!(item = resolve_object(e[i].id))) {
            box_failures++;
            log_text("net.contents_unknown", e[i].id);
            continue;
        }
        data = 0;
        if ((e[i].flags & ENTRY_DATA) &&
            plausible(data = ((fn_item_data_new)TES3X_NET_ITEM_DATA_NEW)(item))) {
            *(u32 *)(data + ITEM_CONDITION) = e[i].condition;
            *(u32 *)(data + ITEM_CHARGE) = e[i].charge;
        }
        ((fn_inventory_add)TES3X_NET_INVENTORY_ADD)(inventory, 0, item, e[i].count, 0,
                                                    data ? &data : 0);
    }
    ((fn_set_modified)TES3X_NET_REF_MODIFIED)(ref, 1);
    object_applying = 0;
    return 1;
}

static void contents_event(const struct event *e)
{
    static struct entry read_back[BOX_ENTRIES];
    const u8 *p = e->data;
    u32 refid, cell, off = CONTENTS_HEAD, k, n;
    u8 *cell_ptr, *ref;

    if (e->length < CONTENTS_HEAD)
        return;
    refid = get32le(p);
    cell = p[4] | (u32)p[5] << 8;
    if (p[6] == 0) {
        box_in_count = box_in_part = 0;
        box_in_refid = refid;
    } else if (p[6] != box_in_part || refid != box_in_refid) {
        return;
    }
    box_in_part++;
    while (off + 5 < e->length && box_in_count < BOX_ENTRIES) {
        struct entry *x = &box_in[box_in_count++];
        x->count = (int)get32le(p + off);
        x->flags = p[off + 4];
        off += 5;
        x->condition = x->charge = 0;
        if (x->flags & ENTRY_DATA) {
            x->condition = get32le(p + off);
            x->charge = get32le(p + off + 4);
            off += 8;
        }
        for (k = 0; off + k < e->length && p[off + k] && k < SPAWN_ID - 1; k++)
            x->id[k] = (char)p[off + k];
        x->id[k] = 0;
        if (!script_safe((const u8 *)x->id, SPAWN_ID)) {
            box_in_count = box_in_part = 0;
            refused_events++;
            return;
        }
        while (off < e->length && p[off])
            off++;
        off++;
    }
    if (box_in_part != p[7])
        return;
    boxes_received++;
    /* Not loaded here: WANT asks for it when its cell is. */
    if (!cell_active(cell_ptr = cell_at(cell)) || !(ref = cell_reference(cell_ptr, refid)))
        ref = inventory_owner(refid);
    if (!ref)
        return;
    if (inventory_actor(ref) ? !inventory_apply(ref, box_in, box_in_count, 0, 0)
                             : !contents_apply(ref, box_in, box_in_count)) {
        box_failures++;
        tes3x_log_hex3("net.contents_failed", refid, (u32)vtable_of(*(u8 **)(ref + REF_BASE)), 0);
        return;
    }
    n = contents_read(*(u8 **)(ref + REF_BASE), read_back, BOX_ENTRIES, 0);
    *box_hash(refid, 1) = contents_hash(read_back, n) | 1;
    boxes_applied++;
    tes3x_log_hex3("net.contents_applied", refid, box_in_count, n);
}

/* Game thread, in the world. */
static void containers_frame(void)
{
    static u32 scanned, signature;
    u32 now = now_us(), i;

    if (ses.state != SESSION_JOINED)
        return;
    if (boxes_welcome != ses.welcomes) {
        boxes_welcome = ses.welcomes;
        for (i = 0; i < BOXES; i++)
            boxes[i].refid = 0;
        boxes_want = 1;
    }
    if (signature != spawns_settled) {
        signature = spawns_settled;
        boxes_want = 1;
    }
    /* Once the cells have loaded their references. */
    if (now - spawns_settled < SPAWN_SETTLE_US)
        return;
    if (boxes_want)
        wants_send();
    if (now - scanned < SPAWN_WATCH_US)
        return;
    scanned = now;
    containers_scan();
}

static void containers_stat(void)
{
    tes3x_log_hex3("net.contents", boxes_sent, boxes_received, boxes_applied);
    tes3x_log_hex3("net.contents_bad", box_failures, boxes_full, 0);
    tes3x_log_hex3("net.inventory_items", items_worn, items_taken, 0);
}

/* Statuses. An effect that changes how an actor looks or acts goes out as AFFECT from the console
 * that applies it, and each follower applies the same effect of the same spell to its copy. Damage
 * effects are left out: their result arrives with the statistics in ACTORS. AI settings and the
 * stored base disposition go out as STATUS from whichever console changes them (dialogue changes
 * disposition on the talker's), latest wins; the disposition shown adds the local player's race,
 * faction and personality and stays per player. The server keeps the latest STATUS per actor. */
#define EVENT_AFFECT 18u /* actor id, effect index, spell id */
#define EVENT_STATUS 19u /* actor id, fight, flee, alarm, hello, base disposition (i16 each) */
#define AFFECT_BYTES 5u
#define STATUS_VALUES 5u
#define STATUS_BYTES (4u + STATUS_VALUES * 2u)
#define MOBILE_FLEE 0x354
#define MOBILE_HELLO 0x358
#define MOBILE_ALARM 0x35C
#define NPC_BASE_DISPOSITION 0x8C /* vtable offset; what calculateDisposition 0x0011E210 starts from */
#define NO_DISPOSITION (-32768)
#define STATUSES 256u
#define STATUS_NEW 1u
#define STATUS_APPLIED 2u

typedef int(__attribute__((thiscall)) *fn_object_int)(const void *object);

/* The values last sent or applied per actor; pending ones wait for the actor to be loaded here. */
static struct {
    u32 refid;
    short v[STATUS_VALUES];
    u8 pending, settle;
} statuses[STATUSES];
static u32 status_next, statuses_sent, statuses_received, statuses_applied, statuses_lost;
static u32 statuses_kept;
static u32 status_mismatches, affects_sent, affects_received, affects_applied;

static int affect_shared(int id)
{
    return (id >= 3 && id <= 6) || id == 10 || id == 17 || id == 22 || (id >= 39 && id <= 42) ||
           (id >= 45 && id <= 48) || id == 79;
}

/* An effect applied here to an actor others can name: tell them if it shows. */
static void affect_send(const u8 *instance, const u8 *target, u32 effect)
{
    const u8 *source;
    const char *id;
    u8 data[AFFECT_BYTES + SPELL_ID];
    u32 refid, n;

    if (ses.state != SESSION_JOINED || effect >= SOURCE_MAX_EFFECTS || !plausible(target) ||
        target == player_reference() || is_ghost(target) || !(refid = actor_id(target)) ||
        !(id = spell_id(instance)))
        return;
    source = *(const u8 *const *)(instance + INSTANCE_SOURCE);
    if (!affect_shared(*(const short *)(source + SOURCE_EFFECTS + effect * EFFECT_BYTES)))
        return;
    put32le(data, refid);
    data[4] = (u8)effect;
    for (n = 0; id[n] && n < SPELL_ID - 1; n++)
        data[AFFECT_BYTES + n] = (u8)id[n];
    data[AFFECT_BYTES + n] = 0;
    if (!event_queue(EVENT_AFFECT, data, AFFECT_BYTES + n + 1)) {
        tes3x_log("net.event_full", EVENT_AFFECT);
        return;
    }
    affects_sent++;
    log_text("net.affect_sent", id);
    tes3x_log_hex3("net.affect_to", refid, effect, 0);
}

/* Another console applied an effect to an actor followed here: the copy casts the same spell on
 * itself and takes that one effect. */
static void affect_event(const struct event *e)
{
    const u8 *effects;
    u8 *target, *instance;
    char id[SPELL_ID];
    u32 refid, index, n, owner;

    if (e->length < AFFECT_BYTES + 1)
        return;
    refid = get32le(e->data);
    index = e->data[4];
    for (n = 0; n < SPELL_ID - 1 && AFFECT_BYTES + n < e->length && e->data[AFFECT_BYTES + n]; n++)
        id[n] = (char)e->data[AFFECT_BYTES + n];
    id[n] = 0;
    affects_received++;
    log_text("net.affect", id);
    tes3x_log_hex3("net.affect_from", e->origin, refid, index);
    if (!(target = actor_ref(refid)) || !ref_owner(target, &owner) || index >= SOURCE_MAX_EFFECTS)
        return; /* not here, or run here */
    if (!(instance = spell_start(id, target, target, refid)))
        return;
    effects = *(const u8 *const *)(instance + INSTANCE_SOURCE) + SOURCE_EFFECTS;
    for (n = 0; n <= index && *(const short *)(effects + n * EFFECT_BYTES) != -1; n++)
        ;
    if (n <= index) {
        spell_fail(id, 5, refid);
        return;
    }
    ((fn_spell_hit)TES3X_NET_SPELL_HIT)(instance, target, (int)index);
    *(u32 *)(instance + INSTANCE_STATE) = INSTANCE_WORKING;
    affects_applied++;
    tes3x_log_hex3("net.affect_applied", refid, index, 0);
}

static int is_npc(const u8 *ref)
{
    const u8 *object = *(const u8 *const *)(ref + REF_BASE);

    return plausible(object) && *(const u32 *)(object + OBJECT_TYPE) == TAG_NPC;
}

/* An NPC's stored base disposition; 50, which adds nothing to Fight, for a creature. */
static int base_disposition(const u8 *ref)
{
    const u8 *object = *(const u8 *const *)(ref + REF_BASE);

    return is_npc(ref) ? ((fn_object_int)(*(void *const *const *)object)[NPC_BASE_DISPOSITION / 4])(
                             object)
                       : 50;
}

/* An NPC's Fight or Alarm as its record sets it, or value when that is lower or the actor is not
 * an NPC. Raised above the record they belong to the console that raised them, by a crime against
 * its player, so they are neither sent nor applied. */
#define NPC_INSTANCE_BASE 0x6C
#define NPC_AI_CONFIG 0xE0

static int record_ai(const u8 *ref, u32 offset, int value)
{
    const u8 *object = *(const u8 *const *)(ref + REF_BASE), *base;

    if (!is_npc(ref) || !plausible(base = *(const u8 *const *)(object + NPC_INSTANCE_BASE)))
        return value;
    return value > base[NPC_AI_CONFIG + offset] ? base[NPC_AI_CONFIG + offset] : value;
}

static void status_read(const u8 *mobile, const u8 *ref, short *v)
{
    const u8 *object = *(const u8 *const *)(ref + REF_BASE);

    v[0] = (short)record_ai(ref, AI_FIGHT, *(const int *)(mobile + MOBILE_FIGHT));
    v[1] = (short)*(const int *)(mobile + MOBILE_FLEE);
    v[2] = (short)record_ai(ref, AI_ALARM, *(const int *)(mobile + MOBILE_ALARM));
    v[3] = (short)*(const int *)(mobile + MOBILE_HELLO);
    v[4] = is_npc(ref) ? (short)((fn_object_int)(*(void *const *const *)object)
                                     [NPC_BASE_DISPOSITION / 4])(object)
                       : NO_DISPOSITION;
}

/* The entry for refid; a new one takes a free slot, else one not waiting to be applied. */
static u32 status_slot(u32 refid)
{
    u32 i, n;

    for (i = 0; i < STATUSES; i++)
        if (statuses[i].refid == refid)
            return i;
    for (i = 0; i < STATUSES; i++)
        if (!statuses[i].refid)
            break;
    for (n = 0; i == STATUSES && n < STATUSES; n++, status_next = (status_next + 1) % STATUSES)
        if (!statuses[status_next].pending)
            i = status_next;
    if (i == STATUSES) {
        i = status_next;
        statuses_lost++;
    }
    status_next = (i + 1) % STATUSES;
    statuses[i].refid = refid;
    statuses[i].pending = 0;
    statuses[i].settle = STATUS_NEW;
    return i;
}

/* On the actor send tick, for each loaded actor: apply what arrived for it, or send what changed
 * here. The values read on first sight, or right after an apply, are taken as they are. */
static void status_frame(const u8 *mobile, u8 *ref, u32 refid)
{
    u32 i = status_slot(refid), k;
    short v[STATUS_VALUES];
    u8 data[STATUS_BYTES];

    if (statuses[i].pending) {
        statuses[i].v[0] = (short)record_ai(ref, AI_FIGHT, statuses[i].v[0]);
        statuses[i].v[2] = (short)record_ai(ref, AI_ALARM, statuses[i].v[2]);
        actor_command(ref, "SetFight ", statuses[i].v[0]);
        actor_command(ref, "SetFlee ", statuses[i].v[1]);
        actor_command(ref, "SetAlarm ", statuses[i].v[2]);
        actor_command(ref, "SetHello ", statuses[i].v[3]);
        if (statuses[i].v[4] != NO_DISPOSITION && is_npc(ref))
            actor_command(ref, "SetDisposition ", statuses[i].v[4]);
        statuses[i].pending = 0;
        statuses[i].settle = STATUS_APPLIED;
        statuses_applied++;
        tes3x_log_hex3("net.status_applied", refid, (u32)statuses[i].v[0],
                       (u32)statuses[i].v[4]);
        return;
    }
    status_read(mobile, ref, v);
    if ((v[0] != *(const int *)(mobile + MOBILE_FIGHT) ||
         v[2] != *(const int *)(mobile + MOBILE_ALARM)) && statuses_kept++ < 32)
        tes3x_log_hex3("net.status_kept", refid,
                       (u32)*(const int *)(mobile + MOBILE_FIGHT) << 16 | (u16)v[0],
                       (u32)*(const int *)(mobile + MOBILE_ALARM) << 16 | (u16)v[2]);
    for (k = 0; k < STATUS_VALUES && v[k] == statuses[i].v[k]; k++)
        ;
    if (statuses[i].settle) {
        if (statuses[i].settle == STATUS_APPLIED && k < STATUS_VALUES) {
            status_mismatches++;
            tes3x_log_hex3("net.status_mismatch", refid, k, (u32)v[k]);
        }
        statuses[i].settle = 0;
        copy((u8 *)statuses[i].v, (const u8 *)v, sizeof(v));
        return;
    }
    if (k == STATUS_VALUES)
        return;
    copy((u8 *)statuses[i].v, (const u8 *)v, sizeof(v));
    put32le(data, refid);
    for (k = 0; k < STATUS_VALUES; k++) {
        data[4 + k * 2] = (u8)v[k];
        data[5 + k * 2] = (u8)((u16)v[k] >> 8);
    }
    if (!event_queue(EVENT_STATUS, data, sizeof(data))) {
        tes3x_log("net.event_full", EVENT_STATUS);
        return;
    }
    statuses_sent++;
    tes3x_log_hex3("net.status_sent", refid, (u32)v[0], (u32)v[4]);
}

static void status_event(const struct event *e)
{
    u32 refid, i, k;

    if (e->length < STATUS_BYTES || !(refid = get32le(e->data)))
        return;
    i = status_slot(refid);
    for (k = 0; k < STATUS_VALUES; k++)
        statuses[i].v[k] = (short)(e->data[4 + k * 2] | e->data[5 + k * 2] << 8);
    statuses[i].pending = 1;
    statuses[i].settle = 0;
    statuses_received++;
    tes3x_log_hex3("net.status", refid, e->origin, (u32)statuses[i].v[4]);
}

/* Bounties. Each console sends its player's bounty when it changes, and the server replays the
 * latest of each player to a joiner. When a player's bounty drops to nothing (a fine paid, a
 * crime forgiven), the actors run here that fight that player's ghost and would not attack on
 * their record's Fight stop, as the criminal's own console stops its guards. */
#define EVENT_BOUNTY 30u /* the player's bounty, i32 */
#define BOUNTY_EVERY_US 1000000u
#define PEACE_US 3000000u

typedef int(__attribute__((thiscall)) *fn_get_bounty)(const void *mobile_player);

static struct {
    u32 client, peace_until;
    int bounty;
} bounties[PEERS];
static int bounty_sent = -1;
static u32 bounty_checked, bounties_received, peace_stops;

static void bounty_frame(void)
{
    const u8 *ref = player_reference(), *mobile;
    u32 now = now_us();
    int bounty;
    u8 data[4];

    if (ses.state != SESSION_JOINED) {
        bounty_sent = -1;
        return;
    }
    if (now - bounty_checked < BOUNTY_EVERY_US || !plausible(ref) ||
        !plausible(mobile = ref_mobile(ref)))
        return;
    bounty_checked = now;
    bounty = ((fn_get_bounty)TES3X_NET_GET_BOUNTY)(mobile);
    if (bounty == bounty_sent)
        return;
    put32le(data, (u32)bounty);
    if (!event_queue(EVENT_BOUNTY, data, sizeof(data))) {
        tes3x_log("net.event_full", EVENT_BOUNTY);
        return;
    }
    bounty_sent = bounty;
    tes3x_log_hex3("net.bounty_sent", (u32)bounty, 0, 0);
}

static void bounty_event(const struct event *e)
{
    u32 i, free = PEERS;
    int bounty, old;

    if (e->length < 4 || !e->origin || e->origin == ses.client)
        return;
    bounty = (int)get32le(e->data);
    for (i = 0; i < PEERS && bounties[i].client != e->origin; i++)
        if (!bounties[i].client && free == PEERS)
            free = i;
    if (i == PEERS) {
        if (free == PEERS)
            return;
        i = free;
        bounties[i].client = e->origin;
        bounties[i].bounty = 0;
    }
    old = bounties[i].bounty;
    bounties[i].bounty = bounty;
    bounties_received++;
    if (old > 0 && bounty <= 0)
        bounties[i].peace_until = now_us() + PEACE_US;
    tes3x_log_hex3("net.bounty", e->origin, (u32)bounty, (u32)old);
}

static void peace_check(const u8 *mobile, void *ref, u32 refid)
{
    u32 client = combat_target(mobile), i;
    float fight;

    if (!client || client >= PLAYER_IDS || client == ses.client)
        return;
    for (i = 0; i < PEERS; i++)
        if (bounties[i].client == client && (int)(bounties[i].peace_until - now_us()) > 0)
            break;
    if (i == PEERS)
        return;
    fight = (float)record_ai(ref, AI_FIGHT, *(const int *)(mobile + MOBILE_FIGHT)) +
            FIGHT_DISP_MULT * (float)(50 - base_disposition(ref));
    if (fight + FIGHT_DISTANCE_BASE >= FIGHT_ATTACK)
        return;
    run_script_on("StopCombat", ref);
    peace_stops++;
    tes3x_log_hex3("net.peace", refid, client, 0);
}

static void bounty_stat(void)
{
    tes3x_log_hex3("net.bounties", (u32)bounty_sent, bounties_received, peace_stops);
}

static void status_stat(void)
{
    tes3x_log_hex3("net.statuses", statuses_sent, statuses_received, statuses_applied);
    tes3x_log_hex3("net.statuses_bad", status_mismatches, statuses_lost, statuses_kept);
    tes3x_log_hex3("net.affects", affects_sent, affects_received, affects_applied);
}

/* Bulk transfer from the server. OFFER (an event) names a file, its size and its BLAKE2b-256
 * hash; CHUNK packets carry BULK_CHUNK bytes each by index; BULK_ACK tells the sender the first
 * chunk not yet written, which of the next ones arrived, how many past it may be in flight and the
 * transfer's status. The receive DPC only copies chunks into slots: the game thread writes them
 * to U:\TES3X\NAME.part in order and renames it to NAME once its hash matches. A relaunch resumes
 * from the part's length, since the server offers again after WELCOME. */
#define BULK_CHUNK 1024u
#define BULK_SLOTS 16u /* chunks in flight, well under RX_RING with the rest of the traffic */
#define BULK_HASH 32u
#define BULK_NAME 38u /* 37 characters and ".part" fit FATX's 42 */
#define BULK_MAX (16u << 20)
#define BULK_ACK_BYTES 20u
#define BULK_OFFER_BYTES (8u + BULK_HASH)
#define BULK_IDLE 0u
#define BULK_OPENING 1u
#define BULK_RECEIVING 2u
#define BULK_DONE 3u
#define BULK_BAD_HASH 4u
#define BULK_REFUSED 5u
#define BULK_FAILED 6u
#define BULK_NO_SPACE 7u /* the volume cannot take the rest of the file and BULK_SPACE_KB */
/* Left free after a download: the engine's next save into the slot writes a copy beside the old
 * one before it replaces it. */
#define BULK_SPACE_KB 1024u
static char game_loaded[BULK_NAME + 1]; /* the save this launch was made to load */
#define FILE_DIRECTORY_FILE 0x01u
#define FILE_SHARE_WRITE 0x02u
#define FILE_SHARE_DELETE 0x04u
#define DELETE_ACCESS 0x00010000u
#define FileRenameInformation 10u

typedef struct {
    u8 ReplaceIfExists;
    void *RootDirectory;
    ANSI_STRING FileName;
} FILE_RENAME_INFORMATION;

#define NtCreateFile KFN(THUNK_NtCreateFile, fn_NtCreateFile)
#define NtWriteFile KFN(THUNK_NtWriteFile, fn_NtWriteFile)
#define NtReadFile KFN(THUNK_NtReadFile, fn_NtReadFile)
#define NtQueryInformationFile KFN(THUNK_NtQueryInformationFile, fn_NtQueryInformationFile)
#define NtSetInformationFile KFN(THUNK_NtSetInformationFile, fn_NtSetInformationFile)
#define NtFlushBuffersFile KFN(THUNK_NtFlushBuffersFile, fn_NtFlushBuffersFile)
#define NtClose KFN(THUNK_NtClose, fn_NtClose)
typedef u32(__stdcall *fn_NtQueryVolumeInformationFile)(void *, IO_STATUS_BLOCK *, void *, u32,
                                                        u32);
/* Xbox's NtQueryDirectoryFile has no ReturnSingleEntry; RestartScan is a BOOLEAN in a 4-byte slot. */
typedef u32(__stdcall *fn_NtQueryDirectoryFile)(void *, void *, void *, void *, IO_STATUS_BLOCK *,
                                                void *, u32, u32, ANSI_STRING *, u32);
#define NtQueryVolumeInformationFile                                                               \
    KFN(THUNK_NtQueryVolumeInformationFile, fn_NtQueryVolumeInformationFile)
#define NtQueryDirectoryFile KFN(THUNK_NtQueryDirectoryFile, fn_NtQueryDirectoryFile)
#define FileFsSizeInformation 3u
#define FileDirectoryInformation 1u

/* File work runs on a worker thread: a FATX write and flush can take tens of milliseconds (the
 * first servers.ini on hardware: 64 ms), and a bulk transfer ends by hashing the whole file. The
 * log file admits one writer at a time, so the worker queues its lines for the game thread. */
#define WORKER_SLEEP_MS 5
#define WORKER_LOGS 16u
static struct {
    volatile u32 running, stop;
    u32 count, lost;
    struct {
        const char *tag;
        u32 a, b, c;
    } log[WORKER_LOGS];
} worker;

static void worker_log(const char *tag, u32 a, u32 b, u32 c)
{
    u32 flags = lock();

    if (worker.count < WORKER_LOGS) {
        worker.log[worker.count].tag = tag;
        worker.log[worker.count].a = a;
        worker.log[worker.count].b = b;
        worker.log[worker.count++].c = c;
    } else {
        worker.lost++;
    }
    unlock(flags);
}

/* state, id, total, chunks, next and filled are shared with the receive DPC; the rest is the file
 * work's. An offer waits in bulk_offered until the file work takes it up. */
static struct {
    u32 ready, id, total, state;
    u8 hash[BULK_HASH];
    char name[BULK_NAME + 1];
} bulk_offered;
static struct {
    u32 state, id, total, chunks, next, filled; /* filled: bit s, slot s holds its chunk */
    u32 arrived, duplicates, written, resumed, acks;
    void *file;
    u8 hash[BULK_HASH];
    char name[BULK_NAME + 1];
    u8 slot[BULK_SLOTS][BULK_CHUNK];
} bulk;

static u32 bulk_chunk_bytes(u32 index)
{
    return index + 1 < bulk.chunks ? BULK_CHUNK : bulk.total - index * BULK_CHUNK;
}

/* Caller holds the lock. */
static void bulk_ack(void)
{
    u8 body[BULK_ACK_BYTES];
    u32 i, seen = 0;

    if (ses.state != SESSION_JOINED)
        return;
    for (i = 0; i < BULK_SLOTS && bulk.next + i < bulk.chunks; i++)
        if (bulk.filled >> ((bulk.next + i) % BULK_SLOTS) & 1)
            seen |= 1u << i;
    put32le(body, bulk.id);
    put32le(body + 4, bulk.next);
    put32le(body + 8, seen);
    put32le(body + 12, bulk.state == BULK_RECEIVING ? BULK_SLOTS : 0);
    put32le(body + 16, bulk.state);
    session_send(T3MP_BULK_ACK, body, sizeof(body));
    bulk.acks++;
}

/* Caller holds the lock (the receive DPC). A chunk outside the window is answered with an ack,
 * since the sender's view is behind; every fourth arrival is acked too. */
static void bulk_chunk_rx(const u8 *p, u32 n)
{
    u32 index, s;

    if (n < 8 || !bulk.id || get32le(p) != bulk.id || bulk.state == BULK_OPENING)
        return;
    index = get32le(p + 4);
    if (bulk.state != BULK_RECEIVING || index < bulk.next || index >= bulk.next + BULK_SLOTS ||
        index >= bulk.chunks || n - 8 != bulk_chunk_bytes(index) ||
        bulk.filled >> (s = index % BULK_SLOTS) & 1) {
        bulk.duplicates++;
        bulk_ack();
        return;
    }
    copy(bulk.slot[s], p + 8, n - 8);
    bulk.filled |= 1u << s;
    if (++bulk.arrived % 4 == 0)
        bulk_ack();
}

static void named_path(char *path, const char *name, const char *suffix)
{
    static const char dir[] = "U:\\TES3X\\";
    u32 n = 0, i;

    for (i = 0; dir[i]; i++)
        path[n++] = dir[i];
    for (i = 0; name[i]; i++)
        path[n++] = name[i];
    for (i = 0; suffix[i]; i++)
        path[n++] = suffix[i];
    path[n] = 0;
}

static void bulk_path(char *path, const char *suffix)
{
    named_path(path, bulk.name, suffix);
}

static u32 bulk_open(char *path, u32 access, u32 disposition, u32 options, void **h)
{
    ANSI_STRING name;
    OBJECT_ATTRIBUTES oa;
    IO_STATUS_BLOCK iosb;

    *h = 0;
    tes3x_dos_attributes(&oa, &name, path);
    return NtCreateFile(h, access | SYNCHRONIZE, &oa, &iosb, 0, FILE_ATTRIBUTE_NORMAL,
                        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, disposition,
                        options | FILE_SYNCHRONOUS_IO_NONALERT);
}

static u64 bulk_size(void *h)
{
    FILE_NETWORK_OPEN_INFORMATION info;
    IO_STATUS_BLOCK iosb;

    if (NtQueryInformationFile(h, &iosb, &info, sizeof(info), FileNetworkOpenInformation))
        return 0;
    return info.EndOfFile;
}

/* 1 if the file at path is total bytes long and hashes to the offer's hash. Uses the slots as
 * its buffer, so only while nothing is being received into them. */
static int bulk_verify(char *path)
{
    crypto_blake2b_ctx ctx;
    IO_STATUS_BLOCK iosb;
    u8 digest[BULK_HASH];
    u64 offset = 0;
    u32 left = bulk.total, n, i, diff = 0;
    void *h;

    if (bulk_open(path, GENERIC_READ, FILE_OPEN, 0, &h))
        return 0;
    if (bulk_size(h) != bulk.total) {
        NtClose(h);
        return 0;
    }
    crypto_blake2b_init(&ctx, BULK_HASH);
    while (left) {
        n = left < sizeof(bulk.slot) ? left : sizeof(bulk.slot);
        if (NtReadFile(h, 0, 0, 0, &iosb, bulk.slot, n, &offset) || iosb.Information != n)
            break;
        crypto_blake2b_update(&ctx, (const u8 *)bulk.slot, n);
        offset += n;
        left -= n;
    }
    NtClose(h);
    crypto_blake2b_final(&ctx, digest);
    for (i = 0; i < BULK_HASH; i++)
        diff |= digest[i] ^ bulk.hash[i];
    return !left && !diff;
}

/* Renames from to to, replacing what is there; 0 on success. */
static u32 file_replace(char *from, char *to)
{
    FILE_RENAME_INFORMATION rename;
    IO_STATUS_BLOCK iosb;
    u32 status;
    void *h;

    if ((status = bulk_open(from, DELETE_ACCESS, FILE_OPEN, 0, &h)))
        return status;
    rename.ReplaceIfExists = 1;
    rename.RootDirectory = OB_DOS_DEVICES;
    rename.FileName.Buffer = to;
    rename.FileName.Length = rename.FileName.MaximumLength = (unsigned short)tes3x_strlen(to);
    status = NtSetInformationFile(h, &iosb, &rename, sizeof(rename), FileRenameInformation);
    NtClose(h);
    return status;
}

static void bulk_close(void)
{
    if (bulk.file)
        NtClose(bulk.file);
    bulk.file = 0;
}

/* Game thread: check an offer and leave it for the file work. A plain name: letters, digits,
 * space, '.', '-', '_', not starting with a dot. */
static void bulk_offer(const struct event *e)
{
    u32 i, n = e->length - BULK_OFFER_BYTES, flags, id, total, state = BULK_OPENING;
    char name[BULK_NAME + 1];

    if (e->length < BULK_OFFER_BYTES + 1)
        return;
    id = get32le(e->data);
    total = get32le(e->data + 4);
    for (i = 0; i < n && i < BULK_NAME && e->data[BULK_OFFER_BYTES + i]; i++) {
        char c = (char)e->data[BULK_OFFER_BYTES + i];
        if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') ||
              c == ' ' || c == '.' || c == '-' || c == '_') || (!i && c == '.'))
            break;
        name[i] = c;
    }
    name[i] = 0;
    if (!i || i == BULK_NAME || (i < n && e->data[BULK_OFFER_BYTES + i]) || !id ||
        total > BULK_MAX)
        state = BULK_REFUSED;
    tes3x_log_hex3("net.bulk_offer", id, total, state);
    log_text("net.bulk_name", name);
    flags = lock();
    bulk_offered.id = id;
    bulk_offered.total = total;
    bulk_offered.state = state;
    copy(bulk_offered.hash, e->data + 8, BULK_HASH);
    copy((u8 *)bulk_offered.name, (const u8 *)name, BULK_NAME + 1);
    bulk_offered.ready = 1;
    unlock(flags);
}

/* File work: take up the latest offer, dropping the transfer before it. */
static void bulk_adopt(void)
{
    u32 flags;

    if (!bulk_offered.ready)
        return;
    bulk_close();
    flags = lock();
    bulk.id = bulk_offered.id;
    bulk.total = bulk_offered.total;
    bulk.chunks = (bulk.total + BULK_CHUNK - 1) / BULK_CHUNK;
    copy(bulk.hash, bulk_offered.hash, BULK_HASH);
    copy((u8 *)bulk.name, (const u8 *)bulk_offered.name, BULK_NAME + 1);
    bulk.filled = bulk.next = 0;
    bulk.state = bulk_offered.state;
    bulk_offered.ready = 0;
    if (bulk.state == BULK_REFUSED)
        bulk_ack();
    unlock(flags);
}

/* File work: open or resume the part, then write what arrived, in order. */
/* KB free on the volume holding h; all of it when the volume will not say. */
static u32 volume_free_kb(void *h)
{
    struct {
        u64 total, available;
        u32 sectors, bytes;
    } fs;
    IO_STATUS_BLOCK iosb;
    u32 cluster_kb;

    if (NtQueryVolumeInformationFile(h, &iosb, &fs, sizeof(fs), FileFsSizeInformation))
        return 0xFFFFFFFFu;
    cluster_kb = fs.sectors * fs.bytes / 1024;
    if (fs.available >> 32 || (cluster_kb && (u32)fs.available > 0xFFFFFFFFu / cluster_kb))
        return 0xFFFFFFFFu;
    return (u32)fs.available * cluster_kb;
}

static int same_name(const char *a, const char *b)
{
    u32 i;

    for (i = 0; a[i] && (a[i] | 0x20) == (b[i] | 0x20); i++)
        ;
    return !a[i] && !b[i];
}

/* Once per launch, before any transfer: checkpoints sent by the server (char-*.ess and their
 * parts) other than the one this launch loaded. A new one arrives with each join that needs it. */
#define SWEEP_FILES 32u
static u32 sweep_done, swept;

static void checkpoint_sweep(void)
{
    static u8 info[1024];
    static char names[SWEEP_FILES][BULK_NAME + 6];
    char mask_text[] = "char-*", path[16 + BULK_NAME + 8];
    ANSI_STRING mask;
    IO_STATUS_BLOCK iosb;
    u32 count = 0, n, off, i, restart = 1, gone = 0;
    const u8 *e;
    void *dir, *h;
    u8 del = 1;

    if (sweep_done)
        return;
    sweep_done = 1;
    if (bulk_open("U:\\TES3X", GENERIC_READ, FILE_OPEN, FILE_DIRECTORY_FILE, &dir))
        return;
    mask.Buffer = mask_text;
    mask.Length = mask.MaximumLength = sizeof(mask_text) - 1;
    while (count < SWEEP_FILES &&
           !NtQueryDirectoryFile(dir, 0, 0, 0, &iosb, info, sizeof(info),
                                 FileDirectoryInformation, &mask, restart)) {
        restart = 0;
        for (off = 0; off < sizeof(info) && count < SWEEP_FILES; off += n) {
            e = info + off;
            i = *(const u32 *)(e + 0x3C);
            if (i && i < sizeof(names[0])) {
                copy((u8 *)names[count], e + 0x40, i);
                names[count][i] = 0;
                if (!same_name(names[count], game_loaded))
                    count++;
            }
            if (!(n = *(const u32 *)e))
                break;
        }
    }
    NtClose(dir);
    for (i = 0; i < count; i++) {
        named_path(path, names[i], "");
        if (!bulk_open(path, DELETE_ACCESS, FILE_OPEN, 0, &h)) {
            gone += !NtSetInformationFile(h, &iosb, &del, sizeof(del), FileDispositionInformation);
            NtClose(h);
        }
    }
    swept = gone;
    worker_log("net.checkpoints_swept", count, gone, 0);
}

static void bulk_work(void)
{
    char path[16 + BULK_NAME + 8], part[16 + BULK_NAME + 8];
    IO_STATUS_BLOCK iosb;
    u64 offset, size;
    u32 flags, s, n, next, state, wrote = 0, need, free;
    void *h;

    checkpoint_sweep();
    bulk_adopt();
    if (bulk.state == BULK_OPENING) {
        bulk_path(path, "");
        bulk_path(part, ".part");
        state = BULK_RECEIVING;
        next = 0;
        if (bulk_verify(path)) {
            state = BULK_DONE;
            next = bulk.chunks;
        } else if (bulk_open("U:\\TES3X", GENERIC_READ, FILE_OPEN_IF, FILE_DIRECTORY_FILE, &h)) {
            state = BULK_FAILED;
        } else {
            NtClose(h);
            if (bulk_open(part, GENERIC_READ | GENERIC_WRITE, FILE_OPEN_IF, 0, &bulk.file)) {
                state = BULK_FAILED;
            } else {
                size = bulk_size(bulk.file);
                next = size > bulk.total ? 0 : (u32)size / BULK_CHUNK;
                if (next)
                    bulk.resumed++;
                need = (bulk.total - next * BULK_CHUNK + 1023) / 1024 + BULK_SPACE_KB;
                free = volume_free_kb(bulk.file);
                worker_log("net.bulk_space", bulk.id, free, need);
                if (free < need) {
                    worker_log("net.bulk_no_space", bulk.id, free, need);
                    bulk_close();
                    state = BULK_NO_SPACE;
                }
            }
        }
        worker_log("net.bulk_start", bulk.id, next, state);
        flags = lock();
        bulk.next = next;
        bulk.state = state;
        bulk_ack();
        unlock(flags);
    }
    if (bulk.state != BULK_RECEIVING)
        return;
    for (;;) {
        s = bulk.next % BULK_SLOTS;
        if (bulk.next >= bulk.chunks || !(bulk.filled >> s & 1))
            break;
        /* The DPC writes this slot only once next has moved past it. */
        n = bulk_chunk_bytes(bulk.next);
        offset = (u64)bulk.next * BULK_CHUNK;
        if (NtWriteFile(bulk.file, 0, 0, 0, &iosb, bulk.slot[s], n, &offset) ||
            iosb.Information != n) {
            worker_log("net.bulk_write_failed", bulk.id, bulk.next, 0);
            bulk_close();
            flags = lock();
            bulk.state = BULK_FAILED;
            bulk_ack();
            unlock(flags);
            return;
        }
        flags = lock();
        bulk.filled &= ~(1u << s);
        bulk.next++;
        unlock(flags);
        bulk.written++;
        wrote = 1;
    }
    if (bulk.next < bulk.chunks) {
        if (wrote) {
            flags = lock();
            bulk_ack();
            unlock(flags);
        }
        return;
    }
    offset = bulk.total;
    NtSetInformationFile(bulk.file, &iosb, &offset, sizeof(offset), FileEndOfFileInformation);
    NtFlushBuffersFile(bulk.file, &iosb);
    bulk_close();
    bulk_path(path, "");
    bulk_path(part, ".part");
    state = BULK_BAD_HASH;
    if (bulk_verify(part)) {
        state = file_replace(part, path) ? BULK_FAILED : BULK_DONE;
    } else if (!bulk_open(part, GENERIC_WRITE, FILE_OPEN, 0, &h)) {
        offset = 0; /* start over */
        NtSetInformationFile(h, &iosb, &offset, sizeof(offset), FileEndOfFileInformation);
        NtClose(h);
    }
    worker_log("net.bulk_done", bulk.id, bulk.total, state);
    flags = lock();
    bulk.state = state;
    bulk_ack();
    unlock(flags);
}

/* Every TICK_MS; caller holds the lock. The sender resends what it has not seen acked. */
static void bulk_tick(void)
{
    if (bulk.state == BULK_RECEIVING)
        bulk_ack();
}

/* Bulk transfer to the server, the same packets the other way: `tes3xnet send NAME` offers
 * U:\TES3X\NAME as an OFFER event, and the server's BULK_ACK says which chunk it needs next,
 * which of those after it arrived and how many may be in flight. The file work hashes the file
 * and keeps the window's chunks in UP_SLOTS; the receive DPC, the tick and the file work send
 * them. A chunk goes again after UP_RESEND_US, or at once when one sent after it has arrived.
 * After a WELCOME the offer goes again, and the server resumes from its part. */
#define UP_SLOTS 8u
#define UP_EMPTY 0xFFFFFFFFu
#define UP_RESEND_US 500000u
#define UP_PROBE_US 1000000u
#define UP_IDLE 0u
#define UP_WANT 1u    /* named; the file work opens and hashes it */
#define UP_OFFER 2u   /* hashed; the game thread queues the OFFER */
#define UP_OFFERED 3u /* waiting for the server's first ack */
#define UP_SENDING 4u
#define UP_DONE 5u
#define UP_FAILED 6u /* unreadable here, or refused or failed there */
static struct {
    u32 state, id, total, chunks, next, window, seen, status, welcomes, last_ack, first;
    u32 index[UP_SLOTS], serial[UP_SLOTS], sent_at[UP_SLOTS], lost, serials;
    u32 sent, resent, fast, acks, logged;
    void *file;
    u8 hash[BULK_HASH];
    char name[BULK_NAME + 1];
    char path[80]; /* read from here when set, else U:\TES3X\NAME */
    u8 slot[UP_SLOTS][BULK_CHUNK];
} up;

/* Caller holds the lock. */
static void up_send_slot(u32 s, u32 now)
{
    static u8 body[8 + BULK_CHUNK];
    u32 i = up.index[s], n = i + 1 < up.chunks ? BULK_CHUNK : up.total - i * BULK_CHUNK;

    put32le(body, up.id);
    put32le(body + 4, i);
    copy(body + 8, up.slot[s], n);
    session_send(T3MP_CHUNK, body, 8 + n);
    up.resent += up.serial[s] != 0;
    up.fast += up.lost >> s & 1;
    up.lost &= ~(1u << s);
    up.serial[s] = ++up.serials;
    up.sent_at[s] = now;
    up.sent++;
}

/* Caller holds the lock: send what is loaded and due; with nothing due and no ack for a while, one
 * chunk again, to draw an ack whose predecessor was lost. */
static void up_pump(void)
{
    u32 i, s, now = now_us(), limit, sent = 0;

    if (up.state != UP_SENDING || ses.state != SESSION_JOINED)
        return;
    limit = up.window < UP_SLOTS ? up.window : UP_SLOTS;
    for (i = up.next; i < up.next + limit && i < up.chunks; i++) {
        s = i % UP_SLOTS;
        if (up.index[s] != i || (up.seen >> (i - up.next) & 1))
            continue;
        if (up.serial[s] && !(up.lost >> s & 1) && now - up.sent_at[s] < UP_RESEND_US)
            continue;
        up_send_slot(s, now);
        sent++;
    }
    s = up.next % UP_SLOTS;
    if (!sent && now - up.last_ack >= UP_PROBE_US && up.next < up.chunks && up.index[s] == up.next) {
        up.last_ack = now;
        up_send_slot(s, now);
    }
}

/* Caller holds the lock (the receive DPC). */
static void up_ack_rx(const u8 *p, u32 n)
{
    u32 next, seen, k, s, top, last;

    if (n < BULK_ACK_BYTES || !up.id || get32le(p) != up.id ||
        (up.state != UP_OFFERED && up.state != UP_SENDING) || (next = get32le(p + 4)) > up.chunks)
        return;
    up.acks++;
    up.last_ack = now_us();
    if (up.state == UP_OFFERED)
        up.first = next;
    seen = get32le(p + 8) & ((1u << UP_SLOTS) - 1);
    up.next = next;
    up.seen = seen;
    up.window = get32le(p + 12);
    up.status = get32le(p + 16);
    if (up.status != BULK_RECEIVING) {
        up.state = up.status == BULK_DONE ? UP_DONE : UP_FAILED;
        return;
    }
    up.state = UP_SENDING;
    for (top = 0, k = 1; k < UP_SLOTS; k++)
        if (seen >> k & 1)
            top = k;
    if (top && up.index[s = (next + top) % UP_SLOTS] == next + top) {
        last = up.serial[s];
        for (k = 0; k < top; k++)
            if (!(seen >> k & 1) && up.index[s = (next + k) % UP_SLOTS] == next + k &&
                up.serial[s] && up.serial[s] < last)
                up.lost |= 1u << s;
    }
    up_pump();
}

static void up_close(void)
{
    if (up.file)
        NtClose(up.file);
    up.file = 0;
}

/* File work: open and hash a named file, then keep the window's chunks loaded. */
static void up_work(void)
{
    crypto_blake2b_ctx ctx;
    IO_STATUS_BLOCK iosb;
    char path[sizeof(up.path)];
    u64 offset, size;
    u32 flags, i, s, n, left, limit, state = UP_FAILED;

    if (up.state == UP_WANT) {
        up_close();
        if (up.path[0])
            copy((u8 *)path, (const u8 *)up.path, sizeof(path));
        else
            named_path(path, up.name, "");
        if (!bulk_open(path, GENERIC_READ, FILE_OPEN, 0, &up.file) &&
            (size = bulk_size(up.file)) <= BULK_MAX) {
            crypto_blake2b_init(&ctx, BULK_HASH);
            for (offset = 0, left = (u32)size; left; offset += n, left -= n) {
                n = left < sizeof(up.slot) ? left : sizeof(up.slot);
                if (NtReadFile(up.file, 0, 0, 0, &iosb, up.slot, n, &offset) ||
                    iosb.Information != n)
                    break;
                crypto_blake2b_update(&ctx, (const u8 *)up.slot, n);
            }
            crypto_blake2b_final(&ctx, up.hash);
            if (!left)
                state = UP_OFFER;
            up.total = (u32)size;
        }
        flags = lock();
        up.id = get32le(up.hash) ? get32le(up.hash) : 1;
        up.chunks = (up.total + BULK_CHUNK - 1) / BULK_CHUNK;
        up.next = up.window = up.seen = up.lost = 0;
        for (i = 0; i < UP_SLOTS; i++)
            up.index[i] = UP_EMPTY;
        up.state = state;
        unlock(flags);
        worker_log("net.upload_ready", up.id, up.total, state);
    }
    if (up.state == UP_SENDING) {
        limit = up.window < UP_SLOTS ? up.window : UP_SLOTS;
        for (i = up.next; i < up.next + limit && i < up.chunks; i++) {
            if (up.index[s = i % UP_SLOTS] == i)
                continue;
            /* The slot's chunk is acked, so neither the DPC nor the tick sends it now. */
            n = i + 1 < up.chunks ? BULK_CHUNK : up.total - i * BULK_CHUNK;
            offset = (u64)i * BULK_CHUNK;
            if (NtReadFile(up.file, 0, 0, 0, &iosb, up.slot[s], n, &offset) ||
                iosb.Information != n) {
                flags = lock();
                up.state = UP_FAILED;
                unlock(flags);
                worker_log("net.upload_read_failed", up.id, i, 0);
                break;
            }
            flags = lock();
            up.index[s] = i;
            up.serial[s] = 0;
            up.lost &= ~(1u << s);
            up_pump();
            unlock(flags);
        }
    }
    if ((up.state == UP_DONE || up.state == UP_FAILED) && up.file)
        up_close();
}

/* Game thread: offer what is hashed, and again after a WELCOME. */
static void up_frame(void)
{
    u8 data[BULK_OFFER_BYTES + BULK_NAME + 1];
    u32 flags, n;

    flags = lock();
    if ((up.state == UP_OFFERED || up.state == UP_SENDING) && up.welcomes != ses.welcomes)
        up.state = UP_OFFER;
    unlock(flags);
    if (up.state == UP_OFFER && ses.state == SESSION_JOINED) {
        put32le(data, up.id);
        put32le(data + 4, up.total);
        copy(data + 8, up.hash, BULK_HASH);
        n = tes3x_strlen(up.name) + 1;
        copy(data + BULK_OFFER_BYTES, (const u8 *)up.name, n);
        if (event_queue(EVENT_OFFER, data, BULK_OFFER_BYTES + n)) {
            flags = lock();
            up.welcomes = ses.welcomes;
            up.last_ack = now_us();
            up.state = UP_OFFERED;
            unlock(flags);
        }
    }
    if (up.state != up.logged) {
        up.logged = up.state;
        tes3x_log_hex3("net.upload_state", up.id, up.state, up.next);
    }
}

/* Game thread: `tes3xnet send NAME`. */
/* Sends NAME, read from path when given; 0 if NAME is not a plain file name or an upload is under
 * way. */
static int up_start(const char *name, const char *path)
{
    u32 i, flags;

    for (i = 0; i < BULK_NAME && name[i]; i++) {
        char c = name[i];
        if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') ||
              c == ' ' || c == '.' || c == '-' || c == '_') || (!i && c == '.'))
            break;
    }
    if (!i || name[i] || (up.state >= UP_WANT && up.state <= UP_SENDING) ||
        (path && tes3x_strlen(path) >= sizeof(up.path))) {
        tes3x_log("net.upload_refused", up.state);
        return 0;
    }
    copy((u8 *)up.name, (const u8 *)name, i + 1);
    up.path[0] = 0;
    if (path)
        copy((u8 *)up.path, (const u8 *)path, tes3x_strlen(path) + 1);
    flags = lock();
    up.state = UP_WANT;
    up.sent = up.resent = up.fast = up.acks = 0;
    unlock(flags);
    log_text("net.upload_name", up.name);
    return 1;
}

static void up_command(const char *name)
{
    up_start(name, 0);
}

static void up_stat(void)
{
    if (!up.id)
        return;
    tes3x_log_hex3("net.upload", up.id, up.state, up.next);
    tes3x_log_hex3("net.upload_chunks", up.sent, up.resent, up.fast);
    tes3x_log_hex3("net.upload_acks", up.acks, up.first, up.total);
}

static void bulk_stat(void)
{
    if (!bulk.id)
        return;
    tes3x_log_hex3("net.bulk", bulk.id, bulk.state, bulk.next);
    tes3x_log_hex3("net.bulk_chunks", bulk.arrived, bulk.duplicates, bulk.written);
    tes3x_log_hex3("net.bulk_acks", bulk.acks, bulk.resumed, bulk.total);
}

/* Randomness: the kernel exports none. RDTSC is sampled at every NIC interrupt and DPC, every
 * tick and frame and around trust-file reads; the game thread hashes the samples into a pool
 * with BLAKE2b and draws from it with keyed BLAKE2b once ENTROPY_NEEDED samples are in. */
#define ENTROPY_RING 64u
#define ENTROPY_NEEDED 512u

static volatile u32 entropy_ring[ENTROPY_RING], entropy_in;
static struct {
    u32 out, mixed, drawn;
    u8 pool[64];
} entropy;

/* Any context; a sample lost to a race costs nothing. */
static void entropy_add(void)
{
    u32 lo, hi;

    __asm__ volatile("rdtsc" : "=a"(lo), "=d"(hi));
    entropy_ring[entropy_in++ % ENTROPY_RING] = lo;
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

/* Game thread. Up to 64 bytes; 0 until the pool has ENTROPY_NEEDED samples. */
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
    label[4] = 'n';
    label[5] = 'e';
    label[6] = 'x';
    label[7] = 't';
    crypto_blake2b_keyed(block, sizeof(block), entropy.pool, sizeof(entropy.pool), label, 8);
    copy(entropy.pool, block, sizeof(block));
    crypto_wipe(block, sizeof(block));
    return 1;
}

/* U:\TES3X\servers.ini keeps, per server as NetServer names it, the server's pinned static key,
 * this console's own secret key for it (its identity there) and the password typed at Join, which
 * goes in place of NetPassword. Unknown lines are kept. */
#define TRUST_TEXT 4096u

static char trust_path[] = "U:\\TES3X\\servers.ini";
static char trust_new[] = "U:\\TES3X\\servers.ini.new";
static struct {
    u32 loaded, text_n, has_fingerprint, has_server, has_client, dirty, password_n;
    char name[HOST_NAME + 8], password[PASSWORD_MAX + 1];
    u8 fingerprint[TRUST_FINGERPRINT], server[NOISE_KEY], client[NOISE_KEY];
    char text[TRUST_TEXT];
} trust;
static struct {
    volatile u32 pending;
    u32 len;
    char text[TRUST_TEXT + 256];
} trust_job;

static int hex_digit(char c)
{
    return c >= '0' && c <= '9' ? c - '0' : c >= 'a' && c <= 'f' ? c - 'a' + 10 :
           c >= 'A' && c <= 'F' ? c - 'A' + 10 : -1;
}

/* 1 if text starts with 2n hex digits, read into out. */
static int hex_read(const char *text, u8 *out, u32 n)
{
    u32 i;
    int hi, lo;

    for (i = 0; i < n; i++) {
        if ((hi = hex_digit(text[2 * i])) < 0 || (lo = hex_digit(text[2 * i + 1])) < 0)
            return 0;
        out[i] = (u8)(hi << 4 | lo);
    }
    return 1;
}

static u32 hex_write(char *out, const u8 *p, u32 n)
{
    static const char digits[] = "0123456789abcdef";
    u32 i;

    for (i = 0; i < n; i++) {
        out[2 * i] = digits[p[i] >> 4];
        out[2 * i + 1] = digits[p[i] & 15];
    }
    return 2 * n;
}

/* The length of the line at text, without its end. */
static u32 line_length(const char *text, u32 left)
{
    u32 n = 0;

    while (n < left && text[n] != '\r' && text[n] != '\n')
        n++;
    return n;
}

/* 1 if the line is "[name]" for this server, ignoring case. */
static int trust_section(const char *line, u32 n)
{
    u32 i, len = tes3x_strlen(trust.name);

    if (n != len + 2 || line[0] != '[' || line[n - 1] != ']')
        return 0;
    for (i = 0; i < len; i++)
        if ((line[1 + i] | 0x20) != (trust.name[i] | 0x20))
            return 0;
    return 1;
}

static int starts(const char *line, u32 n, const char *key)
{
    u32 i;

    for (i = 0; key[i]; i++)
        if (i >= n || line[i] != key[i])
            return 0;
    return 1;
}

/* Game thread, at `up`: the section is NetServer as given, without its fingerprint. */
static void trust_configure(const char *name, u32 n, const u8 *fingerprint)
{
    trust.loaded = 0;
    trust.has_fingerprint = fingerprint != 0;
    if (fingerprint)
        copy(trust.fingerprint, fingerprint, TRUST_FINGERPRINT);
    if (n >= sizeof(trust.name))
        n = sizeof(trust.name) - 1;
    copy((u8 *)trust.name, (const u8 *)name, n);
    trust.name[n] = 0;
}

/* Game thread, before the first handshake of each `up`. */
static void trust_load(void)
{
    IO_STATUS_BLOCK iosb;
    u64 offset = 0, size;
    u32 off, n, inside = 0;
    void *h;

    if (trust.loaded)
        return;
    trust.loaded = 1;
    trust.text_n = 0;
    trust.has_server = trust.has_client = trust.dirty = trust.password_n = 0;
    entropy_add();
    if (bulk_open(trust_path, GENERIC_READ, FILE_OPEN, 0, &h))
        return;
    size = bulk_size(h);
    if (size >= TRUST_TEXT) {
        trust.text_n = TRUST_TEXT; /* too big to rewrite safely: keys stay in memory only */
        tes3x_log("net.trust_too_big", (u32)size);
    } else if (size && !NtReadFile(h, 0, 0, 0, &iosb, trust.text, (u32)size, &offset)) {
        trust.text_n = iosb.Information;
    }
    NtClose(h);
    entropy_add();
    for (off = 0; off < trust.text_n && trust.text_n < TRUST_TEXT; off += n + 1) {
        const char *line = trust.text + off;
        n = line_length(line, trust.text_n - off);
        if (n && line[0] == '[')
            inside = trust_section(line, n);
        else if (inside && starts(line, n, "server_key=") && n >= 11 + 2 * NOISE_KEY)
            trust.has_server = hex_read(line + 11, trust.server, NOISE_KEY);
        else if (inside && starts(line, n, "client_key=") && n >= 11 + 2 * NOISE_KEY)
            trust.has_client = hex_read(line + 11, trust.client, NOISE_KEY);
        else if (inside && starts(line, n, "password=") && n - 9 <= PASSWORD_MAX)
            copy((u8 *)trust.password, (const u8 *)line + 9, trust.password_n = n - 9);
    }
    tes3x_log_hex3("net.trust", trust.text_n, trust.has_server, trust.has_client);
}

/* Game thread: the file with this server's section last, for trust_write. */
static void trust_save(void)
{
    static char out[TRUST_TEXT + 256];
    u32 off, n, len = 0, inside = 0;

    if (trust.text_n >= TRUST_TEXT)
        return;
    for (off = 0; off < trust.text_n; off += n + 1) {
        const char *line = trust.text + off;
        n = line_length(line, trust.text_n - off);
        if (n && line[0] == '[')
            inside = trust_section(line, n);
        if (!inside && n && len + n + 2 <= TRUST_TEXT) {
            copy((u8 *)out + len, (const u8 *)line, n);
            len += n;
            out[len++] = '\r';
            out[len++] = '\n';
        }
    }
    out[len++] = '[';
    copy((u8 *)out + len, (const u8 *)trust.name, tes3x_strlen(trust.name));
    len += tes3x_strlen(trust.name);
    copy((u8 *)out + len, (const u8 *)"]\r\nserver_key=", 14);
    len += 14;
    len += hex_write(out + len, trust.server, NOISE_KEY);
    copy((u8 *)out + len, (const u8 *)"\r\nclient_key=", 13);
    len += 13;
    len += hex_write(out + len, trust.client, NOISE_KEY);
    if (trust.password_n) {
        copy((u8 *)out + len, (const u8 *)"\r\npassword=", 11);
        len += 11;
        copy((u8 *)out + len, (const u8 *)trust.password, trust.password_n);
        len += trust.password_n;
    }
    out[len++] = '\r';
    out[len++] = '\n';
    if (len <= TRUST_TEXT) {
        copy((u8 *)trust.text, (const u8 *)out, len);
        trust.text_n = len;
    }
    copy((u8 *)trust_job.text, (const u8 *)out, len);
    trust_job.len = len;
    trust_job.pending = 1;
    trust.dirty = 0;
    crypto_wipe(out, sizeof(out));
}

/* File work: through a new file and a rename, so a failed write never loses the keys there. */
static void trust_write(void)
{
    IO_STATUS_BLOCK iosb;
    u32 status;
    void *h;

    if (!trust_job.pending)
        return;
    status = bulk_open("U:\\TES3X", GENERIC_READ, FILE_OPEN_IF, FILE_DIRECTORY_FILE, &h);
    if (!status) {
        NtClose(h);
        status = bulk_open(trust_new, GENERIC_WRITE, FILE_OVERWRITE_IF, 0, &h);
    }
    if (!status) {
        u64 offset = 0;
        status = NtWriteFile(h, 0, 0, 0, &iosb, trust_job.text, trust_job.len, &offset);
        NtFlushBuffersFile(h, &iosb);
        NtClose(h);
    }
    if (!status)
        status = file_replace(trust_new, trust_path);
    worker_log("net.trust_saved", trust_job.len, status, 0);
    crypto_wipe(trust_job.text, sizeof(trust_job.text));
    trust_job.pending = 0;
}

static void file_work(void)
{
    trust_write();
    bulk_work();
    up_work();
}

/* The KTHREAD running now: the KPCR's PrcbData.CurrentThread. */
static void *current_thread(void)
{
    void *t;

    __asm__ volatile("movl %%fs:0x28, %0" : "=r"(t));
    return t;
}

/* Above the game thread, which never yields: at its priority the worker would wait for the end
 * of its time slice, and the bulk window with it. It mostly waits on the disk. */
#define WORKER_PRIORITY 2

static void __stdcall worker_thread(void *context)
{
    long long wait = -(long long)WORKER_SLEEP_MS * 10000;

    (void)context;
    KeSetBasePriorityThread(current_thread(), WORKER_PRIORITY);
    while (!worker.stop) {
        KeDelayExecutionThread(0, 0, &wait);
        file_work();
    }
    trust_write();
    worker.running = 0;
}

static void __stdcall worker_system(void(__stdcall *start)(void *), void *context)
{
    start(context);
    PsTerminateSystemThread(0);
}

static void worker_start(void)
{
    void *h = 0;
    u32 status;

    if (worker.running)
        return;
    worker.stop = 0;
    worker.running = 1;
    status = PsCreateSystemThreadEx(&h, 0, 0x4000, 0, 0, worker_thread, 0, 0, 0,
                                    (void *)worker_system);
    if (status) {
        worker.running = 0;
        tes3x_log_hex("net.worker_failed", status);
    } else {
        NtClose(h);
        tes3x_log_hex3("net.worker_priority", (u32)KeQueryBasePriorityThread(current_thread()),
                       WORKER_PRIORITY, 0);
    }
}

/* Waits up to two seconds for the file work in hand. */
static void worker_stop(void)
{
    long long wait = -(long long)WORKER_SLEEP_MS * 10000;
    u32 i;

    worker.stop = 1;
    for (i = 0; i < 2000 / WORKER_SLEEP_MS && worker.running; i++)
        KeDelayExecutionThread(0, 0, &wait);
}

/* Game thread, each frame while up. Without a worker the file work runs here. */
static void file_frame(void)
{
    u32 flags, i, n;
    const char *tags[WORKER_LOGS];
    u32 values[WORKER_LOGS][3];

    flags = lock();
    n = worker.count;
    for (i = 0; i < n; i++) {
        tags[i] = worker.log[i].tag;
        values[i][0] = worker.log[i].a;
        values[i][1] = worker.log[i].b;
        values[i][2] = worker.log[i].c;
    }
    worker.count = 0;
    unlock(flags);
    for (i = 0; i < n; i++)
        tes3x_log_hex3(tags[i], values[i][0], values[i][1], values[i][2]);
    /* Not before the server's key is known: the section always carries one. */
    if (trust.dirty && trust.has_server && !trust_job.pending)
        trust_save();
    if (!worker.running)
        file_work();
    up_frame();
}

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

/* A save holds the game thread for seconds while the heartbeat DPC keeps the session: BUSY tells
 * the server to hand this console's cells and actors to another player until it is back. */
#define EVENT_BUSY 21u /* BUSY_SAVING, or 0 once back */
#define BUSY_SAVING 1u

typedef unsigned char(__attribute__((thiscall)) *fn_save_game)(void *, const char *, const char *);
typedef u32(__attribute__((stdcall)) *fn_create_save)(const char *root, const u16 *name, u32 how,
                                                      u32 options, char *path, u32 size);
static const u32 save_sites[] = TES3X_NET_SAVE_SITES;
static u32 save_hooked, saves_seen, saves_busy, saves_slotted, saves_uploaded, save_pending;
#define OPEN_EXISTING 3u
/* While joined every save goes to one slot per server and character, "MP <character>
 * <server>": SaveGame names the folder by a hash of its file name, so the slot never meets a
 * single-player save, and its name with ".ess" stays within BULK_NAME. */
#define SLOT_CHARACTER 16u
#define SLOT_SERVER 13u
static char save_slot[3 + SLOT_CHARACTER + 1 + SLOT_SERVER + 1];
static char save_path[sizeof(up.path)], save_name[BULK_NAME + 1];

static u32 slot_text(char *out, const char *text, u32 cap)
{
    u32 i;
    char c;

    for (i = 0; i < cap && (c = text[i]); i++)
        out[i] = (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') ||
                 c == ' ' || c == '_' ? c : '-';
    return i;
}

/* 0 when there is no player name yet. */
static int slot_name(void)
{
    const u8 *ref = player_reference(), *base;
    const char *name;
    u32 n = 3, k;

    /* the player's NPCInstance, its baseNPC (+0x6C) and that NPC's name (+0x70) */
    if (!ref || !plausible(base = *(const u8 *const *)(ref + 0x28)) ||
        !plausible(base = *(const u8 *const *)(base + 0x6C)) ||
        !mapped(name = *(const char *const *)(base + 0x70)) || !name[0])
        return 0;
    copy((u8 *)save_slot, (const u8 *)"MP ", 3);
    n += slot_text(save_slot + n, name, SLOT_CHARACTER);
    save_slot[n++] = ' ';
    k = slot_text(save_slot + n, trust.name, SLOT_SERVER);
    if (!k)
        n--;
    save_slot[n + k] = 0;
    return 1;
}

/* After the save: its folder from XCreateSaveGame, then the upload. */
static void slot_upload(void)
{
    u16 wide[sizeof(save_slot)];
    u32 i, n, r;

    for (i = 0; i < sizeof(save_slot); i++)
        wide[i] = (u8)save_slot[i];
    save_path[0] = 0;
    r = ((fn_create_save)TES3X_NET_CREATE_SAVE)("U:\\", wide, OPEN_EXISTING, 0, save_path,
                                                sizeof(save_path) - sizeof(save_slot) - 4);
    n = tes3x_strlen(save_path);
    tes3x_log_hex3("net.save_slot", r, n, 0);
    if (r || !n)
        return;
    if (save_path[n - 1] != '\\')
        save_path[n++] = '\\';
    i = tes3x_strlen(save_slot);
    copy((u8 *)save_path + n, (const u8 *)save_slot, i);
    copy((u8 *)save_path + n + i, (const u8 *)".ess", 5);
    copy((u8 *)save_name, (const u8 *)save_slot, i);
    copy((u8 *)save_name + i, (const u8 *)".ess", 5);
    log_text("net.save_path", save_path);
    save_pending = 1;
}

#define EVENT_SAVE 22u /* the server asks for a save */
#define CHARGEN_STATE 0xBCu /* WorldController global, -1 once character generation is done */
static u32 save_requested, saves_requested;
unsigned char __attribute__((thiscall)) tes3x_net_save(void *game, const char *file,
                                                      const char *display);

static u32 player_dead; /* a joined player's death waits for its respawn */

/* A requested save waits for the world: no menu, chargen done, the player named, alive. */
static void save_request_frame(void)
{
    const u8 *world = *(const u8 **)TES3X_NET_WORLD, *global;

    if (!save_requested || player_dead || !plausible(world) || world[0xD2] ||
        !plausible(global = *(const u8 *const *)(world + CHARGEN_STATE)) ||
        *(const u32 *)(global + 0x34) != 0xBF800000u || !slot_name())
        return;
    save_requested = 0;
    saves_requested++;
    tes3x_net_save(**(void ***)TES3X_NET_DATA_HANDLER, save_slot, save_slot);
}

/* Joining from the server's copy of the character. After each WELCOME the console reports this
 * launch's token and the save it was launched to load; the server sends a console that is not
 * running its character's latest checkpoint the file (bulk, into U:\TES3X\) and LOAD, and the
 * console relaunches into it as the Load menu does. */
#define EVENT_GAME 23u /* launch token, then the name of the save this launch loaded, or "" */
#define EVENT_LOAD 24u /* a file the server sent: load it */
#define LAUNCH_DATA 0x400u /* in LaunchDataPage, after the header */
#define BXWM_MAGIC 0x4D575842u
#define BXWM_NEW_GAME 0u
#define BXWM_LOAD 1u
#define BXWM_PATH 0x10u
#define BXWM_PATH_MAX 0x100u
#define GAME_NONE 0u /* the launch kind GAME ends with */
#define GAME_LOAD 1u
#define GAME_NEW 2u
static u32 game_token, game_told, game_launch, load_wanted, loads_started;
static char load_name[BULK_NAME + 1];
/* A launch the main menu's Join led to carries JOIN_MAGIC and the server after the path, so it
 * joins that server without NetServer. */
#define JOIN_MAGIC 0x4A4D3354u /* "T3MJ" */
#define JOIN_AT (BXWM_PATH + BXWM_PATH_MAX)
typedef u32(__attribute__((stdcall)) *fn_launch)(const char *xbe, void *data);
typedef u32(__cdecl *fn_persist)(void);

/* XBE entry, before the engine reads its launch data. */
void tes3x_net_entry(void)
{
    const u8 *page = *(const u8 *const *)*(void ***)THUNK_LaunchDataPage, *data, *path;
    u32 lo, hi, i, base = 0;

    __asm__ volatile("rdtsc" : "=a"(lo), "=d"(hi));
    game_token = (lo ^ hi << 16) | 1;
    if (!page || *(const u32 *)page != 0)
        return;
    data = page + LAUNCH_DATA;
    if (*(const u32 *)data != BXWM_MAGIC)
        return;
    if (*(const u32 *)(data + JOIN_AT) == JOIN_MAGIC) {
        for (i = 0; i < JOIN_NAME && data[JOIN_AT + 4 + i]; i++)
            join_server[i] = (char)data[JOIN_AT + 4 + i];
        join_server[i] = 0;
    }
    if (*(const u32 *)(data + 0xC) == BXWM_NEW_GAME)
        game_launch = GAME_NEW;
    if (*(const u32 *)(data + 0xC) != BXWM_LOAD)
        return;
    game_launch = GAME_LOAD;
    path = data + BXWM_PATH;
    for (i = 0; i < BXWM_PATH_MAX && path[i]; i++)
        if (path[i] == '\\')
            base = i + 1;
    for (i = 0; i < BULK_NAME && i + base < BXWM_PATH_MAX && path[base + i]; i++)
        game_loaded[i] = (char)path[base + i];
    game_loaded[i] = 0;
}

static void load_event(const struct event *e)
{
    u32 i;

    for (i = 0; i < e->length && i < BULK_NAME && e->data[i]; i++)
        load_name[i] = (char)e->data[i];
    load_name[i] = 0;
    load_wanted = i != 0;
    log_text("net.load_wanted", load_name);
}

/* The title relaunch Load and New Game make: Load of U:\TES3X\name, or New Game without a name.
 * 0 while the world cannot give the pad port. */
static int relaunch(const char *name)
{
    static u8 data[JOIN_AT + 4 + JOIN_NAME + 1];
    const u8 *world = *(const u8 **)TES3X_NET_WORLD, *pads;
    static const char dir[] = "U:\\TES3X\\";
    u32 i, n = 0, r;

    if (!plausible(world) || !plausible(pads = *(const u8 *const *)(world + 0x4C)))
        return 0;
    for (i = 0; i < sizeof(data); i++)
        data[i] = 0;
    ((u32 *)data)[0] = BXWM_MAGIC;
    ((u32 *)data)[1] = *(const u32 *)(pads + 0x804); /* the pad port, as the Load menu passes */
    ((u32 *)data)[3] = name ? BXWM_LOAD : BXWM_NEW_GAME;
    if (name) {
        for (; dir[n]; n++)
            data[BXWM_PATH + n] = (u8)dir[n];
        for (i = 0; name[i]; i++)
            data[BXWM_PATH + n + i] = (u8)name[i];
    }
    if (join_server[0]) {
        *(u32 *)(data + JOIN_AT) = JOIN_MAGIC;
        for (i = 0; join_server[i]; i++)
            data[JOIN_AT + 4 + i] = (u8)join_server[i];
    }
    log_text("net.load", name ? name : "(new game)");
    ((fn_persist)TES3X_NET_PERSIST_DISPLAY)();
    r = ((fn_launch)TES3X_NET_LAUNCH)((const char *)TES3X_NET_ENGINE_PATH, data);
    tes3x_log("net.load_failed", r);
    return 1;
}

/* Once the file is here, the Load menu's relaunch with its path. */
static void load_frame(void)
{
    u32 i;

    if (load_wanted && bulk.state == BULK_NO_SPACE && same_name(bulk.name, load_name)) {
        load_wanted = 0;
        log_text("net.load_no_space", load_name);
        run_script("MessageBox \"There is not enough free space on the hard disk to load your "
                   "character from the server.\"");
        return;
    }
    if (!load_wanted || bulk.state != BULK_DONE)
        return;
    for (i = 0; load_name[i] && bulk.name[i] == load_name[i]; i++)
        ;
    if (load_name[i] || bulk.name[i])
        return;
    if (relaunch(load_name))
        load_wanted = 0, loads_started++;
}

static void game_frame(void)
{
    u8 body[4 + BULK_NAME + 2];
    u32 n = tes3x_strlen(game_loaded) + 1;

    if (game_told == ses.welcomes)
        return;
    put32le(body, game_token);
    copy(body + 4, (const u8 *)game_loaded, n);
    body[4 + n] = (u8)game_launch;
    if (event_queue(EVENT_GAME, body, 4 + n + 1)) {
        game_told = ses.welcomes;
        log_text("net.game_loaded", game_loaded[0] ? game_loaded : "(none)");
    }
}

/* Leaving while joined: Exit's Yes saves into the slot, uploads it and waits for the server to
 * have the whole file before the engine's own quit runs, so the server keeps where the player
 * stopped. If the server does not confirm, the player chooses to leave without it or stay. */
#define LEAVE_SAVE 1u
#define LEAVE_UPLOAD 2u
#define LEAVE_ASK 3u
#define LEAVE_TIMEOUT_US 30000000u
typedef unsigned char(__cdecl *fn_quit)(void);
static u32 leave_state, leave_since, leave_upload, leave_told, quit_hooked;
static u32 leaves_saved, leaves_forced, leaves_stayed;

static unsigned char quit(void)
{
    log_text("net.leave", leave_state == LEAVE_ASK ? "without the server's copy" : "saved");
    return ((fn_quit)TES3X_NET_QUIT)();
}

unsigned char __cdecl tes3x_net_quit(void)
{
    if (ses.state != SESSION_JOINED || leave_state)
        return ((fn_quit)TES3X_NET_QUIT)();
    if (player_dead) {
        run_script("MessageBox \"You cannot leave while dead.\"");
        return 1;
    }
    leave_state = LEAVE_SAVE;
    leave_since = now_us();
    leave_told = 0;
    return 1;
}

static void quit_hook_install(void)
{
    u32 cr0, flags, *site = (u32 *)TES3X_NET_QUIT_SITE;

    if (quit_hooked)
        return;
    quit_hooked = 1;
    if (*site != TES3X_NET_QUIT) {
        tes3x_log_hex3("net.call_site_unexpected", (u32)site, *site, TES3X_NET_QUIT);
        return;
    }
    flags = lock();
    __asm__ volatile("movl %%cr0, %0" : "=r"(cr0));
    __asm__ volatile("movl %0, %%cr0" : : "r"(cr0 & ~CR0_WP) : "memory");
    *site = (u32)tes3x_net_quit;
    __asm__ volatile("movl %0, %%cr0" : : "r"(cr0) : "memory");
    unlock(flags);
    quit_hooked = 2;
}

static void leave_ask(const char *why)
{
    log_text("net.leave_unconfirmed", why);
    leave_state = LEAVE_ASK;
    *(int *)TES3X_NET_BUTTON = -1;
    run_script("MessageBox \"The server has not confirmed your save. Leave anyway? What it last "
               "heard is kept.\" \"Leave\" \"Stay\"");
}

static void leave_frame(void)
{
    const u8 *world = *(const u8 **)TES3X_NET_WORLD, *global;
    int button;

    if (leave_state == LEAVE_SAVE) {
        if (!leave_told) {
            leave_told = 1;
            run_script("MessageBox \"Saving to the server...\"");
            return;
        }
        if (up.state >= UP_WANT && up.state <= UP_SENDING && now_us() - leave_since <
                                                                 LEAVE_TIMEOUT_US)
            return; /* an earlier save is still going up */
        if (ses.state != SESSION_JOINED || !plausible(world) ||
            !plausible(global = *(const u8 *const *)(world + CHARGEN_STATE)) ||
            *(const u32 *)(global + 0x34) != 0xBF800000u || !slot_name()) {
            leave_ask("no save to make");
            return;
        }
        leave_upload = saves_uploaded + 1;
        save_pending = 0;
        if (!tes3x_net_save(**(void ***)TES3X_NET_DATA_HANDLER, save_slot, save_slot) ||
            !save_pending) {
            leave_ask("the save failed");
            return;
        }
        leave_state = LEAVE_UPLOAD;
    } else if (leave_state == LEAVE_UPLOAD) {
        if (saves_uploaded >= leave_upload && up.state == UP_DONE) {
            leaves_saved++;
            leave_state = 0;
            quit();
        } else if (saves_uploaded >= leave_upload && up.state == UP_FAILED) {
            leave_ask("the upload failed");
        } else if (now_us() - leave_since >= LEAVE_TIMEOUT_US) {
            leave_ask("no answer");
        }
    } else if (leave_state == LEAVE_ASK && (button = *(int *)TES3X_NET_BUTTON) >= 0) {
        *(int *)TES3X_NET_BUTTON = -1;
        if (button == 0) {
            leaves_forced++;
            quit();
        } else {
            leaves_stayed++;
        }
        leave_state = 0;
    }
}

/* Game thread: an upload already under way holds the save's until it ends. */
static void chargen_frame(void);

static void save_frame(void)
{
    if (ses.state == SESSION_JOINED) {
        game_frame();
        load_frame();
        chargen_frame();
    }
    if (save_requested && ses.state == SESSION_JOINED)
        save_request_frame();
    leave_frame();
    if (save_pending && ses.state == SESSION_JOINED &&
        !(up.state >= UP_WANT && up.state <= UP_SENDING) && up_start(save_name, save_path)) {
        save_pending = 0;
        saves_uploaded++;
    }
}

/* Every save goes through here: the call sites still aimed at SaveGame, and rotating-autosaves'
 * hook, which owns the other three. */
unsigned char __attribute__((thiscall)) tes3x_net_save(void *game, const char *file,
                                                      const char *display)
{
    u8 busy = BUSY_SAVING;
    int sent, slotted;
    unsigned char ok;

    if (player_dead && ses.state == SESSION_JOINED)
        return 0; /* a corpse is no checkpoint */
    sent = net.up && event_queue(EVENT_BUSY, &busy, 1);
    slotted = sent && slot_name();
    saves_seen++;
    saves_busy += sent;
    saves_slotted += slotted;
    if (slotted) {
        log_text("net.save_slot_name", save_slot);
        file = display = save_slot;
    }
    ok = ((fn_save_game)TES3X_NET_SAVE_GAME)(game, file, display);
    if (sent) {
        busy = 0;
        event_queue(EVENT_BUSY, &busy, 1);
    }
    if (slotted && ok)
        slot_upload();
    return ok;
}

static void save_hook_install(void)
{
    u32 sites[sizeof(save_sites) / sizeof(save_sites[0])], i, n = 0;
    const u8 *site;

    if (save_hooked)
        return;
    for (i = 0; i < sizeof(save_sites) / sizeof(save_sites[0]); i++) {
        site = (const u8 *)save_sites[i];
        if (site[0] == 0xE8 && (u32)site + 5 + *(const u32 *)(site + 1) == TES3X_NET_SAVE_GAME)
            sites[n++] = save_sites[i];
    }
    if (n && !redirect_calls(sites, n, TES3X_NET_SAVE_GAME, (const void *)tes3x_net_save))
        n = 0;
    save_hooked = 1 + n;
    tes3x_log("net.save_hook", n);
}

static void busy_event(const struct event *e)
{
    u32 i, flags;

    if (e->length < 1)
        return;
    flags = lock();
    for (i = 0; i < PEERS; i++)
        if (peers[i].client == e->origin) {
            peers[i].busy = e->data[0];
            peers[i].time = now_us();
        }
    unlock(flags);
    tes3x_log_hex3("net.peer_busy", e->origin, e->data[0], 0);
}

static void save_stat(void)
{
    tes3x_log_hex3("net.saves", saves_seen, saves_busy, save_hooked);
    tes3x_log_hex3("net.saves_slot", saves_slotted, saves_uploaded, save_pending);
    tes3x_log_hex3("net.saves_asked", saves_requested, save_requested, 0);
    tes3x_log_hex3("net.game", game_token, load_wanted, loads_started);
    tes3x_log_hex3("net.leaves", leaves_saved, leaves_forced, leaves_stayed);
    tes3x_log_hex3("net.leave_state", leave_state, quit_hooked, 0);
}

/* Characters. After GAME the server lists this key's characters (CHARS) or has the console make
 * one (NEWCHAR, the start points); the player picks from a message box and the console answers
 * PICK. A character is made in a New Game, relaunching into one if needed. Once the vanilla
 * CharGen script has put the player on the prison ship, the player waits in CHARGEN_CELL through
 * the name, race, class, birthsign and review menus and picks a start point. What the boat and
 * the census office would have done follows, then the start's lines from the server (RUN) and
 * the first save into the multiplayer slot, which the server keeps as the new character. */
#define EVENT_CHARS 26u   /* part, parts, then names */
#define EVENT_PICK 27u    /* PICK_CHARACTER or PICK_START, then an index or PICK_NEW */
#define EVENT_NEWCHAR 28u /* part, parts, then start point names */
#define EVENT_RUN 29u     /* a line of the chosen start; "" ends them */
#define PICK_CHARACTER 1u
#define PICK_START 2u
#define PICK_NEW 255u
#define CHARGEN_CELL "TES3X Arrival" /* in TES3X Multiplayer.esp */
#define CHARGEN_DONE 0xBF800000u     /* -1.0f; 0 until CharGen has run */
#define NAMES_BYTES 768u
#define NAMES_MAX 24u
#define NAME_LONGEST 36u
#define CHOOSER_PAGE 6u
#define CHOOSER_MORE 0xFEu
#define MENU_QUIET_FRAMES 30u   /* a menu step is over once no menu has been open this long */
#define MENU_MISSING_FRAMES 600u
#define RUN_BYTES 2048u

struct names {
    char text[NAMES_BYTES];
    u16 at[NAMES_MAX];
    u32 count, used, next, complete;
};
static struct names chars_names, start_names;

enum { CG_IDLE, CG_NEW_GAME, CG_HOLD, CG_ARRIVE, CG_MENUS, CG_STARTS, CG_FINISH, CG_RUN };
static u32 cg_state, cg_step, cg_frames, cg_seen, cg_made, cg_bad;
static u32 chooser_open, chooser_page, chooser_kind;
static u8 chooser_map[CHOOSER_PAGE + 2];
static char run_text[RUN_BYTES];
static u32 run_used, run_at, run_ended;

static const char *const chargen_menus[] = {
    "EnableNameMenu", "EnableRaceMenu", "EnableClassMenu", "EnableBirthMenu",
    "EnableStatReviewMenu"};

/* What CharGenClassNPC, CharGenDoorExitCaptain and the other boat and census office scripts
 * would have done by the time the player leaves the census office. */
static const char *const chargen_finish[] = {
    "\"CharGen StatsSheet\"->Disable", "\"CharGen Boat\"->Disable",
    "\"CharGen Boat Guard 1\"->Disable", "\"CharGen Boat Guard 2\"->Disable",
    "\"CharGen Dock Guard\"->Disable", "\"CharGen_cabindoor\"->Disable",
    "\"CharGen_chest_02_empty\"->Disable", "\"CharGen_crate_01\"->Disable",
    "\"CharGen_crate_01_empty\"->Disable", "\"CharGen_crate_01_misc01\"->Disable",
    "\"CharGen_crate_02\"->Disable", "\"CharGen_lantern_03_sway\"->Disable",
    "\"CharGen_ship_trapdoor\"->Disable", "\"CharGen_barrel_01\"->Disable",
    "\"CharGen_barrel_02\"->Disable", "\"CharGenbarrel_01_drinks\"->Disable",
    "\"CharGen_plank\"->Disable", "\"CharGen Door Hall\"->Unlock", "StartScript RaceCheck",
    "EnablePlayerControls", "EnablePlayerJumping", "EnablePlayerViewSwitch", "EnableVanityMode",
    "EnablePlayerFighting", "EnablePlayerMagic", "EnableStatsMenu", "EnableInventoryMenu",
    "EnableMagicMenu", "EnableMapMenu", "EnableRest", "AddTopic \"background\"",
    "AddTopic \"specific place\"", "AddTopic \"someone in particular\"",
    "AddTopic \"services\"", "AddTopic \"my trade\"", "AddTopic \"little secret\"",
    "AddTopic \"latest rumors\"", "AddTopic \"little advice\"", "set CharGenState to -1"};

static void names_event(struct names *list, const struct event *e)
{
    u32 i = 2, start;

    if (e->length < 2)
        return;
    if (e->data[0] == 0)
        list->count = list->used = list->next = list->complete = 0;
    if (e->data[0] != list->next || list->complete)
        return;
    while (i < e->length && list->count < NAMES_MAX) {
        start = i;
        while (i < e->length && e->data[i])
            i++;
        if (i == e->length || i - start > NAME_LONGEST || list->used + i - start + 1 > NAMES_BYTES)
            break;
        list->at[list->count++] = (u16)list->used;
        copy((u8 *)list->text + list->used, e->data + start, i - start + 1);
        list->used += i - start + 1;
        i++;
    }
    list->next++;
    list->complete = list->next >= e->data[1];
}

static u32 chargen_global(void)
{
    const u8 *world = *(const u8 **)TES3X_NET_WORLD, *global;

    if (!plausible(world) || !plausible(global = *(const u8 *const *)(world + CHARGEN_STATE)))
        return 0;
    return *(const u32 *)(global + 0x34);
}

static int world_idle(void)
{
    const u8 *world = *(const u8 **)TES3X_NET_WORLD;

    return player_reference() && plausible(world) && !world[WORLD_MENU_MODE];
}

static char *put_quoted(char *out, const char *text)
{
    *out++ = ' ';
    *out++ = '"';
    out = put_text(out, text);
    *out++ = '"';
    return out;
}

/* A message box of one page of names, "More" while more follow and extra (a name or 0) last. */
/* MessageMenu(text, button, ..., 0): the box the MessageBox command opens, without the command's
 * text macros, which read the player and so fail at the main menu. */
typedef void(__cdecl *fn_message_menu)(const char *text, ...);

static void chooser_show(const struct names *list, const char *title, const char *extra)
{
    char line[16 + (NAME_LONGEST + 3) * (CHOOSER_PAGE + 3)], *out;
    const char *buttons[CHOOSER_PAGE + 3] = {0};
    u32 i, n = 0, first = chooser_page * CHOOSER_PAGE;

    out = put_quoted(put_text(line, "MessageBox"), title);
    for (i = first; i < list->count && i < first + CHOOSER_PAGE; i++) {
        out = put_quoted(out, buttons[n] = list->text + list->at[i]);
        chooser_map[n++] = (u8)i;
    }
    if (list->count > first + CHOOSER_PAGE || chooser_page) {
        out = put_quoted(out, buttons[n] = list->count > first + CHOOSER_PAGE ? "More" : "Back");
        chooser_map[n++] = CHOOSER_MORE;
    }
    if (extra) {
        out = put_quoted(out, buttons[n] = extra);
        chooser_map[n++] = PICK_NEW;
    }
    *out = 0;
    *(int *)TES3X_NET_BUTTON = -1;
    if (player_reference())
        run_script(line);
    else
        ((fn_message_menu)TES3X_NET_MESSAGE_MENU)(title, buttons[0], buttons[1], buttons[2],
                                                  buttons[3], buttons[4], buttons[5], buttons[6],
                                                  buttons[7], (const char *)0);
    chooser_open = 1;
}

/* The chosen index, PICK_NEW for extra, or -1 while the box is up or turns a page. */
static int chooser_poll(const struct names *list, const char *title, const char *extra)
{
    int button = *(int *)TES3X_NET_BUTTON;

    if (!chooser_open) {
        if (world_idle() || (lobby && !player_reference()))
            chooser_show(list, title, extra);
        return -1;
    }
    if (button < 0 || button >= CHOOSER_PAGE + 2)
        return -1;
    *(int *)TES3X_NET_BUTTON = -1;
    chooser_open = 0;
    if (chooser_map[button] != CHOOSER_MORE)
        return chooser_map[button];
    chooser_page = (chooser_page + 1) * CHOOSER_PAGE < list->count ? chooser_page + 1 : 0;
    return -1;
}

static void pick(u32 what, u32 index)
{
    u8 body[2] = {(u8)what, (u8)index};

    if (!event_queue(EVENT_PICK, body, 2))
        cg_bad++;
    tes3x_log_hex3("net.chargen_pick", what, index, 0);
}

static void chars_event(const struct event *e)
{
    names_event(&chars_names, e);
    if (chars_names.complete)
        chooser_open = chooser_page = 0, chooser_kind = EVENT_CHARS;
}

static void newchar_event(const struct event *e)
{
    names_event(&start_names, e);
    if (!start_names.complete)
        return;
    log_text("net.chargen_starts", start_names.count ? start_names.text : "(none)");
    if (chooser_kind == EVENT_CHARS)
        chooser_kind = 0;
    if (cg_state != CG_IDLE && cg_state != CG_NEW_GAME)
        return;
    cg_state = game_launch == GAME_NEW && chargen_global() != CHARGEN_DONE ? CG_HOLD : CG_NEW_GAME;
    cg_step = cg_frames = 0;
    run_used = run_at = run_ended = 0;
    tes3x_log_hex3("net.chargen", cg_state, game_launch, start_names.count);
}

static void run_event(const struct event *e)
{
    u32 n = 0;

    while (n < e->length && e->data[n])
        n++;
    if (!n) {
        run_ended = 1;
        return;
    }
    if (run_used + n + 1 > RUN_BYTES) {
        cg_bad++;
        return;
    }
    copy((u8 *)run_text + run_used, e->data, n);
    run_text[run_used + n] = 0;
    run_used += n + 1;
}

static void chargen_next(u32 state)
{
    cg_state = state;
    cg_step = cg_frames = cg_seen = 0;
    tes3x_log_hex3("net.chargen", cg_state, game_launch, start_names.count);
}

static void chargen_frame(void)
{
    const u8 *world = *(const u8 **)TES3X_NET_WORLD;
    int chosen;

    if (chooser_kind == EVENT_CHARS && cg_state == CG_IDLE) {
        chosen = chooser_poll(&chars_names, "Choose your character", "New character");
        if (chosen >= 0) {
            chooser_kind = 0;
            pick(PICK_CHARACTER, (u32)chosen);
        }
        return;
    }
    switch (cg_state) {
    case CG_NEW_GAME:
        if (relaunch(0))
            chargen_next(CG_IDLE);
        break;
    case CG_HOLD:
        /* Once CharGen has put the player on the ship and disabled the controls and menus. */
        if (!world_idle() || !chargen_global() || chargen_global() == CHARGEN_DONE)
            break;
        run_script("Player->PositionCell 0 0 64 0 \"" CHARGEN_CELL "\"");
        chargen_next(CG_ARRIVE);
        break;
    case CG_ARRIVE:
        if (++cg_frames >= MENU_QUIET_FRAMES)
            chargen_next(CG_MENUS);
        break;
    case CG_MENUS:
        if (!plausible(world))
            break;
        if (cg_frames++ == 0)
            run_script(chargen_menus[cg_step]);
        if (world[WORLD_MENU_MODE])
            cg_seen = 1, cg_frames = 1;
        else if ((cg_seen && cg_frames > MENU_QUIET_FRAMES) || cg_frames > MENU_MISSING_FRAMES) {
            if (!cg_seen)
                tes3x_log("net.chargen_no_menu", cg_step);
            cg_seen = cg_frames = 0;
            if (++cg_step == sizeof(chargen_menus) / sizeof(*chargen_menus))
                chargen_next(CG_STARTS);
        }
        break;
    case CG_STARTS:
        if (!start_names.complete || !start_names.count)
            break;
        chosen = start_names.count == 1 ? 0 : chooser_poll(&start_names, "Where does your story begin?", 0);
        if (chosen >= 0) {
            pick(PICK_START, (u32)chosen);
            chargen_next(CG_FINISH);
        }
        break;
    case CG_FINISH:
        if (!world_idle())
            break;
        run_script(chargen_finish[cg_step]);
        if (++cg_step == sizeof(chargen_finish) / sizeof(*chargen_finish))
            chargen_next(CG_RUN);
        break;
    case CG_RUN:
        if (!world_idle())
            break;
        if (run_at < run_used) {
            log_text("net.chargen_run", run_text + run_at);
            run_script(run_text + run_at);
            run_at += tes3x_strlen(run_text + run_at) + 1;
        } else if (run_ended && ++cg_frames >= MENU_QUIET_FRAMES) {
            cg_made++;
            save_requested = 1;
            chargen_next(CG_IDLE);
        }
        break;
    }
}

static void chargen_stat(void)
{
    tes3x_log_hex3("net.chargen", cg_state, game_launch, start_names.count);
    tes3x_log_hex3("net.chargen_made", cg_made, cg_bad, chars_names.count);
}

/* The player's own state, streamed so that a crash loses only what the server has not seen since
 * the checkpoint. Once a second the console compares its inventory (per item object), level,
 * attributes, skills and journal with what it last sent and sends the changes as PLAYER events;
 * the server keeps the latest of each per character. Current health, magicka and fatigue go out
 * only on a change of a point, or for fatigue of FATIGUE_STEP, so regeneration stays quiet. When a launch runs the character's
 * checkpoint the server sends them back and then READY. Nothing goes out before READY, so a
 * checkpoint's older values never overwrite the server's. */
#define EVENT_PLAYER 25u
#define PLAYER_ITEMS 1u   /* part, parts, item id, then entries as in CONTENTS */
#define PLAYER_LEVEL 2u   /* LEVEL_BYTES */
#define PLAYER_SKILLS 3u  /* count, then (skill u8, base f32, progress f32) */
#define PLAYER_JOURNAL 4u /* count, then (index u16, quest id) */
#define PLAYER_READY 5u   /* from the server: 1 once it replayed what it keeps, 0 to send it all */
#define PLAYER_VITALS 6u  /* current health, magicka, fatigue as f32 */
#define PLAYER_PLACE 7u   /* from the server: where the player last was, as STATE's first bytes */
#define PLACE_BYTES (20 + CELL_NAME)
#define FATIGUE_STEP 8    /* fatigue regenerates: send a change of an eighth of its base */
#define PLAYER_POLL_US 1000000u
#define CARRIED 256u
#define ITEM_PARTS 12u
#define SKILLS 27u
#define SKILL_BYTES 9u
#define SKILLS_PER_EVENT ((EVENT_DATA - 2) / SKILL_BYTES)
#define JOURNALS 4096u
#define JOURNAL_ENTRIES 16u
/* level u16, level progress u16, level-ups per attribute 8 u8 and per specialisation 3 u8, base
 * health, magicka, fatigue and the 8 attributes as f32 */
#define LEVEL_BYTES 59u
#define ATTRIBUTES 8u
#define STAT_BASE 4 /* Statistic: vtable, base, current */
#define MOBILE_ATTRIBUTES 0x254
#define MOBILE_HEALTH_STAT 0x2B4 /* the Statistic; MOBILE_HEALTH is its current value */
#define MOBILE_MAGICKA_STAT 0x2C0
#define MOBILE_FATIGUE_STAT 0x2D8
#define MOBILE_SKILLS 0x3B0 /* 0x10 each */
#define PLAYER_LEVELUPS 0x56C /* int per attribute, then per specialisation */
#define PLAYER_LEVEL_PROGRESS 0x5E8
#define PLAYER_SKILL_PROGRESS 0x5F4
#define NPC_LEVEL 0x7C
#define RECORDS_DIALOGUES 0x48 /* list: head +0x8; node: next +0x4, dialogue +0x8 */
#define DIALOGUE_NAME 0x10
#define DIALOGUE_TYPE 0x14
#define DIALOGUE_JOURNAL 4
#define DIALOGUE_INDEX 0x1C

static const char *const attribute_names[ATTRIBUTES] = {
    "Strength", "Intelligence", "Willpower", "Agility", "Speed", "Endurance", "Personality",
    "Luck"};
static const char *const skill_names[SKILLS] = {
    "Block",      "Armorer",     "MediumArmor", "HeavyArmor",  "BluntWeapon", "LongBlade",
    "Axe",        "Spear",       "Athletics",   "Enchant",     "Destruction", "Alteration",
    "Illusion",   "Conjuration", "Mysticism",   "Restoration", "Alchemy",     "Unarmored",
    "Security",   "Sneak",       "Acrobatics",  "LightArmor",  "ShortBlade",  "Marksman",
    "Mercantile", "Speechcraft", "HandToHand"};

static struct {
    const u8 *item;
    u32 hash;
} carried[CARRIED];
static u8 carried_seen[CARRIED], level_sent[LEVEL_BYTES];
static u32 skills_sent[SKILLS][2], skills_known, level_known, vitals_known;
static float vitals_sent[3];
static u16 journal_sent[JOURNALS];
static u32 player_mode, player_welcome, player_polled;
static u32 player_items_out, player_stats_out, player_journal_out, player_too_many;
static u32 player_items_in, player_stats_in, player_journal_in, player_apply_failures;
static struct entry player_in[BOX_ENTRIES];
static u32 player_in_count, player_in_part;
static char player_in_id[SPAWN_ID];

static u8 *player_mobile(void)
{
    const u8 *world = *(const u8 **)TES3X_NET_WORLD, *mobs;
    u8 *const *list;

    if (!plausible(world) || !plausible(mobs = *(const u8 **)(world + 0x5C)))
        return 0;
    list = *(u8 *const *const *)(mobs + 0x24);
    return plausible(list) && plausible(*list) ? *list : 0;
}

/* The player's base NPC, which holds the level. */
static u8 *player_npc(const u8 *ref)
{
    u8 *instance = *(u8 *const *)(ref + REF_BASE), *npc;

    return plausible(instance) && plausible(npc = *(u8 *const *)(instance + 0x6C)) ? npc : 0;
}

static u32 events_room(void)
{
    u32 flags = lock(), room = EVENTS_OUT - (rel.out_next - rel.out_first);

    unlock(flags);
    return room;
}

/* A stack's count and item data, as contents_read reads them. */
static u32 stack_hash(const u8 *stack)
{
    const u8 *vars = *(const u8 *const *)(stack + STACK_VARIABLES), *const *data;
    u32 hash = (2166136261u ^ *(const u32 *)stack) * 16777619u, filled, i;

    filled = plausible(vars) ? *(const u32 *)(vars + 0xC) : 0;
    data = filled ? *(const u8 *const *const *)(vars + 4) : 0;
    for (i = 0; plausible(data) && i < filled; i++)
        if (plausible(data[i]))
            hash = ((hash ^ *(const u32 *)(data[i] + ITEM_CONDITION)) * 16777619u ^
                    *(const u32 *)(data[i] + ITEM_CHARGE)) * 16777619u;
    return hash | 1;
}

/* Every stack of one item the player carries, as PLAYER_ITEMS parts; none when it is gone. 0 when
 * the queue has no room. */
static int carried_send(const u8 *object, const char *id)
{
    static struct entry e[BOX_ENTRIES];
    static u8 parts[ITEM_PARTS][EVENT_DATA];
    u32 lengths[ITEM_PARTS], n = contents_read(object, e, BOX_ENTRIES, id), count = 0, head, i, k;
    u32 size;

    for (k = 0; id[k]; k++)
        ;
    head = 3 + k + 1;
    lengths[0] = head;
    for (i = 0; i < n; i++) {
        size = 5 + (e[i].flags & ENTRY_DATA ? 8 : 0);
        if (lengths[count] + size > EVENT_DATA) {
            if (++count == ITEM_PARTS) {
                player_too_many++;
                return 1;
            }
            lengths[count] = head;
        }
        put32le(parts[count] + lengths[count], (u32)e[i].count);
        parts[count][lengths[count] + 4] = (u8)e[i].flags;
        if (e[i].flags & ENTRY_DATA) {
            put32le(parts[count] + lengths[count] + 5, e[i].condition);
            put32le(parts[count] + lengths[count] + 9, e[i].charge);
        }
        lengths[count] += size;
    }
    count++;
    if (events_room() < count + 2)
        return 0;
    for (i = 0; i < count; i++) {
        parts[i][0] = PLAYER_ITEMS;
        parts[i][1] = (u8)i;
        parts[i][2] = (u8)count;
        copy(parts[i] + 3, (const u8 *)id, k + 1);
        event_queue(EVENT_PLAYER, parts[i], lengths[i]);
    }
    player_items_out++;
    return 1;
}

/* The inventory by item object: a stack that changed, or one that is gone, goes out. */
static void carried_scan(const u8 *object, int send)
{
    const u8 *node, *stack, *item;
    const char *id;
    u32 i, free, guard, hash;

    for (i = 0; i < CARRIED; i++)
        carried_seen[i] = 0;
    for (node = *(const u8 *const *)(object + OBJECT_INVENTORY + INVENTORY_FIRST), guard = 0;
         plausible(node) && guard < 512; node = *(const u8 *const *)(node + 4), guard++) {
        if (!plausible(stack = *(const u8 *const *)(node + 8)) ||
            !plausible(item = *(const u8 *const *)(stack + 4)) || !(id = object_id(item)))
            continue;
        hash = stack_hash(stack);
        for (i = 0, free = CARRIED; i < CARRIED && carried[i].item != item; i++)
            if (!carried[i].item && free == CARRIED)
                free = i;
        if (i == CARRIED && (i = free) == CARRIED) {
            player_too_many++;
            continue;
        }
        carried_seen[i] = 1;
        if (carried[i].item == item && carried[i].hash == hash)
            continue;
        if (send && !carried_send(object, id)) {
            carried_seen[i] = carried[i].item == item;
            continue;
        }
        carried[i].item = item;
        carried[i].hash = hash;
    }
    for (i = 0; i < CARRIED; i++)
        if (carried[i].item && !carried_seen[i] &&
            (!send || ((id = object_id(carried[i].item)) && carried_send(object, id))))
            carried[i].item = 0;
}

static void level_read(const u8 *mobile, const u8 *npc, u8 *out)
{
    static const u32 stats[3] = {MOBILE_HEALTH_STAT, MOBILE_MAGICKA_STAT, MOBILE_FATIGUE_STAT};
    u32 i;
    int v;

    out[0] = npc[NPC_LEVEL];
    out[1] = npc[NPC_LEVEL + 1];
    v = *(const int *)(mobile + PLAYER_LEVEL_PROGRESS);
    out[2] = (u8)v;
    out[3] = (u8)(v >> 8);
    for (i = 0; i < 11; i++) {
        v = ((const int *)(mobile + PLAYER_LEVELUPS))[i];
        out[4 + i] = (u8)(v < 0 ? 0 : v > 255 ? 255 : v);
    }
    for (i = 0; i < 3; i++)
        copy(out + 15 + 4 * i, mobile + stats[i] + STAT_BASE, 4);
    for (i = 0; i < ATTRIBUTES; i++)
        copy(out + 27 + 4 * i, mobile + MOBILE_ATTRIBUTES + 0xC * i + STAT_BASE, 4);
}

static void level_scan(const u8 *mobile, const u8 *npc, int send)
{
    u8 data[1 + LEVEL_BYTES];
    u32 i;

    level_read(mobile, npc, data + 1);
    for (i = 0; level_known && i < LEVEL_BYTES && data[1 + i] == level_sent[i]; i++)
        ;
    if (level_known && i == LEVEL_BYTES)
        return;
    data[0] = PLAYER_LEVEL;
    if (send && !event_queue(EVENT_PLAYER, data, sizeof(data)))
        return;
    copy(level_sent, data + 1, LEVEL_BYTES);
    level_known = 1;
    player_stats_out += send;
}

/* Skills whose base or progress changed, SKILLS_PER_EVENT to an event. */
static void skills_scan(const u8 *mobile, int send)
{
    u8 data[EVENT_DATA];
    u32 i, n = 0, k, base, progress, pending[SKILLS_PER_EVENT];

    for (i = 0; i <= SKILLS; i++) {
        if (i < SKILLS) {
            base = *(const u32 *)(mobile + MOBILE_SKILLS + 0x10 * i + STAT_BASE);
            progress = ((const u32 *)(mobile + PLAYER_SKILL_PROGRESS))[i];
            if ((skills_known >> i & 1) && skills_sent[i][0] == base &&
                skills_sent[i][1] == progress)
                continue;
            data[2 + n * SKILL_BYTES] = (u8)i;
            put32le(data + 3 + n * SKILL_BYTES, base);
            put32le(data + 7 + n * SKILL_BYTES, progress);
            pending[n++] = i;
        }
        if (!n || (n < SKILLS_PER_EVENT && i < SKILLS))
            continue;
        data[0] = PLAYER_SKILLS;
        data[1] = (u8)n;
        if (send && !event_queue(EVENT_PLAYER, data, 2 + n * SKILL_BYTES))
            return;
        for (k = 0; k < n; k++) {
            skills_sent[pending[k]][0] = get32le(data + 3 + k * SKILL_BYTES);
            skills_sent[pending[k]][1] = get32le(data + 7 + k * SKILL_BYTES);
            skills_known |= 1u << pending[k];
        }
        player_stats_out += send;
        n = 0;
    }
}

static int vitals_moved(const float *now, float fatigue_base)
{
    float step, d;
    u32 i;

    for (i = 0; i < 3; i++) {
        step = i == 2 && fatigue_base > FATIGUE_STEP ? fatigue_base / FATIGUE_STEP : 1.0f;
        d = now[i] - vitals_sent[i];
        if (d >= step || -d >= step)
            return 1;
    }
    return 0;
}

static void vitals_scan(const u8 *mobile, int send)
{
    u8 data[1 + 12];
    float now[3];

    now[0] = *(const float *)(mobile + MOBILE_HEALTH);
    now[1] = *(const float *)(mobile + MOBILE_MAGICKA);
    now[2] = *(const float *)(mobile + MOBILE_FATIGUE);
    if (vitals_known &&
        !vitals_moved(now, *(const float *)(mobile + MOBILE_FATIGUE_STAT + STAT_BASE)))
        return;
    data[0] = PLAYER_VITALS;
    copy(data + 1, (const u8 *)now, 12);
    if (send && !event_queue(EVENT_PLAYER, data, sizeof(data)))
        return;
    copy((u8 *)vitals_sent, (const u8 *)now, 12);
    vitals_known = 1;
    player_stats_out += send;
}

/* The first node of the dialogue list. */
static const u8 *dialogues_head(void)
{
    const u8 *handler = *(const u8 **)TES3X_NET_DATA_HANDLER, *records, *list;

    if (!plausible(handler) || !plausible(records = *(const u8 *const *)handler) ||
        !plausible(list = *(const u8 *const *)(records + RECORDS_DIALOGUES)))
        return 0;
    return *(const u8 *const *)(list + 8);
}

static const char *dialogue_name(const u8 *dialogue)
{
    const char *name = *(const char *const *)(dialogue + DIALOGUE_NAME);

    return mapped(name) ? name : 0;
}

/* Journal indices, kept by the quest's place among the journals and sent as (index, id). */
static void journal_scan(int send)
{
    u8 data[EVENT_DATA];
    const u8 *node = dialogues_head(), *dialogue;
    const char *name;
    u32 k, n = 0, length = 2, size, guard, i, pending[JOURNAL_ENTRIES], values[JOURNAL_ENTRIES];
    int index;

    for (guard = k = 0; k <= JOURNALS && guard < 65536; guard++) {
        dialogue = plausible(node) ? *(const u8 *const *)(node + 8) : 0;
        if (dialogue && (!plausible(dialogue) || dialogue[DIALOGUE_TYPE] != DIALOGUE_JOURNAL)) {
            node = *(const u8 *const *)(node + 4);
            continue;
        }
        size = 0;
        index = 0;
        if (dialogue && k < JOURNALS) {
            index = *(const int *)(dialogue + DIALOGUE_INDEX);
            index = index < 0 ? 0 : index > 0xFFFF ? 0xFFFF : index;
            name = dialogue_name(dialogue);
            for (size = 0; name && name[size] && size < SPAWN_ID - 1; size++)
                ;
            if (journal_sent[k] == (u16)index || !name || name[size] ||
                !script_safe((const u8 *)name, SPAWN_ID)) {
                k++;
                node = *(const u8 *const *)(node + 4);
                continue;
            }
        }
        /* the event is full, or this was the end of the list */
        if (n && (!dialogue || k == JOURNALS || length + 3 + size > EVENT_DATA ||
                  n == JOURNAL_ENTRIES)) {
            data[0] = PLAYER_JOURNAL;
            data[1] = (u8)n;
            if (send && !event_queue(EVENT_PLAYER, data, length))
                return;
            for (i = 0; i < n; i++)
                journal_sent[pending[i]] = (u16)values[i];
            player_journal_out += send;
            n = 0;
            length = 2;
        }
        if (!dialogue || k == JOURNALS)
            return;
        data[length] = (u8)index;
        data[length + 1] = (u8)(index >> 8);
        copy(data + length + 2, (const u8 *)name, size + 1);
        length += 3 + size;
        pending[n] = k++;
        values[n++] = (u32)index;
        node = *(const u8 *const *)(node + 4);
    }
}

/* Game thread, in the world: after READY, the changes once a second. */
static void player_frame(const u8 *ref)
{
    u8 *mobile = player_mobile(), *npc = player_npc(ref), *object = *(u8 *const *)(ref + REF_BASE);
    u32 now = now_us(), i, send = 1;

    if (ses.state != SESSION_JOINED || player_welcome != ses.welcomes || !player_mode ||
        !plausible(mobile) || !npc || !plausible(object))
        return;
    if (player_mode != 3) {
        /* After a replay what the console has is what the server keeps; otherwise send it all. */
        for (i = 0; i < CARRIED; i++)
            carried[i].item = 0;
        for (i = 0; i < JOURNALS; i++)
            journal_sent[i] = 0;
        level_known = skills_known = vitals_known = 0;
        send = player_mode == 1;
        tes3x_log_hex3("net.player_ready", player_mode, ses.welcomes, 0);
        player_mode = 3;
    } else if (now - player_polled < PLAYER_POLL_US) {
        return;
    }
    player_polled = now;
    carried_scan(object, send);
    level_scan(mobile, npc, send);
    skills_scan(mobile, send);
    vitals_scan(mobile, send);
    journal_scan(send);
}

static void player_set(u8 *ref, const char *what, const char *name, int value)
{
    char line[48], *p = put_text(put_text(line, what), name);

    p = put_int(put_text(p, " "), value);
    *p = 0;
    run_script_on(line, ref);
}

/* Attributes and the level through their script commands, which update what follows from them. */
static void level_apply(u8 *ref, const u8 *body)
{
    static const u32 stats[3] = {MOBILE_HEALTH_STAT, MOBILE_MAGICKA_STAT, MOBILE_FATIGUE_STAT};
    u8 *mobile = player_mobile(), *npc = player_npc(ref);
    float value, *stat;
    u32 i;
    int level = body[0] | body[1] << 8;

    if (!plausible(mobile) || !npc || !float_within(body + 15, 3 + ATTRIBUTES, STAT_LIMIT)) {
        player_apply_failures++;
        return;
    }
    if (*(const short *)(npc + NPC_LEVEL) != level)
        player_set(ref, "SetLevel", "", level);
    for (i = 0; i < ATTRIBUTES; i++) {
        copy((u8 *)&value, body + 27 + 4 * i, 4);
        if (*(const float *)(mobile + MOBILE_ATTRIBUTES + 0xC * i + STAT_BASE) != value)
            player_set(ref, "Set", attribute_names[i], round_int(value));
    }
    for (i = 0; i < 3; i++) {
        stat = (float *)(mobile + stats[i]);
        copy((u8 *)&stat[1], body + 15 + 4 * i, 4);
        if (stat[2] > stat[1])
            stat[2] = stat[1];
    }
    *(int *)(mobile + PLAYER_LEVEL_PROGRESS) = body[2] | body[3] << 8;
    for (i = 0; i < 11; i++)
        ((int *)(mobile + PLAYER_LEVELUPS))[i] = body[4 + i];
    player_stats_in++;
}

static void skills_apply(u8 *ref, const u8 *p, u32 length)
{
    u8 *mobile = player_mobile();
    u32 i, skill;
    float base;

    if (!plausible(mobile)) {
        player_apply_failures++;
        return;
    }
    for (i = 0; i < p[1] && 2 + (i + 1) * SKILL_BYTES <= length; i++) {
        skill = p[2 + i * SKILL_BYTES];
        if (skill >= SKILLS || !float_within(p + 3 + i * SKILL_BYTES, 2, STAT_LIMIT))
            continue;
        copy((u8 *)&base, p + 3 + i * SKILL_BYTES, 4);
        if (*(const float *)(mobile + MOBILE_SKILLS + 0x10 * skill + STAT_BASE) != base)
            player_set(ref, "Set", skill_names[skill], round_int(base));
        copy(mobile + PLAYER_SKILL_PROGRESS + 4 * skill, p + 7 + i * SKILL_BYTES, 4);
    }
    player_stats_in++;
}

/* Through ModCurrent*, which keeps the HUD in step. Death is not the stream's to cause: health
 * stays at 1 or more. */
static void vitals_apply(u8 *ref, const u8 *body)
{
    static const char *const names[3] = {"Health", "Magicka", "Fatigue"};
    static const u32 current[3] = {MOBILE_HEALTH, MOBILE_MAGICKA, MOBILE_FATIGUE};
    u8 *mobile = player_mobile();
    float want;
    u32 i;
    int delta;

    if (!plausible(mobile) || !float_within(body, 3, STAT_LIMIT)) {
        player_apply_failures++;
        return;
    }
    for (i = 0; i < 3; i++) {
        copy((u8 *)&want, body + 4 * i, 4);
        if (i == 0 && want < 1.0f)
            want = 1.0f;
        if ((delta = round_int(want - *(const float *)(mobile + current[i]))) != 0)
            player_set(ref, "ModCurrent", names[i], delta);
    }
    player_stats_in++;
}

/* The checkpoint was made somewhere else: go where the server last saw the player, so a power cut
 * is no way out of a place. */
static void place_apply(const u8 *body)
{
    char line[160], *q;
    u32 flags = get32le(body);
    float heading;

    if (!(flags & STATE_IN_WORLD) || !float_within(body + 4, 3, POSITION_LIMIT) ||
        !float_within(body + 16, 1, ANGLE_LIMIT) ||
        ((flags & STATE_INTERIOR) && !script_safe(body + 20, CELL_NAME))) {
        player_apply_failures++;
        return;
    }
    copy((u8 *)&heading, body + 16, 4);
    q = put_xyz(put_text(line, "Player->PositionCell "), (const float *)(body + 4));
    q = put_int(put_text(q, " "), round_int(heading * (180.0f / PI)));
    q = put_text(q, " \"");
    q = put_text(q, flags & STATE_INTERIOR ? interior_name(body + 20) : GHOST_EXTERIOR);
    q = put_text(q, "\"");
    *q = 0;
    run_script(line);
    log_text("net.player_place", flags & STATE_INTERIOR ? interior_name(body + 20) : "(exterior)");
    player_stats_in++;
}

/* Death while joined. The engine's death ends in "load the most recent save?", which would load a
 * stale copy of the character: while joined, MobilePlayer::onDeath returns before it and the
 * server is told (PLAYER_DEATH). Its RESPAWN sets a delay, a place and the gold lost; then the
 * player is resurrected at the closest TempleMarker or DivineMarker, as the Intervention spells
 * find them, and the console sends PLAYER_ALIVE. Without an answer it respawns anyway. */
#define PLAYER_DEATH 8u   /* to the server: the player died */
#define PLAYER_RESPAWN 9u /* from the server: delay ms u32, RESPAWN_*, gold to lose u32 */
#define PLAYER_ALIVE 10u  /* to the server: resurrected */
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
    if (bounty > 0) { /* Resurrect clears it */
        *put_int(put_text(line, "SetPCCrimeLevel "), bounty) = 0;
        run_script(line);
    }
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
    player_dead = 0;
    respawns_done++;
    event_queue(EVENT_PLAYER, &data, 1);
    tes3x_log_hex3("net.respawned", marker ? *(const u32 *)(marker + REF_ID) : 0, (u32)bounty,
                   respawn_gold);
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
}

/* A quest only moves forward: the checkpoint already holds the entries up to its own index. */
static void journal_apply(const u8 *p, u32 length)
{
    char line[64], *out;
    const u8 *node, *dialogue = 0;
    const char *id, *name;
    u32 off = 2, i, guard, max;
    int index;

    for (i = 0; i < p[1] && off + 3 <= length; i++) {
        index = p[off] | p[off + 1] << 8;
        id = (const char *)p + off + 2;
        max = length - off - 2 < SPAWN_ID ? length - off - 2 : SPAWN_ID;
        if (!script_safe(p + off + 2, max))
            return;
        for (off += 2; p[off]; off++)
            ;
        off++;
        for (node = dialogues_head(), guard = 0; plausible(node) && guard < 65536;
             node = *(const u8 *const *)(node + 4), guard++)
            if (plausible(dialogue = *(const u8 *const *)(node + 8)) &&
                dialogue[DIALOGUE_TYPE] == DIALOGUE_JOURNAL && (name = dialogue_name(dialogue)) &&
                same_name(name, id))
                break;
        if (!plausible(node)) {
            player_apply_failures++;
            log_text("net.player_quest_unknown", id);
            continue;
        }
        if (*(const int *)(dialogue + DIALOGUE_INDEX) >= index)
            continue;
        out = put_int(put_text(put_text(put_text(line, "Journal \""), id), "\" "), index);
        *out = 0;
        run_script(line);
        player_journal_in++;
    }
}

static void player_items_event(u8 *ref, const struct event *e)
{
    const u8 *p = e->data;
    u32 off, k, max = e->length - 3 < SPAWN_ID ? e->length - 3 : SPAWN_ID;

    if (e->length < 4 || !script_safe(p + 3, max))
        return;
    if (p[1] == 0) {
        player_in_count = player_in_part = 0;
        for (k = 0; p[3 + k]; k++)
            player_in_id[k] = (char)p[3 + k];
        player_in_id[k] = 0;
    } else if (p[1] != player_in_part || !same_id((const char *)p + 3, player_in_id)) {
        return;
    }
    player_in_part++;
    for (off = 3; p[off]; off++)
        ;
    off++;
    while (off + 5 <= e->length && player_in_count < BOX_ENTRIES) {
        struct entry *x = &player_in[player_in_count];
        x->count = (int)get32le(p + off);
        x->flags = p[off + 4] & ENTRY_DATA;
        x->condition = x->charge = 0;
        off += 5;
        if (x->flags & ENTRY_DATA) {
            if (off + 8 > e->length)
                break;
            x->condition = get32le(p + off);
            x->charge = get32le(p + off + 4);
            off += 8;
        }
        for (k = 0; player_in_id[k]; k++)
            x->id[k] = player_in_id[k];
        x->id[k] = 0;
        player_in_count++;
    }
    if (player_in_part != p[2])
        return;
    if (inventory_apply(ref, player_in, player_in_count, player_in_id, player_mobile()))
        player_items_in++;
    else
        player_apply_failures++;
    log_text("net.player_item", player_in_id);
}

static void player_event(const struct event *e)
{
    u8 *ref = (u8 *)player_reference();

    if (e->length < 1)
        return;
    if (e->data[0] == PLAYER_READY) {
        player_mode = e->length >= 2 && e->data[1] ? 2 : 1;
        player_welcome = ses.welcomes;
        return;
    }
    if (!ref) {
        player_apply_failures++;
        return;
    }
    if (e->data[0] == PLAYER_ITEMS)
        player_items_event(ref, e);
    else if (e->data[0] == PLAYER_LEVEL && e->length >= 1 + LEVEL_BYTES)
        level_apply(ref, e->data + 1);
    else if (e->data[0] == PLAYER_SKILLS && e->length >= 2)
        skills_apply(ref, e->data, e->length);
    else if (e->data[0] == PLAYER_JOURNAL && e->length >= 2)
        journal_apply(e->data, e->length);
    else if (e->data[0] == PLAYER_VITALS && e->length >= 1 + 12)
        vitals_apply(ref, e->data + 1);
    else if (e->data[0] == PLAYER_PLACE && e->length >= 1 + PLACE_BYTES)
        place_apply(e->data + 1);
    else if (e->data[0] == PLAYER_RESPAWN && e->length >= 1 + 9)
        respawn_event(e->data + 1);
}

static void player_stat(void)
{
    tes3x_log_hex3("net.player_out", player_items_out, player_stats_out, player_journal_out);
    tes3x_log_hex3("net.player_in", player_items_in, player_stats_in, player_journal_in);
    tes3x_log_hex3("net.player_bad", player_apply_failures, player_too_many, player_mode);
}

static void event_handle(const struct event *e)
{
    char text[EVENT_DATA + 1], line[EVENT_DATA + 16];

    if (e->kind == EVENT_TEXT) {
        copy((u8 *)text, e->data, e->length);
        text[e->length] = 0;
        tes3x_log_hex3("net.text_from", e->origin, e->seq, 0);
        log_text("net.text", text);
        /* The server's own notices (deaths) show on screen; players' lines only in the log. */
        if (!e->origin && player_reference() && script_safe((const u8 *)text, sizeof(text))) {
            *put_text(put_text(put_text(line, "MessageBox \""), text), "\"") = 0;
            run_script(line);
        }
    } else if ((e->kind >= EVENT_AUTHORITY && e->kind <= EVENT_DEATH) || e->kind == EVENT_OWNERS) {
        authority_event(e);
    } else if (e->kind == EVENT_EQUIPMENT) {
        equipment_event(e);
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
    } else if (e->kind == EVENT_SAVE) {
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

/* Weather. A region's weather is Region+0x74; when the hours between changes have passed the
 * engine rolls every region at once, and each frame the sky transitions to the weather of the
 * player's region when it differs. While joined only the lowest client id in the session rolls;
 * the others NOP the roll. Each change a client sees goes to the server as a WEATHER event
 * (flags, count, then region index and weather), and the server sends the result to every client,
 * the sender too, so all end in the server's order. After WELCOME a client offers its whole table,
 * of which the server keeps the regions it does not know yet, and the server replays its own.
 * Regions are numbered by their place in the region list, the same under one load order. */
#define WEATHER_OFFER 1u
#define WEATHER_ENTRY 3u
#define WEATHER_PER_EVENT ((EVENT_DATA - 2) / WEATHER_ENTRY)
#define WEATHERS 10u
#define REGIONS 256u
#define RECORDS_REGIONS 0x4C /* list: head +0x8; node: next +0x4, region +0x8 */
#define REGION_WEATHER 0x74
#define REGION_MODIFIED 0x14 /* vtable offset; randomizeWeather passes 1 */
#define WORLD_WEATHER 0x58
#define WEATHER_REGION 0x1D0 /* the player's region */
#define WEATHER_TRANSITION 0x170 /* how far the sky is from the current weather to the next */

typedef void(__attribute__((thiscall)) *fn_object_flag)(void *object, int on);

static u8 *const weather_roll = (u8 *)TES3X_NET_WEATHER_ROLL;
static u8 weather_call[5], weather_seen[REGIONS];
static u8 *regions[REGIONS];
static u32 weather_saved, weather_blocked, weather_welcome, weather_forced;
static u32 weather_sent, weather_received, weather_applied;

/* The region list in load order, into regions; 0 if it is not there or looks wrong. */
static u32 regions_list(void)
{
    const u8 *handler = *(const u8 **)TES3X_NET_DATA_HANDLER, *records, *list, *node;
    u32 n = 0;

    if (!plausible(handler) || !plausible(records = *(const u8 *const *)handler) ||
        !plausible(list = *(const u8 *const *)(records + RECORDS_REGIONS)))
        return 0;
    for (node = *(const u8 *const *)(list + 8); plausible(node) && n < REGIONS;
         node = *(const u8 *const *)(node + 4))
        if (!plausible(regions[n++] = *(u8 *const *)(node + 8)))
            return 0;
    return n;
}

static void weather_gate(u32 block)
{
    u32 cr0, flags, i;

    if (block == weather_blocked || weather_saved == 2)
        return;
    if (!weather_saved) {
        if (weather_roll[0] != 0xE8) {
            tes3x_log_hex3("net.weather_roll_unexpected", weather_roll[0], weather_roll[1], 0);
            weather_saved = 2;
            return;
        }
        copy(weather_call, weather_roll, 5);
        weather_saved = 1;
    }
    flags = lock();
    __asm__ volatile("movl %%cr0, %0" : "=r"(cr0));
    __asm__ volatile("movl %0, %%cr0" : : "r"(cr0 & ~CR0_WP) : "memory");
    for (i = 0; i < 5; i++)
        weather_roll[i] = block ? 0x90 : weather_call[i];
    __asm__ volatile("movl %0, %%cr0" : : "r"(cr0) : "memory");
    unlock(flags);
    weather_blocked = block;
    tes3x_log("net.weather_rolls", !block);
}

static int weather_roller(void)
{
    u32 i;

    if (weather_forced)
        return weather_forced - 1;
    for (i = 0; i < PEERS; i++)
        if (peers[i].client && peers[i].client < ses.client)
            return 0;
    return 1;
}

/* Game thread, each frame: the roll gate, then the offer after WELCOME or the regions changed
 * since the last frame, whole or not at all. */
static void weather_frame(int in_world)
{
    u8 entries[REGIONS * WEATHER_ENTRY], data[EVENT_DATA];
    u32 joined = ses.state == SESSION_JOINED && in_world, n, i, count = 0, offer, room, lk, part;

    weather_gate(weather_forced ? weather_forced == 1 : joined && !weather_roller());
    if (!joined || !(n = regions_list()))
        return;
    offer = weather_welcome != ses.welcomes;
    for (i = 0; i < n; i++) {
        u8 w = (u8) * (const int *)(regions[i] + REGION_WEATHER);
        if (!offer && w == weather_seen[i])
            continue;
        entries[count * WEATHER_ENTRY] = (u8)i;
        entries[count * WEATHER_ENTRY + 1] = (u8)(i >> 8);
        entries[count * WEATHER_ENTRY + 2] = w;
        count++;
    }
    lk = lock();
    room = EVENTS_OUT - (rel.out_next - rel.out_first);
    unlock(lk);
    if (room < (count + WEATHER_PER_EVENT - 1) / WEATHER_PER_EVENT)
        return;
    for (i = 0; i < count; i += part) {
        part = count - i < WEATHER_PER_EVENT ? count - i : WEATHER_PER_EVENT;
        data[0] = (u8)(offer ? WEATHER_OFFER : 0);
        data[1] = (u8)part;
        copy(data + 2, entries + i * WEATHER_ENTRY, part * WEATHER_ENTRY);
        event_queue(EVENT_WEATHER, data, 2 + part * WEATHER_ENTRY);
    }
    for (i = 0; i < count; i++)
        weather_seen[entries[i * WEATHER_ENTRY] | entries[i * WEATHER_ENTRY + 1] << 8] =
            entries[i * WEATHER_ENTRY + 2];
    weather_sent += count;
    if (offer || count)
        tes3x_log_hex3("net.weather_sent", offer, count, n);
    weather_welcome = ses.welcomes;
}

/* The server's weather: written as randomizeWeather writes a roll, so the sky transitions as it
 * does for one, and remembered as seen so it is not sent back. */
static void weather_event(const struct event *e)
{
    u32 n = regions_list(), i, index, w;
    int *at;

    if (e->length < 2 || (e->data[0] & WEATHER_OFFER))
        return;
    weather_received++;
    for (i = 0; i < e->data[1] && 2 + (i + 1) * WEATHER_ENTRY <= e->length; i++) {
        index = e->data[2 + i * WEATHER_ENTRY] | e->data[3 + i * WEATHER_ENTRY] << 8;
        w = e->data[4 + i * WEATHER_ENTRY];
        if (index >= n || w >= WEATHERS)
            continue;
        weather_seen[index] = (u8)w;
        at = (int *)(regions[index] + REGION_WEATHER);
        if (*at == (int)w)
            continue;
        *at = (int)w;
        ((fn_object_flag)(*(void *const *const *)regions[index])[REGION_MODIFIED / 4])(
            regions[index], 1);
        weather_applied++;
        tes3x_log_hex3("net.weather_set", index, w, e->origin);
    }
}

static void weather_stat(void)
{
    const u8 *world = *(const u8 **)TES3X_NET_WORLD, *controller, *here;
    u32 n = regions_list(), i;

    tes3x_log_hex3("net.weather", weather_sent, weather_received, weather_applied);
    tes3x_log_hex3("net.weather_roller", weather_roller(), weather_blocked, n);
    if (!plausible(world) || !plausible(controller = *(const u8 *const *)(world + WORLD_WEATHER)) ||
        !plausible(here = *(const u8 *const *)(controller + WEATHER_REGION)))
        return;
    for (i = 0; i < n && regions[i] != here; i++)
        ;
    tes3x_log_hex3("net.weather_here", i, *(const u32 *)(here + REGION_WEATHER),
                   (u32)(int)(*(const float *)(controller + WEATHER_TRANSITION) * 1000.0f));
}

/* The main menu's Join, above Exit. It turns the menu's column into the server list: one row per
 * server this console knows (NetServer, then servers.ini's sections, newest first), New server,
 * which types an address on the console patch's keyboard, and Return. Choosing a server brings the
 * NIC up if it is not, opens a lobby session and marks later relaunches with the server; the
 * character list or the start points follow at the main menu as they do in a game. */
#define UI_CLICK 0xFFFF8035u
#define UI_PRESS 0xFFFF8034u /* the main menu's buttons show their pressed image */
#define UI_OVER 0xFFFF8033u  /* ... their highlighted one */
#define UI_LEAVE 0xFFFF8032u /* ... their normal one */
#define UI_FLAG_B 0xFFFF800Bu
#define UI_FOCUS_A 0xFFFF8048u /* both UI_TRUE on each main menu button */
#define UI_FOCUS_B 0xFFFF80A8u
#define UI_CHILD_ALIGN_X 0xFFFF8054u
#define UI_CHILD_ALIGN_Y 0xFFFF8055u
#define UI_TRUE 0xFFFF80BDu
#define UI_HALF 0x3F000000 /* 0.5f */
#define UI_PROP_INT 1
#define UI_PROP_FLOAT 2
#define UI_PROP_PTR 8
#define UI_PROP_ENUM 0x10
#define UI_PROP_HANDLER 0x20
#define UI_PARENT 0x34
#define UI_CHILDREN 0x28 /* vector: begin, then end at +4 */
#define UI_WIDTH 0xF4
#define UI_HEIGHT 0xF8
#define UI_COLOUR 0x154    /* red, green, blue, alpha; MWSE's PC Element + 8 */
#define UI_COLOUR_CHANGED 0x7F /* flagColourChanged, likewise */
#define UI_FONT 0x164      /* 0 the small Century Gothic, 1 the big one */
#define UI_IMAGE_FLAG 0x87 /* cleared on each main menu image */
#define MENU_ROW 0x32      /* a main menu button's height */
#define MENU_ROWS 5        /* New, Load, Options, Join, Exit */
#define SERVERS_SHOWN 8
#define LIST_VISIBLE 4   /* rows the list's box shows; the rest scroll */
#define LIST_ROW 32
#define BOX_PAD 8
#define UI_FLOW 0xFFFF8059u
#define UI_TOP_DOWN 0xFFFF80CCu
#define UI_ALIGN_Y 0x12C /* the main menu sits at 0.95 */
#define TYPE_ALIGN_Y 0.12f
typedef void *(__attribute__((thiscall)) *fn_find_child)(void *widget, u32 id);
typedef void *(__attribute__((thiscall)) *fn_create_block)(void *parent, u32 id, int a0);
typedef void *(__attribute__((thiscall)) *fn_create_image)(void *parent, u32 id, const char *path,
                                                           int a0);
typedef void *(__attribute__((thiscall)) *fn_create_label)(void *parent, u32 id, const char *text,
                                                           int black, int replace);
typedef void *(__attribute__((thiscall)) *fn_create_nif)(void *parent, u32 id, const char *path,
                                                         int a0);
typedef void *(__attribute__((thiscall)) *fn_create_widget)(void *parent, u32 id, u32 factory,
                                                            int a0);
typedef void(__attribute__((thiscall)) *fn_set_size)(void *widget, int value);
typedef void(__attribute__((thiscall)) *fn_set_auto)(void *widget, int on);
typedef void(__attribute__((thiscall)) *fn_set_visible)(void *widget, int on);
typedef void(__attribute__((thiscall)) *fn_set_prop)(void *widget, u32 id, int value, int type);
typedef void(__attribute__((thiscall)) *fn_set_text)(void *widget, const char *text);
typedef void(__attribute__((thiscall)) *fn_layout)(void *widget, int a0);
typedef void(__cdecl *fn_set_focus)(void *widget, int on);
typedef char(__cdecl *fn_ui_handler)(void *owner, u32 id, int d0, int d1, void *source);
static const char *const button_states[3] = {"TES3X_normal", "TES3X_over", "TES3X_pressed"};
static const char *const main_rows[MENU_ROWS] = {
    "MenuOptions_New_container", "MenuOptions_Load_container", "MenuOptions_Options_container",
    "TES3X_Join", "MenuOptions_Exit_container"};
static const char *const server_rows[SERVERS_SHOWN] = {
    "TES3X_Server1", "TES3X_Server2", "TES3X_Server3", "TES3X_Server4",
    "TES3X_Server5", "TES3X_Server6", "TES3X_Server7", "TES3X_Server8"};
static const float gold[3] = {0.88f, 0.74f, 0.42f}, gold_lit[3] = {1.0f, 0.93f, 0.68f};
static char servers[SERVERS_SHOWN][JOIN_NAME + 1];
static u32 servers_n, servers_view, join_buttons, joins_pressed, menu_height, menu_width;
static int server_chosen = -1; /* a row, SERVER_NEW or SERVER_RETURN, from a click handler */
static u32 typing;             /* the keyboard is up, for TYPE_SERVER or TYPE_PASSWORD */
#define TYPE_SERVER 1u
#define TYPE_PASSWORD 2u
#define JOIN_WAIT_US 20000000u /* a join with no WELCOME by then has failed */
static u8 *join_row, *list_focus; /* the row being joined; the focus the list last followed */
static u32 join_started, join_failures, list_follows;
typedef u8 *(__attribute__((thiscall)) *fn_get_focus)(void *menu);
typedef char(__cdecl *fn_scroll_to)(void *element);
#define SERVER_NEW SERVERS_SHOWN
#define SERVER_RETURN (SERVERS_SHOWN + 1)
#define SERVER_OPEN (SERVERS_SHOWN + 2)

static int same_server(const char *a, const char *b)
{
    for (; *a && (*a | 0x20) == (*b | 0x20); a++, b++)
        ;
    return !*a && !*b;
}

static void server_add(const char *name, u32 n)
{
    u32 i;

    if (!n || n > JOIN_NAME)
        return;
    if (servers_n == SERVERS_SHOWN)
        servers_n--; /* the oldest goes */
    for (i = servers_n; i > 0; i--)
        copy((u8 *)servers[i], (const u8 *)servers[i - 1], JOIN_NAME + 1);
    copy((u8 *)servers[0], (const u8 *)name, n);
    servers[0][n] = 0;
    servers_n++;
    for (i = 1; i < servers_n; i++)
        if (same_server(servers[i], servers[0])) {
            for (; i + 1 < servers_n; i++)
                copy((u8 *)servers[i], (const u8 *)servers[i + 1], JOIN_NAME + 1);
            servers_n--;
            break;
        }
}

/* servers.ini is small; trust.text is free until the next `up` loads it again. Its last section is
 * the newest, so sections are added oldest first, each in front. */
static void servers_read(void)
{
    char server[JOIN_NAME + 1];
    IO_STATUS_BLOCK iosb;
    u64 offset = 0, size;
    u32 off, n, len = 0;
    void *h;

    servers_n = 0;
    if (!bulk_open(trust_path, GENERIC_READ, FILE_OPEN, 0, &h)) {
        size = bulk_size(h);
        if (size && size < TRUST_TEXT &&
            !NtReadFile(h, 0, 0, 0, &iosb, trust.text, (u32)size, &offset))
            len = iosb.Information;
        NtClose(h);
        trust.loaded = 0;
    }
    for (off = 0; off < len; off += n + 1) {
        const char *line = trust.text + off;
        n = line_length(line, len - off);
        if (n > 2 && line[0] == '[' && line[n - 1] == ']')
            server_add(line + 1, n - 2);
    }
    if ((n = ini_text("NetServer", server, sizeof(server))))
        server_add(server, n);
}

static u8 *menu_part(u8 *menu, const char *name)
{
    return ((fn_find_child)TES3X_NET_FIND_CHILD)(menu, ((fn_ui_id)TES3X_NET_UI_ID)(name));
}

/* Show one of a button's three images, as the engine's own handlers do, and lay out its menu. */
static void button_show(u8 *button, u32 which)
{
    fn_ui_id ui_id = (fn_ui_id)TES3X_NET_UI_ID;
    fn_find_child child = (fn_find_child)TES3X_NET_FIND_CHILD;
    u8 *image, *root = button, *up;
    u32 i;

    if (!plausible(button))
        return;
    for (i = 0; i < 3; i++)
        if (plausible(image = child(button, ui_id(button_states[i]))))
            ((fn_set_visible)TES3X_NET_SET_VISIBLE)(image, i == which);
    while (plausible(up = *(u8 **)(root + UI_PARENT)))
        root = up;
    ((fn_layout)TES3X_NET_PERFORM_LAYOUT)(root, 1);
}

/* A handler is given the menu as owner and the widget hit as source, which may be a part of the
 * button or row: the widget holding a part named so. */
static u8 *event_widget(void *source, const char *part)
{
    u32 id = ((fn_ui_id)TES3X_NET_UI_ID)(part), depth;
    u8 *el = source, *found;

    for (depth = 0; plausible(el) && depth < 4; depth++, el = *(u8 **)(el + UI_PARENT))
        if (plausible(found = ((fn_find_child)TES3X_NET_FIND_CHILD)(el, id)) && found != el)
            return el;
    return 0;
}

static char __cdecl button_pressed(void *owner, u32 id, int d0, int d1, void *source)
{
    (void)owner, (void)id, (void)d0, (void)d1;
    button_show(event_widget(source, "TES3X_pressed"), 2);
    return 1;
}

static char __cdecl button_over(void *owner, u32 id, int d0, int d1, void *source)
{
    (void)owner, (void)id, (void)d0, (void)d1;
    button_show(event_widget(source, "TES3X_pressed"), 1);
    return 1;
}

static char __cdecl button_left(void *owner, u32 id, int d0, int d1, void *source)
{
    (void)owner, (void)id, (void)d0, (void)d1;
    button_show(event_widget(source, "TES3X_pressed"), 0);
    return 1;
}

static void focusable(u8 *block, int width, fn_ui_handler click)
{
    fn_set_prop set = (fn_set_prop)TES3X_NET_SET_PROP;

    ((fn_set_size)TES3X_NET_SET_WIDTH)(block, width);
    ((fn_set_size)TES3X_NET_SET_HEIGHT)(block, MENU_ROW);
    set(block, UI_FLAG_B, 0, UI_PROP_INT);
    set(block, UI_FOCUS_A, (int)UI_TRUE, UI_PROP_ENUM);
    set(block, UI_FOCUS_B, (int)UI_TRUE, UI_PROP_ENUM);
    set(block, UI_CLICK, (int)click, UI_PROP_HANDLER);
}

/* A button as the main menu builds its own: a block holding Textures\NAME.tga, NAME_over and
 * NAME_pressed, the last two hidden until the pad or the pointer reaches it. */
static u8 *menu_button(u8 *parent, u32 id, const char *name, int width, fn_ui_handler click)
{
    static const char *const suffix[3] = {"", "_over", "_pressed"};
    fn_set_prop set = (fn_set_prop)TES3X_NET_SET_PROP;
    fn_ui_id ui_id = (fn_ui_id)TES3X_NET_UI_ID;
    char path[64];
    u8 *block, *image;
    u32 i;

    if (!plausible(block = ((fn_create_block)TES3X_NET_CREATE_BLOCK)(parent, id, 0)))
        return 0;
    focusable(block, width, click);
    set(block, UI_PRESS, (int)button_pressed, UI_PROP_HANDLER);
    set(block, UI_OVER, (int)button_over, UI_PROP_HANDLER);
    set(block, UI_LEAVE, (int)button_left, UI_PROP_HANDLER);
    for (i = 0; i < 3; i++) {
        *put_text(put_text(put_text(put_text(path, "Textures\\"), name), suffix[i]), ".tga") = 0;
        if (!plausible(image = ((fn_create_image)TES3X_NET_CREATE_IMAGE)(
                           block, ui_id(button_states[i]), path, 0)))
            continue;
        ((fn_set_size)TES3X_NET_SET_WIDTH)(image, width);
        ((fn_set_size)TES3X_NET_SET_HEIGHT)(image, MENU_ROW);
        image[UI_IMAGE_FLAG] = 0;
        set(image, UI_FLAG_B, 0, UI_PROP_INT);
        if (i)
            ((fn_set_visible)TES3X_NET_SET_VISIBLE)(image, 0);
    }
    return block;
}

/* A server's row: its address in the gold of the buttons, brighter while highlighted. */
static void row_colour(u8 *row, int lit)
{
    u8 *label;

    if (!plausible(row) || !plausible(label = menu_part(row, "TES3X_label")))
        return;
    copy(label + UI_COLOUR, (const u8 *)(lit ? gold_lit : gold), 12);
    label[UI_COLOUR_CHANGED] = 1;
    button_show(row, 0);
}

static char __cdecl row_over(void *owner, u32 id, int d0, int d1, void *source)
{
    (void)owner, (void)id, (void)d0, (void)d1;
    row_colour(event_widget(source, "TES3X_label"), 1);
    return 1;
}

static char __cdecl row_left(void *owner, u32 id, int d0, int d1, void *source)
{
    (void)owner, (void)id, (void)d0, (void)d1;
    row_colour(event_widget(source, "TES3X_label"), 0);
    return 1;
}

/* A row's text, in the small font when it has a ':', which the big one draws as ';'. */
static void row_label(u8 *row, const char *text)
{
    u8 *label = menu_part(row, "TES3X_label");
    u32 i;

    if (!plausible(label))
        return;
    for (i = 0; text[i] && text[i] != ':'; i++)
        ;
    *(int *)(label + UI_FONT) = !text[i];
    ((fn_set_text)TES3X_NET_WIDGET_SET_TEXT)(label, text);
}

/* The row holding el, which may be the row, its label or the menu's own element. */
static int server_row(const u8 *el)
{
    u8 *menu = ((fn_find_menu)TES3X_NET_FIND_MENU)(((fn_ui_id)TES3X_NET_UI_ID)("MenuOptions"));
    u32 depth;
    int i;

    for (depth = 0; plausible(el) && plausible(menu) && depth < 4;
         depth++, el = *(const u8 *const *)(el + UI_PARENT))
        for (i = 0; i < SERVERS_SHOWN; i++)
            if (menu_part(menu, server_rows[i]) == el)
                return i;
    return -1;
}

static char __cdecl join_click(void *owner, u32 id, int d0, int d1, void *source)
{
    (void)owner, (void)id, (void)d0, (void)d1, (void)source;
    server_chosen = SERVER_OPEN;
    return 1;
}

static char __cdecl row_click(void *owner, u32 id, int d0, int d1, void *source)
{
    (void)id, (void)d0, (void)d1;
    server_chosen = server_row(source);
    tes3x_log_hex3("net.join_row", (u32)owner, (u32)source, (u32)server_chosen);
    return 1;
}

static char __cdecl new_server_click(void *owner, u32 id, int d0, int d1, void *source)
{
    (void)owner, (void)id, (void)d0, (void)d1, (void)source;
    server_chosen = SERVER_NEW;
    return 1;
}

static char __cdecl return_click(void *owner, u32 id, int d0, int d1, void *source)
{
    (void)owner, (void)id, (void)d0, (void)d1, (void)source;
    server_chosen = SERVER_RETURN;
    return 1;
}

static void typing_text(u8 *menu, const char *text)
{
    char line[JOIN_NAME + 2];
    u32 i;

    for (i = 0; text[i] && i < JOIN_NAME; i++)
        line[i] = typing == TYPE_PASSWORD ? '*' : text[i];
    line[i++] = '_';
    line[i] = 0;
    ((fn_set_text)TES3X_NET_WIDGET_SET_TEXT)(menu_part(menu, "TES3X_TypeText"), line);
    ((fn_layout)TES3X_NET_PERFORM_LAYOUT)(menu, 1);
}

/* A box with the thin border the settings lists have, its children top to bottom. */
static u8 *bordered(u8 *parent, const char *name, int width, int height)
{
    fn_set_prop set = (fn_set_prop)TES3X_NET_SET_PROP;
    u8 *box = ((fn_create_nif)TES3X_NET_CREATE_NIF)(parent, ((fn_ui_id)TES3X_NET_UI_ID)(name),
                                                    "menu_thin_border.nif", 0);

    if (!plausible(box))
        return 0;
    ((fn_set_size)TES3X_NET_SET_WIDTH)(box, width);
    ((fn_set_size)TES3X_NET_SET_HEIGHT)(box, height);
    set(box, UI_FLOW, (int)UI_TOP_DOWN, UI_PROP_ENUM);
    set(box, UI_CHILD_ALIGN_X, UI_HALF, UI_PROP_FLOAT);
    return box;
}

static u8 *gold_label(u8 *parent, const char *name, const char *text)
{
    u8 *label = ((fn_create_label)TES3X_NET_CREATE_LABEL)(
        parent, ((fn_ui_id)TES3X_NET_UI_ID)(name), text, 0, 0);

    if (plausible(label)) {
        *(int *)(label + UI_FONT) = 1;
        copy(label + UI_COLOUR, (const u8 *)gold, 12);
    }
    return label;
}

/* The list's box, New Server and Return go at the end of the column, hidden; the box that shows
 * what is typed too. */
static void servers_build(u8 *column, int width)
{
    fn_set_prop set = (fn_set_prop)TES3X_NET_SET_PROP;
    fn_ui_id ui_id = (fn_ui_id)TES3X_NET_UI_ID;
    fn_set_visible visible = (fn_set_visible)TES3X_NET_SET_VISIBLE;
    u8 *box, *pane, *content, *row, *text;
    u32 i;

    if (plausible(box = bordered(column, "TES3X_TypeBox", 3 * width, 2 * LIST_ROW + 2 * BOX_PAD))) {
        /* the small font: the big one draws ':' as ';' */
        if (plausible(text = gold_label(box, "TES3X_TypeTitle", "IP/DNS:PORT")))
            *(int *)(text + UI_FONT) = 0;
        if (plausible(text = gold_label(box, "TES3X_TypeText", "_")))
            *(int *)(text + UI_FONT) = 0;
        visible(box, 0);
    }
    if (!plausible(box = bordered(column, "TES3X_ServerBox", 3 * width,
                                  LIST_VISIBLE * LIST_ROW + 2 * BOX_PAD)))
        return;
    pane = ((fn_create_widget)TES3X_NET_CREATE_WIDGET)(box, ui_id("TES3X_ServerPane"),
                                                       TES3X_NET_SCROLL_PANE, 0);
    if (!plausible(pane))
        return;
    ((fn_set_size)TES3X_NET_SET_WIDTH)(pane, 3 * width - 2 * BOX_PAD);
    ((fn_set_size)TES3X_NET_SET_HEIGHT)(pane, LIST_VISIBLE * LIST_ROW);
    content = menu_part(pane, "PartScrollPane_pane");
    if (!plausible(content))
        content = pane;
    set(content, UI_FLOW, (int)UI_TOP_DOWN, UI_PROP_ENUM);
    set(content, UI_CHILD_ALIGN_X, UI_HALF, UI_PROP_FLOAT);
    for (i = 0; i < SERVERS_SHOWN; i++) {
        if (!plausible(row = ((fn_create_block)TES3X_NET_CREATE_BLOCK)(
                           content, ui_id(server_rows[i]), 0)))
            continue;
        focusable(row, width, row_click);
        ((fn_set_size)TES3X_NET_SET_HEIGHT)(row, LIST_ROW);
        ((fn_set_auto)TES3X_NET_SET_AUTO_WIDTH)(row, 1); /* fitted to the name, so centred */
        set(row, UI_CHILD_ALIGN_Y, UI_HALF, UI_PROP_FLOAT);
        set(row, UI_OVER, (int)row_over, UI_PROP_HANDLER);
        set(row, UI_LEAVE, (int)row_left, UI_PROP_HANDLER);
        gold_label(row, "TES3X_label", "-");
    }
    visible(box, 0);
#ifdef TES3X_CONSOLE
    if (plausible(row = menu_button(column, ui_id("TES3X_NewServer"), "menu_newserver", 2 * width,
                                    new_server_click)))
        visible(row, 0);
#endif
    if (plausible(row = menu_button(column, ui_id("TES3X_Return"), "menu_return", width,
                                    return_click)))
        visible(row, 0);
}

/* Show the main column or the server list, link the pad's focus through what shows and put it on
 * the first. */
static void servers_show(u8 *menu, u32 on)
{
    fn_set_visible visible = (fn_set_visible)TES3X_NET_SET_VISIBLE;
    fn_set_prop set = (fn_set_prop)TES3X_NET_SET_PROP;
    u16 up = *(const u16 *)TES3X_NET_NAV_UP_ID, down = *(const u16 *)TES3X_NET_NAV_DOWN_ID;
    u8 *shown[SERVERS_SHOWN + 2], *part;
    u32 i, n = 0;

    for (i = 0; i < MENU_ROWS; i++)
        if (plausible(part = menu_part(menu, main_rows[i])))
            visible(part, !on);
    if (plausible(part = menu_part(menu, "TES3X_ServerBox")))
        visible(part, on);
    for (i = 0; i < SERVERS_SHOWN; i++)
        if (plausible(part = menu_part(menu, server_rows[i]))) {
            visible(part, on && i < servers_n);
            if (on && i < servers_n) {
                row_label(part, servers[i]);
                row_colour(part, 0);
                shown[n++] = part;
            }
        }
    if (plausible(part = menu_part(menu, "TES3X_NewServer"))) {
        visible(part, on);
        if (on)
            shown[n++] = part;
    }
    if (plausible(part = menu_part(menu, "TES3X_Return"))) {
        visible(part, on);
        if (on)
            shown[n++] = part;
    }
    servers_view = on;
    for (i = 0; i < n; i++) {
        set(shown[i], up, (int)shown[(i + n - 1) % n], UI_PROP_PTR);
        set(shown[i], down, (int)shown[(i + 1) % n], UI_PROP_PTR);
    }
    /* the menu's width is set, not fitted: the box is wider than a button */
    ((fn_set_size)TES3X_NET_SET_WIDTH)(menu, (int)(on ? 3 * menu_width + 2 * BOX_PAD : menu_width));
    ((fn_layout)TES3X_NET_PERFORM_LAYOUT)(menu, 1);
    part = on ? (n ? shown[0] : 0) : menu_part(menu, "TES3X_Join");
    if (plausible(part))
        ((fn_set_focus)TES3X_NET_SET_FOCUS)(part, 1);
}

/* While the keyboard is up the menu shows only what is typed, moved up above the keyboard. */
static void typing_show(u8 *menu, u32 on)
{
    static const char *const list[3] = {"TES3X_ServerBox", "TES3X_NewServer", "TES3X_Return"};
    static float bottom;
    fn_set_visible visible = (fn_set_visible)TES3X_NET_SET_VISIBLE;
    u8 *part;
    u32 i;

    for (i = 0; i < 3; i++)
        if (plausible(part = menu_part(menu, list[i])))
            visible(part, !on);
    if (plausible(part = menu_part(menu, "TES3X_TypeBox")))
        visible(part, on);
    if (on && plausible(part = menu_part(menu, "TES3X_TypeTitle")))
        ((fn_set_text)TES3X_NET_WIDGET_SET_TEXT)(part, typing == TYPE_PASSWORD ? "PASSWORD" :
                                                 "IP/DNS:PORT");
    if (on) {
        bottom = *(float *)(menu + UI_ALIGN_Y);
        *(float *)(menu + UI_ALIGN_Y) = TYPE_ALIGN_Y;
        typing_text(menu, "");
    } else {
        *(float *)(menu + UI_ALIGN_Y) = bottom;
        servers_show(menu, 1);
    }
    ((fn_layout)TES3X_NET_PERFORM_LAYOUT)(menu, 1);
}

static void row_text(u8 *menu, u8 *row, const char *a, const char *b, const char *c)
{
    char text[JOIN_NAME + 40];

    if (!plausible(row))
        return;
    *put_text(put_text(put_text(text, a), b), c) = 0;
    row_label(row, text);
    ((fn_layout)TES3X_NET_PERFORM_LAYOUT)(menu, 1);
}

/* 1 if the session in hand, or the one being opened, is to this server (NetServer's text,
 * fingerprint aside, as trust_configure keeps it). */
static int session_for(const char *server)
{
    u32 i;

    if (ses.state == SESSION_IDLE || ses.state == SESSION_REFUSED ||
        ses.state == SESSION_UNTRUSTED || (!ses.server && !ses.host[0]))
        return 0;
    for (i = 0; trust.name[i] && (trust.name[i] | 0x20) == (server[i] | 0x20); i++)
        ;
    return !trust.name[i] && (!server[i] || server[i] == '#');
}

static void join_server_now(u8 *menu, const char *server, u8 *row)
{
    if (lobby)
        return;
    joins_pressed++;
    if (server != join_server)
        copy((u8 *)join_server, (const u8 *)server, tes3x_strlen(server) + 1);
    lobby = 1;
    join_row = row;
    join_started = now_us();
    log_text("net.join", join_server);
    row_text(menu, row, "Joining ", join_server, "");
    /* NetAddress alone brings the NIC up without a session; NetServer's may be another server */
    if (!net.up || !session_for(join_server))
        autostart();
}

static void join_failed(u8 *menu, const char *why)
{
    u32 flags = lock();

    if (ses.state != SESSION_JOINED && ses.state != SESSION_REFUSED &&
        ses.state != SESSION_UNTRUSTED) {
        ses.state = SESSION_IDLE;
        handshake_reset();
    }
    unlock(flags);
    lobby = 0;
    join_failures++;
    log_text("net.join_failed", why);
    row_text(menu, join_row, join_server, " - ", why);
    join_row = 0;
}

static void password_ask(u8 *menu)
{
    lobby = 0;
#ifdef TES3X_CONSOLE
    if (tes3x_console_text_begin_on(menu, "")) {
        typing = TYPE_PASSWORD;
        typing_show(menu, 1);
        tes3x_log("net.join_password", trust.password_n);
        return;
    }
#endif
    join_failed(menu, "password needed");
}

/* Until WELCOME: what stopped the join, on its row. */
static void join_watch(u8 *menu)
{
    static const char *const refused[6] = {"refused", "different mods", "server full",
                                           "wrong password", "kicked", "banned"};
    u32 state = ses.state, reason = ses.refused_reason;

    if (!lobby || !join_row)
        return;
    if (state == SESSION_JOINED) {
        join_row = 0;
    } else if (state == SESSION_REFUSED && reason == 3) {
        password_ask(menu);
    } else if (state == SESSION_REFUSED) {
        join_failed(menu, refused[reason < 6 ? reason : 0]);
    } else if (state == SESSION_UNTRUSTED) {
        join_failed(menu, "server key changed");
    } else if (now_us() - join_started > JOIN_WAIT_US) {
        join_failed(menu, state == SESSION_DHCP ? "no network address" :
                          state == SESSION_RESOLVE ? "name not found" :
                          state == SESSION_ARP ? "no route" : "no answer");
    }
}

/* The D-pad moves focus along NAV links without the events that light a button, so they are sent
 * here; on a server row the list scrolls to show it. */
static void list_follow(u8 *menu)
{
    fn_trigger_event trigger = (fn_trigger_event)TES3X_NET_TRIGGER_EVENT;
    u8 *focus = ((fn_get_focus)TES3X_NET_GET_FOCUS)(menu);

    if (focus == list_focus)
        return;
    if (plausible(list_focus))
        trigger(list_focus, UI_LEAVE, 0, 0, list_focus);
    if (plausible(focus))
        trigger(focus, UI_OVER, 0, 0, focus);
    list_focus = focus;
    if (plausible(focus) && server_row(focus) >= 0) {
        ((fn_scroll_to)TES3X_NET_SCROLL_TO)(focus);
        list_follows++;
    }
}

/* New server: whatever was typed, without spaces, is the server to join. A password goes into
 * the server's section of servers.ini and the join is made again. */
static void typing_frame(u8 *menu)
{
#ifdef TES3X_CONSOLE
    char text[JOIN_NAME + 1];
    const char *now = tes3x_console_text_now();
    u8 *field = tes3x_console_text_field();
    u32 i, n = 0, kind = typing;
    int status = tes3x_console_text_poll(text, sizeof(text));

    if (!status) {
        if (now)
            typing_text(menu, now);
        /* the keyboard echoes what is typed above its keys; the box shows a password masked */
        if (kind == TYPE_PASSWORD && plausible(field) && field[MENU_VISIBLE])
            ((fn_set_visible)TES3X_NET_SET_VISIBLE)(field, 0);
        return;
    }
    typing = 0;
    typing_show(menu, 0);
    if (kind == TYPE_PASSWORD) {
        for (n = 0; status == 2 && text[n] && n < PASSWORD_MAX; n++)
            trust.password[n] = text[n];
        crypto_wipe(text, sizeof(text));
        if (!n) {
            join_failed(menu, "password needed");
            return;
        }
        trust.password_n = n;
        trust.dirty = 1;
        join_server_now(menu, join_server, join_row);
        return;
    }
    if (status != 2)
        return;
    for (i = 0; text[i]; i++)
        if (text[i] != ' ')
            text[n++] = text[i];
    text[n] = 0;
    log_text("net.join_typed", text);
    if (!n)
        return;
    server_add(text, n);
    servers_show(menu, 1);
    join_server_now(menu, servers[0], menu_part(menu, server_rows[0]));
#else
    (void)menu;
    typing = 0;
#endif
}

static void join_frame(void)
{
    fn_ui_id ui_id = (fn_ui_id)TES3X_NET_UI_ID;
    fn_set_prop set = (fn_set_prop)TES3X_NET_SET_PROP;
    u16 up = *(const u16 *)TES3X_NET_NAV_UP_ID, down = *(const u16 *)TES3X_NET_NAV_DOWN_ID;
    u8 *menu, *exit, *above, *column, *button, **begin, **end, **at;
    int chosen;

    if (player_reference())
        return; /* a game is loaded: Load and New relaunch, and the pause menu has no Join */
    if (!plausible(menu = ((fn_find_menu)TES3X_NET_FIND_MENU)(ui_id("MenuOptions"))))
        return;
    if (!plausible(button = menu_part(menu, "TES3X_Join"))) {
        exit = menu_part(menu, "MenuOptions_Exit_container");
        above = menu_part(menu, "MenuOptions_Options_container");
        if (!plausible(exit) || !plausible(column = *(u8 **)(exit + UI_PARENT)) ||
            !plausible(button = menu_button(column, ui_id("TES3X_Join"), "menu_join",
                                            *(const int *)(exit + UI_WIDTH), join_click)))
            return;
        /* the menu's height is set, not fitted: one row more */
        menu_height = *(const int *)(menu + UI_HEIGHT) + MENU_ROW;
        menu_width = *(const int *)(menu + UI_WIDTH);
        ((fn_set_size)TES3X_NET_SET_HEIGHT)(menu, (int)menu_height);
        /* Join goes above Exit: the block was added last to its column's children */
        begin = *(u8 ***)(column + UI_CHILDREN);
        end = *(u8 ***)(column + UI_CHILDREN + 4);
        if (plausible(begin) && end > begin && end[-1] == button) {
            for (at = end - 1; at > begin && at[-1] != exit; at--)
                ;
            if (at > begin) {
                for (at = end - 1; at[-1] != exit; at--)
                    at[0] = at[-1];
                at[0] = at[-1];
                at[-1] = button;
            }
        }
        set(button, down, (int)exit, UI_PROP_PTR);
        set(exit, up, (int)button, UI_PROP_PTR);
        if (plausible(above)) {
            set(button, up, (int)above, UI_PROP_PTR);
            set(above, down, (int)button, UI_PROP_PTR);
        }
        servers_build(column, *(const int *)(exit + UI_WIDTH));
        servers_view = 0;
        ((fn_layout)TES3X_NET_PERFORM_LAYOUT)(menu, 0);
        join_buttons++;
    }
    if (typing) {
        typing_frame(menu);
        return;
    }
    join_watch(menu);
    if (typing)
        return;
    if (servers_view)
        list_follow(menu);
    chosen = server_chosen;
    server_chosen = -1;
    if (chosen == SERVER_OPEN) {
        servers_read();
        servers_show(menu, 1);
    } else if (chosen == SERVER_RETURN) {
        servers_show(menu, 0);
#ifdef TES3X_CONSOLE
    } else if (chosen == SERVER_NEW) {
        typing = tes3x_console_text_begin_on(menu, "") ? TYPE_SERVER : 0;
        tes3x_log("net.join_keyboard", typing);
        if (typing)
            typing_show(menu, 1);
#endif
    } else if (chosen >= 0 && chosen < (int)servers_n) {
        join_server_now(menu, servers[chosen], menu_part(menu, server_rows[chosen]));
    }
}

static void join_stat(void)
{
    tes3x_log_hex3("net.join_menu", join_buttons, joins_pressed, lobby);
    tes3x_log_hex3("net.join_servers", servers_n, servers_view, typing);
    tes3x_log_hex3("net.join_failures", join_failures, list_follows, trust.password_n);
}

/* Once per frame, from the Game::Update hook. */
void tes3x_net_frame(void)
{
    static u8 last_cell[CELL_NAME];
    static u32 logged_player, logged_server, logged_refused, logged_ip, known[PEERS];
    u8 state[STATE_BYTES];
    const u8 *ref;
    u32 i, flags;

    if (!ini_checked) {
        ini_checked = 1;
        autostart();
    }
    if (net.up && dhcp.state && net.ip != logged_ip) {
        logged_ip = net.ip;
        tes3x_log_hex3("net.dhcp", net.ip, net.mask, ses.gateway);
        if (net.ip)
            tes3x_log_hex3("net.dhcp_from", dhcp.server, ses.dns, dhcp.lease / (1000 / TICK_MS));
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
        if (logged_refused)
            tes3x_log_hex3("net.refused_reason", ses.refused_reason, 0, 0);
    }
    spell_sent_count = 0;
    cast_aim_frame();
    join_frame();
    if (net.up) {
        spell_hook_install();
        shot_hook_install();
        ai_hook_install();
        objects_hook_install();
        leveled_hook_install();
        summon_hook_install();
        player_hook_install();
        save_hook_install();
        quit_hook_install();
        death_hook_install();
        authority_session();
        spawns_session();
        events_frame();
        save_frame();
        respawn_frame();
        file_frame();
        handshake_frame();
    }
    ref = player_reference();
    menu_frame(net.up && ref);
    weather_frame(net.up && ref);
    if (!net.up || !ref)
        return;
    clock_frame();
    bounty_frame();
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
        /* A peer whose leave notice was lost drops out once it goes quiet, unless it is saving. */
        if (client && !peers[i].busy && now_us() - peers[i].time > PEER_TIMEOUT_US)
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
    equipment_send(ref);
    ghosts_frame(state);
    authority_frame(ref, state);
    objects_frame();
    spawns_frame();
    containers_frame();
    player_frame(ref);
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
        weather_stat();
        authority_stat();
        spell_stat();
        status_stat();
        bounty_stat();
        shot_stat();
        objects_stat();
        spawns_stat();
        containers_stat();
        bulk_stat();
        up_stat();
        handshake_stat();
        save_stat();
        chargen_stat();
        player_stat();
        death_stat();
        join_stat();
    } else if ((rest = word(text, "menusim")) && (rest = word(skip(rest), "auto")) &&
               !*skip(rest)) {
        menu_forced = 0;
    } else if ((rest = word(text, "menusim")) && (rest = number(skip(rest), &value)) &&
               !*skip(rest)) {
        menu_forced = 1 + (value != 0);
        menu_sim(value != 0);
    } else if ((rest = word(text, "weather")) && (rest = word(skip(rest), "roll")) &&
               (rest = skip(rest)) && (word(rest, "auto") || number(rest, &value))) {
        weather_forced = word(rest, "auto") ? 0 : 1 + (value != 0);
    } else if ((rest = word(text, "send")) && *(rest = skip(rest))) {
        up_command(rest);
    } else if ((rest = word(text, "leave")) && !*skip(rest)) {
        ((fn_quit)*(const u32 *)TES3X_NET_QUIT_SITE)(); /* what Exit's Yes calls */
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
