"""Apply content-located patches to a retail Morrowind XBE."""

from types import SimpleNamespace
import argparse
import hashlib
import json
import os
import re
import struct
import tes3x.inject as tes3x_inject  # noqa: E402
import tes3x.patches as registry  # noqa: E402
from tes3x.patch_sites import (ARENA_SIZE_OFF, ARENA_SIZE_SIG, ASSET_PATHS, AUTOSAVE_NAME_SIG,
                               BOUND16, BOUND16_COUNT, BOUND32, BOUND32_COUNT, BOW_VIEW_SIG,
                               BUTTON_PRESSED_RE, CERT_ALLOWED_MEDIA, CERT_GAME_REGION,
                               CERT_TITLE_CHARS, CERT_TITLE_ID, CERT_TITLE_NAME, COMMAND_SENTINEL,
                               COMMAND_STRIDE, CONSOLE_GATE_SIG, CONSOLE_PRINT_SIG,
                               CONSOLE_PRINT_VSPRINTF, CONTROLS_COPY_SIG, CRT_HEAP_ALLOC,
                               DIAGNOSTICS_UPDATE_SIG, DIALOGUE_MERGE_SIG, DXT5_SIZE_SIG,
                               FIND_MARKER_RE, FIRST_OPCODE, HEAP_FORWARD, HEAP_FUNCTIONS,
                               HEAP_NEW, HEAP_WRAPPERS, INLINE_DRIVE, LEAN_LAUNCH_SIG,
                               LEAN_NO_REBOOT_SIG, LEAN_PLAYER_SIG, LEAN_PRELOAD_SIG,
                               LEAN_RECORD_SIG, LEAN_TAG_SIG, LOCATORS, MCP102_ACTN_SIG,
                               MCP102_FOUND_JUMP, MCP102_STORE, MCP123_ADD_SIG,
                               MCP125_ADD_MOB_SIG, MCP125_ATTACH_SIG, MCP125_COLLISION_SIG,
                               MCP125_POSITION_A_SIG, MCP125_POSITION_B_SIG, MCP146_READY_SIG,
                               MCP146_RESUME_SIG, MCP146_SIG, MCP154_LOAD_SIG, MCP154_RELOAD_SIG,
                               MCP154_REPLACED, MCP37_SITE_SIG, MCP37_TREE_NEXT_SIG,
                               MCP3_UNARMORED_SIG, MCP92_RETIRE_MAGIC_SIG, MCP92_UNSUMMON_SIG,
                               MCP94_SIGS, MCP97_FIXED_IMM, MCP97_LENGTH_CASE,
                               MCP97_LENGTH_REPLACED, MCP97_SCAN_SIG, MCP98_REFCOUNT_SIG,
                               MEDIA_ANY, MENU_GATE_RE, MOBILE_PLAYER_SIG, MOB_GATE_RE, OPCODE_HI,
                               OPCODE_LO, POOL_NAME, POOL_PROLOGUE, PREFERENCES_LOAD_SIG,
                               PROFILE_LIST_SITES, PatchError, REF_INDEX_FIND_SIG, REF_INDEX_OFF,
                               REF_INDEX_SCRIPTS_SIG, REF_INDEX_SCRIPT_CALLS, REF_INDEX_SIG,
                               REF_LOAD_OFF, REF_LOAD_SIG, REF_SKIP_SIG, REGION_ANY,
                               RUNFN_DISPATCH, RUNFN_PROLOGUE, SAVE_ALLOWED_SIG, SAVE_STAGING,
                               SCRIPT_DECODE_SIG, SLEEP_LOOPS, SLEEP_LOOP_SIG, SLEEP_SIG,
                               TITLE_ID, TRANSITION_CALL_SIGS, VIDEO_CREATE_SIG, VIDEO_MODES_SIG,
                               VIDEO_RENDERER_SIG, VK_KEY_LIMIT_SIG, VK_SPACE_LIMIT_SIG,
                               WEATHER_ROLL_RE, XAPI_HEAP_FREE, _find_in, _find_mcp154_site,
                               _unique_call, find_arena_size, find_autosave_calls, find_bow_view,
                               find_button_pressed, find_call_sites, find_command_table,
                               find_console_gate, find_console_print, find_controls_table,
                               find_diagnostics_update, find_dialogue_merge, find_dxt5_size,
                               find_game_instance, find_hd_video, find_heap_function,
                               find_heap_object, find_heap_region, find_heap_region_sites,
                               find_info_arena_sites, find_lean_menu, find_marker,
                               find_mcp102_actn, find_mcp123_add, find_mcp125_collision,
                               find_mcp125_context, find_mcp146, find_mcp146_context,
                               find_mcp154_load, find_mcp154_reload, find_mcp37_context,
                               find_mcp3_unarmored, find_mcp92_unsummon, find_mcp94,
                               find_mcp97_scan, find_mcp98_refcounts, find_menu_mode_gate,
                               find_mob_gate, find_preferences_load, find_ref_index,
                               find_ref_load, find_ref_skip, find_run_function,
                               find_save_allowed_context, find_save_this_ptr,
                               find_script_decode_state, find_script_fixup_call,
                               find_script_ip_restore_call, find_script_ip_restore_site,
                               find_transition_calls, find_unique, find_vk_limit,
                               find_weather_roll, find_world_controller, find_xapi_heap_alloc,
                               find_xapi_heap_free, heap_call_sites, heap_new_site,
                               heap_pool_site, heap_wrapper_sites, text_section)

PATCHES = {}


TEST_PATCHES = {}


PATCH_BITS = {entry["name"]: 1 << entry["bit"] for entry in registry.PATCHES if "bit" in entry}


# The profiler deliberately replaces script-ext's calls with timing stubs which then call the
# installed dispatch hook.  Other patches have exclusive ownership of their reported ranges.
ALLOWED_PATCH_OVERLAPS = {("script-ext", "profile")}


def _format_ranges(offsets):
    """Render sorted byte offsets as compact half-open file ranges."""
    ranges = []
    for off in sorted(offsets):
        if ranges and off == ranges[-1][1]:
            ranges[-1] = (ranges[-1][0], off + 1)
        else:
            ranges.append((off, off + 1))
    return ", ".join("0x%X..0x%X" % pair for pair in ranges)


def _validate_patch_changes(name, before, after, edits, owners):
    """Require one patch to report every existing-byte change and own every reported range."""
    existing_size = len(before)
    if len(after) < existing_size:
        raise PatchError("%s: patch truncated the image" % name)

    changed = {off for off in range(existing_size) if before[off] != after[off]}
    claims = []
    for off, length, _label in edits:
        # None describes newly appended bytes or an informational line.  A patch cannot overwrite
        # an existing byte without claiming its concrete file range.
        if off is None:
            continue
        if not isinstance(off, int) or not isinstance(length, int) or length <= 0:
            raise PatchError("%s: invalid reported range (%r, %r)" % (name, off, length))
        if off < 0 or off + length > existing_size:
            raise PatchError("%s: reported range 0x%X+0x%X is outside the existing image"
                             % (name, off, length))
        claims.append((off, length))

    covered = set()
    for off, length in claims:
        owned = set(range(off, off + length))
        if not changed.intersection(owned):
            raise PatchError("%s: reported range 0x%X+0x%X but changed no byte in it"
                             % (name, off, length))
        if covered.intersection(owned):
            raise PatchError("%s: reported overlapping ranges at 0x%X+0x%X"
                             % (name, off, length))
        covered.update(owned)

    missing = changed - covered
    if missing:
        raise PatchError("%s: changed unreported byte(s) at %s"
                         % (name, _format_ranges(missing)))

    for off, length in claims:
        end = off + length
        for prior_off, prior_length, prior_name in owners:
            if off < prior_off + prior_length and prior_off < end:
                if (prior_name, name) not in ALLOWED_PATCH_OVERLAPS:
                    raise PatchError("%s: range 0x%X+0x%X overlaps %s's 0x%X+0x%X"
                                     % (name, off, length, prior_name,
                                        prior_off, prior_length))
    return claims


def patch(name):
    """Register fn as the patch patches.toml calls name, with its value and summary from there."""
    entry = registry.BY_NAME[name]

    def register(fn):
        PATCHES[name] = (fn, entry.get("takes"), entry["summary"])
        return fn
    return register


def test_patch(name, summary):
    """Register test instrumentation that is not part of the public patch table."""
    def register(fn):
        TEST_PATCHES[name] = (fn, None, summary)
        return fn
    return register


def drive_letter(value, what):
    if not value or len(value) != 1 or not value.isalpha():
        raise PatchError("%s: want a single drive letter, got %r" % (what, value))
    return value.upper()


@patch("drive-letters")
def _drive_letters(x, value, ctx):
    """Point every Data Files asset path at one drive."""
    letter = drive_letter(value, "drive-letters")
    edits = []
    # Rewrite every occurrence; the NUL keeps short paths from matching longer ones.
    for tail in ASSET_PATHS:
        hits = [m.start() for m in re.finditer(rb"[A-Za-z]:\\" + re.escape(tail) + rb"\x00", x.data)]
        if not hits:
            raise PatchError("drive-letters: no match for %r" % tail)
        for off in hits:
            if x.data[off] != ord(letter):
                edits.append((off, 1, "%c:\\%s -> %s:" % (x.data[off], tail.decode(), letter)))
                x.data[off] = ord(letter)

    hits = [m.start() for m in re.finditer(rb"\xC7\x44\x24\x0C[A-Za-z]:\\\x00", x.data)]
    if len(hits) != 1:
        raise PatchError("drive-letters: %d inline drive store(s), expected 1" % len(hits))
    off = hits[0] + 4
    if x.data[off] != ord(letter):
        edits.append((off, 1, "inline %s:\\" % letter))
        x.data[off] = ord(letter)
    return edits


# A scene copy of a retail image differs from it only by these edits. Making them, rather than
# undoing them, is unambiguous: retail itself has `Data Files\%s` under both D: and Z:.
SCENE_EDITS = (("boot-media", ""), ("drive-letters", "D"))


def scene_form(data):
    """An image with the scene's edits made, headers untouched; deltas are made against it.

    An image without the asset paths, such as the launcher, gets only the certificate edit."""
    if data[:4] != b"XBEH":
        raise ValueError("not an XBE")
    x = SimpleNamespace(data=bytearray(data))
    for name, value in SCENE_EDITS:
        before = bytes(x.data)
        try:
            PATCHES[name][0](x, value, {})
        except PatchError:
            x.data[:] = before
    return bytes(x.data)


def retail_digest(data):
    """SHA-256 of an image's scene form, so retail and scene copies share it."""
    return hashlib.sha256(scene_form(data)).hexdigest()


@patch("save-staging")
def _save_staging(x, value, ctx):
    """Stage saves on one volume with UDATA so the commit renames instead of copying."""
    letter = drive_letter(value, "save-staging")
    edits = []

    def set_letter(off, label):
        want = letter if x.data[off] < 0x61 else letter.lower()
        if x.data[off] != ord(want):
            edits.append((off, 1, "%s -> %s" % (label, want)))
            x.data[off] = ord(want)

    # Match the adjacent path, drive prefix, and filename as one unambiguous block.
    hits = list(re.finditer(
        rb"[A-Za-z]:\\tempsave\.ess\x00[A-Za-z]:\\\x00tempsave\.ess\x00", x.data))
    if len(hits) != 1:
        raise PatchError("save-staging: %d tempsave block(s), expected 1" % len(hits))
    set_letter(hits[0].start(), "tempsave.ess path")
    set_letter(hits[0].start() + 16, "staging drive prefix")

    for tail in SAVE_STAGING[1:]:
        found = list(re.finditer(rb"[A-Za-z]:\\" + re.escape(tail) + rb"\x00", x.data))
        if not found:
            raise PatchError("save-staging: no match for %r" % tail)
        for m in found:
            set_letter(m.start(), tail.decode())
    return edits


@patch("loop-sleeps")
def _loop_sleeps(x, value, ctx):
    """Yield instead of sleeping 1 ms every 16-128 objects in the save and load walks."""
    hits = list(SLEEP_SIG.finditer(bytes(x.data)))
    if len(hits) != 1:
        raise PatchError("loop-sleeps: %d Sleep wrapper(s), expected 1" % len(hits))
    sleep = x.off_to_va(hits[0].start())
    sec = text_section(x)
    body = bytes(x.data[sec.raw:sec.raw + sec.rsize])
    edits = []
    for m in SLEEP_LOOP_SIG.finditer(body):
        call = sec.va + m.end() - 5
        if call + 5 + struct.unpack("<i", m.group("rel"))[0] != sleep:
            continue
        off = sec.raw + m.start("ms")
        x.data[off] = 0
        edits.append((off, 1, "Sleep(1) -> Sleep(0) at 0x%08X" % call))
    if len(edits) != SLEEP_LOOPS:
        raise PatchError("loop-sleeps: %d periodic Sleep(1) call(s), expected %d"
                         % (len(edits), SLEEP_LOOPS))
    return edits


@patch("boot-media")
def _boot_media(x, value, ctx):
    """Permit booting from any media and region, not just a retail DVD."""
    edits = []
    for off, want, label in ((CERT_ALLOWED_MEDIA, MEDIA_ANY, "dwAllowedMedia"),
                             (CERT_GAME_REGION, REGION_ANY, "dwGameRegion")):
        if struct.unpack_from("<I", x.data, off)[0] != want:
            struct.pack_into("<I", x.data, off, want)
            edits.append((off, 4, "%s = 0x%08X" % (label, want)))
    return edits


@patch("title")
def _title(x, value, ctx):
    """Rename the image, so parallel installs are told apart in a dashboard."""
    name = value.strip()
    if not name:
        raise PatchError("title: needs a name")
    if len(name) > CERT_TITLE_CHARS - 1:
        raise PatchError("title: %r is %d characters, the certificate holds %d"
                         % (name, len(name), CERT_TITLE_CHARS - 1))
    size = CERT_TITLE_CHARS * 2
    was = bytes(x.data[CERT_TITLE_NAME:CERT_TITLE_NAME + size]).decode("utf-16-le")
    encoded = name.encode("utf-16-le").ljust(size, b"\0")
    if bytes(x.data[CERT_TITLE_NAME:CERT_TITLE_NAME + size]) == encoded:
        return []
    x.data[CERT_TITLE_NAME:CERT_TITLE_NAME + size] = encoded
    return [(CERT_TITLE_NAME, size, "title %r -> %r" % (was.split("\x00")[0], name))]


@patch("title-id")
def _title_id(x, value, ctx):
    """Give the image its own title ID, and so its own save folder."""
    try:
        new = int(value, 16)
    except ValueError:
        raise PatchError("title-id: want 8 hex digits, got %r" % value)
    if not 0 < new <= 0xFFFFFFFF:
        raise PatchError("title-id: 0x%X is not a 32-bit title ID" % new)
    was = struct.unpack_from("<I", x.data, CERT_TITLE_ID)[0]
    if was == new:
        return []
    struct.pack_into("<I", x.data, CERT_TITLE_ID, new)
    return [(CERT_TITLE_ID, 4, "title ID 0x%08X -> 0x%08X" % (was, new))]


@patch("payload")
def _payload(x, value, ctx):
    """Inject a code section and run it from the entry point."""
    if not value:
        raise PatchError("payload: needs a linked PE")
    blob, vsize, entry_rva, imgbase = tes3x_inject.load_pe(value)
    va = x.next_va()
    if imgbase != va:
        raise PatchError("payload linked at 0x%08X but the section lands at 0x%08X; "
                         "relink with /base:0x%X" % (imgbase, va, va))
    x.add_section(ctx["section"], blob, vsize, 0x02 | 0x04 | 0x01)
    x.set_entry(imgbase + entry_rva)
    ctx["payload"] = value
    manifest = os.path.splitext(value)[0] + ".json"
    if os.path.exists(manifest):
        with open(manifest, encoding="utf-8") as f:
            ctx["hooks"] = json.load(f).get("hooks", {})
    return [(tes3x_inject.HDR_ENTRY, 4, "section %s at 0x%08X, entry -> 0x%08X"
             % (ctx["section"], va, imgbase + entry_rva))]


@patch("multi-bsa")
def _multi_bsa(x, value, ctx):
    """Load every archive listed in tes3xarch.txt, not just Morrowind.bsa."""
    target = ctx.get("hooks", {}).get("archive_load")
    if not target:
        raise PatchError("multi-bsa: needs `payload` first, with an archive_load hook in its "
                         "manifest")
    target = int(str(target), 16)
    off = find_unique(x.data, b":\\Data Files\\Morrowind.bsa\x00", "archive path")
    string_va = x.off_to_va(off - 1)
    if string_va is None:
        raise PatchError("multi-bsa: archive path is outside any section")
    # `push <string>; call rel32` - the one place the hardcoded archive is opened
    sites = [m.start() for m in
             re.finditer(re.escape(b"\x68" + struct.pack("<I", string_va)) + rb"\xE8", x.data)]
    if len(sites) != 1:
        raise PatchError("multi-bsa: %d push/call site(s) for the archive path, expected 1"
                         % len(sites))
    site_va = x.off_to_va(sites[0]) + 5
    was, call_off = x.patch_call(site_va, target)
    return [(call_off, 5, "Archive::Load call 0x%08X: 0x%08X -> 0x%08X" % (site_va, was, target))]


def widen_opcode_bounds(x, ceiling):
    """Raise the six instruction-length bounds from 0x11BD to `ceiling`."""
    sec = text_section(x)
    body = bytes(x.data[sec.raw:sec.raw + sec.rsize])
    found = []
    for rx, width, count in ((BOUND16, 2, BOUND16_COUNT), (BOUND32, 4, BOUND32_COUNT)):
        hits = list(rx.finditer(body))
        if len(hits) != count:
            raise PatchError("script-ext: %d of the %d-bit opcode bounds, expected %d"
                             % (len(hits), width * 8, count))
        for m in hits:
            found.append((sec.raw + m.end() - width, width))
    edits = []
    for off, width in sorted(found):
        struct.pack_into("<H" if width == 2 else "<I", x.data, off, ceiling)
        edits.append((off, width, "opcode bound 0x%08X: 0x%04X -> 0x%04X"
                      % (x.off_to_va(off), OPCODE_HI, ceiling)))
    return edits


@patch("script-ext")
def _script_ext(x, value, ctx):
    """Add script opcodes: widen the length bounds and hook Script::RunFunction."""
    target = ctx.get("hooks", {}).get("script_dispatch")
    if not target:
        raise PatchError("script-ext: needs `payload` first, with a script_dispatch hook in its "
                         "manifest")
    target = int(str(target), 16)
    ceiling = int(str(ctx.get("hooks", {}).get("opcode_ceil") or value or 0x4000), 0)
    if not OPCODE_HI < ceiling <= 0x7FFF:
        raise PatchError("script-ext: ceiling 0x%X must be above 0x%04X and below 0x8000 - the "
                         "bound comparisons are signed" % (ceiling, OPCODE_HI))

    edits = widen_opcode_bounds(x, ceiling)

    runfn = find_run_function(x)
    sites = find_call_sites(x, runfn)
    if len(sites) != 3:
        raise PatchError("script-ext: %d call site(s) for RunFunction 0x%08X, expected 3"
                         % (len(sites), runfn))
    for site in sites:
        was, off = x.patch_call(site, target)
        edits.append((off, 5, "RunFunction call 0x%08X: 0x%08X -> 0x%08X" % (site, was, target)))
    return edits


@patch("mwse-legacy")
def _mwse_legacy(x, value, ctx):
    """Interpret legacy MWSE 0.9.4 bytecode embedded in compiled scripts."""
    hooks = ctx.get("hooks", {})
    target, dispatch = hooks.get("mwse_fixup"), hooks.get("script_dispatch")
    if not target or not dispatch:
        raise PatchError("mwse-legacy: needs `payload` first, with mwse_fixup and "
                         "script_dispatch hooks in its manifest")
    target = int(str(target), 16)
    dispatch = int(str(dispatch), 16)
    if len(find_call_sites(x, dispatch)) != 3:
        raise PatchError("mwse-legacy: requires script-ext to be applied first")
    decode, _ip, opcode = find_script_decode_state(x)
    site = find_script_fixup_call(x, decode, opcode)
    was, off = x.patch_call(site, target)
    return [(off, 5, "script fixup decoder 0x%08X: 0x%08X -> 0x%08X" % (site, was, target))]


@patch("build-preferences")
def _build_preferences(x, value, ctx):
    """Apply profile-selected player preferences after stored Xbox options load."""
    target = ctx.get("hooks", {}).get("preferences_load")
    if not target:
        raise PatchError("build-preferences: needs `payload` first, with a preferences_load "
                         "hook in its manifest")
    target = int(str(target), 16)
    site = find_preferences_load(x)
    expected = tes3x_inject.call_target(x, site)
    was, off = x.patch_call(site, target)
    if was != expected:
        raise PatchError("build-preferences: call 0x%08X targets 0x%08X, expected 0x%08X"
                         % (site, was, expected))
    return [(off, 5, "controls load 0x%08X: 0x%08X -> 0x%08X"
             % (site, was, target))]


@patch("rotating-autosaves")
def _rotating_autosaves(x, value, ctx):
    """Rotate automatic saves through INI-configurable slots."""
    target = ctx.get("hooks", {}).get("autosave")
    if not target:
        raise PatchError("rotating-autosaves: needs `payload` first, with an autosave hook in "
                         "its manifest")
    target = int(str(target), 16)
    save_game, sites = find_autosave_calls(x)
    edits = []
    for site in sites:
        was, off = x.patch_call(site, target)
        if was != save_game:
            raise PatchError("rotating-autosaves: call 0x%08X targets 0x%08X, expected 0x%08X"
                             % (site, was, save_game))
        edits.append((off, 5, "autosave call 0x%08X: 0x%08X -> 0x%08X"
                      % (site, was, target)))
    return edits


@patch("transition-autosaves")
def _transition_autosaves(x, value, ctx):
    """Save before player-triggered doors, teleports and paid travel."""
    hooks = ctx.get("hooks", {})
    wanted = {
        "cell": "transition_cell",
        "cell_companions": "transition_cell_companions",
        "teleport": "transition_teleport",
        "travel": "transition_travel",
    }
    if any(not hooks.get(name) for name in wanted.values()):
        raise PatchError("transition-autosaves: needs `payload` first, with transition hooks "
                         "in its manifest")
    calls, cell_change, companions = find_transition_calls(x)
    edits = []
    for kind, sites in calls.items():
        target = int(str(hooks[wanted[kind]]), 16)
        expected = companions if kind == "cell_companions" else cell_change
        for site in sites:
            was, off = x.patch_call(site, target)
            if was != expected:
                raise PatchError("transition-autosaves: call 0x%08X targets 0x%08X, expected 0x%08X"
                                 % (site, was, expected))
            edits.append((off, 5, "%s transition 0x%08X: 0x%08X -> 0x%08X"
                          % (kind, site, was, target)))
    return edits


@patch("mcp-1")
def _mcp_1(x, value, ctx):
    """Stop an unresolvable reference being restamped as created at runtime."""
    target = ctx.get("hooks", {}).get("ref_load")
    if not target:
        raise PatchError("mcp-1: needs `payload` first, with a ref_load hook in its manifest")
    target = int(str(target), 16)
    site = find_ref_load(x)
    off = x.va_to_off(site)
    # jmp rel32 plus one pad; the replaced instruction is six bytes and the payload repeats it.
    x.data[off:off + 6] = b"\xe9" + struct.pack("<i", target - (site + 5)) + b"\x90"
    edits = [(off, 6, "restamp fallback 0x%08X -> 0x%08X" % (site, target))]

    shift = find_unique(x.data, REF_INDEX_SIG, "mod index shift") + REF_INDEX_OFF
    x.data[shift] = 0xE8
    edits.append((shift, 1, "mod index 0x%08X: sar -> shr" % x.off_to_va(shift - 1)))
    return edits


@patch("mcp-37")
def _mcp_37(x, value, ctx):
    """Cancel stale NPC casts before their actor references leave the active cell."""
    target = ctx.get("hooks", {}).get("mcp37")
    if not target:
        raise PatchError("mcp-37: needs `payload` first, with an mcp37 hook in its manifest")
    target = int(str(target), 16)
    site, _game, _player, _tree = find_mcp37_context(x)
    off = x.va_to_off(site)
    expected = b"\x8b\x0d" + struct.pack("<I", _game)
    if bytes(x.data[off:off + 6]) != expected:
        raise PatchError("mcp-37: cell-change game load does not match expected instruction")
    x.data[off:off + 6] = b"\xe8" + struct.pack("<i", target - (site + 5)) + b"\x90"
    return [(off, 6, "stale-cast cleanup 0x%08X -> 0x%08X" % (site, target))]


@patch("mcp-3")
def _mcp_3(x, value, ctx):
    """Apply Unarmored damage reduction when no armor is equipped."""
    site = find_mcp3_unarmored(x)
    off = x.va_to_off(site)
    # Jump over four trap bytes to the calculation which already handles no equipped armor.
    x.data[off:off + 6] = b"\xeb\x04\xcc\xcc\xcc\xcc"
    return [(off, 6, "fully unarmored damage 0x%08X: keep reduction path" % site)]


@patch("dialogue-merge")
def _dialogue_merge(x, value, ctx):
    """Keep a topic's INFO chain when a later plugin repeats its DIAL record."""
    site = find_dialogue_merge(x)
    off = x.va_to_off(site)
    x.data[off:off + 3] = b"\x90\x90\x90"
    return [(off, 3, "repeated topic 0x%08X: keep INFO chain" % site)]


@patch("mcp-97")
def _mcp_97(x, value, ctx):
    """Advance the script parser correctly while initializing saved data."""
    target = ctx.get("hooks", {}).get("mcp97_scan")
    if not target:
        raise PatchError("mcp-97: needs `payload` first, with an mcp97_scan hook in its "
                         "manifest")
    target = int(str(target), 16)
    site = find_mcp97_scan(x)
    off = x.va_to_off(site)

    fixed = off - MCP97_LENGTH_CASE + MCP97_FIXED_IMM
    if x.data[fixed] != 3:
        raise PatchError("mcp-97: fixed-width advance is %d, expected 3" % x.data[fixed])
    x.data[fixed] = 2

    expected = b"\x0f\xbe\x40\x01\x03\xc8"
    if bytes(x.data[off:off + MCP97_LENGTH_REPLACED]) != expected:
        raise PatchError("mcp-97: length-prefixed case does not match expected instructions")
    x.data[off:off + MCP97_LENGTH_REPLACED] = (
        b"\xe9" + struct.pack("<i", target - (site + 5)) + b"\x90"
    )
    return [
        (fixed, 1, "fixed-width cursor 0x%08X: 3 -> 2" % x.off_to_va(fixed)),
        (off, MCP97_LENGTH_REPLACED,
         "length-prefixed cursor 0x%08X -> 0x%08X" % (site, target)),
    ]


@patch("mcp-98")
def _mcp_98(x, value, ctx):
    """Keep animated-container access from changing the references' own counts."""
    retain, release, exit_va = find_mcp98_refcounts(x)
    retain_off, release_off = x.va_to_off(retain), x.va_to_off(release)
    jump = exit_va - (release + 2)
    if not -128 <= jump <= 127:
        raise PatchError("mcp-98: common return is outside short-jump range")
    x.data[retain_off] = 0x90
    x.data[release_off:release_off + 2] = b"\xeb" + struct.pack("<b", jump)
    return [
        (retain_off, 1, "animated-container retain 0x%08X: removed" % retain),
        (release_off, 2, "animated-container release 0x%08X -> 0x%08X"
         % (release, exit_va)),
    ]


@patch("mcp-92")
def _mcp_92(x, value, ctx):
    """Retire magic targeting a summoned actor before destroying the actor."""
    site, target = find_mcp92_unsummon(x)
    off = x.va_to_off(site)
    replacement = b"\x8b\xcf\xe8" + struct.pack("<i", target - (site + 7)) + b"\x90\x90"
    x.data[off:off + 9] = replacement
    return [(off, 9, "summon magic cleanup 0x%08X -> 0x%08X" % (site, target))]


@patch("mcp-154")
def _mcp_154(x, value, ctx):
    """Pad compiled script-data allocations to keep dword reads in bounds."""
    hooks = ctx.get("hooks", {})
    targets = (hooks.get("mcp154_load"), hooks.get("mcp154_reload"))
    if not all(targets):
        raise PatchError("mcp-154: needs `payload` first, with mcp154_load and mcp154_reload "
                         "hooks in its manifest")
    sites = (find_mcp154_load(x), find_mcp154_reload(x))
    expected = (b"\x8b\x87\x40\x02\x00\x00", b"\x8b\xbe\x40\x02\x00\x00")
    labels = ("initial", "reload")
    edits = []
    for site, target, want, label in zip(sites, targets, expected, labels):
        target = int(str(target), 16)
        off = x.va_to_off(site)
        if bytes(x.data[off:off + MCP154_REPLACED]) != want:
            raise PatchError("mcp-154: %s allocation does not match expected instruction" % label)
        x.data[off:off + MCP154_REPLACED] = (
            b"\xe9" + struct.pack("<i", target - (site + 5)) + b"\x90"
        )
        edits.append((off, MCP154_REPLACED,
                      "%s allocation 0x%08X -> 0x%08X" % (label, site, target)))
    return edits


@patch("dxt5-size")
def _dxt5_size(x, value, ctx):
    """Size DXT5 textures as DXT3 instead of with a negative length."""
    target = ctx.get("hooks", {}).get("dxt5_size")
    if not target:
        raise PatchError("dxt5-size: needs `payload` first, with a dxt5_size hook in its manifest")
    site = find_dxt5_size(x)
    was, off = x.patch_call(site, int(str(target), 16))
    return [(off, 5, "texture size call 0x%08X: 0x%08X -> %s" % (site, was, target))]


@patch("mcp-146")
def _mcp_146(x, value, ctx):
    """Remove a fully nocked arrow when Ready Weapon is pressed."""
    target = ctx.get("hooks", {}).get("mcp146")
    if not target:
        raise PatchError("mcp-146: needs `payload` first, with an mcp146 hook in its manifest")
    site = find_mcp146(x)
    was, off = x.patch_call(site, int(str(target), 16))
    return [(off, 5, "player input guard 0x%08X: 0x%08X -> %s" % (site, was, target))]


@patch("ref-index")
def _ref_index(x, value, ctx):
    """Answer reference lookups from an index while global scripts start."""
    hooks = ctx.get("hooks", {})
    if not all(hooks.get(k) for k in ("refindex_find", "refindex_scripts")):
        raise PatchError("ref-index: needs `payload` first, with refindex_find and "
                         "refindex_scripts hooks in its manifest")
    walk, _, scripts = find_ref_index(x)
    sites = find_call_sites(x, scripts)
    if len(sites) != REF_INDEX_SCRIPT_CALLS:
        raise PatchError("ref-index: %d startGlobalScripts call(s), expected %d"
                         % (len(sites), REF_INDEX_SCRIPT_CALLS))
    find_hook = int(str(hooks["refindex_find"]), 16)
    off = x.va_to_off(walk)
    x.data[off:off + 5] = b"\xe9" + struct.pack("<i", find_hook - (walk + 5))
    edits = [(off, 5, "cell walk 0x%08X -> 0x%08X" % (walk, find_hook))]
    scripts_hook = int(str(hooks["refindex_scripts"]), 16)
    for site in sites:
        _, off = x.patch_call(site, scripts_hook)
        edits.append((off, 5, "startGlobalScripts call 0x%08X -> 0x%08X" % (site, scripts_hook)))
    return edits


@patch("bow-view")
def _bow_view(x, value, ctx):
    """Lower the first-person bow and arms while a projectile is nocked."""
    target = ctx.get("hooks", {}).get("bow_view")
    if not target:
        raise PatchError("bow-view: needs `payload` first, with a bow_view hook in its manifest")
    site = find_bow_view(x)
    was, off = x.patch_call(site, int(str(target), 16))
    return [(off, 5, "first-person transform 0x%08X: 0x%08X -> %s" %
             (site, was, target))]


@patch("mcp-94")
def _mcp_94(x, value, ctx):
    """Scale books, the journal and scrolls by screen height instead of width."""
    edits = []
    for what, view_off, base_off, base in find_mcp94(x):
        x.data[view_off] = 0x78
        x.data[base_off:base_off + 4] = struct.pack("<I", base + 4)
        edits.append((view_off, 1, "%s scale: viewWidth -> viewHeight" % what))
        edits.append((base_off, 4, "%s scale: 640.0 -> 480.0" % what))
    return edits


@patch("hd-video")
def _hd_video(x, value, ctx):
    """Render and output 1280x720 when the dashboard and AV pack allow it."""
    hooks = ctx.get("hooks", {})
    if not all(hooks.get(k) for k in ("video_renderer", "video_create")):
        raise PatchError("hd-video: needs `payload` first, with video_renderer and video_create "
                         "hooks in its manifest")
    found = find_hd_video(x)
    edits = []
    for site, key, what in ((found[0], "video_renderer", "NiXBoxRenderer::create"),
                            (found[1], "video_create", "Direct3D_CreateDevice")):
        was, off = x.patch_call(site, int(str(hooks[key]), 16))
        edits.append((off, 5, "%s call 0x%08X: 0x%08X -> %s" % (what, site, was, hooks[key])))
    return edits


@patch("lean-menu")
def _lean_menu(x, value, ctx):
    """Load only game settings for the main menu; the relaunch loads the rest."""
    hooks = ctx.get("hooks", {})
    if not all(hooks.get(k) for k in ("lean_record", "lean_preload", "lean_player")):
        raise PatchError("lean-menu: needs `payload` first, with lean_record, lean_preload and "
                         "lean_player hooks in its manifest")
    found = find_lean_menu(x)
    edits = []
    for site, key, what in ((found[0], "lean_record", "record load"),
                            (found[4], "lean_preload", "PreLoad cell lookup"),
                            (found[5], "lean_player", "New Game create-player")):
        was, off = x.patch_call(site, int(str(hooks[key]), 16))
        edits.append((off, 5, "%s call 0x%08X: 0x%08X -> %s" % (what, site, was, hooks[key])))
    return edits


@patch("video-arena")
def _video_arena(x, value, ctx):
    """Size the video-memory arena from [Xbox] VideoMemoryKB when there is more than 64 MB."""
    target = ctx.get("hooks", {}).get("arena_size")
    if not target:
        raise PatchError("video-arena: needs `payload` first, with an arena_size hook in its "
                         "manifest")
    site = find_arena_size(x)
    off = x.va_to_off(site)
    target = int(str(target), 16)
    x.data[off:off + 5] = b"\xe8" + struct.pack("<i", target - (site + 5))
    return [(off, 5, "arena size 0x%08X: mov ebx, 0xF80000 -> call 0x%08X" % (site, target))]


@patch("heap-region")
def _heap_region(x, value, ctx):
    """Reserve the engine heap's region, commit it as the heap grows, size it from the ini."""
    hooks = ctx.get("hooks", {})
    names = ("region_size", "region_reserve", "region_carve", "region_release")
    missing = [name for name in names if not hooks.get(name)]
    if missing:
        raise PatchError("heap-region: needs `payload` first, with %s in its manifest"
                         % ", ".join(missing))
    size, reserve, carve, release = (int(str(hooks[name]), 16) for name in names)
    site = find_heap_region(x)
    malloc_site, fit, free_site = find_heap_region_sites(x)
    off = x.va_to_off(site)
    x.data[off:off + 5] = b"\xe8" + struct.pack("<i", size - (site + 5))
    malloc, malloc_off = x.patch_call(malloc_site, reserve)
    free, free_off = x.patch_call(free_site, release)
    fit_off = x.va_to_off(fit)
    struct.pack_into("<i", x.data, fit_off + 2, carve - (fit + 6))
    return [
        (off, 5, "heap region 0x%08X: push 0x1100000 -> call 0x%08X" % (site, size)),
        (malloc_off, 5, "region malloc 0x%08X: 0x%08X -> 0x%08X" % (malloc_site, malloc, reserve)),
        (fit_off, 6, "region carve 0x%08X: jbe -> 0x%08X" % (fit, carve)),
        (free_off, 5, "region free 0x%08X: 0x%08X -> 0x%08X" % (free_site, free, release)),
    ]


@patch("info-name-arena")
def _info_name_arena(x, value, ctx):
    """Put INFO's temporary three-name tables in the demand-paged arena."""
    hooks = ctx.get("hooks", {})
    names = ("info_table_allocate", "info_names_allocate", "info_names_free",
             "info_table_free", "info_cleanup", "info_finish")
    missing = [name for name in names if not hooks.get(name)]
    if missing:
        raise PatchError("info-name-arena: needs `payload` first, with %s in its manifest"
                         % ", ".join(missing))
    sites = find_info_arena_sites(x)
    targets = {name: int(str(hooks[name]), 16) for name in names}
    edits = []
    for key, site in (("info_table_allocate", sites["table_allocate"]),
                      ("info_names_allocate", sites["names_allocate"]),
                      ("info_names_free", sites["destructor_names_free"]),
                      ("info_table_free", sites["destructor_table_free"]),
                      ("info_cleanup", sites["cleanup_call"]),
                      ("info_finish", sites["finish"])):
        was, off = x.patch_call(site, targets[key])
        edits.append((off, 5, "%s 0x%08X: 0x%08X -> 0x%08X"
                      % (key.replace("_", " "), site, was, targets[key])))
    return edits


@patch("mcp-102")
def _mcp_102(x, value, ctx):
    """Reactivate script-triggered objects after their script mod is removed."""
    site = find_mcp102_actn(x)
    off = x.va_to_off(site)
    match = MCP102_ACTN_SIG.match(bytes(x.data), off)
    store = match.start("missing")

    # Both the existing-state path and the newly-allocated path now share one store of
    # `serialized_flags | 1`; seven trailing padding bytes keep the function boundary fixed.
    x.data[off + MCP102_FOUND_JUMP] = store - (off + MCP102_FOUND_JUMP + 1)
    replaced = match.end() - store
    x.data[store:match.end()] = MCP102_STORE + b"\x90" * (replaced - len(MCP102_STORE))
    return [
        (off + MCP102_FOUND_JUMP, 1,
         "ACTN existing-state path 0x%08X -> shared store" % (site + 10)),
        (store, replaced,
         "ACTN flags 0x%08X: force active bit" % x.off_to_va(store)),
    ]


@test_patch("test-mcp97", "Trace saved-script cursor advances for the mcp-97 game test.")
def _test_mcp97(x, value, ctx):
    hooks = ctx.get("hooks", {})
    target, site = hooks.get("mcp97_test"), hooks.get("mcp97_test_site")
    if not target or not site:
        raise PatchError("test-mcp97: payload has no mcp-97 test probe")
    target, site = int(str(target), 16), int(str(site), 16)
    off = x.va_to_off(site)
    expected = b"\x41\x85\xed\x74\xc2"
    if off is None or bytes(x.data[off:off + 5]) != expected:
        raise PatchError("test-mcp97: scan landing does not match expected instructions")
    x.data[off:off + 5] = b"\xe9" + struct.pack("<i", target - (site + 5))
    return [(off, 5, "saved-script trace 0x%08X -> 0x%08X" % (site, target))]


@test_patch("test-mcp102", "Trace ACTN restoration for the mcp-102 game test.")
def _test_mcp102(x, value, ctx):
    hooks = ctx.get("hooks", {})
    target, site = hooks.get("mcp102_test"), hooks.get("mcp102_test_site")
    if not target or not site:
        raise PatchError("test-mcp102: payload has no mcp-102 test probe")
    target, site = int(str(target), 16), int(str(site), 16)
    was, off = x.patch_call(site, target)
    return [(off, 5, "ACTN load call 0x%08X: 0x%08X -> 0x%08X" %
             (site, was, target))]


@test_patch("test-dialoguemerge", "Count a topic's responses for the dialogue-merge game test.")
def _test_dialoguemerge(x, value, ctx):
    if not ctx.get("hooks", {}).get("dialmerge_test"):
        raise PatchError("test-dialoguemerge: payload has no dialogue-merge test command")
    return [(None, 0, "topic response count command installed")]


@test_patch("test-mcp3", "Measure fully unarmored damage for the mcp-3 game test.")
def _test_mcp3(x, value, ctx):
    if not ctx.get("hooks", {}).get("mcp3_test"):
        raise PatchError("test-mcp3: payload has no mcp-3 test command")
    return [(None, 0, "fully unarmored damage command installed")]


@patch("mcp-123")
def _mcp_123(x, value, ctx):
    """Mark a PlaceItem destination cell changed before inserting the new reference."""
    target = ctx.get("hooks", {}).get("mcp123_add")
    if not target:
        raise PatchError("mcp-123: needs `payload` first, with an mcp123_add hook in its manifest")
    target = int(str(target), 16)
    site = find_mcp123_add(x)
    was, off = x.patch_call(site, target)
    return [(off, 5, "PlaceItem cell insertion 0x%08X: 0x%08X -> 0x%08X"
             % (site, was, target))]


@patch("mcp-125")
def _mcp_125(x, value, ctx):
    """Attach moved-reference scripts and register actor collision in the destination cell."""
    hook = ctx.get("hooks", {}).get("mcp125_collision")
    if not hook:
        raise PatchError("mcp-125: needs `payload` first, with an mcp125_collision hook "
                         "in its manifest")
    hook = int(str(hook), 16)
    found = find_mcp125_context(x)
    edits = []

    for site, angle in zip(found["positions"], found["angles"]):
        off = x.va_to_off(site)
        call_site = site + 19
        block = (
            b"\xff\x35" + struct.pack("<I", angle) +
            b"\x68" + struct.pack("<I", found["coords"]) +
            b"\x56\x8b\x35" + struct.pack("<I", found["ref"]) + b"\x56" +
            b"\xe8" + struct.pack("<i", found["move"] - (call_site + 5)) +
            b"\x83\xc4\x10\xe9" + struct.pack("<i", found["resume"] - (site + 32))
        )
        if off is None or len(block) != 32:
            raise PatchError("mcp-125: invalid Position rewrite at 0x%08X" % site)
        x.data[off:off + 32] = block
        edits.append((off, 32, "Position script attachment 0x%08X -> 0x%08X"
                      % (site, found["resume"])))

    site = found["collision"]
    off = x.va_to_off(site)
    block = (
        b"\xa1" + struct.pack("<I", found["manager"]) + b"\x8b\x48\x5c" +
        b"\x8a\x44\x24\x3c\x57\xe8" + struct.pack("<i", hook - (site + 18)) +
        b"\xeb\x06"
    )
    if off is None or len(block) != 20:
        raise PatchError("mcp-125: invalid collision rewrite")
    x.data[off:off + 20] = block
    edits.append((off, 20, "Position collision branch 0x%08X -> 0x%08X" % (site, hook)))
    return edits


@patch("diagnostics")
def _diagnostics(x, value, ctx):
    """Enable INI-controlled crash records, snapshots and a hang watchdog."""
    hooks = ctx.get("hooks", {})
    target = hooks.get("diagnostics_update")
    flag = hooks.get("diagnostics_flag")
    if not target or not flag:
        raise PatchError("diagnostics: needs `payload` first, with diagnostics_update and flag in "
                         "its manifest")
    target = int(str(target), 16)
    flag = int(str(flag), 16)
    flag_off = x.va_to_off(flag)
    if flag_off is None:
        raise PatchError("diagnostics: installed flag is outside the payload section")
    struct.pack_into("<I", x.data, flag_off, 1)
    update = find_diagnostics_update(x)
    sites = find_call_sites(x, update)
    if len(sites) != 1:
        raise PatchError("diagnostics: %d call site(s) for Game::Update 0x%08X, expected 1"
                         % (len(sites), update))
    site = sites[0]
    was, off = x.patch_call(site, target)
    return [(flag_off, 4, "diagnostics installed flag at 0x%08X" % flag),
            (off, 5, "Game::Update call 0x%08X: 0x%08X -> 0x%08X"
             % (site, was, target))]


@patch("data-overlay")
def _data_overlay(x, value, ctx):
    """Read files missing from the game folder from the folder named by [Xbox] OverlayBase."""
    flag = ctx.get("hooks", {}).get("overlay_flag")
    if not flag:
        raise PatchError("data-overlay: needs `payload` first, built with tes3xoverlay.c")
    flag = int(str(flag), 16)
    flag_off = x.va_to_off(flag)
    if flag_off is None:
        raise PatchError("data-overlay: installed flag is outside the payload section")
    struct.pack_into("<I", x.data, flag_off, 1)
    return [(flag_off, 4, "data-overlay installed flag at 0x%08X" % flag)]


@patch("net")
def _net(x, value, ctx):
    """Install the shared Xbox NIC, IPv4 and UDP layer."""
    hooks = ctx.get("hooks", {})
    frame = hooks.get("diagnostics_update")
    if not hooks.get("net") or not frame:
        raise PatchError("net: needs `payload` first, built with tes3xnet.c and tes3xdiag.c")
    if len(find_call_sites(x, int(str(frame), 16))) != 1:
        raise PatchError("net: requires diagnostics to be applied first")
    return [(None, 0, "network foundation %s, started by [Xbox] NetAddress" % hooks["net"])]


@patch("multiplayer")
def _multiplayer(x, value, ctx):
    """Join a TES3X server from [Xbox] NetAddress and send the player's state each frame."""
    hooks = ctx.get("hooks", {})
    if not hooks.get("multiplayer") or not hooks.get("net"):
        raise PatchError("multiplayer: needs `payload` first, built with tes3xmulti.c and "
                         "tes3xnet.c")
    return [(None, 0, "multiplayer channel %s" % hooks["multiplayer"])]


@patch("agent")
def _agent(x, value, ctx):
    """Connect to the authenticated TES3X GUI listener."""
    hooks = ctx.get("hooks", {})
    if not hooks.get("agent") or not hooks.get("net"):
        raise PatchError("agent: needs `payload` first, built with tes3xagent.c and tes3xnet.c")
    return [(None, 0, "in-game agent channel %s" % hooks["agent"])]


@patch("profile")
def _profile(x, value, ctx):
    """Time listed functions with RDTSC at every direct call site."""
    hooks = ctx.get("hooks", {})
    table, stubs = hooks.get("prof_target"), hooks.get("prof_stubs")
    if not table or not stubs:
        raise PatchError("profile: needs `payload` first, built with tes3xprof.c in SRCS")
    table_off = x.va_to_off(int(str(table), 16))
    stubs_off = x.va_to_off(int(str(stubs), 16))
    if table_off is None or stubs_off is None:
        raise PatchError("profile: the payload tables are outside the injected section")
    count = int(str(hooks.get("prof_count", 0)), 0)

    targets = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        # `@name` names a payload hook, so an installed hook can be timed in its own right.
        if item.startswith("@"):
            if item[1:] not in hooks:
                raise PatchError("profile: no %r in the payload manifest" % item[1:])
            targets.append((item, int(str(hooks[item[1:]]), 16)))
        else:
            targets.append((item, int(item, 0)))
    if not targets:
        raise PatchError("profile: no targets")
    if len(targets) > count:
        raise PatchError("profile: %d target(s), the payload carries %d slot(s)"
                         % (len(targets), count))

    seen = set()
    edits = []
    for k, (item, va) in enumerate(targets):
        if va in seen:
            raise PatchError("profile: 0x%08X listed twice" % va)
        seen.add(va)
        # Virtual dispatch is invisible to call-site hooking; say so rather than time nothing.
        label = "%s " % item if item.startswith("@") else ""
        sites = find_call_sites(x, va)
        if not sites:
            raise PatchError("profile: no direct call site reaches %s0x%08X" % (label, va))
        stub = struct.unpack_from("<I", x.data, stubs_off + 4 * k)[0]
        struct.pack_into("<I", x.data, table_off + 4 * k, va)
        edits.append((table_off + 4 * k, 4,
                      "slot %d = %s0x%08X, stub 0x%08X, %d call site(s)"
                      % (k, label, va, stub, len(sites))))
        for site in sites:
            was, off = x.patch_call(site, stub)
            edits.append((off, 5, "  call 0x%08X: 0x%08X -> slot %d" % (site, was, k)
                          if len(sites) <= PROFILE_LIST_SITES else None))
    return edits


@patch("heap-census")
def _heap_census(x, value, ctx):
    """Redirect every direct Memory_Heap::Allocate and ::Free call to the census."""
    hooks = ctx.get("hooks", {})
    names = ("heap_allocate", "heap_allocate_wrapped", "heap_allocate_new",
             "heap_allocate_pool", "heap_free")
    if not all(hooks.get(name) for name in names):
        raise PatchError("heap-census: needs `payload` first, built with tes3xheap.c")
    direct, wrapped, new, pool, free = (int(str(hooks[name]), 16) for name in names)
    allocs = heap_call_sites(x, "allocate")
    targets = dict.fromkeys(heap_wrapper_sites(x, allocs), wrapped)
    targets[heap_new_site(x, allocs)] = new
    targets[heap_pool_site(x, allocs)] = pool
    frees = heap_call_sites(x, "free")
    edits = [(None, 0, "Memory_Heap::Allocate: %d call site(s), %d forwarding"
              % (len(allocs), len(targets))),
             (None, 0, "Memory_Heap::Free: %d call site(s)" % len(frees))]
    for site in allocs:
        _was, off = x.patch_call(site, targets.get(site, direct))
        edits.append((off, 5, None))
    for site in frees:
        _was, off = x.patch_call(site, free)
        edits.append((off, 5, None))
    return edits


@patch("mem-census")
def _mem_census(x, value, ctx):
    """Redirect every direct RtlAllocateHeap and RtlFreeHeap call to the memory census."""
    hooks = ctx.get("hooks", {})
    if not (hooks.get("mem_heap_alloc") and hooks.get("mem_heap_free")):
        raise PatchError("mem-census: needs `payload` first, built with tes3xmem.c")
    edits = []
    for name, target, key in (("RtlAllocateHeap", find_xapi_heap_alloc(x), "mem_heap_alloc"),
                              ("RtlFreeHeap", find_xapi_heap_free(x), "mem_heap_free")):
        hook = int(str(hooks[key]), 16)
        sites = find_call_sites(x, target)
        edits.append((None, 0, "%s 0x%08X: %d call site(s)" % (name, target, len(sites))))
        for site in sites:
            _was, off = x.patch_call(site, hook)
            edits.append((off, 5, None))
    return edits


@patch("console")
def _console(x, value, ctx):
    """Make the in-game console reachable, by replacing its input gate."""
    target = ctx.get("hooks", {}).get("console_gate")
    if not target:
        raise PatchError("console: needs `payload` first, with a console_gate hook in its "
                         "manifest")
    target = int(str(target), 16)
    site = find_console_gate(x)
    was, off = x.patch_call(site, target)
    edits = [(off, 5, "console gate 0x%08X: 0x%08X -> 0x%08X" % (site, was, target))]
    for what, sig, hook in (("key", VK_KEY_LIMIT_SIG, "console_vk_key"),
                            ("space", VK_SPACE_LIMIT_SIG, "console_vk_space")):
        stub = ctx["hooks"].get(hook)
        if not stub:
            continue
        stub = int(str(stub), 16)
        site = find_vk_limit(x, sig, what)
        off = x.va_to_off(site)
        x.data[off:off + 5] = b"\xe8" + struct.pack("<i", stub - (site + 5))
        edits.append((off, 5, "keyboard %s length check 0x%08X -> 0x%08X" % (what, site, stub)))
    printer = ctx["hooks"].get("console_print")
    if printer:
        printer = int(str(printer), 16)
        target = find_console_print(x)
        sites = find_call_sites(x, target)
        for site in sites:
            was, off = x.patch_call(site, printer)
            edits.append((off, 5, None))
        edits.append((None, 0, "console printf 0x%08X: %d call site(s) -> 0x%08X"
                      % (target, len(sites), printer)))
    return edits


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("xbe", nargs="?")
    ap.add_argument("--out")
    ap.add_argument("--apply", action="append", default=[], metavar="NAME[=VALUE]")
    ap.add_argument("--section", default=".tes3xhk")
    ap.add_argument("--list", action="store_true", help="list available patches and exit")
    ap.add_argument("--locate", choices=list(LOCATORS),
                    help="print a content-located engine address and exit")
    ap.add_argument("--digest", action="store_true",
                    help="print the image's retail digest, which a scene copy shares, and exit")
    a = ap.parse_args()

    if a.list or not a.xbe:
        print("\n  available patches:\n")
        for entry in registry.PATCHES:
            name = entry["name"]
            _fn, takes, help_text = PATCHES[name]
            spec = "%s=%s" % (name, takes) if takes else name
            print("    %-26s %s" % (spec, help_text))
        print()
        return

    seen = set()
    for spec in a.apply:
        name = spec.partition("=")[0]
        if name in seen:
            raise SystemExit("duplicate --apply %r" % name)
        seen.add(name)

    raw = open(a.xbe, "rb").read()
    if a.digest:
        print(retail_digest(raw))
        return
    x = tes3x_inject.Xbe(raw)
    if a.locate:
        print("0x%08X" % LOCATORS[a.locate](x))
        return
    cert = struct.unpack_from("<I", x.data, 0x118)[0]
    title = struct.unpack_from("<I", x.data, cert - x.base + 8)[0]
    print("%s: %d bytes, title 0x%08X, %d sections" % (a.xbe, len(raw), title, len(x.sections)))
    if title != TITLE_ID:
        raise SystemExit("not Morrowind (title 0x%08X, want 0x%08X)" % (title, TITLE_ID))
    if any(s.name == a.section for s in x.sections):
        raise SystemExit("already carries a %s section; patch a clean XBE instead" % a.section)

    ctx = {"section": a.section}
    touched = []
    owners = []
    applied = []
    for spec in a.apply:
        name, _, value = spec.partition("=")
        available = {**PATCHES, **TEST_PATCHES}
        if name not in available:
            raise SystemExit("unknown patch %r; --list shows them" % name)
        fn, takes, _help = available[name]
        if takes and not value:
            raise SystemExit("%s needs a value: %s=%s" % (name, name, takes))
        print("\n  %s" % spec)
        try:
            before = bytes(x.data)
            edits = list(fn(x, value, ctx))
            claims = _validate_patch_changes(name, before, x.data, edits, owners)
            for off, length, label in edits:
                # A patch with hundreds of identical edits reports them as one line.
                if label:
                    print("    %s" % label)
            touched.extend((off, length) for off, length in claims
                           if off + length <= len(raw))
            owners.extend((off, length, name) for off, length in claims)
            applied.append(name)
        except PatchError as exc:
            raise SystemExit("  FAILED: %s" % exc)

    mask_va = ctx.get("hooks", {}).get("patch_mask")
    mask_hi_va = ctx.get("hooks", {}).get("patch_mask_hi")
    if mask_va:
        mask_va = int(str(mask_va), 16)
        mask_hi_va = int(str(mask_hi_va), 16) if mask_hi_va else None
        mask_off = x.va_to_off(mask_va)
        mask_hi_off = x.va_to_off(mask_hi_va) if mask_hi_va else None
        if mask_off is None or (mask_hi_va and mask_hi_off is None):
            raise SystemExit("payload patch mask is outside the injected section")
        mask = 0
        for name in applied:
            mask |= PATCH_BITS.get(name, 0)
        if mask >> 32 and mask_hi_off is None:
            raise SystemExit("payload has no high patch mask for patches above bit 31")
        struct.pack_into("<I", x.data, mask_off, mask & 0xFFFFFFFF)
        if mask_hi_off is not None:
            struct.pack_into("<I", x.data, mask_hi_off, mask >> 32)
        width = 16 if mask >> 32 else 8
        print("\n  payload patch mask 0x%0*X at 0x%08X" % (width, mask, mask_va))

    x.rebuild_headers()
    out = bytes(x.data)
    problems = tes3x_inject.verify(raw, out, touched)
    if problems:
        for p in problems:
            print("  FAIL: %s" % p)
        raise SystemExit("refusing to write a corrupted XBE")
    print("\n  verified: %d byte(s) changed in the original image, headers intact"
          % sum(n for _, n in touched))
    if a.out:
        open(a.out, "wb").write(out)
        print("  wrote %s (%d bytes)" % (a.out, len(out)))
    else:
        print("  (no --out; nothing written)")


if __name__ == "__main__":
    main()

__all__ = [
    'ALLOWED_PATCH_OVERLAPS',
    'ARENA_SIZE_OFF',
    'ARENA_SIZE_SIG',
    'ASSET_PATHS',
    'AUTOSAVE_NAME_SIG',
    'BOUND16',
    'BOUND16_COUNT',
    'BOUND32',
    'BOUND32_COUNT',
    'BOW_VIEW_SIG',
    'BUTTON_PRESSED_RE',
    'CERT_ALLOWED_MEDIA',
    'CERT_GAME_REGION',
    'CERT_TITLE_CHARS',
    'CERT_TITLE_ID',
    'CERT_TITLE_NAME',
    'COMMAND_SENTINEL',
    'COMMAND_STRIDE',
    'CONSOLE_GATE_SIG',
    'CONSOLE_PRINT_SIG',
    'CONSOLE_PRINT_VSPRINTF',
    'CONTROLS_COPY_SIG',
    'CRT_HEAP_ALLOC',
    'DIAGNOSTICS_UPDATE_SIG',
    'DIALOGUE_MERGE_SIG',
    'DXT5_SIZE_SIG',
    'FIND_MARKER_RE',
    'FIRST_OPCODE',
    'HEAP_FORWARD',
    'HEAP_FUNCTIONS',
    'HEAP_NEW',
    'HEAP_WRAPPERS',
    'INLINE_DRIVE',
    'LEAN_LAUNCH_SIG',
    'LEAN_NO_REBOOT_SIG',
    'LEAN_PLAYER_SIG',
    'LEAN_PRELOAD_SIG',
    'LEAN_RECORD_SIG',
    'LEAN_TAG_SIG',
    'LOCATORS',
    'MCP102_ACTN_SIG',
    'MCP102_FOUND_JUMP',
    'MCP102_STORE',
    'MCP123_ADD_SIG',
    'MCP125_ADD_MOB_SIG',
    'MCP125_ATTACH_SIG',
    'MCP125_COLLISION_SIG',
    'MCP125_POSITION_A_SIG',
    'MCP125_POSITION_B_SIG',
    'MCP146_READY_SIG',
    'MCP146_RESUME_SIG',
    'MCP146_SIG',
    'MCP154_LOAD_SIG',
    'MCP154_RELOAD_SIG',
    'MCP154_REPLACED',
    'MCP37_SITE_SIG',
    'MCP37_TREE_NEXT_SIG',
    'MCP3_UNARMORED_SIG',
    'MCP92_RETIRE_MAGIC_SIG',
    'MCP92_UNSUMMON_SIG',
    'MCP94_SIGS',
    'MCP97_FIXED_IMM',
    'MCP97_LENGTH_CASE',
    'MCP97_LENGTH_REPLACED',
    'MCP97_SCAN_SIG',
    'MCP98_REFCOUNT_SIG',
    'MEDIA_ANY',
    'MENU_GATE_RE',
    'MOBILE_PLAYER_SIG',
    'MOB_GATE_RE',
    'OPCODE_HI',
    'OPCODE_LO',
    'PATCHES',
    'PATCH_BITS',
    'POOL_NAME',
    'POOL_PROLOGUE',
    'PREFERENCES_LOAD_SIG',
    'PROFILE_LIST_SITES',
    'PatchError',
    'REF_INDEX_FIND_SIG',
    'REF_INDEX_OFF',
    'REF_INDEX_SCRIPTS_SIG',
    'REF_INDEX_SCRIPT_CALLS',
    'REF_INDEX_SIG',
    'REF_LOAD_OFF',
    'REF_LOAD_SIG',
    'REF_SKIP_SIG',
    'REGION_ANY',
    'RUNFN_DISPATCH',
    'RUNFN_PROLOGUE',
    'SAVE_ALLOWED_SIG',
    'SAVE_STAGING',
    'SCENE_EDITS',
    'SCRIPT_DECODE_SIG',
    'SLEEP_LOOPS',
    'SLEEP_LOOP_SIG',
    'SLEEP_SIG',
    'TEST_PATCHES',
    'TITLE_ID',
    'TRANSITION_CALL_SIGS',
    'VIDEO_CREATE_SIG',
    'VIDEO_MODES_SIG',
    'VIDEO_RENDERER_SIG',
    'VK_KEY_LIMIT_SIG',
    'VK_SPACE_LIMIT_SIG',
    'WEATHER_ROLL_RE',
    'XAPI_HEAP_FREE',
    '_agent',
    '_boot_media',
    '_bow_view',
    '_build_preferences',
    '_console',
    '_data_overlay',
    '_diagnostics',
    '_dialogue_merge',
    '_drive_letters',
    '_dxt5_size',
    '_find_in',
    '_find_mcp154_site',
    '_format_ranges',
    '_hd_video',
    '_heap_census',
    '_heap_region',
    '_info_name_arena',
    '_lean_menu',
    '_loop_sleeps',
    '_mcp_1',
    '_mcp_102',
    '_mcp_123',
    '_mcp_125',
    '_mcp_146',
    '_mcp_154',
    '_mcp_3',
    '_mcp_37',
    '_mcp_92',
    '_mcp_94',
    '_mcp_97',
    '_mcp_98',
    '_mem_census',
    '_multi_bsa',
    '_multiplayer',
    '_mwse_legacy',
    '_net',
    '_payload',
    '_profile',
    '_ref_index',
    '_rotating_autosaves',
    '_save_staging',
    '_script_ext',
    '_test_dialoguemerge',
    '_test_mcp102',
    '_test_mcp3',
    '_test_mcp97',
    '_title',
    '_title_id',
    '_transition_autosaves',
    '_unique_call',
    '_validate_patch_changes',
    '_video_arena',
    'drive_letter',
    'find_arena_size',
    'find_autosave_calls',
    'find_bow_view',
    'find_button_pressed',
    'find_call_sites',
    'find_command_table',
    'find_console_gate',
    'find_console_print',
    'find_controls_table',
    'find_diagnostics_update',
    'find_dialogue_merge',
    'find_dxt5_size',
    'find_game_instance',
    'find_hd_video',
    'find_heap_function',
    'find_heap_object',
    'find_heap_region',
    'find_heap_region_sites',
    'find_info_arena_sites',
    'find_lean_menu',
    'find_marker',
    'find_mcp102_actn',
    'find_mcp123_add',
    'find_mcp125_collision',
    'find_mcp125_context',
    'find_mcp146',
    'find_mcp146_context',
    'find_mcp154_load',
    'find_mcp154_reload',
    'find_mcp37_context',
    'find_mcp3_unarmored',
    'find_mcp92_unsummon',
    'find_mcp94',
    'find_mcp97_scan',
    'find_mcp98_refcounts',
    'find_menu_mode_gate',
    'find_mob_gate',
    'find_preferences_load',
    'find_ref_index',
    'find_ref_load',
    'find_ref_skip',
    'find_run_function',
    'find_save_allowed_context',
    'find_save_this_ptr',
    'find_script_decode_state',
    'find_script_fixup_call',
    'find_script_ip_restore_call',
    'find_script_ip_restore_site',
    'find_transition_calls',
    'find_unique',
    'find_vk_limit',
    'find_weather_roll',
    'find_world_controller',
    'find_xapi_heap_alloc',
    'find_xapi_heap_free',
    'heap_call_sites',
    'heap_new_site',
    'heap_pool_site',
    'heap_wrapper_sites',
    'main',
    'patch',
    'retail_digest',
    'scene_form',
    'test_patch',
    'text_section',
    'widen_opcode_bounds',
]
