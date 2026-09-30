import socket
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import tes3x_net
from tes3x_records import records, subrecords


def query(name, qtype=1):
    labels = b''.join(bytes([len(part)]) + part.encode() for part in name.split('.'))
    return struct.pack('>HHHHHH', 0x1235, 0x0100, 1, 0, 0, 0) + labels + b'\0' + \
        struct.pack('>HH', qtype, 1)


class DnsReplyTests(unittest.TestCase):
    def test_known_name_gets_one_a_record(self):
        reply = tes3x_net.dns_reply(query('mw.test'), {'mw.test': '10.0.2.2'})
        ident, flags, questions, answers = struct.unpack_from('>HHHH', reply)
        self.assertEqual((ident, flags & 0x800F, questions, answers), (0x1235, 0x8000, 1, 1))
        self.assertEqual(reply[-4:], socket.inet_aton('10.0.2.2'))

    def test_unknown_name_and_other_types_are_nxdomain(self):
        for reply in (tes3x_net.dns_reply(query('other.test'), {'mw.test': '10.0.2.2'}),
                      tes3x_net.dns_reply(query('mw.test', 28), {'mw.test': '10.0.2.2'})):
            self.assertEqual(struct.unpack_from('>HH', reply, 2)[0] & 0x000F, 3)
            self.assertEqual(struct.unpack_from('>H', reply, 6)[0], 0)

    def test_truncated_query_is_ignored(self):
        self.assertIsNone(tes3x_net.dns_reply(query('mw.test')[:-3], {'mw.test': '10.0.2.2'}))


class GhostPluginTests(unittest.TestCase):
    def test_one_persistent_ghost_per_peer_slot_in_the_parking_cell(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / tes3x_net.GHOST_PLUGIN
            path.write_bytes(tes3x_net.ghost_plugin(12345))
            found = list(records(path))
        self.assertEqual(found[0][0], b'TES3')
        header = dict(subrecords(found[0][2]))
        self.assertEqual(struct.unpack('<Q', header[b'DATA'])[0], 12345)
        self.assertEqual(struct.unpack_from('<I', header[b'HEDR'], 296)[0], len(found) - 1)
        npcs = [(flags, dict(subrecords(body))) for tag, flags, body in found if tag == b'NPC_']
        self.assertEqual(len(npcs), tes3x_net.GHOSTS)
        self.assertTrue(all(flags & 0x400 and subs[b'AIDT'] == bytes(12) for flags, subs in npcs))
        cell = list(subrecords(found[-1][2]))
        self.assertEqual(cell[0][1], tes3x_net.GHOST_CELL.encode() + b'\0')
        refs = [value for tag, value in cell if tag == b'NAME'][1:]
        self.assertEqual(refs, [b'tes3x_ghost%d\0' % i for i in range(1, tes3x_net.GHOSTS + 1)])


class LoadOrderTests(unittest.TestCase):
    def test_hash_ignores_case_but_not_order(self):
        names = ['Morrowind.esm', 'Tribunal.esm', 'Bloodmoon.esm']
        self.assertEqual(tes3x_net.load_order_hash(names),
                         tes3x_net.load_order_hash([n.upper() for n in names]))
        self.assertNotEqual(tes3x_net.load_order_hash(names),
                            tes3x_net.load_order_hash(names[::-1]))
        self.assertNotEqual(tes3x_net.load_order_hash(['ab', 'c']),
                            tes3x_net.load_order_hash(['a', 'bc']))

    def test_hash_is_fnv1a_with_terminators(self):
        self.assertEqual(tes3x_net.load_order_hash([]), 0x811C9DC5)
        self.assertEqual(tes3x_net.load_order_hash(['a']), 0x2B24D044)


class EventChannelTests(unittest.TestCase):
    def test_pack_round_trip_and_limit(self):
        events = [(i, 1, 7, b'x' * 60) for i in range(1, 20)]
        body = tes3x_net.pack_events(5, events)
        self.assertLessEqual(len(body), tes3x_net.EVENTS_BYTES)
        ack, got = tes3x_net.unpack_events(body)
        self.assertEqual(ack, 5)
        self.assertEqual(got, events[:len(got)])
        self.assertEqual(len(got), (512 - 8) // 72)

    def test_in_order_delivery_under_loss(self):
        import random
        loss = random.Random(3)
        a, b = tes3x_net.Reliable(), tes3x_net.Reliable()
        for i in range(40):
            a.queue(1, 0, b'%d' % i)
        got = []
        for tick in range(400):
            body = a.packet(tick)
            if loss.random() < 0.4:
                continue
            ready, carried = b.receive(body)
            got += [data for _, _, _, data in ready]
            if carried and loss.random() >= 0.4:
                a.receive(b.packet(tick, resend=False))
        self.assertEqual(got, [b'%d' % i for i in range(40)])
        self.assertEqual(a.out, [])
        self.assertGreater(a.resent, 0)


class EquipmentTests(unittest.TestCase):
    def test_parts_fit_an_event_and_round_trip(self):
        ids = ['glass_cuirass', 'glass_helm', 'daedric longsword', 'x' * 40] + \
            ['common_shirt_%02d' % i for i in range(8)]
        parts = tes3x_net.pack_equipment(ids)
        self.assertTrue(all(len(p) <= tes3x_net.EVENT_DATA for p in parts))
        self.assertEqual([p[:2] for p in parts],
                         [bytes((i, len(parts))) for i in range(len(parts))])
        got = [item for p in parts for item in tes3x_net.unpack_equipment(p)]
        self.assertEqual(got, ids[:3] + ['x' * 31] + ids[4:])

    def test_nothing_worn_is_one_empty_part(self):
        self.assertEqual(tes3x_net.pack_equipment([]), [b'\x00\x01'])


class ClockTests(unittest.TestCase):
    def test_advances_at_timescale_and_rolls_the_calendar(self):
        clock = tes3x_net.Clock(23.0, 31, 11, 427, 100, 30.0, now=0.0)
        clock.advance(3600 / 30 * 2)  # two game hours
        self.assertAlmostEqual(clock.hour, 1.0)
        self.assertEqual((clock.day, clock.month, clock.year, clock.days_passed),
                         (1, 0, 428, 101))
        clock = tes3x_net.Clock(12.0, 28, 1, 427, 0, 30.0, now=0.0)
        clock.advance(3600 / 30 * 24)
        self.assertEqual((clock.day, clock.month), (1, 2))

    def test_body_is_six_floats(self):
        body = tes3x_net.Clock(9.5, 16, 7, 427, 1, 30.0, now=0.0).body(0.0)
        self.assertEqual(tes3x_net.CLOCK_BODY.unpack(body), (9.5, 16, 7, 427, 1, 30.0))
        self.assertEqual(tes3x_net.HELLO_BODY.size, 18 + tes3x_net.CLOCK_BODY.size)


class AuthorityTests(unittest.TestCase):
    def state(self, x, y, cell=b''):
        flags = tes3x_net.IN_WORLD | (tes3x_net.INTERIOR if cell else 0)
        return tes3x_net.STATE_BODY.pack(flags, x, y, 0.0, 0.0, cell)

    def test_exteriors_load_three_by_three_and_interiors_one(self):
        own, loaded = tes3x_net.cell_keys(self.state(-1.0, 8192.0))
        self.assertEqual(own, (tes3x_net.KEY_EXTERIOR, -1, 1, b''))
        self.assertEqual(len(loaded), 9)
        self.assertIn((tes3x_net.KEY_EXTERIOR, -2, 0, b''), loaded)
        own, loaded = tes3x_net.cell_keys(self.state(0.0, 0.0, b'Seyda Neen, Census'))
        self.assertEqual(loaded, {own})
        self.assertEqual(tes3x_net.cell_keys(tes3x_net.STATE_BODY.pack(0, 0, 0, 0, 0, b'')),
                         (None, set()))

    def test_authority_is_kept_until_someone_stands_in_a_cell_it_only_loads(self):
        cell = (tes3x_net.KEY_EXTERIOR, 0, 0, b'')
        assign = tes3x_net.assign_authority
        self.assertEqual(assign({}, {cell: [(1, False), (2, True)]}), {cell: 2})
        self.assertEqual(assign({cell: 2}, {cell: [(1, True), (2, True)]}), {cell: 2})
        self.assertEqual(assign({cell: 1}, {cell: [(1, False), (2, False)]}), {cell: 1})
        self.assertEqual(assign({cell: 1}, {cell: [(1, False), (2, True)]}), {cell: 2})
        self.assertEqual(assign({cell: 3}, {cell: [(1, False), (2, False)]}), {cell: 1})
        self.assertEqual(assign({cell: 1}, {cell: [(1, True), (99, False)]}, forced=99),
                         {cell: 99})

    def test_authority_event_fits_the_event_channel(self):
        data = tes3x_net.KEY.pack(tes3x_net.KEY_INTERIOR, 0, 0, b'Seyda Neen, Census') + \
            struct.pack('<I', 1)
        self.assertLessEqual(len(data), tes3x_net.EVENT_DATA)
        self.assertEqual(4 + tes3x_net.ACTORS_PER_PACKET * tes3x_net.ACTOR.size,
                         484)  # within tes3xnet.c's EVENTS_BYTES


if __name__ == '__main__':
    unittest.main()
