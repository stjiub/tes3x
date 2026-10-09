/* Multiplayer client over the shared TES3X network layer: a session with a PC server and the
 * engine hooks that exchange player and world state.
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
 *   tes3xnet notice TEXT  show TEXT on this console's screen
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
 */

#include "tes3x_thunks.h"
#include "tes3xnt.h"
#include "tes3xini.h"
#include "tes3xlaunch.h"
#include "tes3xnet.h"
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
#ifndef TES3X_NET_SHOW_MESSAGE
#error "define TES3X_NET_SHOW_MESSAGE to showMessageBox"
#endif
#if !defined(TES3X_NET_FIND_MENU) || !defined(TES3X_NET_UI_ID) || !defined(TES3X_NET_TRIGGER_EVENT)
#error "define TES3X_NET_FIND_MENU, TES3X_NET_UI_ID and TES3X_NET_TRIGGER_EVENT to the UI functions"
#endif
#if !defined(TES3X_NET_FIND_REFERENCE) || !defined(TES3X_NET_REF_ANIMATION) || \
    !defined(TES3X_NET_REF_ORIENTATION) || !defined(TES3X_NET_REF_ROTATION) || \
    !defined(TES3X_NET_NODE_SET_ROTATION) || !defined(TES3X_NET_NODE_UPDATE) || \
    !defined(TES3X_NET_NODE_UPDATE_EFFECTS) || !defined(TES3X_NET_NODE_UPDATE_PROPERTIES) || \
    !defined(TES3X_NET_ANIM_HAS_GROUP) || !defined(TES3X_NET_ANIM_PLAY_GROUP) || \
    !defined(TES3X_NET_BODY_PART_UPDATE) || !defined(TES3X_NET_EXTERIOR_CHANGE) || \
    !defined(TES3X_NET_PRELOAD_FIND_SITE) || \
    !defined(TES3X_NET_UNREADY_WEAPON) || !defined(TES3X_NET_MOBILE_HANDS) ||     !defined(TES3X_NET_APPLY_HEALTH_DAMAGE) || !defined(TES3X_NET_APPLY_FATIGUE_DAMAGE) ||     !defined(TES3X_NET_HIT_STUN) || !defined(TES3X_NET_HIT_STUN_SITES)
#error "define the TES3X_NET_ functions SetPos, SetAngle, PlayGroup, weapon readying and damage"
#endif
#if !defined(TES3X_NET_SPELL_HIT) || !defined(TES3X_NET_SPELL_HIT_SITES) || \
    !defined(TES3X_NET_ACTIVATE_SPELL) || !defined(TES3X_NET_MAGIC_INSTANCE) || \
    !defined(TES3X_NET_RESOLVE_OBJECT) || !defined(TES3X_NET_CAST_BOLT) || \
    !defined(TES3X_NET_CAST_BOLT_SITES) || !defined(TES3X_NET_SOURCE_EFFECTS) || \
    !defined(TES3X_NET_EFFECT_INSERT) || !defined(TES3X_NET_ACTIVE_EFFECT_NODE) || \
    !defined(TES3X_NET_RETIRE_EFFECTS) || !defined(TES3X_NET_MAGIC_PROCESS) || \
    !defined(TES3X_NET_EFFECT_CALLBACKS) || !defined(TES3X_NET_EFFECT_ICONS) || \
    !defined(TES3X_NET_ALCHEMY_NEW) || !defined(TES3X_NET_ADD_NEW_OBJECT) || \
    !defined(TES3X_NET_SET_STRING_SLOT)
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
#if !defined(TES3X_NET_FIND_RACE) || !defined(TES3X_NET_FIND_CLASS) || \
    !defined(TES3X_NET_FIND_BIRTHSIGN) || !defined(TES3X_NET_ENGINE_ALLOCATE) || \
    !defined(TES3X_NET_CLASS_NEW) || !defined(TES3X_NET_CLASS_SET_ID) || \
    !defined(TES3X_NET_CLASS_DESCRIPTION) || !defined(TES3X_NET_CLASS_SET_DESCRIPTION) || \
    !defined(TES3X_NET_LIST_APPEND) || !defined(TES3X_NET_GMST_TEXT) || \
    !defined(TES3X_NET_RACE_SEX_OK) || !defined(TES3X_NET_UNEQUIP_ITEM) || \
    !defined(TES3X_NET_EQUIP_ITEM) || !defined(TES3X_NET_INVENTORY_BUILD) || \
    !defined(TES3X_NET_MENU_TAB)
#error "define the TES3X_NET_ record lookups and the class functions chargen uses"
#endif

typedef unsigned short u16;
typedef void(__stdcall *fn_KeStallExecutionProcessor)(u32);
typedef u32(__stdcall *fn_PsCreateSystemThreadEx)(void **, u32, u32, u32, void **,
                                                  void(__stdcall *)(void *), void *,
                                                  unsigned char, unsigned char, void *);
typedef void(__stdcall *fn_PsTerminateSystemThread)(u32);
typedef u32(__stdcall *fn_KeDelayExecutionThread)(u32, unsigned char, long long *);
typedef long(__stdcall *fn_KeSetBasePriorityThread)(void *, long);
typedef long(__stdcall *fn_KeQueryBasePriorityThread)(void *);

#define KeStallExecutionProcessor \
    KFN(THUNK_KeStallExecutionProcessor, fn_KeStallExecutionProcessor)
#define PsCreateSystemThreadEx KFN(THUNK_PsCreateSystemThreadEx, fn_PsCreateSystemThreadEx)
#define PsTerminateSystemThread KFN(THUNK_PsTerminateSystemThread, fn_PsTerminateSystemThread)
#define KeDelayExecutionThread KFN(THUNK_KeDelayExecutionThread, fn_KeDelayExecutionThread)
#define KeSetBasePriorityThread KFN(THUNK_KeSetBasePriorityThread, fn_KeSetBasePriorityThread)
#define KeQueryBasePriorityThread \
    KFN(THUNK_KeQueryBasePriorityThread, fn_KeQueryBasePriorityThread)

#define TES3X_NET_STR_(x) #x
#define TES3X_NET_STR(x) TES3X_NET_STR_(x)
#define BUF 2048u
#define PORT TES3X_NET_PORT
#define net tes3x_net
#define lock tes3x_net_lock
#define unlock tes3x_net_unlock
#define now_us tes3x_net_now_us

#define TICK_MS 250u
#define HELLO_TICKS 4u
#define HEARTBEAT_TICKS 4u
#define TIMEOUT_TICKS 60u
#define CPU_MHZ 733u
#define CR0_WP 0x10000u

/* Session packet: "T3MP", version, type, then session, seq, ack, time and echoed peer time. On
 * the wire only the handshake is not sealed: a SEALED packet keeps "T3MP", version, its type,
 * session and seq in the clear (T3MP_OUTER, the AEAD's associated data) and seals the real type,
 * ack, times and body under the session's key, seq being the nonce. The receiver rebuilds the
 * T3MP_HEADER layout after opening it. */
#define T3MP_VERSION 22u
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
#define BUILD_ID 32u /* the manifest's, after the clock: a server serving another refuses it */
#define HELLO_BYTES (18u + CLOCK_BYTES + BUILD_ID)
static u8 build_id[BUILD_ID];
#define REFUSED_STALE 6u /* the build is not the server's: the manager updates it */
#define REFUSED_PROTOCOL 7u
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
    u32 state, server, port, gateway, id, client, seq, peer_seq, peer_time;
    u32 ticks, quiet, hellos, welcomes, beats_out, beats_in, timeouts, gaps;
    u32 rtt_last, rtt_min, rtt_max, rtt_sum, rtt_count;
    u32 states_out, peers_in;
    u32 dns, dns_id, dns_queries, dns_answers;
    u32 plugins, plugins_hash, refused_hash, refused_plugins, refused_reason;
    char host[HOST_NAME]; /* the server's name, if it is not an address */
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
#define EVENT_IDENTITY 33u /* two parts: sex, then name/race or head/hair */
#define EVENT_ACTOR_EQUIPMENT 34u /* actor id, part, parts, then equipped item ids */
#define EVENT_WELCOME 35u /* the server's welcome text, shown in a box once the player is in */
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
static u32 player_hits_unseen;
static void ghost_heading_stat(void);
static u32 ghost_crimes_blocked, ghost_crime_hooked;
static u32 equip_sent, equip_received, equip_applied, stance_changes, stance_refused;
static u32 identity_sent, identity_received, identity_applied, identity_refused;
static u32 actor_equip_sent, actor_equip_received, actor_equip_applied, actor_equip_refused;
static u32 first_person_states; /* player states whose animation came from the first person */
static u32 ini_checked;
static u8 mac[6];
static struct tes3x_net_channel multi_channel;
static struct tes3x_net_channel dhcp_channel;

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
static void build_id_load(void);
static void trust_configure(const char *name, u32 n, const u8 *fingerprint);
static void bulk_tick(void);
void tes3x_multi_frame(void);
int tes3x_multi_command(const char *text);

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

/* One translation unit: each section uses what the ones before it define. */
#include "multi/session.c"
#include "multi/menus.c"
#include "multi/commands.c"
#include "multi/animation.c"
#include "multi/ghosts.c"
#include "multi/looks.c"
#include "multi/ghost_frame.c"
#include "multi/authority.c"
#include "multi/spells.c"
#include "multi/shots.c"
#include "multi/objects.c"
#include "multi/spawns.c"
#include "multi/containers.c"
#include "multi/statuses.c"
#include "multi/download.c"
#include "multi/upload.c"
#include "multi/trust.c"
#include "multi/worker.c"
#include "multi/handshake.c"
#include "multi/saves.c"
#include "multi/checkpoint.c"
#include "multi/characters.c"
#include "multi/player.c"
#include "multi/player_identity.c"
#include "multi/player_effects.c"
#include "multi/player_apply.c"
#include "multi/death.c"
#include "multi/player_events.c"
#include "multi/preload.c"
#include "multi/events.c"
#include "multi/world.c"
#include "multi/servers.c"
#include "multi/pause.c"

/* Once per frame, from the Game::Update hook. */
void tes3x_multi_frame(void)
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
        ghost_crime_hook_install();
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
    connection_notice_frame();
    launch_notice_frame();
    notice_frame();
    menu_frame(net.up && ref);
    pause_frame(net.up && ref);
    weather_frame(net.up && ref);
    if (!net.up || !ref)
        return;
    clock_frame();
    bounty_frame();
    player_state(ref, state);
    player_view_frame();
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
    identity_send(ref);
    equipment_send(ref);
    ghosts_frame(state);
    authority_frame(ref, state);
    objects_frame();
    spawns_frame();
    containers_frame();
    player_frame(ref);
}

int tes3x_multi_command(const char *text)
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
            tes3x_net_stop();
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
        pause_stat();
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
    } else if ((rest = word(text, "save")) && !*skip(rest)) {
        save_to_server();
    } else if ((rest = word(text, "leave")) && !*skip(rest)) {
        ((fn_quit)*(const u32 *)TES3X_NET_QUIT_SITE)(); /* what Exit's Yes calls */
    } else if ((rest = word(text, "notice")) && *(rest = skip(rest))) {
        notice(rest);
    } else if ((rest = word(text, "say")) && *(rest = skip(rest))) {
        for (value = 0; rest[value] && value < EVENT_DATA; value++)
            ;
        if (!event_queue(EVENT_TEXT, (const u8 *)rest, value))
            tes3x_log("net.event_full", rel.out_next - rel.out_first);
    } else if (!*text) {
        tes3x_net_broadcast(8);
    } else if ((rest = number(text, &value)) && !*skip(rest) && value) {
        tes3x_net_broadcast(value);
    } else {
        tes3x_log("net.usage", 0);
    }
    return 1;
}
