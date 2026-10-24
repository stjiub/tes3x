"""Wire format, event kinds and authenticated packets."""

from tes3x.paths import resource
import hashlib
import hmac
import math
import struct
import time

PORT = 26500


AGENT_PORT = 26501


ADMIN_PORT = 26502


REMOTE_ADMIN_PORT = 26503


DNS_PORT = 53


DHCP_SERVER, DHCP_CLIENT = 67, 68


DHCP_MAGIC = bytes([0x63, 0x82, 0x53, 0x63])


GUEST_IP = "10.0.2.15"


MAGIC = b"TES3XNET"


PING, PONG = b"TES3XPNG", b"TES3XPON"


PEER_MAC = bytes.fromhex("020000000001")


PEER_IP = "10.0.2.2"


BROADCAST = b"\xff" * 6


def wire_text(data):
    """Text a client sent, safe to print and store: control characters become '?'."""
    return "".join(c if " " <= c < "\x7f" or c >= "\xa0" else "?" for c in data.decode("latin-1"))


LOG_LEVELS = ("normal", "verbose")


CAIUS_PACKAGE = "bk_a1_1_caiuspackage"


SPYMASTER_QUEST, SPYMASTER_GIVEN, SPYMASTER_DONE = "a1_1_findspymaster", 1, 10


T3MP = struct.Struct("<4sBBHIIIII")  # magic, version, type, 0, session, seq, ack, time, echo


T3MP_VERSION = 23


MANAGER_VERSION = 1  # build discovery stays independent of gameplay state


HELLO, WELCOME, HEARTBEAT, BYE, STATE, PEER, GONE, EVENTS, REFUSE, CLOCK, ACTORS = range(1, 12)


BUILD = 12  # to a Dash's HELLO: tes3x_netbuild.BUILD_BODY, then the server forgets it


# On the wire every packet but the handshake is SEALED: OUTER in the clear (the AEAD's associated
# data), then INNER and the body sealed under the session key with seq as the nonce.
HANDSHAKE1, HANDSHAKE2, HANDSHAKE3, SEALED = range(20, 24)


OUTER = struct.Struct("<4sBBHII")  # magic, version, type, 0, session, seq


INNER = struct.Struct("<B3xIII")  # type, ack, time, echo


PROLOGUE = b"TES3X T3MP 11"


HANDSHAKE_PAD = 128  # a HANDSHAKE1 is at least as large as the HANDSHAKE2 it draws


HANDSHAKE_KEEP = 10.0  # seconds a handshake is kept to answer its resent messages


# A HANDSHAKE1 costs the server four X25519 before the client has proved anything: per source
# address and in all, (burst, per second); and at most HANDSHAKES_PENDING kept at once.
HANDSHAKE_RATE = (10, 5.0)


HANDSHAKE_RATE_ALL = (200, 100.0)


HANDSHAKES_PENDING = 1024


CLIENT_RATE = (600, 600.0)  # sealed packets from one joined client; a console sends about 60/s


# GameHour, Day, Month (0-11), Year, DaysPassed, TimeScale, as the game's float globals
CLOCK_BODY = struct.Struct("<6f")


# MAC, build id, load order hash, plugin count, the client's clock, then its manifest's build id,
# the published revision the Dash installed and its local patch selection's BLAKE2b (each zero
# without one); NetPassword follows. A Dash's HELLO carries its content identity (an Ed25519 public
# key) in place of the revision.
# LOBBY in the plugin count: a console at the main menu, with no game and so no clock. It gets no
# world, only what picks a character (GAME, CHARS or NEWCHAR, PICK, the checkpoint and LOAD).
HELLO_BODY = struct.Struct("<6sIII" + CLOCK_BODY.format[1:] + "32s32s32s")


LOBBY = 0x80000000


# MANAGER in the plugin count: the Dash, asking for this server's build, not joining
MANAGER = 0x40000000


PASSWORD_MAX = 64


PASSWORD_RATE = (5, 1 / 60)  # tries per IPv4 address or IPv6 /64, (burst, per second)


# REFUSE: the session's load order hash, its plugin count, and why
REFUSE_BODY = struct.Struct("<III")


REFUSED_LOAD_ORDER, REFUSED_FULL, REFUSED_PASSWORD, REFUSED_KICKED, REFUSED_BANNED = 1, 2, 3, 4, 5


REFUSED_STALE = 6  # the console's build or revision is not the world's: its Dash can update it


REFUSED_PROTOCOL = 7  # expected version, offered version, reason (instead of load order/count)


BAN_KINDS = ("key", "mac", "address")


MONTH_DAYS = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


IDLE_TIMEOUT = 20.0  # silence after which a client is dropped; the console gives up at 15 s


ANNOUNCE_WAIT = 6.0  # how long a join waits for the character's name before using a number


EVENTS_HEAD = struct.Struct("<IB3x")  # the sender's last delivered event, event count


EVENT = struct.Struct("<IHHI")  # seq, kind, length, origin client; the data follows


EVENTS_BYTES = 512  # the client's largest EVENTS body


EVENT_DATA = 80


EVENT_TEXT = 1
# A console's TEXT is chat for every player. One starting with TEXT_PRIVATE is NAME, a zero, then
# the text, for one player, or for the server's admin when NAME is `admin`. The server sends
# every line to consoles as its own notice.
TEXT_PRIVATE = 1


EVENT_WELCOME = 35  # the --welcome text, once per join as a character, shown in a box


EVENT_IDENTITY = 33  # part, two parts, female, then two ids/text values ending in zero


EVENT_ACTOR_EQUIPMENT = 34  # actor id, part, parts, then equipped item ids ending in zero


EVENT_AUTHORITY, EVENT_HOLD, EVENT_HOLD_BROKEN, EVENT_HIT, EVENT_DEATH = 2, 3, 4, 5, 6


EVENT_EQUIPMENT = 7  # part, parts, then item ids each ending in a zero


EVENT_WEATHER = 8  # flags, count, then (region index u16, weather u8) each


EVENT_PLAYER_HIT = 9  # attacker refid (0: a player), victim client, health, fatigue


EVENT_SPELL = 10  # SPELL, then the spell id ending in a zero


EVENT_CAST = 11  # as EVENT_SPELL, sent to every other client; the target may be empty


EVENT_SHOT = 12  # SHOT, then the ammunition id ending in a zero


EVENT_OBJECTS = 13  # count, then OBJECT records


# A data-file reference's shared state: refid, its cell's index in the cells list (the same under
# one load order), state bits, lock level.
OBJECT = struct.Struct("<IHBB")


OBJECT_DISABLED, OBJECT_DELETED, OBJECT_LOCK, OBJECT_LOCKED = 1, 2, 4, 8


OBJECTS_PER_EVENT = (EVENT_DATA - 1) // OBJECT.size


EVENT_SPAWN = 14  # SPAWN, then the base object's id ending in a zero


EVENT_REMOVE = 15  # count, then spawn ids


# A reference made at run time: its id (from its maker a token, which the server replaces), cell
# index, stack count with SPAWN_REMOVED once removed and SPAWN_DATA if it has item data, position,
# orientation, and the item data's condition (uses, time left) and charge, raw: an int or a float
# by the item's type. With SPAWN_LEVELED it is a leveled creature, and its placeholder's refid
# follows before the id; SPAWN_SUMMON marks a summon, run by the client that made it (the event's
# origin).
SPAWN = struct.Struct("<IHH6fII")


SPAWN_REMOVED, SPAWN_DATA, SPAWN_LEVELED, SPAWN_SUMMON = 0x8000, 0x4000, 0x2000, 0x1000


SPAWN_COUNT = 0x0FFF


SPAWN_IDS = 0xFF000000  # never a data-file refid: mod index 0xFF


SPAWN_TWIN = 16.0  # units: a script's reference made on two consoles at once


SPAWN_TWIN_SECONDS = 2.0


EVENT_CONTENTS = 16  # CONTENTS_HEAD, then entries


EVENT_WANT = 17  # count, then cell indices u16: the containers of those cells are wanted


EVENT_AFFECT = 18  # actor id, effect index u8, then the spell id ending in a zero


EVENT_STATUS = 19  # STATUS: the latest per actor is kept and replayed


# count, then (actor id, client) pairs: who runs an actor instead of its cell's authority; client 0
# hands it back to the cell's authority
EVENT_OWNERS = 20


OWNER_PAIR = struct.Struct("<II")


OWNERS_PER_EVENT = (EVENT_DATA - 4) // OWNER_PAIR.size


# u8: BUSY_SAVING while the client's game thread is held by a save, 0 once it is back. A busy
# client keeps its session but gives up its cells and actors to any other player loading them.
EVENT_BUSY = 21


BUSY_SAVING = 1


EVENT_SAVE = 22  # request a state flush; byte 1 requests a diagnostic save upload


EVENT_SNAPSHOT = 36  # token u32 + STATE_BODY; reply token u32 after durable storage


EVENT_BOUNTY = 30  # the player's bounty, i32: the latest of each client is kept and replayed


# Script locals of data-file references: refid, count, then LOCAL (slot: kind << 14 | index, the
# raw value; a short sign-extended). The cell's authority sends changes, any console those of a
# reference its player's dialogue ran on. The server keeps the latest of each, relays them and
# replays all after WELCOME.
EVENT_LOCALS = 37
LOCAL = struct.Struct("<HI")
LOCAL_SHORT, LOCAL_LONG, LOCAL_FLOAT = 0, 1, 2
LOCAL_INDEX = 0x3FFF
LOCALS_PER_EVENT = (EVENT_DATA - 5) // LOCAL.size
LOCALS_MAX = 16384  # values kept in all


# To a cell's new authority, before its AUTHORITY, when nobody had the cell: count, then the kept
# state of each actor last reported in it (refid, x, y, z, heading, health, magicka, fatigue,
# ACTOR flags). The console places each one the first frame it runs it.
EVENT_ACTOR_PLACE = 38
ACTOR_PLACE = struct.Struct("<I7fI")
ACTOR_PLACES_PER_EVENT = (EVENT_DATA - 4) // ACTOR_PLACE.size
ACTORS_KEPT_MAX = 8192


# An actor a script moved to another cell, or that a player's dialogue result moved: refid, the
# cell index it left (the server keeps the first, where the data files put it), the cell index it
# is in, position and the rotation relocateReference was given (Position and PositionCell pass
# radians). The server keeps the latest per actor, relays it and replays it after WELCOME; each
# console moves the actor once it finds it.
EVENT_ACTOR_MOVE = 39
ACTOR_MOVE = struct.Struct("<IHH4f")
ACTOR_MOVES_MAX = 4096
NO_CELL = 0xFFFF


# An AI package a player's dialogue result or console, or a world script, gave a data-file actor:
# refid, config type, target kind, the escort or follow cell index (NO_CELL for none), a client,
# the config's bytes from +4 (destination, or wander range, duration, time and idles), reset,
# then the target actor's id ending in zero. A console names its own player as AI_TARGET_PLAYER
# and another's ghost as AI_TARGET_CLIENT. The server keeps the latest package of each actor and a
# player target by character; it sends AI_TARGET_PLAYER to that character's console and
# AI_TARGET_CLIENT with its client to the others, and replays every package before READY.
EVENT_AI_PACKAGE = 40
AI_PACKAGE = struct.Struct("<IBBHI16sB")
AI_TRAVEL, AI_WANDER, AI_ESCORT, AI_FOLLOW = 1, 2, 3, 4
AI_TARGET_NONE, AI_TARGET_PLAYER, AI_TARGET_CLIENT, AI_TARGET_OBJECT = 0, 1, 2, 3
AI_ID_MAX = 31
AI_NO_DESTINATION = struct.unpack("<f", struct.pack("<I", 0x7F7FFFFF))[0]  # AIFollow's FLT_MAX
AI_PACKAGES_MAX = 4096


def pack_ai_package(refid, kind, target, cell, client, config, reset, ident=""):
    return AI_PACKAGE.pack(refid, kind, target, cell, client, config, reset) + zstr(ident)


def unpack_ai_package(data):
    """(refid, kind, target, cell, client, config, reset, id); ValueError if malformed."""
    if not AI_PACKAGE.size < len(data) <= AI_PACKAGE.size + AI_ID_MAX + 1 or data[-1] \
            or data.count(0, AI_PACKAGE.size) != 1:
        raise ValueError("AI package length or id")
    refid, kind, target, cell, client, config, reset = AI_PACKAGE.unpack_from(data)
    ident = data[AI_PACKAGE.size:-1].decode("latin-1")
    if refid < 0x01000000 or kind not in (AI_TRAVEL, AI_WANDER, AI_ESCORT, AI_FOLLOW):
        raise ValueError("AI package actor or type")
    if (target != AI_TARGET_NONE) != (kind >= AI_ESCORT) or target > AI_TARGET_OBJECT \
            or (target == AI_TARGET_OBJECT) != bool(ident) or reset > 1:
        raise ValueError("AI package target")
    if kind != AI_WANDER and not finite(*struct.unpack_from("<3f", config)):
        raise ValueError("AI package destination")
    return refid, kind, target, cell, client, config, reset, ident


# From a client after each WELCOME: its launch token (new each title launch), then the name of the
# save that launch loaded, or "". A console not running its character's latest checkpoint is sent
# the checkpoint as CHECKPOINT_NAME and LOAD (to a client: load that file once it has it).
EVENT_GAME, EVENT_LOAD = 23, 24


# The player's own state, per character and never relayed: a sub-kind, then PLAYER_ITEMS (part,
# parts, item id, then entries as in CONTENTS: every stack of that item, none once it is gone),
# PLAYER_LEVEL (LEVEL), PLAYER_SKILLS (count, then SKILL each), PLAYER_MODIFIERS (count, then
# MODIFIER each), PLAYER_JOURNAL (count, then (index u16, quest id) each) or PLAYER_VITALS
# (VITALS). To a client only: PLAYER_PLACE (a
# STATE_BODY: where the player last was, which the server takes from STATE), PLAYER_SPELLS (mode,
# part, parts, ids ending in zero), the kept state, then PLAYER_READY (0: the console sends all of
# its state, so a stream kept from before a field was streamed fills in; 1, which the console still
# takes as "already sent", is no longer used). The console sends nothing before READY.
EVENT_PLAYER = 25


PLAYER_ITEMS, PLAYER_LEVEL, PLAYER_SKILLS, PLAYER_JOURNAL, PLAYER_READY = 1, 2, 3, 4, 5


PLAYER_VITALS, PLAYER_PLACE = 6, 7


# Death while joined: the console sends PLAYER_DEATH instead of offering its last save, is answered
# PLAYER_RESPAWN (RESPAWN: delay in ms, RESPAWN_*, gold to lose) and sends PLAYER_ALIVE once
# resurrected at the closest marker. The server relays DEATH and ALIVE to the other players so
# their ghosts fall and rise. A character that died and was not back is respawned again after the
# replay of its next launch.
PLAYER_DEATH, PLAYER_RESPAWN, PLAYER_ALIVE = 8, 9, 10


PLAYER_SPELLS = 11
PLAYER_TOPICS = 17  # count, then learned topic names ending in zero; additions only
TOPIC_NAME_MAX = 63
PLAYER_TOPICS_MAX = 4096


# The player's faction state, both ways: count, then FACTION entries, each followed by the faction id
# ending in zero. The low two bits are the membership flags (FACTION_JOINED, FACTION_EXPELLED);
# FACTION_RANK, FACTION_REPUTATION and FACTION_FLAGS say which values a receiver applies. A
# console always sends all three, absolute. Rank and reputation are the world's; membership and
# expulsion are each character's.
PLAYER_FACTIONS = 18
FACTION = struct.Struct("<Bii")
FACTION_JOINED, FACTION_EXPELLED = 1, 2
FACTION_RANK, FACTION_REPUTATION, FACTION_FLAGS = 0x10, 0x20, 0x40
FACTION_ALL = FACTION_RANK | FACTION_REPUTATION | FACTION_FLAGS
FACTION_ID_MAX = 31
FACTIONS_MAX = 512


# The character's own globals (PC* and a few vanilla per-player ones; the console chooses), both
# ways: count, then each value (f32) and the global's name ending in zero. Absolute; a console
# sends what changed since its last scan, all of them first after READY.
PLAYER_GLOBALS = 19
GLOBAL_NAME_MAX = 31
PLAYER_GLOBALS_MAX = 128


# The world's globals, every one neither the character's nor derived (the console chooses), in
# PLAYER_GLOBALS's form. A console sends what changed since READY; the server keeps the latest of
# each, relays it to the other players and replays them all before READY and in the character file.
WORLD_GLOBALS = 20
WORLD_GLOBALS_MAX = 1024


# The world's scripts: global scripts a console started after its first READY of a launch (a
# dialogue result, a quest script), not the data files' start scripts. Op, the script's reference
# (refid; 0 none, WORLD_SCRIPT_PLAYER the runner's own player), the script id ending in zero,
# count, then LOCAL entries. To the server: SCRIPT_RUNNING, SCRIPT_STOPPED, or SCRIPT_VALUES with
# the locals that changed. From it: SCRIPT_VALUES, then SCRIPT_RUNNING: run it with those locals.
WORLD_SCRIPTS = 21
SCRIPT_RUNNING, SCRIPT_STOPPED, SCRIPT_VALUES = 1, 2, 3
WORLD_SCRIPT_PLAYER = 1
WORLD_SCRIPTS_MAX = 512
SCRIPT_VALUES_MAX = 64
SCRIPT_ID_MAX = 31


PLAYER_BOUNTY = 12  # from the server: the character's last streamed crime bounty, i32


# Who the character is, both ways: part, parts, then a slice of one body (IDENTITY_STATS: female,
# then the class's two attributes, specialisation and ten skills; then name, race, head, hair,
# birthsign, class id and class name, each ending in zero; the birthsign may be empty). The
# server keeps the latest and replays it first, so a launch running another character becomes
# this one.
PLAYER_IDENTITY = 13


# What the character wears, both ways: part, parts, then a slice of one body of WORN entries (flags,
# condition and charge when flags has ENTRY_DATA, item id ending in zero), every equipped stack. The
# server keeps the latest and replays it after the items.
PLAYER_WORN = 14


PLAYER_EFFECTS = 16  # complete multipart active-effect snapshot


PLAYER_EFFECT_BYTES = 608


PLAYER_EFFECTS_MAX = 64


PLAYER_MODIFIERS = 15  # current attribute/skill values: count, then MODIFIER entries


IDENTITY_STATS = struct.Struct("<B13i")


IDENTITY_FIELDS = ("name", "race", "head", "hair", "birthsign", "class", "class_name")


SPELLS_SNAPSHOT, SPELLS_ADD, SPELLS_REMOVE = 0, 1, 2


RESPAWN = struct.Struct("<IBI")


RESPAWN_PLACES = {"temple": 0, "shrine": 1, "nearest": 2}


PLACE_HOLD = 15.0  # seconds a replayed place waits for the console to arrive before STATE counts


PLACE_NEAR = 512.0  # units: the console has arrived; also the move that marks the place to write


# Characters. GAME's name is followed by the launch's kind (GAME_NEW: a New Game). To a client:
# CHARS (part, parts, then the names of its key's characters each ending in a zero) to choose
# from, or NEWCHAR (the same with start point names) to make one; the console answers PICK
# (PICK_CHARACTER and an index into CHARS or PICK_NEW, or PICK_START and an index into NEWCHAR).
# The chosen start comes back as RUN events, one script line each, ended by an empty one.
EVENT_CHARS, EVENT_PICK, EVENT_NEWCHAR, EVENT_RUN = 26, 27, 28, 29


GAME_NONE, GAME_LOAD, GAME_NEW = 0, 1, 2


PICK_CHARACTER, PICK_START, PICK_NEW = 1, 2, 255


CHARACTERS_LISTED = 8  # buttons on the console's list, with "New character"


START_NAME = 31


STARTS = str(resource("examples", "starts.toml"))


# level, level progress, level-ups per attribute (8) and per specialisation (3), base health,
# magicka and fatigue, base attributes (8)
LEVEL = struct.Struct("<HH11B3f8f")


SKILL = struct.Struct("<Bff")  # skill, base, progress


MODIFIER = struct.Struct("<Bf")  # attribute 0..7, then skill 0..26, current value


VITALS = struct.Struct("<3f")  # current health, magicka, fatigue


ATTRIBUTE_NAMES = ("Strength", "Intelligence", "Willpower", "Agility", "Speed", "Endurance",
                   "Personality", "Luck")


SKILL_NAMES = ("Block", "Armorer", "MediumArmor", "HeavyArmor", "BluntWeapon", "LongBlade", "Axe",
               "Spear", "Athletics", "Enchant", "Destruction", "Alteration", "Illusion",
               "Conjuration", "Mysticism", "Restoration", "Alchemy", "Unarmored", "Security",
               "Sneak", "Acrobatics", "LightArmor", "ShortBlade", "Marksman", "Mercantile",
               "Speechcraft", "HandToHand")


QUEST_INDICES = 32  # a quest's latest indices kept for the replay
JOURNAL_QUESTS_MAX = 4096  # quests a world's journal keeps, as many as a console tracks


STREAM_NAME = "stream.json"


# actor id, fight, flee, alarm, hello, base disposition (NO_DISPOSITION for a creature)
STATUS = struct.Struct("<I5h")


NO_DISPOSITION = -32768


# A container's contents in parts: refid, cell index, part, parts, flags (CONTENTS_ROLLED: the
# console's first reading of its instance). An entry: count, flags (ENTRY_DATA: its condition and
# charge follow, raw), then the item's id ending in a zero.
CONTENTS_HEAD = struct.Struct("<IHBBB")


CONTENTS_ROLLED = 1


ENTRY = struct.Struct("<iB")


ENTRY_DATA = 1


SHOT = struct.Struct("<IffB")  # firer refid, the two shot values, firer is a player


# caster refid, target client, target refid (0: its player), source type, caster is a player
SPELL = struct.Struct("<IIIBB")


SOURCE_SPELL = 1


WEATHER_OFFER = 1  # a joining client's whole table: the server keeps regions it does not know


WEATHER_ENTRY = struct.Struct("<HB")


WEATHER_PER_EVENT = (EVENT_DATA - 2) // WEATHER_ENTRY.size


WEATHERS = ("clear", "cloudy", "foggy", "overcast", "rain", "thunder", "ash", "blight", "snow",
            "blizzard")


# refid, target client, then a word: on, reason, or the damage as a float
TARGETED = {EVENT_HOLD: "holds", EVENT_HOLD_BROKEN: "breaks the hold on", EVENT_HIT: "hits",
            EVENT_PLAYER_HIT: "hits the player of"}


# From the server after each join: the rules a console follows, one byte of flags.
EVENT_RULES = 41
RULE_AUTO_LEVEL = 1  # a character levels up as soon as the level is earned, without resting


# The shared wait and rest. A console sends REST_OPEN or REST_CLOSE as its rest menu opens or goes
# (the player agrees to a rest by opening it), and REST_ASK with the hours when the player presses
# Rest, Wait or Until Healed. When every console in the world has the menu open and a rest has been
# asked for, the server moves its clock on by the hours and sends REST_START to each, which runs
# its own wait or rest for those hours.
EVENT_REST = 42
REST_OPEN, REST_CLOSE, REST_ASK, REST_START = 0, 1, 2, 3
REST_HOURS_MAX = 24 * 7


# What an actor's mobile holds beyond ACTOR: refid, count, then ACTOR_STAT (slot, raw value). Slots
# 0-7 attribute base, 8-15 attribute current (f32), 16 barter gold (i32), 17 corpse hourstamp,
# 18-44 skill base (f32, NPCs). The cell's authority sends a change; any console sends barter
# gold, since the player trading may follow the merchant. The server keeps the latest of each,
# relays it and replays all after WELCOME; each console applies them to the actor once loaded.
EVENT_ACTOR_STATS = 44
ACTOR_STAT = struct.Struct("<HI")
ACTOR_STAT_SLOTS = 45
ACTOR_STAT_GOLD = 16
ACTOR_STATS_PER_EVENT = (EVENT_DATA - 5) // ACTOR_STAT.size
ACTOR_STATS_MAX = 16384  # values kept in all


# An actor's active effects, complete: refid, part u16, parts u16, then the part of the body, whole
# PLAYER_EFFECT_BYTES records (none when the actor has none left). The cell's authority sends a
# set when it changes and now and then while one is running; the server keeps the latest set per
# actor, relays it and replays all after WELCOME.
EVENT_ACTOR_EFFECTS = 47
ACTOR_EFFECTS_HEADER = struct.Struct("<IHH")
ACTOR_EFFECT_CHUNK = EVENT_DATA - ACTOR_EFFECTS_HEADER.size
ACTOR_EFFECTS_MAX = 6  # effects per actor
ACTOR_EFFECT_ACTORS = 24  # actors kept


# A death the cell's authority saw, counted by the engine's kill counter under the dead actor's
# base object: refid, then that object's id (31 characters at most) and a zero. The server keeps
# one per refid and answers every client, the sender too, with EVENT_KILLS: the id and the count
# of kept deaths under it, which each console writes into its own counter (GetDeadCount). All
# counts are replayed after WELCOME.
EVENT_KILL = 45
EVENT_KILLS = 46  # count u32, then the id and a zero; only the server sends it
KILL_ID_MAX = 31
KILLS_MAX = 8192  # deaths kept


# A record made in play: a custom spell, whose id the engine rolled on the console that made it.
# One record is a blob sent in parts of [part, parts, chunk]: RECORD_HEAD (type, cast type, magicka
# cost, flags, effect count, 0), the id and the name each ending in a zero, then the effects as the
# engine keeps them (RECORD_EFFECT bytes each). A console sends its record to the server, which
# keeps it in the world file and sends it whole to every other console, and to each at join before
# its character's items and spells, so a spell resolves by id everywhere.
EVENT_RECORD = 43
RECORD_SPELL = 1
RECORD_HEAD = struct.Struct("<BBHIBx")
RECORD_EFFECT = 24
RECORD_EFFECTS_MAX = 8
RECORD_ID_MAX = 31
RECORD_NAME_MAX = 31
RECORD_CHUNK = 78
RECORD_PARTS_MAX = 4
RECORDS_MAX = 4096


def unpack_record(blob):
    """(type, cast type, cost, flags, id, name, effects); ValueError if malformed."""
    blob = bytes(blob)
    if len(blob) <= RECORD_HEAD.size:
        raise ValueError("record length")
    kind, cast, cost, flags, count = RECORD_HEAD.unpack_from(blob)
    if kind != RECORD_SPELL or cast > 5 or flags > 7 or count > RECORD_EFFECTS_MAX:
        raise ValueError("record type")
    end = blob.find(b"\0", RECORD_HEAD.size)
    name_end = blob.find(b"\0", end + 1) if end >= 0 else -1
    if end < 0 or name_end < 0:
        raise ValueError("record text")
    ident, name = blob[RECORD_HEAD.size:end], blob[end + 1:name_end]
    if (not 0 < len(ident) <= RECORD_ID_MAX or len(name) > RECORD_NAME_MAX
            or any(b < 32 or b == 127 or b == 34 for b in ident)
            or any(b < 32 or b == 127 for b in name)):
        raise ValueError("record text")
    effects = blob[name_end + 1:]
    if len(effects) != count * RECORD_EFFECT or not count:
        raise ValueError("record effects")
    for off in range(0, len(effects), RECORD_EFFECT):
        effect, skill, attribute, rng = struct.unpack_from("<hbbI", effects, off)
        if (not 0 <= effect <= 142 or not -1 <= skill < len(SKILL_NAMES)
                or not -1 <= attribute < len(ATTRIBUTE_NAMES) or rng > 2):
            raise ValueError("record effect")
        if any(v > 10000000 for v in struct.unpack_from("<4I", effects, off + 8)):
            raise ValueError("record effect values")
        low, high = struct.unpack_from("<II", effects, off + 16)
        if low > high or high > 10000000:
            raise ValueError("record effect values")
    return (kind, cast, cost, flags, ident.decode("latin-1"), name.decode("latin-1"), effects)


def pack_record(cast, cost, flags, ident, name, effects):
    """The blob of a spell record."""
    blob = (RECORD_HEAD.pack(RECORD_SPELL, cast, cost, flags, len(effects) // RECORD_EFFECT)
            + zstr(ident) + zstr(name) + effects)
    unpack_record(blob)
    return blob


def record_parts(blob):
    """A record's EVENT_RECORD payloads."""
    chunks = [blob[i:i + RECORD_CHUNK] for i in range(0, len(blob), RECORD_CHUNK)]
    return [bytes([n, len(chunks)]) + chunk for n, chunk in enumerate(chunks)]


# Events only the server sends. A console acts on them, so one from a client is never relayed.
SERVER_EVENTS = {EVENT_WELCOME, EVENT_AUTHORITY, EVENT_OWNERS, EVENT_SAVE, EVENT_LOAD,
                 EVENT_CHARS, EVENT_NEWCHAR, EVENT_RUN, EVENT_ACTOR_PLACE, EVENT_RULES,
                 EVENT_KILLS}


# What an event handler returns: whether the event goes on to the other clients.
RELAY, KEEP = True, False


KEY = struct.Struct("<Iii32s")  # kind, grid x, grid y, interior name


KEY_EXTERIOR, KEY_INTERIOR = 1, 2


ANIM_BYTES = 20  # per layer: 3 groups, pad, 3 keys, pad, 3 times (tes3xnet.c anim_capture)


NO_ANIM = b"\xff\xff\xff" + bytes(ANIM_BYTES - 3)  # no group on any layer: the ghost idles


# refid, x, y, z, heading, health, flags, magicka, fatigue, combat target, animation. The target is
# a client id for a player (ghosts included), else an actor id; 0 for none.
ACTOR = struct.Struct(f"<I5fI2fI{ANIM_BYTES}s")


ACTORS_PER_PACKET = 8


ACTOR_PERIOD = 0.1


ACTOR_DEAD, ACTOR_IN_COMBAT = 1, 2


AUTHORITY_PERIOD = 0.25


# An actor goes to a nearer player only when that player is this much nearer than its owner and
# the owner has had it this long, so two players at about the same distance do not trade it. One
# fighting a player goes to that player's console, after the same hold.
OWNER_MARGIN = 256


OWNER_HOLD = 2.0


OWNER_STALE = 5.0  # seconds without a state before an actor is no longer owned


RESEND = 0.25


# Least time between two EVENTS packets to one client, and at most PACE_PACKETS packets to one
# client per PACE_WINDOW seconds, the rest queued. xemu's NIC stops reading its tunnel for good once
# frames arrive faster than the guest's receive slots drain.
EVENTS_GAP = 0.02


PACE_PACKETS = 4


PACE_WINDOW = 0.005


CLOCK_INTERVAL = 1.0


# Bulk transfer: OFFER names the file; CHUNK carries id, index and BULK_CHUNK bytes; the receiver's
# BULK_ACK gives the next chunk it will write, a bitmap of the 32 after it that arrived, how many
# chunks past it may be in flight, and its status.
CHUNK, BULK_ACK = 16, 17


EVENT_OFFER = 32  # BULK_OFFER, then the file name ending in a zero


BULK_OFFER = struct.Struct("<II32s")  # id, size, BLAKE2b-256 of the file


BULK_ACK_BODY = struct.Struct("<5I")


BULK_CHUNK = 1024


BULK_NAME = 37  # with ".part", FATX's 42 characters


BULK_RESEND = 0.5


BULK_PROBE = 1.0


BULK_STATUS = ("idle", "opening", "receiving", "done", "bad hash", "refused", "failed",
               "no space")


BULK_RECEIVING, BULK_DONE, BULK_BAD_HASH, BULK_REFUSED, BULK_FAILED = 2, 3, 4, 5, 6


BULK_NO_SPACE = 7  # the console's drive cannot take the file and a margin


BULK_WINDOW_IN = 8  # chunks a console keeps in flight to the server: its send slots


BULK_ACK_EVERY = 0.25  # seconds between acks to a console that is sending


UPLOAD_FILES = 64  # files one console key may keep in its uploads folder


CHARACTER_BACKUPS = 3  # earlier versions kept beside each character's save


CHECKPOINT_NAME = "char-{:08x}.ess"  # by the first four bytes of its BLAKE2b


# What a New Game writes into the player at its [PreLoad] read (serve --load-state): "T3MC", then
# PLAYER events (identity, items, worn, place) each behind a u16 length.
CHARACTER_FILE = "char-{:08x}.t3c"


CHARACTER_MAGIC = b"T3MC"


BULK_MAX = 16 << 20  # as the console's


# Names Windows opens as devices, whatever the extension
DEVICE_NAMES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(10)),
                *(f"LPT{i}" for i in range(10))}


def load_order_hash(names):
    """FNV-1a over plugin names in load order, lowercased and zero-terminated, as tes3xnet.c."""
    h = 2166136261
    for name in names:
        for byte in name.lower().encode("latin-1") + b"\0":
            h = ((h ^ byte) * 16777619) & 0xFFFFFFFF
    return h


def pack_events(ack, events, limit=EVENTS_BYTES):
    """An EVENTS body: ack, then as many (seq, kind, origin, data) as fit in limit, in order."""
    out = b""
    count = 0
    for seq, kind, origin, data in events:
        item = EVENT.pack(seq, kind, len(data), origin) + data
        if EVENTS_HEAD.size + len(out) + len(item) > limit or count == 255:
            break
        out += item
        count += 1
    return EVENTS_HEAD.pack(ack, count) + out


def pack_equipment(ids):
    """The EQUIPMENT events of one set, as tes3xnet.c equipment_send packs them."""
    parts = [b""]
    for item in ids:
        item = item.encode("latin-1")[:31] + b"\0"
        if 2 + len(parts[-1]) + len(item) > EVENT_DATA:
            parts.append(b"")
        parts[-1] += item
    return [bytes((i, len(parts))) + part for i, part in enumerate(parts)]


def unpack_equipment(part):
    return [wire_text(item) for item in part[2:].split(b"\0") if item]


def pack_identity(name, race, head, hair, female=False):
    """The two IDENTITY events sent for one character."""
    values = [value.encode() if isinstance(value, str) else value
              for value in (name, race, head, hair)]
    if any(not value or len(value) >= 32 or b"\0" in value for value in values):
        raise ValueError("identity values must be 1..31 bytes without a zero")
    return [bytes((part, 2, bool(female))) + b"\0".join(values[2 * part:2 * part + 2]) + b"\0"
            for part in range(2)]


def unpack_identity(part):
    """Return (part, female, first, second), or raise ValueError for malformed identity data."""
    if len(part) < 5 or part[0] > 1 or part[1] != 2:
        raise ValueError("bad identity header")
    values = part[3:].split(b"\0")
    if len(values) != 3 or values[-1] or any(not value or len(value) >= 32 for value in values[:2]):
        raise ValueError("bad identity strings")
    if any(byte < 0x20 for value in values[:2] for byte in value):
        raise ValueError("bad identity character")
    return part[0], bool(part[2]), *(wire_text(value) for value in values[:2])


def pack_player_identity(identity):
    """The PLAYER_IDENTITY events for a kept identity."""
    body = IDENTITY_STATS.pack(bool(identity["female"]), *identity["class_attributes"],
                               identity["specialization"], *identity["class_skills"])
    for field in IDENTITY_FIELDS:
        value = identity[field].encode("latin-1", "replace")
        if len(value) >= 32 or b"\0" in value or (not value and field != "birthsign"):
            raise ValueError(f"identity {field} must be 1..31 bytes without a zero")
        body += value + b"\0"
    per = EVENT_DATA - 3
    chunks = [body[i:i + per] for i in range(0, len(body), per)]
    return [bytes([PLAYER_IDENTITY, i, len(chunks)]) + chunk for i, chunk in enumerate(chunks)]


def unpack_player_identity(body):
    """A whole PLAYER_IDENTITY body as a dict, or raise ValueError."""
    if len(body) < IDENTITY_STATS.size:
        raise ValueError("identity too short")
    female, *stats = IDENTITY_STATS.unpack_from(body)
    values = body[IDENTITY_STATS.size:].split(b"\0")
    if len(values) != len(IDENTITY_FIELDS) + 1 or values[-1]:
        raise ValueError("bad identity strings")
    for field, value in zip(IDENTITY_FIELDS, values):
        if len(value) >= 32 or (not value and field != "birthsign") or \
                any(byte < 0x20 for byte in value):
            raise ValueError(f"bad identity {field}")
    identity = dict(zip(IDENTITY_FIELDS, (wire_text(value) for value in values)))
    identity.update(female=bool(female), class_attributes=stats[:2], specialization=stats[2],
                    class_skills=stats[3:])
    return identity


def describe_identity(identity):
    sex = "female" if identity["female"] else "male"
    sign = identity["birthsign"] or "no birthsign"
    return (f"is {identity['name']}: {sex} {identity['race']}, {identity['head']}, "
            f"{identity['hair']}, {identity['class_name']} ({identity['class']}), {sign}")


def pack_actor_equipment(refid, ids):
    """The ACTOR_EQUIPMENT events of one set."""
    parts = [bytearray(struct.pack("<I", refid) + b"\0\0")]
    for item in ids:
        item = item.encode() if isinstance(item, str) else item
        if not item or len(item) >= 32 or b"\0" in item:
            raise ValueError("equipment ids must be 1..31 bytes without a zero")
        if len(parts[-1]) + len(item) + 1 > EVENT_DATA:
            parts.append(bytearray(struct.pack("<I", refid) + b"\0\0"))
        parts[-1] += item + b"\0"
    for index, part in enumerate(parts):
        part[4:6] = bytes((index, len(parts)))
    return [bytes(part) for part in parts]


def unpack_actor_equipment(data):
    """Return (refid, part, parts, ids), or raise ValueError for malformed data."""
    if len(data) < 6:
        raise ValueError("short actor equipment")
    refid = struct.unpack_from("<I", data)[0]
    part, parts = data[4], data[5]
    if not refid or not parts or part >= parts:
        raise ValueError("bad actor equipment header")
    values = data[6:].split(b"\0")
    if values[-1] or any(not value or len(value) >= 32 for value in values[:-1]):
        raise ValueError("bad actor equipment id")
    if any(byte < 0x20 or byte == ord('"') for value in values[:-1] for byte in value):
        raise ValueError("bad actor equipment character")
    return refid, part, parts, [wire_text(value) for value in values[:-1]]


def pack_weather(entries, flags=0):
    """WEATHER events for {region index: weather}, as tes3xnet.c weather_frame packs them."""
    items = sorted(entries.items())
    return [bytes((flags, len(chunk))) + b"".join(WEATHER_ENTRY.pack(*e) for e in chunk)
            for chunk in (items[i:i + WEATHER_PER_EVENT]
                          for i in range(0, len(items), WEATHER_PER_EVENT))]


def pack_locals(refid, values):
    """LOCALS events for one reference's {slot: raw value}."""
    items = sorted(values.items())
    return [struct.pack("<IB", refid, len(chunk)) + b"".join(LOCAL.pack(*v) for v in chunk)
            for chunk in (items[i:i + LOCALS_PER_EVENT]
                          for i in range(0, len(items), LOCALS_PER_EVENT))]


def unpack_locals(data):
    """(refid, {slot: raw value}) of a LOCALS event; ValueError if malformed."""
    if len(data) < 5 or len(data) != 5 + data[4] * LOCAL.size or not data[4]:
        raise ValueError("bad locals event")
    values = dict(LOCAL.iter_unpack(data[5:]))
    if len(values) != data[4] or any(slot >> 14 > LOCAL_FLOAT for slot in values):
        raise ValueError("bad local slot")
    return struct.unpack_from("<I", data)[0], values


def pack_actor_stats(refid, values):
    """ACTOR_STATS events for one actor's {slot: raw value}."""
    items = sorted(values.items())
    return [struct.pack("<IB", refid, len(chunk)) + b"".join(ACTOR_STAT.pack(*v) for v in chunk)
            for chunk in (items[i:i + ACTOR_STATS_PER_EVENT]
                          for i in range(0, len(items), ACTOR_STATS_PER_EVENT))]


def unpack_actor_stats(data):
    """(refid, {slot: raw value}) of an ACTOR_STATS event; ValueError if malformed."""
    if len(data) < 5 or len(data) != 5 + data[4] * ACTOR_STAT.size or not data[4]:
        raise ValueError("bad actor stats event")
    values = dict(ACTOR_STAT.iter_unpack(data[5:]))
    if len(values) != data[4] or any(slot >= ACTOR_STAT_SLOTS for slot in values):
        raise ValueError("bad actor stat slot")
    refid = struct.unpack_from("<I", data)[0]
    if refid < 0x01000000:
        raise ValueError("bad actor stats id")
    return refid, values


def pack_actor_effects(refid, body):
    """ACTOR_EFFECTS events for one actor's complete, validated set of records."""
    unpack_actor_effects(body)
    parts = [body[i:i + ACTOR_EFFECT_CHUNK]
             for i in range(0, len(body), ACTOR_EFFECT_CHUNK)] or [b""]
    return [ACTOR_EFFECTS_HEADER.pack(refid, i, len(parts)) + p for i, p in enumerate(parts)]


def unpack_actor_effect_part(data):
    """(refid, part, parts, bytes) of an ACTOR_EFFECTS event; ValueError if malformed."""
    if len(data) < ACTOR_EFFECTS_HEADER.size:
        raise ValueError("bad actor effects event")
    refid, part, parts = ACTOR_EFFECTS_HEADER.unpack_from(data)
    body = bytes(data[ACTOR_EFFECTS_HEADER.size:])
    if (refid < 0x01000000 or not parts or part >= parts
            or parts > -(-PLAYER_EFFECT_BYTES * ACTOR_EFFECTS_MAX // ACTOR_EFFECT_CHUNK)
            or len(body) > ACTOR_EFFECT_CHUNK):
        raise ValueError("bad actor effects event")
    return refid, part, parts, body


def unpack_actor_effects(body):
    """The records of a complete set; ValueError unless every one is valid, as the player's are."""
    if len(body) > PLAYER_EFFECT_BYTES * ACTOR_EFFECTS_MAX:
        raise ValueError("too many actor effects")
    return unpack_player_effects(body)


def unpack_kill(data):
    """(refid, id) of a KILL event; ValueError if malformed."""
    if len(data) < 6 or len(data) > 4 + KILL_ID_MAX + 1 or data[-1] or data.count(0) != 1:
        raise ValueError("bad kill event")
    refid = struct.unpack_from("<I", data)[0]
    raw = data[4:-1]
    if refid < 0x01000000 or any(c < 32 or c == 127 for c in raw):
        raise ValueError("bad kill event")
    return refid, raw.decode("latin-1")


def pack_kills(name, count):
    return struct.pack("<I", count) + name.encode("latin-1") + b"\0"


def pack_world_script(op, refid, name, values=None):
    """WORLD_SCRIPTS events: one with no values, or as many as {slot: raw value} needs."""
    raw = name.encode("latin-1")
    if not raw or len(raw) > SCRIPT_ID_MAX or any(c < 32 or c in (34, 127) for c in raw):
        raise ValueError(f"bad script id {name!r}")
    head = struct.pack("<BBI", WORLD_SCRIPTS, op, refid) + raw + b"\0"
    per = (EVENT_DATA - len(head) - 1) // LOCAL.size
    items = sorted((values or {}).items())
    return [head + bytes([len(chunk)]) + b"".join(LOCAL.pack(*v) for v in chunk)
            for chunk in ([items[i:i + per] for i in range(0, len(items), per)] or [[]])]


def unpack_world_script(data):
    """(op, refid, script id, {slot: raw value}) of a WORLD_SCRIPTS event; ValueError if
    malformed."""
    if len(data) < 8 or data[0] != WORLD_SCRIPTS or data[1] not in (
            SCRIPT_RUNNING, SCRIPT_STOPPED, SCRIPT_VALUES):
        raise ValueError("not a world script event")
    end = data.find(b"\0", 6)
    raw = bytes(data[6:end]) if end > 6 else b""
    if (not raw or len(raw) > SCRIPT_ID_MAX or any(c < 32 or c in (34, 127) for c in raw)
            or end + 2 > len(data) or len(data) != end + 2 + data[end + 1] * LOCAL.size):
        raise ValueError("bad world script event")
    values = dict(LOCAL.iter_unpack(data[end + 2:]))
    if len(values) != data[end + 1] or any(slot >> 14 > LOCAL_FLOAT for slot in values):
        raise ValueError("bad world script local")
    return data[1], struct.unpack_from("<I", data, 2)[0], raw.decode("latin-1"), values


def describe_local(slot, value):
    kind, index = slot >> 14, slot & LOCAL_INDEX
    if kind == LOCAL_FLOAT:
        shown = f"{struct.unpack('<f', struct.pack('<I', value))[0]:g}"
    else:
        shown = str(struct.unpack("<i", struct.pack("<I", value))[0])
    return f"{'slf'[kind]}{index} {shown}"


def pack_objects(objects):
    """OBJECTS events for {refid: (cell, state, level)}."""
    items = sorted(objects.items())
    return [bytes([len(chunk)]) + b"".join(OBJECT.pack(refid, *rest) for refid, rest in chunk)
            for chunk in (items[i:i + OBJECTS_PER_EVENT]
                          for i in range(0, len(items), OBJECTS_PER_EVENT))]


def unpack_objects(data):
    """{refid: (cell, state, level)} of an OBJECTS event."""
    count = data[0] if data else 0
    return {refid: (cell, state, level) for refid, cell, state, level in
            (OBJECT.unpack_from(data, 1 + i * OBJECT.size) for i in range(count)
             if 1 + (i + 1) * OBJECT.size <= len(data))}


def describe_object(refid, cell, state, level):
    words = [name for bit, name in ((OBJECT_DISABLED, "disabled"), (OBJECT_DELETED, "taken"))
             if state & bit]
    if state & OBJECT_LOCK:
        words.append(f"locked {level}" if state & OBJECT_LOCKED else "unlocked")
    return f"{refid:#010x} in cell {cell}: {', '.join(words) or 'restored'}"


def pack_spawn(sid, spawn):
    """A SPAWN event for one reference made at run time."""
    leveled = spawn.get("leveled", 0)
    count = spawn["count"] | (SPAWN_REMOVED if spawn["removed"] else 0) | \
        (SPAWN_DATA if spawn.get("data") else 0) | (SPAWN_LEVELED if leveled else 0) | \
        (SPAWN_SUMMON if spawn.get("summon") else 0)
    return (SPAWN.pack(sid, spawn["cell"], count, *spawn["pos"], *spawn["rot"],
                       spawn.get("condition", 0), spawn.get("charge", 0))
            + (struct.pack("<I", leveled) if leveled else b"") + zstr(spawn["id"][:31]))


def unpack_spawn(data):
    """(sid, spawn) of a SPAWN event."""
    sid, cell, count, *place, condition, charge = SPAWN.unpack_from(data)
    off, leveled = SPAWN.size, 0
    if count & SPAWN_LEVELED:
        if len(data) < off + 4:
            raise ValueError("SPAWN too short for its placeholder")
        leveled = struct.unpack_from("<I", data, off)[0]
        off += 4
    name = wire_text(data[off:].split(b"\0")[0])
    return sid, {"cell": cell, "count": count & SPAWN_COUNT, "leveled": leveled,
                 "summon": bool(count & SPAWN_SUMMON),
                 "removed": bool(count & SPAWN_REMOVED), "pos": place[:3], "rot": place[3:],
                 "id": name, "data": bool(count & SPAWN_DATA), "condition": condition,
                 "charge": charge}


def pack_removes(sids):
    """REMOVE events for spawn ids, as tes3xnet.c spawns_frame packs them."""
    per = (EVENT_DATA - 1) // 4
    return [bytes([len(chunk)]) + struct.pack(f"<{len(chunk)}I", *chunk)
            for chunk in (sids[i:i + per] for i in range(0, len(sids), per))]


def unpack_removes(data):
    count = min(data[0], (len(data) - 1) // 4) if data else 0
    return list(struct.unpack_from(f"<{count}I", data, 1))


def pack_contents(refid, cell, entries, flags=0):
    """CONTENTS events for [id, count, flags, condition, charge] entries, as tes3xnet.c packs
    them."""
    parts = [b""]
    for name, count, entry_flags, condition, charge in entries:
        item = ENTRY.pack(count, entry_flags)
        if entry_flags & ENTRY_DATA:
            item += struct.pack("<II", condition, charge)
        item += zstr(name[:31])
        if CONTENTS_HEAD.size + len(parts[-1]) + len(item) > EVENT_DATA:
            parts.append(b"")
        parts[-1] += item
    return [CONTENTS_HEAD.pack(refid, cell, i, len(parts), flags) + part
            for i, part in enumerate(parts)]


def unpack_contents(data):
    """(refid, cell, part, parts, flags, entries) of one CONTENTS event."""
    refid, cell, part, parts, flags = CONTENTS_HEAD.unpack_from(data)
    off, entries = CONTENTS_HEAD.size, []
    while off + ENTRY.size < len(data):
        count, entry_flags = ENTRY.unpack_from(data, off)
        off += ENTRY.size
        condition = charge = 0
        if entry_flags & ENTRY_DATA:
            if off + 8 > len(data):
                raise ValueError("CONTENTS entry too short for its item data")
            condition, charge = struct.unpack_from("<II", data, off)
            off += 8
        end = data.index(b"\0", off) if b"\0" in data[off:] else len(data)
        entries.append([wire_text(data[off:end]), count, entry_flags, condition, charge])
        off = end + 1
    return refid, cell, part, parts, flags, entries


def describe_status(refid, values):
    fight, flee, alarm, hello, disposition = values
    base = "" if disposition == NO_DISPOSITION else f", disposition {disposition}"
    return (f"{refid:#010x} fight {fight}, flee {flee}, alarm {alarm}, hello {hello}{base}")


def describe_contents(entries):
    return ", ".join(f"{name} x{count}" + (f" (condition {condition:#x})" if flags else "")
                     for name, count, flags, condition, _ in entries) or "empty"


def describe_spawn(sid, spawn):
    x, y, z = spawn["pos"]
    what = f"{spawn['id']}" + (f" x{spawn['count']}" if spawn["count"] > 1 else "")
    if spawn.get("leveled"):
        what += f" for placeholder {spawn['leveled']:#010x}"
    if spawn.get("summon"):
        what += f" summoned by client {spawn.get('origin')}"
    if spawn.get("data"):
        what += f" (condition {spawn['condition']:#x}, charge {spawn['charge']:#x})"
    return (f"{sid:#010x} {what} in cell {spawn['cell']} at {x:.0f} {y:.0f} {z:.0f}"
            + (" (removed)" if spawn["removed"] else ""))


def unpack_weather(data):
    """(flags, {region index: weather}) of a WEATHER event."""
    flags, count = data[0], data[1]
    entries = {}
    for i in range(count):
        off = 2 + i * WEATHER_ENTRY.size
        if off + WEATHER_ENTRY.size > len(data):
            break
        index, weather = WEATHER_ENTRY.unpack_from(data, off)
        entries[index] = weather
    return flags, entries


def describe_weather(entries):
    return ", ".join(f"{i} {WEATHERS[w] if w < len(WEATHERS) else w}"
                     for i, w in sorted(entries.items()))


def unpack_events(body):
    """(ack, [(seq, kind, origin, data)]) of an EVENTS body; a truncated event ends the list."""
    ack, count = EVENTS_HEAD.unpack_from(body)
    off, events = EVENTS_HEAD.size, []
    for _ in range(count):
        if off + EVENT.size > len(body):
            break
        seq, kind, length, origin = EVENT.unpack_from(body, off)
        data = body[off + EVENT.size:off + EVENT.size + length]
        if len(data) < length:
            break
        events.append((seq, kind, origin, data))
        off += EVENT.size + length
    return ack, events


NOISE_PROTOCOL = b"Noise_XX_25519_ChaChaPoly_BLAKE2b"


NOISE_TAG = 16


def crypto():
    """The primitives the encrypted session needs beyond hashlib, from the cryptography package."""
    try:
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.asymmetric import x25519
        from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
    except ImportError:
        raise SystemExit("the session is encrypted: pip install cryptography") from None
    return InvalidTag, x25519, ChaCha20Poly1305


def aead_nonce(counter):
    return bytes(4) + struct.pack("<Q", counter)


def seal(key, counter, ad, data):
    """ChaCha20-Poly1305 (RFC 8439): the ciphertext, then the tag."""
    return crypto()[2](key).encrypt(aead_nonce(counter), data, ad)


def unseal(key, counter, ad, data):
    """The plaintext, or None if data is not authentic."""
    invalid, _, aead = crypto()
    try:
        return aead(key).decrypt(aead_nonce(counter), data, ad)
    except invalid:
        return None


def x25519_public(secret):
    from cryptography.hazmat.primitives import serialization
    return crypto()[1].X25519PrivateKey.from_private_bytes(secret).public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def fingerprint(public):
    return hashlib.blake2b(public, digest_size=16).hexdigest()


def plain_name(name):
    """A file name the console and FATX both take: letters, digits, ' .-_', not led by a dot."""
    return (0 < len(name) <= BULK_NAME and not name.startswith(".") and
            all(c.isascii() and (c.isalnum() or c in " .-_") for c in name))


def checkpoint_name(data, form=CHECKPOINT_NAME):
    return form.format(
        int.from_bytes(hashlib.blake2b(data, digest_size=32).digest()[:4], "big"))


def pack_names(names):
    """CHARS or NEWCHAR bodies: part, parts, then names each ending in a zero."""
    parts, body = [], b""
    for name in names:
        entry = name.encode("latin-1", "replace") + b"\0"
        if body and 2 + len(body) + len(entry) > EVENT_DATA:
            parts.append(body)
            body = b""
        body += entry
    parts.append(body)
    return [bytes([i, len(parts)]) + part for i, part in enumerate(parts)]


def pack_items(item, entries):
    """PLAYER_ITEMS parts for every stack of one item; an empty list says it is gone."""
    head = item.encode("latin-1") + b"\0"
    parts, body = [], b""
    for count, flags, condition, charge in entries:
        entry = ENTRY.pack(count, flags) + (struct.pack("<II", condition, charge)
                                            if flags & ENTRY_DATA else b"")
        if body and 3 + len(head) + len(body) + len(entry) > EVENT_DATA:
            parts.append(body)
            body = b""
        body += entry
    parts.append(body)
    return [bytes([PLAYER_ITEMS, i, len(parts)]) + head + part for i, part in enumerate(parts)]


def pack_worn(worn):
    """PLAYER_WORN parts for a kept list of [item id, flags, condition, charge]."""
    body = b"".join(bytes([flags & ENTRY_DATA]) +
                    (struct.pack("<II", condition, charge) if flags & ENTRY_DATA else b"") +
                    item.encode("latin-1", "replace") + b"\0"
                    for item, flags, condition, charge in worn)
    per = EVENT_DATA - 3
    chunks = [body[i:i + per] for i in range(0, len(body), per)] or [b""]
    return [bytes([PLAYER_WORN, i, len(chunks)]) + chunk for i, chunk in enumerate(chunks)]


def unpack_worn(body):
    """A whole PLAYER_WORN body as [item id, flags, condition, charge] entries, or raise
    ValueError."""
    worn, off = [], 0
    while off < len(body):
        flags = body[off] & ENTRY_DATA
        off += 1
        condition = charge = 0
        if flags:
            if off + 8 > len(body):
                raise ValueError("worn entry cut short")
            condition, charge = struct.unpack_from("<II", body, off)
            off += 8
        end = body.find(b"\0", off)
        if end <= off or end - off >= 32:
            raise ValueError("bad worn item id")
        worn.append([wire_text(body[off:end]), flags, condition, charge])
        off = end + 1
    return worn


def unpack_items(data):
    """(part, parts, item id, entries) of a PLAYER_ITEMS event."""
    part, parts = data[1], data[2]
    raw, _, rest = data[3:].partition(b"\0")
    entries, off = [], 0
    while off + ENTRY.size <= len(rest):
        count, flags = ENTRY.unpack_from(rest, off)
        off += ENTRY.size
        condition = charge = 0
        if flags & ENTRY_DATA:
            if off + 8 > len(rest):
                break
            condition, charge = struct.unpack_from("<II", rest, off)
            off += 8
        entries.append([count, flags & ENTRY_DATA, condition, charge])
    return part, parts, wire_text(raw), entries


def pack_topics(names):
    """Learned topics in bounded events; never truncate a dialogue name."""
    events, body, count = [], b"", 0
    if len(names) > PLAYER_TOPICS_MAX:
        raise ValueError("too many learned topics")
    for name in names:
        raw = name.encode("latin-1")
        if (not raw or len(raw) > TOPIC_NAME_MAX or
                any(c < 32 or c == 127 or c == 34 for c in raw)):
            raise ValueError("topic names must be 1..63 bytes without controls or quotes")
        if 2 + len(body) + len(raw) + 1 > EVENT_DATA:
            events.append(bytes([PLAYER_TOPICS, count]) + body)
            body, count = b"", 0
        body += raw + b"\0"
        count += 1
    if body or not events:
        events.append(bytes([PLAYER_TOPICS, count]) + body)
    return events


def pack_factions(entries):
    """PLAYER_FACTIONS events for (faction id, bits, rank, reputation) tuples."""
    events, body, count = [], b"", 0
    for faction, bits, rank, reputation in entries:
        raw = faction.encode("latin-1")
        if (not raw or len(raw) > FACTION_ID_MAX or any(c < 32 or c == 34 for c in raw)
                or bits & ~(FACTION_ALL | FACTION_JOINED | FACTION_EXPELLED)):
            raise ValueError(f"bad faction entry {faction!r}")
        entry = FACTION.pack(bits, rank, reputation) + raw + b"\0"
        if count and 2 + len(body) + len(entry) > EVENT_DATA:
            events.append(bytes([PLAYER_FACTIONS, count]) + body)
            body, count = b"", 0
        body += entry
        count += 1
    if count:
        events.append(bytes([PLAYER_FACTIONS, count]) + body)
    return events


def unpack_factions(data):
    """(faction id, bits, rank, reputation) from a PLAYER_FACTIONS event; ValueError if malformed."""
    if len(data) < 2 or data[0] != PLAYER_FACTIONS:
        raise ValueError("not a faction event")
    entries, off = [], 2
    for _ in range(data[1]):
        if off + FACTION.size >= len(data):
            raise ValueError("short faction entry")
        bits, rank, reputation = FACTION.unpack_from(data, off)
        end = data.find(b"\0", off + FACTION.size)
        raw = bytes(data[off + FACTION.size:end]) if end >= 0 else b""
        if (not raw or len(raw) > FACTION_ID_MAX or any(c < 32 or c == 34 for c in raw)
                or bits & ~(FACTION_ALL | FACTION_JOINED | FACTION_EXPELLED)):
            raise ValueError("bad faction entry")
        entries.append((raw.decode("latin-1"), bits, rank, reputation))
        off = end + 1
    if off != len(data):
        raise ValueError("trailing faction bytes")
    return entries


def pack_globals(entries, kind=PLAYER_GLOBALS):
    """PLAYER_GLOBALS (or WORLD_GLOBALS) events for (name, value) pairs."""
    events, body, count = [], b"", 0
    for name, value in entries:
        raw = name.encode("latin-1")
        if not raw or len(raw) > GLOBAL_NAME_MAX or any(c < 32 or c == 34 for c in raw):
            raise ValueError(f"bad global {name!r}")
        entry = struct.pack("<f", value) + raw + b"\0"
        if count and 2 + len(body) + len(entry) > EVENT_DATA:
            events.append(bytes([kind, count]) + body)
            body, count = b"", 0
        body += entry
        count += 1
    if count:
        events.append(bytes([kind, count]) + body)
    return events


def unpack_globals(data, kind=PLAYER_GLOBALS):
    """(name, value) pairs from a PLAYER_GLOBALS (or WORLD_GLOBALS) event; ValueError if
    malformed."""
    if len(data) < 2 or data[0] != kind:
        raise ValueError("not a globals event")
    entries, off = [], 2
    for _ in range(data[1]):
        if off + 4 >= len(data):
            raise ValueError("short global entry")
        value, = struct.unpack_from("<f", data, off)
        end = data.find(b"\0", off + 4)
        raw = bytes(data[off + 4:end]) if end >= 0 else b""
        if (not raw or len(raw) > GLOBAL_NAME_MAX or any(c < 32 or c == 34 for c in raw)
                or not finite(value)):
            raise ValueError("bad global entry")
        entries.append((raw.decode("latin-1"), value))
        off = end + 1
    if off != len(data):
        raise ValueError("trailing global bytes")
    return entries


def describe_faction(faction, rank, reputation, flags):
    return (f"{faction} rank {rank} reputation {reputation}"
            + (" member" if flags & FACTION_JOINED else "")
            + (" expelled" if flags & FACTION_EXPELLED else ""))


def pack_journal(quests):
    """PLAYER_JOURNAL events for (quest id, index) pairs, in order."""
    events, body, count = [], b"", 0
    for quest, index in quests:
        entry = struct.pack("<H", index) + quest.encode("latin-1") + b"\0"
        if count and 2 + len(body) + len(entry) > EVENT_DATA:
            events.append(bytes([PLAYER_JOURNAL, count]) + body)
            body, count = b"", 0
        body += entry
        count += 1
    if count:
        events.append(bytes([PLAYER_JOURNAL, count]) + body)
    return events


def unpack_journal(data):
    """(quest id, index) pairs of a PLAYER_JOURNAL event."""
    quests, off = [], 2
    for _ in range(data[1]):
        if off + 3 > len(data):
            break
        index = struct.unpack_from("<H", data, off)[0]
        raw, found, _ = data[off + 2:].partition(b"\0")
        if not found:
            break
        quests.append((wire_text(raw), index))
        off += 3 + len(raw)
    return quests


def describe_level(body):
    level, progress, *rest = LEVEL.unpack(body)
    stats, attributes = rest[11:14], rest[14:]
    return (f"level {level} ({progress} toward the next), health {stats[0]:.0f}, magicka "
            f"{stats[1]:.0f}, fatigue {stats[2]:.0f}, "
            + ", ".join(f"{n} {v:.0f}" for n, v in zip(ATTRIBUTE_NAMES, attributes)))


def effect_id_not_bound(active):
    effect_id = struct.unpack_from('<h', active, 2)[0]
    return not 120 <= effect_id <= 131 or effect_id == 126


def unpack_player_effects(body):
    """Validate a complete, pointer-free active-effect snapshot."""
    body = bytes(body)
    if len(body) % PLAYER_EFFECT_BYTES or len(body) > PLAYER_EFFECT_BYTES * PLAYER_EFFECTS_MAX:
        raise ValueError("bad active-effect snapshot size")
    effects, seen, sources, instances = [], set(), {}, {}
    for off in range(0, len(body), PLAYER_EFFECT_BYTES):
        serial, source_type, index, caster_kind, flags, caster, corprus = struct.unpack_from(
            '<IBBBBIf', body, off)
        source, item = body[off + 16:off + 48], body[off + 48:off + 80]
        active = body[off + 80:off + 92]
        resisted, magnitude, elapsed, cumulative, state, condition, charge = struct.unpack_from(
            '<fiffiII', body, off + 92)
        definitions = body[off + 120:off + 312]
        source_name = body[off + 312:off + 376]
        source_stats = body[off + 376:off + 388]
        previous = []
        for slot in range(5):
            at = off + 388 + slot * 44
            name, stack_flags, stack_condition, stack_charge = struct.unpack_from('<32sIII', body, at)
            if b'\0' not in name or stack_flags & ~1:
                raise ValueError('bad previous bound equipment')
            name = wire_text(name.split(b'\0')[0])
            if (name or stack_flags or stack_condition or stack_charge) and effect_id_not_bound(active):
                raise ValueError('previous equipment on a non-bound effect')
            if not name and (stack_flags or stack_condition or stack_charge):
                raise ValueError('previous bound equipment has no item')
            previous.append(dict(item=name, flags=stack_flags, condition=stack_condition,
                                 charge=stack_charge))
        effect_id = struct.unpack_from('<h', active, 2)[0]
        source_key = (source_type, source)
        source_data = (definitions, source_name, source_stats)
        if source_key in sources and sources[source_key] != source_data:
            raise ValueError("inconsistent active-effect source")
        sources[source_key] = source_data
        if (not serial or source_type not in (1, 2, 3) or index >= 8 or caster_kind > 3
                or flags & ~3 or not finite(corprus, resisted, elapsed, cumulative)
                or corprus < 0 or elapsed < 0 or state != 5 or magnitude < 0
                or active[0] != index or not 0 <= effect_id <= 142
                or (serial, index) in seen or b'\0' not in source or not source[0]
                or b'\0' not in item or b'\0' not in source_name
                or struct.unpack_from('<h', definitions, index * 24)[0] != effect_id):
            raise ValueError("bad active-effect entry")
        metadata = (source_type, source, item, caster_kind, caster, flags, corprus,
                    condition, charge)
        if serial in instances and instances[serial] != metadata:
            raise ValueError("inconsistent active-effect instance")
        instances[serial] = metadata
        for definition in struct.iter_unpack('<hbbiiiii', definitions):
            effect, skill, attribute, range_, area, duration, low, high = definition
            if effect == -1:
                continue
            if (not 0 <= effect <= 142 or not -1 <= skill < 27 or not -1 <= attribute < 8
                    or range_ not in (0, 1, 2) or min(area, duration, low, high) < 0
                    or max(area, duration, low, high) > 10000000 or low > high):
                raise ValueError("bad active-effect source definition")
        if source_type == 3:
            weight = struct.unpack_from('<f', source_stats)[0]
            if not finite(weight) or not 0 <= weight <= 10000000:
                raise ValueError("bad active-effect source weight")
        seen.add((serial, index))
        effects.append(dict(serial=serial, source_type=source_type, index=index,
                            caster_kind=caster_kind, flags=flags, caster=caster,
                            corprus=corprus, source=wire_text(source.split(b'\0')[0]),
                            item=wire_text(item.split(b'\0')[0]), active=active.hex(),
                            resisted=resisted, magnitude=magnitude, elapsed=elapsed,
                            cumulative=cumulative, state=state, condition=condition,
                            charge=charge, definitions=definitions.hex(),
                            source_name=wire_text(source_name.split(b'\0')[0]),
                            source_stats=source_stats.hex(), previous=previous))
    return effects


def pack_player_effects(effects):
    """Atomic snapshot parts; elapsed game time stops while the character is offline."""
    body = b''
    for e in effects:
        if (len(e['source'].encode('latin-1')) >= 32
                or len(e['item'].encode('latin-1')) >= 32
                or len(e['source_name'].encode('latin-1')) >= 64):
            raise ValueError('active-effect source text too long')
        body += struct.pack('<IBBBBIf', e['serial'], e['source_type'], e['index'],
                            e['caster_kind'], e['flags'], e['caster'], e['corprus'])
        body += e['source'].encode('latin-1').ljust(32, b'\0')
        body += e['item'].encode('latin-1').ljust(32, b'\0')
        body += bytes.fromhex(e['active'])
        body += struct.pack('<fiffiII', e['resisted'], e['magnitude'], e['elapsed'],
                            e['cumulative'], e['state'], e['condition'], e['charge'])
        body += bytes.fromhex(e['definitions'])
        body += e['source_name'].encode('latin-1').ljust(64, b'\0')
        body += bytes.fromhex(e['source_stats'])
        previous = e.get('previous', [])
        if len(previous) > 5:
            raise ValueError('too much previous bound equipment')
        for slot in range(5):
            entry = previous[slot] if slot < len(previous) else {}
            name = entry.get('item', '').encode('latin-1')
            if len(name) >= 32:
                raise ValueError('previous bound item text too long')
            body += struct.pack('<32sIII', name, entry.get('flags', 0),
                                entry.get('condition', 0), entry.get('charge', 0))
    unpack_player_effects(body)
    size = EVENT_DATA - 5
    parts = [body[i:i + size] for i in range(0, len(body), size)] or [b'']
    return [bytes([PLAYER_EFFECTS]) + struct.pack('<HH', i, len(parts)) + p
            for i, p in enumerate(parts)]


class Noise:
    """A Noise_XX_25519_ChaChaPoly_BLAKE2b handshake, either side; tes3xnoise.c is the console's
    initiator. Raises ValueError on a message that does not authenticate."""

    def __init__(self, initiator, s_secret, e_secret, prologue):
        self.initiator, self.s, self.e = initiator, s_secret, e_secret
        self.h = self.ck = NOISE_PROTOCOL.ljust(64, b"\0")
        self.k, self.n, self.re, self.rs = None, 0, None, None
        self.mix_hash(prologue)

    def mix_hash(self, data):
        self.h = hashlib.blake2b(self.h + data).digest()

    def hkdf(self, ikm):
        temp = hmac.new(self.ck, ikm, hashlib.blake2b).digest()
        out1 = hmac.new(temp, b"\x01", hashlib.blake2b).digest()
        return out1, hmac.new(temp, out1 + b"\x02", hashlib.blake2b).digest()

    def dh(self, secret, public):
        try:
            shared = crypto()[1].X25519PrivateKey.from_private_bytes(secret).exchange(
                crypto()[1].X25519PublicKey.from_public_bytes(public))
        except ValueError:
            raise ValueError("low-order key") from None
        self.ck, temp = self.hkdf(shared)
        self.k, self.n = temp[:32], 0

    def encrypt(self, plain):
        out = plain
        if self.k:
            out, self.n = seal(self.k, self.n, self.h, plain), self.n + 1
        self.mix_hash(out)
        return out

    def decrypt(self, data):
        plain = data
        if self.k:
            plain = unseal(self.k, self.n, self.h, data)
            if plain is None:
                raise ValueError("not authentic")
            self.n += 1
        self.mix_hash(data)
        return plain

    def write_e(self):
        e = x25519_public(self.e)
        self.mix_hash(e)
        return e

    def read_e(self, message):
        if len(message) < 32:
            raise ValueError("short message")
        self.re = message[:32]
        self.mix_hash(self.re)
        return message[32:]

    def write1(self, payload=b""):
        return self.write_e() + self.encrypt(payload)

    def read1(self, message):
        return self.decrypt(self.read_e(message))

    def write2(self, payload=b""):
        out = self.write_e()
        self.dh(self.e, self.re)
        out += self.encrypt(x25519_public(self.s))
        self.dh(self.s, self.re)
        return out + self.encrypt(payload)

    def read2(self, message):
        rest = self.read_e(message)
        if len(rest) < 32 + 2 * NOISE_TAG:
            raise ValueError("short message")
        self.dh(self.e, self.re)
        self.rs = self.decrypt(rest[:32 + NOISE_TAG])
        self.dh(self.e, self.rs)
        return self.decrypt(rest[32 + NOISE_TAG:])

    def write3(self, payload=b""):
        out = self.encrypt(x25519_public(self.s))
        self.dh(self.s, self.re)
        return out + self.encrypt(payload)

    def read3(self, message):
        if len(message) < 32 + 2 * NOISE_TAG:
            raise ValueError("short message")
        self.rs = self.decrypt(message[:32 + NOISE_TAG])
        self.dh(self.e, self.rs)
        return self.decrypt(message[32 + NOISE_TAG:])

    def split(self):
        """(initiator to responder, responder to initiator) transport keys."""
        one, two = self.hkdf(b"")
        return one[:32], two[:32]


STATE_BODY = struct.Struct("<I4f32s")  # flags, x, y, z, heading, interior cell name


STATE_SIZE = STATE_BODY.size + ANIM_BYTES  # then the animation


IN_WORLD, INTERIOR = 1, 2


STANCE = 4 | 8  # weapon drawn, spell readied; in STATE's flags and ACTOR's


PLACE = IN_WORLD | INTERIOR


CELL_UNITS = 8192


def describe_state(state):
    flags, x, y, z, heading, cell = STATE_BODY.unpack_from(state)
    if not flags & IN_WORLD:
        return "not in the world"
    where = (wire_text(cell.split(b"\0", 1)[0]) if flags & INTERIOR
             else f"exterior {int(x // CELL_UNITS)},{int(y // CELL_UNITS)}")
    return f"{where} at {x:.0f},{y:.0f},{z:.0f} heading {math.degrees(heading) % 360:.0f}"


def same_place(a, b):
    """Two STATE_BODYs in one cell within PLACE_NEAR of each other."""
    fa, xa, ya, za, _, ca = STATE_BODY.unpack_from(a)
    fb, xb, yb, zb, _, cb = STATE_BODY.unpack_from(b)
    return (fa & PLACE == fb & PLACE and (not fa & INTERIOR or ca == cb)
            and math.dist((xa, ya, za), (xb, yb, zb)) < PLACE_NEAR)


def cell_keys(state):
    """(own cell, loaded cells) of a STATE: an interior, or an exterior cell and its neighbours."""
    flags, x, y, _, _, cell = STATE_BODY.unpack_from(state)
    if not flags & IN_WORLD:
        return None, set()
    if flags & INTERIOR:
        key = (KEY_INTERIOR, 0, 0, cell.split(b"\0", 1)[0])
        return key, {key}
    gx, gy = math.floor(x / CELL_UNITS), math.floor(y / CELL_UNITS)
    return ((KEY_EXTERIOR, gx, gy, b""),
            {(KEY_EXTERIOR, gx + dx, gy + dy, b"") for dx in (-1, 0, 1) for dy in (-1, 0, 1)})


def describe_key(key):
    kind, gx, gy, name = key
    return wire_text(name) if kind == KEY_INTERIOR else f"exterior {gx},{gy}"


def now_us():
    return int(time.perf_counter() * 1e6) & 0xFFFFFFFF


GHOST_PLUGIN = "TES3X Multiplayer.esp"


GHOST_CELL = "TES3X Ghosts"


ARRIVAL_CELL = "TES3X Arrival"  # tes3xnet.c's CHARGEN_CELL


GHOSTS = 8  # one per peer slot in tes3xnet.c


BOT_ID = 99


def zstr(text):
    return text.encode("latin-1") + b"\0"


CHARGEN_SOURCE = """Begin CharGen
; Morrowind.esm's, except that a New Game joining a server starts in TES3X Arrival (the payload
; swaps [PreLoad] Cell 0) and stays there rather than going to the prison ship, and one started
; from a kept character (serve --load-state) stays in that character's cell.
DisablePlayerControls
DisablePlayerJumping
DisablePlayerViewSwitch
DisableVanityMode
DisablePlayerFighting
DisablePlayerMagic
if ( GetPCCell "TES3X Arrival" == 1 )
	Player->PositionCell 0, 0, 64, 0, "TES3X Arrival"
elseif ( GetPCCell "Imperial Prison Ship" == 1 )
	Player->PositionCell 61, -135, 24, 340, "Imperial Prison Ship"
	ChangeWeather "Bitter Coast Region" 1
endif
set CharGenState to 10
stopscript CharGen
End CharGen
"""


def finite(*values):
    return all(math.isfinite(v) for v in values)


POSITION_LIMIT = 1e7  # units; the game's world spans well under a million


def placeable(*coordinates):
    """A position a client may report: finite, and inside cells a 32-bit grid can number."""
    return all(math.isfinite(v) and abs(v) <= POSITION_LIMIT for v in coordinates)


# What a HELLO's clock must fall in to be adopted: an hour past 24 or a huge timescale would keep
# Clock.advance rolling days for ever.
CLOCK_LIMITS = ((0, 24), (1, 31), (0, 11), (0, 100000), (0, 10000000), (0, 10000))


CLOCK_DEFAULT = (9.0, 16.0, 7.0, 427.0, 1.0, 30.0)


def sane_clock(offered):
    ok = finite(*offered) and all(lo <= v <= hi for v, (lo, hi) in zip(offered, CLOCK_LIMITS))
    return list(offered) if ok else list(CLOCK_DEFAULT)


# Remote admin: HELLO, then a single-use challenge, then the command and its reply sealed with
# a key from the admin password and that challenge. The password is stretched, so a captured
# exchange does not make guessing it cheap.
REMOTE_MAGIC = b"T3AD"


REMOTE_VERSION = 1


REMOTE_HELLO, REMOTE_CHALLENGE, REMOTE_COMMAND, REMOTE_REPLY, REMOTE_REFUSED = range(5)


REMOTE_HEAD = struct.Struct("<4sBB")


REMOTE_NONCE = 16


REMOTE_NONCE_SECONDS = 10.0


REMOTE_NONCES = 64


ADMIN_PASSWORD_MIN = 8


def admin_secret(password):
    """The stretched admin password both ends derive their per-command keys from."""
    return hashlib.scrypt(password, salt=b"tes3x remote admin v1", n=1 << 14, r=8, p=1,
                          dklen=32)


def remote_key(secret, nonce):
    return hmac.new(secret, b"tes3x admin " + nonce, hashlib.sha256).digest()
