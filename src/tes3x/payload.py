#!/usr/bin/env python3
"""Compile the injected payload's objects and link them for one XBE.

Each payload source compiles once to an object that names every engine-specific value as an
undefined symbol. Linking for a retail XBE supplies those values from the engine locators, places
the payload at the XBE's next section address and records its hooks, so shipped objects serve
every supported engine without a compiler.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess

import tes3x.inject as tes3x_inject
from tes3x.link import LinkError, link, write_map, write_pe
from tes3x.multi_signatures import SIGNATURES as MULTI_SIGNATURES, find_multi_signature
from tes3x.paths import bundled, checkout, resource
from tes3x.patch import (CONSOLE_PRINT_VSPRINTF, LOCATORS, archive_load_site,
                         find_call_sites, find_mcp37_context,
                         find_mcp125_context, find_save_allowed_context,
                         find_script_ip_restore_call, find_transition_calls, scene_form)

HOOKS = resource("hooks")
OBJECTS = resource("payloads", "objects")
SECTION = re.compile(r'^#include "(multi/\w+\.c)"$', re.M)
DEFAULT_SOURCES = ("tes3xhook.c", "tes3xlog.c", "tes3xini.c", "tes3xdiag.c")
SOURCE_DEPENDENCIES = {
    "tes3xinfoarena.c": ("tes3xpager.c",),
    "tes3xmwse.c": ("tes3xconsole.c",),
    "tes3xmulti.c": ("tes3xnet.c",),
    "tes3xagent.c": ("tes3xnet.c",),
}
NTSC_TEXT_SHA256 = "38b922df5461b195c61fb2fdb62d893810ef8482ec813d9f6a7a064fb75493d8"
BUNDLED_LLVM = bundled("externals", "llvm", "bin")
LLVM_DIRS = (Path("C:/Program Files/LLVM/bin"), Path("C:/msys64/clang64/bin"),
             Path("C:/msys64/mingw64/bin"))
CFLAGS = ("-target", "i386-pc-win32", "-march=pentium3", "-Os", "-ffreestanding", "-nostdlib",
          "-fno-builtin", "-fno-stack-protector", "-fno-asynchronous-unwind-tables",
          "-mno-incremental-linker-compatible")  # no timestamp, so objects rebuild identically
# Raise when the generated headers change shape, so shipped objects count as stale.
HEADER_FORMAT = 2
ENTRY = "_tes3x_entry"
ABSOLUTE = "tes3x_abs_"
WORD = "tes3x_word_"
# Values the build chooses rather than the engine, held as data words the linker places.
WORDS = ("BUILD_ID", "INVERT_LOOK")
OBJECTS_SCHEMA = 1

DATA_HANDLER = 0x003CB5F8
SERVICE_ACTOR = 0x001CD530  # ui::getServiceActor: the MenuDialog partner's mobile, or 0
LEVELUP_MENU = 0x001E68F0  # what EnableLevelupMenu runs: opens or re-shows MenuLevelUp
# What the SetPos, SetAngle and PlayGroup handlers call, to place and animate a reference without
# the script compiler.
PLACE_ADDRESSES = (
    ("FIND_REFERENCE", 0x00109CD0), ("REF_ANIMATION", 0x0012A160), ("REF_ORIENTATION", 0x0012C3D0),
    ("REF_ROTATION", 0x0012AFE0), ("NODE_SET_ROTATION", 0x00042C40), ("NODE_UPDATE", 0x00044170),
    ("NODE_UPDATE_EFFECTS", 0x00044530), ("NODE_UPDATE_PROPERTIES", 0x00044260),
    ("ANIM_HAS_GROUP", 0x000CC960), ("ANIM_PLAY_GROUP", 0x000CE5C0),
    ("ANIM_GROUP_TYPES", 0x003B6F20),
    ("BODY_PART_UPDATE", 0x000D0CF0), ("UPDATE_BIPED_PARTS", 0x0012E3D0),
    ("EXTERIOR_CHANGE", 0x000E34A0),
    ("UNREADY_WEAPON", 0x00158560), ("MOBILE_HANDS", 0x0015BAF0),
    ("BASE_UNDERWATER", 0x00158EF0),
    ("APPLY_HEALTH_DAMAGE", 0x0017C3D0), ("APPLY_FATIGUE_DAMAGE", 0x0017C950),
    ("HIT_STUN", 0x0017DC30), ("BLOOD", 0x001758B0),
)
# MagicSourceInstance::spellHit, which the multiplayer patch fronts at every call site, and what
# the ExplodeSpell and Cast handlers call to start a spell on a reference.
SPELL_HIT = 0x00150370
SPELL_HIT_SITES = 9
CAST_BOLT = 0x0014EB30  # a target effect's bolt at the cast, from process only
SPELL_ADDRESSES = (
    ("ACTIVATE_SPELL", 0x000BE050), ("MAGIC_INSTANCE", 0x000BCD30),
    ("RESOLVE_OBJECT", 0x00104300),
    ("SOURCE_EFFECTS", 0x000E7460), ("EFFECT_INSERT", 0x000BCA70),
    ("ACTIVE_EFFECT_NODE", 0x000BCBA0), ("RETIRE_EFFECTS", 0x0014D900),
    ("RETIRE_MAGIC_SERIAL", 0x000BDB10),
    ("MAGIC_PROCESS", 0x00151350), ("EFFECT_CALLBACKS", 0x003B5FC8),
    ("EFFECT_ICONS", 0x001EE460), ("ALCHEMY_NEW", 0x000FC650),
    ("ADD_NEW_OBJECT", 0x0010CA40), ("SET_STRING_SLOT", 0x000D50E0),
)
# What the leveled creature spawn and Inventory::DropItem call to make a reference at run time,
# put it in a cell, give it a stack count and attach it to the cell's scene.
SPAWN_ADDRESSES = (
    ("CREATE_REFERENCE", 0x00111730), ("CELL_INSERT", 0x00122F90), ("CELL_NODE", 0x00126220),
    ("CELL_ACTIVATORS", 0x001253E0), ("ATTACH_SCENE", 0x000DC2C0), ("ITEM_DATA_NEW", 0x0012AB10),
    ("ATTACH_ITEM_DATA", 0x0012D810), ("UPDATE_LIGHTING", 0x000DBFE0),
)
# A container's vtable (slot +0x164 clones it into an instance for one reference, as opening it
# does), the instance's vtable, and what the Contents menu calls to move items.
CONTAINER_ADDRESSES = (
    ("CONTAINER_VTABLE", 0x00366558), ("CONTAINER_INSTANCE_VTABLE", 0x003659A0),
    ("INVENTORY_ADD", 0x000EADD0), ("INVENTORY_REMOVE", 0x000EB4B0),
    ("ITEM_DATA_DESTROY", 0x0012BEB0), ("HEAP_FREE", 0x00011CF0), ("HEAP", 0x003EFC70),
)
# An actor's shoot slot (mobile vtable +0xF4) releases the projectile nock put in its hand; a
# MobileProjectile's actor collision rolls to hit once.
SHOOT = 0x0017BD30
SHOOT_SLOTS = 3
# The player's slot holds its own release, which settles the ammunition stack and jumps to SHOOT.
PLAYER_SHOOT = 0x00185390
# An actor's AI step (mobile vtable +0xA8): decisions and movement, or none with ToggleAI off.
AI_STEP = 0x001618D0
AI_STEP_SLOTS = 3
PROJECTILE_ACTOR_HIT = 0x00192610
HIT_ROLL = 0x0017B770
NOCK = 0x00158620
GET_BOUNTY = 0x00189E40  # MobilePlayer::getBounty: mov ecx, [ecx+0x598]; push "bounty"; call
ACTIVATION_TARGET = 0x00096110  # Game::CheckPlayerActivationTarget, from Game::Update only
REF_MODIFIED = 0x0012A940  # Reference::setObjectModified, in the Reference vtable only
# LeveledCreature's spawn for a placeholder reference, in its vtable only; what it calls to roll
# the list and to link the creature and the placeholder, and what gives a new actor its mobile.
LEVELED_SPAWN = 0x0011AD90
SUMMON = 0x000C8B00  # makes a summoned creature, from the summon effect's start only
PLAYER_SCRIPTS = 3  # CompileAndRun's callers: a dialogue result and the console
DROP_ITEM = 0x0015B0D0  # MobileActor::dropItem; the inventory menus call it on the player
PLAYER_DROPS = 2
# relocateReference(ref, cell, position, rotation): Position, PositionCell and PlaceItemCell
RELOCATE = 0x00138AA0
RELOCATE_CALLS = 3
SCRIPT_EXECUTE = 0x00148A70  # Script::execute(reference, variables, info, reference)
PROCESS_GLOBAL_SCRIPTS = 0x0008C910  # calls Script::execute once per running global script
ADD_GLOBAL_SCRIPT = 0x0008E6E0  # WorldController::addGlobalScript(script, reference)
FIND_SCRIPT = 0x00105DA0  # NonDynamicData::findScriptByName
# NPCInstance and CreatureInstance setAIPackage (Actor vtable +0x184), each in one vtable only:
# what AITravel, AIWander, AIEscort and AIFollow end in.
NPC_SET_AI = 0x0011FBB0
CREATURE_SET_AI = 0x000ECA90
SAVE_CALLS = 7  # SaveGame's callers: quicksave, three autosaves, the save menu's two, fatal error
ACTOR_ADDRESSES = (
    ("LEVELED_RESOLVE", 0x0011A740), ("LEVELED_LINKED", 0x0012AEE0),
    ("LEVELED_LINK", 0x0012A370), ("ADD_MOB", 0x001840B0), ("SIMULATE", 0x00180630),
    ("START_COMBAT", 0x001629F0),
)
# What the player's identity is applied with: the record lookups, what MenuCreateClass's OK
# calls to make and list chargen's class, MenuRaceSex's OK, which rebuilds the player's model, and
# Actor::unequipItem and Actor::equipItem, to put back what that OK changes, and the per-frame menu
# check's build of MenuInventory, which that OK destroys.
IDENTITY_ADDRESSES = (
    ("FIND_RACE", 0x00105C10), ("FIND_CLASS", 0x00105D50), ("FIND_BIRTHSIGN", 0x00105CB0),
    ("ENGINE_ALLOCATE", 0x00011840), ("CLASS_NEW", 0x000EFEB0), ("CLASS_SET_ID", 0x000F01C0),
    ("CLASS_DESCRIPTION", 0x000FB790), ("CLASS_SET_DESCRIPTION", 0x000F0060),
    ("LIST_APPEND", 0x000D5A60), ("GMST_TEXT", 0x0008BB50), ("RACE_SEX_OK", 0x00206570),
    ("UNEQUIP_ITEM", 0x000E6B90), ("EQUIP_ITEM", 0x000E7E90),
    ("INVENTORY_BUILD", 0x001DEC90), ("MENU_TAB", 0x001FB4C0),
    # NonDynamicData::findFaction, Faction::loadPlayerData (the save load's restore), UpdateStatsPane
    ("FIND_FACTION", 0x00105DF0), ("FACTION_LOAD", 0x000F0910), ("UPDATE_STATS_PANE", 0x0021E7E0),
    ("FIND_GLOBAL", 0x00106000),  # NonDynamicData::findGlobalVariable
    ("JOURNAL_ADD", 0x000FFE00),  # Dialogue::journalAdd: the Journal command without its toast
)
PLAYER_CONTROL = 0x001717E0  # PlayerAnimationController's update: look, controls, animation
CONSOLE_ADDRESSES = (
    ("FIND_MENU", 0x001AD340), ("OPEN_VK", 0x0022D210), ("CONSOLE_MENU_ID", 0x003D816C),
    ("GET_PROP", 0x0019A770), ("COMPILE_RUN", 0x0014B3C0), ("VK_MENU_ID", 0x003DC710),
    ("VK_TEXT_ID", 0x003DC75C), ("GAME_PTR", 0x003CB5F4), ("WIDGET_TEXT", 0x0019B350),
    ("WIDGET_SET_TEXT", 0x0012F3A0), ("WIDGET_DIRTY", 0x00199660),
    ("PERFORM_LAYOUT", 0x001A6280), ("SET_PROP", 0x001A6C00), ("SET_AUTO_WIDTH", 0x00197710),
    ("SET_AUTO_HEIGHT", 0x00197740), ("FIND_CHILD", 0x0019A170), ("UI_ID", 0x001A7B50),
    ("VK_CASE_ID", 0x003DC810), ("VK_DONE_ID", 0x003DC834), ("TRIGGER_EVENT", 0x0019DCD0),
    ("CREATE_WIDGET", 0x001A6E00), ("VK_BUTTON", 0x0022E290), ("NAV_RIGHT_ID", 0x003D7328),
    ("NAV_LEFT_ID", 0x003D72F4), ("NAV_UP_ID", 0x003D7334), ("NAV_DOWN_ID", 0x003D7350),
    ("VK_ROW_NUM_ID", 0x003DC74C),
    ("VK_COL_NUM_ID", 0x003DC788), ("VK_CAPS_ID", 0x003DC714),
    ("VK_BACKSPACE_ID", 0x003DC774), ("VK_SPACE_ID", 0x003DC7E8), ("VK_CAPS", 0x0022C240),
    ("CREATE_IMAGE", 0x001A7080), ("BUTTON_HINT", 0x001F8630),
    ("OPEN_MENU", 0x001AED50), ("OPEN_JOURNAL", 0x001E3190), ("JOURNAL_OPENED", 0x001ACBE0),
    ("RECORDS_PTR", 0x003CB5F8), ("RESOLVE_OBJECT", 0x00104300), ("CLOSEST_REF", 0x0010CCD0),
    ("REF_ACTIVATE", 0x0012F630), ("PLAYER_MOBILE", 0x0008BC10),
    ("UI_NAV", 0x001B1050),  # the pad's D-pad: direction 0xE-0x11, click sound, remember
    ("OPTIONS_OPEN", 0x00201590), ("OPTIONS_ID", 0x003D9ED0), ("MENU_MODE_ON", 0x001AD020),
)
# The main menu builder's own calls (0x00200990), for buttons made the way it makes them
NET_UI_ADDRESSES = (
    ("CREATE_BLOCK", 0x001A7240), ("CREATE_RECT", 0x001A6EA0),
    ("CREATE_IMAGE", 0x001A7080), ("SET_WIDTH", 0x00198980),
    ("SET_HEIGHT", 0x00198A60), ("SET_VISIBLE", 0x001A63C0), ("CREATE_LABEL", 0x001A7300),
    ("SET_FOCUS", 0x001AF6E0), ("SET_AUTO_WIDTH", 0x00197710), ("CREATE_NIF", 0x001A7170),
    ("SCROLL_PANE", 0x002357B0),  # the factory registered as PartScrollPaneVert
    ("GET_FOCUS", 0x0019B700), ("SCROLL_TO", 0x00234300),
    # MenuOptions' Save, Load and Exit container ids
    ("SAVE_ID", 0x003D9ECC), ("LOAD_ID", 0x003DA018), ("EXIT_ID", 0x003DA05C),
)
class PayloadError(RuntimeError):
    pass


def hexva(value):
    return "0x%08X" % value


def find_ui_rect(image, create_image):
    """Find the rectangle factory just before the image factory by its widget type."""
    end = image.va_to_off(create_image)
    if end is None:
        raise PayloadError("UI image factory is outside the XBE")
    start = max(0, end - 0x300)
    marker = b"\xB8\xB7\x80\xFF\xFF"  # mov eax, UI rectangle type
    hits = [m.start() for m in re.finditer(re.escape(marker), image.data[start:end])]
    if len(hits) != 1:
        raise PayloadError(f"UI rectangle factory: {len(hits)} type markers")
    entry = image.data.rfind(b"\xCC", start, start + hits[0]) + 1
    head = bytes(image.data[entry:entry + 16])
    if not head.startswith(b"\x51") or b"\x8B\xF9\x80\x3F\x58" not in head:
        raise PayloadError("UI rectangle factory has an unexpected entry")
    va = image.off_to_va(entry)
    if va is None:
        raise PayloadError("UI rectangle factory is outside the XBE")
    return va


def resolve_address(name, default, known_image=True):
    """A fixed engine address, overridable for research builds on other images."""
    if name in os.environ:
        return os.environ[name]
    if not known_image:
        raise PayloadError(f"no binding for {name} in this engine; set {name} for a research build")
    return hexva(default)


def has_ntsc_fixed_bindings(image):
    """Accept retail/scene NTSC text, including XBEs with extra sections."""
    text = next((s for s in image.sections if s.name == ".text"), None)
    if text is None:
        return False
    raw = scene_form(bytes(image.data))
    return hashlib.sha256(raw[text.raw:text.raw + text.rsize]).hexdigest() == NTSC_TEXT_SHA256


def find_tool(name, llvm_dir=None):
    exe = name + (".exe" if os.name == "nt" else "")
    if llvm_dir:
        path = Path(llvm_dir) / exe
        if not path.is_file():
            raise PayloadError(f"{exe} not found in {llvm_dir}")
        return str(path)
    if (BUNDLED_LLVM / exe).is_file():
        return str(BUNDLED_LLVM / exe)
    found = shutil.which(name)
    if found:
        return found
    for folder in LLVM_DIRS:
        if (folder / exe).is_file():
            return str(folder / exe)
    raise PayloadError(f"{name} not found. Install LLVM (https://releases.llvm.org) and put its "
                       "bin folder on PATH, or set paths.llvm in the local config")


def source_path(name):
    path = Path(name)
    return path if path.is_absolute() or path.parent != Path(".") else HOOKS / name


def section_names(path):
    """The section files a source includes from multi/, as relative names."""
    return SECTION.findall(path.read_text(encoding="utf-8")) if path.exists() else []


def source_text(name):
    """A source's text with each section file it includes in place of its #include."""
    path = source_path(name)
    return SECTION.sub(lambda m: (path.parent / m.group(1)).read_text(encoding="utf-8"),
                       path.read_text(encoding="utf-8"))


def define_value(source, name):
    match = re.search(r"^#define %s +(.*)$" % name, source_path(source).read_text(), re.M)
    if not match:
        raise PayloadError(f"{source} does not define {name}")
    return match.group(1)


def link_symbol(link_map, *names):
    """A symbol's address from the lld-link map, 16 hex digits, trying each spelling in turn."""
    for name in names:
        for line in link_map:
            fields = line.split()
            if len(fields) >= 3 and fields[1] == name:
                return "0x" + fields[-2]
    raise PayloadError(f"could not find {names[0]} in the link map")


def expand_sources(sources):
    """The sources with those they always need, in link order."""
    sources = list(sources)
    names = {Path(s).name for s in sources}
    if "tes3xnet.c" in names:
        for extra_source in ("monocypher.c", "tes3xnoise.c", "tes3xcrt.c"):
            if extra_source not in names:
                sources.append(extra_source)
                names.add(extra_source)
    if names & {"tes3xconsole.c", "tes3xmulti.c", "tes3xdashbutton.c"} \
            and "tes3xlaunch.c" not in names:
        sources.append("tes3xlaunch.c")
    return sources


class Bindings:
    """What linking for one XBE needs beyond the objects."""

    def __init__(self, image, sources, values, wanted, extra, arch_site):
        self.image = image
        self.sources = sources
        self.values = values
        self.wanted = wanted
        self.extra = extra
        self.arch_site = arch_site

    def layout(self):
        """Each value's element count: None for a scalar."""
        return {name: (len(items) if array else None)
                for name, (array, items) in self.values.items()}


def engine_bindings(xbe, sources):
    """Locate every engine value the sources need in xbe."""
    sources = expand_sources(sources)
    names = {Path(s).name for s in sources}
    image = xbe if isinstance(xbe, tes3x_inject.Xbe) else tes3x_inject.Xbe(Path(xbe).read_bytes())
    known_image = has_ntsc_fixed_bindings(image)
    resolved_addresses = {}

    def engine_address(name, default):
        if name in os.environ:
            return os.environ[name]
        if name == "CREATE_RECT":
            create_image = int(engine_address("CREATE_IMAGE", 0x001A7080), 16)
            return hexva(find_ui_rect(image, create_image))
        key = name.lower().replace("_", "-")
        if name in ("GET_FOCUS", "LIST_APPEND", "SET_VISIBLE"):
            key = "multi-" + key
        elif not key.startswith("console-"):
            key = "console-" + key
        if key in LOCATORS:
            if name not in resolved_addresses:
                resolved_addresses[name] = hexva(LOCATORS[key](image))
            return resolved_addresses[name]
        if name in MULTI_SIGNATURES:
            if name not in resolved_addresses:
                resolved_addresses[name] = hexva(find_multi_signature(image, name))
            return resolved_addresses[name]
        return resolve_address(name, default, known_image)

    address = engine_address
    values = {}

    def define(name, value):
        value = str(value).strip()
        array = value.startswith("{")
        items = [int(item, 0) for item in value.strip("{}").split(",")]
        values[name] = (array, items)

    def locate(name):
        return LOCATORS[name](image)

    arch_site = None
    wanted, extra = {}, {}
    if "tes3xarch.c" in names:
        arch_site = int(os.environ.get("ARCH_SITE", hexva(archive_load_site(image))), 16)
        load = tes3x_inject.call_target(image, arch_site)
        print(f"archive hook: call site {hexva(arch_site)} -> Archive::Load {hexva(load)}")
        define("ARCHIVE_LOAD", hexva(load))
        wanted["archive_load"] = ("_tes3x_archive_hook",)
    if "tes3xscript.c" in names:
        run_function, table = locate("run-function"), locate("command-table")
        base = define_value("tes3xscript.c", "TES3X_OPCODE_BASE")
        ceil = define_value("tes3xscript.c", "TES3X_OPCODE_CEIL")
        print(f"script hook: RunFunction {hexva(run_function)}, table {hexva(table)}, "
              f"opcodes [{base}, {ceil})")
        define("RUN_FUNCTION", hexva(run_function))
        define("COMMAND_TABLE", hexva(table))
        wanted["script_dispatch"] = ("_tes3x_script_hook",)
        extra["script_dispatch"] = {"opcode_base": base, "opcode_ceil": ceil}
    if "tes3xmwse.c" in names:
        if "tes3xscript.c" not in names:
            raise PayloadError("tes3xmwse.c requires tes3xscript.c")
        decode = locate("script-decode")
        script_ip = locate("script-ip")
        script_opcode = locate("script-opcode")
        game_instance = locate("game-instance")
        world = locate("world-controller")
        print(f"legacy MWSE: Decode {hexva(decode)}, IP {hexva(script_ip)}, "
              f"opcode {hexva(script_opcode)}, Game {hexva(game_instance)}, World {hexva(world)}")
        define("SCRIPT_DECODE", hexva(decode))
        define("SCRIPT_IP", hexva(script_ip))
        define("SCRIPT_OPCODE", hexva(script_opcode))
        restore_site = find_script_ip_restore_call(image, run_function, script_ip)
        define("MWSE_IP_RESTORE_RETURN", hexva(restore_site + 5))
        define("GAME_INSTANCE", hexva(game_instance))
        define("MWSE_WORLD", hexva(world))
        define("MWSE_DATA_HANDLER", address("DATA_HANDLER", DATA_HANDLER))
        find_reference = dict(PLACE_ADDRESSES)["FIND_REFERENCE"]
        define("MWSE_FIND_REFERENCE", address("FIND_REFERENCE", find_reference))
        resolve_object = dict(SPELL_ADDRESSES)["RESOLVE_OBJECT"]
        inventory_add = dict(CONTAINER_ADDRESSES)["INVENTORY_ADD"]
        inventory_remove = dict(CONTAINER_ADDRESSES)["INVENTORY_REMOVE"]
        define("MWSE_RESOLVE_OBJECT", address("RESOLVE_OBJECT", resolve_object))
        define("MWSE_INVENTORY_ADD", address("INVENTORY_ADD", inventory_add))
        define("MWSE_INVENTORY_REMOVE", address("INVENTORY_REMOVE", inventory_remove))
        define("MWSE_INVENTORY_ITEM_DATA", address("INVENTORY_ITEM_DATA", 0x000E8880))
        define("MWSE_ACTOR_EQUIPPED", address("ACTOR_EQUIPPED", 0x000E7250))
        define("MWSE_WEAR_ITEM", address("WEAR_ITEM", 0x0015E6B0))
        define("MWSE_DROP_ITEM", address("DROP_ITEM", DROP_ITEM))
        define("MWSE_START_COMBAT", address("START_COMBAT", 0x001629F0))
        define("MWSE_GET_SPELL_LIST", address("GET_SPELL_LIST", 0x0015A5A0))
        define("MWSE_SPELL_ADD_ID", address("SPELL_ADD_ID", 0x000FCC60))
        define("MWSE_SPELL_REMOVE", address("SPELL_REMOVE", 0x000F9A30))
        define("MWSE_FORCE_CAST", address("FORCE_CAST", 0x00162950))
        for name, default in SPAWN_ADDRESSES:
            if name in ("CREATE_REFERENCE", "CELL_INSERT", "CELL_NODE", "CELL_ACTIVATORS",
                        "ATTACH_SCENE", "UPDATE_LIGHTING"):
                define("MWSE_" + name, address(name, default))
        define("MWSE_REF_MODIFIED", address("REF_MODIFIED", REF_MODIFIED))
        define("MWSE_ADD_MOB", address("ADD_MOB", dict(ACTOR_ADDRESSES)["ADD_MOB"]))
        wanted["mwse_fixup"] = ("@tes3x_mwse_fixup_hook@8", "_tes3x_mwse_fixup_hook")
    if "tes3xini.c" in names:
        ini_get = os.environ.get("INI_GET") or hexva(locate("ini-get-string"))
        ini_path = os.environ.get("INI_PATH") or hexva(locate("ini-path"))
        print(f"ini reader {ini_get}, ini path {ini_path}")
        define("INI_GET_STRING", ini_get)
        define("INI_PATH", ini_path)
    if "tes3xsaves.c" in names:
        save_game = hexva(locate("save-game"))
        save_this = hexva(locate("save-this-ptr"))
        save_owner, save_owner_off, save_state_off = find_save_allowed_context(image)
        save_owner = hexva(save_owner)
        _transition_calls, cell_change, companions = find_transition_calls(image)
        print(f"autosave hook: SaveGame {save_game}, owner pointer {save_this}")
        print(f"save gate: owner {save_owner} + 0x{save_owner_off:X} + 0x{save_state_off:X}")
        print(f"transition hooks: cell change {hexva(cell_change)}, companions {hexva(companions)}")
        define("SAVE_GAME", save_game)
        define("SAVE_THIS_PTR", save_this)
        define("SAVE_GATE_OWNER", save_owner)
        define("SAVE_GATE_OWNER_OFFSET", hex(save_owner_off))
        define("SAVE_GATE_STATE_OFFSET", hex(save_state_off))
        define("CELL_CHANGE", hexva(cell_change))
        define("CELL_CHANGE_COMPANIONS", hexva(companions))
    if "tes3xprefs.c" in names:
        site = locate("preferences-load")
        load = hexva(tes3x_inject.call_target(image, site))
        table = hexva(locate("controls-table"))
        print(f"preferences hook: controls load {load}, bindings {table}")
        define("CONTROLS_LOAD", load)
        define("CONTROLS_TABLE", table)
    if "tes3xprof.c" in names:
        print("profiler: RDTSC region timing, targets chosen at patch time")
    if "tes3xheap.c" in names:
        allocate, free = hexva(locate("heap-allocate")), hexva(locate("heap-free"))
        heap = hexva(locate("heap-object"))
        print(f"heap census: Allocate {allocate}, Free {free}, heap {heap}")
        define("HEAP_ALLOCATE", allocate)
        define("HEAP_FREE", free)
        define("HEAP_OBJECT", heap)
        wanted["heap_allocate"] = ("_tes3x_heap_alloc_hook",)
        wanted["heap_allocate_wrapped"] = ("_tes3x_heap_alloc_wrapped_hook",)
        wanted["heap_allocate_new"] = ("_tes3x_heap_alloc_new_hook",)
        wanted["heap_allocate_pool"] = ("_tes3x_heap_alloc_pool_hook",)
        wanted["heap_free"] = ("_tes3x_heap_free_hook",)
    if "tes3xmem.c" in names:
        heap_alloc, heap_free = hexva(locate("xapi-heap-alloc")), hexva(locate("xapi-heap-free"))
        text = next(s for s in image.sections if s.name == ".text")
        print(f"memory census: RtlAllocateHeap {heap_alloc}, RtlFreeHeap {heap_free}")
        define("MEM_HEAP_ALLOC", heap_alloc)
        define("MEM_HEAP_FREE", heap_free)
        define("MEM_CODE_START", hexva(text.va))
        define("MEM_CODE_END", hexva(text.va + text.vsize))
        wanted["mem_heap_alloc"] = ("_tes3x_mem_heap_alloc@12",)
        wanted["mem_heap_free"] = ("_tes3x_mem_heap_free@12",)
    if "tes3xpager.c" in names:
        print("pager: synthetic test on the console command tes3xpager")
    if "tes3xnet.c" in names:
        if "tes3xdiag.c" not in names:
            raise PayloadError("tes3xnet.c requires tes3xdiag.c for its frame hook")
        wanted["net"] = ("_tes3x_net_frame",)
    if "tes3xmulti.c" in names:
        if "tes3xnet.c" not in names:
            raise PayloadError("tes3xmulti.c requires tes3xnet.c")
        world = hexva(locate("world-controller"))
        data_handler = address("DATA_HANDLER", DATA_HANDLER)
        print(f"network: [Xbox] NetAddress, UDP 26500, WorldController {world}, "
              f"DataHandler {data_handler}")
        define("NET_WORLD", world)
        define("NET_DATA_HANDLER", data_handler)
        for name in ("COMPILE_RUN", "FIND_MENU", "UI_ID", "TRIGGER_EVENT", "FIND_CHILD",
                     "CREATE_WIDGET", "VK_BUTTON", "WIDGET_SET_TEXT", "SET_PROP",
                     "PERFORM_LAYOUT", "NAV_UP_ID", "NAV_DOWN_ID"):
            define("NET_" + name, address(name, dict(CONSOLE_ADDRESSES)[name]))
        for name, default in NET_UI_ADDRESSES:
            define("NET_" + name, address(name, default))
        define("NET_SERVICE_ACTOR", address("SERVICE_ACTOR", SERVICE_ACTOR))
        define("NET_LEVELUP_MENU", address("LEVELUP_MENU", LEVELUP_MENU))
        for name, default in (PLACE_ADDRESSES + SPELL_ADDRESSES + SPAWN_ADDRESSES
                              + CONTAINER_ADDRESSES + ACTOR_ADDRESSES + IDENTITY_ADDRESSES):
            define("NET_" + name, address(name, default))
        spell_hit = int(address("SPELL_HIT", SPELL_HIT), 16)
        sites = find_call_sites(image, spell_hit)
        if len(sites) != SPELL_HIT_SITES:
            raise PayloadError(f"spellHit {hexva(spell_hit)}: {len(sites)} call sites, expected "
                               f"{SPELL_HIT_SITES}")
        define("NET_SPELL_HIT", hexva(spell_hit))
        define("NET_SPELL_HIT_SITES", "{" + ",".join(hexva(s) for s in sites) + "}")
        cast_bolt = int(address("CAST_BOLT", CAST_BOLT), 16)
        sites = find_call_sites(image, cast_bolt)
        if len(sites) != 1:
            raise PayloadError(f"cast bolt {hexva(cast_bolt)}: {len(sites)} call sites, expected 1")
        define("NET_CAST_BOLT", hexva(cast_bolt))
        define("NET_CAST_BOLT_SITES", "{" + hexva(sites[0]) + "}")
        shoot = int(address("SHOOT", SHOOT), 16)
        slots = [image.off_to_va(m.start()) for m in re.finditer(re.escape(struct.pack("<I", shoot)),
                                                                 bytes(image.data))]
        if len(slots) != SHOOT_SLOTS or None in slots:
            raise PayloadError(f"shoot {hexva(shoot)}: {len(slots)} vtable slots, expected "
                               f"{SHOOT_SLOTS}")
        hit = int(address("PROJECTILE_ACTOR_HIT", PROJECTILE_ACTOR_HIT), 16)
        roll = int(address("HIT_ROLL", HIT_ROLL), 16)
        roll_sites = [s for s in find_call_sites(image, roll) if hit <= s < hit + 0x600]
        if len(roll_sites) != 1:
            raise PayloadError(f"projectile hit roll: {len(roll_sites)} call sites, expected 1")
        define("NET_SHOOT", hexva(shoot))
        define("NET_SHOOT_SLOTS", "{" + ",".join(hexva(s) for s in slots) + "}")
        player_shoot = int(address("PLAYER_SHOOT", PLAYER_SHOOT), 16)
        body = bytes(image.data[image.va_to_off(player_shoot):][:0x40])
        jumps = [i for i in range(len(body) - 4) if body[i] == 0xE9 and
                 player_shoot + i + 5 + struct.unpack_from("<i", body, i + 1)[0] == shoot]
        slots = [image.off_to_va(m.start()) for m in re.finditer(
            re.escape(struct.pack("<I", player_shoot)), bytes(image.data))]
        if len(jumps) != 1 or len(slots) != 1 or None in slots:
            raise PayloadError(f"player shoot {hexva(player_shoot)}: {len(jumps)} jumps to shoot, "
                               f"{len(slots)} vtable slots, expected 1 and 1")
        define("NET_PLAYER_SHOOT", hexva(player_shoot))
        define("NET_PLAYER_SHOOT_SLOT", hexva(slots[0]))
        ai_step = int(address("AI_STEP", AI_STEP), 16)
        slots = [image.off_to_va(m.start()) for m in re.finditer(
            re.escape(struct.pack("<I", ai_step)), bytes(image.data))]
        if len(slots) != AI_STEP_SLOTS or None in slots:
            raise PayloadError(f"AI step {hexva(ai_step)}: {len(slots)} vtable slots, expected "
                               f"{AI_STEP_SLOTS}")
        define("NET_AI_STEP", hexva(ai_step))
        define("NET_AI_STEP_SLOTS", "{" + ",".join(hexva(s) for s in slots) + "}")
        define("NET_NOCK", address("NOCK", NOCK))
        get_bounty = int(address("GET_BOUNTY", GET_BOUNTY), 16)
        if bytes(image.data[image.va_to_off(get_bounty):][:7]) != bytes.fromhex("8b899805000068"):
            raise PayloadError(f"get bounty {hexva(get_bounty)}: not MobilePlayer::getBounty")
        define("NET_GET_BOUNTY", hexva(get_bounty))
        hit_stun = int(address("HIT_STUN", dict(PLACE_ADDRESSES)["HIT_STUN"]), 16)
        sites = find_call_sites(image, hit_stun)
        if len(sites) != 2:
            raise PayloadError(f"hit stun/crime {hexva(hit_stun)}: {len(sites)} call sites, "
                               "expected 2")
        define("NET_HIT_STUN_SITES", "{" + ",".join(hexva(s) for s in sites) + "}")
        define("NET_HIT_ROLL", hexva(roll))
        define("NET_SHOT_ROLL_SITES", "{" + hexva(roll_sites[0]) + "}")
        target = int(address("ACTIVATION_TARGET", ACTIVATION_TARGET), 16)
        sites = find_call_sites(image, target)
        if len(sites) != 1:
            raise PayloadError(f"activation target: {len(sites)} call sites, expected 1")
        define("NET_ACTIVATION_TARGET", hexva(target))
        define("NET_ACTIVATION_TARGET_SITES", "{" + hexva(sites[0]) + "}")
        activate = int(address("REF_ACTIVATE", dict(CONSOLE_ADDRESSES)["REF_ACTIVATE"]), 16)
        sites = [s for s in find_call_sites(image, activate) if target <= s < target + 0x400]
        if len(sites) != 1:
            raise PayloadError(f"player activation: {len(sites)} call sites, expected 1")
        define("NET_REF_ACTIVATE", hexva(activate))
        define("NET_PLAYER_ACTIVATE_SITES", "{" + hexva(sites[0]) + "}")
        control = int(address("PLAYER_CONTROL", PLAYER_CONTROL), 16)
        slots = [image.off_to_va(m.start()) for m in re.finditer(
            re.escape(struct.pack("<I", control)), bytes(image.data))]
        if len(slots) != 1 or None in slots:
            raise PayloadError(f"player control {hexva(control)}: {len(slots)} vtable slots, "
                               "expected 1")
        define("NET_PLAYER_CONTROL", hexva(control))
        define("NET_PLAYER_CONTROL_SLOT", hexva(slots[0]))
        modified = int(address("REF_MODIFIED", REF_MODIFIED), 16)
        slots = [image.off_to_va(m.start()) for m in re.finditer(
            re.escape(struct.pack("<I", modified)), bytes(image.data))]
        if len(slots) != 1 or None in slots:
            raise PayloadError(f"Reference::setObjectModified {hexva(modified)}: {len(slots)} "
                               "vtable slots, expected 1")
        define("NET_REF_MODIFIED", hexva(modified))
        define("NET_REF_MODIFIED_SLOT", hexva(slots[0]))
        leveled = int(address("LEVELED_SPAWN", LEVELED_SPAWN), 16)
        slots = [image.off_to_va(m.start()) for m in re.finditer(
            re.escape(struct.pack("<I", leveled)), bytes(image.data))]
        if len(slots) != 1 or None in slots:
            raise PayloadError(f"LeveledCreature spawn {hexva(leveled)}: {len(slots)} vtable "
                               "slots, expected 1")
        define("NET_LEVELED_SPAWN", hexva(leveled))
        define("NET_LEVELED_SPAWN_SLOT", hexva(slots[0]))
        summon = int(address("SUMMON", SUMMON), 16)
        sites = find_call_sites(image, summon)
        if len(sites) != 1:
            raise PayloadError(f"summon {hexva(summon)}: {len(sites)} call sites, expected 1")
        define("NET_SUMMON", hexva(summon))
        define("NET_SUMMON_SITES", "{" + hexva(sites[0]) + "}")
        compile_run = int(address("COMPILE_RUN", dict(CONSOLE_ADDRESSES)["COMPILE_RUN"]), 16)
        sites = find_call_sites(image, compile_run)
        if len(sites) != PLAYER_SCRIPTS:
            raise PayloadError(f"CompileAndRun: {len(sites)} call sites, expected {PLAYER_SCRIPTS}")
        define("NET_PLAYER_SCRIPT_SITES", "{" + ",".join(hexva(s) for s in sites) + "}")
        relocate = int(address("RELOCATE", RELOCATE), 16)
        sites = find_call_sites(image, relocate)
        if len(sites) != RELOCATE_CALLS:
            raise PayloadError(f"relocateReference {hexva(relocate)}: {len(sites)} call sites, "
                               f"expected {RELOCATE_CALLS}")
        define("NET_RELOCATE", hexva(relocate))
        define("NET_RELOCATE_SITES", "{" + ",".join(hexva(s) for s in sites) + "}")
        execute = int(address("SCRIPT_EXECUTE", SCRIPT_EXECUTE), 16)
        process = int(address("PROCESS_GLOBAL_SCRIPTS", PROCESS_GLOBAL_SCRIPTS), 16)
        sites = [s for s in find_call_sites(image, execute) if process <= s < process + 0x60]
        if len(sites) != 1:
            raise PayloadError(f"Script::execute in processGlobalScripts: {len(sites)} call "
                               "sites, expected 1")
        define("NET_SCRIPT_EXECUTE", hexva(execute))
        define("NET_GLOBAL_SCRIPT_SITES", "{" + hexva(sites[0]) + "}")
        define("NET_ADD_GLOBAL_SCRIPT", address("ADD_GLOBAL_SCRIPT", ADD_GLOBAL_SCRIPT))
        define("NET_FIND_SCRIPT", address("FIND_SCRIPT", FIND_SCRIPT))
        for name, default in (("NPC_SET_AI", NPC_SET_AI), ("CREATURE_SET_AI", CREATURE_SET_AI)):
            set_ai = int(address(name, default), 16)
            slots = [image.off_to_va(m.start()) for m in re.finditer(
                re.escape(struct.pack("<I", set_ai)), bytes(image.data))]
            if len(slots) != 1 or None in slots:
                raise PayloadError(f"{name} {hexva(set_ai)}: {len(slots)} vtable slots, "
                                   "expected 1")
            define(f"NET_{name}", hexva(set_ai))
            define(f"NET_{name}_SLOT", hexva(slots[0]))
        drop = int(address("DROP_ITEM", DROP_ITEM), 16)
        player_mobile = int(address("PLAYER_MOBILE", dict(CONSOLE_ADDRESSES)["PLAYER_MOBILE"]), 16)
        data = bytes(image.data)

        def after_player_mobile(site):
            off = image.va_to_off(site - 7)
            if off is None or data[off] != 0xE8 or data[off + 5:off + 7] not in (b"\x8b\xc8",
                                                                                  b"\x89\xc1"):
                return False
            return site - 2 + struct.unpack_from("<i", data, off + 1)[0] == player_mobile

        sites = [s for s in find_call_sites(image, drop) if after_player_mobile(s)]
        if len(sites) != PLAYER_DROPS:
            raise PayloadError(f"dropItem on the player: {len(sites)} call sites, expected "
                               f"{PLAYER_DROPS}")
        define("NET_DROP_ITEM", hexva(drop))
        define("NET_PLAYER_DROP_SITES", "{" + ",".join(hexva(s) for s in sites) + "}")
        save_game = locate("save-game")
        sites = find_call_sites(image, save_game)
        if len(sites) != SAVE_CALLS:
            raise PayloadError(f"SaveGame: {len(sites)} call sites, expected {SAVE_CALLS}")
        define("NET_SAVE_GAME", hexva(save_game))
        off = image.va_to_off(save_game)
        # XCreateSaveGame("U:\", name, OPEN_ALWAYS, 0, path, 0x104), which finds the slot's folder
        calls = list(re.finditer(rb"\x6a\x00\x6a\x04\x8d\x94\x24....\x52\x68....\xe8(....)",
                                 data[off:off + 0x400], re.S))
        if len(calls) != 1:
            raise PayloadError(f"XCreateSaveGame in SaveGame: {len(calls)} calls, expected 1")
        after = save_game + calls[0].end()
        define("NET_CREATE_SAVE", hexva(after + struct.unpack("<i", calls[0].group(1))[0]))
        define("NET_SAVE_SITES", "{" + ",".join(hexva(s) for s in sites) + "}")
        # The engine's relaunches (New Game, Load) call 0x00336DF0, which keeps the picture on
        # screen, then XLaunchNewImage("D:\morrowind.xbe", &launch data).
        launches = set()
        for m in re.finditer(rb"\xe8(....)\x8d\x44\x24.\x50\x68(....)\xe8(....)", data, re.S):
            path = image.va_to_off(struct.unpack("<I", m.group(2))[0])
            if path is None or data[path:path + 17] != b"D:\\morrowind.xbe\0":
                continue
            at = image.off_to_va(m.start())
            launches.add((at + 5 + struct.unpack("<i", m.group(1))[0],
                          struct.unpack("<I", m.group(2))[0],
                          at + m.end() - m.start() + struct.unpack("<i", m.group(3))[0]))
        if len(launches) != 1:
            raise PayloadError(f"title relaunch: {len(launches)} call sequences, expected 1")
        persist, engine_path, launch = launches.pop()
        define("NET_PERSIST_DISPLAY", hexva(persist))
        define("NET_ENGINE_PATH", hexva(engine_path))
        define("NET_LAUNCH", hexva(launch))
        # Exit in the pause menu: MessageBox(sMessage5 (GMST 0x266), Yes, No), then the answer's
        # callback (push 0x20, push callback), which relaunches D:\Default.xbe.
        quits = list(re.finditer(rb"\x68\x66\x02\x00\x00\xe8....\x50\xe8.{0,48}?\x6a\x20\x68(....)"
                                 rb"\x8b\xc8", data, re.S))
        if len(quits) != 1:
            raise PayloadError(f"quit callback: {len(quits)} sites, expected 1")
        quit_site = image.off_to_va(quits[0].start(1))
        quit = struct.unpack("<I", quits[0].group(1))[0]
        body = data[image.va_to_off(quit):image.va_to_off(quit) + 0x30]
        pushed = [struct.unpack_from("<I", body, m.start() + 1)[0]
                  for m in re.finditer(rb"\x68", body)]
        if not any(image.va_to_off(p) is not None and
                   data[image.va_to_off(p):image.va_to_off(p) + 15].lower() == b"d:\\default.xbe\0"
                   for p in pushed):
            raise PayloadError(f"quit callback {quit:#x} does not launch D:\\Default.xbe")
        define("NET_QUIT_SITE", hexva(quit_site))
        define("NET_QUIT", hexva(quit))
        define("NET_MENU_GATE", hexva(locate("menu-mode-gate")))
        define("NET_MOB_GATE", hexva(locate("mob-update-gate")))
        define("NET_WEATHER_ROLL", hexva(locate("weather-roll")))
        define("NET_BUTTON", hexva(locate("button-pressed")))
        # MobilePlayer::onDeath: UpdateFillBar(health bar, current, base) empties the bar; after the
        # engine's death (magic stopped, DataHandler told), the last save's name chooses between
        # "load it?" and the main menu, both ending in the epilogue.
        world_va, handler_va = int(world, 16), int(data_handler, 16)
        deaths = [m for m in re.finditer(rb"\x33\xc0\x66\xa1(....)\x51\x52\x50\xe8(....)\x83\xc4"
                                         rb"\x0c\x8b\xcf\xe8....\x8b\x0d(....)\x8b\x51\x6c\xc6\x42"
                                         rb"\x04\x01\x8b\x0d(....)\xe8....\xa1(....)\x8b\x08\x8b\x71"
                                         rb"\x14\x85\xf6\x0f\x84(....)", data, re.S)
                  if struct.unpack("<3I", m.group(3) + m.group(4) + m.group(5)) ==
                  (world_va, handler_va, handler_va) and b"\x68\x61\x02\x00\x00" in
                  data[m.end():m.end() + 0x80]]
        if len(deaths) != 1:
            raise PayloadError(f"player death menu: {len(deaths)} sites, expected 1")
        site = deaths[0].start(5) - 1
        menu = image.off_to_va(deaths[0].end()) + struct.unpack("<i", deaths[0].group(6))[0]
        if data[image.va_to_off(menu) - 6:image.va_to_off(menu)] != b"\x5f\x5e\x83\xc4\x08\xc3":
            raise PayloadError(f"player death menu {hexva(menu)}: not after onDeath's epilogue")
        fill = image.off_to_va(deaths[0].end(2)) + struct.unpack("<i", deaths[0].group(2))[0]
        # MessageMenu(text, button, ..., 0), as onDeath shows "load it?" (Yes, No)
        boxes = list(re.finditer(rb"\x6a\x44\xe8....\x50\x57\xe8(....)",
                                 data[deaths[0].end():deaths[0].end() + 0x100], re.S))
        if len(boxes) != 1:
            raise PayloadError(f"message menu: {len(boxes)} calls in onDeath, expected 1")
        define("NET_MESSAGE_MENU", hexva(image.off_to_va(deaths[0].end() + boxes[0].end()) +
                                         struct.unpack("<i", boxes[0].group(1))[0]))
        # showMessageBox(text, 0, 1): the toast for the "cannot rest" GMST, which blocks nothing
        toasts = {image.off_to_va(m.end(1)) + struct.unpack("<i", m.group(1))[0]
                  for m in re.finditer(rb"\x6a\x01\x6a\x00\x68\xfa\x01\x00\x00\xe8....\x50\xe8(....)"
                                       rb"\x83\xc4\x0c", data, re.S)}
        if len(toasts) != 1:
            raise PayloadError(f"showMessageBox: {len(toasts)} targets, expected 1")
        define("NET_SHOW_MESSAGE", hexva(toasts.pop()))
        define("NET_DEATH_SITE", hexva(image.off_to_va(site)))
        # New Game's first ini read of [PreLoad] Cell N (the menu's reads a constant "Cell 0"):
        # push key, push "PreLoad", call, add esp 0x18, test eax, je.
        preload = data.find(b"\0PreLoad\0") + 1
        preload_va = image.off_to_va(preload) if preload else None
        reads = list(re.finditer(rb"\x57\x68" + struct.pack("<I", preload_va or 0) +
                                 rb"\xe8....\x83\xc4\x18\x85\xc0\x0f\x84", data, re.S))
        if not preload_va or len(reads) != 1:
            raise PayloadError(f"New Game's PreLoad read: {len(reads)} sites, expected 1")
        define("NET_PRELOAD_SITE", hexva(image.off_to_va(reads[0].start() + 6)))
        # The loop's next step finds the cell by that name: mov edx, [DataHandler]; mov ecx,
        # [edx]; push ebp; call.
        finds = list(re.finditer(rb"\x8b\x15....\x8b\x0a\x55\xe8",
                                 data[reads[0].end():reads[0].end() + 0x60], re.S))
        if len(finds) != 1:
            raise PayloadError(f"New Game's PreLoad cell lookup: {len(finds)} sites, expected 1")
        define("NET_PRELOAD_FIND_SITE", hexva(image.off_to_va(reads[0].end() + finds[0].end() - 1)))
        define("NET_FILL_BAR", hexva(fill))
        define("NET_HEALTH_BAR", hexva(struct.unpack("<I", deaths[0].group(1))[0]))
        define("NET_FIND_MARKER", hexva(locate("find-marker")))
        define("NET_TEMPLE_MARKER", hexva(locate("temple-marker")))
        define("NET_DIVINE_MARKER", hexva(locate("divine-marker")))
        wanted["multiplayer"] = ("_tes3x_multi_frame",)
    if "tes3xagent.c" in names:
        if "tes3xnet.c" not in names:
            raise PayloadError("tes3xagent.c requires tes3xnet.c")
        wanted["agent"] = ("_tes3x_agent_entry",)
    if "tes3xinfoarena.c" in names:
        heap_allocate = hexva(locate("heap-allocate"))
        heap_free = hexva(locate("heap-free"))
        heap_object = hexva(locate("heap-object"))
        names_allocate = hexva(locate("info-names-allocate"))
        names_free = hexva(locate("info-names-free"))
        current_topic = hexva(locate("info-current-topic"))
        link_original = hexva(locate("info-link-original"))
        finish_original = hexva(locate("info-finish-original"))
        print(f"INFO name arena: table {heap_allocate}, names {names_allocate}, "
              f"topic {current_topic}, finish {finish_original}")
        define("INFO_HEAP_ALLOCATE", heap_allocate)
        define("INFO_HEAP_FREE", heap_free)
        define("INFO_HEAP_OBJECT", heap_object)
        define("INFO_NAMES_ALLOCATE", names_allocate)
        define("INFO_NAMES_FREE", names_free)
        define("INFO_CURRENT_TOPIC", current_topic)
        define("INFO_LINK_ORIGINAL", link_original)
        define("INFO_FINISH_ORIGINAL", finish_original)
        wanted["info_table_allocate"] = ("_tes3x_info_table_allocate_hook",)
        wanted["info_names_allocate"] = ("_tes3x_info_names_allocate_hook",)
        wanted["info_names_free"] = ("_tes3x_info_names_free",)
        wanted["info_table_free"] = ("_tes3x_info_table_free@4",)
        wanted["info_cleanup"] = ("_tes3x_info_cleanup_hook",)
        wanted["info_finish"] = ("_tes3x_info_finish_hook",)
    if "tes3xdiag.c" in names:
        update = hexva(locate("diagnostics-update"))
        print(f"diagnostics hook: Game::Update {update}")
        define("DIAG_UPDATE", update)
    if "tes3xrefs.c" in names:
        load, skip = locate("ref-load"), hexva(locate("ref-skip"))
        print(f"refs hook: fallback {hexva(load)}, resume {hexva(load + 6)}, skip {skip}")
        define("REF_RESUME", hexva(load + 6))
        define("REF_SKIP", skip)
        wanted["ref_load"] = ("_tes3x_ref_load_hook",)
    if "tes3xmcp37.c" in names:
        site, game, player, tree = find_mcp37_context(image)
        print(f"mcp-37 hook: cell change {hexva(site)}, game {hexva(game)}, "
              f"player {hexva(player)}, tree iterator {hexva(tree)}")
        define("MCP37_GAME", hexva(game))
        define("MCP37_GET_PLAYER", hexva(player))
        define("MCP37_TREE_NEXT", hexva(tree))
        wanted["mcp37"] = ("_tes3x_mcp37_hook",)
    if "tes3xmcp97.c" in names:
        scan = locate("mcp-97-scan")
        print(f"mcp-97 hook: scan {hexva(scan)}, resume {hexva(scan + 6)}")
        define("MCP97_RESUME", hexva(scan + 6))
        wanted["mcp97_scan"] = ("_tes3x_mcp97_scan_hook",)
    if "tes3xtest_mcp97.c" in names:
        scan = locate("mcp-97-scan")
        landing = scan + 10
        print(f"mcp-97 test probe: landing {hexva(landing)}")
        define("MCP97_TEST_LOOP", hexva(scan - 47))
        define("MCP97_TEST_EXIT", hexva(landing + 5))
        wanted["mcp97_test"] = ("_tes3x_mcp97_test_hook",)
        extra["mcp97_test"] = {"mcp97_test_site": hexva(landing)}
    if "tes3xtest_mcp102.c" in names:
        setter = locate("mcp-102-actn")
        sites = find_call_sites(image, setter)
        if len(sites) != 1:
            raise PayloadError(f"mcp-102 test probe: {len(sites)} ACTN load calls, expected 1")
        print(f"mcp-102 test probe: setter {hexva(setter)}, call {hexva(sites[0])}")
        define("MCP102_TEST_SET_FLAGS", hexva(setter))
        define("MCP102_TEST_GET_FLAGS", hexva(setter - 0x60))
        wanted["mcp102_test"] = ("@tes3x_mcp102_test_hook@8",
                                 "_tes3x_mcp102_test_hook")
        extra["mcp102_test"] = {"mcp102_test_site": hexva(sites[0])}
    if "tes3xtest_mcp3.c" in names:
        if "tes3xconsole.c" not in names:
            raise PayloadError("tes3xtest_mcp3.c requires tes3xconsole.c")
        print("mcp-3 test probe: console command tes3xmcp3")
        wanted["mcp3_test"] = ("_tes3x_mcp3_test_command",)
    if "tes3xtest_dialoguemerge.c" in names:
        if "tes3xconsole.c" not in names:
            raise PayloadError("tes3xtest_dialoguemerge.c requires tes3xconsole.c")
        print("dialogue-merge test probe: console command tes3xdial TOPIC")
        define("DIALMERGE_DATA_HANDLER", address("DATA_HANDLER", DATA_HANDLER))
        wanted["dialmerge_test"] = ("_tes3x_dialmerge_test_command",)
    if "tes3xmcp154.c" in names:
        load, reload = locate("mcp-154-load"), locate("mcp-154-reload")
        print(f"mcp-154 hooks: load {hexva(load)}, reload {hexva(reload)}")
        define("MCP154_LOAD_RESUME", hexva(load + 6))
        define("MCP154_RELOAD_RESUME", hexva(reload + 6))
        wanted["mcp154_load"] = ("_tes3x_mcp154_load_hook",)
        wanted["mcp154_reload"] = ("_tes3x_mcp154_reload_hook",)
    if "tes3xmcp123.c" in names:
        site = locate("mcp-123")
        add_reference = tes3x_inject.call_target(image, site)
        print(f"mcp-123 hook: PlaceItem call {hexva(site)}, "
              f"Cell::addReference {hexva(add_reference)}")
        define("MCP123_ADD_REFERENCE", hexva(add_reference))
        wanted["mcp123_add"] = ("_tes3x_mcp123_add_hook",)
    if "tes3xmcp125.c" in names:
        context = find_mcp125_context(image)
        print(f"mcp-125 hook: collision add {hexva(context['add'])}, "
              f"remove {hexva(context['remove'])}")
        define("MCP125_ADD_MOB", hexva(context["add"]))
        define("MCP125_REMOVE_MOB", hexva(context["remove"]))
        wanted["mcp125_collision"] = ("_tes3x_mcp125_collision_hook",)
    if "tes3xdxt5.c" in names:
        site = locate("dxt5-size")
        size = hexva(tes3x_inject.call_target(image, site))
        print(f"dxt5-size hook: call {hexva(site)}, size function {size}")
        define("TEXTURE_SIZE", size)
        wanted["dxt5_size"] = ("_tes3x_dxt5_size_hook",)
    if "tes3xmcp146.c" in names:
        site = locate("mcp-146")
        input_action = locate("mcp-146-input")
        game = locate("mcp-146-game")
        resume = locate("mcp-146-resume")
        attacking = tes3x_inject.call_target(image, site)
        print(f"mcp-146 hook: input guard {hexva(site)}, attacking {hexva(attacking)}, "
              f"input {hexva(input_action)}, resume {hexva(resume)}")
        define("MCP146_ATTACKING", hexva(attacking))
        define("MCP146_INPUT", hexva(input_action))
        define("MCP146_GAME", hexva(game))
        define("MCP146_RESUME", hexva(resume))
        wanted["mcp146"] = ("_tes3x_mcp146_hook",)
    if "tes3xrefindex.c" in names:
        find = hexva(locate("ref-index-find"))
        scripts = hexva(locate("ref-index-scripts"))
        print(f"ref-index hooks: Cell::findReferenceToObject {find}, startGlobalScripts {scripts}")
        define("REFINDEX_FIND_IN_CELL", find)
        define("REFINDEX_START_SCRIPTS", scripts)
        wanted["refindex_find"] = ("_tes3x_refindex_find_hook",)
        wanted["refindex_scripts"] = ("_tes3x_refindex_scripts_hook",)
    if "tes3xbowview.c" in names:
        site = locate("bow-view")
        update = tes3x_inject.call_target(image, site)
        print(f"bow-view hook: transform call {hexva(site)}, update {hexva(update)}")
        define("BOW_VIEW_UPDATE", hexva(update))
        wanted["bow_view"] = ("_tes3x_bow_view_hook",)
    if "tes3xvideo.c" in names:
        renderer_site, create_site, count, enum = locate("hd-video")
        renderer = tes3x_inject.call_target(image, renderer_site)
        create = tes3x_inject.call_target(image, create_site)
        print(f"hd-video hook: renderer call {hexva(renderer_site)} -> {hexva(renderer)}, "
              f"CreateDevice call {hexva(create_site)} -> {hexva(create)}, "
              f"modes {hexva(count)}, {hexva(enum)}")
        define("VIDEO_RENDERER_CREATE", hexva(renderer))
        define("VIDEO_CREATE_DEVICE", hexva(create))
        define("VIDEO_MODE_COUNT", hexva(count))
        define("VIDEO_ENUM_MODES", hexva(enum))
        wanted["video_renderer"] = ("_tes3x_video_renderer_hook",)
        wanted["video_create"] = ("_tes3x_video_create_hook",)
    if "tes3xbootclear.c" in names:
        site, clear, display = locate("boot-clear")
        nxt = tes3x_inject.call_target(image, site)
        print(f"boot-clear hook: call {hexva(site)} -> {hexva(nxt)}, clear {hexva(clear)}, "
              f"display {hexva(display)}")
        define("BOOTCLEAR_CLEAR", hexva(clear))
        define("BOOTCLEAR_DISPLAY", hexva(display))
        define("BOOTCLEAR_NEXT", hexva(nxt))
        wanted["bootclear"] = ("_tes3x_bootclear_hook",)
    if "tes3xdashbutton.c" in names:
        site, height_site = locate("dash-button")
        build = tes3x_inject.call_target(image, site)
        define("DASHBTN_SET_HEIGHT", hexva(tes3x_inject.call_target(image, height_site)))
        print(f"dash-button hook: MenuOptions::build call {hexva(site)} -> {hexva(build)}")
        define("DASHBTN_BUILD", hexva(build))
        for name in ("UI_ID", "FIND_CHILD", "FIND_MENU", "SET_PROP", "PERFORM_LAYOUT",
                     "NAV_UP_ID", "NAV_DOWN_ID"):
            define("DASHBTN_" + name, address(name, dict(CONSOLE_ADDRESSES)[name]))
        for name in ("CREATE_BLOCK", "CREATE_IMAGE", "SET_WIDTH", "SET_VISIBLE"):
            define("DASHBTN_" + name, address(name, dict(NET_UI_ADDRESSES)[name]))
        wanted["dashbutton"] = ("_tes3x_dashbutton_hook",)
        wanted["dashbutton_height"] = ("_tes3x_dashbutton_height_hook",)
    if "tes3xmenustill.c" in names:
        menu, new_game, load, slot, clear, display, fence, block = locate("menu-still")
        define("STILL_FENCE", hexva(tes3x_inject.call_target(image, fence)))
        define("STILL_BLOCK", hexva(tes3x_inject.call_target(image, block)))
        opened = tes3x_inject.call_target(image, menu)
        print(f"menu-still hook: open {hexva(opened)} from {hexva(menu)}, {hexva(new_game)}, "
              f"{hexva(load)}; clear slot {hexva(slot)}, clear {hexva(clear)}, "
              f"display {hexva(display)}")
        define("STILL_OPEN", hexva(opened))
        define("STILL_CLEAR", hexva(clear))
        define("STILL_DISPLAY", hexva(display))
        wanted["still_open"] = ("_tes3x_still_open_hook",)
        wanted["still_end"] = ("_tes3x_still_end_hook",)
        wanted["still_clear"] = ("_tes3x_still_clear_hook",)
    if "tes3xleanmenu.c" in names:
        site, tag, launch, no_reboot, preload, player = locate("lean-menu")
        load = tes3x_inject.call_target(image, site)
        by_name = tes3x_inject.call_target(image, preload)
        create = tes3x_inject.call_target(image, player)
        print(f"lean-menu hook: call {hexva(site)}, record load {hexva(load)}, tag {hexva(tag)}, "
              f"launch info {hexva(launch)}, no reboot {hexva(no_reboot)}, "
              f"PreLoad lookup {hexva(preload)} -> {hexva(by_name)}, "
              f"create player {hexva(player)} -> {hexva(create)}")
        define("LEAN_CREATE_PLAYER", hexva(create))
        define("LEAN_LOAD_RECORD", hexva(load))
        define("LEAN_RECORD_TAG", hexva(tag))
        define("LEAN_CELL_BY_NAME", hexva(by_name))
        define("LEAN_LAUNCH_INFO", hexva(launch))
        define("LEAN_NO_REBOOT", hexva(no_reboot))
        wanted["lean_record"] = ("_tes3x_lean_record_hook",)
        wanted["lean_preload"] = ("_tes3x_lean_preload_hook",)
        wanted["lean_player"] = ("_tes3x_lean_player_hook",)
    if "tes3xarena.c" in names:
        print(f"video-arena hook: size {hexva(locate('video-arena'))}")
        wanted["arena_size"] = ("_tes3x_arena_size_hook",)
    if "tes3xregion.c" in names:
        malloc, fit, free = locate("heap-region-malloc"), locate("heap-region-fit"), \
            locate("heap-region-free")
        print(f"heap-region hooks: size {hexva(locate('heap-region'))}, malloc {hexva(malloc)}, "
              f"fit {hexva(fit)}, free {hexva(free)}")
        define("REGION_MALLOC", hexva(tes3x_inject.call_target(image, malloc)))
        define("REGION_FREE", hexva(tes3x_inject.call_target(image, free)))
        define("REGION_SPILL", hexva(fit + 6))
        define("REGION_CARVE", hexva(fit + 6 + struct.unpack_from(
            "<i", image.data, image.va_to_off(fit) + 2)[0]))
        wanted["region_size"] = ("_tes3x_region_size_hook",)
        wanted["region_reserve"] = ("@tes3x_region_reserve@12",)
        wanted["region_carve"] = ("_tes3x_region_carve_hook",)
        wanted["region_release"] = ("@tes3x_region_release@12",)
    if "tes3xconsole.c" in names:
        print("console hook: keyboard and print sites located by content")
        for name, default in CONSOLE_ADDRESSES:
            define(name, address(name, default))
        console_print = locate("console-print")
        vsprintf = tes3x_inject.call_target(image, console_print + CONSOLE_PRINT_VSPRINTF)
        print(f"console output: printf {hexva(console_print)}, vsprintf {hexva(vsprintf)}")
        define("CONSOLE_PRINT", hexva(console_print))
        define("VSPRINTF", hexva(vsprintf))
        wanted["console_gate"] = ("@tes3x_console_hook@12", "_tes3x_console_hook")
        wanted["console_vk_key"] = ("_tes3x_vk_key_limit",)
        wanted["console_vk_space"] = ("_tes3x_vk_space_limit",)
        wanted["console_print"] = ("_tes3x_console_print",)
        wanted["console_mailbox"] = ("_tes3x_mailbox",)
    if "tes3xdiag.c" in names:
        wanted["diagnostics_update"] = ("_tes3x_diag_update_hook",)
        wanted["diagnostics_flag"] = ("_tes3x_diag_installed",)
        wanted["patch_mask"] = ("_tes3x_patch_mask",)
        wanted["patch_mask_hi"] = ("_tes3x_patch_mask_hi",)
    if "tes3xprof.c" in names:
        wanted["prof_target"] = ("_tes3x_prof_target",)
        wanted["prof_stubs"] = ("_tes3x_prof_stubs",)
        extra["prof_stubs"] = {"prof_count": define_value("tes3xprof.c", "TES3X_PROF_SLOTS")}
    if "tes3xsaves.c" in names:
        wanted["autosave"] = ("@tes3x_autosave_hook@12", "_tes3x_autosave_hook")
        wanted["transition_cell"] = ("_tes3x_transition_cell_hook",)
        wanted["transition_cell_companions"] = ("_tes3x_transition_cell_companions_hook",)
        wanted["transition_teleport"] = ("_tes3x_transition_teleport_hook",)
        wanted["transition_travel"] = ("_tes3x_transition_travel_hook",)
    if "tes3xoverlay.c" in names:
        wanted["overlay_flag"] = ("_tes3x_overlay_installed",)
    if "tes3xprefs.c" in names:
        wanted["preferences_load"] = ("@tes3x_preferences_hook@4",
                                      "_tes3x_preferences_hook")


    for name, (_array, items) in values.items():
        if 0 in items:
            raise PayloadError(f"engine value {name} is 0")
    return Bindings(image, sources, values, wanted, extra, arch_site)


# A value read from an instruction the linker fills: to the compiler an opaque number, as a
# compile-time constant was, so no object is assumed behind it, its memory may change and be
# written, and two values may overlap.
VALUE_MACRO = ('#ifndef TES3X_VALUE\n'
               '#define TES3X_SYM(name) "_%s" #name\n'
               '#define TES3X_VALUE(name) __extension__ ({ unsigned long tes3x_value_; '
               '__asm__("movl $" TES3X_SYM(name) ", %%0" : "=r"(tes3x_value_)); '
               'tes3x_value_; })\n'
               '#endif\n\n' % ABSOLUTE)


def binding_header(path, layout):
    """Name every engine value as a symbol the linker resolves, and the payload's own link-time
    words."""
    with open(path, "w", encoding="utf-8", newline="\n") as stream:
        stream.write("/* generated by tes3x.payload - values the linker supplies per XBE */\n")
        stream.write("#ifndef TES3X_BINDINGS_H\n#define TES3X_BINDINGS_H\n\n")
        stream.write(VALUE_MACRO)
        stream.write("#define TES3X_OPTIONAL __attribute__((weak))\n\n")
        for name in WORDS:
            stream.write(f"extern const unsigned long {WORD}{name};\n")
            stream.write(f"#define TES3X_{name} ({WORD}{name})\n")
        for name, count in sorted(layout.items()):
            if count is None:
                stream.write(f"#define TES3X_{name} TES3X_VALUE({name})\n")
                continue
            items = ",".join(f"TES3X_VALUE({name}_{i})" for i in range(count))
            stream.write(f"#define TES3X_{name} {{{items}}}\n")
        stream.write("\n#endif\n")


def thunk_header(path):
    """Name each kernel import; its slot in the host XBE is a link-time symbol."""
    names = tes3x_inject.kernel_names(tes3x_inject.DEFAULT_KRNL_DEF)
    with open(path, "w", encoding="utf-8", newline="\n") as stream:
        stream.write("/* generated by tes3x.payload - host slots are link-time symbols */\n")
        stream.write("#ifndef TES3X_THUNKS_H\n#define TES3X_THUNKS_H\n\n")
        stream.write(VALUE_MACRO)
        for _ordinal, name in sorted(names.items()):
            stream.write(f"#define THUNK_{name} TES3X_VALUE(THUNK_{name})\n")
        stream.write("\n#endif\n")


def source_digest(name):
    """What a source's object depends on: compiler flags, the generated headers' format, the
    source with its sections and every shared header."""
    digest = hashlib.sha256()
    digest.update(repr((CFLAGS, HEADER_FORMAT)).encode("ascii"))
    path = source_path(name)
    files = [path] + [path.parent / section for section in section_names(path)]
    files += sorted(HOOKS.glob("*.h")) + [Path(tes3x_inject.DEFAULT_KRNL_DEF)]
    for file in files:
        # A checkout with CRLF line endings compiles to the same object
        text = file.read_bytes().replace(b"\r\n", b"\n")
        digest.update(file.name.encode("utf-8") + b"\0" + text)
    return digest.hexdigest()


def shipped_objects():
    """The bundled object index, or an empty one."""
    index = OBJECTS / "index.json"
    if not index.is_file():
        return {"schema": OBJECTS_SCHEMA, "layout": {}, "objects": {}}
    data = json.loads(index.read_text(encoding="utf-8"))
    if data.get("schema") != OBJECTS_SCHEMA:
        raise PayloadError("unsupported payload object index")
    return data


def shipped_object(name, index=None):
    """A bundled object still current with its source, or None."""
    entry = (index or shipped_objects())["objects"].get(Path(name).name)
    if entry is None or Path(name).name != name:
        return None
    path = OBJECTS / (Path(name).stem + ".obj")
    if not path.is_file() or entry["digest"] != source_digest(name):
        return None
    if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
        raise PayloadError(f"bundled payload object {path.name}: SHA-256 mismatch")
    return path


def compile_objects(sources, out, layout, user_flags="", llvm_dir=None):
    """Compile sources with clang into out. Returns {source: object path}."""
    clang = os.environ.get("CLANG") or find_tool("clang", llvm_dir)
    out.mkdir(parents=True, exist_ok=True)
    # Generated into the build, never into hooks/, which an install may not let us write.
    thunk_header(out / "tes3x_thunks.h")
    binding_header(out / "tes3x_bindings.h", layout)
    objects = {}
    for source in sources:
        obj = out / (Path(source).stem + ".obj")
        # Monocypher is vendored whole; the linker keeps only the functions the payload calls.
        sections = ["-ffunction-sections", "-fdata-sections"] if source == "monocypher.c" else []
        subprocess.run([clang, *CFLAGS, *sections, *user_flags.split(),
                        "-include", str(out / "tes3x_bindings.h"), f"-I{out}", f"-I{HOOKS}",
                        "-c", str(source_path(source)), "-o", str(obj)], check=True)
        objects[source] = obj
    return objects


def production_sources():
    """Every source a patch selection can link: the patch table's and those they need."""
    import tes3x.patches as registry

    sources = list(DEFAULT_SOURCES)
    for entry in registry.PATCHES:
        for source in (entry.get("source"), *SOURCE_DEPENDENCIES.get(entry.get("source"), ())):
            if source and source not in sources:
                sources.append(source)
    return expand_sources(sources)


def sources_to_compile(sources, user_flags="", rebuild=False):
    """The sources that have no current bundled object for this compile."""
    if rebuild or user_flags.strip():
        return expand_sources(sources)
    index = shipped_objects()
    return [source for source in expand_sources(sources) if not shipped_object(source, index)]


def object_files(bindings, out, user_flags="", llvm_dir=None, rebuild=False):
    """An object for each source: the bundled one when it is current and no flags change the
    compile, otherwise a fresh compile."""
    index = shipped_objects()
    objects, stale = {}, []
    for source in bindings.sources:
        found = None if rebuild or user_flags.strip() else shipped_object(source, index)
        if found:
            objects[source] = found
        else:
            stale.append(source)
    if stale:
        layout = dict(index.get("layout", {}))
        layout.update(bindings.layout())
        try:
            objects.update(compile_objects(stale, out, layout, user_flags, llvm_dir))
        except PayloadError as exc:
            raise PayloadError(f"{exc}\n(compiling {', '.join(stale)}: no current bundled "
                               "object)") from exc
    else:
        for name, count in bindings.layout().items():
            shipped = index["layout"].get(name, "missing")
            if shipped != count:
                raise PayloadError(f"engine value {name} has shape {count}, bundled objects "
                                   f"expect {shipped}")
    return [objects[source] for source in bindings.sources]


def build_id(objects, user_flags):
    """The ID a server matches consoles by: the linked objects and the compile flags."""
    digest = hashlib.sha256()
    digest.update(b"flags\0" + user_flags.encode("utf-8") + b"\0")
    for obj in objects:
        digest.update(Path(obj).name.encode("utf-8") + b"\0" + Path(obj).read_bytes())
    return int(digest.hexdigest()[:8], 16)


def link_payload(bindings, objects, out, ident, invert_look=True):
    """Link objects at the XBE's next section VA. Returns the payload and manifest paths."""
    image = bindings.image
    base = image.next_va()
    absolute = {f"_{ABSOLUTE}ORIG_ENTRY": image.entry}
    names = tes3x_inject.kernel_names(tes3x_inject.DEFAULT_KRNL_DEF)
    for i, ordinal in enumerate(image.thunk_ordinals()):
        if ordinal in names:
            absolute[f"_{ABSOLUTE}THUNK_{names[ordinal]}"] = image.thunk + i * 4
    for name, (array, items) in bindings.values.items():
        if array:
            for i, value in enumerate(items):
                absolute[f"_{ABSOLUTE}{name}_{i}"] = value
        else:
            absolute[f"_{ABSOLUTE}{name}"] = items[0]
    words = {f"_{WORD}BUILD_ID": ident, f"_{WORD}INVERT_LOOK": int(bool(invert_look))}
    print(f"section VA {hexva(base)}   original entry {hexva(image.entry)}   "
          f"payload build id 0x{ident:08x}")
    try:
        linked = link([(Path(obj).name, Path(obj).read_bytes()) for obj in objects], base, ENTRY,
                      absolute, words)
    except LinkError as exc:
        missing = re.findall(r"_%sTHUNK_(\w+)" % ABSOLUTE, str(exc))
        if missing:
            raise PayloadError("XBE has no kernel thunk for " + ", ".join(missing)) from exc
        raise PayloadError(str(exc)) from exc
    out.mkdir(parents=True, exist_ok=True)
    payload, map_path = out / "tes3xhook.pe", out / "tes3xhook.map"
    write_pe(linked, payload)
    write_map(linked, map_path)
    link_map = map_path.read_text(encoding="utf-8").splitlines()
    hooks = {}
    for key, symbols in bindings.wanted.items():
        hooks[key] = "0x%08X" % int(link_symbol(link_map, *symbols), 16)
        hooks.update(bindings.extra.get(key, {}))
    # Beside the payload, so tes3x patch can apply it without the map.
    manifest = out / "tes3xhook.json"
    with open(manifest, "w", encoding="utf-8", newline="\n") as stream:
        json.dump({"base": base, "payload_hooks": list(bindings.wanted), "hooks": hooks},
                  stream, indent=2)
    print(f"payload {linked.vsize - 0x200:,} bytes from {len(objects)} objects")
    return payload, manifest


def build_payload(xbe, sources=DEFAULT_SOURCES, out=HOOKS.parent / "build" / "hooks",
                  user_flags="", llvm_dir=None, check_xbe=None, forced_build_id=None,
                  invert_look=True, rebuild=False):
    """Link a payload of sources for xbe. Returns the paths of the payload and its manifest."""
    out = Path(out)
    bindings = engine_bindings(xbe, sources)
    objects = object_files(bindings, out, user_flags, llvm_dir, rebuild)
    ident = int(forced_build_id, 16) if forced_build_id else build_id(objects, user_flags)
    payload, manifest = link_payload(bindings, objects, out, ident, invert_look)
    if check_xbe:
        hooks = json.loads(manifest.read_text(encoding="utf-8"))["hooks"]
        calls = [(bindings.arch_site, int(hooks["archive_load"], 16))] \
            if "archive_load" in hooks else []
        tes3x_inject.inject(xbe, payload, check_xbe, hook_entry=True, patch_calls=calls)
    return payload, manifest


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("xbe", help="clean retail morrowind.xbe")
    ap.add_argument("--src", action="append", metavar="FILE.c",
                    help="payload source, in hooks/ or a path (repeatable; default: %s)"
                         % " ".join(DEFAULT_SOURCES))
    ap.add_argument("--out", default=str(checkout("build", "hooks")), help="output folder")
    ap.add_argument("--cflags", default=os.environ.get("EXTRA_CFLAGS", ""),
                    help="extra compiler flags, such as -DNAME=VALUE; compiles every object")
    ap.add_argument("--llvm", help="folder holding clang (default: search PATH)")
    ap.add_argument("--rebuild", action="store_true",
                    help="compile every object even when a bundled one is current")
    ap.add_argument("--no-invert-look", dest="invert_look", action="store_false",
                    help="leave the look axis as the engine reads it (with tes3xprefs.c)")
    ap.add_argument("--check", metavar="OUT.xbe",
                    help="also inject the payload into a copy of the XBE, as a structural check")
    a = ap.parse_args(argv)
    try:
        build_payload(a.xbe, a.src or DEFAULT_SOURCES, a.out, a.cflags, a.llvm, a.check,
                      invert_look=a.invert_look, rebuild=a.rebuild)
    except (PayloadError, ValueError, subprocess.CalledProcessError) as exc:
        raise SystemExit(str(exc))


if __name__ == "__main__":
    main()
