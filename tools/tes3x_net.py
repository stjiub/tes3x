#!/usr/bin/env python3
"""Talk to the payload's network driver (the `multiplayer` patch).

    python tools/tes3x_net.py listen                     # console broadcasts on UDP 26500
    python tools/tes3x_net.py ping 192.0.2.50             # echo round trips to `tes3xnet up`
    python tools/tes3x_net.py listen --tunnel 9369       # the same through xemu's udp backend
    python tools/tes3x_net.py ping 10.0.2.15 --tunnel 9369
    python tools/tes3x_net.py serve --tunnel 9369 --bot   # plus a player circling the first client
    python tools/tes3x_net.py serve --tunnel 9369 --bot --bot-say 2 --drop 0.2   # events under loss
    python tools/tes3x_net.py plugin OUT.esp --master Morrowind.esm   # the ghost plugin

With --tunnel PORT this tool is the guest's only peer: xemu sends each guest Ethernet frame to
PORT as one datagram and accepts frames on PORT+1 (`tes3x_xemu.py --net-tunnel PORT`), so ping
answers ARP itself and resolves the console's MAC before it pings.
"""

import argparse
import math
import os
import random
import select
import socket
import struct
import sys
import time

PORT = 26500
DNS_PORT = 53
MAGIC = b"TES3XNET"
PING, PONG = b"TES3XPNG", b"TES3XPON"
PEER_MAC = bytes.fromhex("020000000001")
PEER_IP = "10.0.2.2"
BROADCAST = b"\xff" * 6


def decode(payload):
    """(seq, count, mac) of a broadcast test datagram, or None."""
    if len(payload) < 22 or payload[:8] != MAGIC:
        return None
    seq, count = struct.unpack_from("<II", payload, 8)
    return seq, count, payload[16:22].hex(":")


def udp_from_frame(frame, port=PORT):
    """UDP payload of an IPv4 frame to port, or None."""
    if len(frame) < 42 or frame[12:14] != b"\x08\x00" or frame[23] != 17:
        return None
    udp = 14 + (frame[14] & 0x0F) * 4
    if struct.unpack_from(">H", frame, udp + 2)[0] != port:
        return None
    length = struct.unpack_from(">H", frame, udp + 4)[0]
    return frame[udp + 8:udp + length]


def checksum(data):
    total = sum(struct.unpack(">%dH" % (len(data) // 2), data))
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return ~total & 0xFFFF


def udp_frame(dst_mac, dst_ip, payload, ident=0, sport=PORT):
    """An Ethernet frame carrying payload from PEER_IP:sport to dst_ip:PORT."""
    udp = struct.pack(">HHHH", sport, PORT, 8 + len(payload), 0) + payload
    ip = struct.pack(">BBHHHBBH4s4s", 0x45, 0, 20 + len(udp), ident & 0xFFFF, 0, 64, 17, 0,
                     socket.inet_aton(PEER_IP), socket.inet_aton(dst_ip))
    ip = ip[:10] + struct.pack(">H", checksum(ip)) + ip[12:]
    return dst_mac + PEER_MAC + b"\x08\x00" + ip + udp


def dns_reply(query, hosts):
    """The answer to an A query from hosts (name -> address), NXDOMAIN otherwise; None if malformed."""
    if len(query) < 12:
        return None
    labels, off = [], 12
    while off < len(query) and query[off]:
        labels.append(query[off + 1:off + 1 + query[off]].decode("ascii", "replace"))
        off += 1 + query[off]
    off += 1
    if off + 4 > len(query):
        return None
    qtype, qclass = struct.unpack_from(">HH", query, off)
    question = query[12:off + 4]
    address = hosts.get(".".join(labels).lower()) if (qtype, qclass) == (1, 1) else None
    flags = 0x8180 if address else 0x8183
    head = query[:2] + struct.pack(">HHHHH", flags, 1, 1 if address else 0, 0, 0)
    if not address:
        return head + question
    return head + question + struct.pack(">HHHIH4s", 0xC00C, 1, 1, 60, 4,
                                         socket.inet_aton(address))


def arp_frame(op, dst_mac, target_mac, target_ip):
    body = struct.pack(">HHBBH6s4s6s4s", 1, 0x0800, 6, 4, op, PEER_MAC,
                       socket.inet_aton(PEER_IP), target_mac, socket.inet_aton(target_ip))
    return dst_mac + PEER_MAC + b"\x08\x06" + body


def udp_socket():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    # Windows reports an ICMP port-unreachable as a reset on the next recv; a peer that is not
    # up yet (xemu still booting) is normal here.
    if hasattr(socket, "SIO_UDP_CONNRESET"):
        sock.ioctl(socket.SIO_UDP_CONNRESET, False)
    return sock


class Tunnel:
    """Raw frames to and from xemu's udp backend."""

    def __init__(self, port):
        self.sock = udp_socket()
        self.sock.bind(("127.0.0.1", port))
        self.guest = ("127.0.0.1", port + 1)

    def send(self, frame):
        self.sock.sendto(frame.ljust(60, b"\0"), self.guest)

    def recv(self, timeout):
        self.sock.settimeout(timeout)
        try:
            return self.sock.recvfrom(2048)[0]
        except (socket.timeout, ConnectionResetError):
            return None


def listen(args):
    port = args.tunnel or args.port
    if args.tunnel:
        link = Tunnel(args.tunnel)
    else:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((args.bind, port))
    mode = "xemu tunnel" if args.tunnel else "udp"
    print(f"listening on {args.bind}:{port} ({mode})", flush=True)
    seen, frames, expected = set(), 0, None
    deadline = time.time() + args.timeout if args.timeout else None
    while deadline is None or time.time() < deadline:
        if args.tunnel:
            data = link.recv(0.5)
            if data is None:
                continue
            frames += 1
            data = udp_from_frame(data)
            if data is None:
                continue
            peer = "tunnel"
        else:
            sock.settimeout(0.5)
            try:
                data, (peer, _) = sock.recvfrom(2048)
            except socket.timeout:
                continue
        hit = decode(data)
        if not hit:
            continue
        seq, count, mac = hit
        seen.add(seq)
        expected = count
        print(f"seq {seq}/{count} from {mac} via {peer}", flush=True)
        if len(seen) >= count:
            break
    if args.tunnel:
        print(f"frames: {frames}")
    missing = sorted(set(range(expected)) - seen) if expected else []
    print(f"received {len(seen)}" + (f" of {expected}" if expected else "")
          + (f"; missing {missing}" if missing else ""))
    return 0 if expected and not missing else 1


def resolve(link, host, timeout):
    """The guest's MAC, by ARP through the tunnel, once the guest has announced itself.

    xemu's NIC never restarts a udp backend that it refused a frame from, so nothing is sent
    until the guest's gratuitous ARP shows it can receive.
    """
    target = socket.inet_aton(host)
    deadline = time.time() + timeout
    while time.time() < deadline:
        frame = link.recv(0.5)
        if frame and frame[12:14] == b"\x08\x06" and frame[28:32] == target:
            break
    while time.time() < deadline:
        link.send(arp_frame(1, BROADCAST, b"\0" * 6, host))
        end = time.time() + 1.0
        while time.time() < end:
            frame = link.recv(0.2)
            if frame is None or frame[12:14] != b"\x08\x06" or len(frame) < 42:
                continue
            op, sha, spa = struct.unpack_from(">H6s4s", frame, 20)
            if op == 2 and spa == target:
                return sha
    return None


def ping(args):
    link = Tunnel(args.tunnel) if args.tunnel else None
    if link:
        print(f"arp {args.host} through the tunnel (up to {args.wait:.0f} s)", flush=True)
        t0 = time.time()
        mac = resolve(link, args.host, args.wait)
        if mac is None:
            print("no ARP reply")
            return 1
        print(f"arp reply from {mac.hex(':')} after {time.time() - t0:.1f} s", flush=True)
    else:
        sock = udp_socket()
        sock.bind(("0.0.0.0", 0))
        print(f"probing {args.host} until it answers (up to {args.wait:.0f} s)", flush=True)
        t0 = time.time()
        while True:
            if time.time() - t0 > args.wait:
                print("no echo reply")
                return 1
            sock.sendto(PING + struct.pack("<I", 0xFFFFFFFF), (args.host, PORT))
            sock.settimeout(1.0)
            try:
                if sock.recvfrom(2048)[0][:8] == PONG:
                    break
            except (socket.timeout, ConnectionResetError):
                pass
        print(f"first reply after {time.time() - t0:.1f} s", flush=True)
    rtts, lost, runs, run = [], 0, [], 0
    for seq in range(args.count):
        payload = PING + struct.pack("<I", seq) + b"\0" * args.pad
        start = time.perf_counter()
        if link:
            link.send(udp_frame(mac, args.host, payload, seq))
        else:
            sock.sendto(payload, (args.host, PORT))
        got = None
        end = start + args.reply_timeout
        while got is None and time.perf_counter() < end:
            left = max(0.001, end - time.perf_counter())
            if link:
                frame = link.recv(left)
                data = frame and udp_from_frame(frame)
            else:
                sock.settimeout(left)
                try:
                    data = sock.recvfrom(2048)[0]
                except (socket.timeout, ConnectionResetError):
                    data = None
            if data and data[:8] == PONG and struct.unpack_from("<I", data, 8)[0] == seq:
                got = time.perf_counter()
        if got is None:
            lost += 1
            run += 1
            if args.verbose:
                print(f"seq {seq}: lost")
        else:
            if run:
                runs.append((seq - run, run))
                run = 0
            rtts.append((got - start) * 1000)
            if args.verbose:
                print(f"seq {seq}: {rtts[-1]:.2f} ms")
        time.sleep(args.interval)
    if run:
        runs.append((args.count - run, run))
    print(f"sent {args.count}, received {len(rtts)}, lost {lost}")
    if runs:
        print("loss runs (first seq x count): " + ", ".join(f"{a}x{n}" for a, n in runs[:20]))
    if rtts:
        rtts.sort()
        print("rtt ms: min %.2f  median %.2f  avg %.2f  p95 %.2f  max %.2f" % (
            rtts[0], rtts[len(rtts) // 2], sum(rtts) / len(rtts),
            rtts[min(len(rtts) - 1, int(len(rtts) * 0.95))], rtts[-1]))
    return 0 if not lost else 1


T3MP = struct.Struct("<4sBBHIIIII")  # magic, version, type, 0, session, seq, ack, time, echo
T3MP_VERSION = 2
HELLO, WELCOME, HEARTBEAT, BYE, STATE, PEER, GONE, EVENTS, REFUSE = range(1, 10)
HELLO_BODY = struct.Struct("<6sIII")  # MAC, build id, load order hash, plugin count
TIMEOUT = 5.0
EVENTS_HEAD = struct.Struct("<IB3x")  # the sender's last delivered event, event count
EVENT = struct.Struct("<IHHI")  # seq, kind, length, origin client; the data follows
EVENTS_BYTES = 512  # the client's largest EVENTS body
EVENT_DATA = 64
EVENT_TEXT = 1
RESEND = 0.25


def load_order_hash(names):
    """FNV-1a over plugin names in load order, lowercased and zero-terminated, as tes3xnet.c."""
    h = 2166136261
    for name in names:
        for byte in name.lower().encode("latin-1") + b"\0":
            h = ((h ^ byte) * 16777619) & 0xFFFFFFFF
    return h


def pack_events(ack, events, limit=EVENTS_BYTES):
    """An EVENTS body: ack, then as many (seq, kind, origin, data) as fit in limit, in order."""
    out = b""
    count = 0
    for seq, kind, origin, data in events:
        item = EVENT.pack(seq, kind, len(data), origin) + data
        if EVENTS_HEAD.size + len(out) + len(item) > limit or count == 255:
            break
        out += item
        count += 1
    return EVENTS_HEAD.pack(ack, count) + out


def unpack_events(body):
    """(ack, [(seq, kind, origin, data)]) of an EVENTS body; a truncated event ends the list."""
    ack, count = EVENTS_HEAD.unpack_from(body)
    off, events = EVENTS_HEAD.size, []
    for _ in range(count):
        if off + EVENT.size > len(body):
            break
        seq, kind, length, origin = EVENT.unpack_from(body, off)
        data = body[off + EVENT.size:off + EVENT.size + length]
        if len(data) < length:
            break
        events.append((seq, kind, origin, data))
        off += EVENT.size + length
    return ack, events


class Reliable:
    """One direction pair of a session's event channel: numbered from 1, resent until acked,
    delivered only in order."""

    def __init__(self):
        self.out = []  # unacked (seq, kind, origin, data), oldest first
        self.out_next = 1
        self.in_next = 1
        self.last_send = 0.0
        self.sent = self.resent = self.delivered = self.stale = 0
        self.sent_upto = 1

    def queue(self, kind, origin, data):
        self.out.append((self.out_next, kind, origin, bytes(data)))
        self.out_next += 1

    def packet(self, now, resend=True):
        """The EVENTS body to send: the ack, then the unacked events that fit."""
        self.last_send = now
        body = pack_events(self.in_next - 1, self.out if resend else [])
        for seq, *_ in unpack_events(body)[1]:
            self.sent += 1
            if seq < self.sent_upto:
                self.resent += 1
            else:
                self.sent_upto = seq + 1
        return body

    def receive(self, body):
        """The events now deliverable, in order; the caller then sends an ack."""
        ack, events = unpack_events(body)
        self.out = [e for e in self.out if e[0] > ack]
        ready = []
        for event in events:
            if event[0] == self.in_next:
                ready.append(event)
                self.in_next += 1
                self.delivered += 1
            elif event[0] < self.in_next:
                self.stale += 1
        return ready, bool(events)
STATE_BODY = struct.Struct("<I4f32s")  # flags, x, y, z, heading, interior cell name
IN_WORLD, INTERIOR = 1, 2
CELL_UNITS = 8192


def describe_state(state):
    flags, x, y, z, heading, cell = STATE_BODY.unpack_from(state)
    if not flags & IN_WORLD:
        return "not in the world"
    where = (cell.split(b"\0", 1)[0].decode("latin-1") if flags & INTERIOR
             else f"exterior {int(x // CELL_UNITS)},{int(y // CELL_UNITS)}")
    return f"{where} at {x:.0f},{y:.0f},{z:.0f} heading {math.degrees(heading) % 360:.0f}"


def now_us():
    return int(time.perf_counter() * 1e6) & 0xFFFFFFFF


GHOST_PLUGIN = "TES3X Multiplayer.esp"
GHOST_CELL = "TES3X Ghosts"
GHOSTS = 8  # one per peer slot in tes3xnet.c
BOT_ID = 99


def record(tag, subs, flags=0):
    body = b"".join(name + struct.pack("<I", len(value)) + value for name, value in subs)
    return tag + struct.pack("<III", len(body), 0, flags) + body


def zstr(text):
    return text.encode("latin-1") + b"\0"


def ghost_plugin(master_size, master="Morrowind.esm"):
    """The plugin tes3xnet.c moves: one persistent NPC per peer slot, parked in a cell of its own.
    They have no AI packages and zero fight, flee, alarm and hello, so they stand where put."""
    hedr = (struct.pack("<fI", 1.3, 0) + b"TES3X".ljust(32, b"\0")
            + b"Other players, placed by the multiplayer patch.".ljust(256, b"\0")
            + struct.pack("<I", GHOSTS + 1))
    out = [record(b"TES3", [(b"HEDR", hedr), (b"MAST", zstr(master)),
                            (b"DATA", struct.pack("<Q", master_size))])]
    items = ("common_shirt_01", "common_pants_01", "common_shoes_01")
    for i in range(1, GHOSTS + 1):
        subs = [(b"NAME", zstr(f"tes3x_ghost{i}")), (b"FNAM", zstr(f"Player {i}")),
                (b"RNAM", zstr("Dark Elf")), (b"CNAM", zstr("Commoner")), (b"ANAM", b"\0"),
                (b"BNAM", zstr("b_n_dark elf_m_head_01")),
                (b"KNAM", zstr("b_n_dark elf_m_hair_01")),
                (b"NPDT", struct.pack("<hBBB3xI", 1, 50, 0, 0, 0)),
                (b"FLAG", struct.pack("<I", 0x1A))]  # essential, autocalc
        subs += [(b"NPCO", struct.pack("<i32s", 1, item.encode())) for item in items]
        subs.append((b"AIDT", bytes(12)))
        out.append(record(b"NPC_", subs, flags=0x400))  # references persist
    cell = [(b"NAME", zstr(GHOST_CELL)), (b"DATA", struct.pack("<Iii", 1, 0, 0)),
            (b"WHGT", struct.pack("<f", 0)), (b"AMBI", struct.pack("<3If", 0x404040, 0, 0, 0))]
    for i in range(1, GHOSTS + 1):
        cell += [(b"FRMR", struct.pack("<I", i)), (b"NAME", zstr(f"tes3x_ghost{i}")),
                 (b"DATA", struct.pack("<6f", 128.0 * i, 0, 0, 0, 0, 0))]
    out.append(record(b"CELL", cell))
    return b"".join(out)


def write_ghost_plugin(data_files, master):
    """Write the ghost plugin into a staged Data Files; the engine loads every plugin there."""
    target = os.path.join(data_files, GHOST_PLUGIN)
    with open(target, "wb") as f:
        f.write(ghost_plugin(os.path.getsize(master)))
    return target


class Client:
    def __init__(self, ident, mac):
        self.id, self.mac = ident, mac
        self.session = self.seq = self.peer_seq = self.peer_time = 0
        self.addr = None
        self.joins = self.beats = self.gaps = self.states = 0
        self.state = None
        self.last = time.time()
        self.alive = False
        self.rel = Reliable()
        self.events = 0


def serve(args):
    """A session server: welcomes consoles by MAC, answers each heartbeat at once and relays each
    client's state to the others. With --tunnel it also serves an xemu guest."""
    link = Tunnel(args.tunnel) if args.tunnel else None
    sock = udp_socket()
    sock.bind((args.bind, args.port))
    print(f"serving on {args.bind}:{args.port}" + (f" and tunnel {args.tunnel}" if link else ""),
          flush=True)
    clients, by_session = {}, {}
    hosts = {}
    for entry in args.host:
        name, _, address = entry.partition("=")
        socket.inet_aton(address)
        hosts[name.lower().rstrip(".")] = address
    dns = None
    if hosts:
        dns = udp_socket()
        dns.bind((args.bind, DNS_PORT))
        print(f"answering DNS on {args.bind}:{DNS_PORT} for {', '.join(sorted(hosts))}",
              flush=True)
    deadline = time.time() + args.duration if args.duration else None
    report = time.time() + args.report
    loss = random.Random(args.seed)
    pinned = None
    if args.load_order:
        pinned = (int(args.load_order, 16), None)
    lost = {"in": 0, "out": 0}

    def dropped(direction):
        if args.drop and loss.random() < args.drop:
            lost[direction] += 1
            return True
        return False

    def send(client, kind, body=b""):
        client.seq += 1
        packet = T3MP.pack(b"T3MP", T3MP_VERSION, kind, 0, client.session, client.seq,
                           client.peer_seq, now_us(), client.peer_time) + body
        if dropped("out"):
            return
        if len(client.addr) == 3:  # a tunnel guest, by its MAC
            ip, _port, mac = client.addr
            link.send(udp_frame(mac, ip, packet, client.seq))
        else:
            sock.sendto(packet, client.addr)

    bot = {"anchor": None, "next": 0.0, "start": time.time(), "said": 0.0, "line": 0}

    def bot_anchor(state):
        """The bot circles where the first client entered the world, and follows it to a new
        cell or across a long jump."""
        flags, x, y, z, _, cell = STATE_BODY.unpack_from(state)
        anchor = bot["anchor"]
        if flags & IN_WORLD and (anchor is None or anchor[0] != (flags, cell)
                                 or math.hypot(x - anchor[1], y - anchor[2]) > 2048):
            bot["anchor"] = ((flags, cell), x, y, z)
            print(f"{time.strftime('%H:%M:%S')} bot circles {describe_state(state)}",
                  flush=True)

    def bot_step(now):
        (flags, cell), cx, cy, cz = bot["anchor"]
        t = (now - bot["start"]) * 2 * math.pi / args.bot_period
        state = STATE_BODY.pack(flags, cx + args.bot_radius * math.cos(t),
                                cy + args.bot_radius * math.sin(t), cz, -t % (2 * math.pi), cell)
        for other in clients.values():
            if other.alive:
                send(other, PEER, struct.pack("<I", BOT_ID) + state)

    def leave(client):
        client.alive = False
        for other in clients.values():
            if other.alive:
                send(other, GONE, struct.pack("<I", client.id))

    def flush(client, now, resend=True):
        send(client, EVENTS, client.rel.packet(now, resend))

    def broadcast_event(origin, kind, data, now):
        for other in clients.values():
            if other.alive and other.id != origin:
                other.rel.queue(kind, origin, data)
                flush(other, now)

    def on_event(client, kind, data, stamp, now):
        client.events += 1
        if kind == EVENT_TEXT:
            print(f"{stamp} client {client.id} says: {data.decode('latin-1')}", flush=True)
        broadcast_event(client.id, kind, data, now)

    def handle(packet, addr):
        nonlocal pinned
        if len(packet) < T3MP.size or dropped("in"):
            return
        magic, version, kind, _, session, seq, _, sent, _ = T3MP.unpack_from(packet)
        if magic != b"T3MP" or version != T3MP_VERSION:
            return
        stamp = time.strftime("%H:%M:%S")
        now = time.time()
        if kind == HELLO and len(packet) >= T3MP.size + HELLO_BODY.size:
            mac, build, order, plugins = HELLO_BODY.unpack_from(packet, T3MP.size)
            mac = mac.hex(":")
            if pinned is None:
                pinned = (order, plugins)
                print(f"{stamp} load order {order:#010x} ({plugins} plugins) set by {mac}",
                      flush=True)
            if order != pinned[0]:
                print(f"{stamp} refused {mac}: load order {order:#010x} ({plugins} plugins), "
                      f"session has {pinned[0]:#010x}", flush=True)
                stranger = Client(0, mac)
                stranger.addr = addr
                send(stranger, REFUSE, struct.pack("<II", pinned[0], pinned[1] or 0))
                return
            client = clients.get(mac)
            if client is None:
                client = clients[mac] = Client(len(clients) + 1, mac)
            by_session.pop(client.session, None)
            client.session = int.from_bytes(os.urandom(4), "little") or 1
            by_session[client.session] = client
            client.addr, client.peer_seq, client.peer_time = addr, seq, sent
            client.joins += 1
            client.alive, client.last = True, now
            if client.rel.out:
                print(f"{stamp} client {client.id}: {len(client.rel.out)} unacked events "
                      f"dropped by the rejoin", flush=True)
            client.rel = Reliable()
            verb = "joined" if client.joins == 1 else "rejoined"
            print(f"{stamp} client {client.id} {verb}: {mac} at {addr[0]}:{addr[1]}, "
                  f"build {build:#010x}", flush=True)
            send(client, WELCOME, struct.pack("<I", client.id))
            return
        client = by_session.get(session)
        if client is None:
            return
        if seq > client.peer_seq + 1:
            client.gaps += seq - client.peer_seq - 1
        client.peer_seq = max(client.peer_seq, seq)
        client.peer_time, client.addr, client.last = sent, addr, time.time()
        if not client.alive:
            print(f"{stamp} client {client.id} back", flush=True)
            client.alive = True
        if kind == HEARTBEAT:
            client.beats += 1
            send(client, HEARTBEAT)
        elif kind == STATE and len(packet) >= T3MP.size + STATE_BODY.size:
            client.state = packet[T3MP.size:T3MP.size + STATE_BODY.size]
            client.states += 1
            if args.bot:
                bot_anchor(client.state)
            for other in clients.values():
                if other is not client and other.alive:
                    send(other, PEER, struct.pack("<I", client.id) + client.state)
        elif kind == EVENTS and len(packet) >= T3MP.size + EVENTS_HEAD.size:
            ready, carried = client.rel.receive(packet[T3MP.size:])
            if carried:
                flush(client, now, resend=False)
            for _, event_kind, _, data in ready:
                on_event(client, event_kind, data, stamp, now)
        elif kind == BYE:
            print(f"{stamp} client {client.id} left", flush=True)
            leave(client)
            by_session.pop(session, None)

    while deadline is None or time.time() < deadline:
        waiting = [sock] + ([link.sock] if link else []) + ([dns] if dns else [])
        for ready in select.select(waiting, [], [], 0.25)[0]:
            if ready is dns:
                try:
                    query, addr = dns.recvfrom(2048)
                except ConnectionResetError:
                    continue
                reply = dns_reply(query, hosts)
                if reply:
                    print(f"{time.strftime('%H:%M:%S')} dns query from {addr[0]}: "
                          f"{'answered' if reply[7] else 'unknown name'}", flush=True)
                    dns.sendto(reply, addr)
                continue
            if ready is sock:
                try:
                    data, addr = sock.recvfrom(2048)
                except ConnectionResetError:
                    continue
                handle(data, addr)
                continue
            frame = link.recv(0)
            if frame and frame[12:14] == b"\x08\x06" and len(frame) >= 42:
                op, sha, spa, _, tpa = struct.unpack_from(">H6s4s6s4s", frame, 20)
                if op == 1 and tpa == socket.inet_aton(PEER_IP):
                    link.send(arp_frame(2, sha, sha, socket.inet_ntoa(spa)))
            elif frame:
                src = socket.inet_ntoa(frame[26:30])
                query = udp_from_frame(frame, DNS_PORT) if hosts else None
                reply = dns_reply(query, hosts) if query else None
                if reply:
                    print(f"{time.strftime('%H:%M:%S')} dns query from {src}: "
                          f"{'answered' if reply[7] else 'unknown name'}", flush=True)
                    link.send(udp_frame(frame[6:12], src, reply, sport=DNS_PORT))
                    continue
                data = udp_from_frame(frame)
                if data:
                    handle(data, (src, PORT, frame[6:12]))
        now = time.time()
        for client in clients.values():
            if client.alive and now - client.last > TIMEOUT:
                print(f"{time.strftime('%H:%M:%S')} client {client.id} timed out", flush=True)
                leave(client)
        if args.bot and bot["anchor"] and now >= bot["next"]:
            bot["next"] = now + 1 / args.bot_rate
            bot_step(now)
        if args.bot_say and bot["anchor"] and now >= bot["said"] + args.bot_say:
            bot["said"] = now
            bot["line"] += 1
            broadcast_event(BOT_ID, EVENT_TEXT, b"bot %d" % bot["line"], now)
        for client in clients.values():
            if client.alive and client.rel.out and now - client.rel.last_send >= RESEND:
                flush(client, now)
        if args.report and now >= report:
            report = now + args.report
            for client in clients.values():
                print(f"  client {client.id}: {'up' if client.alive else 'down'}, "
                      + summary(client), flush=True)
    for client in clients.values():
        print(f"client {client.id} {client.mac}: " + summary(client, "last "))
    if args.drop:
        print(f"dropped {lost['in']} packets in, {lost['out']} out")
    return 0 if clients else 1


def summary(client, prefix=""):
    rel = client.rel
    return (f"joins {client.joins}, heartbeats {client.beats}, states {client.states}, "
            f"gaps {client.gaps}, events in {client.events} (stale {rel.stale}), out "
            f"{rel.out_next - 1} (sent {rel.sent}, resent {rel.resent}, unacked {len(rel.out)})"
            + (f"; {prefix}{describe_state(client.state)}" if client.state else ""))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("listen", help="print broadcast datagrams until a full run arrives")
    p.add_argument("--port", type=int, default=PORT)
    p.add_argument("--tunnel", type=int, metavar="PORT",
                   help="read raw frames from xemu's udp backend on this port")
    p.add_argument("--bind", default="0.0.0.0")
    p.add_argument("--timeout", type=float, help="give up after this many seconds")
    p = sub.add_parser("ping", help="measure echo round trips to a console running tes3xnet up")
    p.add_argument("host")
    p.add_argument("--count", type=int, default=100)
    p.add_argument("--interval", type=float, default=0.02, help="seconds between pings")
    p.add_argument("--pad", type=int, default=0, help="extra payload bytes")
    p.add_argument("--reply-timeout", type=float, default=0.5)
    p.add_argument("--tunnel", type=int, metavar="PORT", help="ping through xemu's udp backend")
    p.add_argument("--wait", type=float, default=600,
                   help="seconds to wait for the console to answer before measuring")
    p.add_argument("-v", "--verbose", action="store_true")
    p = sub.add_parser("serve", help="run a session server for consoles (tes3xnet up ... SERVER)")
    p.add_argument("--port", type=int, default=PORT)
    p.add_argument("--bind", default="0.0.0.0")
    p.add_argument("--tunnel", type=int, metavar="PORT", help="serve through xemu's udp backend")
    p.add_argument("--duration", type=float, help="stop after this many seconds")
    p.add_argument("--report", type=float, default=30, help="seconds between status lines")
    p.add_argument("--host", action="append", default=[], metavar="NAME=ADDRESS",
                   help="answer DNS queries for NAME, on port 53 and through the tunnel "
                        "(repeatable)")
    p.add_argument("--bot", action="store_true",
                   help="relay a synthetic player circling where the first client stands")
    p.add_argument("--bot-radius", type=float, default=256, help="units")
    p.add_argument("--bot-period", type=float, default=12, help="seconds per circle")
    p.add_argument("--bot-rate", type=float, default=20, help="states per second")
    p.add_argument("--bot-say", type=float, metavar="SECONDS",
                   help="the bot also sends a numbered text event this often")
    p.add_argument("--drop", type=float, default=0,
                   help="drop this fraction of session packets each way, to test loss")
    p.add_argument("--seed", type=int, help="seed for --drop")
    p.add_argument("--load-order", metavar="HASH",
                   help="refuse clients whose load order hash differs (hex); default: the first "
                        "client's")
    p = sub.add_parser("plugin", help="write the ghost plugin the multiplayer patch moves")
    p.add_argument("out")
    p.add_argument("--master", required=True, help="Morrowind.esm, for its size")
    args = ap.parse_args(argv)
    if args.command == "plugin":
        with open(args.out, "wb") as f:
            f.write(ghost_plugin(os.path.getsize(args.master)))
        return 0
    return {"listen": listen, "ping": ping, "serve": serve}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
