import hashlib
import os
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
import tes3x_netbuild

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

    def test_dialogue_is_held_by_one_player(self):
        self.start()
        net = tes3x_net
        first, second = self.client(1), self.client(2)
        first.join()
        second.join()
        refid = 0x0101F7C4
        first.send(net.EVENTS, net.pack_events(0, [
            (1, net.EVENT_HOLD, 0, struct.pack('<III', refid, 1, 1))]))
        second.send(net.EVENTS, net.pack_events(0, [
            (1, net.EVENT_HOLD, 0, struct.pack('<III', refid, 1, 1))]))
        refused = self.events(second, lambda kind, _: kind == net.EVENT_HOLD_BROKEN)
        self.assertEqual(refused[-1],
                         (net.EVENT_HOLD_BROKEN, struct.pack('<III', refid, 2, 3)))

        first.send(net.EVENTS, net.pack_events(0, [
            (2, net.EVENT_HOLD, 0, struct.pack('<III', refid, 1, 0))]))
        time.sleep(0.1)
        second.send(net.EVENTS, net.pack_events(second.delivered, [
            (2, net.EVENT_HOLD, 0, struct.pack('<III', refid, 1, 1))]))
        claimed = self.events(first, lambda kind, data: kind == net.EVENT_HOLD and data[-4:] ==
                              struct.pack('<I', 1))
        self.assertEqual(claimed[-1],
                         (net.EVENT_HOLD, struct.pack('<III', refid, 1, 1)))

    def test_player_identity_is_relayed_and_replayed(self):
        self.start()
        net = tes3x_net
        first, second = self.client(1), self.client(2)
        first.join()
        second.join()
        parts = net.pack_identity('Nerevar', 'Imperial', 'b_n_imperial_f_head_01',
                                  'b_n_imperial_f_hair_01', True)
        first.send(net.EVENTS, net.pack_events(0, [
            (i + 1, net.EVENT_IDENTITY, 0, part) for i, part in enumerate(parts)]))
        relayed = self.events(second, lambda kind, data: kind == net.EVENT_IDENTITY and
                              data[0] == 1)
        self.assertEqual([data for kind, data in relayed if kind == net.EVENT_IDENTITY], parts)

        late = self.client(3)
        late.join()
        replayed = self.events(late, lambda kind, data: kind == net.EVENT_IDENTITY and
                               data[0] == 1)
        self.assertEqual([data for kind, data in replayed if kind == net.EVENT_IDENTITY], parts)

    def test_bot_identity_is_replayed(self):
        self.start('--bot')
        net = tes3x_net
        client = self.client(1)
        client.join()
        replayed = self.events(client, lambda kind, data: kind == net.EVENT_IDENTITY and
                               data[0] == 1)
        self.assertEqual([data for kind, data in replayed if kind == net.EVENT_IDENTITY],
                         net.pack_identity('Bot', 'Imperial', 'b_n_imperial_m_head_01',
                                           'b_n_imperial_m_hair_01'))

    def test_actor_equipment_is_relayed_and_replayed(self):
        self.start()
        net = tes3x_net
        first, second = self.client(1), self.client(2)
        first.join()
        second.join()
        refid = 0x0101F7C4
        items = [f'long_actor_equipment_id_{i:02d}' for i in range(7)]
        parts = net.pack_actor_equipment(refid, items)
        self.assertGreater(len(parts), 1)
        first.send(net.EVENTS, net.pack_events(0, [
            (i + 1, net.EVENT_ACTOR_EQUIPMENT, 0, part) for i, part in enumerate(parts)]))
        relayed = self.events(second, lambda kind, data: kind == net.EVENT_ACTOR_EQUIPMENT and
                              data[4] + 1 == data[5])
        self.assertEqual([data for kind, data in relayed
                          if kind == net.EVENT_ACTOR_EQUIPMENT], parts)

        late = self.client(3)
        late.join()
        replayed = self.events(late, lambda kind, data: kind == net.EVENT_ACTOR_EQUIPMENT and
                               data[4] + 1 == data[5])
        self.assertEqual([data for kind, data in replayed
                          if kind == net.EVENT_ACTOR_EQUIPMENT], parts)

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

    def staged_build(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root)
        game = root / 'deploy'
        (game / 'Data Files').mkdir(parents=True)
        (game / 'Data Files' / 'Mod.esp').write_bytes(b'TES3 plugin')
        (game / 'Data Files' / 'Morrowind.esm').write_bytes(b'retail master')
        (root / 'deltas').mkdir()
        delta = b'zstd frame'
        digest = hashlib.sha256(delta).hexdigest()
        (root / 'deltas' / f'{digest}.zst').write_bytes(delta)
        sys.path.insert(0, str(NET.parent))
        import tes3x_manifest
        files = tes3x_manifest.file_entries(game)
        files['Data Files/Morrowind.esm']['origin'] = 'retail'
        tes3x_manifest.write(game, tes3x_manifest.create(
            game, profile='net', source={}, files=files,
            xbe=[{'path': 'morrowind.xbe', 'delta': {'sha256': digest, 'size': len(delta)}}]))
        return game, digest

    def test_a_manager_is_handed_the_build_and_not_joined(self):
        game, digest = self.staged_build()
        self.start('--build', str(game), '--http-port', '0', '--max-players', '1')
        body = self.client(1).join(manager=True)
        sha, size, port, ticket = tes3x_netbuild.BUILD_BODY.unpack(body)
        manifest = tes3x_netbuild.fetch('127.0.0.1', port, ticket, 'manifest')
        self.assertEqual(hashlib.sha256(manifest).digest(), sha)
        self.assertEqual(len(manifest), size)
        self.assertEqual(tes3x_netbuild.fetch('127.0.0.1', port, ticket, 'file/Data Files/mod.esp'),
                         b'TES3 plugin')
        self.assertEqual(tes3x_netbuild.fetch('127.0.0.1', port, ticket, f'delta/{digest}'),
                         b'zstd frame')
        # retail files are the admin's choice, and a ticket is needed at all
        self.assertIsNone(tes3x_netbuild.fetch('127.0.0.1', port, ticket,
                                               'file/Data Files/Morrowind.esm'))
        self.assertIsNone(tes3x_netbuild.fetch('127.0.0.1', port, bytes(16), 'manifest'))
        self.client(2).join()  # the manager took no player's place

    def test_a_manager_without_the_password_gets_no_build(self):
        game, _ = self.staged_build()
        world = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, world)
        (world / 'password.txt').write_text('open sesame')
        self.start('--build', str(game), '--http-port', '0', '--world', str(world),
                   '--password-file', str(world / 'password.txt'))
        refused = self.client(1)
        self.assertIsNone(refused.join(timeout=1.0, manager=True))
        self.assertEqual(tes3x_net.REFUSE_BODY.unpack(refused.refused)[2],
                         tes3x_net.REFUSED_PASSWORD)
        self.assertIsNotNone(self.client(2).join(manager=True, password=b'open sesame'))

    def test_a_console_with_another_build_is_refused_as_stale(self):
        game, _ = self.staged_build()
        self.start('--build', str(game), '--http-port', '0')
        import tes3x_manifest
        build = bytes.fromhex(tes3x_manifest.load(game)['build'])
        stale = self.client(1)
        with self.assertRaises(RuntimeError):
            stale.join(timeout=1.0, lobby=True, build_id=bytes(31) + b'1')
        self.assertEqual(tes3x_net.REFUSE_BODY.unpack(stale.refused),
                         (0, 0, tes3x_net.REFUSED_STALE))
        self.client(2).join(build_id=build)
        self.client(3).join()  # a console without a manifest is judged by its load order

    def test_a_manager_is_told_its_key_character(self):
        world = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, world)
        self.start('--world', str(world), '--adopt')
        console = self.client(1)
        console.join()
        self.game(console, 1, 7, b'')
        self.upload(console, 2, 1, b'mp-hero.ess', self.save(b'Nerevar', 0))
        manager = self.client(1)
        manager.session ^= 2
        body = manager.join(manager=True)
        size = tes3x_netbuild.BUILD_BODY.size
        self.assertEqual(body[size:], b'Nerevar\0')
        self.assertEqual(tes3x_netbuild.BUILD_BODY.unpack(body[:size])[1], 0)

    def test_a_server_without_a_build_says_so(self):
        self.start()
        self.assertEqual(tes3x_netbuild.BUILD_BODY.unpack(self.client(1).join(manager=True))[1], 0)

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

    def game(self, client, seq, token, loaded, launch=tes3x_net.GAME_LOAD):
        client.send(tes3x_net.EVENTS, tes3x_net.pack_events(0, [
            (seq, tes3x_net.EVENT_GAME, 0, struct.pack('<I', token) + loaded + b'\0'
             + bytes([launch]))]))

    @staticmethod
    def character(world, name='Nerevar'):
        (key,) = (world / 'characters').iterdir()
        return key / name

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
        self.start('--world', str(world), '--adopt')
        client = self.client(1)
        client.join()
        self.game(client, 1, 7, b'')
        versions = [self.save(b'Nerevar', seed) for seed in range(5)]
        for ident, data in enumerate(versions, 1):
            self.upload(client, ident + 1, ident, b'mp-hero.ess', data)
        folder = self.character(world)
        self.assertEqual(sorted(p.name for p in folder.iterdir()),
                         ['mp-hero.1.ess', 'mp-hero.2.ess', 'mp-hero.3.ess', 'mp-hero.ess'])
        self.assertEqual((folder / 'mp-hero.ess').read_bytes(), versions[4])
        self.assertEqual((folder / 'mp-hero.3.ess').read_bytes(), versions[1])
        self.assertEqual(list((world / 'uploads').rglob('*.ess')), [])

    def test_a_new_launch_loads_the_kept_character(self):
        world = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, world)
        self.start('--world', str(world), '--adopt')
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
        folder = self.character(world)
        self.assertEqual((folder / 'mp-hero.ess').read_bytes(), kept)
        self.assertEqual(len(list((world / 'uploads').rglob('mp-hero.ess'))), 1)

        loaded = self.client(1)  # relaunched into the checkpoint it was sent
        loaded.session ^= 4
        loaded.join()
        self.game(loaded, 1, 9, name)
        newer = self.save(b'Nerevar', 2)
        self.upload(loaded, 2, 3, b'mp-hero.ess', newer)
        self.assertEqual((folder / 'mp-hero.ess').read_bytes(), newer)

    def events(self, client, until, timeout=3.0):
        """Events the server sends, acked, in order, up to the first for which until is true."""
        got, end = [], time.time() + timeout
        delivered = getattr(client, 'delivered', 0)
        while time.time() < end:
            body = client.receive(0.5, tes3x_net.EVENTS)
            if body is None:
                continue
            for seq, kind, _, data in tes3x_net.unpack_events(body)[1]:
                if seq == delivered + 1:
                    delivered = client.delivered = seq
                    got.append((kind, data))
            client.send(tes3x_net.EVENTS, tes3x_net.pack_events(delivered, []))
            if any(until(kind, data) for kind, data in got):
                return got
        self.fail(f'no such event among {got}')

    def test_player_state_is_replayed_over_the_checkpoint(self):
        world = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, world)
        self.start('--world', str(world), '--adopt')
        net = tes3x_net

        def ready(kind, data):
            return kind == net.EVENT_PLAYER and data[:1] == bytes([net.PLAYER_READY])

        def player(client, seq, *bodies):
            client.send(net.EVENTS, net.pack_events(0, [
                (seq + i, net.EVENT_PLAYER, 0, body) for i, body in enumerate(bodies)]))
            return seq + len(bodies)

        first = self.client(1)
        first.join()
        self.game(first, 1, 7, b'')
        self.upload(first, 2, 1, b'mp-hero.ess', self.save(b'Nerevar', 0))
        # the first checkpoint makes the character, and the console sends its state from scratch
        self.assertEqual(self.events(first, ready)[-1][1], bytes([net.PLAYER_READY, 0]))
        level = net.LEVEL.pack(9, 3, *range(11), 80.0, 60.0, 200.0, *[40.0] * 8)
        swords = [[1, net.ENTRY_DATA, 300 + i, 0] for i in range(12)]  # three parts
        seq = player(first, 3, *net.pack_items('Gold_001', [[100, 0, 0, 0]]),
               *net.pack_items('iron longsword', swords),
               bytes([net.PLAYER_LEVEL]) + level,
               bytes([net.PLAYER_SKILLS, 1]) + net.SKILL.pack(5, 42.0, 0.5),
               bytes([net.PLAYER_VITALS]) + net.VITALS.pack(70.0, 60.0, 150.0),
               *net.pack_journal([('A1_1_FindSpymaster', 10)]))
        place = net.STATE_BODY.pack(net.IN_WORLD | net.INTERIOR, 100.0, 200.0, 30.0, 1.5,
                                    b"Arrille's Tradehouse")
        first.send(net.STATE, place + bytes(net.ANIM_BYTES))
        first.send(net.EVENTS, net.pack_events(0, [
            (seq, net.EVENT_BOUNTY, 0, struct.pack('<i', 4321))]))
        seq += 1
        time.sleep(0.3)
        player(first, seq, *net.pack_items('Gold_001', [[150, 0, 0, 0]]),
               *net.pack_items('iron longsword', []),
               bytes([net.PLAYER_VITALS]) + net.VITALS.pack(55.0, 60.0, 180.0),
               *net.pack_journal([('A1_1_FindSpymaster', 20)]))
        time.sleep(0.3)
        kept = (self.character(world) / 'mp-hero.ess').read_bytes()
        name = net.CHECKPOINT_NAME.format(int.from_bytes(
            hashlib.blake2b(kept, digest_size=32).digest()[:4], 'big')).encode()

        stale = self.client(1)  # a relaunch into another save gets no replay, only LOAD
        stale.session ^= 2
        stale.join()
        self.game(stale, 1, 8, b'old.ess')
        sent = self.events(stale, lambda kind, _: kind == net.EVENT_LOAD)
        self.assertFalse([d for k, d in sent if k == net.EVENT_PLAYER])
        player(stale, 2, *net.pack_items('Gold_001', [[1, 0, 0, 0]]))  # not its character's

        loaded = self.client(1)
        loaded.session ^= 4
        loaded.join()
        self.game(loaded, 1, 9, name)
        replay = [d for k, d in self.events(loaded, ready) if k == net.EVENT_PLAYER]
        self.assertEqual(replay[-1], bytes([net.PLAYER_READY, 0]))  # and then the console sends it all
        items = [net.unpack_items(d) for d in replay if d[0] == net.PLAYER_ITEMS]
        self.assertEqual([(i[2], i[3]) for i in items], [('Gold_001', [[150, 0, 0, 0]])])
        self.assertIn(bytes([net.PLAYER_LEVEL]) + level, replay)
        self.assertIn(bytes([net.PLAYER_PLACE]) + place, replay)
        self.assertIn(bytes([net.PLAYER_BOUNTY]) + struct.pack('<i', 4321), replay)
        vitals = bytes([net.PLAYER_VITALS]) + net.VITALS.pack(55.0, 60.0, 180.0)
        self.assertGreater(replay.index(vitals), replay.index(bytes([net.PLAYER_LEVEL]) + level))
        self.assertIn(bytes([net.PLAYER_SKILLS, 1]) + net.SKILL.pack(5, 42.0, 0.5), replay)
        quests = [q for d in replay if d[0] == net.PLAYER_JOURNAL for q in net.unpack_journal(d)]
        self.assertEqual(quests, [('A1_1_FindSpymaster', 10), ('A1_1_FindSpymaster', 20)])

        self.game(loaded, 2, 9, name)  # a rejoin of the same launch: the console sends it all
        self.assertEqual(self.events(loaded, ready)[-1][1], bytes([net.PLAYER_READY, 0]))
        stream = self.character(world) / net.STREAM_NAME
        end = time.time() + 4
        while not stream.exists() and time.time() < end:
            time.sleep(0.2)
        self.assertEqual(net.PlayerStream(str(stream)).items, {'Gold_001': [[150, 0, 0, 0]]})
        self.assertEqual(net.PlayerStream(str(stream)).bounty, 4321)

    def test_rebuild_replays_the_kept_state_over_another_save_and_diffs_it(self):
        world = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, world)
        self.start('--world', str(world), '--adopt', '--rebuild', '0.5')
        net = tes3x_net

        def save(seed):  # a header, then a journal record tes3x_ess.py can read
            data = self.save(b'Nerevar', seed)
            head = 16 + struct.unpack_from('<I', data, 4)[0]
            text = random.Random(seed).randbytes(3 * net.BULK_CHUNK)
            body = b'NAME' + struct.pack('<I', len(text)) + text
            return data[:head] + b'JOUR' + struct.pack('<III', len(body), 0, 0) + body

        first = self.client(1)
        first.join()
        self.game(first, 1, 7, b'')
        kept = save(0)
        self.upload(first, 2, 1, b'mp-hero.ess', kept)
        self.events(first, lambda k, d: k == net.EVENT_PLAYER and d[:1] == bytes([net.PLAYER_READY]))
        first.send(net.EVENTS, net.pack_events(0, [
            (3, net.EVENT_PLAYER, 0, body) for body in net.pack_items('Gold_001', [[150, 0, 0, 0]])]))
        time.sleep(0.3)

        other = self.client(1)  # a launch running another save gets the character's state
        other.session ^= 2
        other.join()
        self.game(other, 1, 8, b'base.ess')
        sent = self.events(other, lambda kind, _: kind == net.EVENT_SAVE)
        replay = [d for k, d in sent if k == net.EVENT_PLAYER]
        self.assertEqual([net.unpack_items(d)[2:4] for d in replay if d[0] == net.PLAYER_ITEMS],
                         [('Gold_001', [[150, 0, 0, 0]])])
        self.assertIn(bytes([net.PLAYER_READY, 0]), replay)
        self.upload(other, 2, 2, b'mp-hero.ess', save(1))
        end = time.time() + 3
        while not (found := list((world / 'uploads').rglob('mp-hero.diff.txt')))                 and time.time() < end:
            time.sleep(0.1)
        self.assertTrue(found, 'no diff report')
        report = found[0].read_text(encoding='utf-8')
        self.assertRegex(report, r'Journal\s+1\s+1')
        self.assertEqual((self.character(world) / 'mp-hero.ess').read_bytes(), kept)

    def test_the_main_menu_gets_no_world_only_the_choice(self):
        world = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, world)
        self.start('--world', str(world))
        net = tes3x_net
        menu = self.client(1)
        menu.join(lobby=True)
        self.assertIsNone(menu.receive(1.5, net.CLOCK))
        self.game(menu, 1, 7, b'', net.GAME_NONE)
        got = self.events(menu, lambda kind, _: kind == net.EVENT_NEWCHAR)
        self.assertEqual({k for k, _ in got}, {net.EVENT_NEWCHAR})
        playing = self.client(2)  # a lobby does not set the session's load order or clock
        playing.join()
        self.assertIsNotNone(playing.receive(1.5, net.CLOCK))

    def test_a_death_is_respawned_and_announced(self):
        world = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, world)
        self.start('--world', str(world), '--adopt', '--respawn', 'temple',
                   '--respawn-delay', '3', '--death-gold', '25')
        net = tes3x_net

        def player(kind, data):
            return kind == net.EVENT_PLAYER and data[:1] == bytes([kind_wanted[0]])

        first, second = self.client(1), self.client(2)
        first.join()
        second.join()
        self.game(first, 1, 7, b'')
        self.upload(first, 2, 1, b'mp-hero.ess', self.save(b'Nerevar', 0))
        kind_wanted = [net.PLAYER_READY]
        self.events(first, player)
        first.send(net.EVENTS, net.pack_events(0, [
            (3, net.EVENT_PLAYER, 0, net.pack_items('Gold_001', [[200, 0, 0, 0]])[0]),
            (4, net.EVENT_PLAYER, 0, bytes([net.PLAYER_SPELLS, net.SPELLS_ADD, 0, 1])
             + b'fire bite\0'),
            (5, net.EVENT_PLAYER, 0, bytes([net.PLAYER_DEATH]))]))
        kind_wanted = [net.PLAYER_RESPAWN]
        respawn = [d for k, d in self.events(first, player) if player(k, d)][0]
        self.assertEqual(net.RESPAWN.unpack(respawn[1:]), (3000, 0, 50))
        seen = self.events(second, lambda kind, _: kind in (net.EVENT_PLAYER, net.EVENT_TEXT))
        self.assertIn((net.EVENT_PLAYER, bytes([net.PLAYER_DEATH])), seen)
        notice = [data for kind, data in seen if kind == net.EVENT_TEXT][-1]
        self.assertEqual(notice, b'Nerevar has died.')

        late = self.client(3)
        late.join()
        replay = self.events(late, lambda kind, _: kind == net.EVENT_PLAYER)
        self.assertIn((net.EVENT_PLAYER, bytes([net.PLAYER_DEATH])), replay)

        kept = (self.character(world) / 'mp-hero.ess').read_bytes()
        name = net.CHECKPOINT_NAME.format(int.from_bytes(
            hashlib.blake2b(kept, digest_size=32).digest()[:4], 'big')).encode()
        again = self.client(1)  # the power went before the respawn
        again.session ^= 2
        again.join()
        self.game(again, 1, 8, name)
        replay = self.events(again, player)
        respawn = [d for k, d in replay if player(k, d)][0]
        self.assertEqual(net.RESPAWN.unpack(respawn[1:])[0], 0)
        self.assertIn((net.EVENT_PLAYER, bytes([net.PLAYER_SPELLS, net.SPELLS_SNAPSHOT, 0, 1])
                       + b'fire bite\0'), replay)
        again.send(net.EVENTS, net.pack_events(again.delivered, [
            (2, net.EVENT_PLAYER, 0, bytes([net.PLAYER_ALIVE]))]))
        alive = self.events(second, lambda kind, _: kind == net.EVENT_PLAYER)
        self.assertIn((net.EVENT_PLAYER, bytes([net.PLAYER_ALIVE])), alive)
        stream = self.character(world) / net.STREAM_NAME
        end = time.time() + 5
        while time.time() < end and (not stream.exists() or
                                     net.PlayerStream(str(stream)).dead):
            time.sleep(0.2)
        self.assertFalse(net.PlayerStream(str(stream)).dead)
        self.assertEqual(net.PlayerStream(str(stream)).spells, ['fire bite'])

    def test_a_new_character_is_made_listed_and_chosen(self):
        world = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, world)
        self.start('--world', str(world))
        net = tes3x_net

        def kinds(*wanted):
            return lambda kind, _: kind in wanted

        def names(events, kind):
            parts = [d for k, d in events if k == kind]
            return [n.decode() for d in parts for n in d[2:].split(b'\0')[:-1]]

        def pick(client, seq, what, index):
            client.send(net.EVENTS, net.pack_events(client.delivered, [
                (seq, net.EVENT_PICK, 0, bytes([what, index]))]))

        starts = [name for name, _ in net.load_starts(net.STARTS)]
        loaded = self.client(1)  # a key with no character, in someone else's save
        loaded.join()
        self.game(loaded, 1, 7, b'old.ess')
        newchar = self.events(loaded, lambda k, d: k == net.EVENT_NEWCHAR and d[0] + 1 == d[1])
        self.assertEqual(names(newchar, net.EVENT_NEWCHAR), starts)  # it relaunches to New Game
        self.upload(loaded, 2, 1, b'mp-hero.ess', self.save(b'Nerevar', 0))
        self.assertFalse((world / 'characters').exists())

        new = self.client(1)
        new.session ^= 2
        new.join()
        self.game(new, 1, 8, b'', net.GAME_NEW)
        self.events(new, lambda k, d: k == net.EVENT_NEWCHAR and d[0] + 1 == d[1])
        pick(new, 2, net.PICK_START, 1)
        run = [d for k, d in self.events(new, lambda k, d: k == net.EVENT_RUN and d == b'\0')
               if k == net.EVENT_RUN]
        self.assertEqual(run[0], b'Player->PositionCell 505 -387 -752 205 '
                                 b'"Balmora, Guild of Mages"\0')
        self.upload(new, 3, 2, b'mp-hero.ess', self.save(b'Nerevar', 1))
        self.events(new, lambda k, d: k == net.EVENT_PLAYER and d == bytes([net.PLAYER_READY, 0]))
        self.assertTrue((self.character(world) / 'mp-hero.ess').exists())

        again = self.client(1)  # in another save: the list, and a second character
        again.session ^= 4
        again.join()
        self.game(again, 1, 9, b'old.ess')
        self.assertEqual(names(self.events(again, kinds(net.EVENT_CHARS)), net.EVENT_CHARS),
                         ['Nerevar'])
        pick(again, 2, net.PICK_CHARACTER, net.PICK_NEW)
        self.events(again, kinds(net.EVENT_NEWCHAR))
        second = self.client(1)
        second.session ^= 8
        second.join()
        self.game(second, 1, 10, b'', net.GAME_NEW)
        self.events(second, kinds(net.EVENT_NEWCHAR))
        self.upload(second, 2, 3, b'mp-hero.ess', self.save(b'Nerevar', 2))
        self.assertTrue((self.character(world, 'Nerevar-2') / 'mp-hero.ess').exists())

        third = self.client(1)
        third.session ^= 16
        third.join()
        self.game(third, 1, 11, b'old.ess')
        listed = names(self.events(third, kinds(net.EVENT_CHARS)), net.EVENT_CHARS)
        self.assertEqual(listed, ['Nerevar-2', 'Nerevar'])
        pick(third, 2, net.PICK_CHARACTER, 1)
        loads = [d for k, d in self.events(third, kinds(net.EVENT_LOAD)) if k == net.EVENT_LOAD]
        kept = (self.character(world) / 'mp-hero.ess').read_bytes()
        self.assertEqual(loads, [net.checkpoint_name(kept).encode() + b'\0'])

    def test_characters_kept_before_the_list_move_into_a_folder(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root)
        (root / 'mp-hero.ess').write_bytes(self.save(b'Nerevar', 0))
        (root / 'mp-hero.1.ess').write_bytes(self.save(b'Nerevar', 1))
        (root / tes3x_net.STREAM_NAME).write_text('{}')
        kept = tes3x_net.kept_characters(str(root))
        self.assertEqual([(f, os.path.basename(p)) for f, p in kept], [('Nerevar', 'mp-hero.ess')])
        self.assertEqual(sorted(p.name for p in (root / 'Nerevar').iterdir()),
                         ['mp-hero.1.ess', 'mp-hero.ess', tes3x_net.STREAM_NAME])

    def test_player_events_round_trip(self):
        net = tes3x_net
        swords = [[1, net.ENTRY_DATA, 300 + i, 7] for i in range(12)]
        parts = net.pack_items('iron longsword', swords)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(p) <= net.EVENT_DATA for p in parts))
        stream = net.PlayerStream(os.devnull + '.missing')
        for part in parts:
            stream.take(part)
        self.assertEqual(stream.items['iron longsword'], swords)
        quests = [(f'quest_{i:02d}_with_a_long_name', i) for i in range(9)]
        events = net.pack_journal(quests)
        self.assertTrue(all(len(e) <= net.EVENT_DATA for e in events))
        self.assertEqual([q for e in events for q in net.unpack_journal(e)], quests)
        self.assertEqual(stream.take(bytes([net.PLAYER_SPELLS, net.SPELLS_ADD, 0, 1])
                                     + b'fire bite\0calm humanoid\0'),
                         'learned fire bite, calm humanoid')
        self.assertEqual(stream.spells, ['fire bite', 'calm humanoid'])
        stream.take(bytes([net.PLAYER_SPELLS, net.SPELLS_REMOVE, 0, 1]) + b'FIRE BITE\0')
        self.assertEqual(stream.spells, ['calm humanoid'])
        identity = {'name': 'Vela Arenim of the Long Road', 'race': 'Dark Elf',
                    'head': 'b_n_dark elf_f_head_01', 'hair': 'b_n_dark elf_f_hair_02',
                    'birthsign': '', 'class': 'NEWCLASSID_CHARGEN',
                    'class_name': 'Spellsword of Vivec', 'female': True,
                    'class_attributes': [0, 6], 'specialization': 1,
                    'class_skills': list(range(10, 20))}
        parts = net.pack_player_identity(identity)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(p) <= net.EVENT_DATA for p in parts))
        for part in parts[:-1]:
            self.assertIsNone(stream.take(part))
        self.assertTrue(stream.take(parts[-1]).startswith('is Vela Arenim'))
        self.assertEqual(stream.identity, identity)
        self.assertIsNone(stream.take(parts[-1]))  # a part out of order is dropped
        self.assertEqual(stream.replay()[:len(parts)], parts)
        worn = [[f'expensive_ring_{i:02d}_long_enough', net.ENTRY_DATA, i, 50] for i in range(8)]
        worn.append(['common_shirt_01', 0, 0, 0])
        parts = net.pack_worn(worn)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(p) <= net.EVENT_DATA for p in parts))
        for part in parts[:-1]:
            self.assertIsNone(stream.take(part))
        self.assertTrue(stream.take(parts[-1]).startswith('wears expensive_ring_00'))
        self.assertEqual(stream.worn, worn)
        self.assertIn(parts[-1], stream.replay())
        self.assertEqual(stream.take(net.pack_worn([])[0]), 'wears nothing')

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

    def test_stopping_waits_for_each_console_to_save(self):
        world = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, world)
        admin = free_port()
        self.start('--world', str(world), '--adopt', '--admin-port', str(admin),
                   '--stop-wait', '20')
        client = self.client(1)
        client.join()
        self.game(client, 1, 7, b'')
        self.upload(client, 2, 1, b'mp-hero.ess', self.save(b'Nerevar', 0))
        self.assertIn('stopping', self.admin(admin, 'stop'))
        self.events(client, lambda kind, _: kind == tes3x_net.EVENT_SAVE)
        self.assertIsNone(self.server.poll())
        last = self.save(b'Nerevar', 1)
        self.upload(client, 3, 2, b'mp-hero.ess', last)
        self.assertEqual(self.server.wait(5), 0)
        self.assertEqual((self.character(world) / 'mp-hero.ess').read_bytes(), last)

    def test_stopping_gives_up_on_a_silent_console(self):
        world = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, world)
        admin = free_port()
        self.start('--world', str(world), '--adopt', '--admin-port', str(admin),
                   '--stop-wait', '1')
        client = self.client(1)
        client.join()
        self.game(client, 1, 7, b'')
        self.upload(client, 2, 1, b'mp-hero.ess', self.save(b'Nerevar', 0))
        started = time.time()
        self.admin(admin, 'stop')
        self.assertEqual(self.server.wait(5), 0)
        self.assertGreaterEqual(time.time() - started, 0.9)

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
