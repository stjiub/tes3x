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


def dhcp_request(kind, mac=bytes.fromhex('020000002499')):
    return (bytes([1, 1, 6, 0]) + b'XID1' + bytes([0, 0, 0x80, 0]) + bytes(16) + mac
            + bytes(10 + 192) + tes3x_net.DHCP_MAGIC + bytes([53, 1, kind, 255]))


class DhcpReplyTests(unittest.TestCase):
    def options(self, reply):
        found, off = {}, 240
        while reply[off] != 255:
            found[reply[off]] = reply[off + 2:off + 2 + reply[off + 1]]
            off += 2 + reply[off + 1]
        return found

    def test_discover_gets_an_offer_and_request_an_ack(self):
        for kind, answer in ((1, 2), (3, 5)):
            reply = tes3x_net.dhcp_reply(dhcp_request(kind), 30)
            self.assertEqual(reply[4:8], b'XID1')
            self.assertEqual(reply[16:20], socket.inet_aton(tes3x_net.GUEST_IP))
            self.assertEqual(reply[28:34], bytes.fromhex('020000002499'))
            options = self.options(reply)
            self.assertEqual(options[53], bytes([answer]))
            self.assertEqual(options[51], struct.pack('>I', 30))
            self.assertEqual(options[1], bytes([255, 255, 255, 0]))

    def test_other_messages_are_ignored(self):
        self.assertIsNone(tes3x_net.dhcp_reply(dhcp_request(7), 30))
        self.assertIsNone(tes3x_net.dhcp_reply(dhcp_request(1)[:200], 30))


class ObjectTests(unittest.TestCase):
    def test_events_fit_and_round_trip(self):
        objects = {0x01000000 + i: (2433, i & 3, i) for i in range(20)}
        events = tes3x_net.pack_objects(objects)
        self.assertTrue(all(len(e) <= tes3x_net.EVENT_DATA for e in events))
        merged = {}
        for event in events:
            merged.update(tes3x_net.unpack_objects(event))
        self.assertEqual(merged, objects)

    def test_world_file_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / 'sub' / 'ef43975a.json')
            self.assertIsNone(tes3x_net.load_world(path))
            world = {'objects': {'17174802': [2433, 12, 30]}, 'deaths': {'16901060': 1}}
            tes3x_net.save_world(path, world)
            self.assertEqual(tes3x_net.load_world(path), world)
            self.assertFalse(Path(path + '.tmp').exists())


def spawn(name='misc_com_bottle_01', cell=2433, pos=(10.0, 20.0, 30.0), **extra):
    return dict({'cell': cell, 'count': 1, 'removed': False, 'pos': list(pos),
                 'rot': [0.0, 0.0, 1.5], 'id': name, 'data': False, 'condition': 0,
                 'charge': 0, 'leveled': 0, 'summon': False}, **extra)


class SpawnTests(unittest.TestCase):
    def test_a_longest_id_fits_the_event_and_round_trips(self):
        made = spawn('x' * 31, count=7, removed=True, data=True, condition=450,
                     charge=0x42C80000)
        data = tes3x_net.pack_spawn(0xFF000001, made)
        self.assertLessEqual(len(data), tes3x_net.EVENT_DATA)
        sid, back = tes3x_net.unpack_spawn(data)
        self.assertEqual(sid, 0xFF000001)
        self.assertEqual(back, made)

    def test_removes_fit_and_round_trip(self):
        sids = [0xFF000000 | i for i in range(1, 40)]
        events = tes3x_net.pack_removes(sids)
        self.assertTrue(all(len(e) <= tes3x_net.EVENT_DATA for e in events))
        self.assertEqual([s for e in events for s in tes3x_net.unpack_removes(e)], sids)

    def test_a_resend_is_known_by_its_token_and_a_twin_only_briefly(self):
        twin = tes3x_net.spawn_twin
        known = {7: spawn(origin=1, token=0x12340001, made=100.0)}
        self.assertEqual(twin(known, spawn(), 1, 0x12340001, 500.0), 7)
        self.assertIsNone(twin(known, spawn(), 1, 0x12340002, 100.0))  # another in the same place
        self.assertEqual(twin(known, spawn(pos=(18, 20, 30)), 2, 5, 101.0), 7)
        self.assertIsNone(twin(known, spawn(pos=(18, 20, 30)), 2, 5, 105.0))
        self.assertIsNone(twin(known, spawn(pos=(30, 20, 30)), 2, 5, 101.0))
        self.assertIsNone(twin(known, spawn(cell=2434), 2, 5, 100.0))
        self.assertIsNone(twin(known, spawn('misc_com_bottle_02'), 2, 5, 100.0))
        known[7]['removed'] = True
        self.assertIsNone(twin(known, spawn(), 2, 5, 100.0))
        self.assertEqual(twin(known, spawn(), 1, 0x12340001, 100.0), 7)


    def test_a_leveled_creature_names_its_placeholder_and_still_fits(self):
        made = spawn('x' * 31, leveled=0x0000A1B2)
        data = tes3x_net.pack_spawn(0xFF000002, made)
        self.assertLessEqual(len(data), tes3x_net.EVENT_DATA)
        self.assertEqual(tes3x_net.unpack_spawn(data), (0xFF000002, made))

    def test_a_summon_keeps_its_flag(self):
        made = spawn('atronach_flame_summon', summon=True)
        self.assertEqual(tes3x_net.unpack_spawn(tes3x_net.pack_spawn(0xFF000003, made)),
                         (0xFF000003, made))

    def test_a_placeholder_keeps_its_living_creature(self):
        twin = tes3x_net.spawn_twin
        known = {7: spawn('rat', origin=1, token=1, made=0.0, leveled=0xA1B2)}
        self.assertEqual(twin(known, spawn('mudcrab', pos=(900, 0, 0), leveled=0xA1B2), 2, 9,
                              1000.0), 7)
        self.assertIsNone(twin(known, spawn('mudcrab', leveled=0xA1B2), 2, 9, 1000.0, {7: 1}))
        self.assertIsNone(twin(known, spawn('rat', leveled=0xA1B3), 2, 9, 1000.0))


class ContentsTests(unittest.TestCase):
    def test_parts_fit_and_round_trip(self):
        entries = [['x' * 31, 1, tes3x_net.ENTRY_DATA, 5, 0x42C80000]] + \
            [[f'ingred_{i:02d}', i - 3, 0, 0, 0] for i in range(40)] + [['Gold_001', 31, 0, 0, 0]]
        parts = tes3x_net.pack_contents(0x0101D347, 2433, entries, tes3x_net.CONTENTS_ROLLED)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(p) <= tes3x_net.EVENT_DATA for p in parts))
        got = []
        for i, part in enumerate(parts):
            refid, cell, index, count, flags, some = tes3x_net.unpack_contents(part)
            self.assertEqual((refid, cell, index, count, flags),
                             (0x0101D347, 2433, i, len(parts), tes3x_net.CONTENTS_ROLLED))
            got += some
        self.assertEqual(got, entries)

    def test_an_empty_container_is_one_part(self):
        parts = tes3x_net.pack_contents(7, 1, [])
        self.assertEqual(len(parts), 1)
        self.assertEqual(tes3x_net.unpack_contents(parts[0])[5], [])


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
        cell = list(subrecords(found[-2][2]))
        self.assertEqual(cell[0][1], tes3x_net.GHOST_CELL.encode() + b'\0')
        refs = [value for tag, value in cell if tag == b'NAME'][1:]
        self.assertEqual(refs, [b'tes3x_ghost%d\0' % i for i in range(1, tes3x_net.GHOSTS + 1)])
        arrival = list(subrecords(found[-1][2]))
        self.assertEqual(arrival[0][1], tes3x_net.ARRIVAL_CELL.encode() + b'\0')
        self.assertEqual([v for t, v in arrival if t == b'FRMR'],
                         [struct.pack('<I', tes3x_net.GHOSTS + 1)])

    def test_chargen_keeps_a_joining_new_game_in_the_arrival_cell(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / tes3x_net.GHOST_PLUGIN
            path.write_bytes(tes3x_net.ghost_plugin(12345))
            scripts = [dict(subrecords(body)) for tag, _, body in records(path) if tag == b'SCPT']
        self.assertEqual(len(scripts), 1)
        head, data = scripts[0][b'SCHD'], scripts[0][b'SCDT']
        self.assertEqual(head[:32].rstrip(b'\0'), b'CharGen')
        self.assertEqual(struct.unpack_from('<I', head, 44)[0], len(data))
        arrival = tes3x_net.ARRIVAL_CELL.encode()
        self.assertIn(b' X\x12\x11 c' + bytes([len(arrival)]) + arrival + b' == 1', data)
        self.assertTrue(data.endswith(b'\x1c\x10\x07CharGen\x01\x01'))


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


class WeatherTests(unittest.TestCase):
    def test_events_fit_and_round_trip(self):
        table = {i: i % 10 for i in range(45)}
        events = tes3x_net.pack_weather(table, tes3x_net.WEATHER_OFFER)
        self.assertEqual(len(events), -(-45 // tes3x_net.WEATHER_PER_EVENT))
        self.assertGreater(len(events), 1)
        self.assertTrue(all(len(e) <= tes3x_net.EVENT_DATA for e in events))
        got = {}
        for e in events:
            flags, entries = tes3x_net.unpack_weather(e)
            self.assertEqual(flags, tes3x_net.WEATHER_OFFER)
            got.update(entries)
        self.assertEqual(got, table)


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

    def test_a_place_is_the_same_within_a_cell_and_a_step(self):
        same = tes3x_net.same_place
        self.assertTrue(same(self.state(0.0, 0.0), self.state(300.0, 300.0)))
        self.assertFalse(same(self.state(0.0, 0.0), self.state(600.0, 0.0)))
        self.assertFalse(same(self.state(0.0, 0.0, b'A'), self.state(0.0, 0.0, b'B')))
        self.assertFalse(same(self.state(0.0, 0.0, b'A'), self.state(0.0, 0.0)))

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

    def test_actors_go_to_the_nearest_player_with_a_margin(self):
        net = tes3x_net
        cell = (net.KEY_EXTERIOR, 0, 0, b'')
        players = {1: ({cell}, 0.0, 0.0), 2: ({cell}, 2000.0, 0.0)}
        authority = {cell: 1}

        def owner(previous, x, flags=0, now=10.0, target=0):
            return net.assign_owners(previous, {7: (cell, x, 0.0, flags, target)}, players,
                                     authority, now)[7]

        self.assertEqual(owner({}, 1900.0), (2, 10.0))  # a new actor goes straight to the nearer
        self.assertEqual(owner({}, 300.0)[0], 1)
        self.assertEqual(owner({7: (1, 0.0)}, 1100.0)[0], 1)  # within the margin
        self.assertEqual(owner({7: (1, 9.0)}, 1900.0)[0], 1)  # taken too recently
        self.assertEqual(owner({7: (1, 0.0)}, 1900.0, net.ACTOR_DEAD)[0], 1)
        fighting = net.ACTOR_IN_COMBAT
        self.assertEqual(owner({7: (2, 0.0)}, 1900.0, fighting, target=1)[0], 1)  # its foe's
        self.assertEqual(owner({7: (2, 9.0)}, 1900.0, fighting, target=1)[0], 2)  # after the hold
        self.assertEqual(owner({7: (1, 0.0)}, 1100.0, fighting, target=1)[0], 1)
        self.assertEqual(owner({7: (1, 0.0)}, 1900.0, fighting, target=0x0101F7C4)[0], 2)
        self.assertEqual(owner({}, 1900.0, fighting, target=5), (2, 10.0))  # foe not here
        self.assertEqual(owner({7: (3, 9.0)}, 300.0), (1, 10.0))  # its owner left
        players[2] = ({(net.KEY_EXTERIOR, 5, 5, b'')}, 2000.0, 0.0)
        self.assertEqual(owner({7: (1, 0.0)}, 1900.0)[0], 1)  # the nearer one does not load it

    def test_owners_event_fits_the_event_channel(self):
        self.assertLessEqual(4 + tes3x_net.OWNERS_PER_EVENT * tes3x_net.OWNER_PAIR.size,
                             tes3x_net.EVENT_DATA)
        self.assertEqual(tes3x_net.OWNERS_PER_EVENT, 9)

    def test_authority_event_fits_the_event_channel(self):
        data = tes3x_net.KEY.pack(tes3x_net.KEY_INTERIOR, 0, 0, b'Seyda Neen, Census') + \
            struct.pack('<I', 1)
        self.assertLessEqual(len(data), tes3x_net.EVENT_DATA)
        self.assertEqual(4 + tes3x_net.ACTORS_PER_PACKET * tes3x_net.ACTOR.size,
                         484)  # within tes3xnet.c's EVENTS_BYTES

    def test_status_fits_the_event_channel(self):
        self.assertEqual(tes3x_net.STATUS.size, 14)  # tes3xnet.c's STATUS_BYTES
        self.assertIn("disposition 60",
                      tes3x_net.describe_status(0x1234, (30, 0, 0, 30, 60)))
        self.assertNotIn("disposition",
                         tes3x_net.describe_status(0x1234, (90, 0, 0, 0,
                                                            tes3x_net.NO_DISPOSITION)))


class Receiver:
    """The console's side of a bulk transfer (tes3xnet.c bulk_chunk_rx, bulk_frame), in memory."""

    def __init__(self, outgoing, start=0):
        self.out, self.next, self.state, self.filled = outgoing, start, 2, {}
        self.data, self.acks = bytearray(outgoing.data[:start * tes3x_net.BULK_CHUNK]), []
        self.ack()

    def ack(self):
        seen = sum(1 << k for k in range(32) if self.next + k in self.filled)
        self.acks.append(tes3x_net.BULK_ACK_BODY.pack(self.out.id, self.next, seen,
                                                      16 if self.state == 2 else 0, self.state))

    def chunk(self, packet):
        index = struct.unpack_from('<I', packet, 4)[0]
        if self.state != 2 or not self.next <= index < self.next + 16 or index in self.filled:
            self.ack()
            return
        self.filled[index] = packet[8:]
        if len(self.filled) % 4 == 0:
            self.ack()

    def frame(self):
        wrote = False
        while self.next in self.filled:
            self.data += self.filled.pop(self.next)
            self.next += 1
            wrote = True
        if self.state == 2 and self.next >= self.out.chunks:
            self.state = 3
            self.ack()
        elif wrote:
            self.ack()


class BulkTests(unittest.TestCase):
    def run_transfer(self, size, start=0, loss=0.0, lose_final=False):
        import random
        data = bytes(random.Random(size).getrandbits(8) for _ in range(size))
        out = tes3x_net.Outgoing('test.bin', data)
        console, lossy, now, step = Receiver(out, start), random.Random(1), 0.0, 0
        while out.status != 3 and now < 60:
            now, step = now + 0.01, step + 1
            if step % 25 == 0 and console.state == 2:  # the tick
                console.ack()
            for ack in console.acks:
                done = struct.unpack_from('<5I', ack)[4] == 3
                if lossy.random() >= loss and not (lose_final and done):
                    out.on_ack(ack, now)
                lose_final = lose_final and not done
            console.acks = []
            for index in out.due(now):
                if lossy.random() >= loss:
                    console.chunk(out.chunk(index))
            console.frame()
        return out, console, data

    def test_whole_file_arrives_in_order(self):
        out, console, data = self.run_transfer(20 * 1024 + 5)
        self.assertEqual((out.status, bytes(console.data)), (3, data))
        self.assertEqual(out.resent, 0)

    def test_resumes_from_the_part_and_survives_loss(self):
        out, console, data = self.run_transfer(40 * 1024 + 9, start=17, loss=0.3)
        self.assertEqual((out.status, bytes(console.data)), (3, data))
        self.assertEqual(out.first, 17)

    def test_lost_final_ack_is_drawn_again(self):
        out, console, data = self.run_transfer(3 * 1024, lose_final=True)
        self.assertEqual((out.status, bytes(console.data)), (3, data))

    def test_probe_once_everything_in_flight_is_acked(self):
        out = tes3x_net.Outgoing('test.bin', bytes(3000))
        out.on_ack(tes3x_net.BULK_ACK_BODY.pack(out.id, 0, 0, 16, 2), 0.0)
        self.assertEqual(out.due(0.0), [0, 1, 2])
        out.on_ack(tes3x_net.BULK_ACK_BODY.pack(out.id, 0, 7, 16, 2), 0.1)
        self.assertEqual(out.due(0.2), [])
        self.assertEqual(out.due(0.1 + tes3x_net.BULK_PROBE), [0])
        self.assertEqual(out.probes, 1)

    def test_gap_is_resent_at_once_and_only_once(self):
        out = tes3x_net.Outgoing('test.bin', bytes(8 * 1024))
        out.on_ack(tes3x_net.BULK_ACK_BODY.pack(out.id, 0, 0, 16, 2), 0.0)
        self.assertEqual(out.due(0.0), list(range(8)))
        gap = tes3x_net.BULK_ACK_BODY.pack(out.id, 0, 1 << 3 | 1 << 2, 16, 2)  # 0 and 1 lost
        out.on_ack(gap, 0.05)
        self.assertEqual(out.due(0.05), [0, 1])
        out.on_ack(gap, 0.06)  # nothing sent after the resends has arrived yet
        self.assertEqual(out.due(0.07), [])
        self.assertEqual(out.fast, 2)

    def test_offer_fits_the_event_channel(self):
        out = tes3x_net.Outgoing('x' * tes3x_net.BULK_NAME, b'')
        self.assertLessEqual(len(out.offer()), tes3x_net.EVENT_DATA)
        self.assertEqual(out.chunks, 0)


class RemoteAdminTests(unittest.TestCase):
    secret = tes3x_net.admin_secret(b'correct horse')
    here = ('192.0.2.9', 40000)

    def setUp(self):
        self.now = [100.0]
        self.server = tes3x_net.RemoteAdmin(self.secret, None, lambda: self.now[0])
        self.ran = []

    def run_line(self, line):
        self.ran.append(line)
        return f'did {line}'

    def challenge(self, addr=here):
        hello = tes3x_net.REMOTE_HEAD.pack(tes3x_net.REMOTE_MAGIC, 1, tes3x_net.REMOTE_HELLO)
        reply = self.server.handle(hello, addr, self.run_line)
        return reply[tes3x_net.REMOTE_HEAD.size:]

    def command(self, nonce, line, secret=None, addr=here):
        head = tes3x_net.REMOTE_HEAD.pack(tes3x_net.REMOTE_MAGIC, 1,
                                          tes3x_net.REMOTE_COMMAND) + nonce
        key = tes3x_net.remote_key(secret or self.secret, nonce)
        reply = self.server.handle(head + tes3x_net.seal(key, 0, head, line.encode()), addr,
                                   self.run_line)
        kind = reply[5]
        if kind == tes3x_net.REMOTE_REFUSED:
            return 'refused ' + reply[len(head):].decode()
        return tes3x_net.unseal(key, 1, reply[:len(head)], reply[len(head):]).decode()

    def test_command_runs_once_with_the_right_password(self):
        nonce = self.challenge()
        self.assertEqual(self.command(nonce, 'list'), 'did list')
        self.assertEqual(self.command(nonce, 'list'), 'refused unauthorized')  # replayed
        self.assertEqual(self.ran, ['list'])

    def test_wrong_password_other_address_and_stale_challenge_are_refused(self):
        wrong = tes3x_net.admin_secret(b'wrong horse')
        self.assertEqual(self.command(self.challenge(), 'stop', wrong), 'refused unauthorized')
        nonce = self.challenge()
        self.assertEqual(self.command(nonce, 'stop', addr=('192.0.2.8', 40000)),
                         'refused unauthorized')
        nonce = self.challenge()
        self.now[0] += tes3x_net.REMOTE_NONCE_SECONDS + 1
        self.assertEqual(self.command(nonce, 'stop'), 'refused unauthorized')
        self.assertEqual(self.ran, [])

    def test_failures_slow_an_address_even_for_the_right_password(self):
        wrong = tes3x_net.admin_secret(b'wrong horse')
        for _ in range(tes3x_net.PASSWORD_RATE[0]):
            self.assertEqual(self.command(self.challenge(), 'list', wrong),
                             'refused unauthorized')
        self.assertEqual(self.command(self.challenge(), 'list'), 'refused slow down')
        other = ('192.0.2.8', 1)
        self.assertEqual(self.command(self.challenge(other), 'list', addr=other), 'did list')
        self.now[0] += 1 / tes3x_net.PASSWORD_RATE[1]
        self.assertEqual(self.command(self.challenge(), 'list'), 'did list')

    def test_client_talks_to_a_listener(self):
        import threading
        sock = tes3x_net.udp_socket()
        sock.bind(('127.0.0.1', 0))
        server = tes3x_net.RemoteAdmin(self.secret, sock)
        stop = threading.Event()

        def serve():
            sock.settimeout(0.1)
            while not stop.is_set():
                try:
                    data, addr = sock.recvfrom(2048)
                except (socket.timeout, OSError):
                    continue
                reply = server.handle(data, addr, lambda line: line.upper())
                if reply:
                    sock.sendto(reply, addr)

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        self.addCleanup(lambda: (stop.set(), thread.join(1), sock.close()))
        port = sock.getsockname()[1]
        self.assertEqual(tes3x_net.remote_admin_request('127.0.0.1', port, self.secret, 'bans'),
                         'BANS')
        with self.assertRaisesRegex(tes3x_net.RemoteAdminError, 'unauthorized'):
            tes3x_net.remote_admin_request('127.0.0.1', port,
                                           tes3x_net.admin_secret(b'nope nope'), 'bans')


if __name__ == '__main__':
    unittest.main()
