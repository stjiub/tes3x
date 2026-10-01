import hashlib
import random
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
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
        self.assertEqual(tes3x_net.REFUSE_BODY.unpack(second.refused)[2], tes3x_net.REFUSED_FULL)

    def test_password_is_asked_once_of_each_key(self):
        world = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, world)
        (world / 'password.txt').write_text('open sesame\n')
        self.start('--world', str(world), '--password-file', str(world / 'password.txt'))
        wrong = self.client(1)
        with self.assertRaises(RuntimeError):
            wrong.join(timeout=1.0, password=b'open sesamE')
        # (0, 0): the refused console did not set the session's load order
        self.assertEqual(tes3x_net.REFUSE_BODY.unpack(wrong.refused),
                         (0, 0, tes3x_net.REFUSED_PASSWORD))
        self.client(2).join(password=b'open sesame')
        again = self.client(2)  # the same key, admitted by the join before
        again.session ^= 2
        again.join()
        self.assertEqual(len((world / 'admitted.txt').read_text().splitlines()), 1)
        with self.assertRaises(RuntimeError):
            self.client(3).join(timeout=1.0)

    def test_console_sends_a_file_through_loss(self):
        world = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, world)
        self.start('--world', str(world))
        client = self.client(1)
        client.join()
        data = random.Random(5).randbytes(10 * tes3x_net.BULK_CHUNK + 77)
        digest = hashlib.blake2b(data, digest_size=32).digest()
        offer = tes3x_net.BULK_OFFER.pack(9, len(data), digest) + b'char.ess\0'
        client.send(tes3x_net.EVENTS, tes3x_net.pack_events(0, [(1, tes3x_net.EVENT_OFFER, 0,
                                                                   offer)]))
        ack = tes3x_net.BULK_ACK_BODY.unpack(client.receive(1.0, tes3x_net.BULK_ACK))
        self.assertEqual(ack, (9, 0, 0, tes3x_net.BULK_WINDOW_IN, tes3x_net.BULK_RECEIVING))
        chunks = [data[i:i + tes3x_net.BULK_CHUNK]
                  for i in range(0, len(data), tes3x_net.BULK_CHUNK)]
        for index in (0, 2, 3, 4, 5, 6, 7):  # 1 lost
            client.send(tes3x_net.CHUNK, struct.pack('<II', 9, index) + chunks[index])
        acks = []
        while (body := client.receive(0.5, tes3x_net.BULK_ACK)) is not None:
            acks.append(tes3x_net.BULK_ACK_BODY.unpack(body))
        self.assertEqual(acks[-1][1:3], (1, 0b1111110))  # needs 1; 2 to 7 held
        for index in range(1, len(chunks)):
            client.send(tes3x_net.CHUNK, struct.pack('<II', 9, index) + chunks[index])
        while (body := client.receive(1.0, tes3x_net.BULK_ACK)) is not None:
            if tes3x_net.BULK_ACK_BODY.unpack(body)[4] == tes3x_net.BULK_DONE:
                break
        else:
            self.fail('no ack said done')
        (folder,) = (world / 'uploads').iterdir()
        self.assertEqual((folder / 'char.ess').read_bytes(), data)

    @staticmethod
    def save(player, seed):
        def sub(tag, body):
            return tag + struct.pack('<I', len(body)) + body

        gmdt = bytearray(124)
        gmdt[24:29], gmdt[92:92 + len(player)] = b'Balmo', player
        head = (sub(b'HEDR', bytes(300)) + sub(b'MAST', b'Morrowind.esm\0')
                + sub(b'DATA', bytes(8)) + sub(b'GMDT', bytes(gmdt)))
        return (b'TES3' + struct.pack('<III', len(head), 0, 0) + head
                + random.Random(seed).randbytes(3 * tes3x_net.BULK_CHUNK))

    def game(self, client, seq, token, loaded):
        client.send(tes3x_net.EVENTS, tes3x_net.pack_events(0, [
            (seq, tes3x_net.EVENT_GAME, 0, struct.pack('<I', token) + loaded + b'\0')]))

    def upload(self, client, seq, ident, name, data):
        digest = hashlib.blake2b(data, digest_size=32).digest()
        offer = tes3x_net.BULK_OFFER.pack(ident, len(data), digest) + name + b'\0'
        client.send(tes3x_net.EVENTS, tes3x_net.pack_events(
            0, [(seq, tes3x_net.EVENT_OFFER, 0, offer)]))
        client.receive(1.0, tes3x_net.BULK_ACK)
        for index in range(0, len(data), tes3x_net.BULK_CHUNK):
            client.send(tes3x_net.CHUNK, struct.pack('<II', ident, index // tes3x_net.BULK_CHUNK)
                        + data[index:index + tes3x_net.BULK_CHUNK])
        while (body := client.receive(1.0, tes3x_net.BULK_ACK)) is not None:
            if tes3x_net.BULK_ACK_BODY.unpack(body)[4] == tes3x_net.BULK_DONE:
                break
        else:
            self.fail('no ack said done')
        time.sleep(0.2)

    def test_a_save_is_kept_as_a_character(self):
        world = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, world)
        self.start('--world', str(world))
        client = self.client(1)
        client.join()
        self.game(client, 1, 7, b'')
        versions = [self.save(b'Nerevar', seed) for seed in range(5)]
        for ident, data in enumerate(versions, 1):
            self.upload(client, ident + 1, ident, b'mp-hero.ess', data)
        (folder,) = (world / 'characters').iterdir()
        self.assertEqual(sorted(p.name for p in folder.iterdir()),
                         ['mp-hero.1.ess', 'mp-hero.2.ess', 'mp-hero.3.ess', 'mp-hero.ess'])
        self.assertEqual((folder / 'mp-hero.ess').read_bytes(), versions[4])
        self.assertEqual((folder / 'mp-hero.3.ess').read_bytes(), versions[1])
        self.assertEqual(list((world / 'uploads').rglob('*.ess')), [])

    def test_a_new_launch_loads_the_kept_character(self):
        world = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, world)
        self.start('--world', str(world))
        first = self.client(1)
        first.join()
        self.game(first, 1, 7, b'')
        kept = self.save(b'Nerevar', 0)
        self.upload(first, 2, 1, b'mp-hero.ess', kept)
        name = tes3x_net.CHECKPOINT_NAME.format(int.from_bytes(
            hashlib.blake2b(kept, digest_size=32).digest()[:4], 'big')).encode()

        stale = self.client(1)  # the same key after a relaunch into an older save
        stale.session ^= 2
        stale.join()
        self.game(stale, 1, 8, b'old.ess')
        loads = []
        end = time.time() + 2
        while not loads and time.time() < end:
            body = stale.receive(0.5, tes3x_net.EVENTS)
            if body is not None:
                loads = [data for _, kind, _, data in tes3x_net.unpack_events(body)[1]
                         if kind == tes3x_net.EVENT_LOAD]
        self.assertEqual(loads, [name + b'\0'])
        self.upload(stale, 2, 2, b'mp-hero.ess', self.save(b'Nerevar', 1))
        (folder,) = (world / 'characters').iterdir()
        self.assertEqual((folder / 'mp-hero.ess').read_bytes(), kept)
        self.assertEqual(len(list((world / 'uploads').rglob('mp-hero.ess'))), 1)

        loaded = self.client(1)  # relaunched into the checkpoint it was sent
        loaded.session ^= 4
        loaded.join()
        self.game(loaded, 1, 9, name)
        newer = self.save(b'Nerevar', 2)
        self.upload(loaded, 2, 3, b'mp-hero.ess', newer)
        self.assertEqual((folder / 'mp-hero.ess').read_bytes(), newer)

    def admin(self, port, *words):
        run = subprocess.run([sys.executable, str(NET), 'admin', '--port', str(port), *words],
                             capture_output=True, text=True, timeout=10)
        self.assertEqual(run.returncode, 0, run.stderr)
        return run.stdout

    def test_kick_and_ban_from_the_admin_port(self):
        world = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, world)
        admin = free_port()
        self.start('--world', str(world), '--admin-port', str(admin))
        first = self.client(1)
        first.join()
        self.assertIn('client 1: playing', self.admin(admin, 'list'))
        self.admin(admin, 'kick', '1')
        body = first.receive(1.0, tes3x_net.REFUSE)
        self.assertEqual(tes3x_net.REFUSE_BODY.unpack(body)[2], tes3x_net.REFUSED_KICKED)
        again = self.client(1)  # the same key rejoins after a kick
        again.session ^= 2
        again.join()
        self.assertIn('banned key', self.admin(admin, 'ban', '1'))
        body = again.receive(1.0, tes3x_net.REFUSE)
        self.assertEqual(tes3x_net.REFUSE_BODY.unpack(body)[2], tes3x_net.REFUSED_BANNED)
        third = self.client(1)
        third.session ^= 4
        with self.assertRaises(RuntimeError):
            third.join(timeout=1.0)
        self.assertEqual(tes3x_net.REFUSE_BODY.unpack(third.refused)[2],
                         tes3x_net.REFUSED_BANNED)
        banned = [line.split() for line in (world / 'bans.txt').read_text().splitlines()]
        self.assertEqual([kind for kind, _ in banned], ['key', 'mac'])
        self.admin(admin, 'unban', *[word for pair in banned for word in pair])
        fourth = self.client(1)
        fourth.session ^= 8
        fourth.join()

    def test_replayed_handshake3_does_not_move_the_session(self):
        self.start()
        client = self.client(1)
        client.join()
        thief = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        thief.bind(('127.0.0.1', 0))
        self.addCleanup(thief.close)
        thief.sendto(client.handshake3, client.addr)
        self.assertIsNotNone(client.receive(1.0, tes3x_net.WELCOME))
        thief.settimeout(0.5)
        with self.assertRaises(socket.timeout):
            thief.recvfrom(4096)
        client.send(tes3x_net.HEARTBEAT, b'')
        self.assertIsNotNone(client.receive(1.0, tes3x_net.HEARTBEAT))

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
