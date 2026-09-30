#!/usr/bin/env python3
"""Talk to the payload's network driver (the `multiplayer` patch).

    python tools/tes3x_net.py listen                     # console broadcasts on UDP 26500
    python tools/tes3x_net.py ping 192.0.2.50             # echo round trips to `tes3xnet up`
    python tools/tes3x_net.py listen --tunnel 9369       # the same through xemu's udp backend
    python tools/tes3x_net.py ping 10.0.2.15 --tunnel 9369
    python tools/tes3x_net.py serve --tunnel 9369 --bot   # plus a player circling the first client
    python tools/tes3x_net.py serve --tunnel 9369 --bot --bot-say 2 --drop 0.2   # events under loss
    python tools/tes3x_net.py serve --tunnel 9369 --bot --bot-owns 20:60   # the bot runs the cell
    python tools/tes3x_net.py serve --tunnel 9369 --tunnel 9371   # two xemus, one per tunnel
    python tools/tes3x_net.py serve --tunnel 9369 --send FILE   # to TES3X on each console's U:
    python tools/tes3x_net.py plugin OUT.esp --master Morrowind.esm   # the ghost plugin

With --tunnel PORT this tool is the guest's only peer: xemu sends each guest Ethernet frame to
PORT as one datagram and accepts frames on PORT+1 (`tes3x_xemu.py --net-tunnel PORT`), so ping
answers ARP itself and resolves the console's MAC before it pings.
"""

import argparse
import hashlib
import hmac
import json
import math
import os
import random
import select
import socket
import struct
import sys
import time
import traceback

PORT = 26500
DNS_PORT = 53
DHCP_SERVER, DHCP_CLIENT = 67, 68
DHCP_MAGIC = bytes([0x63, 0x82, 0x53, 0x63])
GUEST_IP = "10.0.2.15"
MAGIC = b"TES3XNET"
PING, PONG = b"TES3XPNG", b"TES3XPON"
PEER_MAC = bytes.fromhex("020000000001")
PEER_IP = "10.0.2.2"
BROADCAST = b"\xff" * 6


def wire_text(data):
    """Text a client sent, safe to print and store: control characters become '?'."""
    return "".join(c if " " <= c < "\x7f" or c >= "\xa0" else "?" for c in data.decode("latin-1"))


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


def udp_frame(dst_mac, dst_ip, payload, ident=0, sport=PORT, dport=PORT):
    """An Ethernet frame carrying payload from PEER_IP:sport to dst_ip:dport."""
    udp = struct.pack(">HHHH", sport, dport, 8 + len(payload), 0) + payload
    ip = struct.pack(">BBHHHBBH4s4s", 0x45, 0, 20 + len(udp), ident & 0xFFFF, 0, 64, 17, 0,
                     socket.inet_aton(PEER_IP), socket.inet_aton(dst_ip))
    ip = ip[:10] + struct.pack(">H", checksum(ip)) + ip[12:]
    return dst_mac + PEER_MAC + b"\x08\x00" + ip + udp


def dhcp_reply(request, lease):
    """An OFFER to a DISCOVER or an ACK to a REQUEST, leasing GUEST_IP with this tool as router
    and DNS server; None for anything else."""
    if len(request) < 240 or request[0] != 1 or request[236:240] != DHCP_MAGIC:
        return None
    kind, off = None, 240
    while off + 1 < len(request) and request[off] != 255:
        if request[off] == 0:
            off += 1
            continue
        if request[off] == 53 and request[off + 1]:
            kind = request[off + 2]
        off += 2 + request[off + 1]
    reply = {1: 2, 3: 5}.get(kind)
    if not reply:
        return None
    peer = socket.inet_aton(PEER_IP)
    head = (bytes([2, 1, 6, 0]) + request[4:8] + bytes(2) + request[10:12] + bytes(4)
            + socket.inet_aton(GUEST_IP) + peer + bytes(4) + request[28:44] + bytes(192))
    options = (bytes([53, 1, reply, 54, 4]) + peer + bytes([51, 4]) + struct.pack(">I", lease)
               + bytes([1, 4, 255, 255, 255, 0, 3, 4]) + peer + bytes([6, 4]) + peer + bytes([255]))
    return head + DHCP_MAGIC + options


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
T3MP_VERSION = 11
HELLO, WELCOME, HEARTBEAT, BYE, STATE, PEER, GONE, EVENTS, REFUSE, CLOCK, ACTORS = range(1, 12)
# On the wire every packet but the handshake is SEALED: OUTER in the clear (the AEAD's associated
# data), then INNER and the body sealed under the session key with seq as the nonce.
HANDSHAKE1, HANDSHAKE2, HANDSHAKE3, SEALED = range(20, 24)
OUTER = struct.Struct("<4sBBHII")  # magic, version, type, 0, session, seq
INNER = struct.Struct("<B3xIII")  # type, ack, time, echo
PROLOGUE = b"TES3X T3MP 11"
HANDSHAKE_PAD = 128  # a HANDSHAKE1 is at least as large as the HANDSHAKE2 it draws
HANDSHAKE_KEEP = 10.0  # seconds a handshake is kept to answer its resent messages
# A HANDSHAKE1 costs the server four X25519 before the client has proved anything: per source
# address and in all, (burst, per second); and at most HANDSHAKES_PENDING kept at once.
HANDSHAKE_RATE = (10, 5.0)
HANDSHAKE_RATE_ALL = (200, 100.0)
HANDSHAKES_PENDING = 1024
CLIENT_RATE = (600, 600.0)  # sealed packets from one joined client; a console sends about 60/s
# GameHour, Day, Month (0-11), Year, DaysPassed, TimeScale, as the game's float globals
CLOCK_BODY = struct.Struct("<6f")
# MAC, build id, load order hash, plugin count, then the client's clock; NetPassword follows
HELLO_BODY = struct.Struct("<6sIII" + CLOCK_BODY.format[1:])
PASSWORD_MAX = 64
PASSWORD_RATE = (5, 1 / 60)  # password tries from one address, (burst, per second)
# REFUSE: the session's load order hash, its plugin count, and why
REFUSE_BODY = struct.Struct("<III")
REFUSED_LOAD_ORDER, REFUSED_FULL, REFUSED_PASSWORD = 1, 2, 3
MONTH_DAYS = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
TIMEOUT = 5.0
EVENTS_HEAD = struct.Struct("<IB3x")  # the sender's last delivered event, event count
EVENT = struct.Struct("<IHHI")  # seq, kind, length, origin client; the data follows
EVENTS_BYTES = 512  # the client's largest EVENTS body
EVENT_DATA = 80
EVENT_TEXT = 1
EVENT_AUTHORITY, EVENT_HOLD, EVENT_HOLD_BROKEN, EVENT_HIT, EVENT_DEATH = 2, 3, 4, 5, 6
EVENT_EQUIPMENT = 7  # part, parts, then item ids each ending in a zero
EVENT_WEATHER = 8  # flags, count, then (region index u16, weather u8) each
EVENT_PLAYER_HIT = 9  # attacker refid (0: a player), victim client, health, fatigue
EVENT_SPELL = 10  # SPELL, then the spell id ending in a zero
EVENT_CAST = 11  # as EVENT_SPELL, sent to every other client; the target may be empty
EVENT_SHOT = 12  # SHOT, then the ammunition id ending in a zero
EVENT_OBJECTS = 13  # count, then OBJECT records
# A data-file reference's shared state: refid, its cell's index in the cells list (the same under
# one load order), state bits, lock level.
OBJECT = struct.Struct("<IHBB")
OBJECT_DISABLED, OBJECT_DELETED, OBJECT_LOCK, OBJECT_LOCKED = 1, 2, 4, 8
OBJECTS_PER_EVENT = (EVENT_DATA - 1) // OBJECT.size
EVENT_SPAWN = 14  # SPAWN, then the base object's id ending in a zero
EVENT_REMOVE = 15  # count, then spawn ids
# A reference made at run time: its id (from its maker a token, which the server replaces), cell
# index, stack count with SPAWN_REMOVED once removed and SPAWN_DATA if it has item data, position,
# orientation, and the item data's condition (uses, time left) and charge, raw: an int or a float
# by the item's type. With SPAWN_LEVELED it is a leveled creature, and its placeholder's refid
# follows before the id; SPAWN_SUMMON marks a summon, run by the client that made it (the event's
# origin).
SPAWN = struct.Struct("<IHH6fII")
SPAWN_REMOVED, SPAWN_DATA, SPAWN_LEVELED, SPAWN_SUMMON = 0x8000, 0x4000, 0x2000, 0x1000
SPAWN_COUNT = 0x0FFF
SPAWN_IDS = 0xFF000000  # never a data-file refid: mod index 0xFF
SPAWN_TWIN = 16.0  # units: a script's reference made on two consoles at once
SPAWN_TWIN_SECONDS = 2.0
EVENT_CONTENTS = 16  # CONTENTS_HEAD, then entries
EVENT_WANT = 17  # count, then cell indices u16: the containers of those cells are wanted
EVENT_AFFECT = 18  # actor id, effect index u8, then the spell id ending in a zero
EVENT_STATUS = 19  # STATUS: the latest per actor is kept and replayed
# actor id, fight, flee, alarm, hello, base disposition (NO_DISPOSITION for a creature)
STATUS = struct.Struct("<I5h")
NO_DISPOSITION = -32768
# A container's contents in parts: refid, cell index, part, parts, flags (CONTENTS_ROLLED: the
# console's first reading of its instance). An entry: count, flags (ENTRY_DATA: its condition and
# charge follow, raw), then the item's id ending in a zero.
CONTENTS_HEAD = struct.Struct("<IHBBB")
CONTENTS_ROLLED = 1
ENTRY = struct.Struct("<iB")
ENTRY_DATA = 1
SHOT = struct.Struct("<IffB")  # firer refid, the two shot values, firer is a player
# caster refid, target client, target refid (0: its player), source type, caster is a player
SPELL = struct.Struct("<IIIBB")
SOURCE_SPELL = 1
WEATHER_OFFER = 1  # a joining client's whole table: the server keeps regions it does not know
WEATHER_ENTRY = struct.Struct("<HB")
WEATHER_PER_EVENT = (EVENT_DATA - 2) // WEATHER_ENTRY.size
WEATHERS = ("clear", "cloudy", "foggy", "overcast", "rain", "thunder", "ash", "blight", "snow",
            "blizzard")
# refid, target client, then a word: on, reason, or the damage as a float
TARGETED = {EVENT_HOLD: "holds", EVENT_HOLD_BROKEN: "breaks the hold on", EVENT_HIT: "hits",
            EVENT_PLAYER_HIT: "hits the player of"}
KEY = struct.Struct("<Iii32s")  # kind, grid x, grid y, interior name
KEY_EXTERIOR, KEY_INTERIOR = 1, 2
ANIM_BYTES = 20  # per layer: 3 groups, pad, 3 keys, pad, 3 times (tes3xnet.c anim_capture)
NO_ANIM = b"\xff\xff\xff" + bytes(ANIM_BYTES - 3)  # no group on any layer: the ghost idles
# refid, x, y, z, heading, health, flags, magicka, fatigue, animation
ACTOR = struct.Struct(f"<I5fI2f{ANIM_BYTES}s")
ACTORS_PER_PACKET = 9
ACTOR_PERIOD = 0.1
AUTHORITY_PERIOD = 0.25
RESEND = 0.25
# Least time between two EVENTS packets to one client, and at most PACE_PACKETS packets to one
# client per PACE_WINDOW seconds, the rest queued. xemu's NIC stops reading its tunnel for good once
# frames arrive faster than the guest's receive slots drain.
EVENTS_GAP = 0.02
PACE_PACKETS = 4
PACE_WINDOW = 0.005
CLOCK_INTERVAL = 1.0
# Bulk transfer: OFFER names the file; CHUNK carries id, index and BULK_CHUNK bytes; the receiver's
# BULK_ACK gives the next chunk it will write, a bitmap of the 32 after it that arrived, how many
# chunks past it may be in flight, and its status.
CHUNK, BULK_ACK = 16, 17
EVENT_OFFER = 32  # BULK_OFFER, then the file name ending in a zero
BULK_OFFER = struct.Struct("<II32s")  # id, size, BLAKE2b-256 of the file
BULK_ACK_BODY = struct.Struct("<5I")
BULK_CHUNK = 1024
BULK_NAME = 37  # with ".part", FATX's 42 characters
BULK_RESEND = 0.5
BULK_PROBE = 1.0
BULK_STATUS = ("idle", "opening", "receiving", "done", "bad hash", "refused", "failed")
BULK_RECEIVING, BULK_DONE, BULK_BAD_HASH, BULK_REFUSED, BULK_FAILED = 2, 3, 4, 5, 6
BULK_WINDOW_IN = 8  # chunks a console keeps in flight to the server: its send slots
BULK_ACK_EVERY = 0.25  # seconds between acks to a console that is sending
UPLOAD_FILES = 64  # files one console key may keep in its uploads folder
BULK_MAX = 16 << 20  # as the console's
# Names Windows opens as devices, whatever the extension
DEVICE_NAMES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(10)),
                *(f"LPT{i}" for i in range(10))}


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


def pack_equipment(ids):
    """The EQUIPMENT events of one set, as tes3xnet.c equipment_send packs them."""
    parts = [b""]
    for item in ids:
        item = item.encode("latin-1")[:31] + b"\0"
        if 2 + len(parts[-1]) + len(item) > EVENT_DATA:
            parts.append(b"")
        parts[-1] += item
    return [bytes((i, len(parts))) + part for i, part in enumerate(parts)]


def unpack_equipment(part):
    return [wire_text(item) for item in part[2:].split(b"\0") if item]


def pack_weather(entries, flags=0):
    """WEATHER events for {region index: weather}, as tes3xnet.c weather_frame packs them."""
    items = sorted(entries.items())
    return [bytes((flags, len(chunk))) + b"".join(WEATHER_ENTRY.pack(*e) for e in chunk)
            for chunk in (items[i:i + WEATHER_PER_EVENT]
                          for i in range(0, len(items), WEATHER_PER_EVENT))]


def pack_objects(objects):
    """OBJECTS events for {refid: (cell, state, level)}."""
    items = sorted(objects.items())
    return [bytes([len(chunk)]) + b"".join(OBJECT.pack(refid, *rest) for refid, rest in chunk)
            for chunk in (items[i:i + OBJECTS_PER_EVENT]
                          for i in range(0, len(items), OBJECTS_PER_EVENT))]


def unpack_objects(data):
    """{refid: (cell, state, level)} of an OBJECTS event."""
    count = data[0] if data else 0
    return {refid: (cell, state, level) for refid, cell, state, level in
            (OBJECT.unpack_from(data, 1 + i * OBJECT.size) for i in range(count)
             if 1 + (i + 1) * OBJECT.size <= len(data))}


def describe_object(refid, cell, state, level):
    words = [name for bit, name in ((OBJECT_DISABLED, "disabled"), (OBJECT_DELETED, "taken"))
             if state & bit]
    if state & OBJECT_LOCK:
        words.append(f"locked {level}" if state & OBJECT_LOCKED else "unlocked")
    return f"{refid:#010x} in cell {cell}: {', '.join(words) or 'restored'}"


def pack_spawn(sid, spawn):
    """A SPAWN event for one reference made at run time."""
    leveled = spawn.get("leveled", 0)
    count = spawn["count"] | (SPAWN_REMOVED if spawn["removed"] else 0) | \
        (SPAWN_DATA if spawn.get("data") else 0) | (SPAWN_LEVELED if leveled else 0) | \
        (SPAWN_SUMMON if spawn.get("summon") else 0)
    return (SPAWN.pack(sid, spawn["cell"], count, *spawn["pos"], *spawn["rot"],
                       spawn.get("condition", 0), spawn.get("charge", 0))
            + (struct.pack("<I", leveled) if leveled else b"") + zstr(spawn["id"][:31]))


def unpack_spawn(data):
    """(sid, spawn) of a SPAWN event."""
    sid, cell, count, *place, condition, charge = SPAWN.unpack_from(data)
    off, leveled = SPAWN.size, 0
    if count & SPAWN_LEVELED:
        if len(data) < off + 4:
            raise ValueError("SPAWN too short for its placeholder")
        leveled = struct.unpack_from("<I", data, off)[0]
        off += 4
    name = wire_text(data[off:].split(b"\0")[0])
    return sid, {"cell": cell, "count": count & SPAWN_COUNT, "leveled": leveled,
                 "summon": bool(count & SPAWN_SUMMON),
                 "removed": bool(count & SPAWN_REMOVED), "pos": place[:3], "rot": place[3:],
                 "id": name, "data": bool(count & SPAWN_DATA), "condition": condition,
                 "charge": charge}


def pack_removes(sids):
    """REMOVE events for spawn ids, as tes3xnet.c spawns_frame packs them."""
    per = (EVENT_DATA - 1) // 4
    return [bytes([len(chunk)]) + struct.pack(f"<{len(chunk)}I", *chunk)
            for chunk in (sids[i:i + per] for i in range(0, len(sids), per))]


def unpack_removes(data):
    count = min(data[0], (len(data) - 1) // 4) if data else 0
    return list(struct.unpack_from(f"<{count}I", data, 1))


def spawn_twin(spawns, spawn, origin, token, now, deaths=()):
    """The id of a spawn that this one repeats: the maker sending it again (by its token), the
    living creature of the same leveled placeholder, or the same object made in the same place by
    another console's copy of a script moments ago."""
    for sid, known in spawns.items():
        if token and known["origin"] == origin and known.get("token") == token:
            return sid
    if spawn.get("leveled"):
        for sid, known in spawns.items():
            if known.get("leveled") == spawn["leveled"] and not known["removed"] and \
                    sid not in deaths:
                return sid
        return None
    if spawn.get("summon"):
        return None
    for sid, known in spawns.items():
        if known["removed"] or known["origin"] == origin or known.get("summon") or \
                known["id"].lower() != spawn["id"].lower() or known["cell"] != spawn["cell"]:
            continue
        far = max(abs(a - b) for a, b in zip(known["pos"], spawn["pos"]))
        if far <= SPAWN_TWIN and now - known.get("made", 0) <= SPAWN_TWIN_SECONDS:
            return sid
    return None


def pack_contents(refid, cell, entries, flags=0):
    """CONTENTS events for [id, count, flags, condition, charge] entries, as tes3xnet.c packs
    them."""
    parts = [b""]
    for name, count, entry_flags, condition, charge in entries:
        item = ENTRY.pack(count, entry_flags)
        if entry_flags & ENTRY_DATA:
            item += struct.pack("<II", condition, charge)
        item += zstr(name[:31])
        if CONTENTS_HEAD.size + len(parts[-1]) + len(item) > EVENT_DATA:
            parts.append(b"")
        parts[-1] += item
    return [CONTENTS_HEAD.pack(refid, cell, i, len(parts), flags) + part
            for i, part in enumerate(parts)]


def unpack_contents(data):
    """(refid, cell, part, parts, flags, entries) of one CONTENTS event."""
    refid, cell, part, parts, flags = CONTENTS_HEAD.unpack_from(data)
    off, entries = CONTENTS_HEAD.size, []
    while off + ENTRY.size < len(data):
        count, entry_flags = ENTRY.unpack_from(data, off)
        off += ENTRY.size
        condition = charge = 0
        if entry_flags & ENTRY_DATA:
            if off + 8 > len(data):
                raise ValueError("CONTENTS entry too short for its item data")
            condition, charge = struct.unpack_from("<II", data, off)
            off += 8
        end = data.index(b"\0", off) if b"\0" in data[off:] else len(data)
        entries.append([wire_text(data[off:end]), count, entry_flags, condition, charge])
        off = end + 1
    return refid, cell, part, parts, flags, entries


def describe_status(refid, values):
    fight, flee, alarm, hello, disposition = values
    base = "" if disposition == NO_DISPOSITION else f", disposition {disposition}"
    return (f"{refid:#010x} fight {fight}, flee {flee}, alarm {alarm}, hello {hello}{base}")


def describe_contents(entries):
    return ", ".join(f"{name} x{count}" + (f" (condition {condition:#x})" if flags else "")
                     for name, count, flags, condition, _ in entries) or "empty"


def describe_spawn(sid, spawn):
    x, y, z = spawn["pos"]
    what = f"{spawn['id']}" + (f" x{spawn['count']}" if spawn["count"] > 1 else "")
    if spawn.get("leveled"):
        what += f" for placeholder {spawn['leveled']:#010x}"
    if spawn.get("summon"):
        what += f" summoned by client {spawn.get('origin')}"
    if spawn.get("data"):
        what += f" (condition {spawn['condition']:#x}, charge {spawn['charge']:#x})"
    return (f"{sid:#010x} {what} in cell {spawn['cell']} at {x:.0f} {y:.0f} {z:.0f}"
            + (" (removed)" if spawn["removed"] else ""))


def load_world(path):
    """The saved world of one load order, or None."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None


def save_world(path, world):
    """Write by temporary file and rename, so a crash leaves the last whole save."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(world, f, indent=1, sort_keys=True)
    os.replace(path + ".tmp", path)


def unpack_weather(data):
    """(flags, {region index: weather}) of a WEATHER event."""
    flags, count = data[0], data[1]
    entries = {}
    for i in range(count):
        off = 2 + i * WEATHER_ENTRY.size
        if off + WEATHER_ENTRY.size > len(data):
            break
        index, weather = WEATHER_ENTRY.unpack_from(data, off)
        entries[index] = weather
    return flags, entries


def describe_weather(entries):
    return ", ".join(f"{i} {WEATHERS[w] if w < len(WEATHERS) else w}"
                     for i, w in sorted(entries.items()))


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
NOISE_PROTOCOL = b"Noise_XX_25519_ChaChaPoly_BLAKE2b"
NOISE_TAG = 16


def crypto():
    """The primitives the encrypted session needs beyond hashlib, from the cryptography package."""
    try:
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.asymmetric import x25519
        from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
    except ImportError:
        raise SystemExit("the session is encrypted: pip install cryptography") from None
    return InvalidTag, x25519, ChaCha20Poly1305


def aead_nonce(counter):
    return bytes(4) + struct.pack("<Q", counter)


def seal(key, counter, ad, data):
    """ChaCha20-Poly1305 (RFC 8439): the ciphertext, then the tag."""
    return crypto()[2](key).encrypt(aead_nonce(counter), data, ad)


def unseal(key, counter, ad, data):
    """The plaintext, or None if data is not authentic."""
    invalid, _, aead = crypto()
    try:
        return aead(key).decrypt(aead_nonce(counter), data, ad)
    except invalid:
        return None


def x25519_public(secret):
    from cryptography.hazmat.primitives import serialization
    return crypto()[1].X25519PrivateKey.from_private_bytes(secret).public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def fingerprint(public):
    return hashlib.blake2b(public, digest_size=16).hexdigest()


def plain_name(name):
    """A file name the console and FATX both take: letters, digits, ' .-_', not led by a dot."""
    return (0 < len(name) <= BULK_NAME and not name.startswith(".") and
            all(c.isascii() and (c.isalnum() or c in " .-_") for c in name))


class Incoming:
    """One file a console sends: chunks written in order to NAME.part in its folder, renamed to
    NAME once the whole file's BLAKE2b matches the offer. A part left by an earlier try is
    resumed; without a folder the offer is refused."""

    def __init__(self, folder, ident, size, digest, name, now):
        self.id, self.size, self.hash, self.name = ident, size, digest, name
        self.chunks = (size + BULK_CHUNK - 1) // BULK_CHUNK
        self.next = self.first = self.arrived = 0
        self.held, self.stream = {}, None
        self.started, self.acked = now, 0.0
        self.path = os.path.join(folder, name) if folder else None
        self.status = BULK_REFUSED
        if not folder or size > BULK_MAX or not plain_name(name) or name[-1] in " ." or \
                name.split(".")[0].upper() in DEVICE_NAMES:
            return
        os.makedirs(folder, exist_ok=True)
        if os.path.exists(self.path) and self.matches(self.path):
            self.status, self.next = BULK_DONE, self.chunks
            return
        kept = [f for f in os.listdir(folder) if not f.endswith(".part") and f != name]
        if len(kept) >= UPLOAD_FILES:
            return
        part = self.path + ".part"
        have = os.path.getsize(part) if os.path.exists(part) else 0
        self.next = min(have, size) // BULK_CHUNK
        self.stream = open(part, "r+b" if have else "wb")
        self.stream.truncate(self.next * BULK_CHUNK)
        self.stream.seek(self.next * BULK_CHUNK)
        self.first = self.next
        self.status = BULK_RECEIVING
        if self.next == self.chunks:
            self.finish()

    def matches(self, path):
        digest = hashlib.blake2b(digest_size=32)
        with open(path, "rb") as stream:
            for block in iter(lambda: stream.read(1 << 20), b""):
                digest.update(block)
        return os.path.getsize(path) == self.size and digest.digest() == self.hash

    def finish(self):
        self.stream.close()
        self.stream = None
        part = self.path + ".part"
        if self.matches(part):
            os.replace(part, self.path)
            self.status = BULK_DONE
        else:
            os.remove(part)
            self.status, self.next = BULK_BAD_HASH, 0

    def on_chunk(self, index, data):
        """Take a chunk; true when it should be acked now: out of the window, a duplicate, the
        end, or every fourth arrival."""
        if self.status != BULK_RECEIVING or not self.next <= index < min(
                self.next + BULK_WINDOW_IN, self.chunks) or index in self.held or len(data) != (
                BULK_CHUNK if index + 1 < self.chunks else self.size - index * BULK_CHUNK):
            return True
        self.held[index] = data
        self.arrived += 1
        while self.next in self.held:
            self.stream.write(self.held.pop(self.next))
            self.next += 1
        if self.next == self.chunks:
            self.finish()
            return True
        return self.arrived % 4 == 0

    def ack(self, now):
        self.acked = now
        bitmap = sum(1 << (i - self.next) for i in self.held if i - self.next < 32)
        return BULK_ACK_BODY.pack(self.id, self.next, bitmap,
                                  BULK_WINDOW_IN if self.status == BULK_RECEIVING else 0,
                                  self.status)


class Noise:
    """A Noise_XX_25519_ChaChaPoly_BLAKE2b handshake, either side; tes3xnoise.c is the console's
    initiator. Raises ValueError on a message that does not authenticate."""

    def __init__(self, initiator, s_secret, e_secret, prologue):
        self.initiator, self.s, self.e = initiator, s_secret, e_secret
        self.h = self.ck = NOISE_PROTOCOL.ljust(64, b"\0")
        self.k, self.n, self.re, self.rs = None, 0, None, None
        self.mix_hash(prologue)

    def mix_hash(self, data):
        self.h = hashlib.blake2b(self.h + data).digest()

    def hkdf(self, ikm):
        temp = hmac.new(self.ck, ikm, hashlib.blake2b).digest()
        out1 = hmac.new(temp, b"\x01", hashlib.blake2b).digest()
        return out1, hmac.new(temp, out1 + b"\x02", hashlib.blake2b).digest()

    def dh(self, secret, public):
        try:
            shared = crypto()[1].X25519PrivateKey.from_private_bytes(secret).exchange(
                crypto()[1].X25519PublicKey.from_public_bytes(public))
        except ValueError:
            raise ValueError("low-order key") from None
        self.ck, temp = self.hkdf(shared)
        self.k, self.n = temp[:32], 0

    def encrypt(self, plain):
        out = plain
        if self.k:
            out, self.n = seal(self.k, self.n, self.h, plain), self.n + 1
        self.mix_hash(out)
        return out

    def decrypt(self, data):
        plain = data
        if self.k:
            plain = unseal(self.k, self.n, self.h, data)
            if plain is None:
                raise ValueError("not authentic")
            self.n += 1
        self.mix_hash(data)
        return plain

    def write_e(self):
        e = x25519_public(self.e)
        self.mix_hash(e)
        return e

    def read_e(self, message):
        if len(message) < 32:
            raise ValueError("short message")
        self.re = message[:32]
        self.mix_hash(self.re)
        return message[32:]

    def write1(self, payload=b""):
        return self.write_e() + self.encrypt(payload)

    def read1(self, message):
        return self.decrypt(self.read_e(message))

    def write2(self, payload=b""):
        out = self.write_e()
        self.dh(self.e, self.re)
        out += self.encrypt(x25519_public(self.s))
        self.dh(self.s, self.re)
        return out + self.encrypt(payload)

    def read2(self, message):
        rest = self.read_e(message)
        if len(rest) < 32 + 2 * NOISE_TAG:
            raise ValueError("short message")
        self.dh(self.e, self.re)
        self.rs = self.decrypt(rest[:32 + NOISE_TAG])
        self.dh(self.e, self.rs)
        return self.decrypt(rest[32 + NOISE_TAG:])

    def write3(self, payload=b""):
        out = self.encrypt(x25519_public(self.s))
        self.dh(self.s, self.re)
        return out + self.encrypt(payload)

    def read3(self, message):
        if len(message) < 32 + 2 * NOISE_TAG:
            raise ValueError("short message")
        self.rs = self.decrypt(message[:32 + NOISE_TAG])
        self.dh(self.e, self.rs)
        return self.decrypt(message[32 + NOISE_TAG:])

    def split(self):
        """(initiator to responder, responder to initiator) transport keys."""
        one, two = self.hkdf(b"")
        return one[:32], two[:32]


class Outgoing:
    """One file sent to one client: chunks inside the window its last ack allows, each resent
    after BULK_RESEND until acked, or at once when a chunk sent after it has arrived."""

    def __init__(self, name, data):
        self.name, self.data = name, data
        self.hash = hashlib.blake2b(data, digest_size=32).digest()
        self.id = int.from_bytes(self.hash[:4], "little") or 1
        self.chunks = (len(data) + BULK_CHUNK - 1) // BULK_CHUNK
        self.next = None  # unknown until the first ack
        self.seen, self.sent_at = set(), {}
        self.serial, self.sends, self.lost = {}, 0, set()  # each chunk's latest send, in order
        self.window = self.status = self.sent = self.resent = self.probes = self.fast = 0
        self.first = self.started = None
        self.acked = 0.0  # when the last ack arrived

    def offer(self):
        return BULK_OFFER.pack(self.id, len(self.data), self.hash) + zstr(self.name)

    def on_ack(self, body, now):
        """Take an ack; returns the new status if it changed."""
        ident, nxt, bitmap, window, status = BULK_ACK_BODY.unpack_from(body)
        if ident != self.id:
            return None
        if self.first is None:
            self.first, self.started = nxt, now
        self.acked = now
        self.next, self.window = nxt, window
        self.seen = {nxt + k for k in range(32) if bitmap >> k & 1}
        self.sent_at = {i: t for i, t in self.sent_at.items() if i >= nxt}
        if self.seen:
            last = self.serial.get(max(self.seen), 0)
            self.lost |= {i for i in range(nxt, max(self.seen))
                          if i not in self.seen and 0 < self.serial.get(i, last) < last}
        self.lost = {i for i in self.lost if i >= nxt and i not in self.seen}
        changed = status != self.status
        self.status = status
        return status if changed else None

    def due(self, now):
        """The chunk indices to send now."""
        if self.next is None or self.status != BULK_RECEIVING:
            return []
        out = []
        for i in range(self.next, min(self.next + self.window, self.chunks)):
            sent = self.sent_at.get(i)
            if i in self.seen or (i not in self.lost and sent is not None and
                                  now - sent < BULK_RESEND):
                continue
            self.resent += sent is not None
            self.fast += i in self.lost
            self.lost.discard(i)
            self.sent += 1
            self.sends += 1
            self.sent_at[i], self.serial[i] = now, self.sends
            out.append(i)
        # Everything in flight arrived but the final ack did not: a repeated chunk draws another.
        if not out and now - self.acked >= BULK_PROBE:
            self.acked = now
            self.probes += 1
            out.append(min(self.next, max(self.chunks - 1, 0)))
        return out

    def chunk(self, index):
        return struct.pack("<II", self.id, index) + self.data[index * BULK_CHUNK:
                                                              (index + 1) * BULK_CHUNK]


STATE_BODY = struct.Struct("<I4f32s")  # flags, x, y, z, heading, interior cell name
STATE_SIZE = STATE_BODY.size + ANIM_BYTES  # then the animation
IN_WORLD, INTERIOR = 1, 2
STANCE = 4 | 8  # weapon drawn, spell readied; in STATE's flags and ACTOR's
PLACE = IN_WORLD | INTERIOR
CELL_UNITS = 8192


def describe_state(state):
    flags, x, y, z, heading, cell = STATE_BODY.unpack_from(state)
    if not flags & IN_WORLD:
        return "not in the world"
    where = (wire_text(cell.split(b"\0", 1)[0]) if flags & INTERIOR
             else f"exterior {int(x // CELL_UNITS)},{int(y // CELL_UNITS)}")
    return f"{where} at {x:.0f},{y:.0f},{z:.0f} heading {math.degrees(heading) % 360:.0f}"


def cell_keys(state):
    """(own cell, loaded cells) of a STATE: an interior, or an exterior cell and its neighbours."""
    flags, x, y, _, _, cell = STATE_BODY.unpack_from(state)
    if not flags & IN_WORLD:
        return None, set()
    if flags & INTERIOR:
        key = (KEY_INTERIOR, 0, 0, cell.split(b"\0", 1)[0])
        return key, {key}
    gx, gy = math.floor(x / CELL_UNITS), math.floor(y / CELL_UNITS)
    return ((KEY_EXTERIOR, gx, gy, b""),
            {(KEY_EXTERIOR, gx + dx, gy + dy, b"") for dx in (-1, 0, 1) for dy in (-1, 0, 1)})


def describe_key(key):
    kind, gx, gy, name = key
    return wire_text(name) if kind == KEY_INTERIOR else f"exterior {gx},{gy}"


def assign_authority(owners, candidates, forced=None):
    """Each loaded cell's authority. candidates: cell -> [(client, stands in it)] in joining
    order. An authority keeps a cell while it has it loaded, unless it only has it loaded and
    another client stands in it; forced takes every cell it has loaded."""
    result = {}
    for key, cands in candidates.items():
        ids = [c for c, _ in cands]
        standing = [c for c, here in cands if here]
        current = owners.get(key)
        if forced in ids:
            result[key] = forced
        elif current in ids and (current in standing or not standing):
            result[key] = current
        else:
            result[key] = (standing or ids)[0]
    return result


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
    They have no AI packages and zero fight, flee, alarm and hello, so they stand where put.
    Talking to a ghost would turn it to face the speaker, away from where its player faces: the
    payload refuses the player's activation of one, and Morrowind.esm's noPickUp script swallows
    the rest where the ghost has its script variables."""
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
                (b"KNAM", zstr("b_n_dark elf_m_hair_01")), (b"SCRI", zstr("noPickUp")),
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


def finite(*values):
    return all(math.isfinite(v) for v in values)


POSITION_LIMIT = 1e7  # units; the game's world spans well under a million


def placeable(*coordinates):
    """A position a client may report: finite, and inside cells a 32-bit grid can number."""
    return all(math.isfinite(v) and abs(v) <= POSITION_LIMIT for v in coordinates)


# What a HELLO's clock must fall in to be adopted: an hour past 24 or a huge timescale would keep
# Clock.advance rolling days for ever.
CLOCK_LIMITS = ((0, 24), (1, 31), (0, 11), (0, 100000), (0, 10000000), (0, 10000))
CLOCK_DEFAULT = (9.0, 16.0, 7.0, 427.0, 1.0, 30.0)


def sane_clock(offered):
    ok = finite(*offered) and all(lo <= v <= hi for v, (lo, hi) in zip(offered, CLOCK_LIMITS))
    return list(offered) if ok else list(CLOCK_DEFAULT)


class Clock:
    """The session's game time, run by the server: it advances at TimeScale game seconds per real
    second and rolls days, months and years over as the game does."""

    def __init__(self, hour, day, month, year, days_passed, scale, now):
        self.hour, self.day, self.month, self.year = hour, int(day), int(month), int(year)
        self.days_passed, self.scale, self.last = int(days_passed), scale, now

    def advance(self, now):
        self.hour += (now - self.last) * self.scale / 3600
        self.last = now
        while self.hour >= 24:
            self.hour -= 24
            self.days_passed += 1
            self.day += 1
            if self.day > MONTH_DAYS[self.month % 12]:
                self.day = 1
                self.month += 1
                if self.month > 11:
                    self.month = 0
                    self.year += 1

    def body(self, now):
        self.advance(now)
        return CLOCK_BODY.pack(self.hour, self.day, self.month, self.year, self.days_passed,
                               self.scale)

    def __str__(self):
        return (f"{int(self.hour):02d}:{int(self.hour % 1 * 60):02d} day {self.day} month "
                f"{self.month} year {self.year} (days passed {self.days_passed}, "
                f"timescale {self.scale:g})")


class Bucket:
    """A token bucket: take() is false once more than burst arrive faster than rate per second."""

    def __init__(self, burst, rate):
        self.burst, self.rate = burst, rate
        self.tokens, self.last = float(burst), time.time()

    def take(self, now):
        self.tokens = min(self.burst, self.tokens + (now - self.last) * self.rate)
        self.last = now
        if self.tokens < 1:
            return False
        self.tokens -= 1
        return True


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
        self.known = {}  # cell -> the authority this client was told
        self.loaded = set()
        self.actor_states = 0
        self.flush_due = False  # an EVENTS packet held back by EVENTS_GAP
        self.queue = []  # (address, packet, seq) held back by PACE_PACKETS
        self.window = (0.0, 0)  # the current PACE_WINDOW's start and packets sent in it
        self.joined = 0.0
        self.bursts = []  # (seconds after joining, packets) still to send
        self.bulk = None  # the Outgoing file of --send, offered again on each join
        self.upload = None  # the Incoming file this console is sending
        self.key = None  # the console's static key for this server: its identity
        self.keys = None  # (console to server, server to console) from the handshake
        self.replay = (0, 0)  # the highest seq opened and a bitmap of the 32 up to it
        self.forged = self.replayed = self.limited = 0
        self.bucket = Bucket(*CLIENT_RATE)



def load_server_key(args):
    """The server's static secret: from --key, or server.key in the world folder, made on first
    use; without either, a new one each run."""
    crypto()
    path = args.key or (os.path.join(args.world, "server.key") if args.world else None)
    if path and os.path.exists(path):
        with open(path, encoding="ascii") as stream:
            secret = bytes.fromhex(stream.read().strip())
    else:
        secret = os.urandom(32)
        if path:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            with open(path, "w", encoding="ascii") as stream:
                stream.write(secret.hex() + "\n")
    print(f"server key {fingerprint(x25519_public(secret))}"
          + ("" if path else " (not kept: give --key or --world)"), flush=True)
    return secret


def load_password(args):
    """The first line of --password-file, or None for an open server."""
    if not args.password_file:
        return None
    with open(args.password_file, encoding="utf-8") as stream:
        password = stream.readline().strip()
    # The console reads NetPassword from its ini, which trims spaces and has no Unicode.
    if not 0 < len(password) <= PASSWORD_MAX or not password.isascii() or \
            not password.isprintable():
        sys.exit(f"{args.password_file}: the password must be 1 to {PASSWORD_MAX} printable "
                 "ASCII characters")
    return password.encode("ascii")


def load_admitted(path):
    """Console keys that have given the password, one hex key per line with a note after it."""
    admitted = set()
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as stream:
            for line in stream:
                if line.split():
                    admitted.add(bytes.fromhex(line.split()[0]))
    return admitted


def serve(args):
    """A session server: welcomes consoles by their key, answers each heartbeat at once and relays
    each client's state to the others. Each --tunnel also serves an xemu guest."""
    sys.stdout.reconfigure(errors="replace")  # the console's code page cannot print every name
    links = [Tunnel(port) for port in args.tunnel]
    sock = udp_socket()
    sock.bind((args.bind, args.port))
    print(f"serving on {args.bind}:{args.port}"
          + "".join(f" and tunnel {port}" for port in args.tunnel), flush=True)
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
    clock, clock_next = None, 0.0
    owners = {}  # cell -> authority client
    deaths = {}  # refid -> the client that reported it; replayed to each joining client
    # client -> [parts of its latest whole equipment set, parts of the set arriving]
    equipment = {}
    if args.bot_equip is not None:
        equipment[BOT_ID] = [pack_equipment([i for i in args.bot_equip.split(",") if i]), []]
    actors = {}  # refid -> (reporting client, cell, ACTOR bytes), the latest from an authority
    weather = {}  # region index -> weather, the session's; replayed to each joining client
    objects = {}  # refid -> (cell index, state, lock level); replayed to each joining client
    statuses = {}  # actor id -> STATUS values after the id; replayed to each joining client
    # spawn id -> reference made at run time (unpack_spawn), removed ones too; replayed likewise
    spawns = {}
    # refid -> {"cell", "entries", "origin"}: a container's latest contents; sent to whoever loads
    # its cell (WANT)
    contents = {}
    arriving = {}  # client id -> (refid, entries so far, next part)
    bot_boxes = []
    for spec in args.bot_contents:
        what, _, at = spec.rpartition("@")
        refid, cell, items = what.split(":", 2)
        entries = []
        for item in items.split(","):
            name, count, *condition = item.split("*")
            entries.append([name, int(count), ENTRY_DATA if condition else 0,
                            int(condition[0]) if condition else 0, 0])
        bot_boxes.append((float(at), int(refid, 16), int(cell), entries))
    world = {"path": None, "dirty": False, "saved": 0.0, "next_spawn": 1}
    bot_spawns = []
    for spec in args.bot_spawn:
        what, _, at = spec.rpartition("@")
        name, cell, *condition = what.split(":")
        bot_spawns.append((float(at), name, int(cell), int(condition[0]) if condition else None))
    bot_takes = [float(at) for at in args.bot_take]
    bot_weather = []
    bot_statuses = list(args.bot_status)
    bot_affects = list(args.bot_affect)
    bot_spells = []
    bot_shots = [(float(at), ammo) for ammo, _, at in (s.rpartition("@") for s in args.bot_shoot)]
    for kind, specs in ((EVENT_SPELL, args.bot_spell), (EVENT_CAST, args.bot_cast)):
        for spec in specs:
            cast, _, at = spec.partition("@")
            name, _, refid = cast.partition(":")
            bot_spells.append((float(at), kind, name, int(refid, 16) if refid else 0))
    for spec in args.bot_weather:
        change, _, at = spec.partition("@")
        index, _, value = change.partition(":")
        bot_weather.append((float(at), int(index), int(value)))
    authority_next = actor_next = 0.0
    server_secret = load_server_key(args)
    password = load_password(args)
    admitted_path = os.path.join(args.world, "admitted.txt") if args.world else None
    admitted, password_buckets = load_admitted(admitted_path), {}
    if password:
        print(f"password asked of new consoles; {len(admitted)} admitted"
              + ("" if admitted_path else " (not kept: give --world)"), flush=True)
    handshake_bucket, handshake_buckets = Bucket(*HANDSHAKE_RATE_ALL), {}
    limits = {"handshakes": 0}
    pending = {}  # session -> a handshake in progress or just done: {"noise", "e", "reply", ...}
    bursts = [(float(at), int(count)) for count, _, at in
              (spec.partition("@") for spec in args.burst)]
    sending = None
    if args.send:
        name = os.path.basename(args.send)
        if not plain_name(name):
            raise SystemExit(f"--send: {name!r} is not a plain name of at most {BULK_NAME} "
                             "characters")
        with open(args.send, "rb") as stream:
            sending = (name, stream.read())

    def window(spec, now):
        """Whether now falls in START:END seconds after the bot first placed itself."""
        if not spec or bot["anchored"] is None:
            return False
        start, _, end = spec.partition(":")
        t = now - bot["anchored"]
        return float(start) <= t < (float(end) if end else math.inf)

    def dropped(direction):
        if args.drop and loss.random() < args.drop:
            lost[direction] += 1
            return True
        return False

    def send(client, kind, body=b""):
        """Seal and queue a packet; nothing goes out before the handshake has keyed the client."""
        client.seq += 1
        if client.keys is None:
            return
        inner = INNER.pack(kind, client.peer_seq, now_us(), client.peer_time) + body
        outer = OUTER.pack(b"T3MP", T3MP_VERSION, SEALED, 0, client.session, client.seq)
        packet = outer + seal(client.keys[1], client.seq, outer, inner)
        if dropped("out"):
            return
        client.queue.append((client.addr, packet, client.seq))
        pump(client)

    def transmit(addr, packet, ident=0):
        if len(addr) == 4:  # a tunnel guest, by its MAC
            ip, _port, mac, link = addr
            link.send(udp_frame(mac, ip, packet, ident))
        else:
            sock.sendto(packet, addr)

    def pump(client):
        """Send what PACE_PACKETS allows of the client's queue."""
        now = time.time()
        start, count = client.window
        if now - start >= PACE_WINDOW:
            start, count = now, 0
        while client.queue and count < PACE_PACKETS:
            addr, packet, seq = client.queue.pop(0)
            transmit(addr, packet, seq)
            count += 1
        client.window = (start, count)

    bot = {"anchor": None, "next": 0.0, "start": time.time(), "said": 0.0, "line": 0,
           "anchored": None, "state": None, "breaks": [], "held": 0, "hit": False,
           "killed": False, "mirror": None, "hit_player": False, "echo": None}

    def bot_anchor(state):
        """The bot circles where the first client entered the world, and follows it to a new
        cell or across a long jump."""
        flags, x, y, z, _, cell = STATE_BODY.unpack_from(state)
        flags &= PLACE
        anchor = bot["anchor"]
        if flags & IN_WORLD and (anchor is None or anchor[0] != (flags, cell)
                                 or math.hypot(x - anchor[1], y - anchor[2]) > 2048):
            bot["anchor"] = ((flags, cell), x, y, z)
            if bot["anchored"] is None:
                bot["anchored"] = time.time()
            print(f"{time.strftime('%H:%M:%S')} bot circles {describe_state(state)}",
                  flush=True)

    def bot_step(now):
        (flags, cell), cx, cy, cz = bot["anchor"]
        t = (now - bot["start"]) * 2 * math.pi / args.bot_period
        if bot["mirror"]:
            _, x, y, z, heading, _, actor_flags, _, _, anim = ACTOR.unpack(bot["mirror"])
            state = STATE_BODY.pack(flags | actor_flags & STANCE, x + args.bot_shift, y, z,
                                    heading, cell) + anim
        elif bot["echo"]:
            echo_flags, x, y, z, heading, echo_cell = STATE_BODY.unpack_from(bot["echo"])
            state = STATE_BODY.pack(echo_flags, x + args.bot_shift, y, z, heading,
                                    echo_cell) + bot["echo"][STATE_BODY.size:]
        else:
            state = STATE_BODY.pack(flags, cx + args.bot_radius * math.cos(t),
                                    cy + args.bot_radius * math.sin(t), cz, -t % (2 * math.pi),
                                    cell) + NO_ANIM
        bot["state"] = state
        for other in clients.values():
            if other.alive:
                send(other, PEER, struct.pack("<I", BOT_ID) + state)

    def adopt_world(order, now):
        """Load what --world holds for this load order: the clock, deaths, objects, weather."""
        nonlocal clock
        if not args.world:
            return
        world["path"] = os.path.join(args.world, f"{order:08x}.json")
        saved = load_world(world["path"])
        if not saved:
            print(f"world {world['path']}: new", flush=True)
            return
        deaths.update({int(k): v for k, v in saved.get("deaths", {}).items()})
        objects.update({int(k): tuple(v) for k, v in saved.get("objects", {}).items()})
        spawns.update({int(k): v for k, v in saved.get("spawns", {}).items()})
        contents.update({int(k): v for k, v in saved.get("contents", {}).items()})
        world["next_spawn"] = max(world["next_spawn"], saved.get("next_spawn", 1))
        weather.update({int(k): v for k, v in saved.get("weather", {}).items()})
        statuses.update({int(k): tuple(v) for k, v in saved.get("statuses", {}).items()})
        if saved.get("clock") and args.hour is None:
            clock = Clock(*saved["clock"], now)
        print(f"world {world['path']}: {len(deaths)} deaths, {len(objects)} objects, "
              f"{len(spawns)} spawns, {len(contents)} containers, {len(weather)} regions, "
              f"{len(statuses)} statuses"
              + (f", clock {clock}" if clock else ""), flush=True)

    def write_world(now):
        state = {"deaths": {str(k): v for k, v in deaths.items()},
                 "objects": {str(k): list(v) for k, v in objects.items()},
                 "spawns": {str(k): {f: v.get(f, 0) for f in ("cell", "count", "removed", "pos",
                                                              "rot", "id", "origin", "token",
                                                              "data", "condition", "charge",
                                                              "leveled", "summon")}
                            for k, v in spawns.items()},
                 "next_spawn": world["next_spawn"],
                 "contents": {str(k): v for k, v in contents.items()},
                 "weather": {str(k): v for k, v in weather.items()},
                 "statuses": {str(k): list(v) for k, v in statuses.items()}}
        if clock:
            clock.advance(now)
            state["clock"] = [clock.hour, clock.day, clock.month, clock.year, clock.days_passed,
                              clock.scale]
        save_world(world["path"], state)
        world["dirty"], world["saved"] = False, now

    def leave(client):
        client.alive = False
        for other in clients.values():
            if other.alive:
                send(other, GONE, struct.pack("<I", client.id))

    def flush(client, now, resend=True):
        """Send the ack and the unacked events, or hold them until EVENTS_GAP has passed."""
        if now - client.rel.last_send < EVENTS_GAP:
            client.flush_due = True
            return
        client.flush_due = False
        send(client, EVENTS, client.rel.packet(now, resend or bool(client.rel.out)))

    def broadcast_event(origin, kind, data, now):
        for other in clients.values():
            if other.alive and other.id != origin:
                other.rel.queue(kind, origin, data)
                flush(other, now)

    def send_event(target, origin, kind, data, now):
        for other in clients.values():
            if other.alive and other.id == target:
                other.rel.queue(kind, origin, data)
                flush(other, now)

    def set_weather(origin, entries, stamp, now, to_origin=True):
        """Record the regions whose weather changes and send them to every client."""
        changed = {i: w for i, w in entries.items() if weather.get(i) != w}
        if not changed:
            return
        weather.update(changed)
        world["dirty"] = True
        print(f"{stamp} weather from client {origin}: {describe_weather(changed)}", flush=True)
        for other in clients.values():
            if other.alive and (to_origin or other.id != origin):
                for data in pack_weather(changed):
                    other.rel.queue(EVENT_WEATHER, origin, data)
                flush(other, now)

    def add_spawn(origin, spawn, stamp, now, token=0):
        """Name a reference made at run time and send it to every client, its maker too, which
        knows it as its own by cell, object and place. A repeat gets the id it already has, and
        goes to the maker only."""
        sid = spawn_twin(spawns, spawn, origin, token, now, deaths)
        if sid is not None:
            print(f"{stamp} client {origin} repeats {describe_spawn(sid, spawns[sid])}",
                  flush=True)
            send_event(origin, spawns[sid]["origin"], EVENT_SPAWN, pack_spawn(sid, spawns[sid]),
                       now)
            return sid
        for old, known in list(spawns.items()):  # a dead creature's placeholder rolled again
            if spawn.get("leveled") and known.get("leveled") == spawn["leveled"] and \
                    not known["removed"]:
                remove_spawn(origin, old, stamp, now, to_origin=True)
        sid = SPAWN_IDS | world["next_spawn"]
        world["next_spawn"] += 1
        spawns[sid] = dict(spawn, origin=origin, token=token, made=now, removed=False)
        world["dirty"] = True
        print(f"{stamp} client {origin} made {describe_spawn(sid, spawns[sid])}", flush=True)
        data = pack_spawn(sid, spawns[sid])
        for other in clients.values():
            if other.alive:
                other.rel.queue(EVENT_SPAWN, origin, data)
                flush(other, now)
        return sid

    def remove_spawn(origin, sid, stamp, now, to_origin=False):
        spawn = spawns.get(sid)
        if spawn is None or spawn["removed"]:
            return
        spawn["removed"] = True
        world["dirty"] = True
        print(f"{stamp} client {origin} removed {describe_spawn(sid, spawn)}", flush=True)
        if to_origin:
            send_event(origin, 0, EVENT_SPAWN, pack_spawn(sid, spawn), now)
        broadcast_event(origin, EVENT_SPAWN, pack_spawn(sid, spawn), now)

    def send_contents(target, refid, now, flags=0):
        box = contents[refid]
        for other in clients.values():
            if other.alive and other.id == target:
                for part in pack_contents(refid, box["cell"], box["entries"], flags):
                    other.rel.queue(EVENT_CONTENTS, box["origin"], part)
                flush(other, now)

    def set_contents(origin, refid, cell, entries, rolled, stamp, now):
        """Keep a container's contents and send them to the other clients. A console's first
        reading of a container the server already holds gets the server's contents back."""
        known = contents.get(refid)
        if rolled and known:
            if known["entries"] != entries:
                print(f"{stamp} client {origin} opened {refid:#010x}: keeps "
                      f"{describe_contents(known['entries'])}", flush=True)
                send_contents(origin, refid, now)
            return
        contents[refid] = {"cell": cell, "entries": entries, "origin": origin}
        world["dirty"] = True
        print(f"{stamp} client {origin} {'opened' if rolled else 'changed'} {refid:#010x} in cell "
              f"{cell}: {describe_contents(entries)}", flush=True)
        for other in clients.values():
            if other.alive and other.id != origin:
                send_contents(other.id, refid, now)

    def on_event(client, kind, data, stamp, now):
        client.events += 1
        if kind == EVENT_OFFER and len(data) > BULK_OFFER.size:
            ident, size, digest = BULK_OFFER.unpack_from(data)
            name = wire_text(data[BULK_OFFER.size:].split(b"\0", 1)[0])
            if client.upload and client.upload.stream:
                client.upload.stream.close()
            folder = (os.path.join(args.world, "uploads", fingerprint(client.key))
                      if args.world and client.key else None)
            client.upload = Incoming(folder, ident, size, digest, name, now)
            print(f"{stamp} client {client.id} offers {name} ({size} bytes, id {ident:#010x}): "
                  f"{BULK_STATUS[client.upload.status]}"
                  + (f" from chunk {client.upload.next}"
                     if client.upload.status == BULK_RECEIVING else ""), flush=True)
            send(client, BULK_ACK, client.upload.ack(now))
            return
        if kind == EVENT_CONTENTS and len(data) >= CONTENTS_HEAD.size:
            refid, cell, part, parts, flags, entries = unpack_contents(data)
            have = arriving.get(client.id)
            if part == 0:
                have = arriving[client.id] = (refid, [], 0)
            if not have or have[0] != refid or have[2] != part:
                return
            arriving[client.id] = (refid, have[1] + entries, part + 1)
            if part + 1 == parts:
                del arriving[client.id]
                set_contents(client.id, refid, cell, have[1] + entries,
                             bool(flags & CONTENTS_ROLLED), stamp, now)
            return
        if kind == EVENT_WANT and data:
            cells = set(struct.unpack_from(f"<{min(data[0], (len(data) - 1) // 2)}H", data, 1))
            wanted = [refid for refid, box in contents.items() if box["cell"] in cells]
            for refid in wanted:
                send_contents(client.id, refid, now)
            if wanted:
                print(f"{stamp} client {client.id} loads cells {sorted(cells)}: sent "
                      f"{len(wanted)} containers", flush=True)
            return
        if kind == EVENT_SPAWN and len(data) > SPAWN.size:
            token, spawn = unpack_spawn(data)
            if placeable(*spawn["pos"]) and finite(*spawn["rot"]):
                add_spawn(client.id, spawn, stamp, now, token)
            return
        if kind == EVENT_REMOVE and data:
            for sid in unpack_removes(data):
                remove_spawn(client.id, sid, stamp, now)
            return
        if kind == EVENT_WEATHER and len(data) >= 2:
            flags, entries = unpack_weather(data)
            if flags & WEATHER_OFFER:
                entries = {i: w for i, w in entries.items() if i not in weather}
            set_weather(client.id, entries, stamp, now, not flags & WEATHER_OFFER)
            return
        if kind == EVENT_SPELL and len(data) > SPELL.size:
            caster, target, refid, _, player = SPELL.unpack_from(data)
            name = wire_text(data[SPELL.size:].split(b"\0")[0])
            who = f"client {client.id}" if player else f"{caster:#010x} of client {client.id}"
            on = (f"{refid:#010x} (authority {target})" if refid
                  else f"the player of client {target}")
            print(f"{stamp} {who} casts {name} on {on}", flush=True)
            if target != BOT_ID:
                send_event(target, client.id, kind, data, now)
            return
        if kind == EVENT_CAST and len(data) > SPELL.size:
            caster, target, refid, _, player = SPELL.unpack_from(data)
            name = wire_text(data[SPELL.size:].split(b"\0")[0])
            who = f"client {client.id}" if player else f"{caster:#010x} of client {client.id}"
            at = (f" at {refid:#010x} (client {target})" if refid
                  else f" at the player of client {target}" if target else "")
            print(f"{stamp} {who} casts {name}{at}", flush=True)
        if kind == EVENT_SHOT and len(data) > SHOT.size:
            firer, swing, other, player = SHOT.unpack_from(data)
            name = wire_text(data[SHOT.size:].split(b"\0")[0])
            who = f"client {client.id}" if player else f"{firer:#010x} of client {client.id}"
            print(f"{stamp} {who} shoots {name} ({swing:.2f}, {other:.2f})", flush=True)
        if kind in TARGETED and len(data) >= 12:
            refid, target = struct.unpack_from("<II", data)
            word = (f"damage {struct.unpack_from('<f', data, 8)[0]:.0f}"
                    if kind in (EVENT_HIT, EVENT_PLAYER_HIT)
                    else f"{struct.unpack_from('<I', data, 8)[0]}")
            if kind in (EVENT_HIT, EVENT_PLAYER_HIT) and len(data) >= 16:
                word += f", fatigue {struct.unpack_from('<f', data, 12)[0]:.0f}"
            if kind == EVENT_PLAYER_HIT:
                by = f" (attacker {refid:#010x})" if refid else ""
                print(f"{stamp} client {client.id} {TARGETED[kind]} client {target}{by}: {word}",
                      flush=True)
            else:
                print(f"{stamp} client {client.id} {TARGETED[kind]} {refid:#010x} "
                      f"(authority {target}): {word}", flush=True)
            if target != BOT_ID:
                send_event(target, client.id, kind, data, now)
            elif kind == EVENT_HOLD and struct.unpack_from("<I", data, 8)[0] and \
                    args.bot_break_hold is not None:
                bot["breaks"].append((now + args.bot_break_hold, client.id, refid))
            return
        if kind == EVENT_DEATH and len(data) >= 4:
            refid = struct.unpack_from("<I", data)[0]
            if refid in deaths:
                return
            deaths[refid] = client.id
            world["dirty"] = True
            print(f"{stamp} client {client.id}: {refid:#010x} died", flush=True)
        if kind == EVENT_STATUS and len(data) >= STATUS.size:
            refid, *values = STATUS.unpack_from(data)
            statuses[refid] = tuple(values)
            world["dirty"] = True
            print(f"{stamp} client {client.id}: {describe_status(refid, values)}", flush=True)
        if kind == EVENT_AFFECT and len(data) > 5:
            refid, index = struct.unpack_from("<IB", data)
            name = wire_text(data[5:].split(b"\0")[0])
            print(f"{stamp} client {client.id}: {refid:#010x} takes effect {index} of {name}",
                  flush=True)
        if kind == EVENT_OBJECTS and data:
            changed = unpack_objects(data)
            objects.update(changed)
            world["dirty"] = True
            for refid, rest in changed.items():
                print(f"{stamp} client {client.id}: {describe_object(refid, *rest)}", flush=True)
        if kind == EVENT_TEXT:
            print(f"{stamp} client {client.id} says: {wire_text(data)}", flush=True)
        if kind == EVENT_EQUIPMENT and len(data) >= 2:
            sets = equipment.setdefault(client.id, [[], []])
            if data[0] == 0:
                sets[1] = []
            sets[1].append(data)
            if data[0] + 1 == data[1]:
                sets[0], sets[1] = sets[1], []
                items = [i for part in sets[0] for i in unpack_equipment(part)]
                print(f"{stamp} client {client.id} wears {len(items)}: {', '.join(items)}",
                      flush=True)
        broadcast_event(client.id, kind, data, now)

    def update_authority(now):
        """Name each loaded cell's authority and tell every client that has the cell loaded."""
        candidates, standing = {}, set()
        for client in sorted(clients.values(), key=lambda c: c.id):
            client.loaded = set()
            if client.alive and client.state:
                own, client.loaded = cell_keys(client.state)
                standing.add(own)
                for key in client.loaded:
                    candidates.setdefault(key, []).append((client.id, key == own))
        forced = None
        if bot["state"] and window(args.bot_owns, now):
            own, loaded = cell_keys(bot["state"])
            for key in loaded:
                candidates.setdefault(key, []).append((BOT_ID, key == own))
            forced = BOT_ID
        new = assign_authority(owners, candidates, forced)
        stamp = time.strftime("%H:%M:%S")
        for key in sorted(standing & set(new), key=describe_key):  # the rest only follow
            if owners.get(key) != new[key]:
                print(f"{stamp} authority {describe_key(key)}: client {new[key]}", flush=True)
        owners.clear()
        owners.update(new)
        for client in clients.values():
            told = False
            for key in client.loaded:
                if key in new and client.known.get(key) != new[key]:
                    client.known[key] = new[key]
                    client.rel.queue(EVENT_AUTHORITY, 0, KEY.pack(*key) + struct.pack("<I", new[key]))
                    told = True
            for key in [k for k in client.known if k not in client.loaded]:
                del client.known[key]
            if told:
                flush(client, now)

    def on_actors(client, body):
        """Keep an authority's actor states and relay them to the other clients."""
        count = struct.unpack_from("<I", body)[0]
        own = cell_keys(client.state)[0] if client.state else None
        kept = []  # records with a finite position and statistics, the only ones relayed
        for i in range(min(count, (len(body) - 4) // ACTOR.size)):
            record = body[4 + i * ACTOR.size:4 + (i + 1) * ACTOR.size]
            values = ACTOR.unpack(record)
            if not placeable(*values[1:5]) or not finite(values[5], *values[7:9]):
                continue
            refid, x, y = values[:3]
            key = own if own and own[0] == KEY_INTERIOR else (
                KEY_EXTERIOR, math.floor(x / CELL_UNITS), math.floor(y / CELL_UNITS), b"")
            actors[refid] = (client.id, key, record)
            if refid == args.bot_mirror:
                bot["mirror"] = record
            client.actor_states += 1
            kept.append(record)
        if not kept:
            return
        body = struct.pack("<I", len(kept)) + b"".join(kept)
        for other in clients.values():
            if other is not client and other.alive:
                send(other, ACTORS, struct.pack("<I", client.id) + body)

    def bot_actors(now):
        """As the authority, the bot places each actor of its cells bot_shift units east of where
        the last authority left it, swaying east and west by bot_sway once per bot_period."""
        owned = [record for _, key, record in actors.values() if owners.get(key) == BOT_ID]
        phase = (now - bot["start"]) * 2 * math.pi / args.bot_period
        sway = args.bot_sway * math.sin(phase)
        facing = math.pi / 2 if math.cos(phase) >= 0 else 3 * math.pi / 2
        for i in range(0, len(owned), ACTORS_PER_PACKET):
            chunk = owned[i:i + ACTORS_PER_PACKET]
            body = struct.pack("<I", len(chunk))
            for record in chunk:
                refid, x, y, z, heading, health, flags, magicka, fatigue, anim = \
                    ACTOR.unpack(record)
                if args.bot_sway:
                    heading = facing
                if args.bot_stats:
                    health, magicka, fatigue = (float(v) for v in args.bot_stats.split(","))
                body += ACTOR.pack(refid, x + args.bot_shift + sway, y, z, heading, health, flags,
                                   magicka, fatigue, anim)
            for other in clients.values():
                if other.alive:
                    send(other, ACTORS, struct.pack("<I", BOT_ID) + body)

    def handshake(kind, session, packet, addr, now):
        """Answer HANDSHAKE1 with HANDSHAKE2; on HANDSHAKE3, the HELLO it carries, the console's
        key, the session keys and whether this HANDSHAKE3 came before."""
        for stale in [k for k, v in pending.items() if now - v["time"] > HANDSHAKE_KEEP]:
            del pending[stale]
        entry = pending.get(session)
        if kind == HANDSHAKE1:
            e = packet[OUTER.size:OUTER.size + 32]
            if len(packet) < HANDSHAKE_PAD or entry and entry["e"] != e:
                return None
            if entry is None:
                noise = Noise(False, server_secret, os.urandom(32), PROLOGUE)
                noise.read1(e)
                entry = pending[session] = {
                    "noise": noise, "e": e, "time": now, "done": None,
                    "reply": OUTER.pack(b"T3MP", T3MP_VERSION, HANDSHAKE2, 0, session, 0)
                    + noise.write2()}
            transmit(addr, entry["reply"])
            return None
        if kind != HANDSHAKE3 or entry is None:
            return None
        message = packet[OUTER.size:]
        if entry["done"] is None:
            try:
                hello = entry["noise"].read3(message)
            except ValueError:
                return None
            entry["done"] = (message, hello, entry["noise"].rs, entry["noise"].split())
            return entry["done"][1:] + (False,)
        if entry["done"][0] != message:
            return None
        return entry["done"][1:] + (True,)

    def guarded(packet, addr):
        """handle, with a packet that breaks a parser dropped and logged instead of ending the
        server: the parsers check lengths and ranges, this is the second line."""
        try:
            handle(packet, addr)
        except (struct.error, ValueError, IndexError, KeyError, TypeError, OverflowError) as error:
            where = traceback.extract_tb(error.__traceback__)[-1]
            print(f"{time.strftime('%H:%M:%S')} malformed packet from {addr[0]}: "
                  f"{type(error).__name__} in {where.name}: {error}", flush=True)

    def handle(packet, addr):
        """Take a handshake message or open a sealed packet, then hand it on in the T3MP
        layout. Anything else is dropped unread."""
        if len(packet) < OUTER.size or dropped("in"):
            return
        magic, version, kind, _, session, seq = OUTER.unpack_from(packet)
        if magic != b"T3MP" or version != T3MP_VERSION:
            return
        now = time.time()
        if kind in (HANDSHAKE1, HANDSHAKE3):
            if kind == HANDSHAKE1 and session not in pending:
                if len(handshake_buckets) > HANDSHAKES_PENDING:  # forged sources, most likely
                    handshake_buckets.clear()
                source = handshake_buckets.setdefault(addr[0], Bucket(*HANDSHAKE_RATE))
                if (len(pending) >= HANDSHAKES_PENDING or not source.take(now)
                        or not handshake_bucket.take(now)):
                    limits["handshakes"] += 1
                    return
            done = handshake(kind, session, packet, addr, now)
            if done:
                hello, key, keys, again = done
                client = by_session.get(session)
                if again and client is not None and client.keys == keys:
                    # The WELCOME was lost, or this is a replay: answer the address that joined.
                    send(client, WELCOME, struct.pack("<I", client.id))
                    return
                handle_plain(T3MP.pack(b"T3MP", T3MP_VERSION, HELLO, 0, session, 0, 0, 0, 0)
                             + hello, addr, (key, keys))
            return
        client = by_session.get(session)
        if kind != SEALED or client is None or client.keys is None or \
                len(packet) < OUTER.size + INNER.size + NOISE_TAG:
            return
        top, seen = client.replay
        if not seq or seq <= top and (top - seq >= 32 or seen >> (top - seq) & 1):
            client.replayed += 1
            return
        inner = unseal(client.keys[0], seq, packet[:OUTER.size], packet[OUTER.size:])
        if inner is None:
            client.forged += 1
            return
        if not client.bucket.take(now):
            client.limited += 1
            return
        if seq > top:
            client.replay = (seq, (seen << min(seq - top, 32) | 1) & 0xFFFFFFFF)
        else:
            client.replay = (top, seen | 1 << (top - seq))
        inner_kind, ack, sent, echo = INNER.unpack_from(inner)
        handle_plain(T3MP.pack(b"T3MP", T3MP_VERSION, inner_kind, 0, session, seq, ack, sent,
                               echo) + inner[INNER.size:], addr)

    def refuse(addr, session, keys, mac, reason):
        stranger = Client(0, mac)
        stranger.addr, stranger.session, stranger.keys = addr, session, keys
        order, plugins = pinned or (0, 0)
        send(stranger, REFUSE, REFUSE_BODY.pack(order, plugins or 0, reason))

    def handle_plain(packet, addr, secure=None):
        nonlocal pinned, clock
        magic, version, kind, _, session, seq, _, sent, _ = T3MP.unpack_from(packet)
        stamp = time.strftime("%H:%M:%S")
        now = time.time()
        if kind == HELLO and secure and len(packet) >= T3MP.size + HELLO_BODY.size:
            key, keys = secure
            mac, build, order, plugins, *offered = HELLO_BODY.unpack_from(packet, T3MP.size)
            mac = mac.hex(":")
            if password and key not in admitted:
                if len(password_buckets) > HANDSHAKES_PENDING:
                    password_buckets.clear()
                tries = password_buckets.setdefault(addr[0], Bucket(*PASSWORD_RATE))
                given = packet[T3MP.size + HELLO_BODY.size:]
                if not tries.take(now) or not hmac.compare_digest(given, password):
                    print(f"{stamp} refused {mac} at {addr[0]}: wrong password", flush=True)
                    refuse(addr, session, keys, mac, REFUSED_PASSWORD)
                    return
                admitted.add(key)
                if admitted_path:
                    with open(admitted_path, "a", encoding="utf-8") as stream:
                        stream.write(f"{key.hex()} {mac} {time.strftime('%Y-%m-%d')}\n")
                print(f"{stamp} admitted key {fingerprint(key)} ({mac})", flush=True)
            if pinned is None:
                pinned = (order, plugins)
                print(f"{stamp} load order {order:#010x} ({plugins} plugins) set by {mac}",
                      flush=True)
                adopt_world(order, now)
            if order != pinned[0]:
                print(f"{stamp} refused {mac}: load order {order:#010x} ({plugins} plugins), "
                      f"session has {pinned[0]:#010x}", flush=True)
                refuse(addr, session, keys, mac, REFUSED_LOAD_ORDER)
                return
            # A console is known by its key for this server; the MAC is only a hint.
            client = clients.get(key)
            playing = sum(c.alive for c in clients.values() if c is not client)
            if playing >= args.max_players:
                print(f"{stamp} refused {mac}: {playing} players, the most allowed", flush=True)
                refuse(addr, session, keys, mac, REFUSED_FULL)
                return
            if client is None:
                client = clients[key] = Client(len(clients) + 1, mac)
                client.key = key
                print(f"{stamp} client {client.id} is key {fingerprint(key)}", flush=True)
            client.mac = mac
            by_session.pop(client.session, None)
            client.session = session
            by_session[client.session] = client
            if client.keys != keys:  # a resent HANDSHAKE3 keeps the replay window
                client.keys, client.replay = keys, (0, 0)
            client.addr, client.peer_seq, client.peer_time = addr, seq, sent
            client.joins += 1
            client.alive, client.last = True, now
            if client.rel.out:
                print(f"{stamp} client {client.id}: {len(client.rel.out)} unacked events "
                      f"dropped by the rejoin", flush=True)
            client.rel = Reliable()
            client.known = {}
            if client.joins == 1:
                client.joined = now
                client.bursts = sorted(bursts)
            verb = "joined" if client.joins == 1 else "rejoined"
            print(f"{stamp} client {client.id} {verb}: {mac} at {addr[0]}:{addr[1]}, "
                  f"build {build:#010x}", flush=True)
            send(client, WELCOME, struct.pack("<I", client.id))
            if clock is None:
                offered = sane_clock(offered)
                if args.hour is not None:
                    offered[0] = args.hour
                if args.timescale is not None:
                    offered[5] = args.timescale
                clock = Clock(*offered, now)
                print(f"{stamp} clock {clock}, from client {client.id}", flush=True)
            send(client, CLOCK, clock.body(now))
            for refid, origin in deaths.items():
                client.rel.queue(EVENT_DEATH, origin, struct.pack("<I", refid))
            for data in pack_objects(objects):
                client.rel.queue(EVENT_OBJECTS, 0, data)
            for refid, values in statuses.items():
                client.rel.queue(EVENT_STATUS, 0, STATUS.pack(refid, *values))
            for sid, spawn in sorted(spawns.items(), key=lambda s: s[1]["removed"]):
                client.rel.queue(EVENT_SPAWN, spawn["origin"] if spawn.get("summon") else 0,
                                 pack_spawn(sid, spawn))
            for origin, (parts, _) in equipment.items():
                if origin != client.id:
                    for part in parts:
                        client.rel.queue(EVENT_EQUIPMENT, origin, part)
            for data in pack_weather(weather):
                client.rel.queue(EVENT_WEATHER, 0, data)
            if sending and (client.bulk is None or client.bulk.status != 3):
                client.bulk = Outgoing(*sending)
                client.rel.queue(EVENT_OFFER, 0, client.bulk.offer())
                print(f"{stamp} offering {sending[0]} ({len(sending[1])} bytes, id "
                      f"{client.bulk.id:#010x}) to client {client.id}", flush=True)
            if client.rel.out:
                flush(client, now)
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
        elif kind == STATE and len(packet) >= T3MP.size + STATE_SIZE:
            if not placeable(*STATE_BODY.unpack_from(packet, T3MP.size)[1:5]):
                return
            client.state = packet[T3MP.size:T3MP.size + STATE_SIZE]
            client.states += 1
            if args.bot:
                bot_anchor(client.state)
                if args.bot_echo:
                    bot["echo"] = client.state
            for other in clients.values():
                if other is not client and other.alive:
                    send(other, PEER, struct.pack("<I", client.id) + client.state)
        elif kind == ACTORS and len(packet) >= T3MP.size + 4:
            on_actors(client, packet[T3MP.size:])
        elif kind == BULK_ACK and client.bulk and len(packet) >= T3MP.size + BULK_ACK_BODY.size:
            bulk = client.bulk
            first = bulk.first is None
            status = bulk.on_ack(packet[T3MP.size:], now)
            if first and bulk.first is not None:
                print(f"{stamp} client {client.id} takes {bulk.name} from chunk {bulk.first} "
                      f"of {bulk.chunks}", flush=True)
            if status is not None and status != BULK_RECEIVING:
                took = now - bulk.started
                size = len(bulk.data) - min(bulk.first, bulk.chunks) * BULK_CHUNK
                print(f"{stamp} client {client.id} {bulk.name}: "
                      f"{BULK_STATUS[status] if status < len(BULK_STATUS) else status}, "
                      f"{max(size, 0)} bytes in {took:.1f} s "
                      f"({max(size, 0) / 1024 / max(took, 0.001):.0f} KB/s), "
                      f"chunks sent {bulk.sent}, resent {bulk.resent} ({bulk.fast} on a gap), "
                      f"probes {bulk.probes}",
                      flush=True)
        elif kind == CHUNK and client.upload and len(packet) >= T3MP.size + 8:
            upload = client.upload
            ident, index = struct.unpack_from("<II", packet, T3MP.size)
            if ident != upload.id:
                return
            receiving = upload.status == BULK_RECEIVING
            if upload.on_chunk(index, packet[T3MP.size + 8:]):
                send(client, BULK_ACK, upload.ack(now))
            if receiving and upload.status != BULK_RECEIVING:
                took = max(now - upload.started, 0.001)
                size = upload.size - upload.first * BULK_CHUNK
                print(f"{stamp} client {client.id} sent {upload.name}: "
                      f"{BULK_STATUS[upload.status]}, {size} bytes in {took:.1f} s "
                      f"({size / 1024 / took:.0f} KB/s)", flush=True)
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

    if pinned:
        adopt_world(pinned[0], time.time())
    while deadline is None or time.time() < deadline:
        waiting = [sock] + [link.sock for link in links] + ([dns] if dns else [])
        wait = 0.25
        if any(c.queue for c in clients.values()):
            wait = PACE_WINDOW
        elif any(c.flush_due for c in clients.values()):
            wait = EVENTS_GAP
        elif any(c.bulk and c.bulk.status == BULK_RECEIVING for c in clients.values()):
            wait = 0.05
        for ready in select.select(waiting, [], [], wait)[0]:
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
                guarded(data, addr)
                continue
            link = next(link for link in links if link.sock is ready)
            frame = link.recv(0)
            if frame and frame[12:14] == b"\x08\x06" and len(frame) >= 42:
                op, sha, spa, _, tpa = struct.unpack_from(">H6s4s6s4s", frame, 20)
                if op == 1 and tpa == socket.inet_aton(PEER_IP):
                    link.send(arp_frame(2, sha, sha, socket.inet_ntoa(spa)))
            elif frame:
                src = socket.inet_ntoa(frame[26:30])
                request = udp_from_frame(frame, DHCP_SERVER)
                reply = dhcp_reply(request, args.dhcp_lease) if request else None
                if reply:
                    print(f"{time.strftime('%H:%M:%S')} dhcp "
                          f"{'offer' if reply[242] == 2 else 'ack'} {GUEST_IP} to "
                          f"{frame[6:12].hex(':')}", flush=True)
                    link.send(udp_frame(frame[6:12], "255.255.255.255", reply,
                                        sport=DHCP_SERVER, dport=DHCP_CLIENT))
                    continue
                query = udp_from_frame(frame, DNS_PORT) if hosts else None
                reply = dns_reply(query, hosts) if query else None
                if reply:
                    print(f"{time.strftime('%H:%M:%S')} dns query from {src}: "
                          f"{'answered' if reply[7] else 'unknown name'}", flush=True)
                    link.send(udp_frame(frame[6:12], src, reply, sport=DNS_PORT))
                    continue
                data = udp_from_frame(frame)
                if data:
                    guarded(data, (src, PORT, frame[6:12], link))
        now = time.time()
        for client in clients.values():
            if client.queue:
                pump(client)
            if client.alive and now - client.last > TIMEOUT:
                print(f"{time.strftime('%H:%M:%S')} client {client.id} timed out", flush=True)
                leave(client)
        if world["path"] and now >= world["saved"] + (10 if world["dirty"] else 60):
            write_world(now)
        if args.bot and bot["anchor"] and now >= bot["next"]:
            bot["next"] = now + 1 / args.bot_rate
            bot_step(now)
        if now >= authority_next:
            authority_next = now + AUTHORITY_PERIOD
            update_authority(now)
        if args.bot and now >= actor_next:
            actor_next = now + ACTOR_PERIOD
            bot_actors(now)
        for due, holder, refid in [b for b in bot["breaks"] if now >= b[0]]:
            bot["breaks"].remove((due, holder, refid))
            print(f"{time.strftime('%H:%M:%S')} bot breaks client {holder}'s hold on "
                  f"{refid:#010x}", flush=True)
            send_event(holder, BOT_ID, EVENT_HOLD_BROKEN, struct.pack("<III", refid, holder, 2),
                       now)
        if args.bot_hold:
            refid, _, span = args.bot_hold.partition("@")
            refid = int(refid, 16)
            want = window(span, now)
            owner = owners.get(actors[refid][1]) if refid in actors else None
            if want != bot["held"] and owner:
                bot["held"] = want
                print(f"{time.strftime('%H:%M:%S')} bot {'holds' if want else 'releases'} "
                      f"{refid:#010x} (authority {owner})", flush=True)
                send_event(owner, BOT_ID, EVENT_HOLD, struct.pack("<III", refid, owner, want), now)
        if args.bot_kill and not bot["killed"]:
            refid, _, at = args.bot_kill.partition("@")
            refid = int(refid, 16)
            if window(at, now):
                bot["killed"] = True
                deaths[refid] = BOT_ID
                print(f"{time.strftime('%H:%M:%S')} bot kills {refid:#010x}", flush=True)
                broadcast_event(BOT_ID, EVENT_DEATH, struct.pack("<I", refid), now)
        for spec in [b for b in bot_statuses if window(b.partition("@")[2], now)]:
            bot_statuses.remove(spec)
            refid, _, values = spec.partition("@")[0].partition(":")
            refid, values = int(refid, 16), tuple(int(v) for v in values.split(","))
            statuses[refid] = values
            print(f"{time.strftime('%H:%M:%S')} bot sets {describe_status(refid, values)}",
                  flush=True)
            broadcast_event(BOT_ID, EVENT_STATUS, STATUS.pack(refid, *values), now)
        for spec in [b for b in bot_affects if window(b.rpartition("@")[2], now)]:
            bot_affects.remove(spec)
            refid, index, name = spec.rpartition("@")[0].split(":", 2)
            print(f"{time.strftime('%H:%M:%S')} bot gives {refid} effect {index} of {name}",
                  flush=True)
            broadcast_event(BOT_ID, EVENT_AFFECT, struct.pack("<IB", int(refid, 16), int(index))
                            + name.encode("latin-1") + b"\0", now)
        if args.bot_hit and not bot["hit"]:
            refid, _, at = args.bot_hit.partition("@")
            refid = int(refid, 16)
            owner = owners.get(actors[refid][1]) if refid in actors else None
            if owner and window(at, now):
                bot["hit"] = True
                print(f"{time.strftime('%H:%M:%S')} bot hits {refid:#010x} for 5 (authority "
                      f"{owner})", flush=True)
                send_event(owner, BOT_ID, EVENT_HIT, struct.pack("<IIf", refid, owner, 5.0), now)
        for due, index, value in [w for w in bot_weather if window(f"{w[0]}:", now)]:
            bot_weather.remove((due, index, value))
            set_weather(BOT_ID, {index: value}, time.strftime("%H:%M:%S"), now)
        if args.bot_hit_player and not bot["hit_player"]:
            damage, _, at = args.bot_hit_player.partition("@")
            damage, _, fatigue = damage.partition(":")
            if window(at, now):
                bot["hit_player"] = True
                for other in [c for c in clients.values() if c.alive]:
                    print(f"{time.strftime('%H:%M:%S')} bot hits client {other.id} for {damage}"
                          f" health, {fatigue or 0} fatigue", flush=True)
                    send_event(other.id, BOT_ID, EVENT_PLAYER_HIT,
                               struct.pack("<IIff", 0, other.id, float(damage),
                                           float(fatigue or 0)), now)
        for spell in [s for s in bot_spells if window(f"{s[0]}:", now)]:
            _, kind, name, refid = spell
            if refid:
                target = owners.get(actors[refid][1]) if refid in actors else None
            else:
                target = next((c.id for c in clients.values() if c.alive), None)
            if not target or target == BOT_ID:
                continue
            bot_spells.remove(spell)
            on = f"{refid:#010x}" if refid else "the player"
            verb = "casts" if kind == EVENT_SPELL else "is seen casting"
            print(f"{time.strftime('%H:%M:%S')} bot {verb} {name} on {on} of client {target}",
                  flush=True)
            data = SPELL.pack(0, target, refid, SOURCE_SPELL, 1) + zstr(name)
            if kind == EVENT_SPELL:
                send_event(target, BOT_ID, kind, data, now)
            else:
                broadcast_event(BOT_ID, kind, data, now)
        for spec in [s for s in bot_spawns if window(f"{s[0]}:", now)]:
            bot_spawns.remove(spec)
            _, x, y, z = bot["anchor"]
            add_spawn(BOT_ID, {"cell": spec[2], "count": 1, "pos": [x + 64, y, z],
                               "rot": [0.0, 0.0, 0.0], "id": spec[1],
                               "data": spec[3] is not None, "condition": spec[3] or 0,
                               "charge": 0},
                      time.strftime("%H:%M:%S"), now)
        for box in [b for b in bot_boxes if window(f"{b[0]}:", now)]:
            bot_boxes.remove(box)
            set_contents(BOT_ID, box[1], box[2], box[3], False, time.strftime("%H:%M:%S"), now)
        for at in [t for t in bot_takes if window(f"{t}:", now)]:
            bot_takes.remove(at)
            for sid in [s for s, v in spawns.items() if v["origin"] != BOT_ID]:
                remove_spawn(BOT_ID, sid, time.strftime("%H:%M:%S"), now)
        for shot in [s for s in bot_shots if window(f"{s[0]}:", now)]:
            bot_shots.remove(shot)
            print(f"{time.strftime('%H:%M:%S')} bot shoots {shot[1]}", flush=True)
            broadcast_event(BOT_ID, EVENT_SHOT, SHOT.pack(0, 1.0, 0.0, 1) + zstr(shot[1]), now)
        if args.bot_say and bot["anchor"] and now >= bot["said"] + args.bot_say:
            bot["said"] = now
            bot["line"] += 1
            broadcast_event(BOT_ID, EVENT_TEXT, b"bot %d" % bot["line"], now)
        for client in [c for c in clients.values() if c.alive and c.bursts]:
            if now - client.joined >= client.bursts[0][0]:
                _, count = client.bursts.pop(0)
                print(f"{time.strftime('%H:%M:%S')} burst of {count} heartbeats to client "
                      f"{client.id}", flush=True)
                for _ in range(count):
                    send(client, HEARTBEAT)
                    client.queue, queued = [], client.queue
                    for addr, packet, seq in queued:
                        transmit(addr, packet, seq)
        for client in [c for c in clients.values() if c.alive and c.bulk]:
            for index in client.bulk.due(now):
                send(client, CHUNK, client.bulk.chunk(index))
        for client in [c for c in clients.values() if c.alive and c.upload]:
            if client.upload.status == BULK_RECEIVING and \
                    now - client.upload.acked >= BULK_ACK_EVERY:
                send(client, BULK_ACK, client.upload.ack(now))
        for client in clients.values():
            if client.alive and (client.flush_due or client.rel.out and
                                 now - client.rel.last_send >= RESEND):
                flush(client, now)
        if clock and now >= clock_next:
            clock_next = now + CLOCK_INTERVAL
            body = clock.body(now)
            for client in clients.values():
                if client.alive:
                    send(client, CLOCK, body)
        if args.report and now >= report:
            report = now + args.report
            if clock:
                clock.advance(now)
                print(f"  clock {clock}", flush=True)
            if weather:
                print(f"  weather: {describe_weather(weather)}", flush=True)
            if limits["handshakes"]:
                print(f"  handshakes refused over rate: {limits['handshakes']}", flush=True)
            for client in clients.values():
                print(f"  client {client.id}: {'up' if client.alive else 'down'}, "
                      + summary(client), flush=True)
            if objects:
                print(f"  objects: {len(objects)} changed", flush=True)
            if spawns:
                live = sum(not s["removed"] for s in spawns.values())
                print(f"  spawns: {live} live, {len(spawns) - live} removed", flush=True)
            if actors:
                print(f"  actors: {len(actors)} known; authorities "
                      + ", ".join(f"{describe_key(k)} {c}" for k, c in sorted(
                          owners.items(), key=lambda i: describe_key(i[0]))), flush=True)
    if world["path"]:
        write_world(time.time())
    for client in clients.values():
        print(f"client {client.id} {client.mac}: " + summary(client, "last "))
    if args.drop:
        print(f"dropped {lost['in']} packets in, {lost['out']} out")
    return 0 if clients else 1


class FuzzClient:
    """A console's session in Python: the handshake, then sealed packets of any content."""

    def __init__(self, sock, addr, rng):
        self.sock, self.addr, self.rng = sock, addr, rng
        self.session = rng.getrandbits(32) | 1
        self.seq = self.peer_seq = 0
        self.keys = self.handshake3 = None
        self.event_next = 1  # the next event number the server will deliver, from its acks
        self.refused = None  # a REFUSE's body

    def join(self, timeout=2.0, password=b""):
        secret, e = self.rng.randbytes(32), self.rng.randbytes(32)
        noise = Noise(True, secret, e, PROLOGUE)
        message1 = (OUTER.pack(b"T3MP", T3MP_VERSION, HANDSHAKE1, 0, self.session, 0)
                    + noise.write1()).ljust(HANDSHAKE_PAD, b"\0")
        self.sock.sendto(message1, self.addr)
        reply = self.receive_raw(timeout, HANDSHAKE2)
        noise.read2(reply[OUTER.size:])
        hello = HELLO_BODY.pack(self.rng.randbytes(6), 0, 0x46555A5A, 3, 12.0, 16.0, 7.0, 427.0,
                                1.0, 30.0)
        self.handshake3 = (OUTER.pack(b"T3MP", T3MP_VERSION, HANDSHAKE3, 0, self.session, 0)
                           + noise.write3(hello + password))
        self.sock.sendto(self.handshake3, self.addr)
        self.keys = noise.split()
        if self.receive(timeout, WELCOME) is None:
            raise RuntimeError("no WELCOME")

    def receive_raw(self, timeout, kind):
        end = time.time() + timeout
        while time.time() < end:
            self.sock.settimeout(max(end - time.time(), 0.01))
            try:
                data, _ = self.sock.recvfrom(4096)
            except (socket.timeout, ConnectionResetError):
                continue
            if len(data) >= OUTER.size and data[5] == kind:
                return data
        raise RuntimeError(f"no reply of type {kind}")

    def receive(self, timeout, kind):
        """The body of the next sealed packet of this kind, or None."""
        end = time.time() + timeout
        while time.time() < end:
            try:
                data = self.receive_raw(end - time.time(), SEALED)
            except RuntimeError:
                return None
            seq = OUTER.unpack_from(data)[5]
            inner = unseal(self.keys[1], seq, data[:OUTER.size], data[OUTER.size:])
            if inner is not None:
                self.peer_seq = max(self.peer_seq, seq)
                if inner[0] == REFUSE:
                    self.refused = inner[INNER.size:]
                if inner[0] == EVENTS and len(inner) >= INNER.size + 4:
                    self.event_next = struct.unpack_from("<I", inner, INNER.size)[0] + 1
                if inner[0] == kind:
                    return inner[INNER.size:]
        return None

    def send(self, kind, body):
        self.seq += 1
        outer = OUTER.pack(b"T3MP", T3MP_VERSION, SEALED, 0, self.session, self.seq)
        inner = INNER.pack(kind, self.peer_seq, now_us(), 0) + body
        self.sock.sendto(outer + seal(self.keys[0], self.seq, outer, inner), self.addr)


def fuzz_body(rng, event_next):
    """A packet kind and body: random, or a well-formed frame around random contents. Events are
    numbered from event_next, the server's next, so most are delivered."""
    kinds = [HELLO, WELCOME, HEARTBEAT, STATE, PEER, GONE, EVENTS, REFUSE, CLOCK, ACTORS, CHUNK,
             BULK_ACK, 0, 255]
    kind = EVENTS if rng.random() < 0.6 else rng.choice(kinds)
    shape = rng.random()
    if kind == EVENTS and shape < 0.7:
        events, body = [], b""
        for _ in range(rng.randrange(0, 6)):
            event_kind = rng.choice(list(range(0, 21)) + [EVENT_OFFER, 65535])
            data = rng.randbytes(rng.choice((0, 1, 2, 4, 8, rng.randrange(0, EVENT_DATA + 1))))
            events.append((event_kind, data))
        body = EVENTS_HEAD.pack(rng.getrandbits(32) if rng.random() < 0.1 else 0, len(events))
        for n, (event_kind, data) in enumerate(events):
            length = len(data) if rng.random() < 0.9 else rng.getrandbits(16)
            body += EVENT.pack(event_next + n, event_kind, length, 0) + data
        return kind, body
    if kind == ACTORS and shape < 0.7:
        count = rng.randrange(0, 12)
        return kind, struct.pack("<I", rng.getrandbits(32)) + b"".join(
            ACTOR.pack(rng.getrandbits(32), *(struct.unpack("<5f", rng.randbytes(20))),
                       rng.getrandbits(32), *(struct.unpack("<2f", rng.randbytes(8))),
                       rng.randbytes(ANIM_BYTES)) for _ in range(count))
    if kind == STATE and shape < 0.7:
        return kind, rng.randbytes(STATE_SIZE)
    return kind, rng.randbytes(rng.choice((0, 1, 3, 4, 8, 20, 64, rng.randrange(0, 1400))))


def fuzz(args):
    """Join a server as a console, then send it mutated packets; exit 1 if it stops answering."""
    rng = random.Random(args.seed)
    host, _, port = args.address.partition(":")
    addr = (host, int(port or PORT))
    sock = udp_socket()
    client = FuzzClient(sock, addr, rng)
    client.join(password=args.password.encode("ascii"))
    print(f"joined {args.address} as session {client.session:#010x}", flush=True)
    for n in range(1, args.count + 1):
        time.sleep(1 / args.rate)  # under the server's per-client limit, which would drop the rest
        roll = rng.random()
        if roll < 0.1:  # before any handshake: garbage with a plausible header
            kind = rng.choice((HANDSHAKE1, HANDSHAKE2, HANDSHAKE3, SEALED, 0, 7))
            sock.sendto(OUTER.pack(b"T3MP", T3MP_VERSION, kind, 0, rng.getrandbits(32),
                                   rng.getrandbits(32)) + rng.randbytes(rng.randrange(0, 200)),
                        addr)
        elif roll < 0.15:  # sealed, but tampered, replayed or truncated
            client.seq += 1
            outer = OUTER.pack(b"T3MP", T3MP_VERSION, SEALED, 0, client.session,
                               rng.choice((client.seq, 1, 0)))
            sock.sendto(outer + rng.randbytes(rng.randrange(0, 64)), addr)
        else:
            kind, body = fuzz_body(rng, client.event_next)
            client.send(kind, body)
            if kind == EVENTS:  # its ack moves event_next on
                client.receive(0.05, EVENTS)
        if n % 50 == 0 or n == args.count:
            client.send(HEARTBEAT, b"")
            if client.receive(2.0, HEARTBEAT) is None:
                print(f"no heartbeat answered after {n} packets", flush=True)
                return 1
    print(f"sent {args.count} packets, {client.event_next - 1} events delivered; the server "
          "still answers", flush=True)
    return 0


def summary(client, prefix=""):
    rel = client.rel
    return (f"joins {client.joins}, heartbeats {client.beats}, states {client.states}, "
            f"actor states {client.actor_states}, "
            f"gaps {client.gaps}, events in {client.events} (stale {rel.stale}), out "
            f"{rel.out_next - 1} (sent {rel.sent}, resent {rel.resent}, unacked {len(rel.out)}), "
            f"dropped forged {client.forged}, replayed {client.replayed}, "
            f"over rate {client.limited}"
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
    p.add_argument("--tunnel", type=int, action="append", default=[], metavar="PORT",
                   help="serve an xemu guest through its udp backend (repeatable, one per xemu)")
    p.add_argument("--duration", type=float, help="stop after this many seconds")
    p.add_argument("--world", metavar="DIR",
                   help="keep the world in DIR, one file per load order: the clock, deaths, "
                        "changed objects, objects made at run time and weather, loaded when the "
                        "load order is set and "
                        "written every 10 seconds while it changes")
    p.add_argument("--dhcp-lease", type=int, default=3600, metavar="SECONDS",
                   help="lease time offered to xemu guests that ask for an address "
                        "(NetAddress=dhcp); each tunnel leases %s" % GUEST_IP)
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
    p.add_argument("--bot-owns", metavar="START:END",
                   help="the bot is the authority for its cells from START to END seconds after it "
                        "first appears (END may be left out)")
    p.add_argument("--bot-shift", type=float, default=128,
                   help="as the authority, the bot places each actor this many units east of its "
                        "last reported position")
    p.add_argument("--bot-mirror", type=lambda v: int(v, 16), metavar="REFID",
                   help="instead of circling, the bot stands --bot-shift units east of this actor "
                        "(hex refid) as its authority last reported it, and plays its animation")
    p.add_argument("--bot-echo", action="store_true",
                   help="instead of circling, the bot replays the client's own state --bot-shift "
                        "units east, so a client sees its player as a ghost")
    p.add_argument("--bot-sway", type=float, default=0, metavar="UNITS",
                   help="as the authority, the bot also swings each actor this far east and west, "
                        "once per --bot-period, facing the way it moves")
    p.add_argument("--bot-break-hold", type=float, metavar="SECONDS",
                   help="as the authority, the bot breaks a client's hold this long after it starts")
    p.add_argument("--bot-hold", metavar="REFID@START:END",
                   help="the bot holds this actor (hex refid) in dialogue from START to END seconds")
    p.add_argument("--bot-hit", metavar="REFID@SECONDS",
                   help="the bot hits this actor (hex refid) for 5 once, this long after it appears")
    p.add_argument("--bot-hit-player", metavar="HEALTH[:FATIGUE]@SECONDS",
                   help="the bot hits every client's player once, this long after it appears")
    p.add_argument("--bot-spell", action="append", default=[], metavar="SPELL[:REFID]@SECONDS",
                   help="the bot casts this spell on the first client's player, or on an actor "
                        "(hex refid) at its authority, this long after it appears (repeatable)")
    p.add_argument("--bot-cast", action="append", default=[], metavar="SPELL[:REFID]@SECONDS",
                   help="every client sees the bot cast this spell at the first client's player, "
                        "or at an actor (hex refid), this long after it appears (repeatable)")
    p.add_argument("--bot-shoot", action="append", default=[], metavar="AMMO@SECONDS",
                   help="the bot shoots this ammunition (or thrown weapon) this long after it "
                        "appears (repeatable)")
    p.add_argument("--bot-spawn", action="append", default=[],
                   metavar="ID:CELL[:CONDITION]@SECONDS",
                   help="the bot places this object in the cell of that index (the cells list's "
                        "order), 64 units east of where the first client entered the world, this "
                        "long after it appears, with item data of that condition if given "
                        "(repeatable)")
    p.add_argument("--bot-contents", action="append", default=[],
                   metavar="REFID:CELL:ID*COUNT[*CONDITION],...@SECONDS",
                   help="the bot fills this container (hex refid, in the cell of that index) with "
                        "these items this long after it appears (repeatable)")
    p.add_argument("--bot-take", action="append", default=[], metavar="SECONDS",
                   help="the bot removes every live object a client placed, this long after it "
                        "appears (repeatable)")
    p.add_argument("--bot-status", action="append", default=[],
                   metavar="REFID:FIGHT,FLEE,ALARM,HELLO,DISPOSITION@SECONDS",
                   help="the bot sets an actor's AI settings and base disposition (-32768 for a "
                        "creature) this long after it appears (repeatable)")
    p.add_argument("--bot-affect", action="append", default=[],
                   metavar="REFID:INDEX:SPELL@SECONDS",
                   help="the bot applies one effect of a spell to an actor this long after it "
                        "appears (repeatable)")
    p.add_argument("--bot-stats", metavar="HEALTH,MAGICKA,FATIGUE",
                   help="the bot, as authority, sends these statistics for every actor")
    p.add_argument("--bot-kill", metavar="REFID@SECONDS",
                   help="the bot reports this actor (hex refid) dead this long after it appears")
    p.add_argument("--bot-equip", metavar="ID,ID,...",
                   help="the bot wears these items (an empty string: nothing)")
    p.add_argument("--bot-weather", action="append", default=[],
                   metavar="REGION:WEATHER@SECONDS",
                   help="the bot sets a region's weather (list index, 0-9) this long after it "
                        "appears (repeatable)")
    p.add_argument("--max-players", type=int, default=16,
                   help="refuse a console joining beyond this many (default 16)")
    p.add_argument("--key", metavar="FILE",
                   help="the server's secret key, made on first use (default: server.key in "
                        "--world; without either, a new key each run)")
    p.add_argument("--password-file", metavar="FILE",
                   help="a console whose key is new must give the password on this file's first "
                        "line (its NetPassword); admitted keys go to admitted.txt in --world")
    p.add_argument("--send", metavar="FILE",
                   help="send FILE to each client that joins, into U:\\TES3X\\ under its name")
    p.add_argument("--burst", action="append", default=[], metavar="COUNT@SECONDS",
                   help="send COUNT heartbeats at once, unpaced, SECONDS after a client first "
                        "joins (a receive ring stress test)")
    p.add_argument("--drop", type=float, default=0,
                   help="drop this fraction of session packets each way, to test loss")
    p.add_argument("--seed", type=int, help="seed for --drop")
    p.add_argument("--load-order", metavar="HASH",
                   help="refuse clients whose load order hash differs (hex); default: the first "
                        "client's")
    p.add_argument("--hour", type=float,
                   help="start the session's clock at this GameHour; default: the first client's")
    p.add_argument("--timescale", type=float,
                   help="game seconds per real second; default: the first client's TimeScale")
    p = sub.add_parser("fuzz", help="join a server and send it mutated packets")
    p.add_argument("address", help="HOST[:PORT] of a tes3x_net.py server")
    p.add_argument("--count", type=int, default=2000)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--rate", type=float, default=300, help="packets per second")
    p.add_argument("--password", default="", help="the server's password, if it has one")
    p = sub.add_parser("plugin", help="write the ghost plugin the multiplayer patch moves")
    p.add_argument("out")
    p.add_argument("--master", required=True, help="Morrowind.esm, for its size")
    args = ap.parse_args(argv)
    if args.command == "plugin":
        with open(args.out, "wb") as f:
            f.write(ghost_plugin(os.path.getsize(args.master)))
        return 0
    return {"listen": listen, "ping": ping, "serve": serve, "fuzz": fuzz}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
