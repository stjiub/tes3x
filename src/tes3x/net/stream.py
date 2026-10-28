"""Per-player state stream: decoding what a client sends into the world."""

import json
import struct
from .proto import (ATTRIBUTE_NAMES, CHARACTER_MAGIC, describe_contents, describe_faction,
                    describe_identity, describe_level, EVENT_DATA, FACTION_EXPELLED, FACTION_JOINED,
                    FACTIONS_MAX, finite, LEVEL, MODIFIER, pack_equipment, pack_globals, pack_items,
                    pack_journal, pack_mark, pack_player_effects, pack_player_identity, pack_topics,
                    pack_worn, PLAYER_BOUNTY, PLAYER_EFFECT_BYTES, PLAYER_EFFECTS,
                    PLAYER_EFFECTS_MAX, PLAYER_FACTIONS, PLAYER_GLOBALS, PLAYER_GLOBALS_MAX,
                    PLAYER_IDENTITY, PLAYER_ITEMS, PLAYER_JOURNAL, PLAYER_LEVEL, PLAYER_MARK,
                    PLAYER_MODIFIERS, PLAYER_PLACE, PLAYER_SKILLS, PLAYER_SPELLS, PLAYER_TOPICS,
                    PLAYER_TOPICS_MAX, PLAYER_VITALS, PLAYER_WORN, QUEST_INDICES, same_place, SKILL,
                    SKILL_NAMES, SPELLS_ADD, SPELLS_REMOVE, SPELLS_SNAPSHOT, unpack_equipment,
                    unpack_factions, unpack_globals, unpack_items, unpack_journal, unpack_mark,
                    unpack_player_effects, unpack_player_identity, unpack_worn, VITALS,
                    WORLD_GLOBALS)
from .state import save_world


class PlayerStream:
    """One character's state as its console streamed it: each item's stacks, the level block,
    each skill and current modifier, active effects, quest indices and known spells. It holds
    what changed since the character's first launch on this server and replays it when the
    character joins."""

    def __init__(self, path):
        self.path = path
        self.items, self.skills, self.modifiers, self.journal, self.level = {}, {}, {}, {}, None
        self.spells = self.topics = self.factions = self.globals = None
        self.effects = self.effects_parts = None
        self.vitals = self.place = None
        self.bounty = self.identity = self.worn = self.mark = None
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
        self.topics = kept.get("topics")
        self.factions = kept.get("factions")
        self.globals = kept.get("globals")
        self.effects = kept.get("effects")
        self.bounty = kept.get("bounty")
        self.identity = kept.get("identity")
        self.worn = kept.get("worn")
        self.mark = kept.get("mark")

    def reset(self):
        """A new character: nothing streamed so far belongs to it."""
        self.items, self.skills, self.modifiers, self.journal, self.level = {}, {}, {}, {}, None
        self.spells = self.topics = self.factions = self.globals = None
        self.effects = self.effects_parts = None
        self.vitals = self.place = self.arriving = None
        self.bounty = self.identity = self.identity_parts = None
        self.worn = self.worn_parts = self.mark = None
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
        if kind == PLAYER_TOPICS:
            from .validation import player
            if not player(data):
                return None
            names = [raw.decode("latin-1") for raw in data[2:].split(b"\0") if raw]
            known = {name.lower(): name for name in (self.topics or [])}
            added = [name for name in names if name.lower() not in known]
            if len(set(known) | {name.lower() for name in names}) > PLAYER_TOPICS_MAX:
                return None
            if self.topics is None or added:
                known.update((name.lower(), name) for name in names)
                self.topics, self.dirty = list(known.values()), True
            return "topics " + ", ".join(added) if added else None
        if kind == PLAYER_FACTIONS:
            try:
                entries = unpack_factions(data)
            except ValueError:
                return None
            kept = dict(self.factions or {})
            changed = []
            for faction, bits, rank, reputation in entries:
                value = [faction, rank, reputation, bits & (FACTION_JOINED | FACTION_EXPELLED)]
                if kept.get(faction.lower()) != value:
                    kept[faction.lower()] = value
                    changed.append(describe_faction(*value))
            if len(kept) > FACTIONS_MAX:
                return None
            if self.factions is None or changed:
                self.factions, self.dirty = kept, True
            return "factions " + ", ".join(changed) if changed else None
        if kind == PLAYER_MARK:
            try:
                mark = list(unpack_mark(data))
            except ValueError:
                return None
            if mark == self.mark:
                return None
            self.mark, self.dirty = mark, True
            return "mark " + (mark[7] or f"({mark[1]}, {mark[2]})" if mark[0] else "cleared")
        if kind == PLAYER_GLOBALS:
            try:
                entries = unpack_globals(data)
            except ValueError:
                return None
            kept = dict(self.globals or {})
            changed = []
            for name, value in entries:
                if (kept.get(name.lower()) or [None, None])[1] != value:
                    kept[name.lower()] = [name, value]
                    changed.append(f"{name} {value:g}")
            if len(kept) > PLAYER_GLOBALS_MAX:
                return None
            if self.globals is None or changed:
                self.globals, self.dirty = kept, True
            return "globals " + ", ".join(changed) if changed else None
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
        if self.globals:
            events += pack_globals(sorted(self.globals.values()))
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
        if self.mark is not None:
            events.append(pack_mark(*self.mark))
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
        if self.topics is not None:
            events += pack_topics(self.topics)
        return events

    def character_file(self, world_globals=()):
        """What a New Game builds the player from before its first frame; None without an
        identity. The rest comes with the replay after the console joins."""
        if not self.identity:
            return None
        events = pack_player_identity(self.identity)
        events += [part for item, entries in sorted(self.items.items())
                   for part in pack_items(item, entries)]
        if self.worn is not None:
            events += pack_worn(self.worn)
        if self.globals:  # before the global scripts the [PreLoad] read starts
            events += pack_globals(sorted(self.globals.values()))
        events += pack_globals(world_globals, WORLD_GLOBALS)
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
                               "topics": self.topics,
                               "factions": self.factions,
                               "globals": self.globals,
                               "effects": self.effects,
                               "bounty": self.bounty,
                               "identity": self.identity,
                               "worn": self.worn,
                               "mark": self.mark,
                               "dead": self.dead})
        self.dirty = False
