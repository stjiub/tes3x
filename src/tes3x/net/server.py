"""Session server, retained world and player state, and test bot."""

import glob
import hashlib
import hmac
import ipaddress
import json
import math
import os
import queue
import random
import re
import select
import signal
import socket
import struct
import sys
import tes3x.netbuild as tes3x_netbuild
import threading
import time
import traceback
from .proto import (ACTOR, ACTORS, ACTORS_PER_PACKET, ACTOR_DEAD, ACTOR_IN_COMBAT, ACTOR_PERIOD,
                    ADMIN_PASSWORD_MIN, ADMIN_PORT, ANNOUNCE_WAIT, ATTRIBUTE_NAMES,
                    AUTHORITY_PERIOD, BAN_KINDS, BOT_ID, BUILD, BULK_ACK, BULK_ACK_BODY,
                    BULK_ACK_EVERY, BULK_BAD_HASH, BULK_CHUNK, BULK_DONE, BULK_MAX, BULK_NAME,
                    BULK_NO_SPACE, BULK_OFFER, BULK_PROBE, BULK_RECEIVING, BULK_REFUSED,
                    BULK_RESEND, BULK_STATUS, BULK_WINDOW_IN, BUSY_SAVING, BYE, CAIUS_PACKAGE,
                    CELL_UNITS, CHARACTERS_LISTED, CHARACTER_BACKUPS, CHARACTER_FILE,
                    CHARACTER_MAGIC, CHUNK, CLIENT_RATE, CLOCK, CLOCK_BODY, CLOCK_INTERVAL,
                    CONTENTS_HEAD, CONTENTS_ROLLED, DEVICE_NAMES, DHCP_CLIENT, DHCP_SERVER,
                    DNS_PORT, ENTRY_DATA, EVENTS, EVENTS_GAP, EVENTS_HEAD, EVENT_ACTOR_EQUIPMENT,
                    EVENT_AFFECT, EVENT_AUTHORITY, EVENT_BOUNTY, EVENT_BUSY, EVENT_CAST,
                    EVENT_CHARS, EVENT_CONTENTS, EVENT_DATA, EVENT_DEATH, EVENT_EQUIPMENT,
                    EVENT_GAME, EVENT_HIT, EVENT_HOLD, EVENT_HOLD_BROKEN, EVENT_IDENTITY,
                    EVENT_LOAD, EVENT_NEWCHAR, EVENT_OBJECTS, EVENT_OFFER, EVENT_OWNERS,
                    EVENT_PICK, EVENT_PLAYER, EVENT_PLAYER_HIT, EVENT_REMOVE, EVENT_RUN,
                    EVENT_SAVE, EVENT_SHOT, EVENT_SNAPSHOT, EVENT_SPAWN, EVENT_SPELL,
                    EVENT_STATUS, EVENT_TEXT, EVENT_WANT, EVENT_WEATHER, EVENT_WELCOME, GAME_NEW,
                    GAME_NONE, GONE, GUEST_IP, HANDSHAKE1, HANDSHAKE2, HANDSHAKE3,
                    HANDSHAKES_PENDING, HANDSHAKE_KEEP, HANDSHAKE_PAD, HANDSHAKE_RATE,
                    HANDSHAKE_RATE_ALL, HEARTBEAT, HELLO, HELLO_BODY, INNER, IN_WORLD, KEEP, KEY,
                    KEY_EXTERIOR, KEY_INTERIOR, LEVEL, LOBBY, LOG_LEVELS, MAGIC, MANAGER,
                    MANAGER_VERSION, MODIFIER, MONTH_DAYS, NOISE_TAG, NO_ANIM, Noise, OUTER,
                    OWNERS_PER_EVENT, OWNER_HOLD, OWNER_MARGIN, OWNER_PAIR, OWNER_STALE,
                    PACE_PACKETS, PACE_WINDOW, PASSWORD_MAX, PASSWORD_RATE, PEER, PEER_IP,
                    PICK_CHARACTER, PICK_NEW, PICK_START, PLACE, PLACE_HOLD, PLAYER_ALIVE,
                    PLAYER_BOUNTY, PLAYER_DEATH, PLAYER_EFFECTS, PLAYER_EFFECTS_MAX,
                    PLAYER_EFFECT_BYTES, PLAYER_IDENTITY, PLAYER_ITEMS, PLAYER_JOURNAL,
                    PLAYER_LEVEL, PLAYER_MODIFIERS, PLAYER_PLACE, PLAYER_READY, PLAYER_RESPAWN,
                    PLAYER_SKILLS, PLAYER_SPELLS, PLAYER_VITALS, PLAYER_WORN, PROLOGUE,
                    QUEST_INDICES, REFUSE, REFUSED_BANNED, REFUSED_FULL, REFUSED_KICKED,
                    REFUSED_LOAD_ORDER, REFUSED_PASSWORD, REFUSED_PROTOCOL, REFUSED_STALE,
                    REFUSE_BODY, RELAY, REMOTE_CHALLENGE, REMOTE_COMMAND, REMOTE_HEAD,
                    REMOTE_HELLO, REMOTE_MAGIC, REMOTE_NONCE, REMOTE_NONCES, REMOTE_NONCE_SECONDS,
                    REMOTE_REFUSED, REMOTE_REPLY, REMOTE_VERSION, RESEND, RESPAWN, RESPAWN_PLACES,
                    SEALED, SERVER_EVENTS, SHOT, SKILL, SKILL_NAMES, SOURCE_SPELL, SPAWN,
                    SPAWN_IDS, SPAWN_TWIN, SPAWN_TWIN_SECONDS, SPELL, SPELLS_ADD, SPELLS_REMOVE,
                    SPELLS_SNAPSHOT, SPYMASTER_DONE, SPYMASTER_GIVEN, SPYMASTER_QUEST, STANCE,
                    STARTS, START_NAME, STATE, STATE_BODY, STATE_SIZE, STATUS, STREAM_NAME, T3MP,
                    T3MP_VERSION, TARGETED, UPLOAD_FILES, VITALS, WEATHER_OFFER, WELCOME,
                    admin_secret, cell_keys, checkpoint_name, crypto, describe_contents,
                    describe_identity, describe_key, describe_level, describe_object,
                    describe_spawn, describe_state, describe_status, describe_weather,
                    fingerprint, finite, now_us, pack_contents, pack_equipment, pack_events,
                    pack_identity, pack_items, pack_journal, pack_names, pack_objects,
                    pack_player_effects, pack_player_identity, pack_spawn, pack_weather,
                    pack_worn, placeable, plain_name, remote_key, same_place, sane_clock, seal,
                    unpack_actor_equipment, unpack_contents, unpack_equipment, unpack_events,
                    unpack_identity, unpack_items, unpack_journal, unpack_objects,
                    unpack_player_effects, unpack_player_identity, unpack_removes, unpack_spawn,
                    unpack_weather, unpack_worn, unseal, wire_text, x25519_public, zstr)
from .tunnel import (Tunnel, arp_frame, dhcp_reply, dns_reply, udp_frame, udp_from_frame,
                     udp_socket)
from .validation import valid_client_event

def quiet_admin(line):
    """Whether an admin command goes unlogged: `list`, which the GUI polls."""
    return line.strip().lower() == b"list"


def decode(payload):
    """(seq, count, mac) of a broadcast test datagram, or None."""
    if len(payload) < 22 or payload[:8] != MAGIC:
        return None
    seq, count = struct.unpack_from("<II", payload, 8)
    return seq, count, payload[16:22].hex(":")


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
    os.replace(path + ".tmp", path)


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
                 "statuses": {str(k): list(v) for k, v in self.statuses.items()}}
        if self.clock:
            self.clock.advance(now)
            state["clock"] = [self.clock.hour, self.clock.day, self.clock.month, self.clock.year,
                              self.clock.days_passed, self.clock.scale]
        save_world(self.path, state)
        self.dirty, self.saved = False, now

    def __str__(self):
        return (f"{len(self.deaths)} deaths, {len(self.objects)} objects, "
                f"{len(self.spawns)} spawns, {len(self.contents)} containers, "
                f"{len(self.weather)} regions, {len(self.statuses)} statuses"
                + (f", clock {self.clock}" if self.clock else ""))


class Reliable:
    """One direction pair of a session's event channel: numbered from 1, resent until acked,
    delivered only in order."""

    def __init__(self):
        self.out = []  # unacked (seq, kind, origin, data), oldest first
        self.out_next = 1
        self.in_next = 1
        self.last_send = 0.0
        self.sent = self.resent = self.delivered = self.stale = 0
        self.sent_upto = 1

    def queue(self, kind, origin, data):
        self.out.append((self.out_next, kind, origin, bytes(data)))
        self.out_next += 1

    def packet(self, now, resend=True):
        """The EVENTS body to send: the ack, then the unacked events that fit."""
        self.last_send = now
        body = pack_events(self.in_next - 1, self.out if resend else [])
        for seq, *_ in unpack_events(body)[1]:
            self.sent += 1
            if seq < self.sent_upto:
                self.resent += 1
            else:
                self.sent_upto = seq + 1
        return body

    def receive(self, body):
        """The events now deliverable, in order; the caller then sends an ack."""
        ack, events = unpack_events(body)
        self.out = [e for e in self.out if e[0] > ack]
        ready = []
        for event in events:
            if event[0] == self.in_next:
                ready.append(event)
                self.in_next += 1
                self.delivered += 1
            elif event[0] < self.in_next:
                self.stale += 1
        return ready, bool(events)


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


class PlayerStream:
    """One character's state as its console streamed it: each item's stacks, the level block,
    each skill and current modifier, active effects, quest indices and known spells. It holds
    what changed since the character's first launch on this server and replays it when the
    character joins."""

    def __init__(self, path):
        self.path = path
        self.items, self.skills, self.modifiers, self.journal, self.level = {}, {}, {}, {}, None
        self.spells = None
        self.effects = self.effects_parts = None
        self.vitals = self.place = None
        self.bounty = self.identity = self.worn = None
        self.dead = False  # died and not yet back
        self.arriving = None  # (item id, entries so far, next part)
        self.identity_parts = self.worn_parts = None  # (body so far, next part)
        self.dirty = False
        try:
            with open(path, encoding="utf-8") as f:
                kept = json.load(f)
        except FileNotFoundError:
            return
        self.items = kept.get("items", {})
        self.skills = {int(k): v for k, v in kept.get("skills", {}).items()}
        self.modifiers = {int(k): v for k, v in kept.get("modifiers", {}).items()}
        self.journal = kept.get("journal", {})
        self.level = bytes.fromhex(kept["level"]) if kept.get("level") else None
        self.vitals = kept.get("vitals")
        self.place = bytes.fromhex(kept["place"]) if kept.get("place") else None
        self.dead = kept.get("dead", False)
        self.spells = kept.get("spells")
        self.effects = kept.get("effects")
        self.bounty = kept.get("bounty")
        self.identity = kept.get("identity")
        self.worn = kept.get("worn")

    def reset(self):
        """A new character: nothing streamed so far belongs to it."""
        self.items, self.skills, self.modifiers, self.journal, self.level = {}, {}, {}, {}, None
        self.spells = None
        self.effects = self.effects_parts = None
        self.vitals = self.place = self.arriving = None
        self.bounty = self.identity = self.identity_parts = None
        self.worn = self.worn_parts = None
        self.dead = False
        self.dirty = True

    def checkpoint(self):
        """A new checkpoint holds each quest's entries up to its index; the latest is enough."""
        for quest, indices in self.journal.items():
            if len(indices) > 1:
                self.journal[quest] = indices[-1:]
                self.dirty = True

    def keep_place(self, body):
        """Keep where the player is (a STATE_BODY); written once it moved PLACE_NEAR or changed
        cell."""
        if not self.place or not same_place(self.place, body):
            self.dirty = True
        self.place = bytes(body)

    def take(self, data):
        """Keep one PLAYER event; what changed, for the log, or None."""
        kind = data[0] if data else 0
        if kind == PLAYER_ITEMS and len(data) > 3:
            part, parts, item, entries = unpack_items(data)
            if part == 0:
                self.arriving = (item, [], 0)
            if not self.arriving or self.arriving[0] != item or self.arriving[2] != part:
                self.arriving = None
                return None
            self.arriving = (item, self.arriving[1] + entries, part + 1)
            if part + 1 < parts:
                return None
            entries, self.arriving = self.arriving[1], None
            if entries:
                self.items[item] = entries
            else:
                self.items.pop(item, None)
            self.dirty = True
            return (f"carries {describe_contents([[item, *e] for e in entries])}"
                    if entries else f"no longer carries {item}")
        if kind == PLAYER_LEVEL and len(data) >= 1 + LEVEL.size:
            body = bytes(data[1:1 + LEVEL.size])
            if not finite(*LEVEL.unpack(body)[13:]):
                return None
            self.level, self.dirty = body, True
            return describe_level(body)
        if kind == PLAYER_SKILLS and len(data) >= 2:
            changed = []
            for i in range(min(data[1], (len(data) - 2) // SKILL.size)):
                skill, base, progress = SKILL.unpack_from(data, 2 + i * SKILL.size)
                if skill < len(SKILL_NAMES) and finite(base, progress):
                    self.skills[skill] = [base, progress]
                    changed.append(f"{SKILL_NAMES[skill]} {base:.0f} ({progress:.2f})")
            self.dirty = self.dirty or bool(changed)
            return ", ".join(changed) or None
        if kind == PLAYER_EFFECTS and len(data) >= 5:
            part, parts = struct.unpack_from('<HH', data, 1)
            if part == 0:
                self.effects_parts = (b"", 0, parts)
            pending = self.effects_parts
            if (not pending or not parts or part >= parts or pending[1:] != (part, parts)
                    or len(pending[0]) + len(data) - 5 > PLAYER_EFFECT_BYTES * PLAYER_EFFECTS_MAX):
                self.effects_parts = None
                return None
            body = pending[0] + bytes(data[5:])
            self.effects_parts = (body, part + 1, parts)
            if part + 1 < parts:
                return None
            self.effects_parts = None
            try:
                effects = unpack_player_effects(body)
            except ValueError:
                return None
            if effects == self.effects:
                return None
            self.effects, self.dirty = effects, True
            return f"has {len(effects)} active effects"
        if kind == PLAYER_MODIFIERS and len(data) >= 2:
            changed = []
            for i in range(min(data[1], (len(data) - 2) // MODIFIER.size)):
                stat, current = MODIFIER.unpack_from(data, 2 + i * MODIFIER.size)
                if stat < len(ATTRIBUTE_NAMES) + len(SKILL_NAMES) and finite(current):
                    self.modifiers[stat] = current
                    name = (ATTRIBUTE_NAMES + SKILL_NAMES)[stat]
                    changed.append(f"{name} current {current:.0f}")
            self.dirty = self.dirty or bool(changed)
            return ", ".join(changed) or None
        if kind == PLAYER_JOURNAL and len(data) >= 2:
            quests = unpack_journal(data)
            for quest, index in quests:
                indices = self.journal.setdefault(quest, [])
                if not indices or indices[-1] != index:
                    indices.append(index)
                    del indices[:-QUEST_INDICES]
            self.dirty = self.dirty or bool(quests)
            return "journal " + ", ".join(f"{q} {i}" for q, i in quests) if quests else None
        if kind == PLAYER_VITALS and len(data) >= 1 + VITALS.size:
            vitals = list(VITALS.unpack_from(data, 1))
            if not finite(*vitals):
                return None
            self.vitals, self.dirty = vitals, True
            return "now health {:.0f}, magicka {:.0f}, fatigue {:.0f}".format(*vitals)
        if kind == PLAYER_SPELLS and len(data) >= 4 and data[1] in (SPELLS_ADD, SPELLS_REMOVE):
            names = unpack_equipment(data[2:])
            if not names:
                if self.spells is None and data[1] == SPELLS_ADD:
                    self.spells, self.dirty = [], True
                    return "knows no spells"
                return None
            known = {name.lower(): name for name in (self.spells or [])}
            if data[1] == SPELLS_ADD:
                known.update((name.lower(), name) for name in names)
            else:
                for name in names:
                    known.pop(name.lower(), None)
            self.spells, self.dirty = list(known.values()), True
            return ("learned " if data[1] == SPELLS_ADD else "forgot ") + ", ".join(names)
        if kind == PLAYER_IDENTITY and len(data) > 3:
            part, parts = data[1], data[2]
            if part == 0:
                self.identity_parts = (b"", 0)
            if not self.identity_parts or self.identity_parts[1] != part or part >= parts:
                self.identity_parts = None
                return None
            body = self.identity_parts[0] + bytes(data[3:])
            self.identity_parts = (body, part + 1)
            if part + 1 < parts:
                return None
            self.identity_parts = None
            try:
                identity = unpack_player_identity(body)
            except ValueError:
                return None
            if identity == self.identity:
                return None
            self.identity, self.dirty = identity, True
            return describe_identity(identity)
        if kind == PLAYER_WORN and len(data) >= 3:
            part, parts = data[1], data[2]
            if part == 0:
                self.worn_parts = (b"", 0)
            if not self.worn_parts or self.worn_parts[1] != part or part >= parts:
                self.worn_parts = None
                return None
            body = self.worn_parts[0] + bytes(data[3:])
            self.worn_parts = (body, part + 1)
            if part + 1 < parts:
                return None
            self.worn_parts = None
            try:
                worn = unpack_worn(body)
            except ValueError:
                return None
            if worn == self.worn:
                return None
            self.worn, self.dirty = worn, True
            return "wears " + (", ".join(item for item, *_ in worn) or "nothing")
        return None

    def replay(self):
        """The kept state as events, in the order a console applies them."""
        events = pack_player_identity(self.identity) if self.identity else []
        events += [part for item, entries in sorted(self.items.items())
                   for part in pack_items(item, entries)]
        if self.worn is not None:
            events += pack_worn(self.worn)
        # Abilities change statistic bases; restore them before the absolute stat snapshots.
        if self.spells is not None:
            events += [bytes([PLAYER_SPELLS, SPELLS_SNAPSHOT]) + part
                       for part in pack_equipment(self.spells)]
        if self.effects is not None:
            events += pack_player_effects(self.effects)
        if self.level:
            events.append(bytes([PLAYER_LEVEL]) + self.level)
        if self.vitals:  # after LEVEL, which caps each current value at its base
            events.append(bytes([PLAYER_VITALS]) + VITALS.pack(*self.vitals))
        if self.place and not self.dead:  # the dead go to a marker instead
            events.append(bytes([PLAYER_PLACE]) + self.place)
        if self.bounty is not None:
            events.append(bytes([PLAYER_BOUNTY]) + struct.pack("<i", self.bounty))
        skills = sorted(self.skills.items())
        per = (EVENT_DATA - 2) // SKILL.size
        for i in range(0, len(skills), per):
            chunk = skills[i:i + per]
            events.append(bytes([PLAYER_SKILLS, len(chunk)]) + b"".join(
                SKILL.pack(skill, *values) for skill, values in chunk))
        modifiers = sorted(self.modifiers.items())
        per = (EVENT_DATA - 2) // MODIFIER.size
        for i in range(0, len(modifiers), per):
            chunk = modifiers[i:i + per]
            events.append(bytes([PLAYER_MODIFIERS, len(chunk)]) + b"".join(
                MODIFIER.pack(stat, current) for stat, current in chunk))
        events += pack_journal([(q, i) for q, indices in sorted(self.journal.items())
                                for i in indices])
        return events

    def character_file(self):
        """What a New Game builds the player from before its first frame; None without an
        identity. The rest comes with the replay after the console joins."""
        if not self.identity:
            return None
        events = pack_player_identity(self.identity)
        events += [part for item, entries in sorted(self.items.items())
                   for part in pack_items(item, entries)]
        if self.worn is not None:
            events += pack_worn(self.worn)
        if self.place and not self.dead:
            events.append(bytes([PLAYER_PLACE]) + self.place)
        return CHARACTER_MAGIC + b"".join(struct.pack("<H", len(e)) + e for e in events)

    def save(self):
        save_world(self.path, {"items": self.items,
                               "level": self.level.hex() if self.level else None,
                               "skills": {str(k): v for k, v in self.skills.items()},
                               "modifiers": {str(k): v for k, v in self.modifiers.items()},
                               "journal": self.journal, "vitals": self.vitals,
                               "place": self.place.hex() if self.place else None,
                               "spells": self.spells,
                               "effects": self.effects,
                               "bounty": self.bounty,
                               "identity": self.identity,
                               "worn": self.worn,
                               "dead": self.dead})
        self.dirty = False


class Incoming:
    """One file a console sends: chunks written in order to NAME.part in its folder, renamed to
    NAME once the whole file's BLAKE2b matches the offer. A part left by an earlier try is
    resumed; without a folder the offer is refused."""

    def __init__(self, folder, ident, size, digest, name, now):
        self.id, self.size, self.hash, self.name = ident, size, digest, name
        self.chunks = (size + BULK_CHUNK - 1) // BULK_CHUNK
        self.next = self.first = self.arrived = 0
        self.held, self.stream = {}, None
        self.started, self.acked = now, 0.0
        self.path = os.path.join(folder, name) if folder else None
        self.status = BULK_REFUSED
        if not folder or size > BULK_MAX or not plain_name(name) or name[-1] in " ." or \
                name.split(".")[0].upper() in DEVICE_NAMES:
            return
        os.makedirs(folder, exist_ok=True)
        if os.path.exists(self.path) and self.matches(self.path):
            self.status, self.next = BULK_DONE, self.chunks
            return
        kept = [f for f in os.listdir(folder) if not f.endswith(".part") and f != name]
        if len(kept) >= UPLOAD_FILES:
            return
        part = self.path + ".part"
        have = os.path.getsize(part) if os.path.exists(part) else 0
        self.next = min(have, size) // BULK_CHUNK
        self.stream = open(part, "r+b" if have else "wb")
        self.stream.truncate(self.next * BULK_CHUNK)
        self.stream.seek(self.next * BULK_CHUNK)
        self.first = self.next
        self.status = BULK_RECEIVING
        if self.next == self.chunks:
            self.finish()

    def matches(self, path):
        digest = hashlib.blake2b(digest_size=32)
        with open(path, "rb") as stream:
            for block in iter(lambda: stream.read(1 << 20), b""):
                digest.update(block)
        return os.path.getsize(path) == self.size and digest.digest() == self.hash

    def finish(self):
        self.stream.close()
        self.stream = None
        part = self.path + ".part"
        if self.matches(part):
            os.replace(part, self.path)
            self.status = BULK_DONE
        else:
            os.remove(part)
            self.status, self.next = BULK_BAD_HASH, 0

    def on_chunk(self, index, data):
        """Take a chunk; true when it should be acked now: out of the window, a duplicate, the
        end, or every fourth arrival."""
        if self.status != BULK_RECEIVING or not self.next <= index < min(
                self.next + BULK_WINDOW_IN, self.chunks) or index in self.held or len(data) != (
                BULK_CHUNK if index + 1 < self.chunks else self.size - index * BULK_CHUNK):
            return True
        self.held[index] = data
        self.arrived += 1
        while self.next in self.held:
            self.stream.write(self.held.pop(self.next))
            self.next += 1
        if self.next == self.chunks:
            self.finish()
            return True
        return self.arrived % 4 == 0

    def ack(self, now):
        self.acked = now
        bitmap = sum(1 << (i - self.next) for i in self.held if i - self.next < 32)
        return BULK_ACK_BODY.pack(self.id, self.next, bitmap,
                                  BULK_WINDOW_IN if self.status == BULK_RECEIVING else 0,
                                  self.status)


class Outgoing:
    """One file sent to one client: chunks inside the window its last ack allows, each resent
    after BULK_RESEND until acked, or at once when a chunk sent after it has arrived."""

    def __init__(self, name, data):
        self.name, self.data = name, data
        self.hash = hashlib.blake2b(data, digest_size=32).digest()
        self.id = int.from_bytes(self.hash[:4], "little") or 1
        self.chunks = (len(data) + BULK_CHUNK - 1) // BULK_CHUNK
        self.next = None  # unknown until the first ack
        self.seen, self.sent_at = set(), {}
        self.serial, self.sends, self.lost = {}, 0, set()  # each chunk's latest send, in order
        self.window = self.status = self.sent = self.resent = self.probes = self.fast = 0
        self.first = self.started = None
        self.acked = 0.0  # when the last ack arrived

    def offer(self):
        return BULK_OFFER.pack(self.id, len(self.data), self.hash) + zstr(self.name)

    def on_ack(self, body, now):
        """Take an ack; returns the new status if it changed."""
        ident, nxt, bitmap, window, status = BULK_ACK_BODY.unpack_from(body)
        if ident != self.id:
            return None
        if self.first is None:
            self.first, self.started = nxt, now
        self.acked = now
        self.next, self.window = nxt, window
        self.seen = {nxt + k for k in range(32) if bitmap >> k & 1}
        self.sent_at = {i: t for i, t in self.sent_at.items() if i >= nxt}
        if self.seen:
            last = self.serial.get(max(self.seen), 0)
            self.lost |= {i for i in range(nxt, max(self.seen))
                          if i not in self.seen and 0 < self.serial.get(i, last) < last}
        self.lost = {i for i in self.lost if i >= nxt and i not in self.seen}
        changed = status != self.status
        self.status = status
        return status if changed else None

    def due(self, now):
        """The chunk indices to send now."""
        if self.next is None or self.status != BULK_RECEIVING:
            return []
        out = []
        for i in range(self.next, min(self.next + self.window, self.chunks)):
            sent = self.sent_at.get(i)
            if i in self.seen or (i not in self.lost and sent is not None and
                                  now - sent < BULK_RESEND):
                continue
            self.resent += sent is not None
            self.fast += i in self.lost
            self.lost.discard(i)
            self.sent += 1
            self.sends += 1
            self.sent_at[i], self.serial[i] = now, self.sends
            out.append(i)
        # Everything in flight arrived but the final ack did not: a repeated chunk draws another.
        if not out and now - self.acked >= BULK_PROBE:
            self.acked = now
            self.probes += 1
            out.append(min(self.next, max(self.chunks - 1, 0)))
        return out

    def chunk(self, index):
        return struct.pack("<II", self.id, index) + self.data[index * BULK_CHUNK:
                                                              (index + 1) * BULK_CHUNK]


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


class Clock:
    """The session's game time, run by the server: it advances at TimeScale game seconds per real
    second and rolls days, months and years over as the game does."""

    def __init__(self, hour, day, month, year, days_passed, scale, now):
        self.hour, self.day, self.month, self.year = hour, int(day), int(month), int(year)
        self.days_passed, self.scale, self.last = int(days_passed), scale, now

    def advance(self, now):
        self.hour += (now - self.last) * self.scale / 3600
        self.last = now
        while self.hour >= 24:
            self.hour -= 24
            self.days_passed += 1
            self.day += 1
            if self.day > MONTH_DAYS[self.month % 12]:
                self.day = 1
                self.month += 1
                if self.month > 11:
                    self.month = 0
                    self.year += 1

    def body(self, now):
        self.advance(now)
        return CLOCK_BODY.pack(self.hour, self.day, self.month, self.year, self.days_passed,
                               self.scale)

    def __str__(self):
        return (f"{int(self.hour):02d}:{int(self.hour % 1 * 60):02d} day {self.day} month "
                f"{self.month} year {self.year} (days passed {self.days_passed}, "
                f"timescale {self.scale:g})")


class Bucket:
    """A token bucket: take() is false once more than burst arrive faster than rate per second."""

    def __init__(self, burst, rate, now=None):
        self.burst, self.rate = burst, rate
        self.tokens, self.last = float(burst), time.monotonic() if now is None else now

    def take(self, now):
        self.tokens = min(self.burst, self.tokens + (now - self.last) * self.rate)
        self.last = now
        if self.tokens < 1:
            return False
        self.tokens -= 1
        return True

    def available(self, now):
        """Whether take() would succeed now, without spending a token."""
        return min(self.burst, self.tokens + (now - self.last) * self.rate) >= 1


class SourceBuckets:
    """Bounded source limits; evict only buckets whose entire burst has recovered."""

    def __init__(self, burst, rate, limit=HANDSHAKES_PENDING):
        self.burst, self.rate, self.limit = burst, rate, limit
        self.buckets = {}

    @staticmethod
    def source(host):
        address = ipaddress.ip_address(host.split('%', 1)[0])
        if isinstance(address, ipaddress.IPv6Address):
            if address.ipv4_mapped:
                return str(address.ipv4_mapped)
            return str(ipaddress.ip_network((address, 64), strict=False))
        return str(address)

    def get(self, host, now):
        source = self.source(host)
        if source in self.buckets:
            return self.buckets[source]
        if len(self.buckets) >= self.limit:
            recovered = next((key for key, bucket in self.buckets.items()
                              if bucket.tokens + (now - bucket.last) * bucket.rate
                              >= bucket.burst), None)
            if recovered is None:
                return None
            del self.buckets[recovered]
        bucket = self.buckets[source] = Bucket(self.burst, self.rate, now)
        return bucket


class Client:
    def __init__(self, ident, mac):
        self.id, self.mac = ident, mac
        self.session = self.seq = self.peer_seq = self.peer_time = 0
        self.addr = None
        self.joins = self.beats = self.gaps = self.states = 0
        self.state = None
        self.last = time.monotonic()
        self.alive = False
        self.version = T3MP_VERSION
        self.dead = False  # its player died and has not respawned; relayed to peers
        self.rel = Reliable()
        self.events = 0
        self.known = {}  # cell -> the authority this client was told
        self.owners_told = {}  # actor id -> the owner this client was told, where not 0
        self.loaded = set()
        self.busy = None  # since when it has been saving
        self.lobby = False  # joined from the main menu, with no game
        self.game = None  # the launch token it last reported
        self.synced = False  # that launch runs its character's latest checkpoint
        self.launch = 0  # GAME_NONE, GAME_LOAD or GAME_NEW
        self.character = None  # the folder of the character that launch runs
        self.announced = False  # the others were told it joined
        self.announce_due = False  # that waits for its character's name
        self.relaunching = False  # it was sent a character to load and is about to relaunch
        self.rebuild = None  # (checkpoint, when to ask for the save) under --rebuild
        self.naming = False
        self.snapshots = 0
        self.snapshot_request = 0x80000000
        self.snapshot_saved = None
        self.place_hold = None  # (replayed place, until when) while STATE still shows the old one
        self.listed = []  # the folders CHARS offered, in order
        self.actor_states = 0
        self.flush_due = False  # an EVENTS packet held back by EVENTS_GAP
        self.queue = []  # (address, packet, seq) held back by PACE_PACKETS
        self.window = (0.0, 0)  # the current PACE_WINDOW's start and packets sent in it
        self.joined = 0.0
        self.bursts = []  # (seconds after joining, packets) still to send
        self.bulk = None  # the Outgoing file of --send, offered again on each join
        self.upload = None  # the Incoming file this console is sending
        self.kept = 0  # saves kept from it as its character
        self.key = None  # the console's static key for this server: its identity
        self.keys = None  # (console to server, server to console) from the handshake
        self.replay = (0, 0)  # the highest seq opened and a bitmap of the 32 up to it
        self.forged = self.replayed = self.limited = 0
        self.bucket = Bucket(*CLIENT_RATE)

    @property
    def in_world(self):
        """Joined with a game loaded: it gets the world, the others' states and events."""
        return self.alive and not self.lobby


def load_server_key(args):
    """The server's static secret: from --key, or server.key in the world folder, made on first
    use; without either, a new one each run."""
    crypto()
    path = args.key or (os.path.join(args.world, "server.key") if args.world else None)
    if path and os.path.exists(path):
        with open(path, encoding="ascii") as stream:
            secret = bytes.fromhex(stream.read().strip())
    else:
        secret = os.urandom(32)
        if path:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            with open(path, "w", encoding="ascii") as stream:
                stream.write(secret.hex() + "\n")
    print(f"server key {fingerprint(x25519_public(secret))}"
          + ("" if path else " (not kept: give --key or --world)"), flush=True)
    return secret


def load_password(args):
    """The first line of --password-file, or None for an open server."""
    if not args.password_file:
        return None
    with open(args.password_file, encoding="utf-8") as stream:
        password = stream.readline().strip()
    # The console reads NetPassword from its ini, which trims spaces and has no Unicode.
    if not 0 < len(password) <= PASSWORD_MAX or not password.isascii() or \
            not password.isprintable():
        sys.exit(f"{args.password_file}: the password must be 1 to {PASSWORD_MAX} printable "
                 "ASCII characters")
    return password.encode("ascii")


def ban_value(kind, value):
    """A ban's value in the form the server compares, or None: a key's fingerprint, a MAC, an
    IPv4 address."""
    value = value.lower()
    if kind == "key":
        ok = len(value) == 32 and all(c in "0123456789abcdef" for c in value)
    elif kind == "mac":
        parts = value.split(":")
        ok = len(parts) == 6 and all(len(x) == 2 and all(c in "0123456789abcdef" for c in x)
                                     for x in parts)
    elif kind == "address":
        try:
            ok = socket.inet_ntoa(socket.inet_aton(value)) == value
        except OSError:
            ok = False
    else:
        ok = False
    return value if ok else None


def load_bans(path):
    """{kind: set of values} from bans.txt: one "KIND VALUE" per line, a note may follow."""
    bans = {kind: set() for kind in BAN_KINDS}
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as stream:
            for line in stream:
                words = line.split()
                if len(words) >= 2 and ban_value(words[0], words[1]):
                    bans[words[0]].add(ban_value(words[0], words[1]))
    return bans


def save_bans(path, bans):
    if path:
        with open(path + ".tmp", "w", encoding="utf-8") as stream:
            for kind in BAN_KINDS:
                for value in sorted(bans[kind]):
                    stream.write(f"{kind} {value}\n")
        os.replace(path + ".tmp", path)


def load_admitted(path):
    """Console keys that have given the password, one hex key per line with a note after it."""
    admitted = set()
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as stream:
            for line in stream:
                if line.split():
                    admitted.add(bytes.fromhex(line.split()[0]))
    return admitted


class Bot:
    """A scripted player for tests (--bot and the --bot-* options). It circles where the
    first client entered the world and acts on a schedule counted from when it first
    placed itself."""

    def __init__(self, server, now):
        self.server, self.args = server, server.args
        self.start = now
        self.anchor = None  # ((flags, cell), x, y, z) of the client it circles
        self.anchored = None  # when it first placed itself; the schedule's zero
        self.state = None
        self.next_step = self.said = 0.0
        self.line = self.held = 0
        self.breaks = []  # (due, holder, refid) of holds it breaks
        self.hit = self.killed = self.hit_player = self.busy = self.dead = False
        self.mirror = self.echo = None
        self.actor_next = 0.0
        if self.args.bot:
            self.server.identities[BOT_ID] = pack_identity("Bot", "Imperial",
                                                           "b_n_imperial_m_head_01",
                                                           "b_n_imperial_m_hair_01")
        if self.args.bot_equip is not None:
            self.server.equipment[BOT_ID] = [
                pack_equipment([i for i in self.args.bot_equip.split(",") if i]), []]
        self.boxes = []
        for spec in self.args.bot_contents:
            what, _, at = spec.rpartition("@")
            refid, cell, items = what.split(":", 2)
            entries = []
            for item in items.split(","):
                name, count, *condition = item.split("*")
                entries.append([name, int(count), ENTRY_DATA if condition else 0,
                                int(condition[0]) if condition else 0, 0])
            self.boxes.append((float(at), int(refid, 16), int(cell), entries))
        self.spawns = []
        for spec in self.args.bot_spawn:
            what, _, at = spec.rpartition("@")
            name, cell, *condition = what.split(":")
            self.spawns.append((float(at), name, int(cell),
                                int(condition[0]) if condition else None))
        self.takes = [float(at) for at in self.args.bot_take]
        self.fights = {int(refid, 16): int(client) for refid, _, client in
                       (spec.partition(":") for spec in self.args.bot_fights)}
        self.weather = []
        self.statuses = list(self.args.bot_status)
        self.affects = list(self.args.bot_affect)
        self.spells = []
        self.bounties = [(float(at), int(value)) for value, _, at in
                         (s.rpartition("@") for s in self.args.bot_bounty)]
        self.shots = [(float(at), ammo)
                      for ammo, _, at in (s.rpartition("@") for s in self.args.bot_shoot)]
        for kind, specs in ((EVENT_SPELL, self.args.bot_spell), (EVENT_CAST, self.args.bot_cast)):
            for spec in specs:
                cast, _, at = spec.partition("@")
                name, _, refid = cast.partition(":")
                target = None if refid == "none" else int(refid, 16) if refid else 0
                self.spells.append((float(at), kind, name, target))
        for spec in self.args.bot_weather:
            change, _, at = spec.partition("@")
            index, _, value = change.partition(":")
            self.weather.append((float(at), int(index), int(value)))

    def window(self, spec, now):
        """Whether now falls in START:END seconds after the bot first placed itself."""
        if not spec or self.anchored is None:
            return False
        start, _, end = spec.partition(":")
        t = now - self.anchored
        return float(start) <= t < (float(end) if end else math.inf)

    def follow(self, state):
        """The bot circles where the first client entered the world, and follows it to a new
        cell or across a long jump."""
        flags, x, y, z, _, cell = STATE_BODY.unpack_from(state)
        flags &= PLACE
        anchor = self.anchor
        if flags & IN_WORLD and (anchor is None or anchor[0] != (flags, cell)
                                 or math.hypot(x - anchor[1], y - anchor[2]) > 2048):
            self.anchor = ((flags, cell), x, y, z)
            if self.anchored is None:
                self.anchored = time.monotonic()
            print(f"{time.strftime('%H:%M:%S')} bot circles {describe_state(state)}",
                  flush=True)

    def step(self, now):
        (flags, cell), cx, cy, cz = self.anchor
        if self.args.bot_at:
            dx, dy = (float(v) for v in self.args.bot_at.split(","))
            cx, cy = cx + dx, cy + dy
        t = (now - self.start) * 2 * math.pi / self.args.bot_period
        if self.mirror:
            _, x, y, z, heading, _, actor_flags, _, _, _, anim = ACTOR.unpack(self.mirror)
            state = STATE_BODY.pack(flags | actor_flags & STANCE, x + self.args.bot_shift, y, z,
                                    heading, cell) + anim
        elif self.echo:
            echo_flags, x, y, z, heading, echo_cell = STATE_BODY.unpack_from(self.echo)
            state = STATE_BODY.pack(echo_flags, x + self.args.bot_shift, y, z, heading,
                                    echo_cell) + self.echo[STATE_BODY.size:]
        else:
            state = STATE_BODY.pack(flags, cx + self.args.bot_radius * math.cos(t),
                                    cy + self.args.bot_radius * math.sin(t), cz, -t % (2 * math.pi),
                                    cell) + NO_ANIM
        self.state = state
        for other in self.server.clients.values():
            if other.in_world:
                self.server.send(other, PEER, struct.pack("<I", BOT_ID) + state)

    def move_actors(self, now):
        """As the authority, the bot places each actor of its cells bot_shift units east of where
        the last authority left it, swaying east and west by bot_sway once per bot_period."""
        server = self.server
        owned = [refid for refid, (_, key, _) in server.actors.items()
                 if server.actor_owners.get(refid, (server.owners.get(key),))[0] == BOT_ID]
        for refid in owned:
            server.actor_seen[refid] = now  # a client's states of it have stopped
        owned = [server.actors[refid][2] for refid in owned]
        phase = (now - self.start) * 2 * math.pi / self.args.bot_period
        sway = self.args.bot_sway * math.sin(phase)
        facing = math.pi / 2 if math.cos(phase) >= 0 else 3 * math.pi / 2
        for i in range(0, len(owned), ACTORS_PER_PACKET):
            chunk = owned[i:i + ACTORS_PER_PACKET]
            body = struct.pack("<I", len(chunk))
            for record in chunk:
                refid, x, y, z, heading, health, flags, magicka, fatigue, target, anim = \
                    ACTOR.unpack(record)
                if refid in self.fights:
                    flags, target = flags | ACTOR_IN_COMBAT, self.fights[refid]
                    server.actors[refid] = (BOT_ID, server.actors[refid][1], ACTOR.pack(
                        refid, x, y, z, heading, health, flags, magicka, fatigue, target, anim))
                if self.args.bot_sway:
                    heading = facing
                if self.args.bot_stats:
                    health, magicka, fatigue = (float(v) for v in self.args.bot_stats.split(","))
                body += ACTOR.pack(refid, x + self.args.bot_shift + sway, y, z, heading, health,
                                   flags, magicka, fatigue, target, anim)
            for other in server.clients.values():
                if other.in_world:
                    server.send(other, ACTORS, struct.pack("<I", BOT_ID) + body)

    def move(self, now):
        """The schedule's bounty, busy and death changes, then a step; before authority."""
        for spec in [b for b in self.bounties if self.args.bot and self.window(f"{b[0]}:", now)]:
            self.bounties.remove(spec)
            print(f"{time.strftime('%H:%M:%S')} bot bounty {spec[1]}", flush=True)
            self.server.broadcast_event(BOT_ID, EVENT_BOUNTY, struct.pack("<i", spec[1]), now)
        if self.args.bot and self.anchor and self.window(self.args.bot_busy, now) != self.busy:
            self.busy = not self.busy
            print(f"{time.strftime('%H:%M:%S')} bot {'saves' if self.busy else 'is back'}",
                  flush=True)
            self.server.broadcast_event(BOT_ID, EVENT_BUSY,
                                        bytes([BUSY_SAVING if self.busy else 0]), now)
        if self.args.bot and self.anchor and self.window(self.args.bot_dead, now) != self.dead:
            self.dead = not self.dead
            print(f"{time.strftime('%H:%M:%S')} bot {'dies' if self.dead else 'respawns'}",
                  flush=True)
            self.server.broadcast_event(BOT_ID, EVENT_PLAYER,
                                        bytes([PLAYER_DEATH if self.dead else PLAYER_ALIVE]),
                                        now)
        if self.args.bot and self.anchor and now >= self.next_step and not self.busy:
            self.next_step = now + 1 / self.args.bot_rate
            self.step(now)

    def act(self, now):
        """Its actors, then the rest of the schedule; after authority."""
        if self.args.bot and now >= self.actor_next:
            self.actor_next = now + ACTOR_PERIOD
            self.move_actors(now)
        for due, holder, refid in [b for b in self.breaks if now >= b[0]]:
            self.breaks.remove((due, holder, refid))
            print(f"{time.strftime('%H:%M:%S')} bot breaks client {holder}'s hold on "
                  f"{refid:#010x}", flush=True)
            self.server.send_event(holder, BOT_ID, EVENT_HOLD_BROKEN,
                                   struct.pack("<III", refid, holder, 2),
                                   now)
        if self.args.bot_hold:
            refid, _, span = self.args.bot_hold.partition("@")
            refid = int(refid, 16)
            want = self.window(span, now)
            owner = self.server.actor_authority(refid)
            if want != self.held and owner:
                self.held = want
                print(f"{time.strftime('%H:%M:%S')} bot {'holds' if want else 'releases'} "
                      f"{refid:#010x} (authority {owner})", flush=True)
                self.server.send_event(owner, BOT_ID, EVENT_HOLD,
                                       struct.pack("<III", refid, owner, want), now)
        if self.args.bot_kill and not self.killed:
            refid, _, at = self.args.bot_kill.partition("@")
            refid = int(refid, 16)
            if self.window(at, now):
                self.killed = True
                self.server.world.deaths[refid] = BOT_ID
                print(f"{time.strftime('%H:%M:%S')} bot kills {refid:#010x}", flush=True)
                self.server.broadcast_event(BOT_ID, EVENT_DEATH, struct.pack("<I", refid), now)
        for spec in [b for b in self.statuses if self.window(b.partition("@")[2], now)]:
            self.statuses.remove(spec)
            refid, _, values = spec.partition("@")[0].partition(":")
            refid, values = int(refid, 16), tuple(int(v) for v in values.split(","))
            self.server.world.statuses[refid] = values
            print(f"{time.strftime('%H:%M:%S')} bot sets {describe_status(refid, values)}",
                  flush=True)
            self.server.broadcast_event(BOT_ID, EVENT_STATUS, STATUS.pack(refid, *values), now)
        for spec in [b for b in self.affects if self.window(b.rpartition("@")[2], now)]:
            self.affects.remove(spec)
            refid, index, name = spec.rpartition("@")[0].split(":", 2)
            print(f"{time.strftime('%H:%M:%S')} bot gives {refid} effect {index} of {name}",
                  flush=True)
            self.server.broadcast_event(BOT_ID, EVENT_AFFECT,
                                        struct.pack("<IB", int(refid, 16), int(index))
                                        + name.encode("latin-1") + b"\0", now)
        if self.args.bot_hit and not self.hit:
            refid, _, at = self.args.bot_hit.partition("@")
            refid = int(refid, 16)
            owner = self.server.actor_authority(refid)
            if owner and self.window(at, now):
                self.hit = True
                print(f"{time.strftime('%H:%M:%S')} bot hits {refid:#010x} for 5 (authority "
                      f"{owner})", flush=True)
                self.server.send_event(owner, BOT_ID, EVENT_HIT,
                                       struct.pack("<IIf", refid, owner, 5.0), now)
        for due, index, value in [w for w in self.weather if self.window(f"{w[0]}:", now)]:
            self.weather.remove((due, index, value))
            self.server.set_weather(BOT_ID, {index: value}, time.strftime("%H:%M:%S"), now)
        if self.args.bot_hit_player and not self.hit_player:
            damage, _, at = self.args.bot_hit_player.partition("@")
            damage, _, fatigue = damage.partition(":")
            if self.window(at, now):
                self.hit_player = True
                for other in [c for c in self.server.clients.values() if c.alive]:
                    print(f"{time.strftime('%H:%M:%S')} bot hits client {other.id} for {damage}"
                          f" health, {fatigue or 0} fatigue", flush=True)
                    self.server.send_event(other.id, BOT_ID, EVENT_PLAYER_HIT,
                                           struct.pack("<IIff", 0, other.id, float(damage),
                                                float(fatigue or 0)), now)
        for spell in [s for s in self.spells if self.window(f"{s[0]}:", now)]:
            _, kind, name, refid = spell
            if refid:
                target = self.server.actor_authority(refid)
            else:
                target = next((c.id for c in self.server.clients.values() if c.alive), None)
            if not target or target == BOT_ID:
                continue
            self.spells.remove(spell)
            verb = "casts" if kind == EVENT_SPELL else "is seen casting"
            if refid is None:
                print(f"{time.strftime('%H:%M:%S')} bot {verb} {name} at nothing", flush=True)
                self.server.broadcast_event(BOT_ID, kind,
                                            SPELL.pack(0, 0, 0, SOURCE_SPELL, 1) + zstr(name),
                                            now)
                continue
            on = f"{refid:#010x}" if refid else "the player"
            print(f"{time.strftime('%H:%M:%S')} bot {verb} {name} on {on} of client {target}",
                  flush=True)
            data = SPELL.pack(0, target, refid, SOURCE_SPELL, 1) + zstr(name)
            if kind == EVENT_SPELL:
                self.server.send_event(target, BOT_ID, kind, data, now)
            else:
                self.server.broadcast_event(BOT_ID, kind, data, now)
        for spec in [s for s in self.spawns if self.window(f"{s[0]}:", now)]:
            self.spawns.remove(spec)
            _, x, y, z = self.anchor
            self.server.add_spawn(BOT_ID, {"cell": spec[2], "count": 1, "pos": [x + 64, y, z],
                                           "rot": [0.0, 0.0, 0.0], "id": spec[1],
                                           "data": spec[3] is not None, "condition": spec[3] or 0,
                                           "charge": 0},
                                  time.strftime("%H:%M:%S"), now)
        for box in [b for b in self.boxes if self.window(f"{b[0]}:", now)]:
            self.boxes.remove(box)
            self.server.set_contents(BOT_ID, box[1], box[2], box[3], False,
                                     time.strftime("%H:%M:%S"),
                                     now)
        for at in [t for t in self.takes if self.window(f"{t}:", now)]:
            self.takes.remove(at)
            for sid in [s for s, v in self.server.world.spawns.items() if v["origin"] != BOT_ID]:
                self.server.remove_spawn(BOT_ID, sid, time.strftime("%H:%M:%S"), now)
        for shot in [s for s in self.shots if self.window(f"{s[0]}:", now)]:
            self.shots.remove(shot)
            print(f"{time.strftime('%H:%M:%S')} bot shoots {shot[1]}", flush=True)
            self.server.broadcast_event(BOT_ID, EVENT_SHOT,
                                        SHOT.pack(0, 1.0, 0.0, 1) + zstr(shot[1]),
                                        now)
        if self.args.bot_say and self.anchor and \
                now >= self.said + self.args.bot_say:
            self.said = now
            self.line += 1
            self.server.broadcast_event(BOT_ID, EVENT_TEXT, b"bot %d" % self.line, now)


class Server:
    """A session server: welcomes consoles by their key, answers each heartbeat at once and relays
    each client's state to the others. Each --tunnel also serves an xemu guest."""

    def __init__(self, args):
        self.args = args

    def dropped(self, direction):
        if self.args.drop and self.loss.random() < self.args.drop:
            self.lost[direction] += 1
            return True
        return False

    def send(self, client, kind, body=b""):
        """Seal and queue a packet; nothing goes out before the handshake has keyed the client."""
        client.seq += 1
        if client.keys is None:
            return
        inner = INNER.pack(kind, client.peer_seq, now_us(), client.peer_time) + body
        outer = OUTER.pack(b"T3MP", client.version, SEALED, 0, client.session, client.seq)
        packet = outer + seal(client.keys[1], client.seq, outer, inner)
        if self.dropped("out"):
            return
        client.queue.append((client.addr, packet, client.seq))
        self.pump(client)

    def transmit(self, addr, packet, ident=0):
        if len(addr) == 4:  # a tunnel guest, by its MAC
            ip, port, mac, link = addr
            link.send(udp_frame(mac, ip, packet, ident, sport=self.args.port, dport=port))
        else:
            self.sock.sendto(packet, addr)

    def pump(self, client):
        """Send what PACE_PACKETS allows of the client's queue."""
        now = time.monotonic()
        start, count = client.window
        if now - start >= PACE_WINDOW:
            start, count = now, 0
        while client.queue and count < PACE_PACKETS:
            addr, packet, seq = client.queue.pop(0)
            self.transmit(addr, packet, seq)
            count += 1
        client.window = (start, count)

    def adopt_world(self, order, now):
        """Load what --world holds for this load order: the clock, deaths, objects, weather."""
        if not self.args.world:
            return
        path = os.path.join(self.args.world, f"{order:08x}.json")
        if not self.world.load(path, now, clock=self.args.hour is None):
            print(f"world {path}: new", flush=True)
            return
        print(f"world {path}: {self.world}", flush=True)

    def notify(self, text, now, only=None, skip=None):
        """Show text on the screen of every console in the world, or of one client."""
        data = text[:EVENT_DATA].encode("latin-1", "replace")
        for other in self.clients.values():
            if other.in_world and other is not skip and (only is None or other is only):
                other.rel.queue(EVENT_TEXT, 0, data)
                self.flush(other, now)

    def player_name(self, client):
        parts = self.identities.get(client.id)
        if parts and parts[0]:
            return unpack_identity(parts[0])[2]
        return client.character or f"Player {client.id}"

    def announce_join(self, client, now):
        """Tell the others once the character's name is known, or after a few seconds without."""
        client.relaunching = False
        if client.announced or client.lobby:
            return
        parts = self.identities.get(client.id)
        if not (parts and parts[1]) and now - client.joined < ANNOUNCE_WAIT:
            client.announce_due = True
            return
        client.announced, client.announce_due = True, False
        self.notify(f"{self.player_name(client)} has joined.", now, skip=client)
        if self.args.welcome:
            client.rel.queue(EVENT_WELCOME, 0,
                             self.args.welcome[:EVENT_DATA].encode("latin-1", "replace"))
            self.flush(client, now)

    def leave(self, client):
        # a relaunch to load a character is not a departure
        if client.in_world and client.announced and not client.relaunching:
            self.notify(f"{self.player_name(client)} has left.", time.monotonic(), skip=client)
            client.announced = False
        client.alive = False
        for refid, (holder, target) in list(self.dialogues.items()):
            if holder == client.id:
                del self.dialogues[refid]
                if target != holder:
                    self.send_event(target, holder, EVENT_HOLD,
                                    struct.pack("<III", refid, target, 0), time.monotonic())
        for other in self.clients.values():
            if other.alive:
                self.send(other, GONE, struct.pack("<I", client.id))

    def flush(self, client, now, resend=True):
        """Send the ack and the unacked events, or hold them until EVENTS_GAP has passed."""
        if now - client.rel.last_send < EVENTS_GAP:
            client.flush_due = True
            return
        client.flush_due = False
        self.send(client, EVENTS, client.rel.packet(now, resend or bool(client.rel.out)))

    def broadcast_event(self, origin, kind, data, now):
        for other in self.clients.values():
            if other.in_world and other.id != origin:
                other.rel.queue(kind, origin, data)
                self.flush(other, now)

    def send_event(self, target, origin, kind, data, now):
        for other in self.clients.values():
            if other.alive and other.id == target:
                other.rel.queue(kind, origin, data)
                self.flush(other, now)

    def set_weather(self, origin, entries, stamp, now, to_origin=True):
        """Record the regions whose weather changes and send them to every client."""
        changed = {i: w for i, w in entries.items() if self.world.weather.get(i) != w}
        if not changed:
            return
        self.world.weather.update(changed)
        self.world.dirty = True
        print(f"{stamp} weather from client {origin}: {describe_weather(changed)}", flush=True)
        for other in self.clients.values():
            if other.in_world and (to_origin or other.id != origin):
                for data in pack_weather(changed):
                    other.rel.queue(EVENT_WEATHER, origin, data)
                self.flush(other, now)

    def add_spawn(self, origin, spawn, stamp, now, token=0):
        """Name a reference made at run time and send it to every client, its maker too, which
        knows it as its own by cell, object and place. A repeat gets the id it already has, and
        goes to the maker only."""
        sid = spawn_twin(self.world.spawns, spawn, origin, token, now, self.world.deaths)
        if sid is not None:
            print(f"{stamp} client {origin} repeats {describe_spawn(sid, self.world.spawns[sid])}",
                  flush=True)
            self.send_event(origin, self.world.spawns[sid]["origin"], EVENT_SPAWN,
                            pack_spawn(sid, self.world.spawns[sid]),
                            now)
            return sid
        # a dead creature's placeholder rolled again
        for old, known in list(self.world.spawns.items()):
            if spawn.get("leveled") and known.get("leveled") == spawn["leveled"] and \
                    not known["removed"]:
                self.remove_spawn(origin, old, stamp, now, to_origin=True)
        sid = SPAWN_IDS | self.world.next_spawn
        self.world.next_spawn += 1
        self.world.spawns[sid] = dict(spawn, origin=origin, token=token, made=now, removed=False)
        self.world.dirty = True
        print(f"{stamp} client {origin} made {describe_spawn(sid, self.world.spawns[sid])}",
              flush=True)
        data = pack_spawn(sid, self.world.spawns[sid])
        for other in self.clients.values():
            if other.in_world:
                other.rel.queue(EVENT_SPAWN, origin, data)
                self.flush(other, now)
        return sid

    def remove_spawn(self, origin, sid, stamp, now, to_origin=False):
        spawn = self.world.spawns.get(sid)
        if spawn is None or spawn["removed"]:
            return
        spawn["removed"] = True
        self.world.dirty = True
        print(f"{stamp} client {origin} removed {describe_spawn(sid, spawn)}", flush=True)
        if to_origin:
            self.send_event(origin, 0, EVENT_SPAWN, pack_spawn(sid, spawn), now)
        self.broadcast_event(origin, EVENT_SPAWN, pack_spawn(sid, spawn), now)

    def send_contents(self, target, refid, now, flags=0):
        box = self.world.contents[refid]
        for other in self.clients.values():
            if other.alive and other.id == target:
                for part in pack_contents(refid, box["cell"], box["entries"], flags):
                    other.rel.queue(EVENT_CONTENTS, box["origin"], part)
                self.flush(other, now)

    def set_contents(self, origin, refid, cell, entries, rolled, stamp, now):
        """Keep a container's contents and send them to the other clients. A console's first
        reading of a container the server already holds gets the server's contents back."""
        known = self.world.contents.get(refid)
        if rolled and known:
            if known["entries"] != entries:
                print(f"{stamp} client {origin} opened {refid:#010x}: keeps "
                      f"{describe_contents(known['entries'])}", flush=True)
                self.send_contents(origin, refid, now)
            return
        self.world.contents[refid] = {"cell": cell, "entries": entries, "origin": origin}
        self.world.dirty = True
        print(f"{stamp} client {origin} {'opened' if rolled else 'changed'} {refid:#010x} in cell "
              f"{cell}: {describe_contents(entries)}", flush=True)
        for other in self.clients.values():
            if other.in_world and other.id != origin:
                self.send_contents(other.id, refid, now)

    def key_folder(self, client):
        return (os.path.join(self.args.world, "characters", fingerprint(client.key))
                if self.args.world and client.key else None)

    def manager_character(self, key):
        """The character this key plays on the server: the one in the world now, else the newest
        kept; what the console manager lists beside the server."""
        for other in self.clients.values():
            if other.alive and other.key == key and (
                    other.character or self.identities.get(other.id)):
                return self.player_name(other)
        root = (os.path.join(self.args.world, "characters", fingerprint(key))
                if self.args.world else None)
        kept = kept_characters(root, fixtures=self.args.adopt or self.args.rebuild is not None)
        if not kept:
            return ""
        try:
            return PlayerStream(os.path.join(root, kept[0][0], STREAM_NAME)).identity["name"]
        except (OSError, ValueError, TypeError, KeyError):
            return kept[0][0]

    def character_folder(self, client):
        root = self.key_folder(client)
        return os.path.join(root, client.character) if root and client.character else None

    def player_stream(self, client):
        folder = self.character_folder(client)
        if folder is None:
            return None
        if folder not in self.streams:
            self.streams[folder] = PlayerStream(os.path.join(folder, STREAM_NAME))
        return self.streams[folder]

    def keep_place(self, client, now):
        """The character's last place, from STATE, once a replayed place has been reached."""
        stream = self.player_stream(client) if client.synced else None
        body = client.state[:STATE_BODY.size]
        if stream is None or not STATE_BODY.unpack_from(body)[0] & IN_WORLD:
            return
        if client.place_hold:
            kept, until = client.place_hold
            if not same_place(kept, body) and now < until:
                return
            if not same_place(kept, body):
                print(f"{time.strftime('%H:%M:%S')} client {client.id} did not reach "
                      f"{describe_state(kept)}", flush=True)
            client.place_hold = None
        stream.keep_place(body)

    def respawn(self, client, delay, now):
        """Tell a dead player's console when and where to come back, and what it loses."""
        stream = self.player_stream(client) if client.synced else None
        gold = sum(e[0] for item, entries in stream.items.items() if item.lower() == "gold_001"
                   for e in entries) if stream else 0
        lost = gold * self.args.death_gold // 100
        client.rel.queue(EVENT_PLAYER, 0, bytes([PLAYER_RESPAWN]) + RESPAWN.pack(
            int(delay * 1000), RESPAWN_PLACES[self.args.respawn], lost))
        self.flush(client, now)
        return lost

    def on_player_death(self, client, alive, stamp, now):
        stream = self.player_stream(client) if client.synced else None
        name = self.player_name(client)
        client.dead = not alive
        if stream:
            stream.dead, stream.dirty = not alive, True
        life = bytes([PLAYER_ALIVE if alive else PLAYER_DEATH])
        for other in self.clients.values():
            if other.in_world and other is not client:
                other.rel.queue(EVENT_PLAYER, client.id, life)
        if alive:
            print(f"{stamp} client {client.id} ({name}) is back", flush=True)
            for other in self.clients.values():
                if other.in_world and other is not client:
                    self.flush(other, now)
            return
        lost = self.respawn(client, self.args.respawn_delay, now)
        print(f"{stamp} client {client.id} ({name}) died: respawns at the {self.args.respawn} "
              f"marker "
              f"in {self.args.respawn_delay:g} s, loses {lost} gold", flush=True)
        notice = f"{name} has died."[:EVENT_DATA].encode("latin-1", "replace")
        for other in self.clients.values():
            if other.in_world and other is not client:
                other.rel.queue(EVENT_TEXT, 0, notice)
                self.flush(other, now)

    def player_ready(self, client, replay, stamp, now):
        """Replay retained state, or have the console publish its supported fields."""
        self.announce_join(client, now)
        if replay:
            for sid, spawn in list(self.world.spawns.items()):
                if spawn.get("summon") and spawn["origin"] == client.id and not spawn["removed"]:
                    self.remove_spawn(client.id, sid, stamp, now, to_origin=True)
        stream = self.player_stream(client)
        if stream is None:
            return
        events = stream.replay() if replay else []
        client.place_hold = (stream.place, now + PLACE_HOLD) if (
            replay and stream.place and not stream.dead) else None
        for data in events:
            client.rel.queue(EVENT_PLAYER, 0, data)
        if replay and stream.dead:
            print(f"{stamp} client {client.id} died before its last stop: respawns now",
                  flush=True)
            self.respawn(client, 0, now)
        client.rel.queue(EVENT_PLAYER, 0, bytes([PLAYER_READY, 0]))
        self.flush(client, now)
        print(f"{stamp} client {client.id}: " + (
            "replayed " + (f"{stream.identity['name']}'s identity, " if stream.identity else "")
            + f"{len(stream.items)} items, {len(stream.skills)} skills, "
            f"{len(stream.journal)} quests" + (", the level" if stream.level else "")
            + (f", the place ({describe_state(stream.place)})" if stream.place else "")
            if replay else "streams its player from scratch"), flush=True)

    def send_names(self, client, kind, names, now):
        for part in pack_names(names):
            client.rel.queue(kind, 0, part)
        self.flush(client, now)

    def offer_starts(self, client, stamp, now):
        """Have the console make a character: in this launch if it is a New Game, else it
        relaunches into one and is offered the start points again."""
        self.creating.add(fingerprint(client.key))
        client.synced = client.launch == GAME_NEW
        self.send_names(client, EVENT_NEWCHAR, [name for name, _ in self.starts], now)
        print(f"{stamp} client {client.id} makes a new character"
              + ("" if client.synced else ", after a New Game"), flush=True)

    def send_character(self, client, folder, path, loaded, stamp, now):
        """The character seed for a New Game; a missing identity cannot load a character.
        Keep its name in the folder to recognise the next launch after a restart."""
        if folder not in self.streams:
            self.streams[folder] = PlayerStream(os.path.join(folder, STREAM_NAME))
        data = self.streams[folder].character_file()
        if data is None:
            client.rel.queue(EVENT_TEXT, 0, b"This character has no retained identity.")
            self.flush(client, now)
            return
        name = checkpoint_name(data, CHARACTER_FILE)
        for f in os.listdir(folder):
            if f.lower().endswith(".t3c") and f != name:
                os.remove(os.path.join(folder, f))
        with open(os.path.join(folder, name), "wb") as stream:
            stream.write(data)
        client.bulk = Outgoing(name, data)
        client.rel.queue(EVENT_OFFER, 0, client.bulk.offer())
        client.rel.queue(EVENT_LOAD, 0, zstr(name))
        client.relaunching = True
        self.flush(client, now)
        print(f"{stamp} client {client.id} loaded {loaded or 'no save'}: sending "
              f"{os.path.basename(folder)}'s state as {name} ({len(data)} bytes) to start from",
              flush=True)

    def on_game(self, client, token, loaded, launch, stamp, now):
        """A console's launch: it runs one of its key's characters, or chooses one, or makes
        one."""
        if token != client.game:
            client.game, client.synced, client.character = token, False, None
            client.launch, client.rebuild = launch, None
        making = client.key and fingerprint(client.key) in self.creating
        if client.synced:  # a rejoin of the same launch: events in flight were dropped
            if client.character:
                self.player_ready(client, False, stamp, now)
            elif making:
                self.offer_starts(client, stamp, now)
            return
        kept = kept_characters(self.key_folder(client),
                               fixtures=self.args.adopt or self.args.rebuild is not None)
        for folder, path in kept:
            if loaded.lower().endswith(".t3c") and os.path.exists(
                    os.path.join(self.key_folder(client), folder, loaded.lower())):
                client.synced, client.character = True, folder
                self.creating.discard(fingerprint(client.key))
                if self.args.rebuild is not None:
                    client.rebuild = (path, now + self.args.rebuild)
                print(f"{stamp} client {client.id} runs {folder}, started from {loaded}",
                      flush=True)
                self.player_ready(client, True, stamp, now)
                return
            if self.args.rebuild is not None and path.lower().endswith(".ess"):
                with open(path, "rb") as stream:
                    matches = loaded.lower() == checkpoint_name(stream.read())
                if matches:
                    client.synced, client.character = True, folder
                    self.creating.discard(fingerprint(client.key))
                    print(f"{stamp} client {client.id} runs {folder} ({os.path.basename(path)})",
                          flush=True)
                    self.player_ready(client, True, stamp, now)
                    return
        if self.args.rebuild is not None and kept:
            client.character, path = kept[0]
            client.rebuild = (path, now + self.args.rebuild)
            print(f"{stamp} client {client.id} loaded {loaded or 'no save'}: rebuilding "
                  f"{client.character} over it from the kept state alone", flush=True)
            self.player_ready(client, True, stamp, now)
            return
        if making or (not kept and not self.args.adopt and self.key_folder(client)):
            self.offer_starts(client, stamp, now)
            return
        if not kept:
            client.synced = True
            print(f"{stamp} client {client.id} has no kept character", flush=True)
            return
        if self.args.adopt:
            self.send_character(client, os.path.join(self.key_folder(client), kept[0][0]),
                                kept[0][1],
                                loaded, stamp, now)
            return
        client.listed = [folder for folder, _ in kept[:CHARACTERS_LISTED]]
        self.send_names(client, EVENT_CHARS, client.listed, now)
        print(f"{stamp} client {client.id} loaded {loaded or 'no save'}: offered "
              f"{', '.join(client.listed)}", flush=True)

    def compare_rebuild(self, client, stamp):
        """Diff a rebuilt character's save against its checkpoint, by coverage row."""
        import contextlib
        import io
        from tes3x.ess import report_diff
        checkpoint, _ = client.rebuild
        out = client.upload.path[:-4] + ".diff.txt"
        text = io.StringIO()
        try:
            with contextlib.redirect_stdout(text):
                count = report_diff(checkpoint, client.upload.path, 1000)
        except ValueError as exc:
            print(f"{stamp} client {client.id} rebuilt {client.character}, but the saves do "
                  f"not compare: {exc}", flush=True)
            return
        with open(out, "w", encoding="utf-8") as f:
            f.write(text.getvalue())
        print(f"{stamp} client {client.id} rebuilt {client.character}: {count} differences "
              f"from {os.path.basename(checkpoint)}, in {out}", flush=True)

    def spymaster_done(self):
        """Whether any kept character has finished the first main quest, which takes the package
        away from Caius's desk: its journal reached the closing index."""
        folders = list(self.streams)
        if self.args.world:
            for path in glob.glob(os.path.join(self.args.world, "characters", "*", "*",
                                               STREAM_NAME)):
                folders.append(os.path.dirname(path))
        for folder in dict.fromkeys(folders):
            stream = self.streams.get(folder) or PlayerStream(os.path.join(folder, STREAM_NAME))
            for quest, indices in stream.journal.items():
                if quest.lower() == SPYMASTER_QUEST and any(i >= SPYMASTER_DONE for i in indices):
                    return True
        return False

    def new_character_kit(self):
        """What chargen's Sellus Gravius would have given: the starting gold, and unless another
        character has already delivered it, Caius Cosades's package with the quest."""
        lines = ([f'Player->AddItem "Gold_001" {self.args.start_gold}'] if self.args.start_gold
                 else [])
        if not self.spymaster_done():
            lines += [f'Player->AddItem "{CAIUS_PACKAGE}" 1',
                      f"Journal {SPYMASTER_QUEST} {SPYMASTER_GIVEN}"]
        return lines

    def on_pick(self, client, what, index, stamp, now):
        if what == PICK_CHARACTER and index == PICK_NEW:
            self.offer_starts(client, stamp, now)
        elif what == PICK_CHARACTER and index < len(client.listed):
            self.creating.discard(fingerprint(client.key))
            folder = os.path.join(self.key_folder(client), client.listed[index])
            path = os.path.join(folder, STREAM_NAME)
            if os.path.isfile(path):
                self.send_character(client, folder, path, "the list", stamp, now)
        elif (what == PICK_START and index < len(self.starts) and client.synced
              and client.character is None):
            folder = new_character_folder(self.key_folder(client), "character")
            os.makedirs(folder)
            client.character = os.path.basename(folder)
            client.naming = True
            self.player_stream(client).reset()
            self.player_ready(client, False, stamp, now)
            name, lines = self.starts[index]
            for line in lines + self.new_character_kit() + [""]:
                client.rel.queue(EVENT_RUN, 0, zstr(line))
            self.flush(client, now)
            print(f"{stamp} client {client.id} starts at {name}", flush=True)

    def received(self, client, stamp, now):
        """Adopt or compare an uploaded save only in an explicit diagnostic session."""
        if not client.upload.name.lower().endswith(".ess") or not (
                self.args.adopt or self.args.rebuild is not None):
            return
        if client.rebuild and client.rebuild[1] is None:
            self.compare_rebuild(client, stamp)
            return
        if not client.synced:
            print(f"{stamp} client {client.id} sent {client.upload.name} from a game that is not "
                  f"its character's; left in uploads", flush=True)
            return
        new = client.character is None
        player = save_player(client.upload.path)
        if not player:
            print(f"{stamp} client {client.id} sent {client.upload.name}, not a save; left in "
                  f"uploads", flush=True)
            return
        folder = (new_character_folder(self.key_folder(client), player) if new
                  else self.character_folder(client))
        head = keep_character(client.upload.path, folder)
        print(f"{stamp} client {client.id} kept {client.upload.name}: {head['player']} in "
              f"{head['cell']}, {len(head['masters'])} masters"
              + (f", a new character in {os.path.basename(folder)}" if new else ""), flush=True)
        client.kept += 1
        if new:
            client.character = os.path.basename(folder)
            self.creating.discard(fingerprint(client.key))
            self.player_ready(client, False, stamp, now)
        else:
            self.player_stream(client).checkpoint()

    def on_event(self, client, kind, data, stamp, now):
        """Validate a client's event before handling or relaying it."""
        client.events += 1
        if kind in SERVER_EVENTS:
            print(f"{stamp} client {client.id} sent server event {kind}: dropped", flush=True)
            return
        if not valid_client_event(kind, data):
            return
        handler = self.EVENT_HANDLERS.get(kind)
        relay = handler(self, client, kind, data, stamp, now) if handler else KEEP
        if relay:
            self.broadcast_event(client.id, kind, data, now)

    def event_offer(self, client, kind, data, stamp, now):
        if len(data) <= BULK_OFFER.size:
            return RELAY
        ident, size, digest = BULK_OFFER.unpack_from(data)
        name = wire_text(data[BULK_OFFER.size:].split(b"\0", 1)[0])
        if client.upload and client.upload.stream:
            client.upload.stream.close()
        folder = (os.path.join(self.args.world, "uploads", fingerprint(client.key))
                  if self.args.world and client.key else None)
        client.upload = Incoming(folder, ident, size, digest, name, now)
        print(f"{stamp} client {client.id} offers {name} ({size} bytes, id {ident:#010x}): "
              f"{BULK_STATUS[client.upload.status]}"
              + (f" from chunk {client.upload.next}"
                 if client.upload.status == BULK_RECEIVING else ""), flush=True)
        self.send(client, BULK_ACK, client.upload.ack(now))
        if client.upload.status == BULK_DONE:
            self.received(client, stamp, now)
        return KEEP

    def event_player(self, client, kind, data, stamp, now):
        if data[:1] in (bytes([PLAYER_DEATH]), bytes([PLAYER_ALIVE])):
            self.on_player_death(client, data[0] == PLAYER_ALIVE, stamp, now)
            return KEEP
        stream = self.player_stream(client) if client.synced else None
        change = stream.take(data) if stream else None
        if stream and client.naming and stream.identity:
            old = self.character_folder(client)
            folder = new_character_folder(self.key_folder(client), stream.identity["name"])
            stream.save()
            os.rename(old, folder)
            self.streams.pop(old)
            stream.path = os.path.join(folder, STREAM_NAME)
            self.streams[folder] = stream
            client.character, client.naming = os.path.basename(folder), False
            self.creating.discard(fingerprint(client.key))
        if change and self.detail["verbose"]:
            print(f"{stamp} client {client.id} {change}", flush=True)
        return KEEP

    def event_snapshot(self, client, kind, data, stamp, now):
        if len(data) != 4 + STATE_BODY.size:
            return RELAY
        stream = self.player_stream(client) if client.synced else None
        body = data[4:]
        flags, x, y, z, heading, cell = STATE_BODY.unpack(body)
        if (stream is None or not stream.identity or not flags & IN_WORLD or
                not placeable(x, y, z) or not finite(heading) or stream.arriving or
                stream.identity_parts or stream.worn_parts or stream.effects_parts):
            return KEEP
        stream.keep_place(body)
        stream.save()
        if self.world.path:
            self.world.save(now)
        client.snapshots += 1
        client.snapshot_saved = struct.unpack_from("<I", data)[0]
        client.rel.queue(EVENT_SNAPSHOT, 0, data[:4])
        self.flush(client, now)
        return KEEP

    def event_contents(self, client, kind, data, stamp, now):
        if len(data) < CONTENTS_HEAD.size:
            return RELAY
        refid, cell, part, parts, flags, entries = unpack_contents(data)
        have = self.arriving.get(client.id)
        if part == 0:
            have = self.arriving[client.id] = (refid, [], 0)
        if not have or have[0] != refid or have[2] != part:
            return KEEP
        self.arriving[client.id] = (refid, have[1] + entries, part + 1)
        if part + 1 == parts:
            del self.arriving[client.id]
            self.set_contents(client.id, refid, cell, have[1] + entries,
                              bool(flags & CONTENTS_ROLLED), stamp, now)
        return KEEP

    def event_want(self, client, kind, data, stamp, now):
        if not data:
            return RELAY
        cells = set(struct.unpack_from(f"<{min(data[0], (len(data) - 1) // 2)}H", data, 1))
        wanted = [refid for refid, box in self.world.contents.items() if box["cell"] in cells]
        for refid in wanted:
            self.send_contents(client.id, refid, now)
        if wanted:
            print(f"{stamp} client {client.id} loads cells {sorted(cells)}: sent "
                  f"{len(wanted)} containers", flush=True)
        return KEEP

    def event_spawn(self, client, kind, data, stamp, now):
        if len(data) <= SPAWN.size:
            return RELAY
        token, spawn = unpack_spawn(data)
        if placeable(*spawn["pos"]) and finite(*spawn["rot"]):
            self.add_spawn(client.id, spawn, stamp, now, token)
        return KEEP

    def event_remove(self, client, kind, data, stamp, now):
        if not data:
            return RELAY
        for sid in unpack_removes(data):
            self.remove_spawn(client.id, sid, stamp, now)
        return KEEP

    def event_weather(self, client, kind, data, stamp, now):
        if len(data) < 2:
            return RELAY
        flags, entries = unpack_weather(data)
        if flags & WEATHER_OFFER:
            entries = {i: w for i, w in entries.items() if i not in self.world.weather}
        self.set_weather(client.id, entries, stamp, now, not flags & WEATHER_OFFER)
        return KEEP

    def event_spell(self, client, kind, data, stamp, now):
        if len(data) <= SPELL.size:
            return RELAY
        caster, target, refid, _, player = SPELL.unpack_from(data)
        name = wire_text(data[SPELL.size:].split(b"\0")[0])
        who = f"client {client.id}" if player else f"{caster:#010x} of client {client.id}"
        on = (f"{refid:#010x} (authority {target})" if refid
              else f"the player of client {target}")
        print(f"{stamp} {who} casts {name} on {on}", flush=True)
        if target != BOT_ID:
            self.send_event(target, client.id, kind, data, now)
        return KEEP

    def event_cast(self, client, kind, data, stamp, now):
        if len(data) <= SPELL.size:
            return RELAY
        caster, target, refid, _, player = SPELL.unpack_from(data)
        name = wire_text(data[SPELL.size:].split(b"\0")[0])
        who = f"client {client.id}" if player else f"{caster:#010x} of client {client.id}"
        at = (f" at {refid:#010x} (client {target})" if refid
              else f" at the player of client {target}" if target else "")
        print(f"{stamp} {who} casts {name}{at}", flush=True)
        return RELAY

    def event_shot(self, client, kind, data, stamp, now):
        if len(data) <= SHOT.size:
            return RELAY
        firer, swing, other, player = SHOT.unpack_from(data)
        name = wire_text(data[SHOT.size:].split(b"\0")[0])
        who = f"client {client.id}" if player else f"{firer:#010x} of client {client.id}"
        print(f"{stamp} {who} shoots {name} ({swing:.2f}, {other:.2f})", flush=True)
        return RELAY

    def event_targeted(self, client, kind, data, stamp, now):
        if len(data) < 12:
            return RELAY
        refid, target = struct.unpack_from("<II", data)
        word = (f"damage {struct.unpack_from('<f', data, 8)[0]:.0f}"
                if kind in (EVENT_HIT, EVENT_PLAYER_HIT)
                else f"{struct.unpack_from('<I', data, 8)[0]}")
        if kind in (EVENT_HIT, EVENT_PLAYER_HIT) and len(data) >= 16:
            word += f", fatigue {struct.unpack_from('<f', data, 12)[0]:.0f}"
        if kind == EVENT_PLAYER_HIT:
            by = f" (attacker {refid:#010x})" if refid else ""
            print(f"{stamp} client {client.id} {TARGETED[kind]} client {target}{by}: {word}",
                  flush=True)
        else:
            print(f"{stamp} client {client.id} {TARGETED[kind]} {refid:#010x} "
                  f"(authority {target}): {word}", flush=True)
        if kind == EVENT_HOLD:
            on = struct.unpack_from("<I", data, 8)[0]
            held = self.dialogues.get(refid)
            if on and held and held[0] != client.id:
                print(f"{stamp} client {client.id} is refused dialogue with {refid:#010x}: "
                      f"client {held[0]} is talking", flush=True)
                self.send_event(client.id, 0, EVENT_HOLD_BROKEN,
                                struct.pack("<III", refid, client.id, 3), now)
                return KEEP
            if on:
                self.dialogues[refid] = (client.id, target)
            elif held and held[0] == client.id:
                del self.dialogues[refid]
        if target != BOT_ID:
            if kind != EVENT_HOLD or target != client.id:
                self.send_event(target, client.id, kind, data, now)
        elif kind == EVENT_HOLD and struct.unpack_from("<I", data, 8)[0] and \
                self.args.bot_break_hold is not None:
            self.bot.breaks.append((now + self.args.bot_break_hold, client.id, refid))
        return KEEP

    def event_death(self, client, kind, data, stamp, now):
        if len(data) < 4:
            return RELAY
        refid = struct.unpack_from("<I", data)[0]
        if refid in self.world.deaths:
            return KEEP
        self.world.deaths[refid] = client.id
        self.world.dirty = True
        print(f"{stamp} client {client.id}: {refid:#010x} died", flush=True)
        return RELAY

    def event_status(self, client, kind, data, stamp, now):
        if len(data) < STATUS.size:
            return RELAY
        refid, *values = STATUS.unpack_from(data)
        self.world.statuses[refid] = tuple(values)
        self.world.dirty = True
        if self.detail["verbose"]:
            print(f"{stamp} client {client.id}: {describe_status(refid, values)}", flush=True)
        return RELAY

    def event_affect(self, client, kind, data, stamp, now):
        if len(data) <= 5:
            return RELAY
        refid, index = struct.unpack_from("<IB", data)
        name = wire_text(data[5:].split(b"\0")[0])
        if self.detail["verbose"]:
            print(f"{stamp} client {client.id}: {refid:#010x} takes effect {index} of "
                  f"{name}", flush=True)
        return RELAY

    def event_objects(self, client, kind, data, stamp, now):
        if not data:
            return RELAY
        changed = unpack_objects(data)
        self.world.objects.update(changed)
        self.world.dirty = True
        for refid, rest in changed.items():
            if self.detail["verbose"]:
                print(f"{stamp} client {client.id}: {describe_object(refid, *rest)}",
                      flush=True)
        return RELAY

    def event_text(self, client, kind, data, stamp, now):
        print(f"{stamp} client {client.id} says: {wire_text(data)}", flush=True)
        return RELAY

    def event_game(self, client, kind, data, stamp, now):
        if len(data) < 5:
            return RELAY
        loaded, _, rest = data[4:].partition(b"\0")
        self.on_game(client, struct.unpack_from("<I", data)[0], wire_text(loaded),
                     rest[0] if rest else GAME_NONE, stamp, now)
        return KEEP

    def event_pick(self, client, kind, data, stamp, now):
        if len(data) < 2:
            return RELAY
        self.on_pick(client, data[0], data[1], stamp, now)
        return KEEP

    def event_busy(self, client, kind, data, stamp, now):
        if not data:
            return RELAY
        if data[0] and client.busy is None:
            client.busy = now
            print(f"{stamp} client {client.id} is saving: its cells and actors go to others",
                  flush=True)
        elif not data[0] and client.busy is not None:
            print(f"{stamp} client {client.id} is back after {now - client.busy:.1f} s",
                  flush=True)
            client.busy = None
        return RELAY

    def event_bounty(self, client, kind, data, stamp, now):
        if len(data) < 4:
            return RELAY
        bounty = struct.unpack_from("<i", data)[0]
        self.bounties[client.id] = data[:4]
        stream = self.player_stream(client) if client.synced else None
        if stream and stream.bounty != bounty:
            stream.bounty, stream.dirty = bounty, True
        print(f"{stamp} client {client.id} bounty {bounty}",
              flush=True)
        return RELAY

    def event_equipment(self, client, kind, data, stamp, now):
        if len(data) < 2:
            return RELAY
        sets = self.equipment.setdefault(client.id, [[], []])
        if data[0] == 0:
            sets[1] = []
        sets[1].append(data)
        if data[0] + 1 == data[1]:
            sets[0], sets[1] = sets[1], []
            items = [i for part in sets[0] for i in unpack_equipment(part)]
            print(f"{stamp} client {client.id} wears {len(items)}: {', '.join(items)}",
                  flush=True)
        return RELAY

    def event_identity(self, client, kind, data, stamp, now):
        try:
            part, female, first, second = unpack_identity(data)
        except ValueError:
            return KEEP
        parts = self.identities.get(client.id)
        if part == 0:
            parts = self.identities[client.id] = [data, None]
        elif not parts or not parts[0] or bool(parts[0][2]) != female:
            return KEEP
        else:
            parts[1] = data
        if parts[1]:
            name, race = unpack_identity(parts[0])[2:]
            head, hair = unpack_identity(parts[1])[2:]
            print(f"{stamp} client {client.id} is {name}: {race}, {head}, {hair}",
                  flush=True)
            if client.announce_due:
                self.announce_join(client, time.monotonic())
        return RELAY

    def event_actor_equipment(self, client, kind, data, stamp, now):
        try:
            refid, part, count, items = unpack_actor_equipment(data)
        except ValueError:
            return KEEP
        have = self.actor_equipment.get(refid)
        if part == 0:
            have = self.actor_equipment[refid] = [client.id, have[1] if have else [], []]
        elif not have or have[0] != client.id or len(have[2]) != part or \
                have[2][0][5] != count:
            return KEEP
        have[2].append(data)
        if part + 1 == count:
            have[1], have[2] = have[2], []
            worn = [item for body in have[1] for item in unpack_actor_equipment(body)[3]]
            print(f"{stamp} client {client.id}: {refid:#010x} wears {len(worn)}: "
                  f"{', '.join(worn)}", flush=True)
        return RELAY

    EVENT_HANDLERS = {EVENT_OFFER: event_offer,
                      EVENT_PLAYER: event_player,
                      EVENT_SNAPSHOT: event_snapshot,
                      EVENT_CONTENTS: event_contents,
                      EVENT_WANT: event_want,
                      EVENT_SPAWN: event_spawn,
                      EVENT_REMOVE: event_remove,
                      EVENT_WEATHER: event_weather,
                      EVENT_SPELL: event_spell,
                      EVENT_CAST: event_cast,
                      EVENT_SHOT: event_shot,
                      **dict.fromkeys(TARGETED, event_targeted),
                      EVENT_DEATH: event_death,
                      EVENT_STATUS: event_status,
                      EVENT_AFFECT: event_affect,
                      EVENT_OBJECTS: event_objects,
                      EVENT_TEXT: event_text,
                      EVENT_GAME: event_game,
                      EVENT_PICK: event_pick,
                      EVENT_BUSY: event_busy,
                      EVENT_BOUNTY: event_bounty,
                      EVENT_EQUIPMENT: event_equipment,
                      EVENT_IDENTITY: event_identity,
                      EVENT_ACTOR_EQUIPMENT: event_actor_equipment}

    def update_authority(self, now):
        """Name each loaded cell's authority and tell every client that has the cell loaded."""
        candidates, standing = {}, set()
        for client in sorted(self.clients.values(), key=lambda c: c.id):
            client.loaded = set()
            if client.alive and client.state:
                own, client.loaded = cell_keys(client.state)
                standing.add(own)
                for key in client.loaded:
                    candidates.setdefault(key, []).append((client.id, key == own))
        busy = {c.id for c in self.clients.values() if c.busy is not None}
        for key, cands in candidates.items():
            if any(c not in busy for c, _ in cands):
                candidates[key] = [c for c in cands if c[0] not in busy]
        forced = None
        if self.bot.state and self.bot.window(self.args.bot_owns, now):
            own, loaded = cell_keys(self.bot.state)
            for key in loaded:
                candidates.setdefault(key, []).append((BOT_ID, key == own))
            forced = BOT_ID
        new = assign_authority(self.owners, candidates, forced)
        stamp = time.strftime("%H:%M:%S")
        for key in sorted(standing & set(new), key=describe_key):  # the rest only follow
            if self.owners.get(key) != new[key]:
                print(f"{stamp} authority {describe_key(key)}: client {new[key]}", flush=True)
        self.owners.clear()
        self.owners.update(new)
        for client in self.clients.values():
            told = False
            for key in client.loaded:
                if key in new and client.known.get(key) != new[key]:
                    client.known[key] = new[key]
                    client.rel.queue(EVENT_AUTHORITY, 0, KEY.pack(*key) + struct.pack("<I", new[key]))
                    told = True
            for key in [k for k in client.known if k not in client.loaded]:
                del client.known[key]
            if told:
                self.flush(client, now)
        self.update_owners(now, forced)

    def update_owners(self, now, forced):
        """Name each actor's owner by proximity and tell every client that loads its cell where
        that differs from the cell's authority."""
        players = {}
        for client in self.clients.values():
            if client.alive and client.state and client.loaded and client.busy is None:
                players[client.id] = (client.loaded,) + STATE_BODY.unpack_from(client.state)[1:3]
        if self.args.bot_at and self.bot.state and not self.bot.busy:
            players[BOT_ID] = (cell_keys(self.bot.state)[1],) + \
                STATE_BODY.unpack_from(self.bot.state)[1:3]
        for refid in [r for r, seen in self.actor_seen.items() if now - seen > OWNER_STALE]:
            del self.actor_seen[refid]
        live = {}
        for refid in self.actor_seen:
            spawn = self.world.spawns.get(refid)
            if spawn and spawn.get("summon"):
                continue  # run by its maker
            _, key, record = self.actors[refid]
            values = ACTOR.unpack(record)
            live[refid] = (key, values[1], values[2], values[6], values[9])
        new = {} if forced else assign_owners(self.actor_owners, live, players, self.owners, now)
        stamp = time.strftime("%H:%M:%S")
        for refid, (client_id, _) in new.items():
            if self.actor_owners.get(refid, (None,))[0] not in (None, client_id):
                print(f"{stamp} actor {refid:#010x} owned by client {client_id}", flush=True)
        self.actor_owners.clear()
        self.actor_owners.update(new)
        for client in self.clients.values():
            if not client.alive:
                continue
            changes = []
            for refid, (client_id, _) in new.items():
                want = client_id if client_id != self.owners.get(live[refid][0]) else 0
                if live[refid][0] in client.loaded and client.owners_told.get(refid, 0) != want:
                    changes.append((refid, want))
            for refid, told in client.owners_told.items():
                if told and (refid not in new or live[refid][0] not in client.loaded):
                    changes.append((refid, 0))
            for refid, want in changes:
                if want:
                    client.owners_told[refid] = want
                else:
                    client.owners_told.pop(refid, None)
            for i in range(0, len(changes), OWNERS_PER_EVENT):
                chunk = changes[i:i + OWNERS_PER_EVENT]
                client.rel.queue(EVENT_OWNERS, 0, struct.pack("<I", len(chunk)) +
                                 b"".join(OWNER_PAIR.pack(*c) for c in chunk))
            if changes:
                self.flush(client, now)

    def actor_authority(self, refid):
        """The authority of the cell an actor was last reported in, or None."""
        return self.owners.get(self.actors[refid][1]) if refid in self.actors else None

    def on_actors(self, client, body):
        """Keep an authority's actor states and relay them to the other clients."""
        count = struct.unpack_from("<I", body)[0]
        own = cell_keys(client.state)[0] if client.state else None
        kept = []  # records with a finite position and statistics, the only ones relayed
        for i in range(min(count, (len(body) - 4) // ACTOR.size)):
            record = body[4 + i * ACTOR.size:4 + (i + 1) * ACTOR.size]
            values = ACTOR.unpack(record)
            if not placeable(*values[1:5]) or not finite(values[5], *values[7:9]):
                continue
            refid, x, y = values[:3]
            key = own if own and own[0] == KEY_INTERIOR else (
                KEY_EXTERIOR, math.floor(x / CELL_UNITS), math.floor(y / CELL_UNITS), b"")
            self.actors[refid] = (client.id, key, record)
            self.actor_seen[refid] = time.monotonic()
            if refid == self.args.bot_mirror:
                self.bot.mirror = record
            client.actor_states += 1
            kept.append(record)
        if not kept:
            return
        body = struct.pack("<I", len(kept)) + b"".join(kept)
        for other in self.clients.values():
            if other is not client and other.in_world:
                self.send(other, ACTORS, struct.pack("<I", client.id) + body)

    def handshake(self, kind, session, packet, addr, now, version):
        """Answer HANDSHAKE1 with HANDSHAKE2; on HANDSHAKE3, the HELLO it carries, the console's
        key, the session keys and whether this HANDSHAKE3 came before."""
        for stale in [k for k, v in self.pending.items() if now - v["time"] > HANDSHAKE_KEEP]:
            del self.pending[stale]
        entry = self.pending.get(session)
        if entry and entry["version"] != version:
            return None
        if kind == HANDSHAKE1:
            e = packet[OUTER.size:OUTER.size + 32]
            if len(packet) < HANDSHAKE_PAD or entry and entry["e"] != e:
                return None
            if entry is None:
                noise = Noise(False, self.server_secret, os.urandom(32), PROLOGUE)
                noise.read1(e)
                entry = self.pending[session] = {
                    "noise": noise, "e": e, "time": now, "done": None, "version": version,
                    "reply": OUTER.pack(b"T3MP", version, HANDSHAKE2, 0, session, 0)
                    + noise.write2()}
            self.transmit(addr, entry["reply"])
            return None
        if kind != HANDSHAKE3 or entry is None:
            return None
        message = packet[OUTER.size:]
        if entry["done"] is None:
            try:
                hello = entry["noise"].read3(message)
            except ValueError:
                return None
            entry["done"] = (message, hello, entry["noise"].rs, entry["noise"].split())
            return entry["done"][1:] + (False,)
        if entry["done"][0] != message:
            return None
        return entry["done"][1:] + (True,)

    def guarded(self, packet, addr):
        """handle, with a packet that breaks a parser dropped and logged instead of ending the
        server: the parsers check lengths and ranges, this is the second line."""
        try:
            self.handle(packet, addr)
        except (struct.error, ValueError, IndexError, KeyError, TypeError, OverflowError) as error:
            where = traceback.extract_tb(error.__traceback__)[-1]
            print(f"{time.strftime('%H:%M:%S')} malformed packet from {addr[0]}: "
                  f"{type(error).__name__} in {where.name}: {error}", flush=True)

    def handle(self, packet, addr):
        """Take a handshake message or open a sealed packet, then hand it on in the T3MP
        layout. Anything else is dropped unread."""
        if len(packet) < OUTER.size or self.dropped("in") or addr[0] in self.bans["address"]:
            return
        magic, version, kind, _, session, seq = OUTER.unpack_from(packet)
        if magic != b"T3MP" or not version:
            return
        now = time.monotonic()
        if kind in (HANDSHAKE1, HANDSHAKE3):
            if kind == HANDSHAKE1 and session not in self.pending:
                source = self.handshake_buckets.get(addr[0], now)
                if (len(self.pending) >= HANDSHAKES_PENDING or source is None or not source.take(now)
                        or not self.handshake_bucket.take(now)):
                    self.limits["handshakes"] += 1
                    return
            done = self.handshake(kind, session, packet, addr, now, version)
            if done:
                hello, key, keys, again = done
                if len(hello) < 18:
                    return
                manager = bool(struct.unpack_from("<I", hello, 14)[0] & MANAGER)
                expected = MANAGER_VERSION if manager else T3MP_VERSION
                if version != expected:
                    stranger = self.handshake_client(addr, session, keys, hello[:6].hex(":"),
                                                     version)
                    self.send(stranger, REFUSE,
                              REFUSE_BODY.pack(expected, version, REFUSED_PROTOCOL))
                    print(f"{time.strftime('%H:%M:%S')} refused "
                          f"{'manager' if manager else 'game'} at {addr[0]}: protocol "
                          f"{version}, expected {expected}", flush=True)
                    return
                client = self.by_session.get(session)
                if again and client is not None and client.keys == keys:
                    # The WELCOME was lost, or this is a replay: answer the address that joined.
                    self.send(client, WELCOME, struct.pack("<I", client.id))
                    return
                self.handle_plain(T3MP.pack(b"T3MP", version, HELLO, 0, session, 0, 0, 0, 0)
                                  + hello, addr, (key, keys))
            return
        client = self.by_session.get(session)
        if version != T3MP_VERSION or kind != SEALED or client is None or client.keys is None or \
                len(packet) < OUTER.size + INNER.size + NOISE_TAG:
            return
        top, seen = client.replay
        if not seq or seq <= top and (top - seq >= 32 or seen >> (top - seq) & 1):
            client.replayed += 1
            return
        inner = unseal(client.keys[0], seq, packet[:OUTER.size], packet[OUTER.size:])
        if inner is None:
            client.forged += 1
            return
        if not client.bucket.take(now):
            client.limited += 1
            return
        if seq > top:
            client.replay = (seq, (seen << min(seq - top, 32) | 1) & 0xFFFFFFFF)
        else:
            client.replay = (top, seen | 1 << (top - seq))
        inner_kind, ack, sent, echo = INNER.unpack_from(inner)
        self.handle_plain(T3MP.pack(b"T3MP", T3MP_VERSION, inner_kind, 0, session, seq, ack, sent,
                                    echo) + inner[INNER.size:], addr)

    def handshake_client(self, addr, session, keys, mac, version):
        # Repeated HANDSHAKE3 replies need distinct nonces and the original destination.
        entry = self.pending[session]
        if "reply_client" not in entry:
            client = entry["reply_client"] = Client(0, mac)
            client.addr, client.session, client.keys = addr, session, keys
            client.version = version
        return entry["reply_client"]

    def refuse(self, addr, session, keys, mac, reason, version=T3MP_VERSION):
        stranger = self.handshake_client(addr, session, keys, mac, version)
        order, plugins = self.pinned or (0, 0)
        self.send(stranger, REFUSE, REFUSE_BODY.pack(order, plugins or 0, reason))

    def kick(self, client, reason):
        """Refuse a joined client, which stops it until the game is launched again."""
        if client.alive and client.keys:
            order, plugins = self.pinned or (0, 0)
            self.send(client, REFUSE, REFUSE_BODY.pack(order, plugins or 0, reason))
        if client.alive:
            self.leave(client)
        self.by_session.pop(client.session, None)

    def ask_save(self, targets, now, diagnostic=False):
        for client in targets:
            if client.alive:
                client.snapshot_request = 0x80000000 | ((client.snapshot_request + 1) & 0x7fffffff)
                client.rel.queue(EVENT_SAVE, 0, b"\x01" if diagnostic else
                                 struct.pack("<I", client.snapshot_request))
                self.flush(client, now)
        return ", ".join(f"client {c.id}" for c in targets if c.alive) or "nobody"

    def admin(self, line):
        """Run an admin command; the reply to print."""
        words = line.split()
        verb, rest = (words[0].lower(), words[1:]) if words else ("", [])
        by_id = {str(c.id): c for c in self.clients.values()}
        if verb == "list" and not rest:
            return "\n".join(
                f"client {c.id}: {'playing' if c.alive else 'away'}, key "
                f"{fingerprint(c.key) if c.key else '-'}, mac {c.mac}, address "
                f"{c.addr[0] if c.addr else '-'}"
                for c in sorted(self.clients.values(), key=lambda c: c.id)) or "no clients"
        if verb == "kick" and len(rest) == 1 and rest[0] in by_id:
            self.kick(by_id[rest[0]], REFUSED_KICKED)
            return f"kicked client {rest[0]}"
        if verb == "ban" and len(rest) == 1 and rest[0] in by_id:
            client = by_id[rest[0]]
            rest = ["key", fingerprint(client.key), "mac", client.mac]
        if verb in ("ban", "unban") and rest and len(rest) % 2 == 0:
            pairs = [(kind, ban_value(kind, value)) for kind, value in zip(rest[::2], rest[1::2])]
            if all(value for _, value in pairs):
                for kind, value in pairs:
                    (self.bans[kind].add if verb == "ban" else self.bans[kind].discard)(value)
                save_bans(self.bans_path, self.bans)
                if verb == "ban":
                    for c in list(self.clients.values()):
                        if c.alive and ((c.key and fingerprint(c.key) in self.bans["key"]) or
                                        c.mac in self.bans["mac"] or
                                        (c.addr and c.addr[0] in self.bans["address"])):
                            self.kick(c, REFUSED_BANNED)
                return (f"{verb}ned " if verb == "ban" else "unbanned ") + ", ".join(
                    f"{kind} {value}" for kind, value in pairs) + (
                    "" if self.bans_path else " (not kept: give --world)")
        if verb == "save" and len(rest) <= 1 and all(r in by_id for r in rest):
            targets = [by_id[r] for r in rest] or list(self.clients.values())
            return "asked to save: " + self.ask_save(targets, time.monotonic())
        if verb == "log" and len(rest) <= 1 and (not rest or rest[0] in LOG_LEVELS):
            if rest:
                self.detail["verbose"] = rest[0] == "verbose"
            return "log " + ("verbose" if self.detail["verbose"] else "normal")
        if verb == "say" and rest:
            self.notify(" ".join(rest), time.monotonic())
            return "sent to everyone"
        if verb == "tell" and len(rest) >= 2 and rest[0] in by_id:
            self.notify(" ".join(rest[1:]), time.monotonic(), only=by_id[rest[0]])
            return f"sent to client {rest[0]}"
        if verb == "stop" and not rest:
            self.begin_stop("admin stop", time.monotonic())
            return "stopping"
        if verb == "bans" and not rest:
            return "\n".join(f"{kind} {value}" for kind in BAN_KINDS
                             for value in sorted(self.bans[kind])) or "no bans"
        return ("commands:\n"
                "  list                  the clients\n"
                "  status                the clock, weather, clients and world\n"
                "  kick N                drop client N\n"
                "  log [normal|verbose]  show or set whether state changes (skills, vitals,\n"
                "                        statuses, objects) are printed\n"
                "  save [N]              ask every console, or client N, to save\n"
                "  ban N                 ban client N's key and MAC, and drop it\n"
                "  ban|unban key FINGERPRINT|mac MAC|address A.B.C.D\n"
                "  bans                  the bans\n"
                "  say TEXT              show TEXT on every console\n"
                "  tell N TEXT           show TEXT on client N's console\n"
                "  stop                  save every character, then stop the server")

    def status(self, now):
        """The server's state, one line each."""
        out = []
        if self.world.clock:
            self.world.clock.advance(now)
            out.append(f"  clock {self.world.clock}")
        if self.world.weather:
            out.append(f"  weather: {describe_weather(self.world.weather)}")
        if self.limits["handshakes"]:
            out.append(f"  handshakes refused over rate: {self.limits['handshakes']}")
        for client in self.clients.values():
            out.append(f"  client {client.id}: {'up' if client.alive else 'down'}"
                  + (" (saving)" if client.busy is not None else "") + ", "
                  + summary(client))
        if self.world.objects:
            out.append(f"  objects: {len(self.world.objects)} changed")
        if self.world.spawns:
            live = sum(not s["removed"] for s in self.world.spawns.values())
            out.append(f"  spawns: {live} live, {len(self.world.spawns) - live} removed")
        if self.actors:
            out.append(f"  actors: {len(self.actors)} known; authorities "
                  + ", ".join(f"{describe_key(k)} {c}" for k, c in sorted(
                      self.owners.items(), key=lambda i: describe_key(i[0]))))
            runs = {}
            for client_id, _ in self.actor_owners.values():
                runs[client_id] = runs.get(client_id, 0) + 1
            out.append("  actors run by: " + ", ".join(
                f"client {c} {n}" for c, n in sorted(runs.items())))
        return "\n".join(out)

    def handle_plain(self, packet, addr, secure=None):
        magic, version, kind, _, session, seq, _, sent, _ = T3MP.unpack_from(packet)
        stamp = time.strftime("%H:%M:%S")
        now = time.monotonic()
        if kind == HELLO and secure and len(packet) >= T3MP.size + HELLO_BODY.size:
            self.hello(packet, addr, secure, version, session, seq, sent, stamp, now)
            return
        client = self.by_session.get(session)
        if client is None:
            return
        if seq > client.peer_seq + 1:
            client.gaps += seq - client.peer_seq - 1
        client.peer_seq = max(client.peer_seq, seq)
        client.peer_time, client.addr, client.last = sent, addr, time.monotonic()
        if not client.alive:
            print(f"{stamp} client {client.id} back", flush=True)
            client.alive = True
        handler = self.PACKET_HANDLERS.get(kind)
        if handler:
            handler(self, client, packet, session, stamp, now)

    def hello(self, packet, addr, secure, version, session, seq, sent, stamp, now):
        """A HELLO sealed by a finished handshake: admit, refuse or rejoin the console, and
        send a joining one the shared world."""
        key, keys = secure
        mac, build, order, plugins, *offered, build_id = HELLO_BODY.unpack_from(packet,
                                                                              T3MP.size)
        manager = bool(plugins & MANAGER)
        mac, lobby, plugins = mac.hex(":"), bool(plugins & LOBBY), plugins & ~LOBBY & ~MANAGER
        if fingerprint(key) in self.bans["key"] or mac in self.bans["mac"]:
            print(f"{stamp} refused {mac} at {addr[0]}: banned", flush=True)
            self.refuse(addr, session, keys, mac, REFUSED_BANNED, version)
            return
        if self.password and key not in self.admitted:
            tries = self.password_buckets.get(addr[0], now)
            given = packet[T3MP.size + HELLO_BODY.size:]
            if tries is None or not tries.take(now) or not hmac.compare_digest(given, self.password):
                print(f"{stamp} refused {mac} at {addr[0]}: wrong password", flush=True)
                self.refuse(addr, session, keys, mac, REFUSED_PASSWORD, version)
                return
            self.admitted.add(key)
            if self.admitted_path:
                with open(self.admitted_path, "a", encoding="utf-8") as stream:
                    stream.write(f"{key.hex()} {mac} {time.strftime('%Y-%m-%d')}\n")
            print(f"{stamp} admitted key {fingerprint(key)} ({mac})", flush=True)
        if manager:
            stranger = self.handshake_client(addr, session, keys, mac, version)
            body = self.build_server.ticket() if self.build_server else \
                tes3x_netbuild.BUILD_BODY.pack(bytes(32), 0, 0, bytes(16))
            who = self.manager_character(key).encode("latin-1", "replace")[:46]
            self.send(stranger, BUILD, body + (who + b"\0" if who else b""))
            print(f"{stamp} manager {fingerprint(key)} at {addr[0]} asked for the build"
                  + ("" if self.build_server else ", which is not served"), flush=True)
            return
        served = self.build_server.build_id() if self.build_server and any(build_id) else None
        if served and build_id != served:
            print(f"{stamp} refused {mac}: build {build_id.hex()[:16]}, the server's is "
                  f"{served.hex()[:16]}", flush=True)
            self.refuse(addr, session, keys, mac, REFUSED_STALE)
            return
        if self.pinned is None and not lobby:
            self.pinned = (order, plugins)
            print(f"{stamp} load order {order:#010x} ({plugins} plugins) set by {mac}",
                  flush=True)
            self.adopt_world(order, now)
        if self.pinned and order != self.pinned[0]:
            print(f"{stamp} refused {mac}: load order {order:#010x} ({plugins} plugins), "
                  f"session has {self.pinned[0]:#010x}", flush=True)
            self.refuse(addr, session, keys, mac, REFUSED_LOAD_ORDER)
            return
        # A console is known by its key for this server; the MAC is only a hint.
        client = self.clients.get(key)
        playing = sum(c.alive for c in self.clients.values() if c is not client)
        if playing >= self.args.max_players:
            print(f"{stamp} refused {mac}: {playing} players, the most allowed", flush=True)
            self.refuse(addr, session, keys, mac, REFUSED_FULL)
            return
        if client is None:
            client = self.clients[key] = Client(len(self.clients) + 1, mac)
            client.key = key
            print(f"{stamp} client {client.id} is key {fingerprint(key)}", flush=True)
        client.mac = mac
        self.by_session.pop(client.session, None)
        client.session = session
        self.by_session[client.session] = client
        if client.keys != keys:  # a resent HANDSHAKE3 keeps the replay window
            client.keys, client.replay = keys, (0, 0)
        client.addr, client.peer_seq, client.peer_time = addr, seq, sent
        client.joins += 1
        client.alive, client.last = True, now
        if client.rel.out:
            print(f"{stamp} client {client.id}: {len(client.rel.out)} unacked events "
                  f"dropped by the rejoin", flush=True)
        client.rel = Reliable()
        client.known = {}
        client.owners_told = {}
        client.busy = None
        client.lobby = lobby
        if client.joins == 1:
            client.joined = now
            client.bursts = sorted(self.bursts)
        verb = "joined" if client.joins == 1 else "rejoined"
        print(f"{stamp} client {client.id} {verb}: {mac} at {addr[0]}:{addr[1]}, "
              f"build {build:#010x}", flush=True)
        self.send(client, WELCOME, struct.pack("<I", client.id))
        if lobby:
            print(f"{stamp} client {client.id} is at the main menu", flush=True)
            return
        if self.world.clock is None:
            offered = sane_clock(offered)
            if self.args.hour is not None:
                offered[0] = self.args.hour
            if self.args.timescale is not None:
                offered[5] = self.args.timescale
            self.world.clock = Clock(*offered, now)
            print(f"{stamp} clock {self.world.clock}, from client {client.id}", flush=True)
        self.send(client, CLOCK, self.world.clock.body(now))
        for refid, origin in self.world.deaths.items():
            client.rel.queue(EVENT_DEATH, origin, struct.pack("<I", refid))
        for data in pack_objects(self.world.objects):
            client.rel.queue(EVENT_OBJECTS, 0, data)
        for refid, values in self.world.statuses.items():
            client.rel.queue(EVENT_STATUS, 0, STATUS.pack(refid, *values))
        for sid, spawn in sorted(self.world.spawns.items(), key=lambda s: s[1]["removed"]):
            client.rel.queue(EVENT_SPAWN, spawn["origin"] if spawn.get("summon") else 0,
                             pack_spawn(sid, spawn))
        for origin, (parts, _) in self.equipment.items():
            if origin != client.id:
                for part in parts:
                    client.rel.queue(EVENT_EQUIPMENT, origin, part)
        for origin, parts in self.identities.items():
            if origin != client.id and all(parts):
                for part in parts:
                    client.rel.queue(EVENT_IDENTITY, origin, part)
        for origin, parts, _ in self.actor_equipment.values():
            for part in parts:
                client.rel.queue(EVENT_ACTOR_EQUIPMENT, origin, part)
        for data in pack_weather(self.world.weather):
            client.rel.queue(EVENT_WEATHER, 0, data)
        for origin, data in self.bounties.items():
            if origin != client.id:
                client.rel.queue(EVENT_BOUNTY, origin, data)
        for other in self.clients.values():
            if other is not client and other.in_world and other.dead:
                client.rel.queue(EVENT_PLAYER, other.id, bytes([PLAYER_DEATH]))
        if self.sending and (client.bulk is None or client.bulk.status != 3):
            client.bulk = Outgoing(*self.sending)
            client.rel.queue(EVENT_OFFER, 0, client.bulk.offer())
            print(f"{stamp} offering {self.sending[0]} ({len(self.sending[1])} bytes, id "
                  f"{client.bulk.id:#010x}) to client {client.id}", flush=True)
        if not self.key_folder(client):  # with characters, it joins once it has one
            self.announce_join(client, now)
        if client.rel.out:
            self.flush(client, now)

    def packet_heartbeat(self, client, packet, session, stamp, now):
        client.beats += 1
        self.send(client, HEARTBEAT)

    def packet_state(self, client, packet, session, stamp, now):
        if len(packet) < T3MP.size + STATE_SIZE:
            return
        if not placeable(*STATE_BODY.unpack_from(packet, T3MP.size)[1:5]):
            return
        client.state = packet[T3MP.size:T3MP.size + STATE_SIZE]
        client.states += 1
        self.keep_place(client, now)
        if self.args.bot:
            self.bot.follow(client.state)
            if self.args.bot_echo:
                self.bot.echo = client.state
        for other in self.clients.values():
            if other is not client and other.in_world:
                self.send(other, PEER, struct.pack("<I", client.id) + client.state)

    def packet_actors(self, client, packet, session, stamp, now):
        if len(packet) < T3MP.size + 4:
            return
        self.on_actors(client, packet[T3MP.size:])

    def packet_bulk_ack(self, client, packet, session, stamp, now):
        if not client.bulk or len(packet) < T3MP.size + BULK_ACK_BODY.size:
            return
        bulk = client.bulk
        first = bulk.first is None
        status = bulk.on_ack(packet[T3MP.size:], now)
        if first and bulk.first is not None:
            print(f"{stamp} client {client.id} takes {bulk.name} from chunk {bulk.first} "
                  f"of {bulk.chunks}", flush=True)
        if status == BULK_NO_SPACE:
            print(f"{stamp} client {client.id} has no room for {bulk.name} "
                  f"({len(bulk.data)} bytes and its margin)", flush=True)
        elif status is not None and status != BULK_RECEIVING:
            took = now - bulk.started
            size = len(bulk.data) - min(bulk.first, bulk.chunks) * BULK_CHUNK
            print(f"{stamp} client {client.id} {bulk.name}: "
                  f"{BULK_STATUS[status] if status < len(BULK_STATUS) else status}, "
                  f"{max(size, 0)} bytes in {took:.1f} s "
                  f"({max(size, 0) / 1024 / max(took, 0.001):.0f} KB/s), "
                  f"chunks sent {bulk.sent}, resent {bulk.resent} ({bulk.fast} on a gap), "
                  f"probes {bulk.probes}",
                  flush=True)

    def packet_chunk(self, client, packet, session, stamp, now):
        if not client.upload or len(packet) < T3MP.size + 8:
            return
        upload = client.upload
        ident, index = struct.unpack_from("<II", packet, T3MP.size)
        if ident != upload.id:
            return
        receiving = upload.status == BULK_RECEIVING
        if upload.on_chunk(index, packet[T3MP.size + 8:]):
            self.send(client, BULK_ACK, upload.ack(now))
        if receiving and upload.status != BULK_RECEIVING:
            took = max(now - upload.started, 0.001)
            size = upload.size - upload.first * BULK_CHUNK
            print(f"{stamp} client {client.id} sent {upload.name}: "
                  f"{BULK_STATUS[upload.status]}, {size} bytes in {took:.1f} s "
                  f"({size / 1024 / took:.0f} KB/s)", flush=True)
            if upload.status == BULK_DONE:
                self.received(client, stamp, now)

    def packet_events(self, client, packet, session, stamp, now):
        if len(packet) < T3MP.size + EVENTS_HEAD.size:
            return
        ready, carried = client.rel.receive(packet[T3MP.size:])
        if carried:
            self.flush(client, now, resend=False)
        for _, event_kind, _, data in ready:
            self.on_event(client, event_kind, data, stamp, now)

    def packet_bye(self, client, packet, session, stamp, now):
        print(f"{stamp} client {client.id} left", flush=True)
        self.leave(client)
        self.by_session.pop(session, None)

    PACKET_HANDLERS = {HEARTBEAT: packet_heartbeat,
                       STATE: packet_state,
                       ACTORS: packet_actors,
                       BULK_ACK: packet_bulk_ack,
                       CHUNK: packet_chunk,
                       EVENTS: packet_events,
                       BYE: packet_bye}


    def begin_stop(self, why, now):
        if self.stop["until"] is not None:
            return
        self.stop["until"] = now + self.args.stop_wait
        self.notify("The server is shutting down.", now)
        asked = self.ask_save([c for c in self.clients.values() if c.alive], now)
        self.stop["waiting"] = {c.id: c.snapshot_request for c in self.clients.values()
                                if c.alive and c.synced}
        print(f"{time.strftime('%H:%M:%S')} stopping ({why}): asked to save: {asked}; waiting "
              f"up to {self.args.stop_wait:g} s for "
              + (", ".join(f"client {i}" for i in self.stop["waiting"]) or "nobody"), flush=True)
        signal.signal(signal.SIGINT, signal.default_int_handler)

    def stopped(self, now):
        if self.stop["until"] is None:
            if self.deadline is None or now < self.deadline:
                return False
            self.begin_stop("duration over", now)
        for ident, kept in list(self.stop["waiting"].items()):
            client = next(c for c in self.clients.values() if c.id == ident)
            if client.snapshot_saved == kept or not client.alive:
                del self.stop["waiting"][ident]
                print(f"{time.strftime('%H:%M:%S')} client {ident} "
                      + ("saved" if client.snapshot_saved == kept else "left without saving"), flush=True)
        if self.stop["waiting"] and now < self.stop["until"]:
            return False
        for ident in self.stop["waiting"]:
            print(f"{time.strftime('%H:%M:%S')} client {ident} did not save in "
                  f"{self.args.stop_wait:g} s", flush=True)
        return True

    def run(self):
        sys.stdout.reconfigure(errors="replace")  # the console's code page cannot print every name
        self.links = [Tunnel(port) for port in self.args.tunnel]
        self.sock = udp_socket()
        self.sock.bind((self.args.bind, self.args.port))
        print(f"serving on {self.args.bind}:{self.args.port}"
              + "".join(f" and tunnel {port}" for port in self.args.tunnel), flush=True)
        self.clients, self.by_session = {}, {}
        self.hosts = {}
        for entry in self.args.host:
            name, _, address = entry.partition("=")
            socket.inet_aton(address)
            self.hosts[name.lower().rstrip(".")] = address
        self.dns = None
        if self.hosts:
            self.dns = udp_socket()
            self.dns.bind((self.args.bind, DNS_PORT))
            print(f"answering DNS on {self.args.bind}:{DNS_PORT} for "
                  f"{', '.join(sorted(self.hosts))}",
                  flush=True)
        self.deadline = time.monotonic() + self.args.duration if self.args.duration else None
        self.report_next = time.monotonic() + self.args.report
        self.loss = random.Random(self.args.seed)
        self.pinned = None
        if self.args.load_order:
            self.pinned = (int(self.args.load_order, 16), None)
        self.lost = {"in": 0, "out": 0}
        self.clock_next = 0.0
        self.save_next = (time.monotonic() + self.args.save_every if self.args.save_every
                          else math.inf)
        self.owners = {}  # cell -> authority client
        self.actor_owners = {}  # actor id -> (client, since): its owner by proximity
        self.actor_seen = {}  # actor id -> when a state of it last came
        self.dialogues = {}  # actor id -> (talking client, authority client)
        # client -> [parts of its latest whole equipment set, parts of the set arriving]
        self.equipment = {}
        self.identities = {}  # client -> its complete [name/race, head/hair] parts
        self.actor_equipment = {}  # actor id -> (authority, complete parts, arriving parts)
        self.bounties = {}
        # refid -> (reporting client, cell, ACTOR bytes), the latest from an authority
        self.actors = {}
        self.arriving = {}  # client id -> (refid, entries so far, next part)
        self.bot = Bot(self, time.monotonic())
        self.world = World()
        # per-tick state changes in the console
        self.detail = {"verbose": self.args.log == "verbose"}
        self.streams = {}  # character folder -> PlayerStream
        self.streams_saved = 0.0
        self.starts = load_starts(self.args.starts or STARTS)
        self.creating = set()  # key fingerprints making a new character
        self.authority_next = 0.0
        self.server_secret = load_server_key(self.args)
        self.password = load_password(self.args)
        self.admitted_path = (os.path.join(self.args.world, "admitted.txt") if self.args.world
                              else None)
        self.admitted = load_admitted(self.admitted_path)
        self.password_buckets = SourceBuckets(*PASSWORD_RATE)
        self.bans_path = os.path.join(self.args.world, "bans.txt") if self.args.world else None
        self.bans = load_bans(self.bans_path)
        self.admin_sock, commands = None, queue.Queue()
        admin_port = ADMIN_PORT if self.args.admin_port is None else self.args.admin_port
        if admin_port:
            self.admin_sock = udp_socket()
            try:
                self.admin_sock.bind(("127.0.0.1", admin_port))
                print(f"admin commands on 127.0.0.1:{admin_port} (tes3x net admin)"
                      + (", and here" if sys.stdin and sys.stdin.isatty() else ""), flush=True)
            except OSError as error:
                self.admin_sock = None
                print(f"no admin port: 127.0.0.1:{admin_port}: {error}", flush=True)
        self.remote_admin = None
        if self.args.remote_admin:
            password_path = self.args.admin_password_file or (
                os.path.join(self.args.world, "admin-password.txt") if self.args.world else None)
            if not password_path or not os.path.isfile(password_path):
                sys.exit("--remote-admin needs --admin-password-file, or admin-password.txt in "
                         "--world")
            remote_sock = udp_socket()
            remote_sock.bind((self.args.bind, self.args.remote_admin))
            self.remote_admin = RemoteAdmin(admin_secret(load_admin_password(password_path)),
                                            remote_sock)
            print(f"remote admin on {self.args.bind}:{self.args.remote_admin}, with the password "
                  f"in "
                  f"{password_path}", flush=True)
        if sys.stdin and sys.stdin.isatty():
            threading.Thread(target=lambda: [commands.put(line) for line in sys.stdin],
                             daemon=True).start()
        if self.password:
            print(f"password asked of new consoles; {len(self.admitted)} admitted"
                  + ("" if self.admitted_path else " (not kept: give --world)"), flush=True)
        self.handshake_bucket = Bucket(*HANDSHAKE_RATE_ALL)
        self.handshake_buckets = SourceBuckets(*HANDSHAKE_RATE)
        self.limits = {"handshakes": 0}
        # session -> a handshake in progress or just done: {"noise", "e", "reply", ...}
        self.pending = {}
        self.bursts = [(float(at), int(count)) for count, _, at in
                       (spec.partition("@") for spec in self.args.burst)]
        self.build_server = None
        if self.args.build:
            build = tes3x_netbuild.Build(self.args.build, self.args.deltas,
                                         self.args.serve_origin or tes3x_netbuild.SERVED_BY_DEFAULT)
            self.build_server = tes3x_netbuild.BuildServer(
                build, self.args.bind,
                self.args.port if self.args.http_port is None else self.args.http_port)
            print(f"{build.describe()}; HTTP on {self.args.bind}:{self.build_server.port}",
                  flush=True)
        self.sending = None
        if self.args.send:
            name = os.path.basename(self.args.send)
            if not plain_name(name):
                raise SystemExit(f"--send: {name!r} is not a plain name of at most {BULK_NAME} "
                                 "characters")
            with open(self.args.send, "rb") as stream:
                self.sending = (name, stream.read())

        # Stopping asks every joined console for its character and waits, up to --stop-wait, for the
        # saves of those running one; a second Ctrl-C stops at once.
        self.stop = {"until": None, "waiting": {}}

        previous = signal.signal(signal.SIGINT, lambda *_: commands.put("stop"))
        try:
            if self.pinned:
                self.adopt_world(self.pinned[0], time.monotonic())
            while not self.stopped(time.monotonic()):
                while not commands.empty():
                    print(self.admin(commands.get()), flush=True)
                waiting = ([self.sock] + [link.sock for link in self.links]
                           + [link.forward for link in self.links]
                           + ([self.dns] if self.dns else [])
                           + ([self.admin_sock] if self.admin_sock else [])
                           + ([self.remote_admin.sock] if self.remote_admin else []))
                wait = 0.25
                if any(c.queue for c in self.clients.values()):
                    wait = PACE_WINDOW
                elif any(c.flush_due for c in self.clients.values()):
                    wait = EVENTS_GAP
                elif any(c.bulk and c.bulk.status == BULK_RECEIVING for c in self.clients.values()):
                    wait = 0.05
                for ready in select.select(waiting, [], [], wait)[0]:
                    self.receive(ready)
                self.tick(time.monotonic())
            if self.world.path:
                self.world.save(time.monotonic())
            for stream in self.streams.values():
                if stream.dirty:
                    stream.save()
            for client in self.clients.values():
                print(f"client {client.id} {client.mac}: " + summary(client, "last "))
            if self.args.drop:
                print(f"dropped {self.lost['in']} packets in, {self.lost['out']} out")
            return 0
        finally:
            if previous is not None:
                signal.signal(signal.SIGINT, previous)

    def receive(self, ready):
        """Handle one socket select found readable."""
        if ready is self.admin_sock:
            try:
                line, addr = self.admin_sock.recvfrom(2048)
            except ConnectionResetError:
                return
            reply = self.admin(line.decode("utf-8", "replace"))
            if not quiet_admin(line):
                print(f"{time.strftime('%H:%M:%S')} admin: {wire_text(line)}: {reply}",
                      flush=True)
            self.admin_sock.sendto(reply.encode("utf-8"), addr)
            return
        if self.remote_admin and ready is self.remote_admin.sock:
            try:
                data, addr = self.remote_admin.sock.recvfrom(2048)
            except ConnectionResetError:
                return

            def run(line, addr=addr):
                reply = self.admin(line)
                if not quiet_admin(line.encode("utf-8")):
                    print(f"{time.strftime('%H:%M:%S')} remote admin from {addr[0]}: "
                          f"{wire_text(line.encode('utf-8'))}: {reply}", flush=True)
                return reply

            reply = self.remote_admin.handle(data, addr, run)
            if reply:
                self.remote_admin.sock.sendto(reply, addr)
            return
        if ready is self.dns:
            try:
                query, addr = self.dns.recvfrom(2048)
            except ConnectionResetError:
                return
            reply = dns_reply(query, self.hosts)
            if reply:
                print(f"{time.strftime('%H:%M:%S')} dns query from {addr[0]}: "
                      f"{'answered' if reply[7] else 'unknown name'}", flush=True)
                self.dns.sendto(reply, addr)
            return
        if ready is self.sock:
            try:
                data, addr = self.sock.recvfrom(2048)
            except ConnectionResetError:
                return
            self.guarded(data, addr)
            return
        forwarding = next((link for link in self.links if link.forward is ready), None)
        if forwarding:
            try:
                data, address = forwarding.forward.recvfrom(2048)
            except ConnectionResetError:
                return
            guest = forwarding.forward_guest.get(address[1])
            if guest:
                mac, guest_ip, guest_port = guest
                host_port = address[1]
                forwarding.send(udp_frame(mac, guest_ip, data, sport=host_port,
                                          dport=guest_port))
            return
        link = next(link for link in self.links if link.sock is ready)
        frame = link.recv(0)
        if frame and frame[12:14] == b"\x08\x06" and len(frame) >= 42:
            op, sha, spa, _, tpa = struct.unpack_from(">H6s4s6s4s", frame, 20)
            if op == 1 and tpa == socket.inet_aton(PEER_IP):
                link.send(arp_frame(2, sha, sha, socket.inet_ntoa(spa)))
        elif frame:
            src = socket.inet_ntoa(frame[26:30])
            request = udp_from_frame(frame, DHCP_SERVER)
            reply = dhcp_reply(request, self.args.dhcp_lease) if request else None
            if reply:
                print(f"{time.strftime('%H:%M:%S')} dhcp "
                      f"{'offer' if reply[242] == 2 else 'ack'} {GUEST_IP} to "
                      f"{frame[6:12].hex(':')}", flush=True)
                link.send(udp_frame(frame[6:12], "255.255.255.255", reply,
                                    sport=DHCP_SERVER, dport=DHCP_CLIENT))
                return
            query = udp_from_frame(frame, DNS_PORT) if self.hosts else None
            reply = dns_reply(query, self.hosts) if query else None
            if reply:
                print(f"{time.strftime('%H:%M:%S')} dns query from {src}: "
                      f"{'answered' if reply[7] else 'unknown name'}", flush=True)
                link.send(udp_frame(frame[6:12], src, reply, sport=DNS_PORT))
                return
            udp = 14 + (frame[14] & 0x0F) * 4 if len(frame) >= 42 else 0
            sport = (struct.unpack_from(">H", frame, udp)[0] if udp and frame[23] == 17
                     else 0)
            data = udp_from_frame(frame, self.args.port)
            if data:
                self.guarded(data, (src, sport, frame[6:12], link))
                return
            if udp and frame[23] == 17:
                sport, dport = struct.unpack_from(">HH", frame, udp)
                data = udp_from_frame(frame, dport)
                if data is not None and dport in self.args.forward_ports:
                    link.forward_guest[dport] = (frame[6:12], src, sport)
                    link.forward.sendto(data, ("127.0.0.1", dport))

    def tick(self, now):
        """What the loop does on time rather than on a packet, once per pass."""
        for client in self.clients.values():
            if client.queue:
                self.pump(client)
            if client.alive and client.in_world and client.announce_due:
                self.announce_join(client, now)
            if client.alive and now - client.last > self.args.idle_timeout:
                print(f"{time.strftime('%H:%M:%S')} client {client.id} timed out", flush=True)
                self.leave(client)
        if self.world.path and \
                now >= self.world.saved + (10 if self.world.dirty else 60):
            self.world.save(now)
        if now >= self.streams_saved + 2:
            self.streams_saved = now
            for stream in self.streams.values():
                if stream.dirty:
                    stream.save()
        self.bot.move(now)
        if now >= self.authority_next:
            self.authority_next = now + AUTHORITY_PERIOD
            self.update_authority(now)
        self.bot.act(now)
        for client in [c for c in self.clients.values() if c.alive and c.bursts]:
            if now - client.joined >= client.bursts[0][0]:
                _, count = client.bursts.pop(0)
                print(f"{time.strftime('%H:%M:%S')} burst of {count} heartbeats to client "
                      f"{client.id}", flush=True)
                for _ in range(count):
                    self.send(client, HEARTBEAT)
                    client.queue, queued = [], client.queue
                    for addr, packet, seq in queued:
                        self.transmit(addr, packet, seq)
        for client in [c for c in self.clients.values() if c.alive and c.bulk]:
            for index in client.bulk.due(now):
                self.send(client, CHUNK, client.bulk.chunk(index))
        for client in [c for c in self.clients.values() if c.alive and c.upload]:
            if client.upload.status == BULK_RECEIVING and \
                    now - client.upload.acked >= BULK_ACK_EVERY:
                self.send(client, BULK_ACK, client.upload.ack(now))
        for client in self.clients.values():
            if client.alive and (client.flush_due or client.rel.out and
                                 now - client.rel.last_send >= RESEND):
                self.flush(client, now)
        for client in self.clients.values():
            if client.alive and client.rebuild and client.rebuild[1] and \
                    now >= client.rebuild[1]:
                client.rebuild = (client.rebuild[0], None)
                print(f"{time.strftime('%H:%M:%S')} asked client {client.id} for its rebuilt "
                      f"character: {self.ask_save([client], now, diagnostic=True)}", flush=True)
        if now >= self.save_next:
            self.save_next = now + self.args.save_every
            if any(c.alive for c in self.clients.values()):
                print(f"{time.strftime('%H:%M:%S')} asked to save: "
                      f"{self.ask_save(list(self.clients.values()), now, diagnostic=self.args.adopt)}",
                      flush=True)
        if self.world.clock and now >= self.clock_next:
            self.clock_next = now + CLOCK_INTERVAL
            body = self.world.clock.body(now)
            for client in self.clients.values():
                if client.alive and not client.lobby:
                    self.send(client, CLOCK, body)
        if self.args.report and now >= self.report_next:
            self.report_next = now + self.args.report
            print(self.status(now), flush=True)


def serve(args):
    return Server(args).run()


def load_admin_password(path):
    """The first line of an admin password file, as bytes."""
    with open(path, encoding="utf-8") as stream:
        password = stream.readline().strip()
    if len(password) < ADMIN_PASSWORD_MIN or not password.isprintable():
        sys.exit(f"{path}: the admin password must be at least {ADMIN_PASSWORD_MIN} printable "
                 "characters")
    return password.encode("utf-8")


class RemoteAdmin:
    """The server's remote admin listener; run(line) answers an authenticated command."""

    def __init__(self, secret, sock, clock=time.monotonic):
        self.secret, self.sock, self.clock = secret, sock, clock
        self.nonces = {}  # nonce -> (address, issued)
        self.failures = SourceBuckets(*PASSWORD_RATE)

    def handle(self, data, addr, run):
        """The reply datagram for one request, or None; run(line) executes a command."""
        now = self.clock()
        if len(data) < REMOTE_HEAD.size:
            return None
        magic, version, kind = REMOTE_HEAD.unpack_from(data)
        if magic != REMOTE_MAGIC or version != REMOTE_VERSION:
            return None
        for nonce in [n for n, (_, at) in self.nonces.items()
                      if now - at > REMOTE_NONCE_SECONDS]:
            del self.nonces[nonce]
        if kind == REMOTE_HELLO:
            if len(self.nonces) >= REMOTE_NONCES:
                return self.refuse(b"", "busy")
            nonce = os.urandom(REMOTE_NONCE)
            self.nonces[nonce] = (addr, now)
            return REMOTE_HEAD.pack(REMOTE_MAGIC, REMOTE_VERSION, REMOTE_CHALLENGE) + nonce
        if kind != REMOTE_COMMAND or len(data) < REMOTE_HEAD.size + REMOTE_NONCE + 16:
            return None
        bucket = self.failures.get(addr[0], now)
        nonce = data[REMOTE_HEAD.size:REMOTE_HEAD.size + REMOTE_NONCE]
        issued = self.nonces.pop(nonce, None)
        if bucket is None or not bucket.available(now):
            # Not even tried: a right guess while slowed would otherwise still get through.
            return self.refuse(nonce, "slow down")
        head = data[:REMOTE_HEAD.size + REMOTE_NONCE]
        line = None
        if issued and issued[0] == addr:
            line = unseal(remote_key(self.secret, nonce), 0, head,
                          data[REMOTE_HEAD.size + REMOTE_NONCE:])
        if line is None:
            # Only failures spend tries, so polling with the right password is never slowed.
            bucket.take(now)
            return self.refuse(nonce, "unauthorized")
        reply = run(line.decode("utf-8", "replace")).encode("utf-8")
        head = REMOTE_HEAD.pack(REMOTE_MAGIC, REMOTE_VERSION, REMOTE_REPLY) + nonce
        return head + seal(remote_key(self.secret, nonce), 1, head, reply)

    @staticmethod
    def refuse(nonce, why):
        return (REMOTE_HEAD.pack(REMOTE_MAGIC, REMOTE_VERSION, REMOTE_REFUSED) + nonce
                + why.encode("ascii"))


def summary(client, prefix=""):
    rel = client.rel
    return (f"joins {client.joins}, heartbeats {client.beats}, states {client.states}, "
            f"actor states {client.actor_states}, "
            f"gaps {client.gaps}, events in {client.events} (stale {rel.stale}), out "
            f"{rel.out_next - 1} (sent {rel.sent}, resent {rel.resent}, unacked {len(rel.out)}), "
            f"dropped forged {client.forged}, replayed {client.replayed}, "
            f"over rate {client.limited}"
            + (f"; {prefix}{describe_state(client.state)}" if client.state else ""))
