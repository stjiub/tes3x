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
# Fixes without code, in the order the generated page lists them.
CANDIDATE_STATUSES = {
    "researching": "Being decoded or reproduced now.",
    "located": "The Xbox cause and a patch design are known; not implemented yet.",
    "candidate": "Worth porting, but not yet shown to affect the Xbox build.",
    "deferred": "A real fix, held back for evidence, prerequisites or risk.",
    "not-applicable": "Targets PC-only code or behaviour; there is nothing to fix on the Xbox.",
    "infeasible": "Attempted, and cannot be done on the Xbox build.",
    "rejected": "Deliberately left out of TES3X.",
}
PATCH_FIELDS = (("name", "category", "status", "selection", "summary"),
                ("bit", "source", "takes", "evidence", "origin"))
CANDIDATE_FIELDS = (("name", "origin", "category", "status", "summary", "reason"),
                    ("default", "doc"))


class RegistryError(ValueError):
    pass


def check(entry, fields, choices, sources, where):
    required, optional = fields
    missing = [key for key in required if key not in entry]
    unknown = set(entry) - set(required) - set(optional)
    if missing or unknown:
        raise RegistryError(f"{where}: missing {missing or 'nothing'}, "
                            f"unknown {sorted(unknown) or 'nothing'}")
    for key, allowed in choices:
        if entry[key] not in allowed:
            raise RegistryError(f"{where}: {key} must be one of {', '.join(allowed)}")
    origin = entry.get("origin")
    if origin is not None and (not isinstance(origin, dict) or origin.get("source") not in sources
                               or set(origin) - {"source", "id"}):
        raise RegistryError(f"{where}: origin wants {{ source = <a [source] name>, id = ... }}")


def read(path=REGISTRY):
    """The sources, the patches in application order, and the fixes without code."""
    with open(path, "rb") as stream:
        data = tomllib.load(stream)
    sources = data.get("source", {})
    patches = data.get("patch", [])
    candidates = data.get("candidate", [])
    names, bits = set(), set()
    for entry in patches:
        where = f"{path.name}: patch {entry.get('name', '<unnamed>')}"
        check(entry, PATCH_FIELDS, (("category", CATEGORIES), ("status", STATUSES),
                                    ("selection", SELECTIONS)), sources, where)
        if "bit" in entry:
            if not isinstance(entry["bit"], int) or not 0 <= entry["bit"] < 32:
                raise RegistryError(f"{where}: bit must be 0-31")
            if entry["bit"] in bits:
                raise RegistryError(f"{where}: bit {entry['bit']} already used")
            bits.add(entry["bit"])
        if entry["status"] in VERIFIED and not entry.get("evidence"):
            raise RegistryError(f"{where}: {entry['status']} needs evidence")
    for entry in candidates:
        where = f"{path.name}: candidate {entry.get('name', '<unnamed>')}"
        check(entry, CANDIDATE_FIELDS, (("category", CATEGORIES + ("undecided",)),
                                        ("status", tuple(CANDIDATE_STATUSES))), sources, where)
    for entry in patches + candidates:
        if entry["name"] in names:
            raise RegistryError(f"{path.name}: {entry['name']} is listed twice")
        names.add(entry["name"])
    return sources, patches, candidates


def load(path=REGISTRY):
    return read(path)[1]


SOURCES, PATCHES, CANDIDATES = read()
BY_NAME = {entry["name"]: entry for entry in PATCHES}


def origin_text(entry):
    origin = entry.get("origin") or {"source": "TES3X"}
    source = SOURCES.get(origin["source"], {})
    text = f"[{origin['source']}]({source['url']})" if source.get("url") else origin["source"]
    return text + (f" #{origin['id']}" if "id" in origin else "")


def evidence_text(entry):
    links = []
    for evidence in entry.get("evidence", []):
        if evidence.startswith("verification/"):
            label = Path(evidence).stem
            links.append(f"[{label}](../{evidence})")
        else:
            links.append("findings log, no record yet")
    return ", ".join(links)


def cell(text):
    return text.replace("|", "\\|")


def render():
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
        "## Implemented",
        "",
        "**Status**: `implemented` means the patch applies and passes structural checks;",
        "`verified-xemu` means it was shown to work in the xemu emulator; `verified-hardware`",
        "means it was shown to work on an original Xbox.",
        "",
        "**Selected by**: `standard` and `development` are presets; a patch marked `standard` is",
        "also in `development`. Anything else is enabled by name in a profile.",
        "",
        "**Evidence**: the proof record behind a status: a control run without the patch, a run",
        "with it, and their logs. See `tools/tes3x_proof.py`.",
        "",
        "| patch | what it does | from | category | status | selected by | evidence |",
        "|---|---|---|---|---|---|---|",
    ]
    for entry in PATCHES:
        name = entry["name"] + (f"={entry['takes']}" if "takes" in entry else "")
        chosen = selection.get(entry["selection"]) or presets.get(entry["name"], "by name")
        lines.append(f"| `{name}` | {cell(entry['summary'])} | {origin_text(entry)} | "
                     f"{entry['category']} | {entry['status']} | {chosen} | "
                     f"{evidence_text(entry)} |")
    lines += [
        "",
        "## Not implemented",
        "",
        "Fixes from other projects that have been reviewed for the Xbox, and why each is not",
        "implemented yet or at all. Appearing here does not mean the Xbox build has the defect.",
        "A fix from another project that is not listed has not been reviewed yet.",
    ]
    for status, meaning in CANDIDATE_STATUSES.items():
        entries = [entry for entry in CANDIDATES if entry["status"] == status]
        if not entries:
            continue
        lines += ["", f"### {status}", "", meaning, "",
                  "| fix | from | category | notes |", "|---|---|---|---|"]
        for entry in entries:
            notes = f"**{cell(entry['summary'])}.** {cell(entry['reason'])}"
            if "doc" in entry:
                notes += f" [More]({entry['doc']})"
            category = entry["category"] + (f" ({entry['default']})" if "default" in entry else "")
            lines.append(f"| `{entry['name']}` | {origin_text(entry)} | {cell(category)} | "
                         f"{notes} |")
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
