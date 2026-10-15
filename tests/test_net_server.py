import contextlib
import hashlib
import io
import json
import os
import random
import re
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

import tes3x.net as tes3x_net
import tes3x.netbuild as tes3x_netbuild
from tes3x.net.validation import valid_client_event
from tes3x.payload import source_text

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class ServerTests(unittest.TestCase):
    """A real `serve` on the loopback, driven by the fuzzer's console client."""

    def wait_for(self, condition, message, timeout=5.0):
        end = time.monotonic() + timeout
        while True:
            result = condition()
            if result:
                return result
            left = end - time.monotonic()
            if left <= 0:
                self.fail(message)
            time.sleep(min(0.05, left))

    def wait_stream(self, world, **expected):
        path = self.character(world) / tes3x_net.STREAM_NAME

        def persisted():
            if not path.exists():
                return False
            stream = tes3x_net.PlayerStream(str(path))
            return all(getattr(stream, key) == value for key, value in expected.items())

        self.wait_for(persisted, f'character stream did not reach {expected}')

    def start(self, *extra):
        try:
            tes3x_net.crypto()
        except SystemExit:
            self.skipTest('cryptography is not installed')
        self.port = free_port()
        self.admin_port = free_port()
        if '--admin-port' in extra:
            self.admin_port = int(extra[extra.index('--admin-port') + 1])
        self.server = subprocess.Popen(
            [sys.executable, '-m', 'tes3x', 'net', 'serve', '--bind', '127.0.0.1',
             '--port', str(self.port),
             '--admin-port', str(self.admin_port),
             '--duration', '120', '--report', '0', *extra],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.stop, self.server)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.settimeout(0.05)

            def ready():
                if self.server.poll() is not None:
                    self.fail('server exited before its admin listener became ready: '
                              + self.server.stderr.read())
                # list is read-only; commands that change state must not be blindly retried.
                probe.sendto(b'list', ('127.0.0.1', self.admin_port))
                try:
                    probe.recvfrom(4096)
                except (socket.timeout, ConnectionResetError):
                    return False
                return True

            self.wait_for(ready, 'server admin listener did not become ready')

    def stop(self, server=None):
        server = server or self.server
        if server.stderr.closed:
            return
        server.kill()
        _, err = server.communicate()
        self.assertNotIn('Traceback', err)

    def client(self, seed, version=None):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(('127.0.0.1', 0))
        self.addCleanup(sock.close)
        return tes3x_net.FuzzClient(sock, ('127.0.0.1', self.port), random.Random(seed), version)

    def test_event_numbers_are_unique(self):
        events = {name: value for name, value in vars(tes3x_net).items()
                  if name.startswith('EVENT_') and name != 'EVENT_DATA' and isinstance(value, int)}
        self.assertEqual(len(events), len(set(events.values())), events)

    def test_incompatible_game_gets_an_authenticated_update_reason(self):
        self.start()
        net = tes3x_net
        for version in (net.T3MP_VERSION - 1, net.T3MP_VERSION + 1):
            with self.subTest(version=version):
                client = self.client(version, version)
                with self.assertRaisesRegex(RuntimeError, 'no WELCOME'):
                    client.join(timeout=0.5)
                self.assertEqual(net.REFUSE_BODY.unpack(client.refused),
                                 (net.T3MP_VERSION, version, net.REFUSED_PROTOCOL))
                self.assertIsNotNone(client.keys)
        compatible = self.client(99)
        compatible.join()
        compatible.send(net.HEARTBEAT, b'')
        self.assertIsNotNone(compatible.receive(1, net.HEARTBEAT))

    def test_dash_protocol_is_independent_and_mismatch_is_explained(self):
        self.start()
        net = tes3x_net
        compatible = self.client(1)
        self.assertIsNotNone(compatible.join(dash=True))
        self.assertEqual(compatible.version, net.MANAGER_VERSION)
        for version in (net.MANAGER_VERSION + 1, net.T3MP_VERSION):
            with self.subTest(version=version):
                client = self.client(version, version)
                self.assertIsNone(client.join(timeout=0.5, dash=True))
                self.assertEqual(net.REFUSE_BODY.unpack(client.refused),
                                 (net.MANAGER_VERSION, version, net.REFUSED_PROTOCOL))

    def test_handshake_cannot_switch_protocol_mid_exchange(self):
        self.start()
        net = tes3x_net
        client = self.client(1, net.T3MP_VERSION - 1)
        with self.assertRaisesRegex(RuntimeError, 'no WELCOME'):
            client.join(timeout=0.5)
        altered = bytearray(client.handshake3)
        altered[4] = net.T3MP_VERSION
        client.sock.sendto(altered, client.addr)
        self.assertIsNone(client.receive(0.3, net.WELCOME))
        self.assertIsNotNone(self.client(2).join())

    def test_repeated_protocol_refusal_keeps_nonce_and_destination_safe(self):
        self.start()
        net = tes3x_net
        client = self.client(1, net.T3MP_VERSION - 1)
        with self.assertRaisesRegex(RuntimeError, 'no WELCOME'):
            client.join(timeout=0.5)
        before = client.peer_seq
        thief = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        thief.bind(('127.0.0.1', 0))
        self.addCleanup(thief.close)
        thief.sendto(client.handshake3, client.addr)
        self.assertEqual(client.receive(0.5, net.REFUSE),
                         net.REFUSE_BODY.pack(net.T3MP_VERSION, client.version,
                                              net.REFUSED_PROTOCOL))
        self.assertGreater(client.peer_seq, before)
        thief.settimeout(0.2)
        with self.assertRaises(socket.timeout):
            thief.recvfrom(4096)

    def test_survives_the_fuzzer(self):
        self.start('--bot')
        fuzz = subprocess.run([sys.executable, '-m', 'tes3x', 'net', 'fuzz',
                               f'127.0.0.1:{self.port}',
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
        import tes3x.manifest as tes3x_manifest
        files = tes3x_manifest.file_entries(game)
        files['Data Files/Morrowind.esm']['origin'] = 'retail'
        tes3x_manifest.write(game, tes3x_manifest.create(
            game, profile='net', source={}, files=files,
            xbe=[{'path': 'morrowind.xbe', 'delta': {'sha256': digest, 'size': len(delta)}}]))
        return game, digest

    def test_a_dash_is_handed_the_build_and_not_joined(self):
        game, digest = self.staged_build()
        self.start('--build', str(game), '--http-port', '0', '--max-players', '1')
        body = self.client(1).join(dash=True)
        sha, size, port, ticket = tes3x_netbuild.BUILD_BODY.unpack_from(body)
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
        self.client(2).join()  # the Dash took no player's place

    def test_a_dash_without_the_password_gets_no_build(self):
        game, _ = self.staged_build()
        world = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, world)
        (world / 'password.txt').write_text('open sesame')
        self.start('--build', str(game), '--http-port', '0', '--world', str(world),
                   '--password-file', str(world / 'password.txt'))
        refused = self.client(1)
        self.assertIsNone(refused.join(timeout=1.0, dash=True))
        self.assertEqual(tes3x_net.REFUSE_BODY.unpack(refused.refused)[2],
                         tes3x_net.REFUSED_PASSWORD)
        self.assertIsNotNone(self.client(2).join(dash=True, password=b'open sesame'))

    def test_a_console_with_another_build_is_refused_as_stale(self):
        game, _ = self.staged_build()
        self.start('--build', str(game), '--http-port', '0')
        import tes3x.manifest as tes3x_manifest
        build = bytes.fromhex(tes3x_manifest.load(game)['build'])
        stale = self.client(1)
        with self.assertRaises(RuntimeError):
            stale.join(timeout=1.0, lobby=True, build_id=bytes(31) + b'1')
        self.assertEqual(tes3x_net.REFUSE_BODY.unpack(stale.refused),
                         (0, 0, tes3x_net.REFUSED_STALE))
        self.client(2).join(build_id=build)
        self.client(3).join()  # a console without a manifest is judged by its load order

    def test_a_dash_is_told_its_key_character(self):
        world = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, world)
        self.start('--world', str(world), '--adopt')
        console = self.client(1)
        console.join()
        self.game(console, 1, 7, b'')
        self.upload(console, 2, 1, b'mp-hero.ess', self.save(b'Nerevar', 0))
        dash = self.client(1)
        dash.session ^= 2
        body = dash.join(dash=True)
        size = tes3x_netbuild.BUILD_BODY.size
        self.assertTrue(body[size:].startswith(b'Nerevar\0INFO'))
        self.assertEqual(tes3x_netbuild.BUILD_BODY.unpack(body[:size])[1], 0)

    def test_a_server_without_a_build_says_so(self):
        self.start()
        self.assertEqual(tes3x_netbuild.BUILD_BODY.unpack_from(self.client(1).join(dash=True))[1], 0)

    def test_dash_server_details_exclude_lobby_and_dash_from_players(self):
        self.start('--name', 'Balmora', '--description', 'Shared quests', '--max-players', '4')
        self.client(1).join()
        self.client(2).join(lobby=True)
        body = self.client(3).join(dash=True)
        info = body[tes3x_netbuild.BUILD_BODY.size + 1:]
        self.assertEqual(tes3x_netbuild.DASH_INFO.unpack_from(info), (b'INFO', 1, 4))
        self.assertEqual(info[tes3x_netbuild.DASH_INFO.size:], b'Balmora\0Shared quests\0')

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
        # The fourth arrival is acked at once; 5 to 7 wait for a timed ack, up to two ticks.
        acks = []
        while (body := client.receive(1.5, tes3x_net.BULK_ACK)) is not None:
            acks.append(tes3x_net.BULK_ACK_BODY.unpack(body)[1:3])
            if acks[-1] == (1, 0b1111110):  # needs 1; 2 to 7 held
                break
        self.assertIn((1, 0b1111110), acks)
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
        """Send a file as a console does: the offer, then the chunks its last ack lacks, again
        until an ack says done; a lost datagram or a slow runner only delays it."""
        net = tes3x_net
        digest = hashlib.blake2b(data, digest_size=32).digest()
        offer = net.BULK_OFFER.pack(ident, len(data), digest) + name + b'\0'
        chunks = [data[i:i + net.BULK_CHUNK] for i in range(0, len(data), net.BULK_CHUNK)]
        acked, end = None, time.monotonic() + 10
        while time.monotonic() < end:
            if acked is None:
                client.send(net.EVENTS, net.pack_events(0, [(seq, net.EVENT_OFFER, 0, offer)]))
            else:
                _, first, held, window, _ = acked
                for index in range(first, min(first + window, len(chunks))):
                    if not held >> (index - first) & 1:
                        client.send(net.CHUNK, struct.pack('<II', ident, index) + chunks[index])
            body = client.receive(1.0, net.BULK_ACK)
            if body is not None:
                acked = net.BULK_ACK_BODY.unpack(body)
                if acked[4] == net.BULK_DONE:
                    break
        else:
            self.fail('no ack said done')
        time.sleep(0.2)

    @staticmethod
    def identity(name='Nerevar'):
        return dict(name=name, race='Dark Elf', head='b_n_dark elf_m_head_01',
                    hair='b_n_dark elf_m_hair_01', birthsign='', female=False,
                    **{'class': 'Warrior', 'class_name': 'Warrior'},
                    class_attributes=[0, 5], specialization=0, class_skills=list(range(10)))

    def seed_identity(self, world, client, seq):
        net = tes3x_net
        parts = net.pack_player_identity(self.identity())
        client.send(net.EVENTS, net.pack_events(0, [
            (seq + i, net.EVENT_PLAYER, 0, part) for i, part in enumerate(parts)]))
        path = self.character(world) / net.STREAM_NAME
        self.wait_for(lambda: path.exists() and net.PlayerStream(str(path)).identity,
                      'character identity was not persisted')
        return seq + len(parts)

    def character_load(self, world, client):
        folder = self.character(world)
        data = tes3x_net.PlayerStream(str(folder / tes3x_net.STREAM_NAME)).character_file()
        name = tes3x_net.checkpoint_name(data, tes3x_net.CHARACTER_FILE)
        (folder / name).write_bytes(data)
        return name.encode()

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

    def test_a_character_without_identity_has_no_save_fallback(self):
        world = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, world)
        self.start('--world', str(world), '--adopt')
        first = self.client(1)
        first.join()
        self.game(first, 1, 7, b'')
        kept = self.save(b'Nerevar', 0)
        self.upload(first, 2, 1, b'mp-hero.ess', kept)

        stale = self.client(1)  # the same key after a relaunch into an older save
        stale.session ^= 2
        stale.join()
        self.game(stale, 1, 8, b'old.ess')
        loads, notices = [], []
        end = time.monotonic() + 2
        while not loads and time.monotonic() < end:
            body = stale.receive(0.5, tes3x_net.EVENTS)
            if body is not None:
                entries = tes3x_net.unpack_events(body)[1]
                loads += [data for _, kind, _, data in entries if kind == tes3x_net.EVENT_LOAD]
                notices += [data for _, kind, _, data in entries if kind == tes3x_net.EVENT_TEXT]
        self.assertEqual(loads, [])
        self.assertIn(b'This character has no retained identity.', notices)
        self.upload(stale, 2, 2, b'mp-hero.ess', self.save(b'Nerevar', 1))
        folder = self.character(world)
        self.assertEqual((folder / 'mp-hero.ess').read_bytes(), kept)
        self.assertEqual(len(list((world / 'uploads').rglob('mp-hero.ess'))), 1)


    def events(self, client, until, timeout=3.0):
        """Events the server sends, acked, in order, up to the first for which until is true."""
        got, end = [], time.monotonic() + timeout
        delivered = getattr(client, 'delivered', 0)
        while (left := end - time.monotonic()) > 0:
            body = client.receive(min(0.5, left), tes3x_net.EVENTS)
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
        seq = self.seed_identity(world, first, 3)
        level = net.LEVEL.pack(9, 3, *range(11), 80.0, 60.0, 200.0, *[40.0] * 8)
        swords = [[1, net.ENTRY_DATA, 300 + i, 0] for i in range(12)]  # three parts
        seq = player(first, seq, *net.pack_items('Gold_001', [[100, 0, 0, 0]]),
               *net.pack_items('iron longsword', swords),
               bytes([net.PLAYER_LEVEL]) + level,
               bytes([net.PLAYER_SKILLS, 1]) + net.SKILL.pack(5, 42.0, 0.5),
               bytes([net.PLAYER_MODIFIERS, 2]) + net.MODIFIER.pack(0, 35.0)
               + net.MODIFIER.pack(13, 57.0),
               bytes([net.PLAYER_VITALS]) + net.VITALS.pack(70.0, 60.0, 150.0),
               *net.pack_journal([('A1_1_FindSpymaster', 10)]))
        place = net.STATE_BODY.pack(net.IN_WORLD | net.INTERIOR, 100.0, 200.0, 30.0, 1.5,
                                    b"Arrille's Tradehouse")
        first.send(net.STATE, place + bytes(net.ANIM_BYTES))
        first.send(net.EVENTS, net.pack_events(0, [
            (seq, net.EVENT_BOUNTY, 0, struct.pack('<i', 4321))]))
        seq += 1
        self.wait_stream(world, items={'Gold_001': [[100, 0, 0, 0]], 'iron longsword': swords},
                         bounty=4321)
        player(first, seq, *net.pack_items('Gold_001', [[150, 0, 0, 0]]),
               *net.pack_items('iron longsword', []),
               bytes([net.PLAYER_VITALS]) + net.VITALS.pack(55.0, 60.0, 180.0),
               *net.pack_journal([('A1_1_FindSpymaster', 20)]))
        self.wait_stream(world, items={'Gold_001': [[150, 0, 0, 0]]},
                         vitals=[55.0, 60.0, 180.0], journal={'A1_1_FindSpymaster': [10, 20]})
        name = self.character_load(world, first)

        stale = self.client(1)  # a relaunch into another save gets no replay, only LOAD
        stale.session ^= 2
        stale.join()
        self.game(stale, 1, 8, b'old.ess')
        sent = self.events(stale, lambda kind, _: kind == net.EVENT_LOAD)
        name = next(d[:-1] for k, d in sent if k == net.EVENT_LOAD)
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
        self.assertIn(bytes([net.PLAYER_MODIFIERS, 2]) + net.MODIFIER.pack(0, 35.0)
                      + net.MODIFIER.pack(13, 57.0), replay)
        quests = [q for d in replay if d[0] == net.PLAYER_JOURNAL for q in net.unpack_journal(d)]
        self.assertEqual(quests, [('A1_1_FindSpymaster', 10), ('A1_1_FindSpymaster', 20)])

        self.game(loaded, 2, 9, name)  # a rejoin of the same launch: the console sends it all
        self.assertEqual(self.events(loaded, ready)[-1][1], bytes([net.PLAYER_READY, 0]))
        loaded.send(net.EVENTS, net.pack_events(loaded.delivered, [
            (3, net.EVENT_SNAPSHOT, 0, struct.pack('<I', 18) + place)]))
        self.events(loaded, lambda k, d: k == net.EVENT_SNAPSHOT)
        stream = self.character(world) / net.STREAM_NAME
        self.wait_for(stream.exists, 'character stream was not persisted')
        saved = net.PlayerStream(str(stream))
        self.assertEqual(saved.items, {'Gold_001': [[150, 0, 0, 0]]})
        self.assertEqual(saved.modifiers, {0: 35.0, 13: 57.0})
        self.assertEqual(saved.bounty, 4321)

    def test_rebuild_replays_the_kept_state_over_another_save_and_diffs_it(self):
        world = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, world)
        self.start('--world', str(world), '--adopt', '--rebuild', '0.5')
        net = tes3x_net

        def save(seed):  # a header, then a journal record tes3x ess can read
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
        self.wait_stream(world, items={'Gold_001': [[150, 0, 0, 0]]})

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
        found = self.wait_for(lambda: list((world / 'uploads').rglob('mp-hero.diff.txt')),
                              'no diff report')
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
        seq = self.seed_identity(world, first, 3)
        kind_wanted = [net.PLAYER_READY]
        self.events(first, player)
        first.send(net.EVENTS, net.pack_events(0, [
            (seq, net.EVENT_PLAYER, 0, net.pack_items('Gold_001', [[200, 0, 0, 0]])[0]),
            (seq + 1, net.EVENT_PLAYER, 0, bytes([net.PLAYER_SPELLS, net.SPELLS_ADD, 0, 1])
             + b'fire bite\0'),
            (seq + 2, net.EVENT_PLAYER, 0, bytes([net.PLAYER_DEATH]))]))
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

        name = self.character_load(world, first)
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
        self.wait_for(lambda: stream.exists() and not net.PlayerStream(str(stream)).dead
                      and net.PlayerStream(str(stream)).spells == ['fire bite'],
                      'revived character state was not persisted')
        self.assertFalse(net.PlayerStream(str(stream)).dead)
        self.assertEqual(net.PlayerStream(str(stream)).spells, ['fire bite'])

    def test_topics_are_shared_between_characters_and_replayed_to_a_late_join(self):
        net = tes3x_net
        world = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, world)
        self.start('--world', str(world), '--adopt')
        ready = lambda k, d: k == net.EVENT_PLAYER and d == bytes([net.PLAYER_READY, 0])
        first, second = self.client(1), self.client(2)
        first.join()
        self.game(first, 1, 7, b'')
        self.upload(first, 2, 1, b'mp-first.ess', self.save(b'Nerevar', 0))
        self.events(first, ready)
        seq = self.seed_identity(world, first, 3)
        second.join()
        self.game(second, 1, 8, b'')
        self.upload(second, 2, 2, b'mp-second.ess', self.save(b'Second', 1))
        self.events(second, ready)
        identity = net.pack_player_identity(self.identity('Second'))
        second.send(net.EVENTS, net.pack_events(second.delivered, [
            (3 + i, net.EVENT_PLAYER, 0, data) for i, data in enumerate(identity)]))
        folder = next((world / 'characters').glob('*/Second'))
        path = folder / net.STREAM_NAME
        self.wait_for(lambda: path.exists() and net.PlayerStream(str(path)).identity,
                      'second character identity was not persisted')
        topic = net.pack_topics(['Caius Cosades'])[0]
        first.send(net.EVENTS, net.pack_events(first.delivered,
                                              [(seq, net.EVENT_PLAYER, 0, topic)]))
        shared = self.events(second, lambda k, d: k == net.EVENT_PLAYER and d == topic)
        self.assertIn((net.EVENT_PLAYER, topic), shared)
        # A topic received from the world does not depend on the recipient publishing it.
        saved = net.PlayerStream(str(folder / net.STREAM_NAME))
        self.assertIsNone(saved.topics)
        again = self.client(2)
        again.session ^= 4
        again.join()
        data = saved.character_file()
        name = net.checkpoint_name(data, net.CHARACTER_FILE)
        (folder / name).write_bytes(data)
        self.game(again, 1, 9, name.encode(), net.GAME_NEW)
        replay = self.events(again, ready)
        self.assertIn((net.EVENT_PLAYER, topic), replay)

    def test_journal_is_shared_between_characters_and_replayed_to_a_late_join(self):
        net = tes3x_net
        world = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, world)
        self.start('--world', str(world), '--adopt')
        ready = lambda k, d: k == net.EVENT_PLAYER and d == bytes([net.PLAYER_READY, 0])
        first, second = self.client(1), self.client(2)
        first.join()
        self.game(first, 1, 7, b'')
        self.upload(first, 2, 1, b'mp-first.ess', self.save(b'Nerevar', 0))
        self.events(first, ready)
        seq = self.seed_identity(world, first, 3)
        second.join()
        self.game(second, 1, 8, b'')
        self.upload(second, 2, 2, b'mp-second.ess', self.save(b'Second', 1))
        self.events(second, ready)
        identity = net.pack_player_identity(self.identity('Second'))
        second.send(net.EVENTS, net.pack_events(second.delivered, [
            (3 + i, net.EVENT_PLAYER, 0, data) for i, data in enumerate(identity)]))
        folder = next((world / 'characters').glob('*/Second'))
        self.wait_for(lambda: net.PlayerStream(str(folder / net.STREAM_NAME)).identity,
                      'second character identity was not persisted')
        later = 3 + len(identity)
        quest = 'A1_1_FindSpymaster'
        step = net.pack_journal([(quest, 10)])[0]
        first.send(net.EVENTS, net.pack_events(first.delivered,
                                              [(seq, net.EVENT_PLAYER, 0, step)]))
        self.assertIn((net.EVENT_PLAYER, step),
                      self.events(second, lambda k, d: k == net.EVENT_PLAYER and d == step))
        # The applied index coming back, or an older one, is not news; a later one is.
        onward = net.pack_journal([(quest, 10), (quest, 5), ('A2_1_MeetSulMatuul', 1)])[0]
        second.send(net.EVENTS, net.pack_events(second.delivered,
                                               [(later, net.EVENT_PLAYER, 0, onward)]))
        news = net.pack_journal([('A2_1_MeetSulMatuul', 1)])[0]
        self.assertIn((net.EVENT_PLAYER, news),
                      self.events(first, lambda k, d: k == net.EVENT_PLAYER and d == news))
        mine = next((world / 'characters').glob('*/Nerevar'))
        data = net.PlayerStream(str(mine / net.STREAM_NAME)).character_file()
        name = net.checkpoint_name(data, net.CHARACTER_FILE)
        (mine / name).write_bytes(data)
        again = self.client(1)
        again.session ^= 4
        again.join()
        self.game(again, 1, 9, name.encode(), net.GAME_NEW)
        replay = [d for k, d in self.events(again, ready) if k == net.EVENT_PLAYER]
        quests = [q for d in replay if d[0] == net.PLAYER_JOURNAL for q in net.unpack_journal(d)]
        self.assertEqual(quests, [(quest, 10), ('A2_1_MeetSulMatuul', 1)])

    def test_world_journal_moves_forward_and_survives_restart(self):
        net = tes3x_net
        with tempfile.TemporaryDirectory() as folder:
            server = net.Server.__new__(net.Server)
            server.world, server.clients = net.World(), {}
            server.world.path = str(Path(folder) / 'world.json')
            server.share_journal(None, [('MS_Quest', 10), ('ms_quest', 30), ('MS_Quest', 20)], 0)
            server.share_journal(None, [(f'Q{i}', 1) for i in range(net.QUEST_INDICES + 2)], 0)
            server.share_journal(None, [('Long', i) for i in range(net.QUEST_INDICES + 5)], 0)
            self.assertEqual(server.world.journal['ms_quest'], ['MS_Quest', [10, 30]])
            self.assertEqual(len(server.world.journal['long'][1]), net.QUEST_INDICES)
            server.world.save(0)
            restored = net.World()
            self.assertTrue(restored.load(server.world.path, 0))
            self.assertEqual(restored.journal, server.world.journal)
            server.world = restored
            replay = [q for d in server.journal_replay() for q in net.unpack_journal(d)]
            self.assertEqual(replay[net.QUEST_INDICES:net.QUEST_INDICES + 2],
                             [('MS_Quest', 10), ('MS_Quest', 30)])
            self.assertEqual(len(replay), net.QUEST_INDICES + 2 + net.QUEST_INDICES + 2)

    def test_faction_rank_and_reputation_are_shared_and_membership_is_per_character(self):
        net = tes3x_net
        world = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, world)
        self.start('--world', str(world), '--adopt')
        ready = lambda k, d: k == net.EVENT_PLAYER and d == bytes([net.PLAYER_READY, 0])
        first, second = self.client(1), self.client(2)
        first.join()
        self.game(first, 1, 7, b'')
        self.upload(first, 2, 1, b'mp-first.ess', self.save(b'Nerevar', 0))
        self.events(first, ready)
        seq = self.seed_identity(world, first, 3)
        second.join()
        self.game(second, 1, 8, b'')
        self.upload(second, 2, 2, b'mp-second.ess', self.save(b'Second', 1))
        self.events(second, ready)
        identity = net.pack_player_identity(self.identity('Second'))
        second.send(net.EVENTS, net.pack_events(second.delivered, [
            (3 + i, net.EVENT_PLAYER, 0, data) for i, data in enumerate(identity)]))
        folder = next((world / 'characters').glob('*/Second'))
        self.wait_for(lambda: net.PlayerStream(str(folder / net.STREAM_NAME)).identity,
                      'second character identity was not persisted')
        later = 3 + len(identity)
        guild, member = 'Fighters Guild', net.FACTION_ALL | net.FACTION_JOINED

        def player(data):
            return lambda k, d: k == net.EVENT_PLAYER and d == data

        def send(client, seq, *entries):
            client.send(net.EVENTS, net.pack_events(client.delivered, [
                (seq, net.EVENT_PLAYER, 0, net.pack_factions(entries)[0])]))

        send(first, seq, (guild, member, 2, 10))
        shared = net.pack_factions([(guild, net.FACTION_RANK | net.FACTION_REPUTATION, 2, 10)])[0]
        self.assertIn((net.EVENT_PLAYER, shared), self.events(second, player(shared)))
        # Joining natively keeps the stored rank; a joiner who reports another gets the world's.
        send(second, later, (guild, member, 0, 10))
        corrected = net.pack_factions([(guild, net.FACTION_RANK, 2, 10)])[0]
        self.assertIn((net.EVENT_PLAYER, corrected), self.events(second, player(corrected)))
        # A member's promotion is the world's; expulsion stays with the character.
        send(second, later + 1, (guild, member | net.FACTION_EXPELLED, 3, 12))
        promoted = net.pack_factions([(guild, net.FACTION_RANK | net.FACTION_REPUTATION,
                                       3, 12)])[0]
        self.assertIn((net.EVENT_PLAYER, promoted), self.events(first, player(promoted)))
        path = folder / net.STREAM_NAME
        self.wait_for(lambda: (net.PlayerStream(str(path)).factions or {}).get('fighters guild')
                      == [guild, 3, 12, net.FACTION_JOINED | net.FACTION_EXPELLED],
                      'second character factions were not persisted')
        mine = next((world / 'characters').glob('*/Nerevar'))
        data = net.PlayerStream(str(mine / net.STREAM_NAME)).character_file()
        name = net.checkpoint_name(data, net.CHARACTER_FILE)
        (mine / name).write_bytes(data)
        again = self.client(1)
        again.session ^= 4
        again.join()
        self.game(again, 1, 9, name.encode(), net.GAME_NEW)
        replay = self.events(again, ready)
        self.assertIn((net.EVENT_PLAYER, net.pack_factions([(guild, member, 3, 12)])[0]), replay)

    def test_world_globals_are_shared_and_replayed(self):
        net = tes3x_net
        world = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, world)
        self.start('--world', str(world), '--adopt')
        ready = lambda k, d: k == net.EVENT_PLAYER and d == bytes([net.PLAYER_READY, 0])
        first, second = self.client(1), self.client(2)
        first.join()
        self.game(first, 1, 7, b'')
        self.upload(first, 2, 1, b'mp-first.ess', self.save(b'Nerevar', 0))
        self.events(first, ready)
        seq = self.seed_identity(world, first, 3)
        second.join()
        self.game(second, 1, 8, b'')
        self.upload(second, 2, 2, b'mp-second.ess', self.save(b'Second', 1))
        self.events(second, ready)
        later = 3

        def player(data):
            return lambda k, d: k == net.EVENT_PLAYER and d == data

        votes = net.pack_globals([('HortatorVotes', 3.0)], net.WORLD_GLOBALS)[0]
        self.assertTrue(valid_client_event(net.EVENT_PLAYER, votes))
        first.send(net.EVENTS, net.pack_events(first.delivered, [
            (seq, net.EVENT_PLAYER, 0, votes)]))
        self.assertIn((net.EVENT_PLAYER, votes), self.events(second, player(votes)))
        # The latest writer wins and reaches the first console.
        five = net.pack_globals([('hortatorvotes', 5.0)], net.WORLD_GLOBALS)[0]
        second.send(net.EVENTS, net.pack_events(second.delivered, [
            (later, net.EVENT_PLAYER, 0, five)]))
        kept = net.pack_globals([('HortatorVotes', 5.0)], net.WORLD_GLOBALS)[0]
        self.assertIn((net.EVENT_PLAYER, kept), self.events(first, player(kept)))
        mine = next((world / 'characters').glob('*/Nerevar'))
        stream = net.PlayerStream(str(mine / net.STREAM_NAME))
        data = stream.character_file([('HortatorVotes', 5.0)])
        self.assertIn(struct.pack('<H', len(kept)) + kept, data)
        name = net.checkpoint_name(data, net.CHARACTER_FILE)
        (mine / name).write_bytes(data)
        again = self.client(1)
        again.session ^= 4
        again.join()
        self.game(again, 1, 9, name.encode(), net.GAME_NEW)
        replay = self.events(again, ready)
        self.assertIn((net.EVENT_PLAYER, kept), replay)
        self.assertLess(replay.index((net.EVENT_PLAYER, kept)),
                        replay.index((net.EVENT_PLAYER, bytes([net.PLAYER_READY, 0]))))

    def test_world_globals_keep_bits_through_a_restart(self):
        net = tes3x_net
        tiny = struct.unpack('<f', struct.pack('<I', 0x002E6000))[0]  # VampClan's master bits
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'world.json')
            world = net.World()
            world.path = path
            server = net.Server.__new__(net.Server)
            server.world, server.clients = world, {}
            client = SimpleNamespace(id=1)
            entries = [('VampClanLike', tiny), ('wearingHelmHHDALike', 2.3e20)]
            self.assertEqual(server.share_globals(client, entries, 0.0),
                             f'world globals VampClanLike {tiny:g}, wearingHelmHHDALike 2.3e+20')
            self.assertIsNone(server.share_globals(client, [('vampclanlike', tiny)], 0.0))
            world.save(0.0)
            restored = net.World()
            self.assertTrue(restored.load(path, 0.0))
            self.assertEqual(struct.pack('<f', restored.globals['vampclanlike'][1]),
                             struct.pack('<I', 0x002E6000))
            # Bounded: past the limit new names are dropped, known ones still change.
            restored.globals.update({f'g{i}': [f'g{i}', 0.0]
                                     for i in range(net.WORLD_GLOBALS_MAX - 2)})
            server.world = restored
            self.assertIsNone(server.share_globals(client, [('NewGlobal', 1.0)], 0.0))
            self.assertIsNotNone(server.share_globals(client, [('g0', 1.0)], 0.0))
            for invalid in (bytes([net.WORLD_GLOBALS, 1]) + struct.pack('<f', float('inf'))
                            + b'HortatorVotes\0',
                            bytes([net.WORLD_GLOBALS, 2]) + struct.pack('<f', 1.0) + b'x\0'):
                self.assertFalse(valid_client_event(net.EVENT_PLAYER, invalid))

    def test_script_locals_events_are_checked_and_bounded(self):
        net = tes3x_net
        values = {i: i for i in range(net.LOCALS_PER_EVENT + 1)}
        events = net.pack_locals(0x0101F7C4, values)
        self.assertEqual(len(events), 2)
        for data in events:
            self.assertLessEqual(len(data), net.EVENT_DATA)
            self.assertTrue(valid_client_event(net.EVENT_LOCALS, data))
        self.assertEqual(dict(kv for data in events for kv in net.unpack_locals(data)[1].items()),
                         values)
        self.assertEqual(net.describe_local(net.LOCAL_SHORT << 14 | 2, 0xFFFFFFFF), 's2 -1')
        self.assertEqual(net.describe_local(net.LOCAL_FLOAT << 14, 0x41480000), 'f0 12.5')
        for invalid in (b'', struct.pack('<IB', 1, 0), struct.pack('<IB', 1, 2) + net.LOCAL.pack(0, 1),
                        struct.pack('<IB', 1, 1) + net.LOCAL.pack(3 << 14, 1),
                        struct.pack('<IB', 1, 2) + net.LOCAL.pack(0, 1) * 2):
            self.assertFalse(valid_client_event(net.EVENT_LOCALS, invalid))
        server = net.Server.__new__(net.Server)
        server.world, server.detail = net.World(), {'verbose': False}
        client = SimpleNamespace(id=1)
        server.world.locals = {1: {i: 0 for i in range(net.LOCALS_MAX - 1)}}
        server.event_locals(client, net.EVENT_LOCALS, net.pack_locals(2, {0: 5, 1: 6})[0], '', 0.0)
        self.assertEqual(server.world.locals[2], {0: 5})
        server.event_locals(client, net.EVENT_LOCALS, net.pack_locals(2, {0: 7})[0], '', 0.0)
        self.assertEqual(server.world.locals[2], {0: 7})

    def test_actor_states_are_kept_and_sent_to_a_cell_s_next_authority(self):
        net = tes3x_net
        server = net.Server.__new__(net.Server)
        server.world, server.detail = net.World(), {'verbose': False}
        key = (net.KEY_EXTERIOR, -2, -9, b'')
        values = (0x0101F7C4, -11000.0, -70800.0, 233.0, 1.5, 25.0, 0, 82.0, 154.0, 0,
                  net.NO_ANIM)
        server.keep_actor(0x0101F7C4, key, values)
        server.world.spawns[0xFF000001] = {'summon': True, 'removed': False}
        server.keep_actor(0xFF000001, key, (0xFF000001,) + values[1:])
        server.world.spawns[0xFF000002] = {'removed': True}
        server.keep_actor(0xFF000002, key, (0xFF000002,) + values[1:])
        self.assertEqual(set(server.world.actors), {0x0101F7C4, 0xFF000002})
        # A removed spawn is kept but not placed; another cell's actors are not sent.
        events = server.actor_places(key)
        self.assertEqual(events, [struct.pack('<I', 1) + net.ACTOR_PLACE.pack(
            0x0101F7C4, -11000.0, -70800.0, 233.0, 1.5, 25.0, 82.0, 154.0, 0)])
        self.assertEqual(server.actor_places((net.KEY_EXTERIOR, -2, -8, b'')), [])
        self.assertLessEqual(4 + net.ACTOR_PLACES_PER_EVENT * net.ACTOR_PLACE.size,
                             net.EVENT_DATA)
        inside = (net.KEY_INTERIOR, 0, 0, b"Arrille's Tradehouse")
        for refid in range(1, 4):
            server.keep_actor(refid, inside, (refid,) + values[1:])
        self.assertEqual([struct.unpack_from('<I', d)[0] for d in server.actor_places(inside)],
                         [2, 1])

        # Through the world file.
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder)
        server.world.path = str(folder / 'world.json')
        server.world.save(0.0)
        restored = net.World()
        restored.load(server.world.path, 0.0)
        restored.spawns = server.world.spawns
        server.world = restored
        self.assertEqual(server.actor_places(key), events)

        # Bounded: a new actor past the limit is dropped, a known one still changes.
        server.world.actors.update({0x02000000 + i: list(restored.actors[0x0101F7C4])
                                    for i in range(net.ACTORS_KEPT_MAX - len(restored.actors))})
        server.keep_actor(0x03000000, key, (0x03000000,) + values[1:])
        self.assertNotIn(0x03000000, server.world.actors)
        server.keep_actor(0x0101F7C4, key, values[:5] + (10.0,) + values[6:])
        self.assertEqual(server.world.actors[0x0101F7C4][8], 10.0)
        for refid in [r for r in server.world.actors if r >= 0x02000000]:
            del server.world.actors[refid]

        # Only a cell nobody had sends them, to its new authority, before AUTHORITY.
        def client(cid, state):
            queued = []
            return SimpleNamespace(id=cid, alive=True, state=state, busy=None, known={},
                                   loaded=set(), rel=SimpleNamespace(
                                       queue=lambda k, s, d: queued.append(k)), queued=queued)
        state = net.STATE_BODY.pack(net.IN_WORLD, -10500.0, -71000.0, 200.0, 0.0, b'')
        first = client(1, state)
        server.clients = {1: first}
        server.owners, server.bot = {}, SimpleNamespace(state=None)
        server.flush = server.update_owners = lambda *a: None
        server.update_authority(0.0)
        place_at = first.queued.index(net.EVENT_ACTOR_PLACE)
        self.assertEqual(first.queued[place_at + 1:].count(net.EVENT_ACTOR_PLACE), 0)
        self.assertEqual(first.queued[place_at + 1], net.EVENT_AUTHORITY)
        second = client(2, state)
        server.clients[2] = second
        server.update_authority(0.0)
        self.assertNotIn(net.EVENT_ACTOR_PLACE, second.queued)
        self.assertIn(net.EVENT_AUTHORITY, second.queued)
        self.assertFalse(valid_client_event(net.EVENT_ACTOR_PLACE, events[0]))

    def fake_server(self):
        net = tes3x_net
        server = net.Server.__new__(net.Server)
        server.world, server.detail, server.clients = net.World(), {'verbose': False}, {}
        server.script_runners = {}
        server.flush = lambda *a, **k: None
        return server

    def fake_client(self, server, client_id):
        sent = []
        client = SimpleNamespace(id=client_id, alive=True, in_world=True, synced=True, sent=sent,
                                 rel=SimpleNamespace(queue=lambda k, o, d: sent.append((k, o, d))))
        server.clients[client_id] = client
        return client

    def test_rules_flags_follow_the_level_up_option_and_are_server_only(self):
        net = tes3x_net
        server = self.fake_server()
        server.args = SimpleNamespace(level_up='rest')
        self.assertEqual(server.rules(), 0)
        server.args.level_up = 'auto'
        self.assertEqual(server.rules(), net.RULE_AUTO_LEVEL)
        self.assertFalse(valid_client_event(net.EVENT_RULES, b''))

    def rest_server(self, count):
        net = tes3x_net
        server = self.fake_server()
        server.rest_menus, server.rest_hours, server.rest_by = set(), None, None
        server.identities = {}
        server.world.clock = net.Clock(10.0, 5, 3, 427, 100, 30.0, 0.0)
        clients = [self.fake_client(server, i + 1) for i in range(count)]
        for client in clients:
            client.character, client.events = None, 0
        return server, clients

    def rest_event(self, server, client, *op):
        net = tes3x_net
        data = bytes([op[0]]) + (struct.pack('<H', op[1]) if len(op) > 1 else b'')
        self.assertTrue(valid_client_event(net.EVENT_REST, data))
        server.on_event(client, net.EVENT_REST, data, '', 0.0)

    def starts(self, client):
        net = tes3x_net
        return [d for k, _, d in client.sent if k == net.EVENT_REST]

    def test_a_rest_starts_when_every_console_has_its_menu_open(self):
        net = tes3x_net
        server, (first, second) = self.rest_server(2)
        self.rest_event(server, first, net.REST_OPEN)
        self.rest_event(server, first, net.REST_ASK, 8)
        text = [d for k, _, d in second.sent if k == net.EVENT_TEXT]
        self.assertIn(b'Player 1 wants to rest for 8 hours. Open your rest menu to agree.', text)
        self.assertEqual(self.starts(first) + self.starts(second), [])
        self.rest_event(server, second, net.REST_OPEN)
        start = struct.pack('<BH', net.REST_START, 8)
        self.assertEqual(self.starts(first), [start])
        self.assertEqual(self.starts(second), [start])
        self.assertEqual(server.world.clock.hour, 18.0)
        self.assertIsNone(server.rest_hours)
        # the menus closing as the rests begin are no cancel
        self.rest_event(server, first, net.REST_CLOSE)
        self.rest_event(server, second, net.REST_CLOSE)
        self.assertEqual(self.starts(second), [start])

    def test_a_rest_alone_starts_at_once_and_rolls_the_day(self):
        net = tes3x_net
        server, (only,) = self.rest_server(1)
        self.rest_event(server, only, net.REST_ASK, 20)
        self.assertEqual(self.starts(only), [struct.pack('<BH', net.REST_START, 20)])
        self.assertEqual((server.world.clock.hour, server.world.clock.day), (6.0, 6))

    def test_closing_the_menu_or_leaving_withdraws_the_ask(self):
        net = tes3x_net
        server, (first, second) = self.rest_server(2)
        server.scripts_orphaned = lambda *a: None
        server.dialogues = {}
        server.send = lambda *a: None
        self.rest_event(server, first, net.REST_ASK, 4)
        self.rest_event(server, first, net.REST_CLOSE)
        self.assertIsNone(server.rest_hours)
        self.rest_event(server, second, net.REST_OPEN)
        self.assertEqual(self.starts(first) + self.starts(second), [])
        # the console that asked leaves: the other opening its menu starts nothing
        self.rest_event(server, second, net.REST_CLOSE)
        self.rest_event(server, first, net.REST_OPEN)
        self.rest_event(server, first, net.REST_ASK, 4)
        server.clients[1].lobby = True
        server.clients[1].alive = False
        server.clients[1].in_world = False
        server.leave(server.clients[1])
        self.assertIsNone(server.rest_hours)
        self.assertEqual(self.starts(second), [])

    def test_a_console_leaving_completes_the_rest_the_others_agreed(self):
        net = tes3x_net
        server, (first, second, third) = self.rest_server(3)
        server.scripts_orphaned = lambda *a: None
        server.dialogues = {}
        server.send = lambda *a: None
        self.rest_event(server, first, net.REST_ASK, 2)
        self.rest_event(server, second, net.REST_OPEN)
        self.assertEqual(self.starts(first), [])
        third.alive = third.in_world = False
        third.lobby, third.announced, third.relaunching = True, False, False
        server.leave(third)
        self.assertEqual(self.starts(first), [struct.pack('<BH', net.REST_START, 2)])

    def test_rest_events_are_checked(self):
        net = tes3x_net
        for data in (b'', b'\x09', b'\x00\x00', bytes([net.REST_ASK]),
                     struct.pack('<BH', net.REST_ASK, 0),
                     struct.pack('<BH', net.REST_ASK, net.REST_HOURS_MAX + 1),
                     bytes([net.REST_START])):
            self.assertFalse(valid_client_event(net.EVENT_REST, data), data)

    def test_actor_moves_keep_their_first_cell_and_reach_late_joins(self):
        net = tes3x_net
        server = self.fake_server()
        first, second = self.fake_client(server, 1), self.fake_client(server, 2)
        fargoth = 0x0101F7C4
        server.world.actors[fargoth] = [net.KEY_EXTERIOR, -2, -9, '', 0, 0, 0, 0, 25, 0, 0, 0]
        move = net.ACTOR_MOVE.pack(fargoth, 40, 900, 4733.0, 3911.0, 14328.0, 0.0)
        self.assertTrue(valid_client_event(net.EVENT_ACTOR_MOVE, move))
        server.event_actor_move(first, net.EVENT_ACTOR_MOVE, move, '', 0.0)
        self.assertEqual(server.world.moves[fargoth], [40, 900, 4733.0, 3911.0, 14328.0, 0.0])
        self.assertNotIn(fargoth, server.world.actors)  # its next cell's report replaces it
        self.assertEqual(second.sent, [(net.EVENT_ACTOR_MOVE, 1, move)])
        self.assertEqual(first.sent, [])
        # The same move again is not news; a later one keeps the cell the actor first left.
        server.event_actor_move(second, net.EVENT_ACTOR_MOVE, move, '', 0.0)
        self.assertEqual(first.sent, [])
        onward = net.ACTOR_MOVE.pack(fargoth, 900, 901, 620.13, -1160.73, 284.33, 189.1)
        server.event_actor_move(second, net.EVENT_ACTOR_MOVE, onward, '', 0.0)
        relayed = net.ACTOR_MOVE.pack(fargoth, 40, 901, 620.13, -1160.73, 284.33, 189.1)
        self.assertEqual(first.sent, [(net.EVENT_ACTOR_MOVE, 2, relayed)])
        # Through the world file; bounded.
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder)
        server.world.path = str(folder / 'world.json')
        server.world.save(0.0)
        restored = net.World()
        restored.load(server.world.path, 0.0)
        self.assertEqual(net.ACTOR_MOVE.pack(fargoth, *restored.moves[fargoth]), relayed)
        server.world = restored
        server.world.moves.update({0x02000000 + i: [1, 2, 0.0, 0.0, 0.0, 0.0]
                                   for i in range(net.ACTOR_MOVES_MAX - 1)})
        extra = net.ACTOR_MOVE.pack(0x03000000, 1, 2, 0.0, 0.0, 0.0, 0.0)
        server.event_actor_move(first, net.EVENT_ACTOR_MOVE, extra, '', 0.0)
        self.assertNotIn(0x03000000, server.world.moves)
        for invalid in (net.ACTOR_MOVE.pack(5, 1, 2, 0.0, 0.0, 0.0, 0.0),
                        net.ACTOR_MOVE.pack(fargoth, 1, net.NO_CELL, 0.0, 0.0, 0.0, 0.0),
                        net.ACTOR_MOVE.pack(fargoth, 1, 2, float('nan'), 0.0, 0.0, 0.0),
                        net.ACTOR_MOVE.pack(fargoth, 1, 2, 0.0, 0.0, 0.0, 1e6), move[:-1]):
            self.assertFalse(valid_client_event(net.EVENT_ACTOR_MOVE, invalid))

    def test_ai_packages_name_a_player_target_per_console_and_reach_late_joins(self):
        net = tes3x_net
        server = self.fake_server()
        first, second = self.fake_client(server, 1), self.fake_client(server, 2)
        first.character, second.character = 'Nerevar', 'Vivec'
        fargoth = 0x0101F7C4
        anywhere = struct.pack('<3fB3x', net.AI_NO_DESTINATION, net.AI_NO_DESTINATION, 0.0, 0)
        follow = net.pack_ai_package(fargoth, net.AI_FOLLOW, net.AI_TARGET_PLAYER, net.NO_CELL, 0,
                                     anywhere, 1)
        self.assertTrue(valid_client_event(net.EVENT_AI_PACKAGE, follow))
        server.event_ai_package(first, net.EVENT_AI_PACKAGE, follow, '', 0.0)
        self.assertEqual(server.world.ai[fargoth]['target'], ['player', 'Nerevar'])
        # The others follow the sender's ghost; the sender is not told.
        as_ghost = net.pack_ai_package(fargoth, net.AI_FOLLOW, net.AI_TARGET_CLIENT, net.NO_CELL,
                                       1, anywhere, 1)
        self.assertEqual(second.sent, [(net.EVENT_AI_PACKAGE, 0, as_ghost)])
        self.assertEqual(first.sent, [])
        # A package without a target, and one naming an actor, are relayed as they are.
        wander = struct.pack('<HBB8B4x', 512, 0, 0, 40, 20, 20, 0, 0, 0, 0, 0)
        server.event_ai_package(second, net.EVENT_AI_PACKAGE, net.pack_ai_package(
            0x01077190, net.AI_WANDER, 0, net.NO_CELL, 0, wander, 0), '', 0.0)
        escort = net.pack_ai_package(0x0107718F, net.AI_ESCORT, net.AI_TARGET_OBJECT, 900, 0,
                                     struct.pack('<3fB3x', 620.0, -1160.0, 284.0, 2), 1,
                                     'marena gilnith')
        server.event_ai_package(second, net.EVENT_AI_PACKAGE, escort, '', 0.0)
        self.assertEqual([d for _, _, d in first.sent][-1], escort)
        # Through the world file; after a restart the sender's character is told it is the
        # target, and a console that joins while it is away is not sent that package.
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder)
        server.world.path = str(folder / 'world.json')
        server.world.save(0.0)
        restored = net.World()
        restored.load(server.world.path, 0.0)
        self.assertEqual(restored.ai, server.world.ai)
        later = self.fake_server()
        later.world = restored
        third = self.fake_client(later, 3)
        third.character = 'Vivec'
        self.assertIsNone(later.ai_package_for(third, fargoth, restored.ai[fargoth]))
        own = net.pack_ai_package(fargoth, net.AI_FOLLOW, net.AI_TARGET_PLAYER, net.NO_CELL, 0,
                                  anywhere, 1)
        third.character = 'Nerevar'
        self.assertEqual(later.ai_package_for(third, fargoth, restored.ai[fargoth]), own)
        # A player target with no character is not kept; the table is bounded.
        first.character = None
        server.event_ai_package(first, net.EVENT_AI_PACKAGE, net.pack_ai_package(
            0x01000005, net.AI_FOLLOW, net.AI_TARGET_PLAYER, net.NO_CELL, 0, anywhere, 1), '', 0.0)
        self.assertNotIn(0x01000005, server.world.ai)
        server.world.ai.update({0x02000000 + i: server.world.ai[fargoth]
                                for i in range(net.AI_PACKAGES_MAX - len(server.world.ai))})
        server.event_ai_package(second, net.EVENT_AI_PACKAGE, net.pack_ai_package(
            0x03000000, net.AI_WANDER, 0, net.NO_CELL, 0, wander, 0), '', 0.0)
        self.assertNotIn(0x03000000, server.world.ai)
        nowhere = struct.pack('<3fB3x', float('nan'), 0.0, 0.0, 0)
        for invalid in (follow[:-1], follow + b'x\0',
                        net.pack_ai_package(5, net.AI_WANDER, 0, net.NO_CELL, 0, wander, 0),
                        net.pack_ai_package(fargoth, 5, 0, net.NO_CELL, 0, wander, 0),
                        net.pack_ai_package(fargoth, net.AI_FOLLOW, 0, net.NO_CELL, 0, anywhere, 1),
                        net.pack_ai_package(fargoth, net.AI_TRAVEL, 0, net.NO_CELL, 0, nowhere, 0),
                        net.pack_ai_package(fargoth, net.AI_FOLLOW, net.AI_TARGET_OBJECT,
                                            net.NO_CELL, 0, anywhere, 1)):
            self.assertFalse(valid_client_event(net.EVENT_AI_PACKAGE, invalid))

    def test_world_scripts_are_kept_handed_on_and_end_with_their_last_runner(self):
        net = tes3x_net
        server = self.fake_server()
        first, second = self.fake_client(server, 1), self.fake_client(server, 2)

        def take(client, data):
            self.assertTrue(valid_client_event(net.EVENT_PLAYER, data))
            return server.world_script(client, *net.unpack_world_script(data), 0.0)

        def handed(client):
            events = [net.unpack_world_script(d) for k, _, d in client.sent
                      if k == net.EVENT_PLAYER and d[0] == net.WORLD_SCRIPTS]
            client.sent.clear()
            return events

        timer, values = 'MatchMakerTimer', {0: 3, 1: 15}
        take(first, net.pack_world_script(net.SCRIPT_RUNNING, 0, timer)[0])
        for data in net.pack_world_script(net.SCRIPT_VALUES, 0, timer, values):
            take(first, data)
        # A second runner's locals are not the world's while the first runs it.
        take(second, net.pack_world_script(net.SCRIPT_RUNNING, 0, timer)[0])
        take(second, net.pack_world_script(net.SCRIPT_VALUES, 0, timer, {0: 9})[0])
        self.assertEqual(server.world.scripts['matchmakertimer'],
                         {'id': timer, 'ref': 0, 'values': values})
        # The first runner leaving leaves it with the second; a script nobody else runs is
        # handed on with its locals first.
        fons = 'MatchMakeFons'
        take(first, net.pack_world_script(net.SCRIPT_RUNNING, 0x0101F7C4, fons)[0])
        take(first, net.pack_world_script(net.SCRIPT_VALUES, 0x0101F7C4, fons, {0x8000: 7})[0])
        server.scripts_orphaned(1, 0.0)
        self.assertEqual(handed(second), [(net.SCRIPT_VALUES, 0x0101F7C4, fons, {0x8000: 7}),
                                          (net.SCRIPT_RUNNING, 0x0101F7C4, fons, {})])
        self.assertEqual(server.script_runners['matchmakertimer'], [2])
        take(second, net.pack_world_script(net.SCRIPT_VALUES, 0, timer, {0: 4})[0])
        self.assertEqual(server.world.scripts['matchmakertimer']['values'], {0: 4, 1: 15})
        # It ends when its last runner stops it.
        take(second, net.pack_world_script(net.SCRIPT_STOPPED, 0, fons)[0])
        self.assertNotIn('matchmakefons', server.world.scripts)
        # After a restart nobody runs the rest: the first player ready takes them.
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder)
        server.world.path = str(folder / 'world.json')
        server.world.save(0.0)
        restored = net.World()
        restored.load(server.world.path, 0.0)
        self.assertEqual(restored.scripts, server.world.scripts)
        server.world, server.script_runners = restored, {}
        replay = [net.unpack_world_script(d) for d in server.scripts_assign(first)]
        self.assertEqual(replay, [(net.SCRIPT_VALUES, 0, timer, {0: 4, 1: 15}),
                                  (net.SCRIPT_RUNNING, 0, timer, {})])
        self.assertEqual(server.scripts_assign(second), [])
        # Bounded in scripts and in locals per script.
        many = {i: i for i in range(net.SCRIPT_VALUES_MAX + 5)}
        for data in net.pack_world_script(net.SCRIPT_VALUES, 0, timer, many):
            self.assertLessEqual(len(data), net.EVENT_DATA)
            take(first, data)
        self.assertEqual(len(server.world.scripts['matchmakertimer']['values']),
                         net.SCRIPT_VALUES_MAX)
        server.world.scripts.update({f's{i}': {'id': f's{i}', 'ref': 0, 'values': {}}
                                     for i in range(net.WORLD_SCRIPTS_MAX - 1)})
        take(first, net.pack_world_script(net.SCRIPT_RUNNING, 0, 'OneTooMany')[0])
        self.assertNotIn('onetoomany', server.world.scripts)
        for invalid in (bytes([net.WORLD_SCRIPTS, 4, 0, 0, 0, 0]) + b'x\0\0',
                        bytes([net.WORLD_SCRIPTS, 1, 0, 0, 0, 0]) + b'\0\0',
                        bytes([net.WORLD_SCRIPTS, 3, 0, 0, 0, 0]) + b'x\0\1',
                        bytes([net.WORLD_SCRIPTS, 3, 0, 0, 0, 0]) + b'x\0\1'
                        + net.LOCAL.pack(3 << 14, 0)):
            self.assertFalse(valid_client_event(net.EVENT_PLAYER, invalid))
        with self.assertRaises(ValueError):
            net.pack_world_script(net.SCRIPT_RUNNING, 0, 'x' * (net.SCRIPT_ID_MAX + 1))

    def test_stream_only_character_creation_snapshot_and_restart(self):
        world = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, world)
        self.start('--world', str(world))
        net = tes3x_net
        client = self.client(1)
        client.join()
        self.game(client, 1, 7, b'', net.GAME_NEW)
        self.events(client, lambda k, d: k == net.EVENT_NEWCHAR and d[0] + 1 == d[1])
        client.send(net.EVENTS, net.pack_events(client.delivered, [
            (2, net.EVENT_PICK, 0, bytes([net.PICK_START, 1]))]))
        self.events(client, lambda k, d: k == net.EVENT_RUN and d == b'\0')
        seq = self.seed_identity(world, client, 3)
        place = net.STATE_BODY.pack(net.IN_WORLD | net.INTERIOR,
                                    100.0, 200.0, 30.0, 1.5, b"Arrille's Tradehouse")
        items = net.pack_items('Gold_001', [[123, 0, 0, 0]])
        topics = [f'learned topic {i} with a long name' for i in range(9)]
        topic_events = net.pack_topics(topics)
        client.send(net.EVENTS, net.pack_events(client.delivered, [
            (seq, net.EVENT_PLAYER, 0, items[0]),
            *((seq + 1 + i, net.EVENT_PLAYER, 0, data)
              for i, data in enumerate(topic_events)),
            (seq + 1 + len(topic_events), net.EVENT_SNAPSHOT, 0,
             struct.pack('<I', 17) + place)]))
        ack = self.events(client, lambda k, d: k == net.EVENT_SNAPSHOT)
        self.assertIn((net.EVENT_SNAPSHOT, struct.pack('<I', 17)), ack)
        folder = self.character(world)
        self.assertEqual(list(folder.glob('*.ess')), [])
        saved = net.PlayerStream(str(folder / net.STREAM_NAME))
        self.assertEqual(saved.items, {'Gold_001': [[123, 0, 0, 0]]})
        self.assertEqual(saved.place, place)
        self.assertEqual(saved.topics, topics)
        state = json.loads(next(world.glob('*.json')).read_text())
        self.assertEqual(state['topics'], topics)
        # Kill immediately after the acknowledgement: storage must already be complete.
        self.server.kill()
        self.server.communicate()
        self.start('--world', str(world))
        again = self.client(1)
        again.join()
        self.game(again, 1, 8, b'old.ess')
        names = self.events(again, lambda k, d: k == net.EVENT_CHARS)
        self.assertIn((net.EVENT_CHARS, b'\0\1Nerevar\0'), names)
        again.send(net.EVENTS, net.pack_events(again.delivered, [
            (2, net.EVENT_PICK, 0, bytes([net.PICK_CHARACTER, 0]))]))
        loads = self.events(again, lambda k, d: k == net.EVENT_LOAD)
        load = next(d for k, d in loads if k == net.EVENT_LOAD)
        self.assertTrue(load.endswith(b'.t3c\0'))
        third = self.client(1)
        third.session ^= 4
        third.join()
        self.game(third, 1, 9, load[:-1], net.GAME_NEW)
        replay = self.events(third, lambda k, d: k == net.EVENT_PLAYER and
                             d == bytes([net.PLAYER_READY, 0]))
        self.assertIn((net.EVENT_PLAYER, bytes([net.PLAYER_PLACE]) + place), replay)
        self.assertIn((net.EVENT_PLAYER, items[0]), replay)
        self.assertEqual([d for k, d in replay if k == net.EVENT_PLAYER and
                          d[0] == net.PLAYER_TOPICS], topic_events)

    def test_characters_kept_before_the_list_move_into_a_folder(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root)
        (root / 'mp-hero.ess').write_bytes(self.save(b'Nerevar', 0))
        (root / 'mp-hero.1.ess').write_bytes(self.save(b'Nerevar', 1))
        (root / tes3x_net.STREAM_NAME).write_text('{}')
        kept = tes3x_net.kept_characters(str(root), fixtures=True)
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
        modifiers = bytes([net.PLAYER_MODIFIERS, 3]) + b''.join((
            net.MODIFIER.pack(0, 35.0), net.MODIFIER.pack(7, 90.0),
            net.MODIFIER.pack(8 + 5, 57.0)))
        self.assertIn('Strength current 35', stream.take(modifiers))
        self.assertEqual(stream.modifiers, {0: 35.0, 7: 90.0, 13: 57.0})
        self.assertIn(modifiers, stream.replay())

    def test_topics_survive_restart_without_a_checkpoint(self):
        net = tes3x_net
        names = ['topic ' + str(i) + ' with a long name' for i in range(18)]
        names += ['x' * 63, 'a\x92b']
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'stream.json'
            stream = net.PlayerStream(str(path))
            self.assertIsNone(stream.topics)
            stream.take(bytes([net.PLAYER_TOPICS, 0]))
            self.assertEqual(stream.topics, [])
            for data in net.pack_topics(names):
                self.assertLessEqual(len(data), net.EVENT_DATA)
                stream.take(data)
            stream.take(net.pack_topics([names[0].upper()])[0])
            self.assertEqual(len(stream.topics), len(names))
            stream.save()
            restored = net.PlayerStream(str(path))
            self.assertEqual(restored.topics, stream.topics)
            replay = [p for p in restored.replay() if p[0] == net.PLAYER_TOPICS]
            receiver = net.PlayerStream(Path(folder) / 'new.json')
            for part in replay:
                receiver.take(part)
            self.assertEqual(receiver.topics, restored.topics)
            before = list(receiver.topics)
            for invalid in (bytes([net.PLAYER_TOPICS, 2]) + b'one\0',
                            bytes([net.PLAYER_TOPICS, 1]) + b'one',
                            bytes([net.PLAYER_TOPICS, 1]) + b'bad"name\0'):
                receiver.take(invalid)
                self.assertEqual(receiver.topics, before)
            restored.reset()
            restored.save()
            self.assertIsNone(net.PlayerStream(path).topics)
            # Existing worlds acquire topics on the next READY publication.
            path.write_text('{}', encoding='utf-8')
            self.assertIsNone(net.PlayerStream(path).topics)

    def test_factions_survive_restart_and_the_world_replays_them(self):
        net = tes3x_net
        entries = [(f'Faction {i:02} long id', net.FACTION_ALL | net.FACTION_JOINED, i, -i)
                   for i in range(9)]
        entries.append(('Imperial Legion', net.FACTION_ALL | net.FACTION_EXPELLED, -1, 3))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'stream.json'
            stream = net.PlayerStream(str(path))
            self.assertIsNone(stream.factions)
            for data in net.pack_factions(entries):
                self.assertLessEqual(len(data), net.EVENT_DATA)
                self.assertTrue(valid_client_event(net.EVENT_PLAYER, data))
                stream.take(data)
            stream.save()
            restored = net.PlayerStream(str(path))
            self.assertEqual(restored.factions['imperial legion'],
                             ['Imperial Legion', -1, 3, net.FACTION_EXPELLED])
            self.assertEqual(len(restored.factions), len(entries))
            partial = net.pack_factions([('Faction 01 long id', net.FACTION_RANK, 5, 0)])[0]
            self.assertFalse(valid_client_event(net.EVENT_PLAYER, partial))
            server = net.Server.__new__(net.Server)
            server.world = net.World()
            replay = [e for data in server.faction_replay(restored, True)
                      for e in net.unpack_factions(data)]
            self.assertEqual(sorted(replay), sorted(entries))
            # Without a replay only the world's values go out, never a non-member's rank.
            replay = [e for data in server.faction_replay(restored, False)
                      for e in net.unpack_factions(data)]
            self.assertIn(('Imperial Legion', net.FACTION_REPUTATION, 0, 3), replay)
            restored.reset()
            restored.save()
            self.assertIsNone(net.PlayerStream(path).factions)

    def test_player_globals_survive_restart_and_seed_the_new_game(self):
        net = tes3x_net
        entries = [(f'PCGlobal{i:02}WithALongerName', float(i) - 0.5) for i in range(12)]
        entries.append(('PCVampire', -1.0))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'stream.json'
            stream = net.PlayerStream(str(path))
            self.assertIsNone(stream.globals)
            events = net.pack_globals(entries)
            self.assertGreater(len(events), 1)
            for data in events:
                self.assertLessEqual(len(data), net.EVENT_DATA)
                self.assertTrue(valid_client_event(net.EVENT_PLAYER, data))
                stream.take(data)
            self.assertEqual(stream.take(net.pack_globals([('pcvampire', 1.0)])[0]),
                             'globals pcvampire 1')
            self.assertIsNone(stream.take(net.pack_globals([('PCVampire', 1.0)])[0]))
            stream.save()
            restored = net.PlayerStream(str(path))
            self.assertEqual(restored.globals['pcvampire'], ['pcvampire', 1.0])
            self.assertEqual(len(restored.globals), len(entries))
            replayed = dict(e for data in restored.replay() if data[0] == net.PLAYER_GLOBALS
                            for e in net.unpack_globals(data))
            self.assertEqual(replayed, dict(restored.globals.values()))
            restored.identity = self.identity('Nerevar')
            seed = restored.character_file()
            for data in net.pack_globals(sorted(restored.globals.values())):
                self.assertIn(struct.pack('<H', len(data)) + data, seed)
            for invalid in (bytes([net.PLAYER_GLOBALS, 1]) + struct.pack('<f', 1.0) + b'PC',
                            bytes([net.PLAYER_GLOBALS, 1]) + struct.pack('<f', float('nan'))
                            + b'PCRace\0',
                            bytes([net.PLAYER_GLOBALS, 1]) + struct.pack('<f', 1.0) + b'x' * 32
                            + b'\0'):
                self.assertFalse(valid_client_event(net.EVENT_PLAYER, invalid))
                self.assertIsNone(restored.take(invalid))
            restored.reset()
            restored.save()
            self.assertIsNone(net.PlayerStream(path).globals)

    def test_abilities_replay_before_absolute_statistics(self):
        net = tes3x_net
        with tempfile.TemporaryDirectory() as folder:
            stream = net.PlayerStream(str(Path(folder) / 'stream.json'))
        stream.spells = ['fortify ability']
        stream.level = net.LEVEL.pack(7, 0, *([0] * 11), 45.0, 100.0, 200.0,
                                      51.0, 50.0, 43.155, *([40.0] * 5))
        stream.skills = {5: [36.524, 0.0]}
        stream.modifiers = {0: 51.0, 2: 43.155, 13: 36.524}
        kinds = [event[0] for event in stream.replay()]
        self.assertLess(kinds.index(net.PLAYER_SPELLS), kinds.index(net.PLAYER_LEVEL))
        self.assertLess(kinds.index(net.PLAYER_SPELLS), kinds.index(net.PLAYER_SKILLS))
        self.assertLess(kinds.index(net.PLAYER_LEVEL), kinds.index(net.PLAYER_MODIFIERS))
        self.assertLess(kinds.index(net.PLAYER_SKILLS), kinds.index(net.PLAYER_MODIFIERS))

    def test_active_effect_snapshot_restart_and_removal(self):
        net = tes3x_net
        entry = dict(serial=17, source_type=1, index=0, caster_kind=1, flags=3, caster=0,
                     corprus=0.0, source='timed fortify', item='',
                     active=struct.pack('<BBhBBHHbB', 0, 0, 79, 0, 0, 60, 11, 0, 0).hex(),
                     resisted=25.0, magnitude=11, elapsed=12.5, cumulative=8.25,
                     state=5, condition=0x42c80000, charge=0x42480000,
                     definitions=(struct.pack('<Hbbiiiii', 79, -1, 0, 0, 0, 60, 11, 11)
                                  + struct.pack('<h', -1).ljust(24, b'\0') * 7).hex(),
                     source_name='Timed fortify', source_stats=(b'\0' * 12).hex(),
                     previous=[dict(item='', flags=0, condition=0, charge=0) for _ in range(5)])
        bound = dict(entry, source='bound dagger',
                     active=struct.pack('<BBhBBHHbB', 0, 0, 120, 0, 0, 60, 1, -1, 0).hex(),
                     definitions=(struct.pack('<Hbbiiiii', 120, -1, -1, 0, 0, 60, 1, 1)
                                  + struct.pack('<h', -1).ljust(24, b'\0') * 7).hex(),
                     previous=[dict(item='iron longsword', flags=1, condition=0x42c80000,
                                    charge=0)] + entry['previous'][1:])
        bound_body = b''.join(p[5:] for p in net.pack_player_effects([bound]))
        self.assertEqual(net.unpack_player_effects(bound_body), [bound])
        for offset, value in ((388, b'x' * 32), (420, struct.pack('<I', 2))):
            damaged = bytearray(bound_body)
            damaged[offset:offset + len(value)] = value
            with self.subTest(previous_offset=offset), self.assertRaises(ValueError):
                net.unpack_player_effects(damaged)
        with self.assertRaises(ValueError):
            net.pack_player_effects([dict(entry, previous=bound['previous'])])
        one = b''.join(p[5:] for p in net.pack_player_effects([entry]))
        for offset, bad_value in ((100, struct.pack('<f', -1)),
                                  (124, struct.pack('<i', 3)),
                                  (132, struct.pack('<i', -1)),
                                  (140, struct.pack('<i', 10))):
            damaged = bytearray(one)
            damaged[offset:offset + 4] = bad_value
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                net.unpack_player_effects(damaged)
        with self.assertRaises(ValueError):
            net.unpack_player_effects(one + one)
        with self.assertRaises(ValueError):
            net.pack_player_effects([dict(entry, source='x' * 32)])
        entries = [dict(entry, serial=i + 1, source_type=1 + i % 3) for i in range(64)]
        parts = net.pack_player_effects(entries)
        self.assertGreater(len(parts), 16)  # larger than the console's reliable queue
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'stream.json')
            stream = net.PlayerStream(path)
            for part in parts[:-1]:
                self.assertIsNone(stream.take(part))
                self.assertIsNone(stream.effects)
            self.assertEqual(stream.take(parts[-1]), 'has 64 active effects')
            self.assertEqual(stream.effects, entries)
            stream.save()
            loaded = net.PlayerStream(path)
            self.assertEqual(loaded.effects, entries)
            self.assertEqual(loaded.replay(), parts)
            # A changed total or an out-of-order part cannot replace the retained snapshot.
            self.assertIsNone(loaded.take(parts[0]))
            self.assertIsNone(loaded.take(parts[2]))
            self.assertIsNone(loaded.take(parts[-1]))
            self.assertEqual(loaded.effects, entries)
            bad = bytearray(parts[1])
            bad[3] -= 1
            loaded.take(parts[0])
            loaded.take(bad)
            self.assertEqual(loaded.effects, entries)
            empty = net.pack_player_effects([])[0]
            self.assertEqual(loaded.take(empty), 'has 0 active effects')
            loaded.save()
            self.assertEqual(net.PlayerStream(path).effects, [])
            loaded.reset()
            self.assertIsNone(loaded.effects)

    def test_active_effect_invalid_snapshot_is_atomic(self):
        net = tes3x_net
        with tempfile.TemporaryDirectory() as folder:
            stream = net.PlayerStream(str(Path(folder) / 'stream.json'))
        stream.effects = []
        self.assertIsNone(stream.take(bytes([net.PLAYER_EFFECTS]) + struct.pack('<HH', 0, 1) + b'x'))
        self.assertEqual(stream.effects, [])
        with self.assertRaises(ValueError):
            net.unpack_player_effects(b'\0' * net.PLAYER_EFFECT_BYTES)
        self.assertIsNone(stream.take(bytes([net.PLAYER_EFFECTS]) + struct.pack('<HH', 0, 0)))
        self.assertEqual(stream.effects, [])

    def admin(self, port, *words):
        run = subprocess.run([sys.executable, '-m', 'tes3x', 'net', 'admin',
                              '--port', str(port), *words],
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
        net = tes3x_net
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
        requested = self.events(client, lambda kind, _: kind == tes3x_net.EVENT_SAVE)
        token = next(d for k, d in requested if k == net.EVENT_SAVE)
        self.assertEqual(len(token), 4)
        self.assertIsNone(self.server.poll())
        seq = self.seed_identity(world, client, 3)
        place = net.STATE_BODY.pack(net.IN_WORLD, 1.0, 2.0, 3.0, 0.0, b'')
        client.send(net.EVENTS, net.pack_events(client.delivered, [
            (seq, net.EVENT_SNAPSHOT, 0, token + place)]))
        self.assertEqual(self.server.wait(5), 0)
        self.assertEqual(net.PlayerStream(str(self.character(world) / net.STREAM_NAME)).place,
                         place)

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
        started = time.monotonic()
        self.admin(admin, 'stop')
        self.assertEqual(self.server.wait(5), 0)
        self.assertGreaterEqual(time.monotonic() - started, 0.9)

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
        replies, end = 0, time.monotonic() + 1.0
        sock.settimeout(0.2)
        while time.monotonic() < end:
            try:
                data, _ = sock.recvfrom(4096)
            except socket.timeout:
                continue
            replies += data[5] == tes3x_net.HANDSHAKE2
            self.assertLessEqual(len(data), tes3x_net.HANDSHAKE_PAD)
        self.assertGreater(replies, 0)
        self.assertLessEqual(replies, tes3x_net.HANDSHAKE_RATE[0] + 2)

    def send_events(self, client, *events):
        """Send (kind, data) events from client, numbered on from its last."""
        first = getattr(client, 'sent', 0) + 1
        client.sent = first + len(events) - 1
        client.send(tes3x_net.EVENTS, tes3x_net.pack_events(getattr(client, 'delivered', 0), [
            (first + i, kind, 0, data) for i, (kind, data) in enumerate(events)]))

    def drain(self, client):
        end = time.monotonic() + 3.0
        while (left := end - time.monotonic()) > 0:
            body = client.receive(min(0.05, left), tes3x_net.EVENTS)
            if body is None:
                return
            for seq, *_ in tes3x_net.unpack_events(body)[1]:
                if seq == getattr(client, 'delivered', 0) + 1:
                    client.delivered = seq
            client.send(tes3x_net.EVENTS,
                        tes3x_net.pack_events(getattr(client, 'delivered', 0), []))
        self.fail('event stream did not drain')

    def relayed(self, sender, receiver, kind, data):
        """Whether an event reaches another console unchanged, judged by a TEXT marker after it,
        which arrives as the server's notice."""
        marker = b'marker %d' % (getattr(sender, 'sent', 0) + 2)
        self.send_events(sender, (kind, data), (tes3x_net.EVENT_TEXT, marker))
        got = self.events(receiver, lambda k, d: k == tes3x_net.EVENT_TEXT and d.endswith(marker))
        self.drain(sender)
        return (kind, data) in got[:-1]

    def test_which_events_reach_other_consoles(self):
        # Only valid client events may reach another console.
        self.start()
        net = tes3x_net
        sender, receiver = self.client(1), self.client(2)
        sender.join()
        receiver.join()
        kinds = {name: value for name, value in vars(net).items()
                 if name.startswith('EVENT_') and name != 'EVENT_DATA' and isinstance(value, int)}
        for name, kind in sorted(kinds.items()) + [('unknown', 200), ('unknown', 65535)]:
            for data in (b'', b'\x01'):
                expected = kind == net.EVENT_BUSY and data == b'\x01'
                with self.subTest(name, data=data):
                    self.assertEqual(self.relayed(sender, receiver, kind, data), expected)
        refid = 0x0101F7C4
        spell = net.SPELL.pack(refid, 0, 0, net.SOURCE_SPELL, 1) + b'fireball\0'
        for name, kind, data, expected in (
                ('cast', net.EVENT_CAST, spell, True),
                ('spell', net.EVENT_SPELL, spell, False),
                ('shot', net.EVENT_SHOT, net.SHOT.pack(0, 1.0, 0.0, 1) + b'iron arrow\0', True),
                ('death', net.EVENT_DEATH, struct.pack('<I', refid), True),
                ('status', net.EVENT_STATUS, net.STATUS.pack(refid, 10, 20, 30, 40, 50), True),
                ('affect', net.EVENT_AFFECT, struct.pack('<IB', refid, 1) + b'fireball\0', True),
                ('objects', net.EVENT_OBJECTS, bytes([1]) + net.OBJECT.pack(refid + 1, 3, 1, 0),
                 True),
                ('busy', net.EVENT_BUSY, bytes([net.BUSY_SAVING]), True),
                ('bounty', net.EVENT_BOUNTY, struct.pack('<i', 40), True),
                ('equipment', net.EVENT_EQUIPMENT, net.pack_equipment(['iron cuirass'])[0], True),
                ('weather', net.EVENT_WEATHER, bytes([0, 1]) + struct.pack('<HB', 3, 2), True),
                ('remove', net.EVENT_REMOVE, bytes([1]) + struct.pack('<I', 77), False),
                ('want', net.EVENT_WANT, bytes([1]) + struct.pack('<H', 5), False),
                ('hit on the receiver', net.EVENT_HIT, struct.pack('<IIf', refid, 2, 5.0), True),
                ('text', net.EVENT_TEXT, b'hello', False)):
            with self.subTest(name):
                self.assertEqual(self.relayed(sender, receiver, kind, data), expected)

    def test_chat_reaches_every_console_as_a_named_notice(self):
        self.start()
        sender, receiver = self.client(1), self.client(2)
        sender.join()
        receiver.join()
        self.send_events(sender, (tes3x_net.EVENT_TEXT, b'  well   met  '))
        for client in (sender, receiver):
            got = self.events(client, lambda k, d: k == tes3x_net.EVENT_TEXT)
            self.assertEqual(got[-1], (tes3x_net.EVENT_TEXT, b'Player 1: well met'))

    def test_private_messages_reach_one_player_or_the_admin(self):
        from tes3x.net.proto import TEXT_PRIVATE
        self.start()
        sender, receiver, other = self.client(1), self.client(2), self.client(3)
        for client in (sender, receiver, other):
            client.join()

        def private(name, text):
            return (tes3x_net.EVENT_TEXT, bytes([TEXT_PRIVATE]) + name + b'\0' + text)

        def last_text(client):
            return self.events(client, lambda k, d: k == tes3x_net.EVENT_TEXT)[-1][1]

        self.send_events(sender, private(b'player 2', b'psst'))
        self.assertEqual(last_text(receiver), b'Player 1 to you: psst')
        self.assertEqual(last_text(sender), b'You to Player 2: psst')
        self.send_events(sender, private(b'Admin', b'help'))
        self.assertEqual(last_text(sender), b'You to Admin: help')
        self.send_events(sender, private(b'Player', b'who'))
        self.assertEqual(last_text(sender), b"3 players' names start with Player.")
        self.send_events(sender, private(b'Vivec', b'hi'))
        self.assertEqual(last_text(sender), b'No player is called Vivec.')
        with self.assertRaises(AssertionError):  # nothing reached the third player
            self.events(other, lambda k, d: k == tes3x_net.EVENT_TEXT, timeout=0.5)

    def test_admin_messages_say_they_are_from_the_admin(self):
        admin = free_port()
        self.start('--admin-port', str(admin))
        first = self.client(1)
        first.join()
        self.admin(admin, 'say', 'restart', 'soon')
        got = self.events(first, lambda k, d: k == tes3x_net.EVENT_TEXT)
        self.assertEqual(got[-1], (tes3x_net.EVENT_TEXT, b'Admin: restart soon'))
        self.admin(admin, 'tell', '1', 'x' * 90)
        got = self.events(first, lambda k, d: k == tes3x_net.EVENT_TEXT and d == b'x' * 17)
        self.assertEqual(got[-2:], [(tes3x_net.EVENT_TEXT, b'Admin: ' + b'x' * 73),
                                    (tes3x_net.EVENT_TEXT, b'x' * 17)])

    def test_run_puts_back_the_interrupt_handler(self):
        try:
            tes3x_net.crypto()
        except SystemExit:
            self.skipTest('cryptography is not installed')
        def caller(*_):
            pass

        self.addCleanup(signal.signal, signal.SIGINT, signal.signal(signal.SIGINT, caller))
        out = io.TextIOWrapper(io.BytesIO())  # run() reconfigures stdout, so not a StringIO
        with contextlib.redirect_stdout(out):
            result = tes3x_net.main(['serve', '--bind', '127.0.0.1', '--port', str(free_port()),
                                    '--admin-port', str(free_port()), '--duration', '0.3',
                                    '--report', '0'])
        self.assertEqual(result, 0)
        self.assertIs(signal.getsignal(signal.SIGINT), caller)

    def test_consoles_ignore_server_events_another_console_sent(self):
        source = source_text(ROOT / 'hooks' / 'tes3xmulti.c')
        handler = source[source.index('static void event_handle('):]
        guard = handler[:handler.index('net.event_forged')]
        names = set(re.findall(r'\bEVENT_[A-Z_]+', guard)) - {'EVENT_DATA'}
        self.assertEqual({getattr(tes3x_net, name) for name in names}, tes3x_net.SERVER_EVENTS)

    def test_world_is_saved_and_restored(self):
        net = tes3x_net
        world = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, world)
        admin = free_port()
        self.start('--world', str(world), '--adopt', '--admin-port', str(admin),
                   '--stop-wait', '1')
        client = self.client(1)
        client.join()
        refid = 0x0101F7C4
        made = {'cell': 4, 'count': 1, 'removed': False, 'pos': [1.0, 2.0, 3.0],
                'rot': [0.0, 0.0, 0.5], 'id': 'gold_001'}
        status = net.STATUS.pack(refid + 2, 10, 20, 30, 40, 50)
        float_slot = net.LOCAL_FLOAT << 14 | 3
        locals_event = net.pack_locals(refid, {0: 1, float_slot: 0x41480000})[0]
        self.send_events(
            client,
            (net.EVENT_DEATH, struct.pack('<I', refid)),
            (net.EVENT_OBJECTS, bytes([1]) + net.OBJECT.pack(refid + 1, 3, net.OBJECT_DISABLED, 0)),
            (net.EVENT_WEATHER, bytes([0, 1]) + struct.pack('<HB', 3, 2)),
            (net.EVENT_STATUS, status),
            (net.EVENT_LOCALS, locals_event),
            (net.EVENT_SPAWN, net.pack_spawn(1, made)),
            (net.EVENT_SPAWN, net.pack_spawn(2, dict(made, id='atronach_flame', summon=True))))
        self.drain(client)
        self.admin(admin, 'stop')
        self.assertEqual(self.server.wait(10), 0)

        saved = next(world.glob('*.json'))
        state = json.loads(saved.read_text(encoding='utf-8'))
        self.assertEqual(sorted(state), ['actors', 'ai', 'clock', 'contents', 'deaths', 'factions', 'globals', 'journal', 'locals', 'moves', 'next_spawn', 'objects',
                                         'scripts', 'spawns', 'statuses', 'topics', 'weather'])
        self.assertEqual(len(state['clock']), 6)  # hour, day, month, year, days passed, scale
        self.assertEqual(state['deaths'], {str(refid): 1})
        self.assertEqual(state['objects'], {str(refid + 1): [3, net.OBJECT_DISABLED, 0]})
        self.assertEqual(state['weather'], {'3': 2})
        self.assertEqual(state['statuses'], {str(refid + 2): [10, 20, 30, 40, 50]})
        self.assertEqual(state['locals'], {str(refid): {'0': 1, str(float_slot): 0x41480000}})
        spawns = sorted(state['spawns'].values(), key=lambda s: s['id'])
        self.assertEqual([(s['id'], s['summon'], s['removed'], s['origin']) for s in spawns],
                         [('atronach_flame', True, False, 1), ('gold_001', False, False, 1)])
        self.assertEqual(state['next_spawn'], len(spawns) + 1)

        self.start('--world', str(world), '--adopt')
        late = self.client(3)
        late.join()
        spawned = set()
        got = self.events(late, lambda kind, data: kind == net.EVENT_SPAWN and
                          not spawned.add(data) and len(spawned) == 2)
        self.assertIn((net.EVENT_DEATH, struct.pack('<I', refid)), got)
        self.assertIn((net.EVENT_STATUS, status), got)
        self.assertIn((net.EVENT_LOCALS, locals_event), got)
        replayed = {net.unpack_spawn(data)[1]['id']: net.unpack_spawn(data)[1]
                    for kind, data in got if kind == net.EVENT_SPAWN}
        self.assertFalse(replayed['gold_001']['removed'])
        # A saved summon comes back removed: its caster's active effect makes it again.
        self.assertTrue(replayed['atronach_flame']['removed'])


if __name__ == '__main__':
    unittest.main()
