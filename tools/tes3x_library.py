#!/usr/bin/env python3
"""Read, scan and write a versioned TES3X mod library."""

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tomllib
import zipfile


CATALOG_NAME = "library.toml"
BUNDLED_7Z = Path(__file__).resolve().parents[1] / "externals" / "7zip" / "7z.exe"
IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9._-]*\Z")
# Optional mod-level text: where the mod comes from and what it is.
MOD_TEXT = ("url", "author", "summary")


class LibraryError(ValueError):
    pass


def _strings(value, field):
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise LibraryError(f"{field} must be an array of non-empty strings")
    return list(value)


def _identifier(value, field):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise LibraryError(f"{field} must use lowercase letters, digits, dot, dash or underscore")
    return value


def _relative(value, field):
    if not isinstance(value, str) or not value:
        raise LibraryError(f"{field} must be a non-empty relative path")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise LibraryError(f"{field} must stay inside the library")
    return value


def load_library(root):
    """Return installed mod metadata keyed by stable id."""
    root = Path(root).resolve()
    path = root / CATALOG_NAME
    try:
        with open(path, "rb") as stream:
            raw = tomllib.load(stream)
    except FileNotFoundError as exc:
        raise LibraryError(f"managed mod selection needs {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise LibraryError(f"{path}: {exc}") from exc
    if raw.get("schema") != 1:
        raise LibraryError(f"{path}: schema must be 1")
    unknown = set(raw) - {"schema", "mod"}
    if unknown:
        raise LibraryError(f"{path}: unknown top-level fields: {', '.join(sorted(unknown))}")
    rows = raw.get("mod", [])
    if not isinstance(rows, list):
        raise LibraryError(f"{path}: mod must be an array of tables")

    result = {}
    for mod_index, mod in enumerate(rows, 1):
        where = f"mod[{mod_index}]"
        if not isinstance(mod, dict):
            raise LibraryError(f"{path}: {where} must be a table")
        extra = set(mod) - {"id", "name", "release", *MOD_TEXT}
        if extra:
            raise LibraryError(f"{path}: {where} has unknown fields: {', '.join(sorted(extra))}")
        mod_id = _identifier(mod.get("id"), f"{where}.id")
        if mod_id in result:
            raise LibraryError(f"{path}: duplicate mod id {mod_id}")
        if not isinstance(mod.get("name"), str) or not mod["name"]:
            raise LibraryError(f"{path}: {where}.name must be a non-empty string")
        for key in MOD_TEXT:
            if key in mod and (not isinstance(mod[key], str) or not mod[key]):
                raise LibraryError(f"{path}: {where}.{key} must be a non-empty string")
        releases = mod.get("release", [])
        if not isinstance(releases, list) or not releases:
            raise LibraryError(f"{path}: {where} needs at least one [[mod.release]]")
        versions = {}
        defaults = []
        for release_index, release in enumerate(releases, 1):
            rwhere = f"{where}.release[{release_index}]"
            if not isinstance(release, dict):
                raise LibraryError(f"{path}: {rwhere} must be a table")
            extra = set(release) - {"version", "folder", "default", "roots", "dependencies",
                                    "component", "source"}
            if extra:
                raise LibraryError(f"{path}: {rwhere} has unknown fields: "
                                   + ", ".join(sorted(extra)))
            version = release.get("version")
            if not isinstance(version, str) or not version:
                raise LibraryError(f"{path}: {rwhere}.version must be a non-empty string")
            if version in versions:
                raise LibraryError(f"{path}: {mod_id} has duplicate version {version}")
            folder = _relative(release.get("folder"), f"{rwhere}.folder")
            source = release.get("source")
            if source is not None and (not isinstance(source, str) or not source):
                raise LibraryError(f"{path}: {rwhere}.source must be a non-empty string")
            if "default" in release and type(release["default"]) is not bool:
                raise LibraryError(f"{path}: {rwhere}.default must be a boolean")
            if release.get("default", False):
                defaults.append(version)
            roots = [_relative(value, f"{rwhere}.roots")
                     for value in _strings(release.get("roots", ["."]), f"{rwhere}.roots")]
            dependencies = [_identifier(value, f"{rwhere}.dependencies")
                            for value in _strings(release.get("dependencies"),
                                                  f"{rwhere}.dependencies")]
            components = {}
            for component_index, component in enumerate(release.get("component", []), 1):
                cwhere = f"{rwhere}.component[{component_index}]"
                if not isinstance(component, dict):
                    raise LibraryError(f"{path}: {cwhere} must be a table")
                extra = set(component) - {"id", "name", "roots", "default", "group", "conflicts"}
                if extra:
                    raise LibraryError(f"{path}: {cwhere} has unknown fields: "
                                       + ", ".join(sorted(extra)))
                component_id = _identifier(component.get("id"), f"{cwhere}.id")
                if component_id in components:
                    raise LibraryError(f"{path}: duplicate component {mod_id}/{component_id}")
                name = component.get("name", component_id)
                if not isinstance(name, str) or not name:
                    raise LibraryError(f"{path}: {cwhere}.name must be a non-empty string")
                component_roots = [_relative(value, f"{cwhere}.roots")
                                   for value in _strings(component.get("roots"), f"{cwhere}.roots")]
                if not component_roots:
                    raise LibraryError(f"{path}: {cwhere}.roots cannot be empty")
                if "default" in component and type(component["default"]) is not bool:
                    raise LibraryError(f"{path}: {cwhere}.default must be a boolean")
                group = component.get("group")
                if group is not None:
                    _identifier(group, f"{cwhere}.group")
                conflicts = [_identifier(value, f"{cwhere}.conflicts")
                             for value in _strings(component.get("conflicts"),
                                                   f"{cwhere}.conflicts")]
                components[component_id] = {
                    "id": component_id, "name": name, "roots": component_roots,
                    "default": component.get("default", False), "group": group,
                    "conflicts": conflicts,
                }
            for component in components.values():
                missing = set(component["conflicts"]) - set(components)
                if missing:
                    raise LibraryError(f"{path}: {mod_id}/{component['id']} conflicts with unknown "
                                       + ", ".join(sorted(missing)))
            versions[version] = {
                "version": version, "folder": folder, "default": release.get("default", False),
                "roots": roots, "dependencies": dependencies, "components": components,
                "source": source,
            }
        if len(defaults) > 1:
            raise LibraryError(f"{path}: {mod_id} has more than one default release")
        result[mod_id] = {"id": mod_id, "name": mod["name"], "releases": versions,
                          "default": defaults[0] if defaults else None,
                          **{key: mod[key] for key in MOD_TEXT if key in mod}}
    for mod_id, mod in result.items():
        for release in mod["releases"].values():
            unknown_dependencies = set(release["dependencies"]) - set(result)
            if unknown_dependencies:
                raise LibraryError(f"{path}: {mod_id} {release['version']} has unknown dependencies: "
                                   + ", ".join(sorted(unknown_dependencies)))
            if mod_id in release["dependencies"]:
                raise LibraryError(f"{path}: {mod_id} cannot depend on itself")
    return result


def slug(value):
    value = re.sub(r"[^a-z0-9._-]+", "-", value.lower()).strip("-._")
    return value or "mod"


def discover_library(root):
    """Conservatively index top-level folders and standalone plugins as unknown releases."""
    root = Path(root).resolve()
    if not root.is_dir():
        raise LibraryError(f"not a mod library directory: {root}")
    result = {}
    candidates = [path for path in root.iterdir()
                  if path.name != CATALOG_NAME and
                  (path.is_dir() or path.suffix.lower() in (".esm", ".esp"))]
    for path in sorted(candidates, key=lambda item: item.name.casefold()):
        base = slug(path.stem if path.is_file() else path.name)
        mod_id = base
        suffix = 2
        while mod_id in result:
            mod_id, suffix = f"{base}-{suffix}", suffix + 1
        release = {"version": "unknown", "folder": path.name, "default": True,
                   "roots": ["."], "dependencies": [], "components": {}}
        result[mod_id] = {"id": mod_id, "name": path.stem if path.is_file() else path.name,
                          "releases": {"unknown": release}, "default": "unknown"}
    return result


def render_mods(catalog):
    """library.toml [[mod]] blocks for these mods, sorted by name."""
    lines = []
    for mod in sorted(catalog.values(), key=lambda item: item["name"].casefold()):
        lines += ["[[mod]]", f"id = {json.dumps(mod['id'], ensure_ascii=False)}",
                  f"name = {json.dumps(mod['name'], ensure_ascii=False)}",
                  *(f"{key} = {json.dumps(mod[key], ensure_ascii=False)}"
                    for key in MOD_TEXT if mod.get(key)), ""]
        for release in mod["releases"].values():
            lines += ["[[mod.release]]",
                      f"version = {json.dumps(release['version'], ensure_ascii=False)}",
                      f"folder = {json.dumps(release['folder'], ensure_ascii=False)}",
                      f"default = {str(bool(release['default'])).lower()}",
                      "roots = " + json.dumps(release["roots"], ensure_ascii=False),
                      "dependencies = " + json.dumps(release["dependencies"], ensure_ascii=False)]
            if release.get("source"):
                lines.append(f"source = {json.dumps(release['source'], ensure_ascii=False)}")
            lines.append("")
            for component in release["components"].values():
                lines += ["[[mod.release.component]]",
                          f"id = {json.dumps(component['id'], ensure_ascii=False)}",
                          f"name = {json.dumps(component['name'], ensure_ascii=False)}",
                          "roots = " + json.dumps(component["roots"], ensure_ascii=False),
                          f"default = {str(bool(component['default'])).lower()}"]
                if component["group"]:
                    lines.append(f"group = {json.dumps(component['group'])}")
                if component["conflicts"]:
                    lines.append("conflicts = " + json.dumps(component["conflicts"],
                                                               ensure_ascii=False))
                lines.append("")
    return lines


def write_library(root, catalog):
    """Write normalized library metadata. Source mod files are never touched."""
    path = Path(root).resolve() / CATALOG_NAME
    path.write_text("\n".join(["schema = 1", "", *render_mods(catalog)]), encoding="utf-8",
                    newline="\n")
    return path


def unindexed(root):
    """Folders and plugins library.toml does not list yet, as new entries with free ids."""
    root = Path(root).resolve()
    existing = load_library(root) if (root / CATALOG_NAME).is_file() else {}
    known = {release["folder"].casefold()
             for mod in existing.values() for release in mod["releases"].values()}
    added = {}
    for mod in discover_library(root).values():
        if mod["releases"]["unknown"]["folder"].casefold() in known:
            continue
        mod_id, base, suffix = mod["id"], mod["id"], 2
        while mod_id in existing or mod_id in added:
            mod_id, suffix = f"{base}-{suffix}", suffix + 1
        added[mod_id] = {**mod, "id": mod_id}
    return added


def append_mods(root, mods):
    """Add mods to library.toml, leaving every existing entry exactly as written."""
    path = Path(root).resolve() / CATALOG_NAME
    if not path.is_file():
        write_library(root, mods)
    elif mods:
        text = path.read_text(encoding="utf-8")
        separator = "" if text.endswith("\n\n") else "\n" if text.endswith("\n") else "\n\n"
        with open(path, "a", encoding="utf-8", newline="\n") as stream:
            stream.write(separator + "\n".join(render_mods(mods)))
    return path


def index_library(root):
    """Add unindexed folders to library.toml, leaving every existing entry exactly as written."""
    added = unindexed(root)
    return append_mods(root, added), added


def folder_ids(catalog):
    """Library folder, case-folded, to (mod id, release) for each indexed release."""
    return {release["folder"].casefold(): (mod["id"], release)
            for mod in catalog.values() for release in mod["releases"].values()}


def convert_profile(text, catalog):
    """Rewrite a profile's `name = "folder"` mods as library ids where that is exact.

    A folder entry takes the whole folder, so only a release that is the whole folder with no
    optional components converts without changing what gets built. Returns the new text, the
    converted names and the names left alone with the reason."""
    by_folder = folder_ids(catalog)
    lines = text.splitlines(keepends=True)
    out, converted, skipped = [], [], []
    in_mods = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("["):
            in_mods = stripped.split("#")[0].strip() == "[[mods]]"
        match = in_mods and re.match(r"(\s*)name(\s*=\s*)(\"[^\"]*\"|'[^']*')(.*?)(\r?\n)?$", line)
        if not match:
            out.append(line)
            continue
        name = tomllib.loads("v = " + match.group(3))["v"]
        found = by_folder.get(name.casefold())
        if found is None:
            skipped.append((name, "no library.toml entry has this folder"))
        elif found[1]["roots"] != ["."] or found[1]["components"]:
            skipped.append((name, "its release uses subfolders or components; choose them in "
                                  "the GUI"))
            found = None
        if found is None:
            out.append(line)
            continue
        mod_id, release = found
        mod = catalog[mod_id]
        newline = match.group(5) or ""
        out.append(f"{match.group(1)}id{match.group(2)}{json.dumps(mod_id)}{match.group(4)}"
                   f"{newline}")
        if mod["default"] != release["version"] and len(mod["releases"]) > 1:
            out.append(f"{match.group(1)}version = {json.dumps(release['version'])}"
                       f"{newline or chr(10)}")
        converted.append(name)
    return "".join(out), converted, skipped


def resolve_selection(entry, library_root, catalog=None):
    """Resolve one profile mod to its ordered source roots and display identity."""
    library_root = Path(library_root).resolve()
    if "id" not in entry:
        name = entry["name"]
        return {"id": None, "name": name, "version": None, "components": [],
                "roots": [library_root / name]}
    catalog = catalog if catalog is not None else load_library(library_root)
    mod_id = entry["id"]
    if mod_id not in catalog:
        raise LibraryError(f"mod id is not installed: {mod_id}")
    mod = catalog[mod_id]
    version = entry.get("version")
    if version is None:
        if mod["default"]:
            version = mod["default"]
        elif len(mod["releases"]) == 1:
            version = next(iter(mod["releases"]))
        else:
            raise LibraryError(f"{mod_id}: select a version; no default release is set")
    if version not in mod["releases"]:
        raise LibraryError(f"{mod_id}: version {version!r} is not installed")
    release = mod["releases"][version]
    components = entry.get("components")
    if components is None:
        components = [item["id"] for item in release["components"].values() if item["default"]]
    unknown = set(components) - set(release["components"])
    if unknown:
        raise LibraryError(f"{mod_id} {version}: unknown components: {', '.join(sorted(unknown))}")
    selected = set(components)
    groups = {}
    for component_id in components:
        component = release["components"][component_id]
        hit = selected & set(component["conflicts"])
        if hit:
            raise LibraryError(f"{mod_id} {version}: {component_id} conflicts with "
                               + ", ".join(sorted(hit)))
        if component["group"]:
            prior = groups.setdefault(component["group"], component_id)
            if prior != component_id:
                raise LibraryError(f"{mod_id} {version}: components {prior} and {component_id} "
                                   f"are both in choice group {component['group']}")
    base = library_root / release["folder"]
    roots = [base if base.is_file() and value == "." else base / value
             for value in release["roots"]]
    component_roots = [base / value for component in release["components"].values()
                       for value in component["roots"]]
    layers = []
    for source in roots:
        # Installer options are often directories beneath an otherwise valid Data Files root.
        # Hide every declared option from that base layer, then add selected options explicitly.
        excluded = [path for path in component_roots
                    if path != source and path.is_relative_to(source)]
        layers.append({"path": source, "exclude": excluded})
    for component_id in components:
        selected_roots = [base / value for value in release["components"][component_id]["roots"]]
        roots.extend(selected_roots)
        layers.extend({"path": path, "exclude": []} for path in selected_roots)
    return {"id": mod_id, "name": mod["name"], "version": version,
            "components": list(components), "dependencies": release["dependencies"],
            "roots": roots, "layers": layers}


def dependency_order(mod_id, catalog, version=None):
    """Dependency ids before the requested release, each present once."""
    ordered = []

    def visit(current, chain, selected_version=None):
        if current in chain:
            raise LibraryError("dependency cycle: " + " -> ".join([*chain, current]))
        mod = catalog[current]
        selected_version = selected_version or mod["default"] or next(iter(mod["releases"]))
        if selected_version not in mod["releases"]:
            raise LibraryError(f"{current}: version {selected_version!r} is not installed")
        for dependency in mod["releases"][selected_version]["dependencies"]:
            visit(dependency, [*chain, current])
        if current not in ordered:
            ordered.append(current)

    visit(mod_id, [], version)
    return ordered


def available_plugins(selection, find_data_root=lambda path: path):
    """List plugins present across a resolved release's selected roots."""
    plugins = {}
    layers = selection.get("layers") or ({"path": path, "exclude": []}
                                         for path in selection["roots"])
    for layer in layers:
        source = layer["path"]
        excluded = [Path(path) for path in layer.get("exclude", [])]
        source = Path(source)
        if not source.exists():
            continue
        root = Path(find_data_root(str(source))) if source.is_dir() else source.parent
        paths = [source] if source.is_file() else root.rglob("*")
        for path in paths:
            if any(path == blocked or blocked in path.parents for blocked in excluded):
                continue
            if path.is_file() and path.suffix.lower() in (".esm", ".esp"):
                plugins[path.name.lower()] = path.name
    return [plugins[key] for key in sorted(plugins)]


DATA_DIRS = {"meshes", "textures", "icons", "sound", "bookart", "splash", "fonts", "video",
             "music", "mwse", "distantland", "shaders"}
DATA_FILES = (".esm", ".esp", ".bsa")
ARCHIVES = (".zip", ".7z", ".rar")
NEXUS_NAME = re.compile(r"^(?P<name>.+?)-(?P<id>\d+)-(?P<version>\d+(?:-\d+)*)-\d{9,}$")
NEXUS_URL = re.compile(r"nexusmods\.com/morrowind/mods/(\d+)", re.I)


def looks_like_data(path):
    """True when a directory holds Data Files content directly."""
    try:
        entries = list(Path(path).iterdir())
    except OSError:
        return False
    return any((entry.is_dir() and entry.name.lower() in DATA_DIRS)
               or (entry.is_file() and entry.suffix.lower() in DATA_FILES) for entry in entries)


def install_layout(root):
    """Data Files folders in an unpacked mod, relative to root, and the ones to install.

    A mod is either one Data Files tree, possibly wrapped in single folders, or several option
    folders side by side, of which the core one is installed by default."""
    root = Path(root)
    base = root
    while not looks_like_data(base):
        folders = [entry for entry in base.iterdir() if entry.is_dir()]
        if len(folders) != 1:
            break
        base = folders[0]
    if looks_like_data(base):
        found = [base.relative_to(root).as_posix()]
        return found, found
    options = sorted((entry for entry in base.iterdir() if entry.is_dir() and looks_like_data(entry)),
                     key=lambda entry: entry.name.casefold())
    found = [entry.relative_to(root).as_posix() for entry in options]
    chosen = [value for value, entry in zip(found, options)
              if re.match(r"(00|core\b|main\b|data files\b)", entry.name, re.I)]
    return found, chosen or found[:1]


def guess_release(path):
    """A mod name and version from an archive or folder name, Nexus style when it matches."""
    path = Path(path)
    stem = path.stem if path.suffix.lower() in ARCHIVES else path.name
    match = NEXUS_NAME.match(stem)
    if match:
        return match["name"].replace("_", " ").strip(), match["version"].replace("-", ".")
    return stem.replace("_", " ").strip(), ""


def nexus_id(url=None, source=None):
    """The Nexus Morrowind mod id in a mod page URL or a Nexus download's file name."""
    match = NEXUS_URL.search(url or "")
    if match:
        return int(match[1])
    if source:
        path = Path(source)
        match = NEXUS_NAME.match(path.stem if path.suffix.lower() in ARCHIVES else path.name)
        if match:
            return int(match["id"])
    return None


def seven_zip():
    for candidate in (BUNDLED_7Z, shutil.which("7z"), shutil.which("7za"),
                      *(Path(os.environ[key]) / "7-Zip" / "7z.exe"
                        for key in ("ProgramFiles", "ProgramW6432") if key in os.environ)):
        if candidate and Path(candidate).is_file():
            return str(candidate)
    return None


def extract_archive(archive, target):
    """Unpack a mod archive into target, which must not exist yet."""
    archive, target = Path(archive), Path(target)
    target.mkdir(parents=True)
    if archive.suffix.lower() == ".zip":
        with zipfile.ZipFile(archive) as stream:
            for member in stream.namelist():
                if Path(member).is_absolute() or ".." in Path(member).parts:
                    raise LibraryError(f"{archive.name}: unsafe path {member}")
            stream.extractall(target)
        return target
    tool = seven_zip()
    if tool is None:
        raise LibraryError(f"{archive.name}: install 7-Zip to unpack {archive.suffix} archives")
    result = subprocess.run([tool, "x", "-y", "-bso0", "-bsp0", f"-o{target}", str(archive)],
                            capture_output=True, text=True)
    if result.returncode:
        raise LibraryError(f"{archive.name}: 7-Zip failed: {result.stderr.strip()}")
    return target


def install_files(unpacked, selection, target):
    """Copy chosen files into a new mod folder, merging each Data Files root in order.

    selection is [(root, [paths relative to that root])]; later roots overwrite earlier ones."""
    unpacked, target = Path(unpacked), Path(target)
    if target.exists():
        raise LibraryError(f"{target} already exists")
    target.mkdir(parents=True)
    count = 0
    for base, files in selection:
        for relative in files:
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(unpacked / base / relative, destination)
            count += 1
    return count


def free_id(catalog, name):
    base = slug(name)
    mod_id, suffix = base, 2
    while mod_id in catalog:
        mod_id, suffix = f"{base}-{suffix}", suffix + 1
    return mod_id


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="validate an existing library.toml")
    check.add_argument("root")
    scan = sub.add_parser("scan", help="list folders and plugins library.toml does not index yet")
    scan.add_argument("root")
    scan.add_argument("--write", action="store_true",
                      help="add them to library.toml; existing entries are kept as written")
    convert = sub.add_parser("convert", help="rewrite a profile's folder names as library ids")
    convert.add_argument("profile")
    convert.add_argument("--library", help="library root (default: the profile's library)")
    convert.add_argument("--dry-run", action="store_true", help="report without writing")
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            catalog = load_library(args.root)
            print(f"{len(catalog)} installed mods")
        elif args.command == "scan":
            if args.write:
                path, added = index_library(args.root)
            else:
                path, added = Path(args.root) / CATALOG_NAME, unindexed(args.root)
            for mod_id, mod in added.items():
                print(f"  {mod_id}: {mod['releases']['unknown']['folder']}")
            verb = "added to" if args.write else "not yet in"
            print(f"{len(added)} mods {verb} {path}")
        else:
            profile = Path(args.profile)
            with open(profile, encoding="utf-8", newline="") as stream:
                text = stream.read()
            library = args.library or tomllib.loads(text).get("profile", {}).get("library")
            if not library:
                raise LibraryError("the profile has no library; pass --library")
            new_text, converted, skipped = convert_profile(text, load_library(library))
            for name in converted:
                print(f"  converted {name}")
            for name, reason in skipped:
                print(f"  kept {name}: {reason}")
            if converted and not args.dry_run:
                with open(profile, "w", encoding="utf-8", newline="") as stream:
                    stream.write(new_text)
            print(f"{len(converted)} converted, {len(skipped)} kept as folder names")
        return 0
    except (LibraryError, OSError) as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    raise SystemExit(main())
