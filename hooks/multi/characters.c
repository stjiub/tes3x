/* Characters. After GAME the server lists this key's characters (CHARS) or has the console make
 * one (NEWCHAR, the start points); the player picks from a message box and the console answers
 * PICK. A character is made in a New Game, relaunching into one if needed. Once CharGen has run
 * (a launch joining a server starts in CHARGEN_CELL; any other is moved there from the prison
 * ship), the player waits in CHARGEN_CELL through the name, race, class, birthsign and review
 * menus and picks a start point. What the boat and
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
 * would have done by the time the player leaves the census office. The outside door's journal tip
 * (CharGenJournalMessage) fires for anyone within 300 units of it, so it is marked shown. */
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
    "AddTopic \"latest rumors\"", "AddTopic \"little advice\"",
    "set chargendoorjournal.done to 1", "set \"chargen captain\".done to 1",
    "set \"chargen captain\".state to -1", "set \"chargen class\".state to -1",
    "set \"chargen name\".state to -1", "set \"chargen dock guard\".state to -1",
    "set \"chargen boat guard 2\".state to -1", "set \"chargen door guard\".done to 1",
    "set chargen_shipdoor.done to 1", "set \"chargen door captain\".done to 1",
    "set \"CharGen Exit Door\".done to 1", "set CharGenState to -1"};

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

/* New Game starts in the cell [PreLoad] Cell 0 names. A launch that will join a server starts in
 * CHARGEN_CELL instead, where TES3X Multiplayer.esp's CharGen keeps the player off the prison ship.
 * The ini reader needs the game drive, mounted by the time New Game reads the cell. */
#define ARRIVAL_HOOKED 1u
#define ARRIVAL_STARTED 2u /* the launch began in CHARGEN_CELL */
#define ARRIVAL_CLAIMED 3u /* ... and the server is making or loading a character */
#define ARRIVAL_SHIP 4u    /* ... and nothing came, so the player went on to the ship */
#define ARRIVAL_JOIN_US 60000000u   /* to join */
#define ARRIVAL_ANSWER_US 15000000u /* once joined, for CHARS, NEWCHAR or LOAD */
typedef u32(__cdecl *fn_ini_read)(const char *section, const char *key, const char *fallback,
                                  char *out, u32 size, const char *file);
typedef u8 *(__attribute__((thiscall)) *fn_find_cell)(void *records, const char *name);
static fn_ini_read preload_read;
static fn_find_cell preload_find;
static u32 arrival_start;
static u32 preloading; /* the character file is being applied, before the world's first frame */
static u32 preload_asked, preload_finished;
static u8 preload_place[20 + CELL_NAME]; /* a PLAYER PLACE body; flags 0 without one */
static int preload_character(void);

static u32 __cdecl preload_cell(const char *section, const char *key, const char *fallback,
                                char *out, u32 size, const char *file)
{
    char probe[JOIN_NAME + 1];
    u32 n = preload_read(section, key, fallback, out, size, file);

    if (!join_server[0] && !game_loaded[0] && (!ini_text("NetAddress", probe, sizeof(probe)) ||
                                               !ini_text("NetServer", probe, sizeof(probe))))
        return n;
    for (n = 0; CHARGEN_CELL[n] && n + 1 < size; n++)
        out[n] = CHARGEN_CELL[n];
    out[n] = 0;
    arrival_start = ARRIVAL_STARTED;
    tes3x_log("net.arrival_start", join_server[0] != 0);
    if (game_loaded[0] && preload_character())
        arrival_start = ARRIVAL_CLAIMED;
    return n;
}

/* The start cell's lookup by name, answered with the character's own interior. An exterior has
 * no name of its own to find it by: the player is put at the spot and the world changes to the
 * exterior there, as a cell change does, and New Game is told there is no cell to load. */
typedef void(__attribute__((thiscall)) *fn_exterior_change)(void *handler, const float *at);

static u8 *__attribute__((thiscall)) preload_cell_find(void *records, const char *name)
{
    u32 flags = get32le(preload_place);
    u8 *cell = 0, *ref = (u8 *)player_reference();

    if (preload_asked++ || !(flags & STATE_IN_WORLD))
        return preload_find(records, name); /* Cell 1 and on, or no place kept */
    if (flags & STATE_INTERIOR) {
        if (script_safe(preload_place + 20, CELL_NAME))
            cell = preload_find(records, interior_name(preload_place + 20));
        tes3x_log_hex3("net.preload_cell", flags, (u32)cell, 0);
        return cell ? cell : preload_find(records, name);
    }
    if (!ref)
        return preload_find(records, name);
    copy(ref + REF_POSITION, preload_place + 4, 12);
    ((fn_exterior_change)TES3X_NET_EXTERIOR_CHANGE)(*(void **)TES3X_NET_DATA_HANDLER,
                                                    (const float *)(preload_place + 4));
    tes3x_log_hex3("net.preload_cell", flags, 0, 0);
    return 0;
}

static int call_retarget(u8 *site, void *to, void **was)
{
    u32 cr0, flags;

    if (site[0] != 0xE8) {
        tes3x_log_hex3("net.call_site_unexpected", (u32)site, *(const u32 *)site, 0);
        return 0;
    }
    *was = site + 5 + *(const int *)(site + 1);
    flags = lock();
    __asm__ volatile("movl %%cr0, %0" : "=r"(cr0));
    __asm__ volatile("movl %0, %%cr0" : : "r"(cr0 & ~CR0_WP) : "memory");
    *(u32 *)(site + 1) = (u32)to - ((u32)site + 5);
    __asm__ volatile("movl %0, %%cr0" : : "r"(cr0) : "memory");
    unlock(flags);
    return 1;
}

static void preload_hook_install(void)
{
    if (!call_retarget((u8 *)TES3X_NET_PRELOAD_SITE, (void *)preload_cell,
                       (void **)&preload_read))
        return;
    arrival_start = ARRIVAL_HOOKED;
    if (game_loaded[0])
        call_retarget((u8 *)TES3X_NET_PRELOAD_FIND_SITE, (void *)preload_cell_find,
                      (void **)&preload_find);
}

/* A launch begun in CHARGEN_CELL that no server claims (none answers, or one keeping whatever the
 * console runs) goes on to the prison ship, as vanilla CharGen would have put it. */
static void arrival_frame(void)
{
    static u32 since, joined_at, timing, joined;
    u32 now = now_us();

    if (arrival_start != ARRIVAL_STARTED || !world_idle())
        return;
    if (cg_state != CG_IDLE || chooser_kind || load_wanted) {
        arrival_start = ARRIVAL_CLAIMED;
        return;
    }
    if (!timing)
        timing = 1, since = now;
    if (ses.state != SESSION_JOINED)
        joined = 0;
    else if (!joined)
        joined = 1, joined_at = now;
    if (joined ? now - joined_at < ARRIVAL_ANSWER_US : now - since < ARRIVAL_JOIN_US)
        return;
    arrival_start = ARRIVAL_SHIP;
    run_script("Player->PositionCell 61 -135 24 340 \"Imperial Prison Ship\"");
    run_script("ChangeWeather \"Bitter Coast Region\" 1");
    tes3x_log("net.arrival_ship", joined);
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
        if (arrival_start != ARRIVAL_STARTED && arrival_start != ARRIVAL_CLAIMED)
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
