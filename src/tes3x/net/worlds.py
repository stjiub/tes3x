"""Named server worlds: one folder each under a worlds folder, holding the world's settings in
world.toml beside what `serve --world` keeps there. The server key sits in the worlds folder and
is shared, since a console pins the key it first meets at an address."""

import json
import os
import re
import shutil
import tomllib
from pathlib import Path

SETTINGS = "world.toml"
SERVER_KEY = "server.key"
CHARACTERS = "characters"
STREAM = "stream.json"
# What a world keeps besides its settings, for moving an old --world folder into one.
KEPT = ("characters", "uploads", "bans.txt", "admitted.txt", "admin-password.txt")
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _.-]{0,39}")


class WorldError(Exception):
    pass


def check_name(name):
    name = name.strip()
    if not NAME.fullmatch(name) or name.endswith((".", " ")):
        raise WorldError(f"{name!r}: 1 to 40 letters, digits, spaces, '.', '_' or '-'")
    return name


def worlds(root):
    """The worlds in root, by name."""
    root = Path(root)
    if not root.is_dir():
        return []
    return sorted((p.name for p in root.iterdir() if (p / SETTINGS).is_file()),
                  key=str.casefold)


def folder(root, name):
    return Path(root) / name


def load(root, name):
    try:
        with open(folder(root, name) / SETTINGS, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return {}
    except tomllib.TOMLDecodeError as exc:
        raise WorldError(f"{name}: {exc}") from exc


def _value(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_value(v) for v in value) + "]"
    return json.dumps(str(value), ensure_ascii=False)


def save(root, name, settings):
    path = folder(root, name)
    path.mkdir(parents=True, exist_ok=True)
    text = "".join(f"{key} = {_value(value)}\n" for key, value in settings.items()
                   if value is not None)
    temporary = path / (SETTINGS + ".new")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temporary, path / SETTINGS)


def _free(root, name):
    name = check_name(name)
    if folder(root, name).exists():
        raise WorldError(f"{name}: already exists")
    return name


def create(root, name, settings):
    name = _free(root, name)
    save(root, name, settings)
    return name


def duplicate(root, source, name, characters=True):
    """A copy of source's settings and world state, with its characters unless told not to."""
    name = _free(root, name)
    ignore = shutil.ignore_patterns(CHARACTERS, "uploads") if not characters else None
    shutil.copytree(folder(root, source), folder(root, name), ignore=ignore)
    return name


def rename(root, source, name):
    name = check_name(name)
    if name.casefold() != source.casefold() and folder(root, name).exists():
        raise WorldError(f"{name}: already exists")
    os.replace(folder(root, source), folder(root, name))
    return name


def delete(root, name):
    path = folder(root, name)
    if not (path / SETTINGS).is_file():
        raise WorldError(f"{name}: not a world")
    shutil.rmtree(path)


def adopt(root, name, old, settings):
    """A world made from an old --world folder: copies what it kept, and its key to the worlds
    folder when that has none. The old folder is left as it was."""
    name = create(root, name, settings)
    old = Path(old)
    if old.is_dir() and old.resolve() != folder(root, name).resolve():
        target = folder(root, name)
        for entry in old.iterdir():
            if entry.name in KEPT or re.fullmatch(r"[0-9a-f]{8}\.json", entry.name):
                (shutil.copytree if entry.is_dir() else shutil.copy2)(entry, target / entry.name)
        if (old / SERVER_KEY).is_file() and not (Path(root) / SERVER_KEY).exists():
            shutil.copy2(old / SERVER_KEY, Path(root) / SERVER_KEY)
    return name


def characters(world):
    """Characters the world keeps, newest first: dicts of name, race, class, level, key (its
    console key's fingerprint), dead, played (mtime) and folder."""
    out = []
    root = Path(world) / CHARACTERS
    if not root.is_dir():
        return out
    for stream in root.glob(f"*/*/{STREAM}"):
        try:
            kept = json.loads(stream.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            kept = {}
        identity = kept.get("identity") or {}
        level = bytes.fromhex(kept["level"]) if kept.get("level") else b""
        out.append({"name": identity.get("name") or stream.parent.name,
                    "race": identity.get("race", ""),
                    "class": identity.get("class_name") or identity.get("class", ""),
                    "level": int.from_bytes(level[:2], "little") if len(level) >= 2 else None,
                    "key": stream.parent.parent.name, "dead": bool(kept.get("dead")),
                    "played": stream.stat().st_mtime, "folder": stream.parent})
    return sorted(out, key=lambda c: -c["played"])


def delete_character(world, character_folder):
    path = Path(character_folder).resolve()
    if path.parent.parent != (Path(world) / CHARACTERS).resolve():
        raise WorldError(f"{path}: not one of this world's characters")
    shutil.rmtree(path)
    if not any(path.parent.iterdir()):
        path.parent.rmdir()
