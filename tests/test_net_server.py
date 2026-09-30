import random
import socket
import subprocess
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import tes3x_net

NET = Path(__file__).resolve().parents[1] / 'tools' / 'tes3x_net.py'


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class ServerTests(unittest.TestCase):
    """A real `serve` on the loopback, driven by the fuzzer's console client."""

    def start(self, *extra):
        try:
            tes3x_net.crypto()
        except SystemExit:
            self.skipTest('cryptography is not installed')
        self.port = free_port()
        self.server = subprocess.Popen(
            [sys.executable, str(NET), 'serve', '--bind', '127.0.0.1', '--port', str(self.port),
             '--duration', '120', '--report', '0', *extra],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.stop)
        time.sleep(1.0)

    def stop(self):
        self.server.kill()
        _, err = self.server.communicate()
        self.assertNotIn('Traceback', err)

    def client(self, seed):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(('127.0.0.1', 0))
        self.addCleanup(sock.close)
        return tes3x_net.FuzzClient(sock, ('127.0.0.1', self.port), random.Random(seed))

    def test_survives_the_fuzzer(self):
        self.start('--bot')
        fuzz = subprocess.run([sys.executable, str(NET), 'fuzz', f'127.0.0.1:{self.port}',
                               '--count', '1500', '--seed', '7'],
                              capture_output=True, text=True, timeout=300)
        self.assertEqual(fuzz.returncode, 0, fuzz.stdout + fuzz.stderr)

    def test_replayed_and_forged_packets_draw_no_answer(self):
        self.start()
        client = self.client(1)
        client.join()
        client.send(tes3x_net.HEARTBEAT, b'')
        self.assertIsNotNone(client.receive(1.0, tes3x_net.HEARTBEAT))
        outer = tes3x_net.OUTER.pack(b'T3MP', tes3x_net.T3MP_VERSION, tes3x_net.SEALED, 0,
                                     client.session, client.seq + 1)
        inner = tes3x_net.INNER.pack(tes3x_net.HEARTBEAT, 0, 0, 0)
        sealed = outer + tes3x_net.seal(client.keys[0], client.seq + 1, outer, inner)
        client.sock.sendto(sealed, client.addr)
        self.assertIsNotNone(client.receive(1.0, tes3x_net.HEARTBEAT))
        client.sock.sendto(sealed, client.addr)  # the same packet again
        self.assertIsNone(client.receive(0.5, tes3x_net.HEARTBEAT))
        forged = bytearray(sealed)
        forged[-1] ^= 1
        forged[12] += 1  # a new seq, so only the tag is wrong
        client.sock.sendto(bytes(forged), client.addr)
        self.assertIsNone(client.receive(0.5, tes3x_net.HEARTBEAT))

    def test_max_players_refuses_the_next_console(self):
        self.start('--max-players', '1')
        self.client(1).join()
        second = self.client(2)
        with self.assertRaises(RuntimeError):
            second.join(timeout=1.0)
        self.assertEqual(second.refused, bytes(8))

    def test_handshake_flood_is_limited_per_address(self):
        self.start()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(('127.0.0.1', 0))
        self.addCleanup(sock.close)
        for session in range(1, 41):
            noise = tes3x_net.Noise(True, bytes(32), random.Random(session).randbytes(32),
                                    tes3x_net.PROLOGUE)
            sock.sendto((tes3x_net.OUTER.pack(b'T3MP', tes3x_net.T3MP_VERSION,
                                              tes3x_net.HANDSHAKE1, 0, session, 0)
                         + noise.write1()).ljust(tes3x_net.HANDSHAKE_PAD, b'\0'),
                        ('127.0.0.1', self.port))
        replies, end = 0, time.time() + 1.0
        sock.settimeout(0.2)
        while time.time() < end:
            try:
                data, _ = sock.recvfrom(4096)
            except socket.timeout:
                continue
            replies += data[5] == tes3x_net.HANDSHAKE2
            self.assertLessEqual(len(data), tes3x_net.HANDSHAKE_PAD)
        self.assertGreater(replies, 0)
        self.assertLessEqual(replies, tes3x_net.HANDSHAKE_RATE[0] + 2)


if __name__ == '__main__':
    unittest.main()
