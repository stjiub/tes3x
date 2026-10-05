#!/usr/bin/env python3
"""Build, patch, pack and optionally deploy one TES3X profile."""

import argparse
import filecmp
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import time
import subprocess
import sys
import tempfile
import tomllib
from xml.sax.saxutils import escape

from tes3x_pack import set_ini_key, write_invalidation
from tes3x_patch import retail_digest
import tes3x_patches as registry
from tes3x_payload import PayloadError, build_payload, find_tool
from tes3x_net import write_ghost_plugin
from tes3x_agent import key_fingerprint, load_or_create_key
from tes3x_paths import DEFAULT_REMOTE_ROOT, require_paths
from tes3x_plugins import rules_file
import tes3x_manifest
import tes3x_savepool
import tes3x_targets


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
MARKER = ".tes3x-pipeline.json"
# tes3x_deploy exits with this when the target belongs to something else.
DEPLOY_CONFLICT = 3
# tes3x_deploy exits with this when the dashboard agent reports too little free space.
DEPLOY_NO_SPACE = 4
MARKER_SCHEMA = 2
REPLACED_RETAIL_ENTRIES = {"data files", "default.xbe", "morrowind.xbe", "morrowind.ini"}
RELEASE_ARTIFACT_SUFFIXES = {".iso", ".nfo", ".rar", ".sfv"}
OVERLAY_BASE_EXCLUDES = {"default.xbe", "morrowind.xbe", "morrowind.ini", "_resources"}

PATCHES = {entry["name"]: entry for entry in registry.PATCHES if entry["selection"] == "preset"}
PATCH_ORDER = tuple(entry["name"] for entry in registry.PATCHES
                    if entry["selection"] in ("packaging", "preset")
                    or entry["name"] == "build-preferences")
HOOK_SOURCES = {entry["name"]: entry.get("source") for entry in registry.PATCHES}
SOURCE_DEPENDENCIES = {
    "tes3xinfoarena.c": ("tes3xpager.c",),
    "tes3xmwse.c": ("tes3xconsole.c",),
    "tes3xmulti.c": ("tes3xnet.c",),
    "tes3xagent.c": ("tes3xnet.c",),
}
CATEGORIES = set(registry.CATEGORIES)
PRESETS = ("minimal", "recommended", "testing")
PRESET_ALIASES = {"standard": "recommended", "dev": "testing"}
PACKAGE_MODES = ("delta-bsa", "merged-bsa", "loose")
INSTALL_LAYOUTS = ("full", "overlay")


class PipelineError(ValueError):
    pass


def read_toml(path):
    with open(path, "rb") as stream:
        return tomllib.load(stream)


def string_list(value, field):
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise PipelineError(f"{field} must be an array of strings")
    return value


def validate_profile(profile):
    """Reject unknown or ill-typed profile fields before resolving a build."""
    if not isinstance(profile, dict):
        raise PipelineError("profile must be a TOML table")

    allowed_sections = {"profile", "rules", "patches", "preferences", "package", "ini", "mods",
                        "plugins"}
    unknown = set(profile) - allowed_sections
    if unknown:
        raise PipelineError("unknown profile sections: " + ", ".join(sorted(unknown)))

    def table(name):
        value = profile.get(name, {})
        if not isinstance(value, dict):
            raise PipelineError(f"{name} must be a table")
        return value

    def known(values, allowed, field):
        extra = set(values) - set(allowed)
        if extra:
            raise PipelineError(f"unknown {field} keys: " + ", ".join(sorted(extra)))

    def typed(values, key, expected, parent):
        if key in values and type(values[key]) not in expected:
            labels = {str: "a string", int: "an integer", bool: "a boolean"}
            names = " or ".join(labels.get(kind, kind.__name__) for kind in expected)
            raise PipelineError(f"{parent}.{key} must be {names}")

    identity = table("profile")
    known(identity, {"name", "title", "install_dir", "remote_root", "library", "dashboards",
                     "save_pool", "save_pool_id", "install_layout"}, "profile")
    for key in ("name", "title", "install_dir", "remote_root", "library", "save_pool",
                "save_pool_id", "install_layout"):
        typed(identity, key, (str,), "profile")
    if not identity.get("name"):
        raise PipelineError("profile.name is required")
    if identity.get("save_pool_id") and not identity.get("save_pool"):
        raise PipelineError("profile.save_pool_id needs profile.save_pool")
    if identity.get("install_layout", "full") not in INSTALL_LAYOUTS:
        raise PipelineError("profile.install_layout must be one of " + ", ".join(INSTALL_LAYOUTS))
    if identity.get("save_pool"):
        try:
            tes3x_savepool.pool_id(identity["save_pool"], identity.get("save_pool_id"))
        except ValueError as exc:
            raise PipelineError(f"profile.{exc}")
    string_list(identity.get("dashboards"), "profile.dashboards")

    rules = table("rules")
    known(rules, {"max_texture_size", "max_filename", "convert_all_textures", "exclude",
                  "keep_assets", "clear_cache_partitions", "plugin_order", "tes3merge"}, "rules")
    if rules.get("plugin_order", "mods") not in {"mods", "mlox"}:
        raise PipelineError("rules.plugin_order must be 'mods' or 'mlox'")
    for key in ("max_texture_size", "max_filename"):
        typed(rules, key, (int,), "rules")
        if key in rules and rules[key] <= 0:
            raise PipelineError(f"rules.{key} must be greater than zero")
    if rules.get("max_filename", 42) > 42:
        raise PipelineError("rules.max_filename cannot exceed the FATX limit of 42")
    for key in ("convert_all_textures", "clear_cache_partitions", "tes3merge"):
        typed(rules, key, (bool,), "rules")
    string_list(rules.get("exclude"), "rules.exclude")
    string_list(rules.get("keep_assets"), "rules.keep_assets")

    plugins = table("plugins")
    known(plugins, {"order"}, "plugins")
    string_list(plugins.get("order"), "plugins.order")
    if plugins.get("order") and rules.get("plugin_order") == "mlox":
        raise PipelineError("plugins.order and rules.plugin_order = 'mlox' both set the load "
                            "order; keep one")

    patches = table("patches")
    known(patches, {"preset", "categories", "enable", "disable"}, "patches")
    typed(patches, "preset", (str,), "patches")
    for key in ("categories", "enable", "disable"):
        string_list(patches.get(key), f"patches.{key}")

    preferences = table("preferences")
    known(preferences, {"invert_look"}, "preferences")
    typed(preferences, "invert_look", (bool,), "preferences")

    package = table("package")
    known(package, {"mode", "archive_name", "archive_only", "drive_letter", "loose_assets"},
          "package")
    for key in ("mode", "archive_name", "drive_letter"):
        typed(package, key, (str,), "package")
    if package.get("mode", "delta-bsa") not in PACKAGE_MODES:
        raise PipelineError("package.mode must be one of " + ", ".join(PACKAGE_MODES))
    typed(package, "archive_only", (bool,), "package")
    if package.get("mode") == "loose" and package.get("archive_only"):
        raise PipelineError("package.archive_only cannot be used with mode = 'loose'")
    string_list(package.get("loose_assets"), "package.loose_assets")

    ini = table("ini")
    for key, value in ini.items():
        section, separator, setting = key.partition(":")
        if not separator or not section.strip() or not setting.strip():
            raise PipelineError(f"ini key must be SECTION:KEY, got {key!r}")
        if type(value) not in (str, int, float, bool):
            raise PipelineError(f"ini.{key} must be a string, number or boolean")

    mods = profile.get("mods", [])
    if not isinstance(mods, list):
        raise PipelineError("mods must be an array of tables")
    for index, mod in enumerate(mods, 1):
        field = f"mods[{index}]"
        if not isinstance(mod, dict):
            raise PipelineError(f"{field} must be a table")
        known(mod, {"name", "id", "version", "components", "order", "enabled", "optional",
                    "plugins", "loose", "archives"}, field)
        if mod.get("archives", "unpack") not in {"unpack", "load"}:
            raise PipelineError(f"{field}.archives must be 'unpack' or 'load'")
        typed(mod, "name", (str,), field)
        typed(mod, "id", (str,), field)
        typed(mod, "version", (str,), field)
        if bool(mod.get("name")) == bool(mod.get("id")):
            raise PipelineError(f"{field} needs exactly one of name or id")
        typed(mod, "order", (int,), field)
        for key in ("enabled", "optional", "loose"):
            typed(mod, key, (bool,), field)
        string_list(mod.get("plugins"), f"{field}.plugins")
        string_list(mod.get("components"), f"{field}.components")


def validate_local_config(local):
    """Validate the public tables while leaving private extension tables alone."""
    unknown = set(local) - {"default_target", "targets", "paths", "deploy", "xemu", "rig",
                            "addons", "console", "server"}
    if unknown:
        raise PipelineError("unknown local config sections: " + ", ".join(sorted(unknown)))
    addons = local.get("addons", {})
    if not isinstance(addons, dict) or any(type(value) is not bool for value in addons.values()):
        raise PipelineError("addons must map add-on names to true or false")
    for section in ("paths", "deploy"):
        if section in local and not isinstance(local[section], dict):
            raise PipelineError(f"{section} must be a table")

    paths = local.get("paths", {})
    extra = set(paths) - {"vanilla_root", "mod_library", "build_root", "llvm", "hardlink_retail",
                          "profiles", "mlox_rules", "pc_morrowind", "ghidra", "tes3merge", "nxdk",
                          "msys2"}
    if extra:
        raise PipelineError("unknown paths keys: " + ", ".join(sorted(extra)))
    for key in ("vanilla_root", "mod_library", "build_root", "llvm", "profiles", "mlox_rules",
                "pc_morrowind", "tes3merge", "nxdk", "msys2"):
        if key in paths and type(paths[key]) is not str:
            raise PipelineError(f"paths.{key} must be a string")
    if "hardlink_retail" in paths and type(paths["hardlink_retail"]) is not bool:
        raise PipelineError("paths.hardlink_retail must be a boolean")

    deploy = local.get("deploy", {})
    extra = set(deploy) - {"host", "port", "user", "password", "remote_root", "retail_root",
                           "agent_token"}
    if extra:
        raise PipelineError("unknown deploy keys: " + ", ".join(sorted(extra)))
    for key in ("host", "user", "password", "remote_root", "retail_root", "agent_token"):
        if key in deploy and type(deploy[key]) is not str:
            raise PipelineError(f"deploy.{key} must be a string")
    if "port" in deploy and (type(deploy["port"]) is not int
                             or not 1 <= deploy["port"] <= 65535):
        raise PipelineError("deploy.port must be an integer from 1 to 65535")
    if deploy.get("agent_token") and not re.fullmatch(r"[0-9a-f]{64}", deploy["agent_token"]):
        raise PipelineError("deploy.agent_token must be 64 lowercase hex digits")

    if "default_target" in local and type(local["default_target"]) is not str:
        raise PipelineError("default_target must be a string")
    targets = local.get("targets", {})
    if not isinstance(targets, dict):
        raise PipelineError("targets must be a table")
    allowed = {"kind", "host", "port", "user", "password", "games_root", "retail_root",
               "agent_token", "dashboard", "ram", *tes3x_targets.XEMU_KEYS}
    for name, target in targets.items():
        if not isinstance(target, dict):
            raise PipelineError(f"targets.{name} must be a table")
        extra = set(target) - allowed
        if extra:
            raise PipelineError(f"unknown targets.{name} keys: " + ", ".join(sorted(extra)))
        if target.get("kind") not in tes3x_targets.TARGET_KINDS:
            raise PipelineError(f"targets.{name}.kind must be 'xbox' or 'xemu'")
        strings = ("host", "user", "password", "games_root", "retail_root", "agent_token",
                   "dashboard", *tes3x_targets.XEMU_KEYS)
        for key in strings:
            if key in target and type(target[key]) is not str:
                raise PipelineError(f"targets.{name}.{key} must be a string")
        if "port" in target and (type(target["port"]) is not int
                                 or not 1 <= target["port"] <= 65535):
            raise PipelineError(f"targets.{name}.port must be an integer from 1 to 65535")
        if "ram" in target and (type(target["ram"]) is not int or target["ram"] not in (64, 128)):
            raise PipelineError(f"targets.{name}.ram must be 64 or 128")
        if target.get("agent_token") and not re.fullmatch(r"[0-9a-f]{64}",
                                                          target["agent_token"]):
            raise PipelineError(f"targets.{name}.agent_token must be 64 lowercase hex digits")
        if target["kind"] == "xbox" and not target.get("games_root"):
            raise PipelineError(f"targets.{name}.games_root is required for an Xbox target")
    if local.get("default_target") and local["default_target"] not in tes3x_targets.targets(local):
        raise PipelineError(f"default_target {local['default_target']!r} is not configured")


def enabled_mods(profile):
    return [mod for mod in profile.get("mods", []) if mod.get("enabled", True)]


def resolve_patch_plan(profile, preset_override=None, enable=(), disable=(), package_mode=None):
    """Resolve user-facing patches and packaging-derived infrastructure."""
    config = profile.get("patches", {})
    preset = preset_override or config.get("preset", "recommended")
    preset = PRESET_ALIASES.get(preset, preset)
    if preset not in PRESETS:
        raise PipelineError(f"unknown patch preset {preset!r}; choose from {', '.join(PRESETS)}")

    selected = set()
    if preset == "recommended":
        selected.update(name for name, meta in PATCHES.items()
                        if meta["category"] in {"core", "correctness"}
                        and meta["channel"] == "release")
        if PATCHES["console"]["channel"] == "release":
            selected.add("console")
    if preset == "testing":
        selected.update(name for name, meta in PATCHES.items()
                        if meta["category"] in {"core", "correctness"}
                        and meta["channel"] in {"preview", "release"})
        for name in ("diagnostics", "console"):
            if PATCHES[name]["channel"] in {"preview", "release"}:
                selected.add(name)

    categories = set(string_list(config.get("categories"), "patches.categories"))
    unknown_categories = categories - CATEGORIES
    if unknown_categories:
        raise PipelineError("unknown patch categories: " + ", ".join(sorted(unknown_categories)))
    selected.update(name for name, meta in PATCHES.items() if meta["category"] in categories)

    enabled = set(string_list(config.get("enable"), "patches.enable"))
    disabled = set(string_list(config.get("disable"), "patches.disable"))
    selectable = set(PATCHES) | {"build-preferences"}
    unknown = (enabled | disabled | set(enable) | set(disable)) - selectable
    if unknown:
        raise PipelineError("unknown selectable patches: " + ", ".join(sorted(unknown)))
    for both, where in ((enabled & disabled, "the profile"),
                        (set(enable) & set(disable), "the command line")):
        if both:
            raise PipelineError("patches both enabled and disabled in %s: %s"
                                % (where, ", ".join(sorted(both))))
    selected.update(enabled)
    selected.difference_update(disabled)
    # The command line is the later word, so --disable overrides a profile's enable.
    # Bisecting a profile is not a contradiction.
    selected.update(enable)
    selected.difference_update(disable)

    layout = profile.get("profile", {}).get("install_layout", "full")
    if layout == "overlay":
        if "data-overlay" in disabled or "data-overlay" in disable:
            raise PipelineError("the overlay install layout requires data-overlay")
        selected.add("data-overlay")

    blocked = disabled | set(disable)
    pending = list(selected)
    while pending:
        name = pending.pop()
        for dependency in registry.BY_NAME[name].get("requires", []):
            if dependency in blocked:
                raise PipelineError(f"{name} requires {dependency}")
            if dependency not in selected:
                selected.add(dependency)
                pending.append(dependency)

    if enabled_mods(profile):
        mode = package_mode or profile.get("package", {}).get("mode", "delta-bsa")
        if mode not in PACKAGE_MODES:
            raise PipelineError("package.mode must be one of " + ", ".join(PACKAGE_MODES))
    else:
        mode = "retail"
    applied = set(selected)
    if mode == "delta-bsa" or (mode != "retail" and any(
            mod.get("archives") == "load" for mod in enabled_mods(profile))):
        applied.add("multi-bsa")
    if profile.get("preferences") and "build-preferences" not in disabled \
            and "build-preferences" not in disable:
        applied.add("build-preferences")

    sources = ["tes3xhook.c", "tes3xlog.c"]
    for name in PATCH_ORDER:
        source = HOOK_SOURCES.get(name)
        if name in applied and source and source not in sources:
            for dependency in SOURCE_DEPENDENCIES.get(source, ()):
                if dependency not in sources:
                    sources.append(dependency)
            sources.append(source)
    return {
        "preset": preset,
        "selected": [name for name in PATCH_ORDER if name in selected],
        "applied": [name for name in PATCH_ORDER if name in applied],
        "sources": sources,
        "package_mode": mode,
        "needs_payload": bool(applied),
    }


def preference_flags(profile):
    preferences = profile.get("preferences", {})
    if not preferences:
        return ""
    return "-DTES3X_INVERT_LOOK=%d" % int(preferences.get("invert_look", True))


def config_path(value, base):
    path = Path(value).expanduser()
    return path if path.is_absolute() else base / path


def require_file(path, label):
    if not path.is_file():
        raise PipelineError(f"{label} not found: {path}")


def run(command):
    print("\n== " + " ".join(str(part) for part in command), flush=True)
    subprocess.run([str(part) for part in command], check=True)


def source_revision():
    """The TES3X commit a build came from, marked dirty when files differ from it."""
    try:
        commit = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short=12", "HEAD"],
                                capture_output=True, text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain",
                                "--untracked-files=no"],
                               capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    return commit + ("-dirty" if dirty else "")


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def tree_digest(root):
    """Hash a tree without exposing its source paths in the build record."""
    digest = hashlib.sha256()
    for path in sorted((p for p in Path(root).rglob("*") if p.is_file()),
                       key=lambda p: p.relative_to(root).as_posix().lower()):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8") + b"\0")
        digest.update(str(path.stat().st_size).encode("ascii") + b"\0")
        digest.update(bytes.fromhex(sha256_file(path)))
    return digest.hexdigest()


def mod_inventory(profile):
    """The selected mod configuration, with no library or machine paths."""
    result = []
    for mod in sorted(enabled_mods(profile), key=lambda item: item.get("order", 0)):
        item = {key: mod[key] for key in ("name", "id", "version", "components") if key in mod}
        item["order"] = mod.get("order", 0)
        for key in ("plugins", "loose"):
            if key in mod:
                item[key] = mod[key]
        result.append(item)
    return result


def plugin_inventory(data_files):
    """Final loose plugin load order and content hashes."""
    plugins = [path for path in Path(data_files).iterdir()
               if path.is_file() and path.suffix.lower() in {".esm", ".esp"}]
    plugins.sort(key=lambda path: (path.stat().st_mtime_ns, path.name.lower()))
    return [{"name": path.name, "sha256": sha256_file(path)} for path in plugins]


def tool_version(path):
    try:
        result = subprocess.run([str(path), "--version"], capture_output=True, text=True,
                                timeout=10, check=True)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return Path(path).name
    return next((line.strip() for line in result.stdout.splitlines() if line.strip()),
                Path(path).name)


def sanitized_command(invocation, profile_name, profile_arg):
    """Keep the effective command shape while removing local filesystem locations."""
    result = ["python", "tools/tes3x_pipeline.py"]
    path_options = {"--config", "--vanilla", "--llvm", "--build-root", "--out"}
    replace_next, replaced_profile = False, False
    for value in invocation:
        if not replaced_profile and value == profile_arg:
            result.append(f"profile:{profile_name}")
            replaced_profile = True
        elif replace_next:
            result.append("<local-path>")
            replace_next = False
        else:
            option = next((item for item in path_options if value.startswith(item + "=")), None)
            result.append(option + "=<local-path>" if option else value)
            replace_next = value in path_options
    return result


def link_or_copy(source, target):
    """Hardlink a retail file into the build, or copy it where the volume cannot link.

    A staged retail file must never be modified in place: through a link, the write would
    reach the clean retail folder."""
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)
    return target


def copy_retail_root(vanilla, staged, copy=shutil.copy2):
    """Carry the Xbox disc-root payload without copying release or generated files."""
    copied = []
    for source in sorted(vanilla.iterdir(), key=lambda path: path.name.lower()):
        suffix = source.suffix.lower()
        release_part = len(suffix) == 4 and suffix[1] == "r" and suffix[2:].isdigit()
        if (source.name.lower() in REPLACED_RETAIL_ENTRIES
                or suffix in RELEASE_ARTIFACT_SUFFIXES or release_part):
            continue
        target = staged / source.name
        if source.is_dir():
            shutil.copytree(source, target, copy_function=copy)
            copied.extend(path for path in source.rglob("*") if path.is_file())
        elif source.is_file():
            copy(source, target)
            copied.append(source)
        else:
            raise PipelineError(f"unsupported retail root entry: {source}")
    return len(copied), sum(path.stat().st_size for path in copied)


def stage_retail_base(vanilla, staged, copy=shutil.copy2):
    """Stage the clean data tree shared by overlay installs, without a dashboard-visible XBE."""
    Path(staged).mkdir(parents=True, exist_ok=True)
    copied = []
    for source in sorted(Path(vanilla).iterdir(), key=lambda path: path.name.lower()):
        suffix = source.suffix.lower()
        release_part = len(suffix) == 4 and suffix[1] == "r" and suffix[2:].isdigit()
        if (source.name.lower() in OVERLAY_BASE_EXCLUDES
                or suffix in RELEASE_ARTIFACT_SUFFIXES or release_part):
            continue
        target = Path(staged) / source.name
        if source.is_dir():
            shutil.copytree(source, target, copy_function=copy)
            copied.extend(path for path in source.rglob("*") if path.is_file())
        elif source.is_file():
            copy(source, target)
            copied.append(source)
        else:
            raise PipelineError(f"unsupported retail base entry: {source}")
    return len(copied), sum(path.stat().st_size for path in copied)


def stage_default_xbe(launcher, engine, output, install_layout):
    """Use the patched engine as an overlay folder's dashboard entry."""
    source = engine if install_layout == "overlay" else launcher
    shutil.copy2(source, output)
    return source


def strip_retail_files(staged, vanilla):
    """Remove files the overlay can read unchanged from its clean retail base."""
    kept_roots = {"default.xbe", "morrowind.xbe", "morrowind.ini", "_resources"}
    removed = removed_bytes = 0
    for path in sorted((item for item in Path(staged).rglob("*") if item.is_file()),
                       reverse=True):
        relative = path.relative_to(staged)
        if relative.parts[0].lower() in kept_roots:
            continue
        retail = Path(vanilla) / relative
        if retail.is_file() and filecmp.cmp(path, retail, shallow=False):
            removed += 1
            removed_bytes += path.stat().st_size
            path.unlink()
    for path in sorted((item for item in Path(staged).rglob("*") if item.is_dir()),
                       key=lambda item: len(item.parts), reverse=True):
        try:
            path.rmdir()
        except OSError:
            pass
    return removed, removed_bytes


def stage_retail(data_files, ini, staged, ini_items, copy=shutil.copy2):
    """Stage unchanged retail Data Files and an adapted Morrowind.ini, for a build without mods."""
    shutil.copytree(data_files, staged / "Data Files", copy_function=copy)
    text = ini.read_text(encoding="latin-1")
    for item in ini_items:
        section, _, rest = item.partition(":")
        key, eq, value = rest.partition("=")
        if not section or not key or not eq:
            raise PipelineError(f"ini keys want SECTION:KEY=VALUE, got {item!r}")
        text = set_ini_key(text, section.strip(), key.strip(), value)
    (staged / "Morrowind.ini").write_text(text, encoding="latin-1")
    print(f"  retail Data Files staged unchanged; Morrowind.ini with {len(ini_items)} key(s) set")


def ini_override(items, wanted_section, wanted_key):
    """Last SECTION:KEY=VALUE override for one case-insensitive INI setting."""
    for item in reversed(items):
        section, separator, rest = item.partition(":")
        key, equals, value = rest.partition("=")
        if (separator and equals and section.strip().casefold() == wanted_section.casefold()
                and key.strip().casefold() == wanted_key.casefold()):
            return value
    return None


def agent_setting(base, target):
    """NetAgent for the selected target and the GUI identity beside the local config."""
    if not target:
        raise PipelineError("the agent patch needs a selected target or an explicit "
                            "Xbox:NetAgent INI value")
    if target.get("kind") == "xemu":
        host = "10.0.2.2"
    else:
        destination = target.get("host")
        if not destination:
            raise PipelineError("the agent patch needs the selected Xbox target's host")
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect((destination, 9))
            host = probe.getsockname()[0]
        except OSError as exc:
            raise PipelineError(f"cannot find this PC's address for {destination}: {exc}") from exc
        finally:
            probe.close()
    secret = load_or_create_key(base / "tes3x.agent.key")
    return f"{host}#{key_fingerprint(secret)}"


def agent_ini(applied, ini_items, base, target):
    """INI overrides the agent needs that the profile does not set itself."""
    if "agent" not in applied:
        return []
    items = []
    if ini_override(ini_items, "Xbox", "NetAgent") is None:
        items.append("Xbox:NetAgent=" + agent_setting(base, target))
    # Without an address the NIC stays off and the agent can never connect. Multiplayer builds
    # bring their own network settings.
    if "multiplayer" not in applied and ini_override(ini_items, "Xbox", "NetAddress") is None:
        items.append("Xbox:NetAddress=dhcp")
    return items


def dashboard_xml(title, folder, title_id=tes3x_savepool.SHARED_ID):
    """XBMC4Gamers lists a game by _resources/default.xml; the XBE title is only its fallback."""
    return ("<synopsis>\n"
            f"<sourcename>{escape(folder)}</sourcename>\n"
            f"<foldername>{escape(folder)}</foldername>\n"
            f"<title>{escape(title)}</title>\n"
            f"<titleid>{title_id:08X}</titleid>\n"
            "</synopsis>\n")


# Files a dashboard reads a game's name from, beyond the XBE title every dashboard falls back to.
DASHBOARDS = {
    "xbmc4gamers": ("_resources/default.xml", dashboard_xml),
}


def dashboard_list(profile):
    names = string_list(profile.get("profile", {}).get("dashboards", ["xbmc4gamers"]),
                        "profile.dashboards")
    unknown = set(names) - DASHBOARDS.keys()
    if unknown:
        raise PipelineError("unknown dashboards: " + ", ".join(sorted(unknown))
                            + "; choose from " + ", ".join(DASHBOARDS))
    return names


def write_dashboard_files(staged, names, title, folder, title_id=tes3x_savepool.SHARED_ID):
    for name in names:
        path, render = DASHBOARDS[name]
        target = staged / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render(title, folder, title_id), encoding="utf-8", newline="\r\n")


def validate_output(path):
    resolved = path.resolve()
    if resolved == Path(resolved.anchor) or resolved == Path.cwd().resolve():
        raise PipelineError(f"refusing to use broad output path: {resolved}")
    if path.exists():
        if not path.is_dir():
            raise PipelineError(f"output is not a directory: {path}")
        if any(path.iterdir()) and not (path / MARKER).is_file():
            raise PipelineError(f"existing output is not owned by tes3x_pipeline: {path}")


def rename_dir(src, dst, tries=10):
    """os.replace, retried: Windows refuses a directory rename while a scanner holds a file in it."""
    for attempt in range(tries):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == tries - 1:
                raise
            time.sleep(0.5 * (attempt + 1))


def publish(work, output):
    """Replace only an empty or pipeline-owned output after a complete build."""
    validate_output(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    backup = output.parent / ("." + output.name + ".previous")
    if backup.exists():
        if not (backup / MARKER).is_file():
            raise PipelineError(f"refusing to replace unowned backup: {backup}")
        shutil.rmtree(backup)
    if output.exists():
        os.replace(output, backup)
    try:
        rename_dir(work, output)
    except Exception:
        if backup.exists() and not output.exists():
            os.replace(backup, output)
        raise
    if backup.exists():
        shutil.rmtree(backup)


def main(argv=None):
    invocation = list(argv) if argv is not None else sys.argv[1:]
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("profile")
    ap.add_argument("--config", help="local paths and Xbox settings (default: ./tes3x.local.toml if present)")
    ap.add_argument("--target", help="machine target (default: default_target)")
    ap.add_argument("--vanilla", help="clean retail game root containing Data Files and both XBEs")
    ap.add_argument("--llvm", help="folder holding clang and lld-link, for engine fixes "
                                   "(default: paths.llvm, then PATH)")
    ap.add_argument("--build-root", help="parent for profile outputs")
    ap.add_argument("--out", help="complete pipeline output (default: BUILD_ROOT/PROFILE_NAME)")
    ap.add_argument("--preset", choices=PRESETS + tuple(PRESET_ALIASES))
    ap.add_argument("--enable", action="append", default=[], metavar="PATCH")
    ap.add_argument("--disable", action="append", default=[], metavar="PATCH")
    ap.add_argument("--package-mode", choices=PACKAGE_MODES,
                    help="override package.mode (default: the profile's)")
    ap.add_argument("--drive", help="game-directory drive letter (default: D)")
    ap.add_argument("--title", help="name both XBEs carry, so parallel installs are told "
                                    "apart in a dashboard (default: profile.title)")
    ap.add_argument("--save-staging", metavar="LETTER",
                    help="stage save files on this drive before committing them to UDATA")
    ap.add_argument("--profile-target", action="append", default=[], metavar="VA",
                    help="time this function with RDTSC at its direct call sites "
                         "(repeatable, or comma-separated). Instrumentation only: the "
                         "stubs stay installed whether or not anything reads them, so "
                         "this never comes from a profile or a preset")
    ap.add_argument("--heap-census", action="store_true",
                    help="count live heap memory by source file and line, dumped to "
                         "E:\\tes3xheap.bin. Instrumentation only: needs an 8 MB table, so run "
                         "it on 128 MB")
    ap.add_argument("--mem-census", action="store_true",
                    help="count kernel allocations and the XAPI heap by caller, with the "
                         "address space map, dumped to E:\\tes3xmem.bin. Instrumentation only: "
                         "needs about 5 MB of tables")
    ap.add_argument("--pager-test", action="store_true",
                    help="add the demand pager prototype, whose console command tes3xpager "
                         "runs a synthetic workload over a 64 MB paged region. Instrumentation "
                         "only: nothing in the engine is paged")
    ap.add_argument("--diag-test-faults", action="store_true",
                    help="add the diagnostics console commands `tes3xdiag hang` and `tes3xdiag "
                         "crash`, which stall the update loop and fault on purpose. Test only")
    ap.add_argument("--test-probe", choices=("mcp-3", "mcp-97", "mcp-102", "dialogue-merge"),
                    help="add a game-test-only trace hook for this patch")
    ap.add_argument("--hardlink", action=argparse.BooleanOptionalAction,
                    help="hardlink unchanged retail files into the build instead of copying "
                         "them, where the volume allows (default: paths.hardlink_retail)")
    ap.add_argument("--ini-set", action="append", default=[], metavar="SECTION:KEY=VALUE",
                    help="set a key in the staged Morrowind.ini (repeatable)")
    action = ap.add_mutually_exclusive_group()
    action.add_argument("--deploy", action="store_true",
                        help="build, then upload the changes to the configured Xbox over FTP")
    action.add_argument("--dry-run", action="store_true",
                        help="build normally, then list what --deploy would upload or delete "
                             "on the Xbox without changing it")
    ap.add_argument("--verify-deploy", choices=("none", "size", "hash"), default="none",
                    help="after --deploy, verify uploaded files; hash downloads each upload")
    ap.add_argument("--replace-remote", action="store_true",
                    help="deploy even where the target folder or save pool belongs to something "
                         "else")
    ap.add_argument("--install-retail-base", action="store_true",
                    help="with an overlay deployment, explicitly install or synchronize the "
                         "configured deploy.retail_root before the profile")
    ap.add_argument("--ignore-space", action="store_true",
                    help="deploy even when the Xbox's dashboard agent reports too little free "
                         "space")
    ap.add_argument("--discard-build", action="store_true",
                    help="delete the regenerable pipeline output after a verified deployment")
    ap.add_argument("--ask-password", action="store_true",
                    help="type the Xbox FTP password at a prompt instead of reading it from "
                         "the local config")
    ap.add_argument("--check", action="store_true",
                    help="validate and resolve the profile, print the build summary, then stop")
    args = ap.parse_args(argv)
    if args.discard_build and (not args.deploy or args.verify_deploy == "none"):
        ap.error("--discard-build requires --deploy and --verify-deploy size or hash")
    if args.install_retail_base and not args.deploy:
        ap.error("--install-retail-base requires --deploy")

    profile_path = Path(args.profile).resolve()
    require_file(profile_path, "profile")
    profile = read_toml(profile_path)
    validate_profile(profile)
    profile_name = profile["profile"]["name"]

    if args.config:
        local_path = Path(args.config).resolve()
    else:
        candidate = Path.cwd() / "tes3x.local.toml"
        local_path = candidate if candidate.is_file() else None
    local = read_toml(local_path) if local_path else {}
    validate_local_config(local)
    base = local_path.parent if local_path else Path.cwd()
    paths = local.get("paths", {})
    try:
        target = tes3x_targets.resolve(local, args.target)
        deploy = target if target and target.get("kind") == "xbox" else {}
        remote = tes3x_targets.remote_root(profile, target) if deploy else None
        path_remote = tes3x_targets.path_check_root(local, profile, args.target)
    except tes3x_targets.TargetError as exc:
        raise PipelineError(str(exc)) from exc
    # A profile with an old absolute destination remains useful without a local target for
    # build-time FATX checks, though deployment still needs a configured Xbox.
    if path_remote is None and profile.get("profile", {}).get("remote_root"):
        path_remote = profile["profile"]["remote_root"]
    library_value = profile.get("profile", {}).get("library") or paths.get("mod_library")
    library = config_path(library_value, base).resolve() if library_value else None
    if enabled_mods(profile) and not library:
        raise PipelineError("set profile.library or paths.mod_library when mods are enabled")

    plan = resolve_patch_plan(profile, args.preset, args.enable, args.disable,
                              args.package_mode)
    install_layout = profile["profile"].get("install_layout", "full")
    if args.install_retail_base and install_layout != "overlay":
        ap.error("--install-retail-base requires profile.install_layout = 'overlay'")
    prof_targets = [item.strip() for value in args.profile_target
                    for item in value.split(",") if item.strip()]
    if prof_targets:
        if "tes3xprof.c" not in plan["sources"]:
            plan["sources"].append("tes3xprof.c")
        plan["needs_payload"] = True
    if args.heap_census:
        if "tes3xheap.c" not in plan["sources"]:
            plan["sources"].append("tes3xheap.c")
        plan["needs_payload"] = True
    if args.mem_census:
        if "tes3xmem.c" not in plan["sources"]:
            plan["sources"].append("tes3xmem.c")
        plan["needs_payload"] = True
    if args.pager_test:
        if "tes3xpager.c" not in plan["sources"]:
            plan["sources"].append("tes3xpager.c")
        plan["needs_payload"] = True
    if args.diag_test_faults and "tes3xdiag.c" not in plan["sources"]:
        raise PipelineError("--diag-test-faults needs the diagnostics patch")
    if args.test_probe:
        source = f"tes3xtest_{args.test_probe.replace('-', '')}.c"
        plan["sources"].append(source)
        plan["needs_payload"] = True
        if args.test_probe in {"mcp-3", "dialogue-merge"} \
                and "tes3xconsole.c" not in plan["sources"]:
            raise PipelineError(f"--test-probe {args.test_probe} needs the console patch")
    package = profile.get("package", {})
    drive = (args.drive or package.get("drive_letter", "D")).upper()
    if len(drive) != 1 or not drive.isalpha():
        raise PipelineError("package.drive_letter must be one letter")
    save_staging = args.save_staging.upper() if args.save_staging else None
    if save_staging and (len(save_staging) != 1 or not save_staging.isalpha()):
        raise PipelineError("--save-staging must be one drive letter")
    title = args.title or profile.get("profile", {}).get("title")
    pool_name = profile.get("profile", {}).get("save_pool")
    pool = (tes3x_savepool.pool_id(pool_name, profile["profile"].get("save_pool_id"))
            if pool_name else None)
    dashboards = dashboard_list(profile)
    ini_items = [f"{k}={v}" for k, v in profile.get("ini", {}).items()] + args.ini_set
    ini_items += agent_ini(plan["applied"], ini_items, base, target)
    overlay_base = ini_override(ini_items, "Xbox", "OverlayBase")
    if install_layout == "overlay":
        overlay_base = overlay_base or deploy.get("retail_root")
        if not overlay_base:
            raise PipelineError("the overlay install layout needs target.retail_root in the "
                                "local config, or an Xbox:OverlayBase INI override")
        if ini_override(ini_items, "Xbox", "OverlayBase") is None:
            ini_items.append("Xbox:OverlayBase=" + overlay_base.replace("/", "\\"))
        if remote and deploy.get("retail_root") and \
                remote.replace("\\", "/").rstrip("/").casefold() == \
                deploy["retail_root"].replace("\\", "/").rstrip("/").casefold():
            raise PipelineError("the profile destination and target.retail_root must be different")
    build_value = args.build_root or paths.get("build_root", "build")
    build_root = config_path(build_value, base).resolve()
    output = Path(args.out).resolve() if args.out else build_root / profile_name
    validate_output(output)

    print(f"profile: {profile_name}")
    print(f"preset: {plan['preset']}")
    print("patches: " + ", ".join(["boot-media", f"drive-letters={drive}"] + plan["applied"]))
    if profile.get("preferences"):
        print("preferences: invert_look=%s" % str(
            profile["preferences"].get("invert_look", True)).lower())
    if title:
        print(f"title: {title}; dashboard files: {', '.join(dashboards) or 'none'}")
    if pool:
        print(f"save pool: {pool_name} (E:/{tes3x_savepool.folder(pool)})")
    if save_staging:
        print(f"save staging: {save_staging}:")
    if prof_targets:
        print("profiler: " + ", ".join(prof_targets))
    if args.heap_census:
        print("heap census: on")
    if args.mem_census:
        print("memory census: on")
    if args.pager_test:
        print("pager test: on")
    if args.diag_test_faults:
        print("diagnostics fault commands: on")
    if args.test_probe:
        print(f"test probe: {args.test_probe}")
    use_mlox = (plan["package_mode"] != "retail"
                and profile.get("rules", {}).get("plugin_order") == "mlox")
    listed_order = (plan["package_mode"] != "retail"
                    and profile.get("plugins", {}).get("order", []))
    use_merge = (plan["package_mode"] != "retail"
                 and profile.get("rules", {}).get("tes3merge", False))
    if plan["package_mode"] == "retail":
        print("mods: none; retail Data Files are staged unchanged")
    else:
        print(f"mods: {len(enabled_mods(profile))}, packed as {plan['package_mode']}")
        print("plugin order: " + ("mlox at build time" if use_mlox
                                  else "saved order" if listed_order else "mod order"))
        if use_merge:
            print("conflict patch: TES3Merge")
    print(f"output: {output}")
    print(f"install layout: {install_layout}"
          + (f"; retail base {overlay_base}" if install_layout == "overlay" else ""))
    if args.deploy or args.dry_run:
        print(f"target: {target.get('name', '<missing>') if target else '<missing>'} "
              f"{deploy.get('host', '<missing>')} {remote or '<missing>'}")
    if args.check:
        return 0

    vanilla_value = args.vanilla or paths.get("vanilla_root")
    if not vanilla_value:
        raise PipelineError("set paths.vanilla_root in local config or pass --vanilla")
    vanilla = config_path(vanilla_value, base).resolve()
    data_files = vanilla / "Data Files"
    retail_xbe = vanilla / "morrowind.xbe"
    launcher = vanilla / "Default.xbe"
    ini = vanilla / "Morrowind.ini"
    for path, label in ((retail_xbe, "retail morrowind.xbe"), (launcher, "retail Default.xbe"),
                        (ini, "retail Morrowind.ini")):
        require_file(path, label)
    if not data_files.is_dir():
        raise PipelineError(f"retail Data Files not found: {data_files}")

    hardlink = args.hardlink if args.hardlink is not None else paths.get("hardlink_retail", False)
    copy = link_or_copy if hardlink else shutil.copy2
    llvm_value = args.llvm or paths.get("llvm")
    llvm = config_path(llvm_value, base).resolve() if llvm_value else None
    toolchain = {}
    if plan["needs_payload"]:
        # Fail before any copying, not halfway through the build.
        clang = os.environ.get("CLANG") or find_tool("clang", llvm)
        lld = os.environ.get("LLD") or find_tool("lld-link", llvm)
        toolchain = {"clang": tool_version(clang), "lld-link": tool_version(lld)}
    if use_mlox:
        try:
            mlox_rules = rules_file(paths.get("mlox_rules")
                                    and config_path(paths["mlox_rules"], base)).resolve()
        except RuntimeError as exc:
            raise PipelineError(str(exc)) from exc
        require_file(mlox_rules, "mlox rules")
    if use_merge:
        if not paths.get("tes3merge"):
            raise PipelineError("rules.tes3merge needs paths.tes3merge, the TES3Merge.exe to run")
        tes3merge = config_path(paths["tes3merge"], base).resolve()
        require_file(tes3merge, "TES3Merge")
    if (args.deploy or args.dry_run) and (not deploy.get("host") or not remote):
        raise PipelineError("deployment requires an Xbox target with host and games_root")

    # Beside the output, so publishing is a rename on one volume.
    output.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f".{profile_name}-", dir=output.parent))
    tree = work / "tree"
    manifest = work / "manifest.json"
    mod_archives = work / "archives.json"
    hook_out = work / "hooks"
    patched = work / "morrowind.xbe"
    staged = work / "deploy"
    has_mods = plan["package_mode"] != "retail"
    try:
        if has_mods:
            build_cmd = [sys.executable, TOOLS / "tes3x_build.py", profile_path,
                         "--out", tree, "--json", manifest, "--vanilla", data_files,
                         "--archive-list", mod_archives]
            if library:
                build_cmd += ["--library", library]
            if path_remote:
                build_cmd += ["--remote-root", path_remote]
            run(build_cmd)

        payload = hook_out / "tes3xhook.pe"
        if plan["needs_payload"]:
            print("\n== payload: " + " ".join(plan["sources"]), flush=True)
            build_payload(retail_xbe, plan["sources"], hook_out,
                          user_flags=" ".join(filter(None, [
                              preference_flags(profile),
                              "-DTES3X_DIAG_TEST_FAULTS" if args.diag_test_faults else "",
                              os.environ.get("EXTRA_CFLAGS", "")])),
                          llvm_dir=llvm,
                          check_xbe=hook_out / "injected-check.xbe")

        patch_specs = []
        if plan["needs_payload"]:
            patch_specs.append(f"payload={payload}")
        patch_specs.extend(("boot-media", f"drive-letters={drive}"))
        if save_staging:
            patch_specs.append(f"save-staging={save_staging}")
        if title:
            patch_specs.append(f"title={title}")
        if pool:
            patch_specs.append(f"title-id={pool:08X}")
        patch_specs.extend(plan["applied"])
        if args.test_probe:
            patch_specs.append("test-" + args.test_probe.replace("-", ""))
        if prof_targets:
            patch_specs.append("profile=" + ",".join(prof_targets))
        if args.heap_census:
            patch_specs.append("heap-census")
        if args.mem_census:
            patch_specs.append("mem-census")
        patch_cmd = [sys.executable, TOOLS / "tes3x_patch.py", retail_xbe]
        for spec in patch_specs:
            patch_cmd += ["--apply", spec]
        patch_cmd += ["--out", patched]
        run(patch_cmd)

        if use_mlox:
            load_order = work / "mlox-order.json"
            run([sys.executable, TOOLS / "tes3x_plugins.py", "order", tree,
                 "--vanilla", data_files, "--rules", mlox_rules,
                 "--work", work / "mlox", "--out", load_order])
        elif listed_order:
            load_order = work / "profile-order.json"
            listed = work / "profile-order-input.json"
            listed.write_text(json.dumps(listed_order), encoding="utf-8")
            run([sys.executable, TOOLS / "tes3x_plugins.py", "arrange", tree,
                 "--vanilla", data_files, "--order", listed,
                 "--work", work / "arrange", "--out", load_order])

        if has_mods and use_merge:
            merged = tree / "Merged Objects.esp"
            merge_cmd = [sys.executable, TOOLS / "tes3x_plugins.py", "merge", tree,
                         "--vanilla", data_files, "--tool", tes3merge,
                         "--work", work / "tes3merge", "--out", merged]
            if use_mlox or listed_order:
                merge_cmd += ["--order", load_order]
            run(merge_cmd)
            if merged.is_file() and (use_mlox or listed_order):
                order_record = json.loads(load_order.read_text(encoding="utf-8"))
                order_record["plugins"].append(merged.name)
                load_order.write_text(json.dumps(order_record, indent=2), encoding="utf-8")

        if has_mods:
            pack_cmd = [sys.executable, TOOLS / "tes3x_pack.py", tree,
                        "--vanilla", data_files, "--ini", ini, "--out", staged,
                        "--mod-archives", mod_archives]
            if use_mlox or listed_order:
                pack_cmd += ["--load-order", load_order]
            if plan["package_mode"] == "loose":
                pack_cmd.append("--no-archive")
            if plan["package_mode"] == "delta-bsa":
                pack_cmd += ["--delta-archive", package.get("archive_name", "tes3xmods.bsa")]
            if package.get("archive_only", False):
                pack_cmd.append("--archive-only")
            for pattern in package.get("loose_assets", []):
                pack_cmd += ["--loose-asset", pattern]
            loose_mods = [m["name"] for m in enabled_mods(profile) if m.get("loose", False)]
            if loose_mods:
                pack_cmd += ["--manifest", manifest]
                for name in loose_mods:
                    pack_cmd += ["--loose-mod", name]
            for item in ini_items:
                pack_cmd += ["--ini-set", item]
            if path_remote:
                pack_cmd += ["--remote-root", path_remote]
            run(pack_cmd)
        else:
            stage_retail(data_files, ini, staged, ini_items, copy)

        if "multiplayer" in plan["applied"]:
            ghost = write_ghost_plugin(staged / "Data Files", data_files / "Morrowind.esm")
            print(f"  ghost plugin: {Path(ghost).name}")
            # The retail menu buttons are redrawn to match the ones multiplayer adds. A loose file
            # beats the archive only when newer, and an ISO has no file times, so the list names
            # them.
            textures = staged / "Data Files" / "Textures"
            textures.mkdir(parents=True, exist_ok=True)
            art = sorted((ROOT / "assets" / "menu").glob("*.dds"))
            for path in art:
                texture_target = textures / path.name
                if texture_target.exists():
                    texture_target.unlink()  # a hard link must not be written through
                shutil.copy2(path, texture_target)
            listed = staged / "ArchiveInvalidationList.txt"
            kept = listed.read_text(encoding="cp1252").splitlines() if listed.exists() else []
            if listed.exists():
                listed.unlink()
            write_invalidation(listed, kept + [f"textures\\{p.name}" for p in art])
            print(f"  menu buttons: {len(art)} textures")

        retail_files, retail_bytes = copy_retail_root(vanilla, staged, copy)
        # A data-only overlay cannot support the unpatched retail launcher. Use the engine as its
        # dashboard entry; New Game and Load still relaunch D:\morrowind.xbe.
        launcher_specs = ([f"title={title}"] if title else []) + (
            [f"title-id={pool:08X}"] if pool else [])
        if install_layout == "overlay":
            stage_default_xbe(launcher, patched, staged / "Default.xbe", install_layout)
        elif launcher_specs:
            run([sys.executable, TOOLS / "tes3x_patch.py", launcher,
                 *[x for spec in launcher_specs for x in ("--apply", spec)],
                 "--out", staged / "Default.xbe"])
        else:
            stage_default_xbe(launcher, patched, staged / "Default.xbe", install_layout)
        if title:
            folder = (remote or DEFAULT_REMOTE_ROOT).replace("\\", "/").rstrip("/")
            write_dashboard_files(staged, dashboards, title, folder.rsplit("/", 1)[-1],
                                  pool or tes3x_savepool.SHARED_ID)
        shutil.copy2(patched, staged / "morrowind.xbe")
        staged_paths = [path.relative_to(staged).as_posix()
                        for path in staged.rglob("*") if path.is_file()]
        require_paths(staged_paths, remote or DEFAULT_REMOTE_ROOT)
        print(f"  retail root payload: {retail_files} files, {retail_bytes / 1048576:.1f} MB")
        staged_ini = staged / "Morrowind.ini"
        plugins = plugin_inventory(staged / "Data Files")
        data_files_sha256 = tree_digest(staged / "Data Files")
        if install_layout == "overlay":
            removed, removed_bytes = strip_retail_files(staged, vanilla)
            print(f"  overlay: omitted {removed} unchanged retail files "
                  f"({removed_bytes / 1048576:.1f} MB)")
        record = {
            "schema": MARKER_SCHEMA,
            "profile": profile_name,
            "profile_sha256": sha256_file(profile_path),
            "preset": plan["preset"],
            "patches": ["payload=hooks/tes3xhook.pe" if spec.startswith("payload=") else spec
                        for spec in patch_specs],
            "package_mode": plan["package_mode"],
            "install_layout": install_layout,
            "preferences": profile.get("preferences", {}),
            "command": sanitized_command(invocation, profile_name, args.profile),
            "ini": {
                "base_sha256": sha256_file(ini),
                "staged_sha256": sha256_file(staged_ini),
                "overrides": ini_items,
            },
            "mods": mod_inventory(profile),
            "plugins": plugins,
            "data_files_sha256": data_files_sha256,
            "toolchain": toolchain,
            "deploy_tree": "deploy",
            "tes3x": source_revision(),
            "retail_xbe_sha1": hashlib.sha1(retail_xbe.read_bytes()).hexdigest(),
            "morrowind_xbe_sha256": sha256_file(staged / "morrowind.xbe"),
        }
        if pool:
            record["save_pool"] = {"name": pool_name, "id": f"{pool:08X}"}
        engine_recipe = {"retail": "morrowind.xbe",
                         "retail_digest": retail_digest(retail_xbe.read_bytes()),
                         "patches": record["patches"]}
        if install_layout == "overlay":
            launcher_recipe = engine_recipe
        else:
            launcher_recipe = {"retail": "Default.xbe",
                               "retail_digest": retail_digest(launcher.read_bytes()),
                               "patches": launcher_specs}
        tes3x_manifest.write(staged, tes3x_manifest.create(
            staged, profile=profile_name, install_layout=install_layout,
            source={"kind": "pipeline", "tes3x": record["tes3x"],
                    "profile_sha256": record["profile_sha256"]},
            plugins=[plugin["name"] for plugin in plugins], ini=ini_items,
            xbe=[{"path": "Default.xbe", **launcher_recipe,
                  "sha256": sha256_file(staged / "Default.xbe")},
                 {"path": "morrowind.xbe", **engine_recipe,
                  "sha256": record["morrowind_xbe_sha256"]}],
            save_pool=record.get("save_pool"), retail=vanilla))
        (work / MARKER).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        publish(work, output)
    except Exception:
        print(f"\nbuild failed; partial output kept at {work}", file=sys.stderr)
        raise

    print(f"\ncomplete install staged at {output / 'deploy'}")
    if args.deploy or args.dry_run:
        if install_layout == "overlay" and not deploy.get("retail_root"):
            raise PipelineError("deploying an overlay layout requires target.retail_root in the "
                                "local config")
        if install_layout == "overlay":
            with tempfile.TemporaryDirectory(prefix="tes3x-retail-base-",
                                             dir=output.parent) as base_temp:
                base_work = Path(base_temp)
                base_tree = base_work / "deploy"
                files, size = stage_retail_base(vanilla, base_tree, copy)
                (base_work / MARKER).write_text(json.dumps({
                    "schema": MARKER_SCHEMA,
                    "profile": "TES3X retail base",
                    "install_layout": "retail-base",
                    "deploy_tree": "deploy",
                }, indent=2) + "\n", encoding="utf-8")
                print(f"\nretail base: {files} files, {size / 1048576:.1f} MB")
                base_cmd = [sys.executable, TOOLS / "tes3x_deploy.py", base_tree,
                            "--config", local_path, "--remote", deploy["retail_root"]]
                if target:
                    base_cmd += ["--target", target["name"]]
                if args.ask_password:
                    base_cmd.append("--ask-password")
                if args.dry_run:
                    base_cmd.append("--dry-run")
                elif args.install_retail_base:
                    base_cmd += ["--replace", "--verify", "size"]
                else:
                    base_cmd += ["--dry-run", "--require-current"]
                print("\n== " + " ".join(str(part) for part in base_cmd), flush=True)
                result = subprocess.run([str(part) for part in base_cmd])
                if result.returncode == DEPLOY_CONFLICT:
                    return DEPLOY_CONFLICT
                if result.returncode:
                    raise subprocess.CalledProcessError(result.returncode, base_cmd)

        # The deploy tool reads the login from the config itself, keeping the password off
        # the command line.
        deploy_cmd = [sys.executable, TOOLS / "tes3x_deploy.py", output / "deploy",
                      "--config", local_path, "--remote", remote]
        if target:
            deploy_cmd += ["--target", target["name"]]
        if args.ask_password:
            deploy_cmd.append("--ask-password")
        if args.dry_run:
            deploy_cmd.append("--dry-run")
        if args.deploy and args.verify_deploy != "none":
            deploy_cmd += ["--verify", args.verify_deploy]
        if profile.get("rules", {}).get("clear_cache_partitions", False) and args.deploy:
            deploy_cmd.append("--clear-cache")
        if args.replace_remote:
            deploy_cmd.append("--replace")
        if args.ignore_space:
            deploy_cmd.append("--ignore-space")
        print("\n== " + " ".join(str(part) for part in deploy_cmd), flush=True)
        result = subprocess.run([str(part) for part in deploy_cmd])
        if result.returncode in (DEPLOY_CONFLICT, DEPLOY_NO_SPACE):
            return result.returncode
        if result.returncode:
            raise subprocess.CalledProcessError(result.returncode, deploy_cmd)
        if args.discard_build:
            shutil.rmtree(output)
            print(f"discarded verified build: {output}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (PipelineError, PayloadError, subprocess.CalledProcessError) as exc:
        raise SystemExit(str(exc))
