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


if __name__ == '__main__':
    unittest.main()
