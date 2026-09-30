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
    python tools/tes3x_net.py plugin OUT.esp --master Morrowind.esm   # the ghost plugin

With --tunnel PORT this tool is the guest's only peer: xemu sends each guest Ethernet frame to
PORT as one datagram and accepts frames on PORT+1 (`tes3x_xemu.py --net-tunnel PORT`), so ping
answers ARP itself and resolves the console's MAC before it pings.
"""

import argparse
import json
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
DHCP_SERVER, DHCP_CLIENT = 67, 68
DHCP_MAGIC = bytes([0x63, 0x82, 0x53, 0x63])
GUEST_IP = "10.0.2.15"
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
T3MP_VERSION = 10
HELLO, WELCOME, HEARTBEAT, BYE, STATE, PEER, GONE, EVENTS, REFUSE, CLOCK, ACTORS = range(1, 12)
# GameHour, Day, Month (0-11), Year, DaysPassed, TimeScale, as the game's float globals
CLOCK_BODY = struct.Struct("<6f")
# MAC, build id, load order hash, plugin count, then the client's clock
HELLO_BODY = struct.Struct("<6sIII" + CLOCK_BODY.format[1:])
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
    return [item.decode("latin-1") for item in part[2:].split(b"\0") if item]


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
        leveled = struct.unpack_from("<I", data, off)[0]
        off += 4
    name = data[off:].split(b"\0")[0].decode("latin-1")
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
            condition, charge = struct.unpack_from("<II", data, off)
            off += 8
        end = data.index(b"\0", off) if b"\0" in data[off:] else len(data)
        entries.append([data[off:end].decode("latin-1"), count, entry_flags, condition, charge])
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
    where = (cell.split(b"\0", 1)[0].decode("latin-1") if flags & INTERIOR
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
    return name.decode("latin-1") if kind == KEY_INTERIOR else f"exterior {gx},{gy}"


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



def serve(args):
    """A session server: welcomes consoles by MAC, answers each heartbeat at once and relays each
    client's state to the others. Each --tunnel also serves an xemu guest."""
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
    bursts = [(float(at), int(count)) for count, _, at in
              (spec.partition("@") for spec in args.burst)]

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
        client.seq += 1
        packet = T3MP.pack(b"T3MP", T3MP_VERSION, kind, 0, client.session, client.seq,
                           client.peer_seq, now_us(), client.peer_time) + body
        if dropped("out"):
            return
        client.queue.append((client.addr, packet, client.seq))
        pump(client)

    def pump(client):
        """Send what PACE_PACKETS allows of the client's queue."""
        now = time.time()
        start, count = client.window
        if now - start >= PACE_WINDOW:
            start, count = now, 0
        while client.queue and count < PACE_PACKETS:
            addr, packet, seq = client.queue.pop(0)
            if len(addr) == 4:  # a tunnel guest, by its MAC
                ip, _port, mac, link = addr
                link.send(udp_frame(mac, ip, packet, seq))
            else:
                sock.sendto(packet, addr)
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
            name = data[SPELL.size:].split(b"\0")[0].decode("latin-1")
            who = f"client {client.id}" if player else f"{caster:#010x} of client {client.id}"
            on = (f"{refid:#010x} (authority {target})" if refid
                  else f"the player of client {target}")
            print(f"{stamp} {who} casts {name} on {on}", flush=True)
            if target != BOT_ID:
                send_event(target, client.id, kind, data, now)
            return
        if kind == EVENT_CAST and len(data) > SPELL.size:
            caster, target, refid, _, player = SPELL.unpack_from(data)
            name = data[SPELL.size:].split(b"\0")[0].decode("latin-1")
            who = f"client {client.id}" if player else f"{caster:#010x} of client {client.id}"
            at = (f" at {refid:#010x} (client {target})" if refid
                  else f" at the player of client {target}" if target else "")
            print(f"{stamp} {who} casts {name}{at}", flush=True)
        if kind == EVENT_SHOT and len(data) > SHOT.size:
            firer, swing, other, player = SHOT.unpack_from(data)
            name = data[SHOT.size:].split(b"\0")[0].decode("latin-1")
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
            name = data[5:].split(b"\0")[0].decode("latin-1")
            print(f"{stamp} client {client.id}: {refid:#010x} takes effect {index} of {name}",
                  flush=True)
        if kind == EVENT_OBJECTS and data:
            changed = unpack_objects(data)
            objects.update(changed)
            world["dirty"] = True
            for refid, rest in changed.items():
                print(f"{stamp} client {client.id}: {describe_object(refid, *rest)}", flush=True)
        if kind == EVENT_TEXT:
            print(f"{stamp} client {client.id} says: {data.decode('latin-1')}", flush=True)
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
        for i in range(count):
            record = body[4 + i * ACTOR.size:4 + (i + 1) * ACTOR.size]
            if len(record) < ACTOR.size:
                break
            refid, x, y = ACTOR.unpack(record)[:3]
            key = own if own and own[0] == KEY_INTERIOR else (
                KEY_EXTERIOR, math.floor(x / CELL_UNITS), math.floor(y / CELL_UNITS), b"")
            actors[refid] = (client.id, key, record)
            if refid == args.bot_mirror:
                bot["mirror"] = record
            client.actor_states += 1
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

    def handle(packet, addr):
        nonlocal pinned, clock
        if len(packet) < T3MP.size or dropped("in"):
            return
        magic, version, kind, _, session, seq, _, sent, _ = T3MP.unpack_from(packet)
        if magic != b"T3MP" or version != T3MP_VERSION:
            return
        stamp = time.strftime("%H:%M:%S")
        now = time.time()
        if kind == HELLO and len(packet) >= T3MP.size + HELLO_BODY.size:
            mac, build, order, plugins, *offered = HELLO_BODY.unpack_from(packet, T3MP.size)
            mac = mac.hex(":")
            if pinned is None:
                pinned = (order, plugins)
                print(f"{stamp} load order {order:#010x} ({plugins} plugins) set by {mac}",
                      flush=True)
                adopt_world(order, now)
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
            client.known = {}
            if client.joins == 1:
                client.joined = now
                client.bursts = sorted(bursts)
            verb = "joined" if client.joins == 1 else "rejoined"
            print(f"{stamp} client {client.id} {verb}: {mac} at {addr[0]}:{addr[1]}, "
                  f"build {build:#010x}", flush=True)
            send(client, WELCOME, struct.pack("<I", client.id))
            if clock is None:
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
                handle(data, addr)
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
                    handle(data, (src, PORT, frame[6:12], link))
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
                        if len(addr) == 4:
                            ip, _port, mac, link = addr
                            link.send(udp_frame(mac, ip, packet, seq))
                        else:
                            sock.sendto(packet, addr)
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


def summary(client, prefix=""):
    rel = client.rel
    return (f"joins {client.joins}, heartbeats {client.beats}, states {client.states}, "
            f"actor states {client.actor_states}, "
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
