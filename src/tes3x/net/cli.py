"""Talk to the payload's network driver (the `multiplayer` patch).

    tes3x net listen                     # console broadcasts on UDP 26500
    tes3x net ping 192.0.2.50             # echo round trips to `tes3xnet up`
    tes3x net listen --tunnel 9369       # the same through xemu's udp backend
    tes3x net ping 10.0.2.15 --tunnel 9369
    tes3x net serve --tunnel 9369 --bot   # plus a player circling the first client
    tes3x net serve --tunnel 9369 --bot --bot-say 2 --drop 0.2   # events under loss
    tes3x net serve --tunnel 9369 --bot --bot-owns 20:60   # the bot runs the cell
    tes3x net serve --tunnel 9369 --tunnel 9371   # two xemus, one per tunnel
    tes3x net serve --tunnel 9369 --send FILE   # to TES3X on each console's U:
    tes3x net plugin OUT.esp --master Morrowind.esm   # the ghost plugin

With --tunnel PORT this tool is the guest's only peer: xemu sends each guest Ethernet frame to
PORT as one datagram and accepts frames on PORT+1 (`tes3x xemu --net-tunnel PORT`), so ping
answers ARP itself and resolves the console's MAC before it pings."""

import argparse
import os
import random
import socket
import struct
import sys
import tes3x.netbuild as tes3x_netbuild
import time
from .proto import (ACTOR, ACTORS, ADMIN_PASSWORD_MIN, ADMIN_PORT, AGENT_PORT, ANIM_BYTES, BUILD,
                    BULK_ACK, CHUNK, CLOCK, EVENT, EVENTS, EVENTS_HEAD, EVENT_ACTOR_EQUIPMENT,
                    EVENT_DATA, EVENT_IDENTITY, EVENT_OFFER, GONE, GUEST_IP, HANDSHAKE1,
                    HANDSHAKE2, HANDSHAKE3, HANDSHAKE_PAD, HEARTBEAT, HELLO, HELLO_BODY,
                    IDLE_TIMEOUT, INNER, LOBBY, LOG_LEVELS, MANAGER, MANAGER_VERSION, Noise,
                    OUTER, PEER, PING, PONG, PORT, PROLOGUE, REFUSE, REMOTE_ADMIN_PORT,
                    REMOTE_CHALLENGE, REMOTE_COMMAND, REMOTE_HEAD, REMOTE_HELLO, REMOTE_MAGIC,
                    REMOTE_NONCE, REMOTE_REFUSED, REMOTE_REPLY, REMOTE_VERSION, RESPAWN_PLACES,
                    SEALED, STATE, STATE_SIZE, T3MP_VERSION, WELCOME, admin_secret, now_us,
                    remote_key, seal, unseal)
from .server import (decode, load_admin_password, serve)
from .tunnel import (Tunnel, resolve, udp_frame, udp_from_frame, udp_socket)
from .plugin import (ghost_plugin)

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
                        "(tes3x ess --diff) into uploads/KEY/NAME.diff.txt and keeps neither")
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
                   help="take admin commands (tes3x net admin) on this port of 127.0.0.1 "
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
    p.add_argument("address", help="HOST[:PORT] of a tes3x net server")
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
