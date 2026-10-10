"""Accepted client event shapes; validate before dispatch or relay."""

import struct

from . import proto as p


def strings(data, maximum=31, empty=False):
    values = data.split(b"\0")
    return (not values[-1] and all((empty or value) and len(value) <= maximum
            and all(byte >= 32 and byte != 127 for byte in value) for value in values[:-1]))


def multipart(data, offset=0):
    return len(data) >= offset + 2 and 0 <= data[offset] < data[offset + 1]


def entries(data, names):
    off = 0
    while off < len(data):
        if off + p.ENTRY.size > len(data):
            return False
        _, flags = p.ENTRY.unpack_from(data, off)
        off += p.ENTRY.size
        if flags & ~p.ENTRY_DATA:
            return False
        off += 8 if flags & p.ENTRY_DATA else 0
        if off > len(data):
            return False
        if names:
            end = data.find(b"\0", off)
            if end < off or not strings(data[off:end + 1]):
                return False
            off = end + 1
    return off == len(data)


def player(data):
    if not data:
        return False
    kind = data[0]
    if kind in (p.PLAYER_DEATH, p.PLAYER_ALIVE):
        return len(data) == 1
    if kind == p.PLAYER_ITEMS:
        end = data.find(b"\0", 3)
        return (multipart(data, 1) and end >= 3 and strings(data[3:end + 1])
                and entries(data[end + 1:], False))
    if kind == p.PLAYER_LEVEL:
        return len(data) == 1 + p.LEVEL.size and p.finite(*p.LEVEL.unpack(data[1:])[13:])
    if kind in (p.PLAYER_SKILLS, p.PLAYER_MODIFIERS):
        record = p.SKILL if kind == p.PLAYER_SKILLS else p.MODIFIER
        limit = len(p.SKILL_NAMES) if kind == p.PLAYER_SKILLS else 35
        return (len(data) >= 2 and len(data) == 2 + data[1] * record.size
                and all(values[0] < limit and p.finite(*values[1:])
                        for values in record.iter_unpack(data[2:])))
    if kind == p.PLAYER_VITALS:
        return len(data) == 1 + p.VITALS.size and p.finite(*p.VITALS.unpack(data[1:]))
    if kind == p.PLAYER_JOURNAL:
        if len(data) < 2:
            return False
        off = 2
        for _ in range(data[1]):
            end = data.find(b"\0", off + 2)
            if end < off + 2 or not strings(data[off + 2:end + 1]):
                return False
            off = end + 1
        return off == len(data)
    if kind == p.PLAYER_SPELLS:
        return (len(data) >= 4 and data[1] in (p.SPELLS_ADD, p.SPELLS_REMOVE)
                and multipart(data, 2) and strings(data[4:]))
    if kind == p.PLAYER_TOPICS:
        return (len(data) >= 2 and data[2:].count(b"\0") == data[1]
                and strings(data[2:], p.TOPIC_NAME_MAX) and b'"' not in data[2:])
    if kind in (p.PLAYER_IDENTITY, p.PLAYER_WORN):
        return multipart(data, 1) and (kind == p.PLAYER_WORN or len(data) > 3)
    if kind == p.PLAYER_EFFECTS:
        if len(data) < 5:
            return False
        part, parts = struct.unpack_from('<HH', data, 1)
        maximum = (p.PLAYER_EFFECT_BYTES * p.PLAYER_EFFECTS_MAX + 74) // 75
        return part < parts <= maximum
    return False


def valid_client_event(kind, data):
    """Unknown and server-only kinds are rejected, including PLAYER sub-kinds."""
    if len(data) > p.EVENT_DATA or kind in p.SERVER_EVENTS:
        return False
    if kind == p.EVENT_TEXT:
        return True
    if kind == p.EVENT_PLAYER:
        return player(data)
    if kind in (p.EVENT_HIT, p.EVENT_PLAYER_HIT):
        return len(data) in (12, 16) and p.finite(
            *struct.unpack('<' + 'f' * ((len(data) - 8) // 4), data[8:]))
    if kind in (p.EVENT_HOLD, p.EVENT_HOLD_BROKEN):
        return len(data) == 12 and struct.unpack_from('<I', data, 8)[0] <= (
            1 if kind == p.EVENT_HOLD else 3)
    if kind == p.EVENT_DEATH:
        return len(data) == 4
    if kind == p.EVENT_STATUS:
        return len(data) == p.STATUS.size
    if kind == p.EVENT_BOUNTY:
        return len(data) == 4
    if kind == p.EVENT_BUSY:
        return data in (b'\0', bytes([p.BUSY_SAVING]))
    if kind in (p.EVENT_SPELL, p.EVENT_CAST):
        return (len(data) > p.SPELL.size and data[12] == p.SOURCE_SPELL
                and data[13] <= 1 and strings(data[p.SPELL.size:])
                and len(data[p.SPELL.size:].split(b'\0')) == 2)
    if kind == p.EVENT_SHOT:
        return (len(data) > p.SHOT.size and data[12] <= 1
                and p.finite(*struct.unpack_from('<ff', data, 4))
                and strings(data[p.SHOT.size:]) and data[p.SHOT.size:].count(0) == 1)
    if kind == p.EVENT_AFFECT:
        return (len(data) > 5 and data[4] < 8 and strings(data[5:])
                and data[5:].count(0) == 1)
    if kind == p.EVENT_EQUIPMENT:
        return multipart(data) and strings(data[2:])
    if kind in (p.EVENT_IDENTITY, p.EVENT_ACTOR_EQUIPMENT):
        try:
            if kind == p.EVENT_IDENTITY:
                p.unpack_identity(data)
                return data[2] <= 1
            p.unpack_actor_equipment(data)
            return True
        except ValueError:
            return False
    if kind == p.EVENT_OBJECTS:
        return (bool(data) and len(data) == 1 + data[0] * p.OBJECT.size
                and all(state & ~15 == 0 for _, _, state, _ in p.OBJECT.iter_unpack(data[1:])))
    if kind in (p.EVENT_REMOVE, p.EVENT_WANT):
        return bool(data) and len(data) == 1 + data[0] * (4 if kind == p.EVENT_REMOVE else 2)
    if kind == p.EVENT_WEATHER:
        return (len(data) >= 2 and data[0] & ~p.WEATHER_OFFER == 0
                and len(data) == 2 + data[1] * p.WEATHER_ENTRY.size
                and all(weather < len(p.WEATHERS)
                        for _, weather in p.WEATHER_ENTRY.iter_unpack(data[2:])))
    if kind == p.EVENT_SPAWN:
        if len(data) <= p.SPAWN.size:
            return False
        values = p.SPAWN.unpack_from(data)
        off = p.SPAWN.size + (4 if values[2] & p.SPAWN_LEVELED else 0)
        return (p.placeable(*values[3:6]) and p.finite(*values[6:9])
                and strings(data[off:]) and data[off:].count(0) == 1)
    if kind == p.EVENT_CONTENTS:
        return (len(data) >= p.CONTENTS_HEAD.size and multipart(data, 6)
                and data[8] & ~p.CONTENTS_ROLLED == 0
                and entries(data[p.CONTENTS_HEAD.size:], True))
    if kind == p.EVENT_OFFER:
        return (len(data) > p.BULK_OFFER.size and strings(data[p.BULK_OFFER.size:],
                                                       p.BULK_NAME - 1)
                and data[p.BULK_OFFER.size:].count(0) == 1)
    if kind == p.EVENT_GAME:
        end = data.find(b'\0', 4)
        return (end >= 4 and strings(data[4:end + 1], p.BULK_NAME - 1, True)
                and (len(data) == end + 1 or
                     (len(data) == end + 2 and data[-1] in (p.GAME_NONE, p.GAME_LOAD, p.GAME_NEW))))
    if kind == p.EVENT_PICK:
        return len(data) == 2 and data[0] in (p.PICK_CHARACTER, p.PICK_START, p.PICK_NEW)
    if kind == p.EVENT_SNAPSHOT:
        return (len(data) == 4 + p.STATE_BODY.size
                and p.finite(*p.STATE_BODY.unpack(data[4:])[1:5]))
    return False
