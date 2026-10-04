#!/usr/bin/env python3
"""Compile the injected payload for one retail XBE and write its hook manifest.

The payload is linked at the exact VA the new XBE section will land at, so absolute references
resolve without relocations. Needs clang and lld-link from any LLVM install.
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

import tes3x_inject
from tes3x_patch import (CONSOLE_PRINT_VSPRINTF, LOCATORS, find_call_sites, find_mcp37_context,
                         find_mcp125_context, find_save_allowed_context,
                         find_script_ip_restore_call, find_transition_calls)

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / "hooks"
HEADERS = ("tes3xdiag.h", "tes3xheap.h", "tes3xlog.h", "tes3xmem.h", "tes3xnt.h",
           "tes3xpager.h", "tes3xprof.h", "tes3xregion.h", "tes3x_thunks.h", "monocypher.h",
           "tes3xnoise.h")
DEFAULT_SOURCES = ("tes3xhook.c", "tes3xlog.c", "tes3xdiag.c")
BUNDLED_LLVM = ROOT / "externals" / "llvm" / "bin"
LLVM_DIRS = (Path("C:/Program Files/LLVM/bin"), Path("C:/msys64/clang64/bin"),
             Path("C:/msys64/mingw64/bin"))
CFLAGS = ("-target", "i386-pc-win32", "-march=pentium3", "-Os", "-ffreestanding", "-nostdlib",
          "-fno-builtin", "-fno-stack-protector", "-fno-asynchronous-unwind-tables")

# The archive hook replaces the call to Archive::Load at this site.
ARCH_SITE = 0x000D4E06
INI_GET = 0x001933E0
INI_PATH = 0x0035E364
DATA_HANDLER = 0x003CB5F8
CONSOLE_SITE = 0x00098430
SERVICE_ACTOR = 0x001CD530  # ui::getServiceActor: the MenuDialog partner's mobile, or 0
# What the SetPos, SetAngle and PlayGroup handlers call, to place and animate a reference without
# the script compiler.
PLACE_ADDRESSES = (
    ("FIND_REFERENCE", 0x00109CD0), ("REF_ANIMATION", 0x0012A160), ("REF_ORIENTATION", 0x0012C3D0),
    ("REF_ROTATION", 0x0012AFE0), ("NODE_SET_ROTATION", 0x00042C40), ("NODE_UPDATE", 0x00044170),
    ("ANIM_HAS_GROUP", 0x000CC960), ("ANIM_PLAY_GROUP", 0x000CE5C0),
    ("BODY_PART_UPDATE", 0x000D0CF0),
    ("UNREADY_WEAPON", 0x00158560), ("MOBILE_HANDS", 0x0015BAF0),
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
SAVE_CALLS = 7  # SaveGame's callers: quicksave, three autosaves, the save menu's two, fatal error
ACTOR_ADDRESSES = (
    ("LEVELED_RESOLVE", 0x0011A740), ("LEVELED_LINKED", 0x0012AEE0),
    ("LEVELED_LINK", 0x0012A370), ("ADD_MOB", 0x001840B0), ("SIMULATE", 0x00180630),
    ("START_COMBAT", 0x001629F0),
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
    ("CREATE_BLOCK", 0x001A7240), ("CREATE_IMAGE", 0x001A7080), ("SET_WIDTH", 0x00198980),
    ("SET_HEIGHT", 0x00198A60), ("SET_VISIBLE", 0x001A63C0), ("CREATE_LABEL", 0x001A7300),
    ("SET_FOCUS", 0x001AF6E0), ("SET_AUTO_WIDTH", 0x00197710), ("CREATE_NIF", 0x001A7170),
    ("SCROLL_PANE", 0x002357B0),  # the factory registered as PartScrollPaneVert
    ("GET_FOCUS", 0x0019B700), ("SCROLL_TO", 0x00234300),
    # MenuOptions' Save, Load and Exit container ids
    ("SAVE_ID", 0x003D9ECC), ("LOAD_ID", 0x003DA018), ("EXIT_ID", 0x003DA05C),
)
INI_USERS = {"tes3xconsole.c", "tes3xrefs.c", "tes3xdiag.c", "tes3xsaves.c", "tes3xprof.c",
             "tes3xarena.c", "tes3xregion.c", "tes3xbowview.c", "tes3xnet.c",
             "tes3xmulti.c", "tes3xagent.c"}


class PayloadError(RuntimeError):
    pass


def hexva(value):
    return "0x%08X" % value


def address(name, default):
    """A fixed engine address, overridable from the environment for research builds."""
    return os.environ.get(name, hexva(default))


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


def define_value(source, name):
    match = re.search(r"^#define %s +(.*)$" % name, source_path(source).read_text(), re.M)
    if not match:
        raise PayloadError(f"{source} does not define {name}")
    return match.group(1)


def build_id(sources, user_flags):
    digest = hashlib.sha256()
    digest.update(b"flags\0" + user_flags.encode("utf-8") + b"\0")
    files = {Path(s).name: source_path(s) for s in sources}
    files.update({name: HOOKS / name for name in HEADERS})
    files[Path(__file__).name] = Path(__file__)
    for name in sorted(files):
        if files[name].exists():
            digest.update(name.encode("ascii") + b"\0" + files[name].read_bytes())
    return "0x" + digest.hexdigest()[:8]


def link_symbol(link_map, *names):
    """A symbol's address from the lld-link map, 16 hex digits, trying each spelling in turn."""
    for name in names:
        for line in link_map:
            fields = line.split()
            if len(fields) >= 3 and fields[1] == name:
                return "0x" + fields[-2]
    raise PayloadError(f"could not find {names[0]} in the link map")


def build_payload(xbe, sources=DEFAULT_SOURCES, out=HOOKS.parent / "build" / "hooks",
                  user_flags="", llvm_dir=None, check_xbe=None, forced_build_id=None):
    """Compile and link sources against xbe. Returns the paths of the payload and its manifest."""
    xbe, out = Path(xbe), Path(out)
    sources = list(sources)
    names = {Path(s).name for s in sources}
    if "tes3xnet.c" in names:
        for extra_source in ("monocypher.c", "tes3xnoise.c", "tes3xcrt.c"):
            if extra_source not in names:
                sources.append(extra_source)
                names.add(extra_source)
    clang, lld = (os.environ.get("CLANG") or find_tool("clang", llvm_dir),
                  os.environ.get("LLD") or find_tool("lld-link", llvm_dir))
    out.mkdir(parents=True, exist_ok=True)

    image = tes3x_inject.Xbe(xbe.read_bytes())
    va = hexva(image.next_va())
    entry = hexva(image.entry)
    print(f"section VA {va}   original entry {entry}")
    tes3x_inject.write_thunks(image, HOOKS / "tes3x_thunks.h", tes3x_inject.DEFAULT_KRNL_DEF)

    ident = forced_build_id or build_id(sources, user_flags)
    print(f"payload build id {ident}")
    flags = user_flags.split() + [f"-DTES3X_BUILD_ID={ident}"]

    def define(name, value):
        flags.append(f"-DTES3X_{name}={value}")

    def locate(name):
        return LOCATORS[name](image)

    wanted, extra = {}, {}
    arch_site = int(address("ARCH_SITE", ARCH_SITE), 16)
    if "tes3xarch.c" in names:
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
        flags.append("-DTES3X_MWSE")
        wanted["mwse_fixup"] = ("@tes3x_mwse_fixup_hook@8", "_tes3x_mwse_fixup_hook")
    if names & INI_USERS:
        ini_get, ini_path = address("INI_GET", INI_GET), address("INI_PATH", INI_PATH)
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
        flags.append("-DTES3X_SAVES")
    if "tes3xprefs.c" in names:
        site = locate("preferences-load")
        load = hexva(tes3x_inject.call_target(image, site))
        table = hexva(locate("controls-table"))
        print(f"preferences hook: controls load {load}, bindings {table}")
        define("CONTROLS_LOAD", load)
        define("CONTROLS_TABLE", table)
    if "tes3xprof.c" in names:
        print("profiler: RDTSC region timing, targets chosen at patch time")
        flags.append("-DTES3X_PROFILE")
    if "tes3xheap.c" in names:
        allocate, free = hexva(locate("heap-allocate")), hexva(locate("heap-free"))
        heap = hexva(locate("heap-object"))
        print(f"heap census: Allocate {allocate}, Free {free}, heap {heap}")
        define("HEAP_ALLOCATE", allocate)
        define("HEAP_FREE", free)
        define("HEAP_OBJECT", heap)
        flags.append("-DTES3X_HEAP")
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
        flags.append("-DTES3X_MEM")
        wanted["mem_heap_alloc"] = ("_tes3x_mem_heap_alloc@12",)
        wanted["mem_heap_free"] = ("_tes3x_mem_heap_free@12",)
    if "tes3xpager.c" in names:
        print("pager: synthetic test on the console command tes3xpager")
        flags.append("-DTES3X_PAGER")
    if "tes3xnet.c" in names:
        if "tes3xdiag.c" not in names:
            raise PayloadError("tes3xnet.c requires tes3xdiag.c for its frame hook")
        flags.append("-DTES3X_NET")
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
        for name, default in (PLACE_ADDRESSES + SPELL_ADDRESSES + SPAWN_ADDRESSES
                              + CONTAINER_ADDRESSES + ACTOR_ADDRESSES):
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
        define("NET_DEATH_SITE", hexva(image.off_to_va(site)))
        define("NET_FILL_BAR", hexva(fill))
        define("NET_HEALTH_BAR", hexva(struct.unpack("<I", deaths[0].group(1))[0]))
        define("NET_FIND_MARKER", hexva(locate("find-marker")))
        define("NET_TEMPLE_MARKER", hexva(locate("temple-marker")))
        define("NET_DIVINE_MARKER", hexva(locate("divine-marker")))
        flags.append("-DTES3X_MULTIPLAYER")
        wanted["multiplayer"] = ("_tes3x_multi_frame",)
    if "tes3xagent.c" in names:
        if "tes3xnet.c" not in names:
            raise PayloadError("tes3xagent.c requires tes3xnet.c")
        flags.append("-DTES3X_AGENT")
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
        flags.append("-DTES3X_INFO_ARENA")
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
        flags.append("-DTES3X_DIAGNOSTICS")
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
        flags.append("-DTES3X_MCP3_TEST")
        wanted["mcp3_test"] = ("_tes3x_mcp3_test_command",)
    if "tes3xtest_dialoguemerge.c" in names:
        if "tes3xconsole.c" not in names:
            raise PayloadError("tes3xtest_dialoguemerge.c requires tes3xconsole.c")
        print("dialogue-merge test probe: console command tes3xdial TOPIC")
        flags.append("-DTES3X_DIALMERGE_TEST")
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
        flags.append("-DTES3X_REGION")
        wanted["region_size"] = ("_tes3x_region_size_hook",)
        wanted["region_reserve"] = ("@tes3x_region_reserve@12",)
        wanted["region_carve"] = ("_tes3x_region_carve_hook",)
        wanted["region_release"] = ("@tes3x_region_release@12",)
    if "tes3xconsole.c" in names:
        print(f"console hook: gate {address('CONSOLE_SITE', CONSOLE_SITE)}")
        for name, default in CONSOLE_ADDRESSES:
            define(name, address(name, default))
        flags.append("-DTES3X_CONSOLE")
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
        flags.append("-DTES3X_OVERLAY")
        wanted["overlay_flag"] = ("_tes3x_overlay_installed",)
    if "tes3xprefs.c" in names:
        wanted["preferences_load"] = ("@tes3x_preferences_hook@4",
                                      "_tes3x_preferences_hook")

    objects = []
    for source in sources:
        obj = out / (Path(source).stem + ".obj")
        # Monocypher is vendored whole; the linker keeps only the functions the payload calls.
        sections = ["-ffunction-sections", "-fdata-sections"] if source == "monocypher.c" else []
        subprocess.run([clang, *CFLAGS, *sections, f"-DTES3X_ORIG_ENTRY={entry}", *flags,
                        f"-I{HOOKS}", "-c", str(source_path(source)), "-o", str(obj)], check=True)
        objects.append(str(obj))
    payload, map_path = out / "tes3xhook.pe", out / "tes3xhook.map"
    subprocess.run([lld, "/nologo", "/subsystem:native", "/entry:tes3x_entry", "/fixed",
                    "/nodefaultlib", f"/base:{va}", f"/map:{map_path}", f"/out:{payload}",
                    *objects], check=True)

    link_map = map_path.read_text(errors="replace").splitlines()
    hooks = {}
    for key, symbols in wanted.items():
        hooks[key] = link_symbol(link_map, *symbols)
        hooks.update(extra.get(key, {}))
    # Beside the payload, so tes3x_patch.py can apply it without a toolchain or the map.
    manifest = out / "tes3xhook.json"
    with open(manifest, "w", encoding="utf-8") as stream:
        json.dump({"base": va, "hooks": hooks}, stream, indent=2)

    if check_xbe:
        calls = [(arch_site, int(hooks["archive_load"], 16))] if "archive_load" in hooks else []
        tes3x_inject.inject(xbe, payload, check_xbe, hook_entry=True, patch_calls=calls)
    return payload, manifest


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("xbe", help="clean retail morrowind.xbe")
    ap.add_argument("--src", action="append", metavar="FILE.c",
                    help="payload source, in hooks/ or a path (repeatable; default: %s)"
                         % " ".join(DEFAULT_SOURCES))
    ap.add_argument("--out", default=str(ROOT / "build" / "hooks"), help="output folder")
    ap.add_argument("--cflags", default=os.environ.get("EXTRA_CFLAGS", ""),
                    help="extra compiler flags, such as -DNAME=VALUE")
    ap.add_argument("--llvm", help="folder holding clang and lld-link (default: search PATH)")
    ap.add_argument("--check", metavar="OUT.xbe",
                    help="also inject the payload into a copy of the XBE, as a structural check")
    a = ap.parse_args(argv)
    try:
        build_payload(a.xbe, a.src or DEFAULT_SOURCES, a.out, a.cflags, a.llvm, a.check)
    except (PayloadError, ValueError, subprocess.CalledProcessError) as exc:
        raise SystemExit(str(exc))


if __name__ == "__main__":
    main()
