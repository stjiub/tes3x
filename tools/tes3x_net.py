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
import glob
import hashlib
import hmac
import json
import math
import os
import queue
import random
import re
import select
import signal
import socket
import struct
import sys
import threading
import time
import traceback

import tes3x_netbuild

PORT = 26500
AGENT_PORT = 26501
ADMIN_PORT = 26502
REMOTE_ADMIN_PORT = 26503
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


LOG_LEVELS = ("normal", "verbose")
CAIUS_PACKAGE = "bk_a1_1_caiuspackage"
SPYMASTER_QUEST, SPYMASTER_GIVEN, SPYMASTER_DONE = "a1_1_findspymaster", 1, 10


def quiet_admin(line):
    """Whether an admin command goes unlogged: `list`, which the GUI polls."""
    return line.strip().lower() == b"list"


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


def free_udp_ports(count):
    """`count` consecutive free UDP ports on localhost, below the ephemeral range, where any
    process's next outgoing socket could take one before xemu binds it."""
    for _ in range(50):
        sockets = []
        base = random.randrange(20000, 30000)
        try:
            for port in range(base, base + count):
                sockets.append(socket.socket(socket.AF_INET, socket.SOCK_DGRAM))
                sockets[-1].bind(("127.0.0.1", port))
            return base
        except OSError:
            continue
        finally:
            for item in sockets:
                item.close()
    raise OSError("no free UDP ports for an xemu tunnel")


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
        self.forward = udp_socket()
        self.forward.bind(("127.0.0.1", 0))
        self.forward_guest = {}

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
    deadline = time.monotonic() + args.timeout if args.timeout else None
    while deadline is None or time.monotonic() < deadline:
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
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        frame = link.recv(0.5)
        if frame and frame[12:14] == b"\x08\x06" and frame[28:32] == target:
            break
    while time.monotonic() < deadline:
        link.send(arp_frame(1, BROADCAST, b"\0" * 6, host))
        end = time.monotonic() + 1.0
        while time.monotonic() < end:
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
        t0 = time.monotonic()
        mac = resolve(link, args.host, args.wait)
        if mac is None:
            print("no ARP reply")
            return 1
        print(f"arp reply from {mac.hex(':')} after {time.monotonic() - t0:.1f} s", flush=True)
    else:
        sock = udp_socket()
        sock.bind(("0.0.0.0", 0))
        print(f"probing {args.host} until it answers (up to {args.wait:.0f} s)", flush=True)
        t0 = time.monotonic()
        while True:
            if time.monotonic() - t0 > args.wait:
                print("no echo reply")
                return 1
            sock.sendto(PING + struct.pack("<I", 0xFFFFFFFF), (args.host, PORT))
            sock.settimeout(1.0)
            try:
                if sock.recvfrom(2048)[0][:8] == PONG:
                    break
            except (socket.timeout, ConnectionResetError):
                pass
        print(f"first reply after {time.monotonic() - t0:.1f} s", flush=True)
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
T3MP_VERSION = 22
MANAGER_VERSION = 1  # build discovery stays independent of gameplay state
HELLO, WELCOME, HEARTBEAT, BYE, STATE, PEER, GONE, EVENTS, REFUSE, CLOCK, ACTORS = range(1, 12)
BUILD = 12  # to a manager's HELLO: tes3x_netbuild.BUILD_BODY, then the server forgets it
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
# MAC, build id, load order hash, plugin count, the client's clock, then its manifest's build id
# (zero without one); NetPassword follows.
# LOBBY in the plugin count: a console at the main menu, with no game and so no clock. It gets no
# world, only what picks a character (GAME, CHARS or NEWCHAR, PICK, the checkpoint and LOAD).
HELLO_BODY = struct.Struct("<6sIII" + CLOCK_BODY.format[1:] + "32s")
LOBBY = 0x80000000
# MANAGER in the plugin count: the console manager, asking for this server's build, not joining
MANAGER = 0x40000000
PASSWORD_MAX = 64
PASSWORD_RATE = (5, 1 / 60)  # password tries from one address, (burst, per second)
# REFUSE: the session's load order hash, its plugin count, and why
REFUSE_BODY = struct.Struct("<III")
REFUSED_LOAD_ORDER, REFUSED_FULL, REFUSED_PASSWORD, REFUSED_KICKED, REFUSED_BANNED = 1, 2, 3, 4, 5
REFUSED_STALE = 6  # the console's build is not the one --build serves: its manager can update it
REFUSED_PROTOCOL = 7  # expected version, offered version, reason (instead of load order/count)
BAN_KINDS = ("key", "mac", "address")
MONTH_DAYS = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
IDLE_TIMEOUT = 20.0  # silence after which a client is dropped; the console gives up at 15 s
ANNOUNCE_WAIT = 6.0  # how long a join waits for the character's name before using a number
EVENTS_HEAD = struct.Struct("<IB3x")  # the sender's last delivered event, event count
EVENT = struct.Struct("<IHHI")  # seq, kind, length, origin client; the data follows
EVENTS_BYTES = 512  # the client's largest EVENTS body
EVENT_DATA = 80
EVENT_TEXT = 1
EVENT_WELCOME = 35  # the --welcome text, once per join as a character, shown in a box
EVENT_IDENTITY = 33  # part, two parts, female, then two ids/text values ending in zero
EVENT_ACTOR_EQUIPMENT = 34  # actor id, part, parts, then equipped item ids ending in zero
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
# count, then (actor id, client) pairs: who runs an actor instead of its cell's authority; client 0
# hands it back to the cell's authority
EVENT_OWNERS = 20
OWNER_PAIR = struct.Struct("<II")
OWNERS_PER_EVENT = (EVENT_DATA - 4) // OWNER_PAIR.size
# u8: BUSY_SAVING while the client's game thread is held by a save, 0 once it is back. A busy
# client keeps its session but gives up its cells and actors to any other player loading them.
EVENT_BUSY = 21
BUSY_SAVING = 1
EVENT_SAVE = 22  # request a state flush; byte 1 requests a diagnostic save upload
EVENT_SNAPSHOT = 36  # token u32 + STATE_BODY; reply token u32 after durable storage
EVENT_BOUNTY = 30  # the player's bounty, i32: the latest of each client is kept and replayed
# From a client after each WELCOME: its launch token (new each title launch), then the name of the
# save that launch loaded, or "". A console not running its character's latest checkpoint is sent
# the checkpoint as CHECKPOINT_NAME and LOAD (to a client: load that file once it has it).
EVENT_GAME, EVENT_LOAD = 23, 24
# The player's own state, per character and never relayed: a sub-kind, then PLAYER_ITEMS (part,
# parts, item id, then entries as in CONTENTS: every stack of that item, none once it is gone),
# PLAYER_LEVEL (LEVEL), PLAYER_SKILLS (count, then SKILL each), PLAYER_MODIFIERS (count, then
# MODIFIER each), PLAYER_JOURNAL (count, then (index u16, quest id) each) or PLAYER_VITALS
# (VITALS). To a client only: PLAYER_PLACE (a
# STATE_BODY: where the player last was, which the server takes from STATE), PLAYER_SPELLS (mode,
# part, parts, ids ending in zero), the kept state, then PLAYER_READY (0: the console sends all of
# its state, so a stream kept from before a field was streamed fills in; 1, which the console still
# takes as "already sent", is no longer used). The console sends nothing before READY.
EVENT_PLAYER = 25
PLAYER_ITEMS, PLAYER_LEVEL, PLAYER_SKILLS, PLAYER_JOURNAL, PLAYER_READY = 1, 2, 3, 4, 5
PLAYER_VITALS, PLAYER_PLACE = 6, 7
# Death while joined: the console sends PLAYER_DEATH instead of offering its last save, is answered
# PLAYER_RESPAWN (RESPAWN: delay in ms, RESPAWN_*, gold to lose) and sends PLAYER_ALIVE once
# resurrected at the closest marker. The server relays DEATH and ALIVE to the other players so
# their ghosts fall and rise. A character that died and was not back is respawned again after the
# replay of its next launch.
PLAYER_DEATH, PLAYER_RESPAWN, PLAYER_ALIVE = 8, 9, 10
PLAYER_SPELLS = 11
PLAYER_BOUNTY = 12  # from the server: the character's last streamed crime bounty, i32
# Who the character is, both ways: part, parts, then a slice of one body (IDENTITY_STATS: female,
# then the class's two attributes, specialisation and ten skills; then name, race, head, hair,
# birthsign, class id and class name, each ending in zero; the birthsign may be empty). The
# server keeps the latest and replays it first, so a launch running another character becomes
# this one.
PLAYER_IDENTITY = 13
# What the character wears, both ways: part, parts, then a slice of one body of WORN entries (flags,
# condition and charge when flags has ENTRY_DATA, item id ending in zero), every equipped stack. The
# server keeps the latest and replays it after the items.
PLAYER_WORN = 14
PLAYER_EFFECTS = 16  # complete multipart active-effect snapshot
PLAYER_EFFECT_BYTES = 608
PLAYER_EFFECTS_MAX = 64
PLAYER_MODIFIERS = 15  # current attribute/skill values: count, then MODIFIER entries
IDENTITY_STATS = struct.Struct("<B13i")
IDENTITY_FIELDS = ("name", "race", "head", "hair", "birthsign", "class", "class_name")
SPELLS_SNAPSHOT, SPELLS_ADD, SPELLS_REMOVE = 0, 1, 2
RESPAWN = struct.Struct("<IBI")
RESPAWN_PLACES = {"temple": 0, "shrine": 1, "nearest": 2}
PLACE_HOLD = 15.0  # seconds a replayed place waits for the console to arrive before STATE counts
PLACE_NEAR = 512.0  # units: the console has arrived; also the move that marks the place to write
# Characters. GAME's name is followed by the launch's kind (GAME_NEW: a New Game). To a client:
# CHARS (part, parts, then the names of its key's characters each ending in a zero) to choose
# from, or NEWCHAR (the same with start point names) to make one; the console answers PICK
# (PICK_CHARACTER and an index into CHARS or PICK_NEW, or PICK_START and an index into NEWCHAR).
# The chosen start comes back as RUN events, one script line each, ended by an empty one.
EVENT_CHARS, EVENT_PICK, EVENT_NEWCHAR, EVENT_RUN = 26, 27, 28, 29
GAME_NONE, GAME_LOAD, GAME_NEW = 0, 1, 2
PICK_CHARACTER, PICK_START, PICK_NEW = 1, 2, 255
CHARACTERS_LISTED = 8  # buttons on the console's list, with "New character"
START_NAME = 31
STARTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "examples",
                      "starts.toml")
# level, level progress, level-ups per attribute (8) and per specialisation (3), base health,
# magicka and fatigue, base attributes (8)
LEVEL = struct.Struct("<HH11B3f8f")
SKILL = struct.Struct("<Bff")  # skill, base, progress
MODIFIER = struct.Struct("<Bf")  # attribute 0..7, then skill 0..26, current value
VITALS = struct.Struct("<3f")  # current health, magicka, fatigue
ATTRIBUTE_NAMES = ("Strength", "Intelligence", "Willpower", "Agility", "Speed", "Endurance",
                   "Personality", "Luck")
SKILL_NAMES = ("Block", "Armorer", "MediumArmor", "HeavyArmor", "BluntWeapon", "LongBlade", "Axe",
               "Spear", "Athletics", "Enchant", "Destruction", "Alteration", "Illusion",
               "Conjuration", "Mysticism", "Restoration", "Alchemy", "Unarmored", "Security",
               "Sneak", "Acrobatics", "LightArmor", "ShortBlade", "Marksman", "Mercantile",
               "Speechcraft", "HandToHand")
QUEST_INDICES = 32  # a quest's latest indices kept for the replay
STREAM_NAME = "stream.json"
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
# Events only the server sends. A console acts on them, so one from a client is never relayed.
SERVER_EVENTS = {EVENT_WELCOME, EVENT_AUTHORITY, EVENT_OWNERS, EVENT_SAVE, EVENT_LOAD,
                 EVENT_CHARS, EVENT_NEWCHAR, EVENT_RUN}
KEY = struct.Struct("<Iii32s")  # kind, grid x, grid y, interior name
KEY_EXTERIOR, KEY_INTERIOR = 1, 2
ANIM_BYTES = 20  # per layer: 3 groups, pad, 3 keys, pad, 3 times (tes3xnet.c anim_capture)
NO_ANIM = b"\xff\xff\xff" + bytes(ANIM_BYTES - 3)  # no group on any layer: the ghost idles
# refid, x, y, z, heading, health, flags, magicka, fatigue, combat target, animation. The target is
# a client id for a player (ghosts included), else an actor id; 0 for none.
ACTOR = struct.Struct(f"<I5fI2fI{ANIM_BYTES}s")
ACTORS_PER_PACKET = 8
ACTOR_PERIOD = 0.1
ACTOR_DEAD, ACTOR_IN_COMBAT = 1, 2
AUTHORITY_PERIOD = 0.25
# An actor goes to a nearer player only when that player is this much nearer than its owner and
# the owner has had it this long, so two players at about the same distance do not trade it. One
# fighting a player goes to that player's console, after the same hold.
OWNER_MARGIN = 256
OWNER_HOLD = 2.0
OWNER_STALE = 5.0  # seconds without a state before an actor is no longer owned
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
BULK_STATUS = ("idle", "opening", "receiving", "done", "bad hash", "refused", "failed",
               "no space")
BULK_RECEIVING, BULK_DONE, BULK_BAD_HASH, BULK_REFUSED, BULK_FAILED = 2, 3, 4, 5, 6
BULK_NO_SPACE = 7  # the console's drive cannot take the file and a margin
BULK_WINDOW_IN = 8  # chunks a console keeps in flight to the server: its send slots
BULK_ACK_EVERY = 0.25  # seconds between acks to a console that is sending
UPLOAD_FILES = 64  # files one console key may keep in its uploads folder
CHARACTER_BACKUPS = 3  # earlier versions kept beside each character's save
CHECKPOINT_NAME = "char-{:08x}.ess"  # by the first four bytes of its BLAKE2b
# What a New Game writes into the player at its [PreLoad] read (serve --load-state): "T3MC", then
# PLAYER events (identity, items, worn, place) each behind a u16 length.
CHARACTER_FILE = "char-{:08x}.t3c"
CHARACTER_MAGIC = b"T3MC"
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


def pack_identity(name, race, head, hair, female=False):
    """The two IDENTITY events sent for one character."""
    values = [value.encode() if isinstance(value, str) else value
              for value in (name, race, head, hair)]
    if any(not value or len(value) >= 32 or b"\0" in value for value in values):
        raise ValueError("identity values must be 1..31 bytes without a zero")
    return [bytes((part, 2, bool(female))) + b"\0".join(values[2 * part:2 * part + 2]) + b"\0"
            for part in range(2)]


def unpack_identity(part):
    """Return (part, female, first, second), or raise ValueError for malformed identity data."""
    if len(part) < 5 or part[0] > 1 or part[1] != 2:
        raise ValueError("bad identity header")
    values = part[3:].split(b"\0")
    if len(values) != 3 or values[-1] or any(not value or len(value) >= 32 for value in values[:2]):
        raise ValueError("bad identity strings")
    if any(byte < 0x20 for value in values[:2] for byte in value):
        raise ValueError("bad identity character")
    return part[0], bool(part[2]), *(wire_text(value) for value in values[:2])


def pack_player_identity(identity):
    """The PLAYER_IDENTITY events for a kept identity."""
    body = IDENTITY_STATS.pack(bool(identity["female"]), *identity["class_attributes"],
                               identity["specialization"], *identity["class_skills"])
    for field in IDENTITY_FIELDS:
        value = identity[field].encode("latin-1", "replace")
        if len(value) >= 32 or b"\0" in value or (not value and field != "birthsign"):
            raise ValueError(f"identity {field} must be 1..31 bytes without a zero")
        body += value + b"\0"
    per = EVENT_DATA - 3
    chunks = [body[i:i + per] for i in range(0, len(body), per)]
    return [bytes([PLAYER_IDENTITY, i, len(chunks)]) + chunk for i, chunk in enumerate(chunks)]


def unpack_player_identity(body):
    """A whole PLAYER_IDENTITY body as a dict, or raise ValueError."""
    if len(body) < IDENTITY_STATS.size:
        raise ValueError("identity too short")
    female, *stats = IDENTITY_STATS.unpack_from(body)
    values = body[IDENTITY_STATS.size:].split(b"\0")
    if len(values) != len(IDENTITY_FIELDS) + 1 or values[-1]:
        raise ValueError("bad identity strings")
    for field, value in zip(IDENTITY_FIELDS, values):
        if len(value) >= 32 or (not value and field != "birthsign") or \
                any(byte < 0x20 for byte in value):
            raise ValueError(f"bad identity {field}")
    identity = dict(zip(IDENTITY_FIELDS, (wire_text(value) for value in values)))
    identity.update(female=bool(female), class_attributes=stats[:2], specialization=stats[2],
                    class_skills=stats[3:])
    return identity


def describe_identity(identity):
    sex = "female" if identity["female"] else "male"
    sign = identity["birthsign"] or "no birthsign"
    return (f"is {identity['name']}: {sex} {identity['race']}, {identity['head']}, "
            f"{identity['hair']}, {identity['class_name']} ({identity['class']}), {sign}")


def pack_actor_equipment(refid, ids):
    """The ACTOR_EQUIPMENT events of one set."""
    parts = [bytearray(struct.pack("<I", refid) + b"\0\0")]
    for item in ids:
        item = item.encode() if isinstance(item, str) else item
        if not item or len(item) >= 32 or b"\0" in item:
            raise ValueError("equipment ids must be 1..31 bytes without a zero")
        if len(parts[-1]) + len(item) + 1 > EVENT_DATA:
            parts.append(bytearray(struct.pack("<I", refid) + b"\0\0"))
        parts[-1] += item + b"\0"
    for index, part in enumerate(parts):
        part[4:6] = bytes((index, len(parts)))
    return [bytes(part) for part in parts]


def unpack_actor_equipment(data):
    """Return (refid, part, parts, ids), or raise ValueError for malformed data."""
    if len(data) < 6:
        raise ValueError("short actor equipment")
    refid = struct.unpack_from("<I", data)[0]
    part, parts = data[4], data[5]
    if not refid or not parts or part >= parts:
        raise ValueError("bad actor equipment header")
    values = data[6:].split(b"\0")
    if values[-1] or any(not value or len(value) >= 32 for value in values[:-1]):
        raise ValueError("bad actor equipment id")
    if any(byte < 0x20 or byte == ord('"') for value in values[:-1] for byte in value):
        raise ValueError("bad actor equipment character")
    return refid, part, parts, [wire_text(value) for value in values[:-1]]


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
        f.flush()
        os.fsync(f.fileno())
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


def latest_character(folder):
    """The newest kept character's save in folder (not a backup), or None."""
    if not folder or not os.path.isdir(folder):
        return None
    saves = [os.path.join(folder, f) for f in os.listdir(folder)
             if f.lower().endswith(".ess") and not re.search(r"\.\d+\.ess$", f, re.I)]
    return max(saves, key=os.path.getmtime, default=None)


def keep_character(upload, folder):
    """Move a finished .ess upload whose header names a player to folder, shifting the versions
    it replaces to NAME.1.ess and on; its header, or None when it is not a save."""
    from tes3x_saves import HEAD_LIMIT, parse_header
    with open(upload, "rb") as stream:
        head = parse_header(stream.read(HEAD_LIMIT))
    if not head["player"]:
        return None
    os.makedirs(folder, exist_ok=True)
    stem = os.path.basename(upload)[:-4]
    versions = [os.path.join(folder, stem + ".ess")] + [
        os.path.join(folder, f"{stem}.{i}.ess") for i in range(1, CHARACTER_BACKUPS + 1)]
    for newer, older in reversed(list(zip(versions, versions[1:]))):
        if os.path.exists(newer):
            os.replace(newer, older)
    os.replace(upload, versions[0])
    return head


def save_player(path):
    """The player's name in a save's header, or None."""
    from tes3x_saves import HEAD_LIMIT, parse_header
    with open(path, "rb") as stream:
        return parse_header(stream.read(HEAD_LIMIT))["player"]


def character_slug(name):
    return re.sub(r"[^A-Za-z0-9 _-]", "-", name or "").strip()[:24] or "character"


def kept_characters(root, fixtures=False):
    """Character folders with retained streams, newest first; optional save fixtures for tests."""
    if not root or not os.path.isdir(root):
        return []
    loose = latest_character(root) if fixtures else None
    if loose:
        folder = new_character_folder(root, save_player(loose))
        os.makedirs(folder)
        for f in os.listdir(root):
            if f.lower().endswith(".ess") or f == STREAM_NAME:
                os.replace(os.path.join(root, f), os.path.join(folder, f))
    found = [(f, (latest_character(os.path.join(root, f)) if fixtures else None) or
              (os.path.join(root, f, STREAM_NAME) if os.path.isfile(
                  os.path.join(root, f, STREAM_NAME)) else None)) for f in os.listdir(root)
             if os.path.isdir(os.path.join(root, f))]
    return sorted(((f, p) for f, p in found if p), key=lambda c: -os.path.getmtime(c[1]))


def new_character_folder(root, player):
    slug = character_slug(player)
    folder, n = os.path.join(root, slug), 1
    while os.path.exists(folder):
        n += 1
        folder = os.path.join(root, f"{slug}-{n}")
    return folder


def checkpoint_name(data, form=CHECKPOINT_NAME):
    return form.format(
        int.from_bytes(hashlib.blake2b(data, digest_size=32).digest()[:4], "big"))


def load_starts(path):
    """Start points as (name, script lines), from a TOML file of [[start]] tables."""
    import tomllib
    with open(path, "rb") as f:
        tables = tomllib.load(f).get("start", [])
    starts = []
    for t in tables:
        name = str(t["name"])
        if not name or len(name) > START_NAME or '"' in name or not name.isascii():
            raise ValueError(f"{path}: start name {name!r}: 1 to {START_NAME} ASCII characters, "
                             "no double quotes")
        x, y, z = (float(v) for v in t["position"])
        turn = float(t.get("rotation", 0))
        where = f"{x:g} {y:g} {z:g} {turn:g}"
        lines = [f'Player->PositionCell {where} "{t["cell"]}"' if "cell" in t
                 else f"Player->Position {where}"]
        lines += [f'Player->RemoveItem "{i}" {int(n)}' for i, n in t.get("remove", [])]
        lines += [f'Player->AddItem "{i}" {int(n)}' for i, n in t.get("items", [])]
        lines += [f'Player->Equip "{i}"' for i in t.get("equip", [])]
        lines += [str(line) for line in t.get("script", [])]
        for line in lines:
            if len(line) >= EVENT_DATA or not line.isascii() or "\n" in line:
                raise ValueError(f"{path}: {name}: line {line!r} is not one ASCII line under "
                                 f"{EVENT_DATA} bytes")
        starts.append((name, lines))
    if not starts:
        raise ValueError(f"{path}: no [[start]] tables")
    return starts


def pack_names(names):
    """CHARS or NEWCHAR bodies: part, parts, then names each ending in a zero."""
    parts, body = [], b""
    for name in names:
        entry = name.encode("latin-1", "replace") + b"\0"
        if body and 2 + len(body) + len(entry) > EVENT_DATA:
            parts.append(body)
            body = b""
        body += entry
    parts.append(body)
    return [bytes([i, len(parts)]) + part for i, part in enumerate(parts)]


def pack_items(item, entries):
    """PLAYER_ITEMS parts for every stack of one item; an empty list says it is gone."""
    head = item.encode("latin-1") + b"\0"
    parts, body = [], b""
    for count, flags, condition, charge in entries:
        entry = ENTRY.pack(count, flags) + (struct.pack("<II", condition, charge)
                                            if flags & ENTRY_DATA else b"")
        if body and 3 + len(head) + len(body) + len(entry) > EVENT_DATA:
            parts.append(body)
            body = b""
        body += entry
    parts.append(body)
    return [bytes([PLAYER_ITEMS, i, len(parts)]) + head + part for i, part in enumerate(parts)]


def pack_worn(worn):
    """PLAYER_WORN parts for a kept list of [item id, flags, condition, charge]."""
    body = b"".join(bytes([flags & ENTRY_DATA]) +
                    (struct.pack("<II", condition, charge) if flags & ENTRY_DATA else b"") +
                    item.encode("latin-1", "replace") + b"\0"
                    for item, flags, condition, charge in worn)
    per = EVENT_DATA - 3
    chunks = [body[i:i + per] for i in range(0, len(body), per)] or [b""]
    return [bytes([PLAYER_WORN, i, len(chunks)]) + chunk for i, chunk in enumerate(chunks)]


def unpack_worn(body):
    """A whole PLAYER_WORN body as [item id, flags, condition, charge] entries, or raise
    ValueError."""
    worn, off = [], 0
    while off < len(body):
        flags = body[off] & ENTRY_DATA
        off += 1
        condition = charge = 0
        if flags:
            if off + 8 > len(body):
                raise ValueError("worn entry cut short")
            condition, charge = struct.unpack_from("<II", body, off)
            off += 8
        end = body.find(b"\0", off)
        if end <= off or end - off >= 32:
            raise ValueError("bad worn item id")
        worn.append([wire_text(body[off:end]), flags, condition, charge])
        off = end + 1
    return worn


def unpack_items(data):
    """(part, parts, item id, entries) of a PLAYER_ITEMS event."""
    part, parts = data[1], data[2]
    raw, _, rest = data[3:].partition(b"\0")
    entries, off = [], 0
    while off + ENTRY.size <= len(rest):
        count, flags = ENTRY.unpack_from(rest, off)
        off += ENTRY.size
        condition = charge = 0
        if flags & ENTRY_DATA:
            if off + 8 > len(rest):
                break
            condition, charge = struct.unpack_from("<II", rest, off)
            off += 8
        entries.append([count, flags & ENTRY_DATA, condition, charge])
    return part, parts, wire_text(raw), entries


def pack_journal(quests):
    """PLAYER_JOURNAL events for (quest id, index) pairs, in order."""
    events, body, count = [], b"", 0
    for quest, index in quests:
        entry = struct.pack("<H", index) + quest.encode("latin-1") + b"\0"
        if count and 2 + len(body) + len(entry) > EVENT_DATA:
            events.append(bytes([PLAYER_JOURNAL, count]) + body)
            body, count = b"", 0
        body += entry
        count += 1
    if count:
        events.append(bytes([PLAYER_JOURNAL, count]) + body)
    return events


def unpack_journal(data):
    """(quest id, index) pairs of a PLAYER_JOURNAL event."""
    quests, off = [], 2
    for _ in range(data[1]):
        if off + 3 > len(data):
            break
        index = struct.unpack_from("<H", data, off)[0]
        raw, found, _ = data[off + 2:].partition(b"\0")
        if not found:
            break
        quests.append((wire_text(raw), index))
        off += 3 + len(raw)
    return quests


def describe_level(body):
    level, progress, *rest = LEVEL.unpack(body)
    stats, attributes = rest[11:14], rest[14:]
    return (f"level {level} ({progress} toward the next), health {stats[0]:.0f}, magicka "
            f"{stats[1]:.0f}, fatigue {stats[2]:.0f}, "
            + ", ".join(f"{n} {v:.0f}" for n, v in zip(ATTRIBUTE_NAMES, attributes)))


def effect_id_not_bound(active):
    effect_id = struct.unpack_from('<h', active, 2)[0]
    return not 120 <= effect_id <= 131 or effect_id == 126


def unpack_player_effects(body):
    """Validate a complete, pointer-free active-effect snapshot."""
    body = bytes(body)
    if len(body) % PLAYER_EFFECT_BYTES or len(body) > PLAYER_EFFECT_BYTES * PLAYER_EFFECTS_MAX:
        raise ValueError("bad active-effect snapshot size")
    effects, seen, sources, instances = [], set(), {}, {}
    for off in range(0, len(body), PLAYER_EFFECT_BYTES):
        serial, source_type, index, caster_kind, flags, caster, corprus = struct.unpack_from(
            '<IBBBBIf', body, off)
        source, item = body[off + 16:off + 48], body[off + 48:off + 80]
        active = body[off + 80:off + 92]
        resisted, magnitude, elapsed, cumulative, state, condition, charge = struct.unpack_from(
            '<fiffiII', body, off + 92)
        definitions = body[off + 120:off + 312]
        source_name = body[off + 312:off + 376]
        source_stats = body[off + 376:off + 388]
        previous = []
        for slot in range(5):
            at = off + 388 + slot * 44
            name, stack_flags, stack_condition, stack_charge = struct.unpack_from('<32sIII', body, at)
            if b'\0' not in name or stack_flags & ~1:
                raise ValueError('bad previous bound equipment')
            name = wire_text(name.split(b'\0')[0])
            if (name or stack_flags or stack_condition or stack_charge) and effect_id_not_bound(active):
                raise ValueError('previous equipment on a non-bound effect')
            if not name and (stack_flags or stack_condition or stack_charge):
                raise ValueError('previous bound equipment has no item')
            previous.append(dict(item=name, flags=stack_flags, condition=stack_condition,
                                 charge=stack_charge))
        effect_id = struct.unpack_from('<h', active, 2)[0]
        source_key = (source_type, source)
        source_data = (definitions, source_name, source_stats)
        if source_key in sources and sources[source_key] != source_data:
            raise ValueError("inconsistent active-effect source")
        sources[source_key] = source_data
        if (not serial or source_type not in (1, 2, 3) or index >= 8 or caster_kind > 3
                or flags & ~3 or not finite(corprus, resisted, elapsed, cumulative)
                or corprus < 0 or elapsed < 0 or state != 5 or magnitude < 0
                or active[0] != index or not 0 <= effect_id <= 142
                or (serial, index) in seen or b'\0' not in source or not source[0]
                or b'\0' not in item or b'\0' not in source_name
                or struct.unpack_from('<h', definitions, index * 24)[0] != effect_id):
            raise ValueError("bad active-effect entry")
        metadata = (source_type, source, item, caster_kind, caster, flags, corprus,
                    condition, charge)
        if serial in instances and instances[serial] != metadata:
            raise ValueError("inconsistent active-effect instance")
        instances[serial] = metadata
        for definition in struct.iter_unpack('<hbbiiiii', definitions):
            effect, skill, attribute, range_, area, duration, low, high = definition
            if effect == -1:
                continue
            if (not 0 <= effect <= 142 or not -1 <= skill < 27 or not -1 <= attribute < 8
                    or range_ not in (0, 1, 2) or min(area, duration, low, high) < 0
                    or max(area, duration, low, high) > 10000000 or low > high):
                raise ValueError("bad active-effect source definition")
        if source_type == 3:
            weight = struct.unpack_from('<f', source_stats)[0]
            if not finite(weight) or not 0 <= weight <= 10000000:
                raise ValueError("bad active-effect source weight")
        seen.add((serial, index))
        effects.append(dict(serial=serial, source_type=source_type, index=index,
                            caster_kind=caster_kind, flags=flags, caster=caster,
                            corprus=corprus, source=wire_text(source.split(b'\0')[0]),
                            item=wire_text(item.split(b'\0')[0]), active=active.hex(),
                            resisted=resisted, magnitude=magnitude, elapsed=elapsed,
                            cumulative=cumulative, state=state, condition=condition,
                            charge=charge, definitions=definitions.hex(),
                            source_name=wire_text(source_name.split(b'\0')[0]),
                            source_stats=source_stats.hex(), previous=previous))
    return effects


def pack_player_effects(effects):
    """Atomic snapshot parts; elapsed game time stops while the character is offline."""
    body = b''
    for e in effects:
        if (len(e['source'].encode('latin-1')) >= 32
                or len(e['item'].encode('latin-1')) >= 32
                or len(e['source_name'].encode('latin-1')) >= 64):
            raise ValueError('active-effect source text too long')
        body += struct.pack('<IBBBBIf', e['serial'], e['source_type'], e['index'],
                            e['caster_kind'], e['flags'], e['caster'], e['corprus'])
        body += e['source'].encode('latin-1').ljust(32, b'\0')
        body += e['item'].encode('latin-1').ljust(32, b'\0')
        body += bytes.fromhex(e['active'])
        body += struct.pack('<fiffiII', e['resisted'], e['magnitude'], e['elapsed'],
                            e['cumulative'], e['state'], e['condition'], e['charge'])
        body += bytes.fromhex(e['definitions'])
        body += e['source_name'].encode('latin-1').ljust(64, b'\0')
        body += bytes.fromhex(e['source_stats'])
        previous = e.get('previous', [])
        if len(previous) > 5:
            raise ValueError('too much previous bound equipment')
        for slot in range(5):
            entry = previous[slot] if slot < len(previous) else {}
            name = entry.get('item', '').encode('latin-1')
            if len(name) >= 32:
                raise ValueError('previous bound item text too long')
            body += struct.pack('<32sIII', name, entry.get('flags', 0),
                                entry.get('condition', 0), entry.get('charge', 0))
    unpack_player_effects(body)
    size = EVENT_DATA - 5
    parts = [body[i:i + size] for i in range(0, len(body), size)] or [b'']
    return [bytes([PLAYER_EFFECTS]) + struct.pack('<HH', i, len(parts)) + p
            for i, p in enumerate(parts)]


class PlayerStream:
    """One character's state as its console streamed it: each item's stacks, the level block,
    each skill and current modifier, active effects, quest indices and known spells. It holds
    what changed since the character's first launch on this server and replays it when the
    character joins."""

    def __init__(self, path):
        self.path = path
        self.items, self.skills, self.modifiers, self.journal, self.level = {}, {}, {}, {}, None
        self.spells = None
        self.effects = self.effects_parts = None
        self.vitals = self.place = None
        self.bounty = self.identity = self.worn = None
        self.dead = False  # died and not yet back
        self.arriving = None  # (item id, entries so far, next part)
        self.identity_parts = self.worn_parts = None  # (body so far, next part)
        self.dirty = False
        try:
            with open(path, encoding="utf-8") as f:
                kept = json.load(f)
        except FileNotFoundError:
            return
        self.items = kept.get("items", {})
        self.skills = {int(k): v for k, v in kept.get("skills", {}).items()}
        self.modifiers = {int(k): v for k, v in kept.get("modifiers", {}).items()}
        self.journal = kept.get("journal", {})
        self.level = bytes.fromhex(kept["level"]) if kept.get("level") else None
        self.vitals = kept.get("vitals")
        self.place = bytes.fromhex(kept["place"]) if kept.get("place") else None
        self.dead = kept.get("dead", False)
        self.spells = kept.get("spells")
        self.effects = kept.get("effects")
        self.bounty = kept.get("bounty")
        self.identity = kept.get("identity")
        self.worn = kept.get("worn")

    def reset(self):
        """A new character: nothing streamed so far belongs to it."""
        self.items, self.skills, self.modifiers, self.journal, self.level = {}, {}, {}, {}, None
        self.spells = None
        self.effects = self.effects_parts = None
        self.vitals = self.place = self.arriving = None
        self.bounty = self.identity = self.identity_parts = None
        self.worn = self.worn_parts = None
        self.dead = False
        self.dirty = True

    def checkpoint(self):
        """A new checkpoint holds each quest's entries up to its index; the latest is enough."""
        for quest, indices in self.journal.items():
            if len(indices) > 1:
                self.journal[quest] = indices[-1:]
                self.dirty = True

    def keep_place(self, body):
        """Keep where the player is (a STATE_BODY); written once it moved PLACE_NEAR or changed
        cell."""
        if not self.place or not same_place(self.place, body):
            self.dirty = True
        self.place = bytes(body)

    def take(self, data):
        """Keep one PLAYER event; what changed, for the log, or None."""
        kind = data[0] if data else 0
        if kind == PLAYER_ITEMS and len(data) > 3:
            part, parts, item, entries = unpack_items(data)
            if part == 0:
                self.arriving = (item, [], 0)
            if not self.arriving or self.arriving[0] != item or self.arriving[2] != part:
                self.arriving = None
                return None
            self.arriving = (item, self.arriving[1] + entries, part + 1)
            if part + 1 < parts:
                return None
            entries, self.arriving = self.arriving[1], None
            if entries:
                self.items[item] = entries
            else:
                self.items.pop(item, None)
            self.dirty = True
            return (f"carries {describe_contents([[item, *e] for e in entries])}"
                    if entries else f"no longer carries {item}")
        if kind == PLAYER_LEVEL and len(data) >= 1 + LEVEL.size:
            body = bytes(data[1:1 + LEVEL.size])
            if not finite(*LEVEL.unpack(body)[13:]):
                return None
            self.level, self.dirty = body, True
            return describe_level(body)
        if kind == PLAYER_SKILLS and len(data) >= 2:
            changed = []
            for i in range(min(data[1], (len(data) - 2) // SKILL.size)):
                skill, base, progress = SKILL.unpack_from(data, 2 + i * SKILL.size)
                if skill < len(SKILL_NAMES) and finite(base, progress):
                    self.skills[skill] = [base, progress]
                    changed.append(f"{SKILL_NAMES[skill]} {base:.0f} ({progress:.2f})")
            self.dirty = self.dirty or bool(changed)
            return ", ".join(changed) or None
        if kind == PLAYER_EFFECTS and len(data) >= 5:
            part, parts = struct.unpack_from('<HH', data, 1)
            if part == 0:
                self.effects_parts = (b"", 0, parts)
            pending = self.effects_parts
            if (not pending or not parts or part >= parts or pending[1:] != (part, parts)
                    or len(pending[0]) + len(data) - 5 > PLAYER_EFFECT_BYTES * PLAYER_EFFECTS_MAX):
                self.effects_parts = None
                return None
            body = pending[0] + bytes(data[5:])
            self.effects_parts = (body, part + 1, parts)
            if part + 1 < parts:
                return None
            self.effects_parts = None
            try:
                effects = unpack_player_effects(body)
            except ValueError:
                return None
            if effects == self.effects:
                return None
            self.effects, self.dirty = effects, True
            return f"has {len(effects)} active effects"
        if kind == PLAYER_MODIFIERS and len(data) >= 2:
            changed = []
            for i in range(min(data[1], (len(data) - 2) // MODIFIER.size)):
                stat, current = MODIFIER.unpack_from(data, 2 + i * MODIFIER.size)
                if stat < len(ATTRIBUTE_NAMES) + len(SKILL_NAMES) and finite(current):
                    self.modifiers[stat] = current
                    name = (ATTRIBUTE_NAMES + SKILL_NAMES)[stat]
                    changed.append(f"{name} current {current:.0f}")
            self.dirty = self.dirty or bool(changed)
            return ", ".join(changed) or None
        if kind == PLAYER_JOURNAL and len(data) >= 2:
            quests = unpack_journal(data)
            for quest, index in quests:
                indices = self.journal.setdefault(quest, [])
                if not indices or indices[-1] != index:
                    indices.append(index)
                    del indices[:-QUEST_INDICES]
            self.dirty = self.dirty or bool(quests)
            return "journal " + ", ".join(f"{q} {i}" for q, i in quests) if quests else None
        if kind == PLAYER_VITALS and len(data) >= 1 + VITALS.size:
            vitals = list(VITALS.unpack_from(data, 1))
            if not finite(*vitals):
                return None
            self.vitals, self.dirty = vitals, True
            return "now health {:.0f}, magicka {:.0f}, fatigue {:.0f}".format(*vitals)
        if kind == PLAYER_SPELLS and len(data) >= 4 and data[1] in (SPELLS_ADD, SPELLS_REMOVE):
            names = unpack_equipment(data[2:])
            if not names:
                if self.spells is None and data[1] == SPELLS_ADD:
                    self.spells, self.dirty = [], True
                    return "knows no spells"
                return None
            known = {name.lower(): name for name in (self.spells or [])}
            if data[1] == SPELLS_ADD:
                known.update((name.lower(), name) for name in names)
            else:
                for name in names:
                    known.pop(name.lower(), None)
            self.spells, self.dirty = list(known.values()), True
            return ("learned " if data[1] == SPELLS_ADD else "forgot ") + ", ".join(names)
        if kind == PLAYER_IDENTITY and len(data) > 3:
            part, parts = data[1], data[2]
            if part == 0:
                self.identity_parts = (b"", 0)
            if not self.identity_parts or self.identity_parts[1] != part or part >= parts:
                self.identity_parts = None
                return None
            body = self.identity_parts[0] + bytes(data[3:])
            self.identity_parts = (body, part + 1)
            if part + 1 < parts:
                return None
            self.identity_parts = None
            try:
                identity = unpack_player_identity(body)
            except ValueError:
                return None
            if identity == self.identity:
                return None
            self.identity, self.dirty = identity, True
            return describe_identity(identity)
        if kind == PLAYER_WORN and len(data) >= 3:
            part, parts = data[1], data[2]
            if part == 0:
                self.worn_parts = (b"", 0)
            if not self.worn_parts or self.worn_parts[1] != part or part >= parts:
                self.worn_parts = None
                return None
            body = self.worn_parts[0] + bytes(data[3:])
            self.worn_parts = (body, part + 1)
            if part + 1 < parts:
                return None
            self.worn_parts = None
            try:
                worn = unpack_worn(body)
            except ValueError:
                return None
            if worn == self.worn:
                return None
            self.worn, self.dirty = worn, True
            return "wears " + (", ".join(item for item, *_ in worn) or "nothing")
        return None

    def replay(self):
        """The kept state as events, in the order a console applies them."""
        events = pack_player_identity(self.identity) if self.identity else []
        events += [part for item, entries in sorted(self.items.items())
                   for part in pack_items(item, entries)]
        if self.worn is not None:
            events += pack_worn(self.worn)
        # Abilities change statistic bases; restore them before the absolute stat snapshots.
        if self.spells is not None:
            events += [bytes([PLAYER_SPELLS, SPELLS_SNAPSHOT]) + part
                       for part in pack_equipment(self.spells)]
        if self.effects is not None:
            events += pack_player_effects(self.effects)
        if self.level:
            events.append(bytes([PLAYER_LEVEL]) + self.level)
        if self.vitals:  # after LEVEL, which caps each current value at its base
            events.append(bytes([PLAYER_VITALS]) + VITALS.pack(*self.vitals))
        if self.place and not self.dead:  # the dead go to a marker instead
            events.append(bytes([PLAYER_PLACE]) + self.place)
        if self.bounty is not None:
            events.append(bytes([PLAYER_BOUNTY]) + struct.pack("<i", self.bounty))
        skills = sorted(self.skills.items())
        per = (EVENT_DATA - 2) // SKILL.size
        for i in range(0, len(skills), per):
            chunk = skills[i:i + per]
            events.append(bytes([PLAYER_SKILLS, len(chunk)]) + b"".join(
                SKILL.pack(skill, *values) for skill, values in chunk))
        modifiers = sorted(self.modifiers.items())
        per = (EVENT_DATA - 2) // MODIFIER.size
        for i in range(0, len(modifiers), per):
            chunk = modifiers[i:i + per]
            events.append(bytes([PLAYER_MODIFIERS, len(chunk)]) + b"".join(
                MODIFIER.pack(stat, current) for stat, current in chunk))
        events += pack_journal([(q, i) for q, indices in sorted(self.journal.items())
                                for i in indices])
        return events

    def character_file(self):
        """What a New Game builds the player from before its first frame; None without an
        identity. The rest comes with the replay after the console joins."""
        if not self.identity:
            return None
        events = pack_player_identity(self.identity)
        events += [part for item, entries in sorted(self.items.items())
                   for part in pack_items(item, entries)]
        if self.worn is not None:
            events += pack_worn(self.worn)
        if self.place and not self.dead:
            events.append(bytes([PLAYER_PLACE]) + self.place)
        return CHARACTER_MAGIC + b"".join(struct.pack("<H", len(e)) + e for e in events)

    def save(self):
        save_world(self.path, {"items": self.items,
                               "level": self.level.hex() if self.level else None,
                               "skills": {str(k): v for k, v in self.skills.items()},
                               "modifiers": {str(k): v for k, v in self.modifiers.items()},
                               "journal": self.journal, "vitals": self.vitals,
                               "place": self.place.hex() if self.place else None,
                               "spells": self.spells,
                               "effects": self.effects,
                               "bounty": self.bounty,
                               "identity": self.identity,
                               "worn": self.worn,
                               "dead": self.dead})
        self.dirty = False


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


def same_place(a, b):
    """Two STATE_BODYs in one cell within PLACE_NEAR of each other."""
    fa, xa, ya, za, _, ca = STATE_BODY.unpack_from(a)
    fb, xb, yb, zb, _, cb = STATE_BODY.unpack_from(b)
    return (fa & PLACE == fb & PLACE and (not fa & INTERIOR or ca == cb)
            and math.dist((xa, ya, za), (xb, yb, zb)) < PLACE_NEAR)


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


def assign_owners(previous, actors, players, cell_owners, now):
    """Each actor's owner: the player it fights if that player loads its cell, else the nearest
    player that does, since the engine runs actors only within aiDistance of its own player.
    previous: id -> (client, since); actors: id -> (cell, x, y, flags, combat target); players:
    client -> (loaded cells, x, y). A dead actor keeps its owner."""
    result = {}
    for actor, (key, x, y, flags, target) in actors.items():
        near = {c: math.hypot(x - px, y - py) for c, (loaded, px, py) in players.items()
                if key in loaded}
        if not near:
            continue
        fights = flags & ACTOR_IN_COMBAT and target in near
        want = target if fights else min(near, key=lambda c: (near[c], c))
        current, since = previous.get(actor, (cell_owners.get(key), now - OWNER_HOLD))
        if current not in near:
            current, since = want, now
        if (want != current and not flags & ACTOR_DEAD and now - since >= OWNER_HOLD
                and (fights or near[current] - near[want] > OWNER_MARGIN)):
            current, since = want, now
        result[actor] = (current, since)
    return result


def now_us():
    return int(time.perf_counter() * 1e6) & 0xFFFFFFFF


GHOST_PLUGIN = "TES3X Multiplayer.esp"
GHOST_CELL = "TES3X Ghosts"
ARRIVAL_CELL = "TES3X Arrival"  # tes3xnet.c's CHARGEN_CELL
GHOSTS = 8  # one per peer slot in tes3xnet.c
BOT_ID = 99


def record(tag, subs, flags=0):
    body = b"".join(name + struct.pack("<I", len(value)) + value for name, value in subs)
    return tag + struct.pack("<III", len(body), 0, flags) + body


def zstr(text):
    return text.encode("latin-1") + b"\0"


CHARGEN_SOURCE = """Begin CharGen
; Morrowind.esm's, except that a New Game joining a server starts in TES3X Arrival (the payload
; swaps [PreLoad] Cell 0) and stays there rather than going to the prison ship, and one started
; from a kept character (serve --load-state) stays in that character's cell.
DisablePlayerControls
DisablePlayerJumping
DisablePlayerViewSwitch
DisableVanityMode
DisablePlayerFighting
DisablePlayerMagic
if ( GetPCCell "TES3X Arrival" == 1 )
	Player->PositionCell 0, 0, 64, 0, "TES3X Arrival"
elseif ( GetPCCell "Imperial Prison Ship" == 1 )
	Player->PositionCell 61, -135, 24, 340, "Imperial Prison Ship"
	ChangeWeather "Bitter Coast Region" 1
endif
set CharGenState to 10
stopscript CharGen
End CharGen
"""


def chargen_script():
    """CHARGEN_SOURCE compiled as the Construction Set would: opcodes little-endian, names and
    strings behind a length byte, an if or else followed by the count of statements it skips."""
    def name(text):
        return bytes([len(text)]) + text.encode("latin-1")

    def op(code):
        return struct.pack("<H", code)

    def position_cell(x, y, z, angle, cell):
        return (op(0x010C) + name("player") + op(0x1005) + struct.pack("<4f", x, y, z, angle)
                + name(cell))

    def in_cell(cell):
        return name((b" X" + op(0x1112) + b" c" + name(cell) + b" == 1").decode("latin-1"))

    data = b"".join(op(c) for c in (0x10DE, 0x1140, 0x10E3, 0x114C, 0x115A, 0x115D))
    data += op(0x0106) + b"\x01" + in_cell(ARRIVAL_CELL)
    data += position_cell(0, 0, 64, 0, ARRIVAL_CELL)
    data += op(0x0108) + b"\x02" + in_cell("Imperial Prison Ship")
    data += position_cell(61, -135, 24, 340, "Imperial Prison Ship")
    data += op(0x1124) + name("Bitter Coast Region") + struct.pack("<h", 1)
    data += op(0x0109)
    data += op(0x0105) + b"G" + name("CharGenState") + name(" 10")
    data += op(0x101C) + name("CharGen") + op(0x0101)
    head = b"CharGen".ljust(32, b"\0") + struct.pack("<5I", 0, 0, 0, len(data), 0)
    return record(b"SCPT", [(b"SCHD", head), (b"SCDT", data),
                            (b"SCTX", CHARGEN_SOURCE.encode("latin-1"))])


def ghost_plugin(master_size, master="Morrowind.esm"):
    """The plugin tes3xnet.c moves: one persistent NPC per peer slot, parked in a cell of its own.
    They have no AI packages and zero fight, flee, alarm and hello, so they stand where put.
    Talking to a ghost would turn it to face the speaker, away from where its player faces: the
    payload refuses the player's activation of one, and Morrowind.esm's noPickUp script swallows
    the rest where the ghost has its script variables."""
    hedr = (struct.pack("<fI", 1.3, 0) + b"TES3X".ljust(32, b"\0")
            + b"Other players, placed by the multiplayer patch.".ljust(256, b"\0")
            + struct.pack("<I", GHOSTS + 3))
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
    out.append(chargen_script())
    cell = [(b"NAME", zstr(GHOST_CELL)), (b"DATA", struct.pack("<Iii", 1, 0, 0)),
            (b"WHGT", struct.pack("<f", 0)), (b"AMBI", struct.pack("<3If", 0x404040, 0, 0, 0))]
    for i in range(1, GHOSTS + 1):
        cell += [(b"FRMR", struct.pack("<I", i)), (b"NAME", zstr(f"tes3x_ghost{i}")),
                 (b"DATA", struct.pack("<6f", 128.0 * i, 0, 0, 0, 0, 0))]
    out.append(record(b"CELL", cell))
    # A new character stands here, out of the shared world, while choosing race, class and the
    # rest; the floor keeps it from falling the whole time.
    out.append(record(b"CELL", [
        (b"NAME", zstr(ARRIVAL_CELL)), (b"DATA", struct.pack("<Iii", 1, 0, 0)),
        (b"WHGT", struct.pack("<f", 0)), (b"AMBI", struct.pack("<3If", 0x808080, 0x808080, 0, 0)),
        (b"FRMR", struct.pack("<I", GHOSTS + 1)), (b"NAME", zstr("In_Lava_Blacksquare")),
        (b"DATA", struct.pack("<6f", 0, 0, 0, 0, 0, 0))]))
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

    def __init__(self, burst, rate, now=None):
        self.burst, self.rate = burst, rate
        self.tokens, self.last = float(burst), time.monotonic() if now is None else now

    def take(self, now):
        self.tokens = min(self.burst, self.tokens + (now - self.last) * self.rate)
        self.last = now
        if self.tokens < 1:
            return False
        self.tokens -= 1
        return True

    def available(self, now):
        """Whether take() would succeed now, without spending a token."""
        return min(self.burst, self.tokens + (now - self.last) * self.rate) >= 1


class Client:
    def __init__(self, ident, mac):
        self.id, self.mac = ident, mac
        self.session = self.seq = self.peer_seq = self.peer_time = 0
        self.addr = None
        self.joins = self.beats = self.gaps = self.states = 0
        self.state = None
        self.last = time.monotonic()
        self.alive = False
        self.version = T3MP_VERSION
        self.dead = False  # its player died and has not respawned; relayed to peers
        self.rel = Reliable()
        self.events = 0
        self.known = {}  # cell -> the authority this client was told
        self.owners_told = {}  # actor id -> the owner this client was told, where not 0
        self.loaded = set()
        self.busy = None  # since when it has been saving
        self.lobby = False  # joined from the main menu, with no game
        self.game = None  # the launch token it last reported
        self.synced = False  # that launch runs its character's latest checkpoint
        self.launch = 0  # GAME_NONE, GAME_LOAD or GAME_NEW
        self.character = None  # the folder of the character that launch runs
        self.announced = False  # the others were told it joined
        self.announce_due = False  # that waits for its character's name
        self.relaunching = False  # it was sent a character to load and is about to relaunch
        self.rebuild = None  # (checkpoint, when to ask for the save) under --rebuild
        self.naming = False
        self.snapshots = 0
        self.snapshot_request = 0x80000000
        self.snapshot_saved = None
        self.place_hold = None  # (replayed place, until when) while STATE still shows the old one
        self.listed = []  # the folders CHARS offered, in order
        self.actor_states = 0
        self.flush_due = False  # an EVENTS packet held back by EVENTS_GAP
        self.queue = []  # (address, packet, seq) held back by PACE_PACKETS
        self.window = (0.0, 0)  # the current PACE_WINDOW's start and packets sent in it
        self.joined = 0.0
        self.bursts = []  # (seconds after joining, packets) still to send
        self.bulk = None  # the Outgoing file of --send, offered again on each join
        self.upload = None  # the Incoming file this console is sending
        self.kept = 0  # saves kept from it as its character
        self.key = None  # the console's static key for this server: its identity
        self.keys = None  # (console to server, server to console) from the handshake
        self.replay = (0, 0)  # the highest seq opened and a bitmap of the 32 up to it
        self.forged = self.replayed = self.limited = 0
        self.bucket = Bucket(*CLIENT_RATE)

    @property
    def in_world(self):
        """Joined with a game loaded: it gets the world, the others' states and events."""
        return self.alive and not self.lobby



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


def ban_value(kind, value):
    """A ban's value in the form the server compares, or None: a key's fingerprint, a MAC, an
    IPv4 address."""
    value = value.lower()
    if kind == "key":
        ok = len(value) == 32 and all(c in "0123456789abcdef" for c in value)
    elif kind == "mac":
        parts = value.split(":")
        ok = len(parts) == 6 and all(len(x) == 2 and all(c in "0123456789abcdef" for c in x)
                                     for x in parts)
    elif kind == "address":
        try:
            ok = socket.inet_ntoa(socket.inet_aton(value)) == value
        except OSError:
            ok = False
    else:
        ok = False
    return value if ok else None


def load_bans(path):
    """{kind: set of values} from bans.txt: one "KIND VALUE" per line, a note may follow."""
    bans = {kind: set() for kind in BAN_KINDS}
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as stream:
            for line in stream:
                words = line.split()
                if len(words) >= 2 and ban_value(words[0], words[1]):
                    bans[words[0]].add(ban_value(words[0], words[1]))
    return bans


def save_bans(path, bans):
    if path:
        with open(path + ".tmp", "w", encoding="utf-8") as stream:
            for kind in BAN_KINDS:
                for value in sorted(bans[kind]):
                    stream.write(f"{kind} {value}\n")
        os.replace(path + ".tmp", path)


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
    deadline = time.monotonic() + args.duration if args.duration else None
    report = time.monotonic() + args.report
    loss = random.Random(args.seed)
    pinned = None
    if args.load_order:
        pinned = (int(args.load_order, 16), None)
    lost = {"in": 0, "out": 0}
    clock, clock_next = None, 0.0
    save_next = time.monotonic() + args.save_every if args.save_every else math.inf
    owners = {}  # cell -> authority client
    actor_owners = {}  # actor id -> (client, since): its owner by proximity
    actor_seen = {}  # actor id -> when a state of it last came
    dialogues = {}  # actor id -> (talking client, authority client)
    deaths = {}  # refid -> the client that reported it; replayed to each joining client
    # client -> [parts of its latest whole equipment set, parts of the set arriving]
    equipment = {}
    identities = {}  # client -> its complete [name/race, head/hair] parts
    actor_equipment = {}  # actor id -> (authority, complete parts, arriving parts)
    bounties = {}
    if args.bot:
        identities[BOT_ID] = pack_identity("Bot", "Imperial", "b_n_imperial_m_head_01",
                                           "b_n_imperial_m_hair_01")
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
    detail = {"verbose": args.log == "verbose"}  # per-tick state changes in the console
    streams = {}  # character folder -> PlayerStream
    streams_saved = 0.0
    starts = load_starts(args.starts or STARTS)
    creating = set()  # key fingerprints making a new character
    bot_spawns = []
    for spec in args.bot_spawn:
        what, _, at = spec.rpartition("@")
        name, cell, *condition = what.split(":")
        bot_spawns.append((float(at), name, int(cell), int(condition[0]) if condition else None))
    bot_takes = [float(at) for at in args.bot_take]
    bot_fights = {int(refid, 16): int(client) for refid, _, client in
                  (spec.partition(":") for spec in args.bot_fights)}
    bot_weather = []
    bot_statuses = list(args.bot_status)
    bot_affects = list(args.bot_affect)
    bot_spells = []
    bot_bounties = [(float(at), int(value)) for value, _, at in
                    (s.rpartition("@") for s in args.bot_bounty)]
    bot_shots = [(float(at), ammo) for ammo, _, at in (s.rpartition("@") for s in args.bot_shoot)]
    for kind, specs in ((EVENT_SPELL, args.bot_spell), (EVENT_CAST, args.bot_cast)):
        for spec in specs:
            cast, _, at = spec.partition("@")
            name, _, refid = cast.partition(":")
            bot_spells.append((float(at), kind, name,
                               None if refid == "none" else int(refid, 16) if refid else 0))
    for spec in args.bot_weather:
        change, _, at = spec.partition("@")
        index, _, value = change.partition(":")
        bot_weather.append((float(at), int(index), int(value)))
    authority_next = actor_next = 0.0
    server_secret = load_server_key(args)
    password = load_password(args)
    admitted_path = os.path.join(args.world, "admitted.txt") if args.world else None
    admitted, password_buckets = load_admitted(admitted_path), {}
    bans_path = os.path.join(args.world, "bans.txt") if args.world else None
    bans = load_bans(bans_path)
    admin_sock, commands = None, queue.Queue()
    admin_port = ADMIN_PORT if args.admin_port is None else args.admin_port
    if admin_port:
        admin_sock = udp_socket()
        try:
            admin_sock.bind(("127.0.0.1", admin_port))
            print(f"admin commands on 127.0.0.1:{admin_port} (tes3x_net.py admin)"
                  + (", and here" if sys.stdin and sys.stdin.isatty() else ""), flush=True)
        except OSError as error:
            admin_sock = None
            print(f"no admin port: 127.0.0.1:{admin_port}: {error}", flush=True)
    remote_admin = None
    if args.remote_admin:
        password_path = args.admin_password_file or (
            os.path.join(args.world, "admin-password.txt") if args.world else None)
        if not password_path or not os.path.isfile(password_path):
            sys.exit("--remote-admin needs --admin-password-file, or admin-password.txt in --world")
        remote_sock = udp_socket()
        remote_sock.bind((args.bind, args.remote_admin))
        remote_admin = RemoteAdmin(admin_secret(load_admin_password(password_path)), remote_sock)
        print(f"remote admin on {args.bind}:{args.remote_admin}, with the password in "
              f"{password_path}", flush=True)
    if sys.stdin and sys.stdin.isatty():
        threading.Thread(target=lambda: [commands.put(line) for line in sys.stdin],
                         daemon=True).start()
    if password:
        print(f"password asked of new consoles; {len(admitted)} admitted"
              + ("" if admitted_path else " (not kept: give --world)"), flush=True)
    handshake_bucket, handshake_buckets = Bucket(*HANDSHAKE_RATE_ALL), {}
    limits = {"handshakes": 0}
    pending = {}  # session -> a handshake in progress or just done: {"noise", "e", "reply", ...}
    bursts = [(float(at), int(count)) for count, _, at in
              (spec.partition("@") for spec in args.burst)]
    build_server = None
    if args.build:
        build = tes3x_netbuild.Build(args.build, args.deltas,
                                     args.serve_origin or tes3x_netbuild.SERVED_BY_DEFAULT)
        build_server = tes3x_netbuild.BuildServer(
            build, args.bind, args.port if args.http_port is None else args.http_port)
        print(f"{build.describe()}; HTTP on {args.bind}:{build_server.port}", flush=True)
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
        outer = OUTER.pack(b"T3MP", client.version, SEALED, 0, client.session, client.seq)
        packet = outer + seal(client.keys[1], client.seq, outer, inner)
        if dropped("out"):
            return
        client.queue.append((client.addr, packet, client.seq))
        pump(client)

    def transmit(addr, packet, ident=0):
        if len(addr) == 4:  # a tunnel guest, by its MAC
            ip, port, mac, link = addr
            link.send(udp_frame(mac, ip, packet, ident, sport=args.port, dport=port))
        else:
            sock.sendto(packet, addr)

    def pump(client):
        """Send what PACE_PACKETS allows of the client's queue."""
        now = time.monotonic()
        start, count = client.window
        if now - start >= PACE_WINDOW:
            start, count = now, 0
        while client.queue and count < PACE_PACKETS:
            addr, packet, seq = client.queue.pop(0)
            transmit(addr, packet, seq)
            count += 1
        client.window = (start, count)

    bot = {"anchor": None, "next": 0.0, "start": time.monotonic(), "said": 0.0, "line": 0,
           "anchored": None, "state": None, "breaks": [], "held": 0, "hit": False,
           "killed": False, "mirror": None, "hit_player": False, "echo": None, "busy": False,
           "dead": False}

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
                bot["anchored"] = time.monotonic()
            print(f"{time.strftime('%H:%M:%S')} bot circles {describe_state(state)}",
                  flush=True)

    def bot_step(now):
        (flags, cell), cx, cy, cz = bot["anchor"]
        if args.bot_at:
            dx, dy = (float(v) for v in args.bot_at.split(","))
            cx, cy = cx + dx, cy + dy
        t = (now - bot["start"]) * 2 * math.pi / args.bot_period
        if bot["mirror"]:
            _, x, y, z, heading, _, actor_flags, _, _, _, anim = ACTOR.unpack(bot["mirror"])
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
            if other.in_world:
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
        for spawn in spawns.values():
            if spawn.get("summon"):
                spawn["removed"] = True  # Active effects recreate summons for their target.
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

    def notify(text, now, only=None, skip=None):
        """Show text on the screen of every console in the world, or of one client."""
        data = text[:EVENT_DATA].encode("latin-1", "replace")
        for other in clients.values():
            if other.in_world and other is not skip and (only is None or other is only):
                other.rel.queue(EVENT_TEXT, 0, data)
                flush(other, now)

    def player_name(client):
        parts = identities.get(client.id)
        if parts and parts[0]:
            return unpack_identity(parts[0])[2]
        return client.character or f"Player {client.id}"

    def announce_join(client, now):
        """Tell the others once the character's name is known, or after a few seconds without."""
        client.relaunching = False
        if client.announced or client.lobby:
            return
        parts = identities.get(client.id)
        if not (parts and parts[1]) and now - client.joined < ANNOUNCE_WAIT:
            client.announce_due = True
            return
        client.announced, client.announce_due = True, False
        notify(f"{player_name(client)} has joined.", now, skip=client)
        if args.welcome:
            client.rel.queue(EVENT_WELCOME, 0, args.welcome[:EVENT_DATA].encode("latin-1", "replace"))
            flush(client, now)

    def leave(client):
        # a relaunch to load a character is not a departure
        if client.in_world and client.announced and not client.relaunching:
            notify(f"{player_name(client)} has left.", time.monotonic(), skip=client)
            client.announced = False
        client.alive = False
        for refid, (holder, target) in list(dialogues.items()):
            if holder == client.id:
                del dialogues[refid]
                if target != holder:
                    send_event(target, holder, EVENT_HOLD,
                               struct.pack("<III", refid, target, 0), time.monotonic())
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
            if other.in_world and other.id != origin:
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
            if other.in_world and (to_origin or other.id != origin):
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
            if other.in_world:
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
            if other.in_world and other.id != origin:
                send_contents(other.id, refid, now)

    def key_folder(client):
        return (os.path.join(args.world, "characters", fingerprint(client.key))
                if args.world and client.key else None)

    def manager_character(key):
        """The character this key plays on the server: the one in the world now, else the newest
        kept; what the console manager lists beside the server."""
        for other in clients.values():
            if other.alive and other.key == key and (
                    other.character or identities.get(other.id)):
                return player_name(other)
        root = (os.path.join(args.world, "characters", fingerprint(key))
                if args.world else None)
        kept = kept_characters(root, fixtures=args.adopt or args.rebuild is not None)
        if not kept:
            return ""
        try:
            return PlayerStream(os.path.join(root, kept[0][0], STREAM_NAME)).identity["name"]
        except (OSError, ValueError, TypeError, KeyError):
            return kept[0][0]

    def character_folder(client):
        root = key_folder(client)
        return os.path.join(root, client.character) if root and client.character else None

    def player_stream(client):
        folder = character_folder(client)
        if folder is None:
            return None
        if folder not in streams:
            streams[folder] = PlayerStream(os.path.join(folder, STREAM_NAME))
        return streams[folder]

    def keep_place(client, now):
        """The character's last place, from STATE, once a replayed place has been reached."""
        stream = player_stream(client) if client.synced else None
        body = client.state[:STATE_BODY.size]
        if stream is None or not STATE_BODY.unpack_from(body)[0] & IN_WORLD:
            return
        if client.place_hold:
            kept, until = client.place_hold
            if not same_place(kept, body) and now < until:
                return
            if not same_place(kept, body):
                print(f"{time.strftime('%H:%M:%S')} client {client.id} did not reach "
                      f"{describe_state(kept)}", flush=True)
            client.place_hold = None
        stream.keep_place(body)

    def respawn(client, delay, now):
        """Tell a dead player's console when and where to come back, and what it loses."""
        stream = player_stream(client) if client.synced else None
        gold = sum(e[0] for item, entries in stream.items.items() if item.lower() == "gold_001"
                   for e in entries) if stream else 0
        lost = gold * args.death_gold // 100
        client.rel.queue(EVENT_PLAYER, 0, bytes([PLAYER_RESPAWN]) + RESPAWN.pack(
            int(delay * 1000), RESPAWN_PLACES[args.respawn], lost))
        flush(client, now)
        return lost

    def on_player_death(client, alive, stamp, now):
        stream = player_stream(client) if client.synced else None
        name = player_name(client)
        client.dead = not alive
        if stream:
            stream.dead, stream.dirty = not alive, True
        life = bytes([PLAYER_ALIVE if alive else PLAYER_DEATH])
        for other in clients.values():
            if other.in_world and other is not client:
                other.rel.queue(EVENT_PLAYER, client.id, life)
        if alive:
            print(f"{stamp} client {client.id} ({name}) is back", flush=True)
            for other in clients.values():
                if other.in_world and other is not client:
                    flush(other, now)
            return
        lost = respawn(client, args.respawn_delay, now)
        print(f"{stamp} client {client.id} ({name}) died: respawns at the {args.respawn} marker "
              f"in {args.respawn_delay:g} s, loses {lost} gold", flush=True)
        notice = f"{name} has died."[:EVENT_DATA].encode("latin-1", "replace")
        for other in clients.values():
            if other.in_world and other is not client:
                other.rel.queue(EVENT_TEXT, 0, notice)
                flush(other, now)

    def player_ready(client, replay, stamp, now):
        """Replay retained state, or have the console publish its supported fields."""
        announce_join(client, now)
        if replay:
            for sid, spawn in list(spawns.items()):
                if spawn.get("summon") and spawn["origin"] == client.id and not spawn["removed"]:
                    remove_spawn(client.id, sid, stamp, now, to_origin=True)
        stream = player_stream(client)
        if stream is None:
            return
        events = stream.replay() if replay else []
        client.place_hold = (stream.place, now + PLACE_HOLD) if (
            replay and stream.place and not stream.dead) else None
        for data in events:
            client.rel.queue(EVENT_PLAYER, 0, data)
        if replay and stream.dead:
            print(f"{stamp} client {client.id} died before its last stop: respawns now",
                  flush=True)
            respawn(client, 0, now)
        client.rel.queue(EVENT_PLAYER, 0, bytes([PLAYER_READY, 0]))
        flush(client, now)
        print(f"{stamp} client {client.id}: " + (
            "replayed " + (f"{stream.identity['name']}'s identity, " if stream.identity else "")
            + f"{len(stream.items)} items, {len(stream.skills)} skills, "
            f"{len(stream.journal)} quests" + (", the level" if stream.level else "")
            + (f", the place ({describe_state(stream.place)})" if stream.place else "")
            if replay else "streams its player from scratch"), flush=True)

    def send_names(client, kind, names, now):
        for part in pack_names(names):
            client.rel.queue(kind, 0, part)
        flush(client, now)

    def offer_starts(client, stamp, now):
        """Have the console make a character: in this launch if it is a New Game, else it
        relaunches into one and is offered the start points again."""
        creating.add(fingerprint(client.key))
        client.synced = client.launch == GAME_NEW
        send_names(client, EVENT_NEWCHAR, [name for name, _ in starts], now)
        print(f"{stamp} client {client.id} makes a new character"
              + ("" if client.synced else ", after a New Game"), flush=True)

    def send_character(client, folder, path, loaded, stamp, now):
        """The character seed for a New Game; a missing identity cannot load a character.
        Keep its name in the folder to recognise the next launch after a restart."""
        if folder not in streams:
            streams[folder] = PlayerStream(os.path.join(folder, STREAM_NAME))
        data = streams[folder].character_file()
        if data is None:
            client.rel.queue(EVENT_TEXT, 0, b"This character has no retained identity.")
            flush(client, now)
            return
        name = checkpoint_name(data, CHARACTER_FILE)
        for f in os.listdir(folder):
            if f.lower().endswith(".t3c") and f != name:
                os.remove(os.path.join(folder, f))
        with open(os.path.join(folder, name), "wb") as stream:
            stream.write(data)
        client.bulk = Outgoing(name, data)
        client.rel.queue(EVENT_OFFER, 0, client.bulk.offer())
        client.rel.queue(EVENT_LOAD, 0, zstr(name))
        client.relaunching = True
        flush(client, now)
        print(f"{stamp} client {client.id} loaded {loaded or 'no save'}: sending "
              f"{os.path.basename(folder)}'s state as {name} ({len(data)} bytes) to start from",
              flush=True)

    def on_game(client, token, loaded, launch, stamp, now):
        """A console's launch: it runs one of its key's characters, or chooses one, or makes
        one."""
        if token != client.game:
            client.game, client.synced, client.character = token, False, None
            client.launch, client.rebuild = launch, None
        making = client.key and fingerprint(client.key) in creating
        if client.synced:  # a rejoin of the same launch: events in flight were dropped
            if client.character:
                player_ready(client, False, stamp, now)
            elif making:
                offer_starts(client, stamp, now)
            return
        kept = kept_characters(key_folder(client), fixtures=args.adopt or args.rebuild is not None)
        for folder, path in kept:
            if loaded.lower().endswith(".t3c") and os.path.exists(
                    os.path.join(key_folder(client), folder, loaded.lower())):
                client.synced, client.character = True, folder
                creating.discard(fingerprint(client.key))
                if args.rebuild is not None:
                    client.rebuild = (path, now + args.rebuild)
                print(f"{stamp} client {client.id} runs {folder}, started from {loaded}",
                      flush=True)
                player_ready(client, True, stamp, now)
                return
            if args.rebuild is not None and path.lower().endswith(".ess"):
                with open(path, "rb") as stream:
                    matches = loaded.lower() == checkpoint_name(stream.read())
                if matches:
                    client.synced, client.character = True, folder
                    creating.discard(fingerprint(client.key))
                    print(f"{stamp} client {client.id} runs {folder} ({os.path.basename(path)})",
                          flush=True)
                    player_ready(client, True, stamp, now)
                    return
        if args.rebuild is not None and kept:
            client.character, path = kept[0]
            client.rebuild = (path, now + args.rebuild)
            print(f"{stamp} client {client.id} loaded {loaded or 'no save'}: rebuilding "
                  f"{client.character} over it from the kept state alone", flush=True)
            player_ready(client, True, stamp, now)
            return
        if making or (not kept and not args.adopt and key_folder(client)):
            offer_starts(client, stamp, now)
            return
        if not kept:
            client.synced = True
            print(f"{stamp} client {client.id} has no kept character", flush=True)
            return
        if args.adopt:
            send_character(client, os.path.join(key_folder(client), kept[0][0]), kept[0][1],
                           loaded, stamp, now)
            return
        client.listed = [folder for folder, _ in kept[:CHARACTERS_LISTED]]
        send_names(client, EVENT_CHARS, client.listed, now)
        print(f"{stamp} client {client.id} loaded {loaded or 'no save'}: offered "
              f"{', '.join(client.listed)}", flush=True)

    def compare_rebuild(client, stamp):
        """Diff a rebuilt character's save against its checkpoint, by coverage row."""
        import contextlib
        import io
        from tes3x_ess import report_diff
        checkpoint, _ = client.rebuild
        out = client.upload.path[:-4] + ".diff.txt"
        text = io.StringIO()
        try:
            with contextlib.redirect_stdout(text):
                count = report_diff(checkpoint, client.upload.path, 1000)
        except ValueError as exc:
            print(f"{stamp} client {client.id} rebuilt {client.character}, but the saves do "
                  f"not compare: {exc}", flush=True)
            return
        with open(out, "w", encoding="utf-8") as f:
            f.write(text.getvalue())
        print(f"{stamp} client {client.id} rebuilt {client.character}: {count} differences "
              f"from {os.path.basename(checkpoint)}, in {out}", flush=True)

    def spymaster_done():
        """Whether any kept character has finished the first main quest, which takes the package
        away from Caius's desk: its journal reached the closing index."""
        folders = list(streams)
        if args.world:
            for path in glob.glob(os.path.join(args.world, "characters", "*", "*", STREAM_NAME)):
                folders.append(os.path.dirname(path))
        for folder in dict.fromkeys(folders):
            stream = streams.get(folder) or PlayerStream(os.path.join(folder, STREAM_NAME))
            for quest, indices in stream.journal.items():
                if quest.lower() == SPYMASTER_QUEST and any(i >= SPYMASTER_DONE for i in indices):
                    return True
        return False

    def new_character_kit():
        """What chargen's Sellus Gravius would have given: the starting gold, and unless another
        character has already delivered it, Caius Cosades's package with the quest."""
        lines = [f'Player->AddItem "Gold_001" {args.start_gold}'] if args.start_gold else []
        if not spymaster_done():
            lines += [f'Player->AddItem "{CAIUS_PACKAGE}" 1',
                      f"Journal {SPYMASTER_QUEST} {SPYMASTER_GIVEN}"]
        return lines

    def on_pick(client, what, index, stamp, now):
        if what == PICK_CHARACTER and index == PICK_NEW:
            offer_starts(client, stamp, now)
        elif what == PICK_CHARACTER and index < len(client.listed):
            creating.discard(fingerprint(client.key))
            folder = os.path.join(key_folder(client), client.listed[index])
            path = os.path.join(folder, STREAM_NAME)
            if os.path.isfile(path):
                send_character(client, folder, path, "the list", stamp, now)
        elif (what == PICK_START and index < len(starts) and client.synced
              and client.character is None):
            folder = new_character_folder(key_folder(client), "character")
            os.makedirs(folder)
            client.character = os.path.basename(folder)
            client.naming = True
            player_stream(client).reset()
            player_ready(client, False, stamp, now)
            name, lines = starts[index]
            for line in lines + new_character_kit() + [""]:
                client.rel.queue(EVENT_RUN, 0, zstr(line))
            flush(client, now)
            print(f"{stamp} client {client.id} starts at {name}", flush=True)

    def received(client, stamp, now):
        """Adopt or compare an uploaded save only in an explicit diagnostic session."""
        if not client.upload.name.lower().endswith(".ess") or not (args.adopt or args.rebuild is not None):
            return
        if client.rebuild and client.rebuild[1] is None:
            compare_rebuild(client, stamp)
            return
        if not client.synced:
            print(f"{stamp} client {client.id} sent {client.upload.name} from a game that is not "
                  f"its character's; left in uploads", flush=True)
            return
        new = client.character is None
        player = save_player(client.upload.path)
        if not player:
            print(f"{stamp} client {client.id} sent {client.upload.name}, not a save; left in "
                  f"uploads", flush=True)
            return
        folder = (new_character_folder(key_folder(client), player) if new
                  else character_folder(client))
        head = keep_character(client.upload.path, folder)
        print(f"{stamp} client {client.id} kept {client.upload.name}: {head['player']} in "
              f"{head['cell']}, {len(head['masters'])} masters"
              + (f", a new character in {os.path.basename(folder)}" if new else ""), flush=True)
        client.kept += 1
        if new:
            client.character = os.path.basename(folder)
            creating.discard(fingerprint(client.key))
            player_ready(client, False, stamp, now)
        else:
            player_stream(client).checkpoint()

    def on_event(client, kind, data, stamp, now):
        client.events += 1
        if kind in SERVER_EVENTS:
            print(f"{stamp} client {client.id} sent server event {kind}: dropped", flush=True)
            return
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
            if client.upload.status == BULK_DONE:
                received(client, stamp, now)
            return
        if kind == EVENT_PLAYER and data[:1] in (bytes([PLAYER_DEATH]), bytes([PLAYER_ALIVE])):
            on_player_death(client, data[0] == PLAYER_ALIVE, stamp, now)
            return
        if kind == EVENT_SNAPSHOT and len(data) == 4 + STATE_BODY.size:
            stream = player_stream(client) if client.synced else None
            body = data[4:]
            flags, x, y, z, heading, cell = STATE_BODY.unpack(body)
            if (stream is None or not stream.identity or not flags & IN_WORLD or
                    not placeable(x, y, z) or not finite(heading) or stream.arriving or
                    stream.identity_parts or stream.worn_parts or stream.effects_parts):
                return
            stream.keep_place(body)
            stream.save()
            if world["path"]:
                write_world(now)
            client.snapshots += 1
            client.snapshot_saved = struct.unpack_from("<I", data)[0]
            client.rel.queue(EVENT_SNAPSHOT, 0, data[:4])
            flush(client, now)
            return
        if kind == EVENT_PLAYER:
            stream = player_stream(client) if client.synced else None
            change = stream.take(data) if stream else None
            if stream and client.naming and stream.identity:
                old = character_folder(client)
                folder = new_character_folder(key_folder(client), stream.identity["name"])
                stream.save()
                os.rename(old, folder)
                streams.pop(old)
                stream.path = os.path.join(folder, STREAM_NAME)
                streams[folder] = stream
                client.character, client.naming = os.path.basename(folder), False
                creating.discard(fingerprint(client.key))
            if change and detail["verbose"]:
                print(f"{stamp} client {client.id} {change}", flush=True)
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
            if kind == EVENT_HOLD:
                on = struct.unpack_from("<I", data, 8)[0]
                held = dialogues.get(refid)
                if on and held and held[0] != client.id:
                    print(f"{stamp} client {client.id} is refused dialogue with {refid:#010x}: "
                          f"client {held[0]} is talking", flush=True)
                    send_event(client.id, 0, EVENT_HOLD_BROKEN,
                               struct.pack("<III", refid, client.id, 3), now)
                    return
                if on:
                    dialogues[refid] = (client.id, target)
                elif held and held[0] == client.id:
                    del dialogues[refid]
            if target != BOT_ID:
                if kind != EVENT_HOLD or target != client.id:
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
            if detail["verbose"]:
                print(f"{stamp} client {client.id}: {describe_status(refid, values)}", flush=True)
        if kind == EVENT_AFFECT and len(data) > 5:
            refid, index = struct.unpack_from("<IB", data)
            name = wire_text(data[5:].split(b"\0")[0])
            if detail["verbose"]:
                print(f"{stamp} client {client.id}: {refid:#010x} takes effect {index} of "
                      f"{name}", flush=True)
        if kind == EVENT_OBJECTS and data:
            changed = unpack_objects(data)
            objects.update(changed)
            world["dirty"] = True
            for refid, rest in changed.items():
                if detail["verbose"]:
                    print(f"{stamp} client {client.id}: {describe_object(refid, *rest)}",
                          flush=True)
        if kind == EVENT_TEXT:
            print(f"{stamp} client {client.id} says: {wire_text(data)}", flush=True)
        if kind == EVENT_GAME and len(data) >= 5:
            loaded, _, rest = data[4:].partition(b"\0")
            on_game(client, struct.unpack_from("<I", data)[0], wire_text(loaded),
                    rest[0] if rest else GAME_NONE, stamp, now)
            return
        if kind == EVENT_PICK and len(data) >= 2:
            on_pick(client, data[0], data[1], stamp, now)
            return
        if kind == EVENT_BUSY and data:
            if data[0] and client.busy is None:
                client.busy = now
                print(f"{stamp} client {client.id} is saving: its cells and actors go to others",
                      flush=True)
            elif not data[0] and client.busy is not None:
                print(f"{stamp} client {client.id} is back after {now - client.busy:.1f} s",
                      flush=True)
                client.busy = None
        if kind == EVENT_BOUNTY and len(data) >= 4:
            bounty = struct.unpack_from("<i", data)[0]
            bounties[client.id] = data[:4]
            stream = player_stream(client) if client.synced else None
            if stream and stream.bounty != bounty:
                stream.bounty, stream.dirty = bounty, True
            print(f"{stamp} client {client.id} bounty {bounty}",
                  flush=True)
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
        if kind == EVENT_IDENTITY:
            try:
                part, female, first, second = unpack_identity(data)
            except ValueError:
                return
            parts = identities.get(client.id)
            if part == 0:
                parts = identities[client.id] = [data, None]
            elif not parts or not parts[0] or bool(parts[0][2]) != female:
                return
            else:
                parts[1] = data
            if parts[1]:
                name, race = unpack_identity(parts[0])[2:]
                head, hair = unpack_identity(parts[1])[2:]
                print(f"{stamp} client {client.id} is {name}: {race}, {head}, {hair}",
                      flush=True)
                if client.announce_due:
                    announce_join(client, time.monotonic())
        if kind == EVENT_ACTOR_EQUIPMENT:
            try:
                refid, part, count, items = unpack_actor_equipment(data)
            except ValueError:
                return
            have = actor_equipment.get(refid)
            if part == 0:
                have = actor_equipment[refid] = [client.id, have[1] if have else [], []]
            elif not have or have[0] != client.id or len(have[2]) != part or \
                    have[2][0][5] != count:
                return
            have[2].append(data)
            if part + 1 == count:
                have[1], have[2] = have[2], []
                worn = [item for body in have[1] for item in unpack_actor_equipment(body)[3]]
                print(f"{stamp} client {client.id}: {refid:#010x} wears {len(worn)}: "
                      f"{', '.join(worn)}", flush=True)
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
        busy = {c.id for c in clients.values() if c.busy is not None}
        for key, cands in candidates.items():
            if any(c not in busy for c, _ in cands):
                candidates[key] = [c for c in cands if c[0] not in busy]
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
        update_owners(now, forced)

    def update_owners(now, forced):
        """Name each actor's owner by proximity and tell every client that loads its cell where
        that differs from the cell's authority."""
        players = {}
        for client in clients.values():
            if client.alive and client.state and client.loaded and client.busy is None:
                players[client.id] = (client.loaded,) + STATE_BODY.unpack_from(client.state)[1:3]
        if args.bot_at and bot["state"] and not bot["busy"]:
            players[BOT_ID] = (cell_keys(bot["state"])[1],) + \
                STATE_BODY.unpack_from(bot["state"])[1:3]
        for refid in [r for r, seen in actor_seen.items() if now - seen > OWNER_STALE]:
            del actor_seen[refid]
        live = {}
        for refid in actor_seen:
            spawn = spawns.get(refid)
            if spawn and spawn.get("summon"):
                continue  # run by its maker
            _, key, record = actors[refid]
            values = ACTOR.unpack(record)
            live[refid] = (key, values[1], values[2], values[6], values[9])
        new = {} if forced else assign_owners(actor_owners, live, players, owners, now)
        stamp = time.strftime("%H:%M:%S")
        for refid, (client_id, _) in new.items():
            if actor_owners.get(refid, (None,))[0] not in (None, client_id):
                print(f"{stamp} actor {refid:#010x} owned by client {client_id}", flush=True)
        actor_owners.clear()
        actor_owners.update(new)
        for client in clients.values():
            if not client.alive:
                continue
            changes = []
            for refid, (client_id, _) in new.items():
                want = client_id if client_id != owners.get(live[refid][0]) else 0
                if live[refid][0] in client.loaded and client.owners_told.get(refid, 0) != want:
                    changes.append((refid, want))
            for refid, told in client.owners_told.items():
                if told and (refid not in new or live[refid][0] not in client.loaded):
                    changes.append((refid, 0))
            for refid, want in changes:
                if want:
                    client.owners_told[refid] = want
                else:
                    client.owners_told.pop(refid, None)
            for i in range(0, len(changes), OWNERS_PER_EVENT):
                chunk = changes[i:i + OWNERS_PER_EVENT]
                client.rel.queue(EVENT_OWNERS, 0, struct.pack("<I", len(chunk)) +
                                 b"".join(OWNER_PAIR.pack(*c) for c in chunk))
            if changes:
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
            actor_seen[refid] = time.monotonic()
            if refid == args.bot_mirror:
                bot["mirror"] = record
            client.actor_states += 1
            kept.append(record)
        if not kept:
            return
        body = struct.pack("<I", len(kept)) + b"".join(kept)
        for other in clients.values():
            if other is not client and other.in_world:
                send(other, ACTORS, struct.pack("<I", client.id) + body)

    def bot_actors(now):
        """As the authority, the bot places each actor of its cells bot_shift units east of where
        the last authority left it, swaying east and west by bot_sway once per bot_period."""
        owned = [refid for refid, (_, key, _) in actors.items()
                 if actor_owners.get(refid, (owners.get(key),))[0] == BOT_ID]
        for refid in owned:
            actor_seen[refid] = now  # a client's states of it have stopped
        owned = [actors[refid][2] for refid in owned]
        phase = (now - bot["start"]) * 2 * math.pi / args.bot_period
        sway = args.bot_sway * math.sin(phase)
        facing = math.pi / 2 if math.cos(phase) >= 0 else 3 * math.pi / 2
        for i in range(0, len(owned), ACTORS_PER_PACKET):
            chunk = owned[i:i + ACTORS_PER_PACKET]
            body = struct.pack("<I", len(chunk))
            for record in chunk:
                refid, x, y, z, heading, health, flags, magicka, fatigue, target, anim = \
                    ACTOR.unpack(record)
                if refid in bot_fights:
                    flags, target = flags | ACTOR_IN_COMBAT, bot_fights[refid]
                    actors[refid] = (BOT_ID, actors[refid][1], ACTOR.pack(
                        refid, x, y, z, heading, health, flags, magicka, fatigue, target, anim))
                if args.bot_sway:
                    heading = facing
                if args.bot_stats:
                    health, magicka, fatigue = (float(v) for v in args.bot_stats.split(","))
                body += ACTOR.pack(refid, x + args.bot_shift + sway, y, z, heading, health, flags,
                                   magicka, fatigue, target, anim)
            for other in clients.values():
                if other.in_world:
                    send(other, ACTORS, struct.pack("<I", BOT_ID) + body)

    def handshake(kind, session, packet, addr, now, version):
        """Answer HANDSHAKE1 with HANDSHAKE2; on HANDSHAKE3, the HELLO it carries, the console's
        key, the session keys and whether this HANDSHAKE3 came before."""
        for stale in [k for k, v in pending.items() if now - v["time"] > HANDSHAKE_KEEP]:
            del pending[stale]
        entry = pending.get(session)
        if entry and entry["version"] != version:
            return None
        if kind == HANDSHAKE1:
            e = packet[OUTER.size:OUTER.size + 32]
            if len(packet) < HANDSHAKE_PAD or entry and entry["e"] != e:
                return None
            if entry is None:
                noise = Noise(False, server_secret, os.urandom(32), PROLOGUE)
                noise.read1(e)
                entry = pending[session] = {
                    "noise": noise, "e": e, "time": now, "done": None, "version": version,
                    "reply": OUTER.pack(b"T3MP", version, HANDSHAKE2, 0, session, 0)
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
        if len(packet) < OUTER.size or dropped("in") or addr[0] in bans["address"]:
            return
        magic, version, kind, _, session, seq = OUTER.unpack_from(packet)
        if magic != b"T3MP" or not version:
            return
        now = time.monotonic()
        if kind in (HANDSHAKE1, HANDSHAKE3):
            if kind == HANDSHAKE1 and session not in pending:
                if len(handshake_buckets) > HANDSHAKES_PENDING:  # forged sources, most likely
                    handshake_buckets.clear()
                source = handshake_buckets.setdefault(addr[0], Bucket(*HANDSHAKE_RATE))
                if (len(pending) >= HANDSHAKES_PENDING or not source.take(now)
                        or not handshake_bucket.take(now)):
                    limits["handshakes"] += 1
                    return
            done = handshake(kind, session, packet, addr, now, version)
            if done:
                hello, key, keys, again = done
                if len(hello) < 18:
                    return
                manager = bool(struct.unpack_from("<I", hello, 14)[0] & MANAGER)
                expected = MANAGER_VERSION if manager else T3MP_VERSION
                if version != expected:
                    stranger = handshake_client(addr, session, keys, hello[:6].hex(":"), version)
                    send(stranger, REFUSE, REFUSE_BODY.pack(expected, version, REFUSED_PROTOCOL))
                    print(f"{time.strftime('%H:%M:%S')} refused "
                          f"{'manager' if manager else 'game'} at {addr[0]}: protocol "
                          f"{version}, expected {expected}", flush=True)
                    return
                client = by_session.get(session)
                if again and client is not None and client.keys == keys:
                    # The WELCOME was lost, or this is a replay: answer the address that joined.
                    send(client, WELCOME, struct.pack("<I", client.id))
                    return
                handle_plain(T3MP.pack(b"T3MP", version, HELLO, 0, session, 0, 0, 0, 0)
                             + hello, addr, (key, keys))
            return
        client = by_session.get(session)
        if version != T3MP_VERSION or kind != SEALED or client is None or client.keys is None or \
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

    def handshake_client(addr, session, keys, mac, version):
        # Repeated HANDSHAKE3 replies need distinct nonces and the original destination.
        entry = pending[session]
        if "reply_client" not in entry:
            client = entry["reply_client"] = Client(0, mac)
            client.addr, client.session, client.keys = addr, session, keys
            client.version = version
        return entry["reply_client"]

    def refuse(addr, session, keys, mac, reason, version=T3MP_VERSION):
        stranger = handshake_client(addr, session, keys, mac, version)
        order, plugins = pinned or (0, 0)
        send(stranger, REFUSE, REFUSE_BODY.pack(order, plugins or 0, reason))

    def kick(client, reason):
        """Refuse a joined client, which stops it until the game is launched again."""
        if client.alive and client.keys:
            order, plugins = pinned or (0, 0)
            send(client, REFUSE, REFUSE_BODY.pack(order, plugins or 0, reason))
        if client.alive:
            leave(client)
        by_session.pop(client.session, None)

    def ask_save(targets, now, diagnostic=False):
        for client in targets:
            if client.alive:
                client.snapshot_request = 0x80000000 | ((client.snapshot_request + 1) & 0x7fffffff)
                client.rel.queue(EVENT_SAVE, 0, b"\x01" if diagnostic else
                                 struct.pack("<I", client.snapshot_request))
                flush(client, now)
        return ", ".join(f"client {c.id}" for c in targets if c.alive) or "nobody"

    def admin(line):
        """Run an admin command; the reply to print."""
        words = line.split()
        verb, rest = (words[0].lower(), words[1:]) if words else ("", [])
        by_id = {str(c.id): c for c in clients.values()}
        if verb == "list" and not rest:
            return "\n".join(
                f"client {c.id}: {'playing' if c.alive else 'away'}, key "
                f"{fingerprint(c.key) if c.key else '-'}, mac {c.mac}, address "
                f"{c.addr[0] if c.addr else '-'}"
                for c in sorted(clients.values(), key=lambda c: c.id)) or "no clients"
        if verb == "kick" and len(rest) == 1 and rest[0] in by_id:
            kick(by_id[rest[0]], REFUSED_KICKED)
            return f"kicked client {rest[0]}"
        if verb == "ban" and len(rest) == 1 and rest[0] in by_id:
            client = by_id[rest[0]]
            rest = ["key", fingerprint(client.key), "mac", client.mac]
        if verb in ("ban", "unban") and rest and len(rest) % 2 == 0:
            pairs = [(kind, ban_value(kind, value)) for kind, value in zip(rest[::2], rest[1::2])]
            if all(value for _, value in pairs):
                for kind, value in pairs:
                    (bans[kind].add if verb == "ban" else bans[kind].discard)(value)
                save_bans(bans_path, bans)
                if verb == "ban":
                    for c in list(clients.values()):
                        if c.alive and ((c.key and fingerprint(c.key) in bans["key"]) or
                                        c.mac in bans["mac"] or
                                        (c.addr and c.addr[0] in bans["address"])):
                            kick(c, REFUSED_BANNED)
                return (f"{verb}ned " if verb == "ban" else "unbanned ") + ", ".join(
                    f"{kind} {value}" for kind, value in pairs) + (
                    "" if bans_path else " (not kept: give --world)")
        if verb == "save" and len(rest) <= 1 and all(r in by_id for r in rest):
            targets = [by_id[r] for r in rest] or list(clients.values())
            return "asked to save: " + ask_save(targets, time.monotonic())
        if verb == "log" and len(rest) <= 1 and (not rest or rest[0] in LOG_LEVELS):
            if rest:
                detail["verbose"] = rest[0] == "verbose"
            return "log " + ("verbose" if detail["verbose"] else "normal")
        if verb == "say" and rest:
            notify(" ".join(rest), time.monotonic())
            return "sent to everyone"
        if verb == "tell" and len(rest) >= 2 and rest[0] in by_id:
            notify(" ".join(rest[1:]), time.monotonic(), only=by_id[rest[0]])
            return f"sent to client {rest[0]}"
        if verb == "stop" and not rest:
            begin_stop("admin stop", time.monotonic())
            return "stopping"
        if verb == "bans" and not rest:
            return "\n".join(f"{kind} {value}" for kind in BAN_KINDS
                             for value in sorted(bans[kind])) or "no bans"
        return ("commands:\n"
                "  list                  the clients\n"
                "  status                the clock, weather, clients and world\n"
                "  kick N                drop client N\n"
                "  log [normal|verbose]  show or set whether state changes (skills, vitals,\n"
                "                        statuses, objects) are printed\n"
                "  save [N]              ask every console, or client N, to save\n"
                "  ban N                 ban client N's key and MAC, and drop it\n"
                "  ban|unban key FINGERPRINT|mac MAC|address A.B.C.D\n"
                "  bans                  the bans\n"
                "  say TEXT              show TEXT on every console\n"
                "  tell N TEXT           show TEXT on client N's console\n"
                "  stop                  save every character, then stop the server")

    def status(now):
        """The server's state, one line each."""
        out = []
        if clock:
            clock.advance(now)
            out.append(f"  clock {clock}")
        if weather:
            out.append(f"  weather: {describe_weather(weather)}")
        if limits["handshakes"]:
            out.append(f"  handshakes refused over rate: {limits['handshakes']}")
        for client in clients.values():
            out.append(f"  client {client.id}: {'up' if client.alive else 'down'}"
                  + (" (saving)" if client.busy is not None else "") + ", "
                  + summary(client))
        if objects:
            out.append(f"  objects: {len(objects)} changed")
        if spawns:
            live = sum(not s["removed"] for s in spawns.values())
            out.append(f"  spawns: {live} live, {len(spawns) - live} removed")
        if actors:
            out.append(f"  actors: {len(actors)} known; authorities "
                  + ", ".join(f"{describe_key(k)} {c}" for k, c in sorted(
                      owners.items(), key=lambda i: describe_key(i[0]))))
            runs = {}
            for client_id, _ in actor_owners.values():
                runs[client_id] = runs.get(client_id, 0) + 1
            out.append("  actors run by: " + ", ".join(
                f"client {c} {n}" for c, n in sorted(runs.items())))
        return "\n".join(out)

    def handle_plain(packet, addr, secure=None):
        nonlocal pinned, clock
        magic, version, kind, _, session, seq, _, sent, _ = T3MP.unpack_from(packet)
        stamp = time.strftime("%H:%M:%S")
        now = time.monotonic()
        if kind == HELLO and secure and len(packet) >= T3MP.size + HELLO_BODY.size:
            key, keys = secure
            mac, build, order, plugins, *offered, build_id = HELLO_BODY.unpack_from(packet,
                                                                                  T3MP.size)
            manager = bool(plugins & MANAGER)
            mac, lobby, plugins = mac.hex(":"), bool(plugins & LOBBY), plugins & ~LOBBY & ~MANAGER
            if fingerprint(key) in bans["key"] or mac in bans["mac"]:
                print(f"{stamp} refused {mac} at {addr[0]}: banned", flush=True)
                refuse(addr, session, keys, mac, REFUSED_BANNED, version)
                return
            if password and key not in admitted:
                if len(password_buckets) > HANDSHAKES_PENDING:
                    password_buckets.clear()
                tries = password_buckets.setdefault(addr[0], Bucket(*PASSWORD_RATE))
                given = packet[T3MP.size + HELLO_BODY.size:]
                if not tries.take(now) or not hmac.compare_digest(given, password):
                    print(f"{stamp} refused {mac} at {addr[0]}: wrong password", flush=True)
                    refuse(addr, session, keys, mac, REFUSED_PASSWORD, version)
                    return
                admitted.add(key)
                if admitted_path:
                    with open(admitted_path, "a", encoding="utf-8") as stream:
                        stream.write(f"{key.hex()} {mac} {time.strftime('%Y-%m-%d')}\n")
                print(f"{stamp} admitted key {fingerprint(key)} ({mac})", flush=True)
            if manager:
                stranger = handshake_client(addr, session, keys, mac, version)
                body = build_server.ticket() if build_server else                     tes3x_netbuild.BUILD_BODY.pack(bytes(32), 0, 0, bytes(16))
                who = manager_character(key).encode("latin-1", "replace")[:46]
                send(stranger, BUILD, body + (who + b"\0" if who else b""))
                print(f"{stamp} manager {fingerprint(key)} at {addr[0]} asked for the build"
                      + ("" if build_server else ", which is not served"), flush=True)
                return
            served = build_server.build_id() if build_server and any(build_id) else None
            if served and build_id != served:
                print(f"{stamp} refused {mac}: build {build_id.hex()[:16]}, the server's is "
                      f"{served.hex()[:16]}", flush=True)
                refuse(addr, session, keys, mac, REFUSED_STALE)
                return
            if pinned is None and not lobby:
                pinned = (order, plugins)
                print(f"{stamp} load order {order:#010x} ({plugins} plugins) set by {mac}",
                      flush=True)
                adopt_world(order, now)
            if pinned and order != pinned[0]:
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
            client.owners_told = {}
            client.busy = None
            client.lobby = lobby
            if client.joins == 1:
                client.joined = now
                client.bursts = sorted(bursts)
            verb = "joined" if client.joins == 1 else "rejoined"
            print(f"{stamp} client {client.id} {verb}: {mac} at {addr[0]}:{addr[1]}, "
                  f"build {build:#010x}", flush=True)
            send(client, WELCOME, struct.pack("<I", client.id))
            if lobby:
                print(f"{stamp} client {client.id} is at the main menu", flush=True)
                return
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
            for origin, parts in identities.items():
                if origin != client.id and all(parts):
                    for part in parts:
                        client.rel.queue(EVENT_IDENTITY, origin, part)
            for origin, parts, _ in actor_equipment.values():
                for part in parts:
                    client.rel.queue(EVENT_ACTOR_EQUIPMENT, origin, part)
            for data in pack_weather(weather):
                client.rel.queue(EVENT_WEATHER, 0, data)
            for origin, data in bounties.items():
                if origin != client.id:
                    client.rel.queue(EVENT_BOUNTY, origin, data)
            for other in clients.values():
                if other is not client and other.in_world and other.dead:
                    client.rel.queue(EVENT_PLAYER, other.id, bytes([PLAYER_DEATH]))
            if sending and (client.bulk is None or client.bulk.status != 3):
                client.bulk = Outgoing(*sending)
                client.rel.queue(EVENT_OFFER, 0, client.bulk.offer())
                print(f"{stamp} offering {sending[0]} ({len(sending[1])} bytes, id "
                      f"{client.bulk.id:#010x}) to client {client.id}", flush=True)
            if not key_folder(client):  # with characters, it joins once it has one
                announce_join(client, now)
            if client.rel.out:
                flush(client, now)
            return
        client = by_session.get(session)
        if client is None:
            return
        if seq > client.peer_seq + 1:
            client.gaps += seq - client.peer_seq - 1
        client.peer_seq = max(client.peer_seq, seq)
        client.peer_time, client.addr, client.last = sent, addr, time.monotonic()
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
            keep_place(client, now)
            if args.bot:
                bot_anchor(client.state)
                if args.bot_echo:
                    bot["echo"] = client.state
            for other in clients.values():
                if other is not client and other.in_world:
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
            if status == BULK_NO_SPACE:
                print(f"{stamp} client {client.id} has no room for {bulk.name} "
                      f"({len(bulk.data)} bytes and its margin)", flush=True)
            elif status is not None and status != BULK_RECEIVING:
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
                if upload.status == BULK_DONE:
                    received(client, stamp, now)
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

    # Stopping asks every joined console for its character and waits, up to --stop-wait, for the
    # saves of those running one; a second Ctrl-C stops at once.
    stop = {"until": None, "waiting": {}}

    def begin_stop(why, now):
        if stop["until"] is not None:
            return
        stop["until"] = now + args.stop_wait
        notify("The server is shutting down.", now)
        asked = ask_save([c for c in clients.values() if c.alive], now)
        stop["waiting"] = {c.id: c.snapshot_request for c in clients.values()
                           if c.alive and c.synced}
        print(f"{time.strftime('%H:%M:%S')} stopping ({why}): asked to save: {asked}; waiting "
              f"up to {args.stop_wait:g} s for "
              + (", ".join(f"client {i}" for i in stop["waiting"]) or "nobody"), flush=True)
        signal.signal(signal.SIGINT, signal.default_int_handler)

    def stopped(now):
        if stop["until"] is None:
            if deadline is None or now < deadline:
                return False
            begin_stop("duration over", now)
        for ident, kept in list(stop["waiting"].items()):
            client = next(c for c in clients.values() if c.id == ident)
            if client.snapshot_saved == kept or not client.alive:
                del stop["waiting"][ident]
                print(f"{time.strftime('%H:%M:%S')} client {ident} "
                      + ("saved" if client.snapshot_saved == kept else "left without saving"), flush=True)
        if stop["waiting"] and now < stop["until"]:
            return False
        for ident in stop["waiting"]:
            print(f"{time.strftime('%H:%M:%S')} client {ident} did not save in "
                  f"{args.stop_wait:g} s", flush=True)
        return True

    signal.signal(signal.SIGINT, lambda *_: commands.put("stop"))
    if pinned:
        adopt_world(pinned[0], time.monotonic())
    while not stopped(time.monotonic()):
        while not commands.empty():
            print(admin(commands.get()), flush=True)
        waiting = [sock] + [link.sock for link in links] + [link.forward for link in links] + (
            [dns] if dns else []) + (
            [admin_sock] if admin_sock else []) + ([remote_admin.sock] if remote_admin else [])
        wait = 0.25
        if any(c.queue for c in clients.values()):
            wait = PACE_WINDOW
        elif any(c.flush_due for c in clients.values()):
            wait = EVENTS_GAP
        elif any(c.bulk and c.bulk.status == BULK_RECEIVING for c in clients.values()):
            wait = 0.05
        for ready in select.select(waiting, [], [], wait)[0]:
            if ready is admin_sock:
                try:
                    line, addr = admin_sock.recvfrom(2048)
                except ConnectionResetError:
                    continue
                reply = admin(line.decode("utf-8", "replace"))
                if not quiet_admin(line):
                    print(f"{time.strftime('%H:%M:%S')} admin: {wire_text(line)}: {reply}",
                          flush=True)
                admin_sock.sendto(reply.encode("utf-8"), addr)
                continue
            if remote_admin and ready is remote_admin.sock:
                try:
                    data, addr = remote_admin.sock.recvfrom(2048)
                except ConnectionResetError:
                    continue

                def run(line, addr=addr):
                    reply = admin(line)
                    if not quiet_admin(line.encode("utf-8")):
                        print(f"{time.strftime('%H:%M:%S')} remote admin from {addr[0]}: "
                              f"{wire_text(line.encode('utf-8'))}: {reply}", flush=True)
                    return reply

                reply = remote_admin.handle(data, addr, run)
                if reply:
                    remote_admin.sock.sendto(reply, addr)
                continue
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
            forwarding = next((link for link in links if link.forward is ready), None)
            if forwarding:
                try:
                    data, address = forwarding.forward.recvfrom(2048)
                except ConnectionResetError:
                    continue
                guest = forwarding.forward_guest.get(address[1])
                if guest:
                    mac, guest_ip, guest_port = guest
                    host_port = address[1]
                    forwarding.send(udp_frame(mac, guest_ip, data, sport=host_port,
                                              dport=guest_port))
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
                udp = 14 + (frame[14] & 0x0F) * 4 if len(frame) >= 42 else 0
                sport = struct.unpack_from(">H", frame, udp)[0] if udp and frame[23] == 17 else 0
                data = udp_from_frame(frame, args.port)
                if data:
                    guarded(data, (src, sport, frame[6:12], link))
                    continue
                if udp and frame[23] == 17:
                    sport, dport = struct.unpack_from(">HH", frame, udp)
                    data = udp_from_frame(frame, dport)
                    if data is not None and dport in args.forward_ports:
                        link.forward_guest[dport] = (frame[6:12], src, sport)
                        link.forward.sendto(data, ("127.0.0.1", dport))
        now = time.monotonic()
        for client in clients.values():
            if client.queue:
                pump(client)
            if client.alive and client.in_world and client.announce_due:
                announce_join(client, now)
            if client.alive and now - client.last > args.idle_timeout:
                print(f"{time.strftime('%H:%M:%S')} client {client.id} timed out", flush=True)
                leave(client)
        if world["path"] and now >= world["saved"] + (10 if world["dirty"] else 60):
            write_world(now)
        if now >= streams_saved + 2:
            streams_saved = now
            for stream in streams.values():
                if stream.dirty:
                    stream.save()
        for spec in [b for b in bot_bounties if args.bot and window(f"{b[0]}:", now)]:
            bot_bounties.remove(spec)
            print(f"{time.strftime('%H:%M:%S')} bot bounty {spec[1]}", flush=True)
            broadcast_event(BOT_ID, EVENT_BOUNTY, struct.pack("<i", spec[1]), now)
        if args.bot and bot["anchor"] and window(args.bot_busy, now) != bot["busy"]:
            bot["busy"] = not bot["busy"]
            print(f"{time.strftime('%H:%M:%S')} bot {'saves' if bot['busy'] else 'is back'}",
                  flush=True)
            broadcast_event(BOT_ID, EVENT_BUSY, bytes([BUSY_SAVING if bot["busy"] else 0]), now)
        if args.bot and bot["anchor"] and window(args.bot_dead, now) != bot["dead"]:
            bot["dead"] = not bot["dead"]
            print(f"{time.strftime('%H:%M:%S')} bot {'dies' if bot['dead'] else 'respawns'}",
                  flush=True)
            broadcast_event(BOT_ID, EVENT_PLAYER,
                            bytes([PLAYER_DEATH if bot["dead"] else PLAYER_ALIVE]), now)
        if args.bot and bot["anchor"] and now >= bot["next"] and not bot["busy"]:
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
            verb = "casts" if kind == EVENT_SPELL else "is seen casting"
            if refid is None:
                print(f"{time.strftime('%H:%M:%S')} bot {verb} {name} at nothing", flush=True)
                broadcast_event(BOT_ID, kind, SPELL.pack(0, 0, 0, SOURCE_SPELL, 1) + zstr(name),
                                now)
                continue
            on = f"{refid:#010x}" if refid else "the player"
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
        for client in clients.values():
            if client.alive and client.rebuild and client.rebuild[1] and now >= client.rebuild[1]:
                client.rebuild = (client.rebuild[0], None)
                print(f"{time.strftime('%H:%M:%S')} asked client {client.id} for its rebuilt "
                      f"character: {ask_save([client], now, diagnostic=True)}", flush=True)
        if now >= save_next:
            save_next = now + args.save_every
            if any(c.alive for c in clients.values()):
                print(f"{time.strftime('%H:%M:%S')} asked to save: "
                      f"{ask_save(list(clients.values()), now, diagnostic=args.adopt)}", flush=True)
        if clock and now >= clock_next:
            clock_next = now + CLOCK_INTERVAL
            body = clock.body(now)
            for client in clients.values():
                if client.alive and not client.lobby:
                    send(client, CLOCK, body)
        if args.report and now >= report:
            report = now + args.report
            print(status(now), flush=True)
    if world["path"]:
        write_world(time.monotonic())
    for stream in streams.values():
        if stream.dirty:
            stream.save()
    for client in clients.values():
        print(f"client {client.id} {client.mac}: " + summary(client, "last "))
    if args.drop:
        print(f"dropped {lost['in']} packets in, {lost['out']} out")
    return 0 if clients else 1


class FuzzClient:
    """A console's session in Python: the handshake, then sealed packets of any content."""

    def __init__(self, sock, addr, rng, version=None):
        self.sock, self.addr, self.rng = sock, addr, rng
        self.version = T3MP_VERSION if version is None else version
        self.version_override = version is not None
        self.session = rng.getrandbits(32) | 1
        self.seq = self.peer_seq = 0
        self.keys = self.handshake3 = None
        self.event_next = 1  # the next event number the server will deliver, from its acks
        self.refused = None  # a REFUSE's body

    def join(self, timeout=2.0, password=b"", lobby=False, manager=False, secret=None,
             build_id=bytes(32)):
        """WELCOME's body, or with manager, BUILD's; None if the server sent neither."""
        if manager and not self.version_override:
            self.version = MANAGER_VERSION
        secret, e = secret or self.rng.randbytes(32), self.rng.randbytes(32)
        noise = Noise(True, secret, e, PROLOGUE)
        message1 = (OUTER.pack(b"T3MP", self.version, HANDSHAKE1, 0, self.session, 0)
                    + noise.write1()).ljust(HANDSHAKE_PAD, b"\0")
        self.sock.sendto(message1, self.addr)
        reply = self.receive_raw(timeout, HANDSHAKE2)
        noise.read2(reply[OUTER.size:])
        hello = HELLO_BODY.pack(self.rng.randbytes(6), 0, 0x46555A5A,
                                3 | (LOBBY if lobby else 0) | (MANAGER if manager else 0),
                                12.0, 16.0, 7.0, 427.0, 1.0, 30.0, build_id)
        self.handshake3 = (OUTER.pack(b"T3MP", self.version, HANDSHAKE3, 0, self.session, 0)
                           + noise.write3(hello + password))
        self.sock.sendto(self.handshake3, self.addr)
        self.keys = noise.split()
        if manager:
            return self.receive(timeout, BUILD)
        welcome = self.receive(timeout, WELCOME)
        if welcome is None:
            raise RuntimeError("no WELCOME")
        return welcome

    def receive_raw(self, timeout, kind):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.sock.settimeout(max(end - time.monotonic(), 0.01))
            try:
                data, _ = self.sock.recvfrom(4096)
            except (socket.timeout, ConnectionResetError):
                continue
            if len(data) >= OUTER.size and data[5] == kind:
                return data
        raise RuntimeError(f"no reply of type {kind}")

    def receive(self, timeout, kind):
        """The body of the next sealed packet of this kind, or None."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                data = self.receive_raw(end - time.monotonic(), SEALED)
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
        outer = OUTER.pack(b"T3MP", self.version, SEALED, 0, self.session, self.seq)
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
            event_kind = rng.choice(list(range(0, 21)) +
                                    [EVENT_OFFER, EVENT_IDENTITY, EVENT_ACTOR_EQUIPMENT, 65535])
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
                       rng.getrandbits(32), rng.randbytes(ANIM_BYTES)) for _ in range(count))
    if kind == STATE and shape < 0.7:
        return kind, rng.randbytes(STATE_SIZE)
    return kind, rng.randbytes(rng.choice((0, 1, 3, 4, 8, 20, 64, rng.randrange(0, 1400))))


# Remote admin: HELLO, then a single-use challenge, then the command and its reply sealed with
# a key from the admin password and that challenge. The password is stretched, so a captured
# exchange does not make guessing it cheap.
REMOTE_MAGIC = b"T3AD"
REMOTE_VERSION = 1
REMOTE_HELLO, REMOTE_CHALLENGE, REMOTE_COMMAND, REMOTE_REPLY, REMOTE_REFUSED = range(5)
REMOTE_HEAD = struct.Struct("<4sBB")
REMOTE_NONCE = 16
REMOTE_NONCE_SECONDS = 10.0
REMOTE_NONCES = 64
ADMIN_PASSWORD_MIN = 8


def admin_secret(password):
    """The stretched admin password both ends derive their per-command keys from."""
    return hashlib.scrypt(password, salt=b"tes3x remote admin v1", n=1 << 14, r=8, p=1,
                          dklen=32)


def remote_key(secret, nonce):
    return hmac.new(secret, b"tes3x admin " + nonce, hashlib.sha256).digest()


def load_admin_password(path):
    """The first line of an admin password file, as bytes."""
    with open(path, encoding="utf-8") as stream:
        password = stream.readline().strip()
    if len(password) < ADMIN_PASSWORD_MIN or not password.isprintable():
        sys.exit(f"{path}: the admin password must be at least {ADMIN_PASSWORD_MIN} printable "
                 "characters")
    return password.encode("utf-8")


class RemoteAdmin:
    """The server's remote admin listener; run(line) answers an authenticated command."""

    def __init__(self, secret, sock, clock=time.monotonic):
        self.secret, self.sock, self.clock = secret, sock, clock
        self.nonces = {}  # nonce -> (address, issued)
        self.failures = {}  # address -> Bucket

    def handle(self, data, addr, run):
        """The reply datagram for one request, or None; run(line) executes a command."""
        now = self.clock()
        if len(data) < REMOTE_HEAD.size:
            return None
        magic, version, kind = REMOTE_HEAD.unpack_from(data)
        if magic != REMOTE_MAGIC or version != REMOTE_VERSION:
            return None
        for nonce in [n for n, (_, at) in self.nonces.items()
                      if now - at > REMOTE_NONCE_SECONDS]:
            del self.nonces[nonce]
        if kind == REMOTE_HELLO:
            if len(self.nonces) >= REMOTE_NONCES:
                return self.refuse(b"", "busy")
            nonce = os.urandom(REMOTE_NONCE)
            self.nonces[nonce] = (addr, now)
            return REMOTE_HEAD.pack(REMOTE_MAGIC, REMOTE_VERSION, REMOTE_CHALLENGE) + nonce
        if kind != REMOTE_COMMAND or len(data) < REMOTE_HEAD.size + REMOTE_NONCE + 16:
            return None
        bucket = self.failures.setdefault(addr[0], Bucket(*PASSWORD_RATE, now))
        nonce = data[REMOTE_HEAD.size:REMOTE_HEAD.size + REMOTE_NONCE]
        issued = self.nonces.pop(nonce, None)
        if not bucket.available(now):
            # Not even tried: a right guess while slowed would otherwise still get through.
            return self.refuse(nonce, "slow down")
        head = data[:REMOTE_HEAD.size + REMOTE_NONCE]
        line = None
        if issued and issued[0] == addr:
            line = unseal(remote_key(self.secret, nonce), 0, head,
                          data[REMOTE_HEAD.size + REMOTE_NONCE:])
        if line is None:
            # Only failures spend tries, so polling with the right password is never slowed.
            bucket.take(now)
            return self.refuse(nonce, "unauthorized")
        reply = run(line.decode("utf-8", "replace")).encode("utf-8")
        head = REMOTE_HEAD.pack(REMOTE_MAGIC, REMOTE_VERSION, REMOTE_REPLY) + nonce
        return head + seal(remote_key(self.secret, nonce), 1, head, reply)

    @staticmethod
    def refuse(nonce, why):
        return (REMOTE_HEAD.pack(REMOTE_MAGIC, REMOTE_VERSION, REMOTE_REFUSED) + nonce
                + why.encode("ascii"))


class RemoteAdminError(Exception):
    pass


def remote_admin_request(host, port, secret, line, timeout=3.0):
    """Run one admin command on a server's remote admin port; its reply text."""
    sock = udp_socket()
    sock.settimeout(timeout)
    try:
        address = (socket.gethostbyname(host), port)
        sock.sendto(REMOTE_HEAD.pack(REMOTE_MAGIC, REMOTE_VERSION, REMOTE_HELLO), address)
        data = sock.recvfrom(65536)[0]
        if REMOTE_HEAD.unpack_from(data) != (REMOTE_MAGIC, REMOTE_VERSION, REMOTE_CHALLENGE) or \
                len(data) != REMOTE_HEAD.size + REMOTE_NONCE:
            raise RemoteAdminError("not a TES3X remote admin port")
        nonce = data[REMOTE_HEAD.size:]
        key = remote_key(secret, nonce)
        head = REMOTE_HEAD.pack(REMOTE_MAGIC, REMOTE_VERSION, REMOTE_COMMAND) + nonce
        sock.sendto(head + seal(key, 0, head, line.encode("utf-8")), address)
        data = sock.recvfrom(65536)[0]
    except (socket.timeout, ConnectionResetError):
        raise RemoteAdminError(f"no server answered on {host}:{port}") from None
    except OSError as exc:
        raise RemoteAdminError(f"{host}:{port}: {exc}") from None
    finally:
        sock.close()
    kind = REMOTE_HEAD.unpack_from(data)[2] if len(data) >= REMOTE_HEAD.size else None
    if kind == REMOTE_REFUSED:
        raise RemoteAdminError("refused: " + data[REMOTE_HEAD.size + REMOTE_NONCE:]
                               .decode("ascii", "replace"))
    head = data[:REMOTE_HEAD.size + REMOTE_NONCE]
    reply = None
    if kind == REMOTE_REPLY and head[REMOTE_HEAD.size:] == nonce:
        reply = unseal(key, 1, head, data[len(head):])
    if reply is None:
        raise RemoteAdminError("the reply was not authentic")
    return reply.decode("utf-8", "replace")


def admin_command(args):
    """Send one admin command to a local server, or with --server to a remote one."""
    line = " ".join(args.words)
    if args.server:
        host, _, port = args.server.partition(":")
        if not args.password_file:
            print("--server needs --password-file with the server's admin password",
                  file=sys.stderr)
            return 2
        secret = admin_secret(load_admin_password(args.password_file))
        try:
            print(remote_admin_request(host, int(port or REMOTE_ADMIN_PORT), secret, line))
        except RemoteAdminError as exc:
            print(exc, file=sys.stderr)
            return 1
        return 0
    sock = udp_socket()
    sock.settimeout(2.0)
    sock.sendto(line.encode("utf-8"), ("127.0.0.1", args.port))
    try:
        print(sock.recvfrom(65536)[0].decode("utf-8"))
    except (socket.timeout, ConnectionResetError):
        print(f"no server answered on 127.0.0.1:{args.port}", file=sys.stderr)
        return 1
    return 0


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
    p.add_argument("--forward", type=int, action="append", default=[AGENT_PORT],
                   dest="forward_ports", metavar="PORT",
                   help="with --tunnel, forward this guest UDP port to localhost "
                        "(repeatable; default 26501 for the in-game agent)")
    p.add_argument("--duration", type=float, help="stop after this many seconds")
    p.add_argument("--stop-wait", type=float, default=60.0, metavar="SECONDS",
                   help="on stopping (Ctrl-C, --duration, admin stop) wait this long for every "
                        "joined console to save its character (default 60)")
    p.add_argument("--welcome", metavar="TEXT",
                   help="show this in a box when a player is in the world as their character (80 characters)")
    p.add_argument("--world", metavar="DIR",
                   help="keep the world in DIR, one file per load order: the clock, deaths, "
                        "changed objects, objects made at run time and weather, loaded when the "
                        "load order is set and "
                        "written every 10 seconds while it changes")
    p.add_argument("--log", choices=LOG_LEVELS, default="normal",
                   help="verbose also prints each player and actor state change")
    p.add_argument("--save-every", type=float, metavar="SECONDS",
                   help="flush every joined character this often; with --adopt, upload "
                        "diagnostic saves instead")
    p.add_argument("--starts", metavar="FILE",
                   help="where new characters may begin ([[start]] tables; default "
                        "examples/starts.toml)")
    p.add_argument("--start-gold", type=int, default=50, metavar="N",
                   help="gold a new character starts with")
    p.add_argument("--respawn", choices=sorted(RESPAWN_PLACES), default="nearest",
                   help="where a player who dies comes back: the closest temple (TempleMarker), "
                        "Imperial shrine (DivineMarker) or either (default)")
    p.add_argument("--idle-timeout", type=float, default=IDLE_TIMEOUT, metavar="SECONDS",
                   help="drop a client silent this long (default 20; consoles stop waiting at 15)")
    p.add_argument("--respawn-delay", type=float, default=5.0, metavar="SECONDS",
                   help="how long a dead player lies before coming back (default 5)")
    p.add_argument("--death-gold", type=int, default=10, metavar="PERCENT",
                   help="the share of carried gold a death costs (default 10)")
    p.add_argument("--rebuild", type=float, nargs="?", const=10.0, metavar="SECONDS",
                   help="a launch not running its key's newest character gets that character's "
                        "kept state replayed over whatever it runs and, SECONDS later (default "
                        "10), is asked for a save; the server diffs it against the checkpoint "
                        "(tes3x_ess.py --diff) into uploads/KEY/NAME.diff.txt and keeps neither")
    p.add_argument("--load-state", action="store_true",
                   help="compatibility option; character loading always uses retained state")
    p.add_argument("--adopt", action="store_true",
                   help="a key with no character keeps whatever game its console runs instead "
                        "of making a new one (tests that start from a save)")
    p.add_argument("--dhcp-lease", type=int, default=3600, metavar="SECONDS",
                   help="lease time offered to xemu guests that ask for an address "
                        "(NetAddress=dhcp); each tunnel leases %s" % GUEST_IP)
    p.add_argument("--report", type=float, default=0,
                   help="seconds between status blocks (0: only the admin status command)")
    p.add_argument("--host", action="append", default=[], metavar="NAME=ADDRESS",
                   help="answer DNS queries for NAME, on port 53 and through the tunnel "
                        "(repeatable)")
    p.add_argument("--bot", action="store_true",
                   help="relay a synthetic player circling where the first client stands")
    p.add_argument("--bot-radius", type=float, default=256, help="units")
    p.add_argument("--bot-period", type=float, default=12, help="seconds per circle")
    p.add_argument("--bot-at", metavar="DX,DY",
                   help="the bot circles this far from where the first client entered the world "
                        "and takes the actors nearer to it than to any client (with --bot-shift 0 "
                        "it leaves them where they are)")
    p.add_argument("--bot-fights", action="append", default=[], metavar="REFID:CLIENT",
                   help="as an actor's owner, the bot reports it fighting that client's player "
                        "(hex refid)")
    p.add_argument("--bot-rate", type=float, default=20, help="states per second")
    p.add_argument("--bot-say", type=float, metavar="SECONDS",
                   help="the bot also sends a numbered text event this often")
    p.add_argument("--bot-owns", metavar="START:END",
                   help="the bot is the authority for its cells from START to END seconds after it "
                        "first appears (END may be left out)")
    p.add_argument("--bot-bounty", action="append", default=[], metavar="VALUE@SECONDS",
                   help="the bot's bounty becomes VALUE this long after it first appears; 0 after "
                        "a bounty ends the fights of actors that would not attack it otherwise "
                        "(repeatable)")
    p.add_argument("--bot-busy", metavar="START:END",
                   help="the bot saves from START to END seconds after it first appears: it sends "
                        "BUSY, stops its states and gives up the actors it owns")
    p.add_argument("--bot-dead", metavar="START:END",
                   help="the bot falls dead from START to END seconds after it first appears")
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
                        "or at an actor (hex refid), or at nothing (none), this long after it appears "
                        "(repeatable)")
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
    p.add_argument("--admin-port", type=int, metavar="PORT",
                   help="take admin commands (tes3x_net.py admin) on this port of 127.0.0.1 "
                        "only; 0 for none (default: 26502)")
    p.add_argument("--password-file", metavar="FILE",
                   help="a console whose key is new must give the password on this file's first "
                        "line (its NetPassword); admitted keys go to admitted.txt in --world")
    p.add_argument("--remote-admin", type=int, metavar="PORT",
                   help="also take admin commands from other machines on this UDP port (26503 "
                        "by convention), authenticated by the admin password")
    p.add_argument("--admin-password-file", metavar="FILE",
                   help="the remote admin password on this file's first line, at least "
                        f"{ADMIN_PASSWORD_MIN} characters (default: admin-password.txt in "
                        "--world)")
    p.add_argument("--build", metavar="DIR",
                   help="hand the console manager this staged game folder (the pipeline's "
                        "deploy/, with tes3xbuild.json), over HTTP")
    p.add_argument("--deltas", metavar="DIR",
                   help="the XBE deltas for --build (default: deltas/ beside it)")
    p.add_argument("--serve-origin", action="append", choices=tes3x_netbuild.ORIGINS,
                   default=None, metavar="ORIGIN",
                   help="files of this manifest origin are served (repeatable; default: build "
                        "only; retail and xbe are the admin's choice)")
    p.add_argument("--http-port", type=int, metavar="PORT",
                   help="the TCP port --build is served on (default: --port; 0 for any)")
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
    p = sub.add_parser("admin", help="send an admin command to a server on this PC or, with "
                                     "--server, elsewhere")
    p.add_argument("words", nargs="+", metavar="COMMAND",
                   help="list, kick N, ban N, ban|unban key|mac|address VALUE, bans")
    p.add_argument("--port", type=int, default=ADMIN_PORT, help="the server's --admin-port")
    p.add_argument("--server", metavar="HOST[:PORT]",
                   help=f"a server's --remote-admin port instead (default port "
                        f"{REMOTE_ADMIN_PORT})")
    p.add_argument("--password-file", metavar="FILE",
                   help="with --server, the admin password on this file's first line")
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
    return {"listen": listen, "ping": ping, "serve": serve, "fuzz": fuzz,
            "admin": admin_command}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
