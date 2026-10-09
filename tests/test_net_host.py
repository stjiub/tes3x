"""The network and multiplayer receive paths built for the PC and fed mutated frames."""

import random
import re
import shutil
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

import tes3x.net as tes3x_net
from tes3x.payload import source_text

HOOKS = Path(__file__).resolve().parents[1] / 'hooks'

HERE = Path(__file__).resolve().parent / 'nethost'
MAC = bytes([2, 0, 0, 0, 0x24, 0x99])
SERVER_MAC = bytes.fromhex('020000000001')
CONSOLE, SERVER = '10.0.2.15', '10.0.2.2'
SESSION, XID, DNS_ID = 0x11223344, 0x01020304, 0x4242
RECEIVE_KEY = bytes([0x22]) * 32
MODE_JOINED, MODE_HANDSHAKE, MODE_RESOLVE, MODE_BOUND = range(4)
MTU = 1518


def build(folder, clang):
    """nethost.exe from copies of the split network sources without their inline assembly."""
    folder = Path(folder)
    sources = {}
    for filename in ('tes3xnet.c', 'tes3xmulti.c'):
        source = source_text(HOOKS / filename)
        source = re.sub(r'^(\s*)__asm__ volatile\(.*\);[ \t]*$', r'\1;', source, flags=re.M)
        source = re.sub(
            r'__attribute__\(\(naked\)\) void tes3x_net_death_gate\(void\)\s*\{.*?^\}',
            'void tes3x_net_death_gate(void) {}', source, flags=re.M | re.S)
        sources[filename] = source.replace('0xFEF00000u', '(uintptr_t)host_nic')
        (folder / filename).write_text(sources[filename], encoding='utf-8')
    source = '\n'.join(sources.values())
    thunks = sorted(set(re.findall(r'THUNK_\w+', source + (HOOKS / 'tes3xnt.h').read_text())))
    (folder / 'tes3x_thunks.h').write_text(
        '#include <stdint.h>\nextern void *host_thunks[64];\n'
        + ''.join(f'#define {name} ((uintptr_t)&host_thunks[{i}])\n'
                  for i, name in enumerate(thunks)), encoding='utf-8')
    for name in ('tes3xnt.h', 'tes3xlog.h', 'tes3xini.h', 'tes3xlaunch.h', 'tes3xnet.h',
                 'monocypher.h', 'monocypher.c', 'tes3xnoise.h', 'tes3xnoise.c'):
        shutil.copy(HOOKS / name, folder / name)
    for name in ('host.c', 'guard.c'):
        shutil.copy(HERE / name, folder / name)
    defines = ['-DTES3X_BUILD_ID=1', '-DTES3X_INI_GET_STRING=0x1000', '-DTES3X_INI_PATH=0x1000']
    for name in sorted(set(re.findall(r'TES3X_NET_[A-Z0-9_]+', source))):
        lists = name.endswith(('_SITES', '_SLOTS'))  # call sites and vtable slots: brace lists
        defines.append(f'-D{name}={{0x1000}}' if lists else f'-D{name}=0x1000')
    exe = folder / 'nethost.exe'
    subprocess.run([clang, '-O1', '-g', '-w', *defines, f'-I{folder}', str(folder / 'host.c'),
                    str(folder / 'guard.c'), str(folder / 'monocypher.c'),
                    str(folder / 'tes3xnoise.c'), '-o', str(exe)], check=True,
                   capture_output=True, text=True)
    return exe


def ip_frame(rng, src, sport, dport, payload, dst=CONSOLE):
    frame = bytearray(tes3x_net.udp_frame(MAC, dst, payload, sport=sport, dport=dport))
    frame[26:30] = bytes(int(p) for p in src.split('.'))
    if rng.random() < 0.15:  # break the IP header: length, header length or protocol
        field = rng.choice((14, 16, 17, 23))
        frame[field] = rng.getrandbits(8)
    return bytes(frame[:MTU])


def sealed(rng, seq, kind, body):
    outer = tes3x_net.OUTER.pack(b'T3MP', tes3x_net.T3MP_VERSION, tes3x_net.SEALED, 0, SESSION,
                                 seq)
    inner = tes3x_net.INNER.pack(kind, 0, 0, 0) + body
    return outer + tes3x_net.seal(RECEIVE_KEY, seq, outer, inner)


def session_frame(rng, seq):
    roll = rng.random()
    if roll < 0.6:
        kind, body = tes3x_net.fuzz_body(rng, 1)
        if rng.random() < 0.2:
            kind = rng.choice((tes3x_net.WELCOME, tes3x_net.REFUSE, tes3x_net.BYE,
                               tes3x_net.GONE, tes3x_net.CHUNK))
        if kind == tes3x_net.CHUNK and rng.random() < 0.7:
            index = rng.randrange(0, 20)
            body = struct.pack('<II', 7, index) + rng.randbytes(
                rng.choice((1024, rng.randrange(0, 1200))))
        return MODE_JOINED, sealed(rng, seq, kind, body[:1400])
    if roll < 0.75:
        message = bytearray(rng.randbytes(rng.choice((96, 112, 112, rng.randrange(0, 200)))))
        return MODE_HANDSHAKE, tes3x_net.OUTER.pack(b'T3MP', tes3x_net.T3MP_VERSION,
                                                    tes3x_net.HANDSHAKE2, 0, SESSION, 0) + message
    if roll < 0.85:  # sealed, then damaged
        packet = bytearray(sealed(rng, seq, tes3x_net.HEARTBEAT, rng.randbytes(8)))
        packet[rng.randrange(len(packet))] ^= 1 << rng.randrange(8)
        return MODE_JOINED, bytes(packet)
    return rng.choice((MODE_JOINED, MODE_HANDSHAKE)), rng.randbytes(rng.randrange(0, 64))


def dhcp_frame(rng):
    options = b''
    for _ in range(rng.randrange(0, 12)):
        code = rng.choice((1, 3, 6, 51, 53, 54, 0, 255, rng.getrandbits(8)))
        value = rng.randbytes(rng.choice((0, 1, 4, 4, rng.randrange(0, 40))))
        if code == 53 and value:
            value = bytes([rng.choice((2, 5, 6))]) + value[1:]
        options += bytes([code, min(len(value) + rng.choice((0, 0, 0, 3)), 255)]) + value
    body = bytearray(240)
    body[0] = 2
    body[4:8] = struct.pack('>I', XID)
    body[16:20] = bytes([10, 0, 2, 15])
    body[28:34] = MAC
    body[236:240] = tes3x_net.DHCP_MAGIC
    body = bytes(body) + options
    if rng.random() < 0.2:
        body = body[:rng.randrange(0, len(body) + 1)]
    return rng.choice((MODE_JOINED, MODE_BOUND)), body


def dns_frame(rng):
    question = b''.join(bytes([len(p)]) + p for p in (b'mw', b'test')) + b'\0' + b'\0\1\0\1'
    answers = b''
    for _ in range(rng.randrange(0, 4)):
        name = rng.choice((b'\xc0\x0c', b'\xc0' + bytes([rng.getrandbits(8)]), b'\x02mw\x00',
                           rng.randbytes(rng.randrange(0, 6))))
        rdata = rng.randbytes(rng.choice((4, 4, 0, 16, rng.randrange(0, 30))))
        length = len(rdata) if rng.random() < 0.8 else rng.getrandbits(16)
        answers += name + struct.pack('>HHIH', rng.choice((1, 5, 28)), 1, 60, length) + rdata
    header = struct.pack('>HHHHHH', DNS_ID, 0x8180, rng.choice((1, 1, 0, 255)),
                         rng.choice((1, 2, 0, 200)), 0, 0)
    body = header + question + answers
    if rng.random() < 0.2:
        body = body[:rng.randrange(0, len(body) + 1)]
    return MODE_RESOLVE, body


def arp_frame(rng):
    frame = bytearray(tes3x_net.arp_frame(rng.choice((1, 2)), MAC, MAC, CONSOLE))
    frame[6:12] = SERVER_MAC
    for _ in range(rng.randrange(0, 3)):
        frame[rng.randrange(14, len(frame))] = rng.getrandbits(8)
    return bytes(frame[:rng.choice((len(frame), rng.randrange(14, len(frame) + 1)))])


def frames(seed, count):
    rng = random.Random(seed)
    seq = 0
    for _ in range(count):
        roll = rng.random()
        if roll < 0.55:
            seq += 1
            mode, payload = session_frame(rng, seq)
            yield mode, ip_frame(rng, SERVER, tes3x_net.PORT, tes3x_net.PORT, payload)
        elif roll < 0.7:
            mode, payload = dhcp_frame(rng)
            yield mode, ip_frame(rng, SERVER, 67, 68, payload, dst='255.255.255.255')
        elif roll < 0.85:
            mode, payload = dns_frame(rng)
            yield mode, ip_frame(rng, SERVER, 53, tes3x_net.PORT, payload)
        elif roll < 0.92:
            yield MODE_JOINED, arp_frame(rng)
        else:
            payload = b'TES3XPNG' + rng.randbytes(rng.randrange(0, 1480))
            yield MODE_JOINED, ip_frame(rng, SERVER, 40000, tes3x_net.PORT, payload)


class HostReceiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            tes3x_net.crypto()
            from tes3x.payload import find_tool  # where the payload build finds clang
            clang = find_tool('clang')
        except (SystemExit, Exception) as error:
            raise unittest.SkipTest(f'needs clang and the cryptography package: {error!r}')
        cls.folder = tempfile.TemporaryDirectory()
        try:
            cls.exe = build(cls.folder.name, clang)
        except subprocess.CalledProcessError as error:
            raise unittest.SkipTest('the host build failed: ' + error.stderr[-400:])

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def feed(self, seed, count):
        data = b''.join(bytes([mode]) + struct.pack('<H', len(f)) + f
                        for mode, f in frames(seed, count))
        run = subprocess.run([str(self.exe)], input=data, capture_output=True, timeout=120)
        self.assertEqual(run.returncode, 0, f'seed {seed}: exit {run.returncode:#x} '
                         + run.stderr.decode(errors='replace'))
        return run.stdout.decode()

    def test_receive_path_survives_mutated_frames(self):
        for seed in range(1, 6):
            result = self.feed(seed, 4000)
            numbers = dict(re.findall(r'([a-z0-9]+) (\d+)', result))
            self.assertGreater(int(numbers['opened']), 1000, result)
            self.assertGreater(int(numbers['events']), 100, result)
            self.assertGreater(int(numbers['chunks']), 10, result)
            self.assertGreater(int(numbers['answers']), 0, result)
            self.assertGreater(int(numbers['offers']) + int(numbers['acks']), 0, result)

    def test_effect_snapshot_parts_are_atomic_and_accept_empty_replacement(self):
        run = subprocess.run([str(self.exe), '--effect-parts'], capture_output=True, text=True,
                             timeout=10)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('ok effect parts', run.stdout)

    def test_snapshot_waits_for_all_effect_parts_under_queue_pressure(self):
        run = subprocess.run([str(self.exe), '--snapshot-parts'], capture_output=True, text=True,
                             timeout=10)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('ok snapshot parts', run.stdout)

    def test_snapshot_ack_requires_the_matching_token(self):
        run = subprocess.run([str(self.exe), '--snapshot-ack'], capture_output=True, text=True,
                             timeout=10)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('ok snapshot ack', run.stdout)

    def test_stat_replay_waits_for_abilities_and_keeps_fractional_values(self):
        run = subprocess.run([str(self.exe), '--stats-replay'], capture_output=True, text=True,
                             timeout=10)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('ok stat replay', run.stdout)


if __name__ == '__main__':
    unittest.main()
