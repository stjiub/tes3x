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
/* A New Game started from a character file the server sent (serve --load-state) carries
 * CHAR_MAGIC and the file's name, which the [PreLoad] read writes into the player. */
#define CHAR_MAGIC 0x434D3354u /* "T3MC", also the file's first four bytes */
#define CHAR_AT 0x300u
#define LAUNCH_BYTES 0xC00u /* what XLaunchNewImage copies */
#define CHAR_SUFFIX ".t3c"
typedef u32(__attribute__((stdcall)) *fn_launch)(const char *xbe, void *data);
typedef u32(__cdecl *fn_persist)(void);
static void preload_hook_install(void);

/* XBE entry, before the engine reads its launch data. */
static void multi_started(void)
{
    tes3x_net_mac(mac);
    worker_start();
}

static void multi_closing(void)
{
    if (ses.state == SESSION_JOINED)
        session_send(T3MP_BYE, 0, 0);
    ses.state = SESSION_IDLE;
}

void tes3x_multi_entry(void)
{
    const u8 *page = *(const u8 *const *)*(void ***)THUNK_LaunchDataPage, *data, *path;
    u32 lo, hi, i, base = 0;

    multi_channel.port = PORT;
    multi_channel.receive = multi_receive;
    multi_channel.tick = multi_tick;
    multi_channel.sample = entropy_add;
    multi_channel.frame = tes3x_multi_frame;
    multi_channel.command = tes3x_multi_command;
    multi_channel.closing = multi_closing;
    multi_channel.stopping = worker_stop;
    multi_channel.started = multi_started;
    dhcp_channel.port = DHCP_CLIENT;
    dhcp_channel.receive = dhcp_receive;
    if (!tes3x_net_register(&multi_channel) || !tes3x_net_register(&dhcp_channel))
        tes3x_log("net.channel_failed", 0);

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
    if (*(const u32 *)(data + 0xC) == BXWM_NEW_GAME) {
        game_launch = GAME_NEW;
        if (*(const u32 *)(data + CHAR_AT) == CHAR_MAGIC) {
            for (i = 0; i < BULK_NAME && data[CHAR_AT + 4 + i]; i++)
                game_loaded[i] = (char)data[CHAR_AT + 4 + i];
            game_loaded[i] = 0;
        }
        preload_hook_install();
    }
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

static int is_character_file(const char *name);

static void load_event(const struct event *e)
{
    u32 i;

    for (i = 0; i < e->length && i < BULK_NAME && e->data[i]; i++)
        load_name[i] = (char)e->data[i];
    load_name[i] = 0;
    load_wanted = i != 0 && is_character_file(load_name);
    log_text("net.load_wanted", load_name);
}

/* The title relaunches to load the server's copy of the character; say so once it is up. */
static void launch_notice_frame(void)
{
    static u32 told;

    if (told || ses.state != SESSION_JOINED || !game_launch || !game_loaded[0] ||
        !player_reference())
        return;
    told = 1;
    notice("Restored from the server's last saved state.");
}

static int is_character_file(const char *name)
{
    u32 n = tes3x_strlen(name), i;

    for (i = 0; i < 4 && n >= 4; i++)
        if ((name[n - 4 + i] | 0x20) != (CHAR_SUFFIX[i] | 0x20))
            return 0;
    return n > 4;
}

/* Relaunch a New Game, optionally seeded by the retained character file. */
static int relaunch(const char *name)
{
    static u8 data[LAUNCH_BYTES];
    const u8 *world = *(const u8 **)TES3X_NET_WORLD, *pads;
    u32 i, r;

    if (!plausible(world) || !plausible(pads = *(const u8 *const *)(world + 0x4C)))
        return 0;
    for (i = 0; i < sizeof(data); i++)
        data[i] = 0;
    ((u32 *)data)[0] = BXWM_MAGIC;
    ((u32 *)data)[1] = *(const u32 *)(pads + 0x804); /* the pad port, as the Load menu passes */
    ((u32 *)data)[3] = BXWM_NEW_GAME;
    if (name && is_character_file(name)) {
        *(u32 *)(data + CHAR_AT) = CHAR_MAGIC;
        for (i = 0; name[i] && i < BULK_NAME; i++)
            data[CHAR_AT + 4 + i] = (u8)name[i];
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

/* Once the character seed is here, relaunch into its New Game. */
static void load_frame(void)
{
    u32 i;

    if (load_wanted && bulk.state == BULK_NO_SPACE && same_name(bulk.name, load_name)) {
        load_wanted = 0;
        log_text("net.load_no_space", load_name);
        notice("There is not enough free space on the hard disk to load your "
               "character from the server.");
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

/* Exit waits for a durable state flush. An unconfirmed leave offers Leave or Stay. */
#define LEAVE_SAVE 1u
#define LEAVE_UPLOAD 2u
#define LEAVE_ASK 3u
#define LEAVE_TIMEOUT_US 30000000u
typedef unsigned char(__cdecl *fn_quit)(void);
static u32 leave_state, leave_since, leave_upload, leave_told, quit_hooked;
static u32 leaves_saved, leaves_forced, leaves_stayed;
static u32 leave_quits = 1; /* Save to Server uses the flush without quitting. */
static u32 leave_idle;
#define SAVE_IDLE_FRAMES 10
static int world_idle(void);
static u32 server_saves, server_saves_failed;

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
        notice("You cannot leave while dead.");
        return 1;
    }
    leave_state = LEAVE_SAVE;
    leave_since = now_us();
    leave_told = 0;
    leave_quits = 1;
    return 1;
}

static void save_to_server(void)
{
    if (ses.state != SESSION_JOINED || leave_state)
        return;
    if (player_dead) {
        notice("You cannot save while dead.");
        return;
    }
    leave_state = LEAVE_SAVE;
    leave_since = now_us();
    leave_told = 0;
    leave_quits = 0;
    leave_idle = 0;
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
    snapshot_stage = snapshot_wait = 0;
    if (!leave_quits) {
        server_saves_failed++;
        leave_state = 0;
        notice("The server has not confirmed your save.");
        return;
    }
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
        /* Give the closing pause menu time to release its player view. */
        if (!leave_quits && (!world_idle() || ++leave_idle < SAVE_IDLE_FRAMES)) {
            if (!world_idle())
                leave_idle = 0;
            if (now_us() - leave_since >= LEAVE_TIMEOUT_US)
                leave_ask("a menu stayed open");
            return;
        }
        if (!leave_told && leave_quits) {
            leave_told = 1;
            notice("Saving to the server...");
            return;
        }

        if (ses.state != SESSION_JOINED || !plausible(world) ||
            !plausible(global = *(const u8 *const *)(world + CHARGEN_STATE)) ||
            *(const u32 *)(global + 0x34) != 0xBF800000u || !slot_name()) {
            leave_ask("character creation is incomplete");
            return;
        }
        snapshot_begin();
        leave_upload = snapshot_token;
        leave_state = LEAVE_UPLOAD;
    } else if (leave_state == LEAVE_UPLOAD) {
        if (snapshot_confirmed == leave_upload && !leave_quits) {
            server_saves++;
            leave_state = 0;
            tes3x_log("net.server_saved", snapshot_confirmed);
            notice("Saved to the server.");
        } else if (snapshot_confirmed == leave_upload) {
            leaves_saved++;
            leave_state = 0;
            quit();

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

/* Diagnostic fixture uploads run separately from normal state flushes. */
static void chargen_frame(void);
static void arrival_frame(void);
static void refusal_frame(void);
static void session_ended_frame(void);
static void welcome_frame(void);
static void preload_frame(void);

static void save_frame(void)
{
    if (ses.state == SESSION_JOINED) {
        game_frame();
        load_frame();
        chargen_frame();
    }
    arrival_frame();
    refusal_frame();
    session_ended_frame();
    welcome_frame();
    preload_frame();
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

    if (ses.state == SESSION_JOINED && !diagnostic_save) {
        snapshot_begin();
        return 1;
    }
    diagnostic_save = 0;
    if (player_dead && ses.state == SESSION_JOINED)
        return 0;
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
    tes3x_log_hex3("net.snapshot", snapshot_stage, snapshot_wait, snapshot_confirmed);
    tes3x_log_hex3("net.game", game_token, load_wanted, loads_started);
    tes3x_log_hex3("net.leaves", leaves_saved, leaves_forced, leaves_stayed);
    tes3x_log_hex3("net.leave_state", leave_state, quit_hooked, leave_quits);
    tes3x_log_hex3("net.server_saves", server_saves, server_saves_failed, 0);
}
