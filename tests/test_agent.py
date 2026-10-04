import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import struct

from tes3x_agent import (AgentProtocol, Fetch, HANDSHAKE1, HANDSHAKE2, HANDSHAKE3, HEADER,
                         HEARTBEAT, MAGIC, OP_CONSOLE, OP_READ, PROLOGUE, READ_CHUNK, REPLY,
                         REQUEST, RETRIES, RETRY_SECONDS, SEALED, VERSION, WELCOME,
                         console_request, decode, encode, key_fingerprint, load_or_create_key,
                         read_request)
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


class AgentListenerTests(unittest.TestCase):
    def test_listener_survives_a_console_that_went_away(self):
        import socket
        import time
        from tes3x_agent import AgentListener
        closed = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        closed.bind(("127.0.0.1", 0))
        gone = closed.getsockname()
        closed.close()
        listener = AgentListener(bytes(range(32)), lambda _event: None, "127.0.0.1", 0)
        listener.start()
        self.addCleanup(listener.close)
        # Windows reports the port-unreachable answer as a reset on the next receive.
        listener.transmit([(b"x", gone)])
        time.sleep(0.4)
        self.assertTrue(listener.is_alive())


class FakeConsole:
    """The console's end of a paired session, as tes3xagent.c speaks it."""

    def __init__(self, protocol, address=("192.0.2.8", 26501), session=0x12345678):
        self.protocol, self.address, self.session = protocol, address, session
        self.noise = Noise(True, bytes(range(32, 64)), bytes(range(64, 96)), PROLOGUE)
        reply = protocol.receive(encode(HANDSHAKE1, session, 0, self.noise.write1()), address)
        self.noise.read2(decode(reply[0][0])[4])
        protocol.receive(encode(HANDSHAKE3, session, 0, self.noise.write3(b"")), address)
        self.keys = self.noise.split()
        self.sequence = 0

    def send(self, message, payload=b""):
        plain = bytes([message]) + payload
        header = HEADER.pack(MAGIC, VERSION, SEALED, len(plain) + 16, self.session,
                             self.sequence)
        packet = header + seal(self.keys[0], self.sequence, header, plain)
        self.sequence += 1
        return self.protocol.receive(packet, self.address)

    def open(self, packet):
        _, _, sequence, header, body = decode(packet)
        return unseal(self.keys[1], sequence, header, body)


class AgentRequestTests(unittest.TestCase):
    def setUp(self):
        self.now = [10.0]
        self.events = []
        self.protocol = AgentProtocol(bytes(range(32)), self.events.append, lambda: self.now[0])
        self.console = FakeConsole(self.protocol)
        self.peer = self.protocol.peer_at("192.0.2.8")

    def replies(self):
        return [event for event in self.events if event["kind"] == "reply"]

    def test_heartbeat_is_acknowledged(self):
        packets = self.console.send(HEARTBEAT, bytes(16))
        self.assertEqual(self.console.open(packets[0][0])[0], WELCOME)

    def test_console_request_reply_and_busy_retry(self):
        ident, packets = self.protocol.request(self.peer, *console_request("tes3xnet stat"))
        plain = self.console.open(packets[0][0])
        self.assertEqual(plain[0], REQUEST)
        self.assertEqual(struct.unpack_from("<IB", plain, 1), (ident, OP_CONSOLE))
        self.assertEqual(plain[6:], b"tes3xnet stat")

        self.console.send(REPLY, struct.pack("<IB", ident, 1))
        self.assertEqual(self.replies(), [])
        self.now[0] += RETRY_SECONDS
        resent = self.protocol.retry()
        self.assertEqual(self.console.open(resent[0][0])[1:], plain[1:])
        self.console.send(REPLY, struct.pack("<IB", ident, 0))
        self.assertEqual(self.replies()[-1]["status"], "ok")
        self.assertEqual(self.replies()[-1]["id"], ident)
        self.now[0] += RETRY_SECONDS
        self.assertEqual(self.protocol.retry(), [])

    def test_unanswered_request_times_out(self):
        ident, _ = self.protocol.request(self.peer, *console_request("fps"))
        for _ in range(RETRIES):
            self.now[0] += RETRY_SECONDS
            self.protocol.retry()
        self.assertEqual([(e["id"], e["status"]) for e in self.replies()], [(ident, "timeout")])

    def test_new_session_replaces_old_and_fails_its_requests(self):
        ident, _ = self.protocol.request(self.peer, *console_request("fps"))
        FakeConsole(self.protocol, session=0x2222)
        self.assertEqual(self.replies()[-1]["status"], "disconnected")
        self.assertEqual(self.replies()[-1]["id"], ident)
        self.assertEqual(self.protocol.peer_at("192.0.2.8").session, 0x2222)

    def test_request_limits(self):
        with self.assertRaises(ValueError):
            console_request("x" * 96)
        with self.assertRaises(ValueError):
            read_request("E:/" + "x" * 200, 0)

    def test_fetch_reads_windowed_chunks(self):
        content = bytes(range(256)) * 13  # 3328 bytes: three full chunks and a tail
        sent = []

        def send(op, args):
            ident, packets = self.protocol.request(self.peer, op, args)
            sent.append((ident, self.console.open(packets[0][0])))
            return ident

        fetch = Fetch(send, "E:\\tes3xprof.bin", window=2)
        fetch.start()
        while sent:
            ident, plain = sent.pop(0)
            _, op = struct.unpack_from("<IB", plain, 1)
            offset, length = struct.unpack_from("<IH", plain, 6)
            self.assertEqual((op, plain[12:]), (OP_READ, b"E:\\tes3xprof.bin"))
            self.assertEqual(length, READ_CHUNK)
            chunk = content[offset:offset + length]
            self.console.send(REPLY, struct.pack("<IBII", ident, 0, len(content), offset) + chunk)
            fetch.handle(self.replies()[-1])
            self.assertLessEqual(len(fetch.outstanding), 2)
        self.assertTrue(fetch.done)
        self.assertIsNone(fetch.error)
        self.assertEqual(fetch.data(), content)

    def test_fetch_reports_missing_file(self):
        def send(op, args):
            return self.protocol.request(self.peer, op, args)[0]

        fetch = Fetch(send, "E:/missing.bin")
        fetch.start()
        ident = next(iter(fetch.outstanding))
        self.console.send(REPLY, struct.pack("<IB", ident, 3))
        fetch.handle(self.replies()[-1])
        self.assertIn("failed", fetch.error)


if __name__ == "__main__":
    unittest.main()
