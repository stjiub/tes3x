#!/usr/bin/env python3
"""Read the mod compatibility catalog, update it from GitHub and draft entries for it.

catalog.toml lists mods tried on the Xbox build and how they fared; docs/catalog.md is generated
from it by `tes3x patches --write`.

  tes3x catalog add FOLDER [--url NEXUS_URL]   print a catalog entry for a mod folder
  tes3x catalog refresh                        download the current catalog from GitHub
"""

import argparse

import tempfile
import urllib.request
from pathlib import Path

from tes3x.library import nexus_id
from tes3x.paths import checkout, data_dir, resource
import re
import tomllib

CATALOG = resource("catalog.toml")
PAGE = checkout("docs", "catalog.md")
REMOTE_URL = "https://raw.githubusercontent.com/stjiub/tes3x/main/catalog.toml"

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
          "requirements", "dependencies", "plugins", "merge_kf", "memory_kb", "notes"}
IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9._-]*\Z")


class CatalogError(ValueError):
    pass


def load(path=CATALOG, patch_names=None, lenient=False):
    """Catalog entries keyed by id. A lenient load, for a newer catalog than this program
    knows, ignores fields it does not recognise and unknown patch names."""
    with open(path, "rb") as stream:
        raw = tomllib.load(stream)
    if raw.get("schema") != 1:
        raise CatalogError(f"{path}: schema must be 1")
    result = {}
    for index, entry in enumerate(raw.get("mod", []), 1):
        where = f"{Path(path).name}: mod[{index}]"
        if lenient:
            entry = {key: value for key, value in entry.items() if key in FIELDS}
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
        if "memory_kb" in entry and (type(entry["memory_kb"]) is not int or entry["memory_kb"] < 0):
            raise CatalogError(f"{where}.memory_kb must be a whole number of KB")
        for key in ("patches", "requirements", "dependencies", "plugins", "merge_kf"):
            value = entry.get(key, [])
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise CatalogError(f"{where}.{key} must be a list of strings")
        if patch_names is not None and set(entry.get("patches", [])) - set(patch_names):
            raise CatalogError(f"{where}: unknown patches "
                               + ", ".join(sorted(set(entry["patches"]) - set(patch_names))))
        result[entry["id"]] = {"status": "untested", **entry}
    for entry in result.values():
        if lenient:
            entry["dependencies"] = [i for i in entry.get("dependencies", []) if i in result]
        missing = set(entry.get("dependencies", [])) - set(result)
        if missing:
            raise CatalogError(f"{entry['id']} depends on unknown ids {sorted(missing)}")
    return result


def updated(path):
    """The `updated` date a catalog file declares, or empty."""
    try:
        with open(path, "rb") as stream:
            return str(tomllib.load(stream).get("updated", ""))
    except (OSError, tomllib.TOMLDecodeError):
        return ""


def cached():
    return data_dir() / "catalog.toml"


def load_best():
    """The catalog to show: the downloaded copy when it is newer than the bundled one."""
    if cached().is_file() and updated(cached()) > updated(CATALOG):
        try:
            return load(cached(), lenient=True)
        except (OSError, ValueError):
            pass
    return load()


def refresh(url=REMOTE_URL):
    """Download the current catalog from GitHub and keep it if it is valid and newer. Returns
    the `updated` date of the catalog now in use."""
    with urllib.request.urlopen(url, timeout=30) as response:
        data = response.read()
    with tempfile.TemporaryDirectory() as folder:
        trial = Path(folder) / "catalog.toml"
        trial.write_bytes(data)
        try:
            load(trial, lenient=True)
        except (OSError, ValueError) as exc:
            raise CatalogError(f"{url} is not a usable catalog: {exc}") from exc
        if updated(trial) > max(updated(CATALOG), updated(cached())):
            cached().parent.mkdir(parents=True, exist_ok=True)
            cached().write_bytes(data)
    return max(updated(CATALOG), updated(cached()))


def needs(entry):
    """What a mod needs beyond a stock 64 MB console, in words."""
    items = [f"`{name}` patch" for name in entry.get("patches", [])]
    if entry.get("ram") == 128:
        items.append("128 MB RAM")
    if entry.get("bios"):
        items.append(f"{entry['bios'].title()} BIOS")
    items += entry.get("requirements", [])
    return items


def memory_label(entry):
    """The tour's extra memory for a mod, such as `+3.5 MB`, or empty if never measured."""
    kb = entry.get("memory_kb")
    return "" if kb is None else "+%.1f MB" % (kb / 1024)


def key(value):
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def match(catalog, *names, nexus=None):
    """The catalog entry for a mod: its Nexus id if given, else any of these names (folder, name
    or id). A Nexus id survives a renamed folder; names are the fallback for other sources."""
    if nexus:
        for entry in catalog.values():
            if nexus_id(entry.get("url")) == nexus:
                return entry
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
             "Generated from [`catalog.toml`](../catalog.toml) by `tes3x patches "
             "--write`;", "edit that file, not this one.", "",
             "Mods tried on the Xbox build and how they fared. A scripted test only shows that a "
             "build starts and loads a few cells; only playing a mod marks it as working. Memory "
             "is the most free memory the mod cost against vanilla on the memory tour. The "
             "GUI's mod list shows the same verdicts.", "",
             "| Status | Meaning |", "|---|---|"]
    lines += [f"| `{name}` | {meaning} |" for name, meaning in STATUSES.items()]
    lines += ["", "| Mod | Version | Status | Needs | Memory | Notes |", "|---|---|---|---|---|---|"]
    order = list(STATUSES)
    for entry in sorted(catalog.values(),
                        key=lambda item: (order.index(item["status"]), item["name"].casefold())):
        name = f"[{cell(entry['name'])}]({entry['url']})" if entry.get("url") else cell(entry["name"])
        lines.append(f"| {name} | {cell(entry.get('version', ''))} | `{entry['status']}` | "
                     f"{cell(', '.join(needs(entry)))} | {memory_label(entry)} | "
                     f"{cell(entry.get('notes', ''))} |")
    return "\n".join(lines) + "\n"


PLUGIN_SUFFIXES = (".esp", ".esm")


def draft(folder, url=None):
    """A catalog entry for a mod folder, as TOML, with the fields the GUI matches on filled in."""
    folder = Path(folder)
    plugins = sorted({path.name for path in folder.rglob("*")
                      if path.suffix.lower() in PLUGIN_SUFFIXES}, key=str.casefold)
    found = nexus_id(url, folder.name)
    page = url or (f"https://www.nexusmods.com/morrowind/mods/{found}" if found else "")
    ident = re.sub(r"[^a-z0-9]+", "-", folder.name.casefold()).strip("-")
    known = match(load(), folder.name, nexus=found)
    lines = [f"# already listed as {known['id']!r}" if known else "# new entry", "[[mod]]",
             f'id = "{ident}"', f'name = "{folder.name}"', 'version = "unknown"']
    if page:
        lines.append(f'url = "{page}"')
    lines += [f'folder = "{folder.name}"', 'status = "untested"']
    if plugins:
        lines.append("plugins = [" + ", ".join(f'"{name}"' for name in plugins) + "]")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    add = sub.add_parser("add", help="print a catalog entry for a mod folder")
    add.add_argument("folder")
    add.add_argument("--url", help="the mod's Nexus page, if the folder name does not say")
    sub.add_parser("refresh", help="download the current catalog from GitHub")
    args = ap.parse_args(argv)
    if args.cmd == "add":
        print(draft(args.folder, args.url))
    else:
        print("catalog updated", refresh())


if __name__ == "__main__":
    main()
