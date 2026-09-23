#!/usr/bin/env python3
"""Read the patch table, patches.toml, and render docs/patches.md from it."""

import argparse
from pathlib import Path
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "patches.toml"
TABLE = ROOT / "docs" / "patches.md"

CATEGORIES = ("core", "correctness", "compat", "performance", "qol", "balance",
              "instrumentation", "infrastructure")
STATUSES = ("implemented", "verified-xemu", "verified-hardware")
VERIFIED = ("verified-xemu", "verified-hardware")
SELECTIONS = ("always", "packaging", "preset", "option")
REQUIRED = ("name", "category", "status", "selection", "summary")
OPTIONAL = ("bit", "source", "takes", "evidence")


class RegistryError(ValueError):
    pass


def load(path=REGISTRY):
    """The patches in application order, each a dict of the fields above."""
    with open(path, "rb") as stream:
        patches = tomllib.load(stream).get("patch", [])
    names, bits = set(), set()
    for entry in patches:
        where = f"{path.name}: {entry.get('name', '<unnamed>')}"
        missing = [key for key in REQUIRED if key not in entry]
        unknown = set(entry) - set(REQUIRED) - set(OPTIONAL)
        if missing or unknown:
            raise RegistryError(f"{where}: missing {missing or 'nothing'}, "
                                f"unknown {sorted(unknown) or 'nothing'}")
        for key, allowed in (("category", CATEGORIES), ("status", STATUSES),
                             ("selection", SELECTIONS)):
            if entry[key] not in allowed:
                raise RegistryError(f"{where}: {key} must be one of {', '.join(allowed)}")
        if entry["name"] in names:
            raise RegistryError(f"{where}: duplicate name")
        names.add(entry["name"])
        if "bit" in entry:
            if not isinstance(entry["bit"], int) or not 0 <= entry["bit"] < 32:
                raise RegistryError(f"{where}: bit must be 0-31")
            if entry["bit"] in bits:
                raise RegistryError(f"{where}: bit {entry['bit']} already used")
            bits.add(entry["bit"])
        if entry["status"] in VERIFIED and not entry.get("evidence"):
            raise RegistryError(f"{where}: {entry['status']} needs evidence")
    return patches


PATCHES = load()
BY_NAME = {entry["name"]: entry for entry in PATCHES}


def render(patches=PATCHES):
    from tes3x_pipeline import resolve_patch_plan
    presets = {}
    for preset in ("development", "standard"):
        plan = resolve_patch_plan({"patches": {"preset": preset}, "mods": [{"name": "x"}]})
        presets.update({name: preset for name in plan["selected"]})
    selection = {
        "always": "every build",
        "packaging": "delta-bsa packing",
        "option": "command line",
    }
    lines = [
        "# Patches",
        "",
        "Generated from [`patches.toml`](../patches.toml) by `tools/tes3x_patches.py --write`.",
        "Edit that file, not this one.",
        "",
        "**Status**: `implemented` means the patch applies and passes structural checks;",
        "`verified-xemu` means it was shown to work in the xemu emulator; `verified-hardware`",
        "means it was shown to work on an original Xbox.",
        "",
        "**Selected by**: `standard` and `development` are presets; a patch marked `standard` is",
        "also in `development`. Anything else is enabled by name in a profile.",
        "",
        "| patch | what it does | category | status | selected by |",
        "|---|---|---|---|---|",
    ]
    for entry in patches:
        name = entry["name"] + (f"={entry['takes']}" if "takes" in entry else "")
        chosen = selection.get(entry["selection"]) or presets.get(entry["name"], "by name")
        lines.append(f"| `{name}` | {entry['summary']} | {entry['category']} | "
                     f"{entry['status']} | {chosen} |")
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--write", action="store_true", help=f"regenerate {TABLE.name}")
    group.add_argument("--check", action="store_true",
                       help=f"exit 1 if {TABLE.name} is out of date")
    args = ap.parse_args(argv)
    text = render()
    if args.write:
        TABLE.write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {TABLE}")
    elif args.check:
        current = TABLE.read_text(encoding="utf-8") if TABLE.is_file() else ""
        if current != text:
            sys.exit(f"{TABLE} is out of date; run tes3x_patches.py --write")
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
