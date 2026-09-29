import socket
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import tes3x_net


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


if __name__ == '__main__':
    unittest.main()
