#!/usr/bin/env python3
"""Read the mod compatibility catalog and render its page.

catalog.toml lists mods tried on the Xbox build and how they fared; docs/catalog.md is generated
from it by `tes3x_patches.py --write`.
"""

from pathlib import Path

from tes3x_paths import checkout, resource
import re
import tomllib

CATALOG = resource("catalog.toml")
PAGE = checkout("docs", "catalog.md")

STATUSES = {
    "works": "Confirmed working on the Xbox.",
    "works-with-requirements": "Confirmed working when its requirements are met.",
    "passes-automated": "Passed an automated test; compatibility is not yet confirmed.",
    "untested": "Compatibility has not been confirmed.",
    "broken": "Known to fail on the Xbox.",
    "not-possible": "Cannot work on the Xbox, even with patches.",
}
STATUS_LABELS = {
    "works": "Confirmed",
    "works-with-requirements": "Confirmed with requirements",
    "passes-automated": "Automated test passed",
    "untested": "Unknown",
    "broken": "Broken",
    "not-possible": "Not possible",
}
FIELDS = {"id", "name", "version", "url", "folder", "status", "patches", "ram", "bios",
          "requirements", "dependencies", "plugins", "notes"}
IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9._-]*\Z")


class CatalogError(ValueError):
    pass


def load(path=CATALOG, patch_names=None):
    """Catalog entries keyed by id."""
    with open(path, "rb") as stream:
        raw = tomllib.load(stream)
    if raw.get("schema") != 1:
        raise CatalogError(f"{path}: schema must be 1")
    result = {}
    for index, entry in enumerate(raw.get("mod", []), 1):
        where = f"{Path(path).name}: mod[{index}]"
        unknown = set(entry) - FIELDS
        if unknown:
            raise CatalogError(f"{where} has unknown fields {sorted(unknown)}")
        if not isinstance(entry.get("id"), str) or not IDENTIFIER.fullmatch(entry["id"]):
            raise CatalogError(f"{where}.id must use lowercase letters, digits, dot, dash or "
                               "underscore")
        if entry["id"] in result:
            raise CatalogError(f"{where}: duplicate id {entry['id']}")
        for key in ("name", "folder"):
            if not isinstance(entry.get(key), str) or not entry[key]:
                raise CatalogError(f"{where}.{key} must be a non-empty string")
        if entry.get("status", "untested") not in STATUSES:
            raise CatalogError(f"{where}.status must be one of {', '.join(STATUSES)}")
        if entry.get("ram", 64) not in (64, 128):
            raise CatalogError(f"{where}.ram must be 64 or 128")
        for key in ("patches", "requirements", "dependencies", "plugins"):
            value = entry.get(key, [])
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise CatalogError(f"{where}.{key} must be a list of strings")
        if patch_names is not None and set(entry.get("patches", [])) - set(patch_names):
            raise CatalogError(f"{where}: unknown patches "
                               + ", ".join(sorted(set(entry["patches"]) - set(patch_names))))
        result[entry["id"]] = {"status": "untested", **entry}
    for entry in result.values():
        missing = set(entry.get("dependencies", [])) - set(result)
        if missing:
            raise CatalogError(f"{entry['id']} depends on unknown ids {sorted(missing)}")
    return result


def needs(entry):
    """What a mod needs beyond a stock 64 MB console, in words."""
    items = [f"`{name}` patch" for name in entry.get("patches", [])]
    if entry.get("ram") == 128:
        items.append("128 MB RAM")
    if entry.get("bios"):
        items.append(f"{entry['bios'].title()} BIOS")
    items += entry.get("requirements", [])
    return items


def key(value):
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def match(catalog, *names):
    """The catalog entry for a mod known by any of these names: folder, name or id."""
    wanted = {key(Path(name).stem if name.lower().endswith((".esp", ".esm")) else name)
              for name in names if name}
    for entry in catalog.values():
        folder = entry["folder"]
        folder = Path(folder).stem if folder.lower().endswith((".esp", ".esm")) else folder
        if wanted & {key(folder), key(entry["name"]), key(entry["id"])}:
            return entry
    return None


def cell(text):
    return str(text).replace("|", "\\|").replace("\n", " ")


def render(catalog):
    lines = ["# Mod compatibility", "",
             "Generated from [`catalog.toml`](../catalog.toml) by `tools/tes3x_patches.py "
             "--write`;", "edit that file, not this one.", "",
             "Mods tried on the Xbox build and how they fared. A scripted test only shows that a "
             "build starts and loads a few cells; only playing a mod marks it as working. The "
             "GUI's mod list shows the same verdicts.", "",
             "| Status | Meaning |", "|---|---|"]
    lines += [f"| `{name}` | {meaning} |" for name, meaning in STATUSES.items()]
    lines += ["", "| Mod | Version | Status | Needs | Notes |", "|---|---|---|---|---|"]
    order = list(STATUSES)
    for entry in sorted(catalog.values(),
                        key=lambda item: (order.index(item["status"]), item["name"].casefold())):
        name = f"[{cell(entry['name'])}]({entry['url']})" if entry.get("url") else cell(entry["name"])
        lines.append(f"| {name} | {cell(entry.get('version', ''))} | `{entry['status']}` | "
                     f"{cell(', '.join(needs(entry)))} | {cell(entry.get('notes', ''))} |")
    return "\n".join(lines) + "\n"
