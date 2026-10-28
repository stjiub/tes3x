"""Retained world state, the character store, and authority assignment."""

import json
import math
import os
import re
import time
from .proto import (ACTOR_DEAD, ACTOR_IN_COMBAT, CHARACTER_BACKUPS, EVENT_DATA, OWNER_HOLD,
                    OWNER_MARGIN, SPAWN_TWIN, SPAWN_TWIN_SECONDS, START_NAME, STREAM_NAME)
from .transport import Clock


def spawn_twin(spawns, spawn, origin, token, now, deaths=()):
    """The id of a spawn that this one repeats: the maker sending it again (by its token), the
    living creature of the same leveled placeholder, or the same object made in the same place by
    another console's copy of a script moments ago."""
    for sid, known in spawns.items():
        if token and known["origin"] == origin and known.get("token") == token:
            return sid
    if spawn.get("leveled"):
        for sid, known in spawns.items():
            if known.get("leveled") == spawn["leveled"] and not known["removed"] and \
                    sid not in deaths:
                return sid
        return None
    if spawn.get("summon"):
        return None
    for sid, known in spawns.items():
        if known["removed"] or known["origin"] == origin or known.get("summon") or \
                known["id"].lower() != spawn["id"].lower() or known["cell"] != spawn["cell"]:
            continue
        far = max(abs(a - b) for a, b in zip(known["pos"], spawn["pos"]))
        if far <= SPAWN_TWIN and now - known.get("made", 0) <= SPAWN_TWIN_SECONDS:
            return sid
    return None


def load_world(path):
    """The saved world of one load order, or None."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None


def save_world(path, world):
    """Write by temporary file and rename, so a crash leaves the last whole save."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(world, f, indent=1, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    deadline = time.monotonic() + 0.25
    while True:
        try:
            os.replace(path + ".tmp", path)
            break
        except PermissionError as error:
            # Windows readers can temporarily deny replacement of an otherwise writable file.
            if getattr(error, "winerror", None) not in (5, 32, 33) or \
                    time.monotonic() >= deadline:
                raise
            time.sleep(0.01)


class World:
    """What --world keeps for one load order; each joining client is sent all of it."""

    SPAWN_FIELDS = ("cell", "count", "removed", "pos", "rot", "id", "origin", "token", "data",
                    "condition", "charge", "leveled", "summon")

    def __init__(self):
        self.path = None
        self.dirty = False
        self.saved = 0.0
        self.next_spawn = 1
        self.deaths = {}  # refid -> the client that reported it; replayed to each joining client
        self.weather = {}  # region index -> weather, the session's; replayed to each joining client
        # refid -> (cell index, state, lock level); replayed to each joining client
        self.objects = {}
        # actor id -> STATUS values after the id; replayed to each joining client
        self.statuses = {}
        self.topics = []
        # quest id lowered -> [quest id, indices reached, ascending]: the world's journal
        self.journal = {}
        # faction id lowered -> {"id", "rank" (None until a member sets it), "reputation"}
        self.factions = {}
        # global name lowered -> [name, value]: the world's globals, as consoles last set them
        self.globals = {}
        # refid -> {slot: raw value}: data-file references' script locals; replayed likewise
        self.locals = {}
        # refid -> {slot: raw value}: actor attributes, skills, barter gold, corpse stamp
        self.actor_stats = {}
        # refid -> bytes: an actor's complete set of active effects (PLAYER_EFFECT_BYTES records)
        self.actor_effects = {}
        # (client id, refid) -> [bytes so far, next part, parts]: sets still arriving
        self.actor_effect_parts = {}
        # refid -> base object id: deaths the engine's kill counter counted; replayed as counts
        self.kills = {}
        # refid -> [cell kind, grid x, grid y, interior, x, y, z, heading, health, magicka,
        # fatigue, flags]: each actor's latest state; sent to a cell's next authority
        self.actors = {}
        # refid -> [cell it first left, cell it is in, x, y, z, heading]: actors a script moved;
        # replayed to each joining client
        self.moves = {}
        # script id lowered -> {"id", "ref", "values": {slot: raw value}}: running world scripts
        self.scripts = {}
        # refid -> {"kind", "cell", "config" (hex), "reset", "target"}: each actor's latest AI
        # package from a player or a world script; target is None, ["player", character],
        # ["object", id] or ["bot"]
        self.ai = {}
        # record id -> hex blob (unpack_record): spells made in play; replayed likewise
        self.records = {}
        # spawn id -> reference made at run time (unpack_spawn), removed ones too; replayed likewise
        self.spawns = {}
        # refid -> {"cell", "entries", "origin"}: a container's latest contents; sent to whoever
        # loads its cell (WANT)
        self.contents = {}
        self.clock = None

    def load(self, path, now, clock=True):
        """Add what path holds, and its clock unless clock is false; false when there is none."""
        self.path = path
        saved = load_world(path)
        if not saved:
            return False
        self.deaths.update({int(k): v for k, v in saved.get("deaths", {}).items()})
        self.objects.update({int(k): tuple(v) for k, v in saved.get("objects", {}).items()})
        self.spawns.update({int(k): v for k, v in saved.get("spawns", {}).items()})
        self.contents.update({int(k): v for k, v in saved.get("contents", {}).items()})
        self.next_spawn = max(self.next_spawn, saved.get("next_spawn", 1))
        self.weather.update({int(k): v for k, v in saved.get("weather", {}).items()})
        self.statuses.update({int(k): tuple(v) for k, v in saved.get("statuses", {}).items()})
        known = {name.lower(): name for name in self.topics}
        known.update((name.lower(), name) for name in saved.get("topics", []))
        self.topics = list(known.values())
        self.journal.update(saved.get("journal", {}))
        self.factions.update(saved.get("factions", {}))
        self.globals.update(saved.get("globals", {}))
        self.locals.update({int(k): {int(s): v for s, v in values.items()}
                            for k, values in saved.get("locals", {}).items()})
        self.kills.update({int(k): v for k, v in saved.get("kills", {}).items()})
        self.actor_stats.update({int(k): {int(s): v for s, v in values.items()}
                                 for k, values in saved.get("actor_stats", {}).items()})
        self.actor_effects.update({int(k): bytes.fromhex(v)
                                   for k, v in saved.get("actor_effects", {}).items()})
        self.actors.update({int(k): v for k, v in saved.get("actors", {}).items()})
        self.moves.update({int(k): v for k, v in saved.get("moves", {}).items()})
        self.ai.update({int(k): v for k, v in saved.get("ai", {}).items()})
        self.records.update(saved.get("records", {}))
        self.scripts.update({k: dict(v, values={int(s): x for s, x in v["values"].items()})
                             for k, v in saved.get("scripts", {}).items()})
        for spawn in self.spawns.values():
            if spawn.get("summon"):
                spawn["removed"] = True  # Active effects recreate summons for their target.
        if saved.get("clock") and clock:
            self.clock = Clock(*saved["clock"], now)
        return True

    def save(self, now):
        state = {"deaths": {str(k): v for k, v in self.deaths.items()},
                 "objects": {str(k): list(v) for k, v in self.objects.items()},
                 "spawns": {str(k): {f: v.get(f, 0) for f in self.SPAWN_FIELDS}
                            for k, v in self.spawns.items()},
                 "next_spawn": self.next_spawn,
                 "contents": {str(k): v for k, v in self.contents.items()},
                 "weather": {str(k): v for k, v in self.weather.items()},
                 "statuses": {str(k): list(v) for k, v in self.statuses.items()},
                 "topics": self.topics,
                 "journal": self.journal,
                 "factions": self.factions,
                 "globals": self.globals,
                 "locals": {str(k): {str(s): v for s, v in values.items()}
                            for k, values in self.locals.items()},
                 "kills": {str(k): v for k, v in self.kills.items()},
                 "actor_stats": {str(k): {str(s): v for s, v in values.items()}
                                for k, values in self.actor_stats.items()},
                 "actor_effects": {str(k): v.hex() for k, v in self.actor_effects.items()},
                 "actors": {str(k): v for k, v in self.actors.items()},
                 "moves": {str(k): v for k, v in self.moves.items()},
                 "ai": {str(k): v for k, v in self.ai.items()},
                 "records": self.records,
                 "scripts": {k: dict(v, values={str(s): x for s, x in v["values"].items()})
                             for k, v in self.scripts.items()}}
        if self.clock:
            self.clock.advance(now)
            state["clock"] = [self.clock.hour, self.clock.day, self.clock.month, self.clock.year,
                              self.clock.days_passed, self.clock.scale]
        save_world(self.path, state)
        self.dirty, self.saved = False, now

    def __str__(self):
        return (f"{len(self.deaths)} deaths, {len(self.objects)} objects, "
                f"{len(self.spawns)} spawns, {len(self.contents)} containers, "
                f"{len(self.weather)} regions, {len(self.statuses)} statuses, "
                f"{len(self.journal)} quests, "
                f"{len(self.globals)} globals, {len(self.locals)} scripted references, "
                f"{len(self.actors)} actors, {len(self.actor_stats)} with stats, "
                f"{len(self.actor_effects)} with effects, "
                f"{len(self.kills)} kills, "
                f"{len(self.moves)} moved, "
                f"{len(self.ai)} AI packages, {len(self.records)} records, "
                f"{len(self.scripts)} scripts"
                + (f", clock {self.clock}" if self.clock else ""))


def latest_character(folder):
    """The newest kept character's save in folder (not a backup), or None."""
    if not folder or not os.path.isdir(folder):
        return None
    saves = [os.path.join(folder, f) for f in os.listdir(folder)
             if f.lower().endswith(".ess") and not re.search(r"\.\d+\.ess$", f, re.I)]
    return max(saves, key=os.path.getmtime, default=None)


def keep_character(upload, folder):
    """Move a finished .ess upload whose header names a player to folder, shifting the versions
    it replaces to NAME.1.ess and on; its header, or None when it is not a save."""
    from tes3x.saves import HEAD_LIMIT, parse_header
    with open(upload, "rb") as stream:
        head = parse_header(stream.read(HEAD_LIMIT))
    if not head["player"]:
        return None
    os.makedirs(folder, exist_ok=True)
    stem = os.path.basename(upload)[:-4]
    versions = [os.path.join(folder, stem + ".ess")] + [
        os.path.join(folder, f"{stem}.{i}.ess") for i in range(1, CHARACTER_BACKUPS + 1)]
    for newer, older in reversed(list(zip(versions, versions[1:]))):
        if os.path.exists(newer):
            os.replace(newer, older)
    os.replace(upload, versions[0])
    return head


def save_player(path):
    """The player's name in a save's header, or None."""
    from tes3x.saves import HEAD_LIMIT, parse_header
    with open(path, "rb") as stream:
        return parse_header(stream.read(HEAD_LIMIT))["player"]


def character_slug(name):
    return re.sub(r"[^A-Za-z0-9 _-]", "-", name or "").strip()[:24] or "character"


def kept_characters(root, fixtures=False):
    """Character folders with retained streams, newest first; optional save fixtures for tests."""
    if not root or not os.path.isdir(root):
        return []
    loose = latest_character(root) if fixtures else None
    if loose:
        folder = new_character_folder(root, save_player(loose))
        os.makedirs(folder)
        for f in os.listdir(root):
            if f.lower().endswith(".ess") or f == STREAM_NAME:
                os.replace(os.path.join(root, f), os.path.join(folder, f))
    found = [(f, (latest_character(os.path.join(root, f)) if fixtures else None) or
              (os.path.join(root, f, STREAM_NAME) if os.path.isfile(
                  os.path.join(root, f, STREAM_NAME)) else None)) for f in os.listdir(root)
             if os.path.isdir(os.path.join(root, f))]
    return sorted(((f, p) for f, p in found if p), key=lambda c: -os.path.getmtime(c[1]))


def new_character_folder(root, player):
    slug = character_slug(player)
    folder, n = os.path.join(root, slug), 1
    while os.path.exists(folder):
        n += 1
        folder = os.path.join(root, f"{slug}-{n}")
    return folder


def load_starts(path):
    """Start points as (name, script lines), from a TOML file of [[start]] tables."""
    import tomllib
    with open(path, "rb") as f:
        tables = tomllib.load(f).get("start", [])
    starts = []
    for t in tables:
        name = str(t["name"])
        if not name or len(name) > START_NAME or '"' in name or not name.isascii():
            raise ValueError(f"{path}: start name {name!r}: 1 to {START_NAME} ASCII characters, "
                             "no double quotes")
        x, y, z = (float(v) for v in t["position"])
        turn = float(t.get("rotation", 0))
        where = f"{x:g} {y:g} {z:g} {turn:g}"
        lines = [f'Player->PositionCell {where} "{t["cell"]}"' if "cell" in t
                 else f"Player->Position {where}"]
        lines += [f'Player->RemoveItem "{i}" {int(n)}' for i, n in t.get("remove", [])]
        lines += [f'Player->AddItem "{i}" {int(n)}' for i, n in t.get("items", [])]
        lines += [f'Player->Equip "{i}"' for i in t.get("equip", [])]
        lines += [str(line) for line in t.get("script", [])]
        for line in lines:
            if len(line) >= EVENT_DATA or not line.isascii() or "\n" in line:
                raise ValueError(f"{path}: {name}: line {line!r} is not one ASCII line under "
                                 f"{EVENT_DATA} bytes")
        starts.append((name, lines))
    if not starts:
        raise ValueError(f"{path}: no [[start]] tables")
    return starts


def assign_authority(owners, candidates, forced=None):
    """Each loaded cell's authority. candidates: cell -> [(client, stands in it)] in joining
    order. An authority keeps a cell while it has it loaded, unless it only has it loaded and
    another client stands in it; forced takes every cell it has loaded."""
    result = {}
    for key, cands in candidates.items():
        ids = [c for c, _ in cands]
        standing = [c for c, here in cands if here]
        current = owners.get(key)
        if forced in ids:
            result[key] = forced
        elif current in ids and (current in standing or not standing):
            result[key] = current
        else:
            result[key] = (standing or ids)[0]
    return result


def assign_owners(previous, actors, players, cell_owners, now):
    """Each actor's owner: the player it fights if that player loads its cell, else the nearest
    player that does, since the engine runs actors only within aiDistance of its own player.
    previous: id -> (client, since); actors: id -> (cell, x, y, flags, combat target); players:
    client -> (loaded cells, x, y). A dead actor keeps its owner."""
    result = {}
    for actor, (key, x, y, flags, target) in actors.items():
        near = {c: math.hypot(x - px, y - py) for c, (loaded, px, py) in players.items()
                if key in loaded}
        if not near:
            continue
        fights = flags & ACTOR_IN_COMBAT and target in near
        want = target if fights else min(near, key=lambda c: (near[c], c))
        current, since = previous.get(actor, (cell_owners.get(key), now - OWNER_HOLD))
        if current not in near:
            current, since = want, now
        if (want != current and not flags & ACTOR_DEAD and now - since >= OWNER_HOLD
                and (fights or near[current] - near[want] > OWNER_MARGIN)):
            current, since = want, now
        result[actor] = (current, since)
    return result
