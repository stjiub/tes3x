import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from tes3x_agent import (AgentProtocol, HANDSHAKE1, HANDSHAKE2, HANDSHAKE3, HEADER,
                         HEARTBEAT, MAGIC, PROLOGUE, SEALED, VERSION, decode, encode,
                         key_fingerprint, load_or_create_key)
from tes3x_net import Noise, fingerprint, seal, unseal


class AgentProtocolTests(unittest.TestCase):
    def test_key_is_created_once(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "agent.key"
            first = load_or_create_key(path)
            second = load_or_create_key(path)
            self.assertEqual(len(first), 32)
            self.assertEqual(first, second)
            self.assertEqual(len(key_fingerprint(first)), 32)

    def test_noise_handshake_heartbeat_replay_and_stall(self):
        now = [10.0]
        events = []
        host_secret, client_secret = bytes(range(32)), bytes(range(32, 64))
        protocol = AgentProtocol(host_secret, events.append, lambda: now[0])
        client = Noise(True, client_secret, bytes(range(64, 96)), PROLOGUE)
        address, session = ("192.0.2.8", 41000), 0x12345678

        first = encode(HANDSHAKE1, session, 0, client.write1())
        replies = protocol.receive(first, address)
        self.assertEqual(len(replies), 1)
        kind, _, _, _, body = decode(replies[0][0])
        self.assertEqual(kind, HANDSHAKE2)
        client.read2(body)
        self.assertEqual(key_fingerprint(host_secret), fingerprint(client.rs))

        third = encode(HANDSHAKE3, session, 0, client.write3(b"build\0profile"))
        welcome = protocol.receive(third, address)[0][0]
        self.assertEqual(events[-1]["kind"], "connected")
        self.assertEqual(events[-1]["payload"], b"build\0profile")
        keys = client.split()
        _, _, sequence, header, body = decode(welcome)
        self.assertEqual(unseal(keys[1], sequence, header, body)[0], 0)

        payload = bytes([HEARTBEAT]) + b"status"
        header = HEADER.pack(MAGIC, VERSION, SEALED, len(payload) + 16, session, 0)
        packet = header + seal(keys[0], 0, header, payload)
        protocol.receive(packet, address)
        self.assertEqual(events[-1]["kind"], "heartbeat")
        self.assertEqual(events[-1]["payload"], b"status")
        count = len(events)
        protocol.receive(packet, address)
        self.assertEqual(len(events), count)

        now[0] += 4
        protocol.expire()
        self.assertEqual(events[-1]["kind"], "stalled")


if __name__ == "__main__":
    unittest.main()
