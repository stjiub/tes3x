#!/usr/bin/env python3
"""Read the patch table and the candidate list, and render their pages.

patches.toml holds every patch TES3X can apply; candidates.toml every fix reviewed but not
implemented. docs/patches.md and docs/candidates.md are generated from them.
"""

import argparse
from pathlib import Path
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "patches.toml"
CANDIDATE_LIST = ROOT / "candidates.toml"
TABLE = ROOT / "docs" / "patches.md"
CANDIDATE_PAGE = ROOT / "docs" / "candidates.md"
PATCH_DOCS = ROOT / "patches"
GAME_TESTS = ROOT / "tests" / "game"

CATEGORIES = ("core", "correctness", "compat", "performance", "qol", "balance",
              "instrumentation", "infrastructure")
CHANNELS = ("dev", "preview", "release")
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
PRIORITIES = ("high", "medium", "low", "none")
PATCH_FIELDS = (("name", "title", "category", "channel", "selection", "summary"),
                ("bit", "source", "takes", "origin", "ini"))
CANDIDATE_FIELDS = (("name", "origin", "status", "priority", "summary", "reason"),
                    ("category", "default", "doc"))


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
        if key in entry and entry[key] not in allowed:
            raise RegistryError(f"{where}: {key} must be one of {', '.join(allowed)}")
    origin = entry.get("origin")
    if origin is not None and (not isinstance(origin, dict) or origin.get("source") not in sources
                               or set(origin) - {"source", "id"}):
        raise RegistryError(f"{where}: origin wants {{ source = <a [source] name>, id = ... }}")


def read(path=REGISTRY, candidate_path=CANDIDATE_LIST):
    """The sources, the patches in application order, and the fixes without code."""
    with open(path, "rb") as stream:
        data = tomllib.load(stream)
    candidates = []
    if candidate_path and Path(candidate_path).is_file():
        with open(candidate_path, "rb") as stream:
            candidates = tomllib.load(stream).get("candidate", [])
    sources = data.get("source", {})
    patches = data.get("patch", [])
    names, bits = set(), set()
    for entry in patches:
        where = f"{Path(path).name}: {entry.get('name', '<unnamed>')}"
        check(entry, PATCH_FIELDS, (("category", CATEGORIES), ("channel", CHANNELS),
                                    ("selection", SELECTIONS)), sources, where)
        ini = entry.get("ini", {})
        if not isinstance(ini, dict) or any(not isinstance(value, str) for value in ini.values()):
            raise RegistryError(f"{where}: ini wants {{ Key = \"default\" }}")
        if "bit" in entry:
            if not isinstance(entry["bit"], int) or not 0 <= entry["bit"] < 32:
                raise RegistryError(f"{where}: bit must be 0-31")
            if entry["bit"] in bits:
                raise RegistryError(f"{where}: bit {entry['bit']} already used")
            bits.add(entry["bit"])
    for entry in candidates:
        where = f"{Path(candidate_path).name}: {entry.get('name', '<unnamed>')}"
        check(entry, CANDIDATE_FIELDS, (("category", CATEGORIES + ("undecided",)),
                                        ("status", tuple(CANDIDATE_STATUSES)),
                                        ("priority", PRIORITIES)), sources, where)
    for entry in patches + candidates:
        if entry["name"] in names:
            raise RegistryError(f"{entry['name']} is listed twice")
        names.add(entry["name"])
    return sources, patches, candidates


def load(path=REGISTRY):
    return read(path, None)[1]


SOURCES, PATCHES, CANDIDATES = read()
BY_NAME = {entry["name"]: entry for entry in PATCHES}


def origin_text(entry):
    origin = entry.get("origin") or {"source": "TES3X"}
    source = SOURCES.get(origin["source"], {})
    text = f"[{origin['source']}]({source['url']})" if source.get("url") else origin["source"]
    return text + (f" #{origin['id']}" if "id" in origin else "")


def name_text(entry):
    name = entry["name"] + (f"={entry['takes']}" if "takes" in entry else "")
    return f"[`{name}`](../patches/{entry['name']}.md)"


# The sections a patch page may have, in order; patches/README.md is the template.
PAGE_SECTIONS = ("How it works", "Using it", "Configuration", "Compatibility and limits")
TABLE_LINK = "](../docs/patches.md)"


def page_problems(patches=None, docs=PATCH_DOCS):
    """Every way the patch pages break the contract in patches/README.md."""
    patches = PATCHES if patches is None else patches
    problems = []
    pages = {path.stem: path for path in Path(docs).glob("*.md") if path.name != "README.md"}
    for name in sorted(set(pages) - {entry["name"] for entry in patches}):
        problems.append(f"patches/{name}.md: no such patch in patches.toml")
    for entry in patches:
        where = f"patches/{entry['name']}.md"
        if entry["name"] not in pages:
            problems.append(f"{where}: missing")
            continue
        lines = pages[entry["name"]].read_text(encoding="utf-8").splitlines()
        titles = [line[2:] for line in lines if line.startswith("# ")]
        if titles != [entry["title"]]:
            problems.append(f"{where}: wants the single heading '# {entry['title']}'")
        sections = [line[3:] for line in lines if line.startswith("## ")]
        if "How it works" not in sections:
            problems.append(f"{where}: no '## How it works'")
        unknown = [s for s in sections if s not in PAGE_SECTIONS]
        if unknown:
            problems.append(f"{where}: sections {unknown} are not in {list(PAGE_SECTIONS)}")
        elif sections != sorted(sections, key=PAGE_SECTIONS.index) or \
                len(set(sections)) != len(sections):
            problems.append(f"{where}: sections out of order or repeated")
        if not any(TABLE_LINK in line for line in lines):
            problems.append(f"{where}: no link to the patch table")
    return problems


def game_test_text(entry):
    path = GAME_TESTS / f"{entry['name']}.toml"
    return f"[test](../tests/game/{entry['name']}.toml)" if path.is_file() else "—"


def cell(text):
    return text.replace("|", "\\|")


def render_patches():
    from tes3x_pipeline import resolve_patch_plan
    presets = {}
    for preset in ("testing", "recommended"):
        plan = resolve_patch_plan({"patches": {"preset": preset}, "mods": [{"name": "x"}]})
        presets.update({name: f"{preset} preset" for name in plan["selected"]})
    selection = {
        "always": "every build",
        "packaging": "delta-bsa packing",
        "option": "build option",
    }
    lines = [
        "# Patches",
        "",
        "Generated from [`patches.toml`](../patches.toml) by `tools/tes3x_patches.py --write`;",
        "edit that file, not this one. Fixes that are not implemented are in",
        "[candidates.md](candidates.md). A linked patch name opens its notes; a game-test link",
        "opens the runnable test definition.",
        "",
        "`dev` patches are contributor-only, `preview` patches work but need broader testing,",
        "and `release` patches are ready for general use.",
        "\"By name\" patches are only applied when a profile enables them.",
        "The game-test column shows whether a public test definition exists, not its result.",
        "",
        "| patch | what it does | from | category | channel | game test | selected by |",
        "|---|---|---|---|---|---|---|",
    ]
    for entry in PATCHES:
        chosen = selection.get(entry["selection"]) or presets.get(entry["name"], "by name")
        lines.append(f"| {name_text(entry)} | {cell(entry['summary'])} | {origin_text(entry)} | "
                     f"{entry['category']} | {entry['channel']} | {game_test_text(entry)} | "
                     f"{chosen} |")
    return "\n".join(lines) + "\n"


def render_candidates():
    counts = {status: sum(e["status"] == status for e in CANDIDATES)
              for status in CANDIDATE_STATUSES}
    lines = [
        "# Candidate fixes",
        "",
        "Generated from [`candidates.toml`](../candidates.toml) by `tools/tes3x_patches.py --write`;",
        "edit that file, not this one.",
        "",
        "Fixes from other projects that we've looked at, and why each isn't implemented, yet or at",
        "all. Every Morrowind Code Patch fix is here. Being listed doesn't mean the Xbox has the bug.",
        "Implemented fixes are in [patches.md](patches.md).",
        "",
        "**Priority** is a rough guess at how much a fix matters on the Xbox: `high` for crashes and",
        "lost saves players are likely to hit, `medium` for bugs seen in normal play or that mods rely",
        "on, `low` for minor ones, and `none` for fixes we won't port.",
        "",
        "| status | fixes |",
        "|---|---|",
    ] + [f"| {status} | {n} |" for status, n in counts.items() if n]
    for status, meaning in CANDIDATE_STATUSES.items():
        entries = [e for e in CANDIDATES if e["status"] == status]
        if not entries:
            continue
        entries.sort(key=lambda e: PRIORITIES.index(e["priority"]))
        lines += ["", f"## {status}", "", meaning, "",
                  "| fix | from | priority | category | notes |", "|---|---|---|---|---|"]
        for e in entries:
            notes = f"**{cell(e['summary'])}.** {cell(e['reason'])}"
            if "doc" in e:
                notes += f" [More]({e['doc']})"
            category = e.get("category", "")
            if "default" in e:
                category += f" ({e['default']})"
            lines.append(f"| `{e['name']}` | {origin_text(e)} | {e['priority']} | "
                         f"{cell(category)} | {notes} |")
    return "\n".join(lines) + "\n"


def render_catalog():
    import tes3x_catalog
    return tes3x_catalog.render(tes3x_catalog.load(patch_names=BY_NAME))


PAGES = ((TABLE, render_patches), (CANDIDATE_PAGE, render_candidates),
         (ROOT / "docs" / "catalog.md", render_catalog))


def stale_pages():
    return [path for path, render in PAGES
            if not path.is_file() or path.read_text(encoding="utf-8") != render()]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--write", action="store_true", help="regenerate the generated pages")
    group.add_argument("--check", action="store_true", help="exit 1 if a page is out of date")
    args = ap.parse_args(argv)
    if args.write:
        for path, render in PAGES:
            path.write_text(render(), encoding="utf-8", newline="\n")
            print(f"wrote {path.relative_to(ROOT).as_posix()}")
    elif args.check:
        problems = page_problems()
        stale = stale_pages()
        if stale:
            problems.append("out of date: " + ", ".join(p.name for p in stale)
                            + "; run tes3x_patches.py --write")
        if problems:
            sys.exit("\n".join(problems))
    else:
        sys.stdout.write(render_patches())


if __name__ == "__main__":
    main()
