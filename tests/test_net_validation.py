import random
import struct
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from tes3x.net import proto as p
from tes3x.net.server import Server
from tes3x.net.validation import valid_client_event


class EventValidationTests(unittest.TestCase):
    def valid_events(self):
        spawn = dict(cell=1, count=1, removed=False, pos=[1., 2., 3.], rot=[0., 0., 0.], id='gold')
        return {
            p.EVENT_TEXT: b'hello',
            p.EVENT_HOLD: struct.pack('<III', 1, 2, 1),
            p.EVENT_HOLD_BROKEN: struct.pack('<III', 1, 2, 3),
            p.EVENT_HIT: struct.pack('<IIff', 1, 2, 3., 4.),
            p.EVENT_PLAYER_HIT: struct.pack('<IIf', 0, 2, 3.),
            p.EVENT_DEATH: struct.pack('<I', 1),
            p.EVENT_STATUS: p.STATUS.pack(1, 2, 3, 4, 5, 6),
            p.EVENT_BOUNTY: struct.pack('<i', 42),
            p.EVENT_BUSY: b'\1',
            p.EVENT_SPELL: p.SPELL.pack(1, 2, 3, p.SOURCE_SPELL, 1) + b'fireball\0',
            p.EVENT_CAST: p.SPELL.pack(1, 0, 0, p.SOURCE_SPELL, 1) + b'fireball\0',
            p.EVENT_SHOT: p.SHOT.pack(1, 2., 3., 1) + b'arrow\0',
            p.EVENT_AFFECT: struct.pack('<IB', 1, 2) + b'fireball\0',
            p.EVENT_EQUIPMENT: p.pack_equipment(['iron cuirass'])[0],
            p.EVENT_IDENTITY: p.pack_identity('Nerevar', 'Imperial', 'head', 'hair')[0],
            p.EVENT_ACTOR_EQUIPMENT: p.pack_actor_equipment(1, ['iron cuirass'])[0],
            p.EVENT_OBJECTS: p.pack_objects({1: (2, p.OBJECT_DISABLED, 0)})[0],
            p.EVENT_REMOVE: p.pack_removes([1])[0],
            p.EVENT_WANT: b'\1\1\0',
            p.EVENT_WEATHER: p.pack_weather({1: 2})[0],
            p.EVENT_SPAWN: p.pack_spawn(1, spawn),
            p.EVENT_CONTENTS: p.pack_contents(1, 2, [['gold', 3, 1, 4, 5]])[0],
            p.EVENT_OFFER: p.BULK_OFFER.pack(1, 2, bytes(32)) + b'save.ess\0',
            p.EVENT_GAME: struct.pack('<I', 1) + b'\0' + bytes([p.GAME_NEW]),
            p.EVENT_PICK: bytes([p.PICK_NEW, 0]),
            p.EVENT_SNAPSHOT: struct.pack('<I', 1) + p.STATE_BODY.pack(1, 2., 3., 4., 5., b''),
            p.EVENT_PLAYER: bytes([p.PLAYER_DEATH]),
        }

    def test_every_handler_has_an_accepted_shape(self):
        events = self.valid_events()
        self.assertEqual(set(events), set(Server.EVENT_HANDLERS))
        for kind, data in events.items():
            with self.subTest(kind=kind):
                self.assertTrue(valid_client_event(kind, data))
                self.assertFalse(valid_client_event(kind, data + bytes(p.EVENT_DATA)))

    def test_malformed_events_have_no_side_effects_or_relay(self):
        server = object.__new__(Server)
        handler = Mock()
        server.EVENT_HANDLERS = dict.fromkeys(Server.EVENT_HANDLERS, handler)
        server.broadcast_event = Mock()
        client = SimpleNamespace(id=1, events=0)
        bad = [(200, b'anything'), (65535, b'')]
        for kind, data in self.valid_events().items():
            if kind != p.EVENT_TEXT:
                bad.append((kind, b''))
                if kind not in (p.EVENT_PLAYER, p.EVENT_BUSY, p.EVENT_GAME):
                    bad.append((kind, data[:-1]))
        bad += [
            (p.EVENT_CAST, p.SPELL.pack(1, 2, 3, 1, 2) + b'spell\0'),
            (p.EVENT_CAST, p.SPELL.pack(1, 2, 3, 1, 1) + b'spell\0extra\0'),
            (p.EVENT_SHOT, p.SHOT.pack(1, float('nan'), 0., 1) + b'arrow\0'),
            (p.EVENT_HIT, struct.pack('<IIf', 1, 2, float('inf'))),
            (p.EVENT_WEATHER, b'\0\1\1\0\xff'),
            (p.EVENT_EQUIPMENT, b'\2\1item\0'),
            (p.EVENT_CONTENTS, p.CONTENTS_HEAD.pack(1, 2, 0, 1, 0) + p.ENTRY.pack(1, 1)),
            (p.EVENT_PLAYER, bytes([p.PLAYER_SKILLS, 2]) + p.SKILL.pack(1, 2., 3.)),
            (p.EVENT_PLAYER, bytes([p.PLAYER_RESPAWN]) + bytes(p.RESPAWN.size)),
            (p.EVENT_PLAYER, bytes([p.PLAYER_TOPICS, 2]) + b'one\0'),
            (p.EVENT_PLAYER, bytes([p.PLAYER_TOPICS, 1]) + b'x' * 64 + b'\0'),
            (p.EVENT_PLAYER, bytes([p.PLAYER_TOPICS, 1]) + b'bad"name\0'),
            (p.EVENT_PLAYER, bytes([p.PLAYER_TOPICS, 1]) + b'bad\x7fname\0'),
        ]
        for kind, data in bad:
            with self.subTest(kind=kind, data=data):
                self.assertFalse(valid_client_event(kind, data))
                server.on_event(client, kind, data, '', 1.)
        handler.assert_not_called()
        server.broadcast_event.assert_not_called()

    def test_mutated_payloads_never_crash_validation(self):
        rng = random.Random(42)
        for kind in (*Server.EVENT_HANDLERS, 65535):
            for length in range(p.EVENT_DATA + 2):
                self.assertIsInstance(valid_client_event(kind, rng.randbytes(length)), bool)

    def test_player_records_accept_complete_counts_and_parts(self):
        records = [
            *p.pack_items('gold', [[1, 0, 0, 0], [2, 1, 3, 4]]),
            *p.pack_journal([('quest', 10)]),
            *p.pack_worn([]),
            *p.pack_topics(['a topic', 'x' * p.TOPIC_NAME_MAX]),
            *p.pack_topics([]),
            bytes([p.PLAYER_SKILLS, 1]) + p.SKILL.pack(26, 20., 3.),
            bytes([p.PLAYER_MODIFIERS, 1]) + p.MODIFIER.pack(34, 20.),
            bytes([p.PLAYER_VITALS]) + p.VITALS.pack(1., 2., 3.),
            bytes([p.PLAYER_SPELLS, p.SPELLS_ADD, 0, 1]) + b'spell\0',
            bytes([p.PLAYER_EFFECTS]) + struct.pack('<HH', 0, 1),
        ]
        for data in records:
            with self.subTest(data=data):
                self.assertTrue(valid_client_event(p.EVENT_PLAYER, data))
