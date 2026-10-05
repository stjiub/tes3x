"""Authenticated UDP listener for the in-game TES3X agent."""

import argparse
from dataclasses import dataclass
import os
from pathlib import Path, PureWindowsPath
import queue
import re
import socket
import struct
import sys
import threading
import time

from tes3x_net import Noise, fingerprint, seal, udp_socket, unseal, x25519_public


MAGIC = b"T3AG"
VERSION = 1
PORT = 26501
PROLOGUE = b"TES3X in-game agent v1"
HEADER = struct.Struct("<4sBBHII")  # magic, version, kind, body bytes, session, sequence
HANDSHAKE1, HANDSHAKE2, HANDSHAKE3, SEALED = range(1, 5)
WELCOME, HEARTBEAT, LOG, GOODBYE, REPLY, REQUEST = range(6)
OP_CONSOLE, OP_READ, OP_REBOOT = range(1, 4)
# The console manager's file operations and launch; the game answers them "bad request".
OP_WRITE, OP_LIST, OP_DELETE, OP_RENAME, OP_MKDIR, OP_LAUNCH = range(4, 10)
STATUS = {0: "ok", 1: "busy", 2: "unsupported", 3: "failed", 4: "bad request"}
MAX_PACKET = 1400
MAX_REQUEST = 200  # what the game accepts
# what one sealed packet carries: the manager accepts requests up to this
MAX_SEALED_REQUEST = MAX_PACKET - 16 - 16 - 1
WRITE_CHUNK = 1024
PATH_MAX = 255
CONSOLE_MAX = 95
READ_CHUNK = 1024
PENDING_SECONDS = 5.0
STALL_SECONDS = 3.0
RETRY_SECONDS = 0.5
RETRIES = 10


def load_or_create_key(path):
    """Read a raw X25519 secret, creating it without replacing an existing identity."""
    path = Path(path)
    try:
        secret = path.read_bytes()
    except FileNotFoundError:
        path.parent.mkdir(parents=True, exist_ok=True)
        secret = os.urandom(32)
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            secret = path.read_bytes()
        else:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(secret)
    if len(secret) != 32:
        raise ValueError(f"agent key must be exactly 32 bytes: {path}")
    return secret


def key_fingerprint(secret):
    return fingerprint(x25519_public(secret))


def encode(kind, session, sequence, body=b""):
    if len(body) + HEADER.size > MAX_PACKET:
        raise ValueError("agent datagram is too large")
    return HEADER.pack(MAGIC, VERSION, kind, len(body), session, sequence) + body


def decode(data):
    if len(data) < HEADER.size or len(data) > MAX_PACKET:
        return None
    magic, version, kind, size, session, sequence = HEADER.unpack_from(data)
    if magic != MAGIC or version != VERSION or size != len(data) - HEADER.size:
        return None
    return kind, session, sequence, data[:HEADER.size], data[HEADER.size:]


@dataclass
class Pending:
    noise: object
    request: bytes
    response: bytes
    created: float


@dataclass
class Peer:
    address: tuple
    session: int
    public: bytes
    keys: tuple
    hello: bytes
    last_seen: float
    top: int = -1
    outgoing: int = 0
    welcome: bytes = b""
    stalled: bool = False


@dataclass
class Request:
    key: tuple
    body: bytes
    sent: float
    tries: int = 1


def console_request(line):
    data = line.strip().encode("cp1252")
    if not data or len(data) > CONSOLE_MAX or b"\n" in data or b"\0" in data:
        raise ValueError(f"console line must be 1-{CONSOLE_MAX} characters")
    return OP_CONSOLE, data


def read_request(path, offset, length=READ_CHUNK):
    data = path.encode("cp1252")
    if b"\0" in data or len(data) + 11 > MAX_REQUEST:
        raise ValueError(f"path too long: {path}")
    return OP_READ, struct.pack("<IH", offset, length) + data


def _path(path):
    data = path.encode("cp1252")
    if not data or b"\0" in data or len(data) > PATH_MAX:
        raise ValueError(f"bad console path: {path!r}")
    return data


def write_request(path, offset, data):
    """Write data at offset; offset 0 creates or truncates the file."""
    name = _path(path)
    return OP_WRITE, struct.pack("<IB", offset, len(name)) + name + bytes(data)


def list_request(path, start=0):
    return OP_LIST, struct.pack("<I", start) + _path(path)


def parse_list(payload):
    """(entries in the folder, [(name, is_dir, size)] from the requested start)."""
    total, count = struct.unpack_from("<IH", payload)
    at, entries = 6, []
    for _ in range(count):
        attrs, size, n = struct.unpack_from("<BIB", payload, at)
        entries.append((payload[at + 6:at + 6 + n].decode("cp1252"), bool(attrs & 1), size))
        at += 6 + n
    return total, entries


def path_request(op, path):
    """OP_DELETE (a file or an empty folder), OP_MKDIR or OP_LAUNCH (an XBE)."""
    return op, _path(path)


def rename_request(source, target):
    name = _path(source)
    return OP_RENAME, bytes([len(name)]) + name + _path(target)


class AgentProtocol:
    """Noise responder, replay gate and request retries; AgentListener owns the socket."""

    def __init__(self, secret, notify=lambda _event: None, clock=time.monotonic):
        if len(secret) != 32:
            raise ValueError("agent secret must be 32 bytes")
        self.secret, self.notify, self.clock = secret, notify, clock
        self.pending, self.peers = {}, {}
        self.requests, self.next_id = {}, 1

    def event(self, peer, kind, payload=b"", **extra):
        value = {"kind": kind, "address": peer.address, "session": peer.session,
                 "client_key": fingerprint(peer.public), "payload": payload}
        value.update(extra)
        self.notify(value)

    def receive(self, data, address):
        parsed = decode(data)
        if parsed is None:
            return []
        kind, session, sequence, header, body = parsed
        key = address, session
        now = self.clock()
        self.expire(now)
        if kind == HANDSHAKE1 and sequence == 0:
            old = self.pending.get(key)
            if old and old.request == body:
                return [(old.response, address)]
            try:
                noise = Noise(False, self.secret, os.urandom(32), PROLOGUE)
                noise.read1(body)
                response = encode(HANDSHAKE2, session, 0, noise.write2())
            except ValueError:
                return []
            if len(self.pending) >= 32:
                oldest = min(self.pending, key=lambda item: self.pending[item].created)
                del self.pending[oldest]
            self.pending[key] = Pending(noise, body, response, now)
            return [(response, address)]
        if kind == HANDSHAKE3 and sequence == 0:
            peer = self.peers.get(key)
            if peer is not None:
                return [(peer.welcome, address)]
            pending = self.pending.pop(key, None)
            if pending is None:
                return []
            try:
                hello = pending.noise.read3(body)
                keys = pending.noise.split()
            except ValueError:
                return []
            peer = Peer(address, session, pending.noise.rs, keys, hello, now)
            # A console pairs again with a new session after the listener restarts.
            for old in [old for old in self.peers if old[0] == address]:
                self.drop(old)
            self.peers[key] = peer
            peer.welcome = self.send(peer, WELCOME)
            self.event(peer, "connected", hello)
            return [(peer.welcome, address)]
        if kind != SEALED:
            return []
        peer = self.peers.get(key)
        if peer is None or address != peer.address or sequence <= peer.top:
            return []
        plain = unseal(peer.keys[0], sequence, header, body)
        if plain is None or not plain:
            return []
        gap = sequence - peer.top - 1
        peer.top, peer.last_seen, peer.stalled = sequence, now, False
        message, payload = plain[0], plain[1:]
        if message == REPLY:
            return self.reply(peer, payload)
        names = {HEARTBEAT: "heartbeat", LOG: "log", GOODBYE: "goodbye"}
        if message not in names:
            return []
        self.event(peer, names[message], payload, gap=gap)
        if message == GOODBYE:
            self.drop(key)
        elif message == HEARTBEAT:
            # The console re-pairs when these stop, e.g. after the listener restarts.
            return [(self.send(peer, WELCOME), address)]
        return []

    def reply(self, peer, payload):
        if len(payload) < 5:
            return []
        ident, status = struct.unpack_from("<IB", payload)
        request = self.requests.get(ident)
        if request is None or request.key != (peer.address, peer.session):
            return []
        if status == 1:  # busy: retry the same request
            return []
        del self.requests[ident]
        self.event(peer, "reply", payload[5:], id=ident, status=STATUS.get(status, str(status)))
        return []

    def drop(self, key):
        peer = self.peers.pop(key)
        for ident in [ident for ident, request in self.requests.items() if request.key == key]:
            del self.requests[ident]
            self.event(peer, "reply", b"", id=ident, status="disconnected")

    def peer_at(self, host, port=None):
        """The newest live session from a console address."""
        live = [peer for (address, _), peer in self.peers.items()
                if address[0] == host and (port is None or address[1] == port)]
        return max(live, key=lambda peer: peer.last_seen) if live else None

    def request(self, peer, op, args=b""):
        """Queue a request; returns (id, packets). Its reply arrives as a `reply` event."""
        ident = self.next_id
        self.next_id = self.next_id % 0xFFFFFFFF + 1
        body = struct.pack("<IB", ident, op) + args
        if len(body) > MAX_SEALED_REQUEST:
            raise ValueError("agent request is too large")
        key = peer.address, peer.session
        self.requests[ident] = Request(key, body, self.clock())
        return ident, [(self.send(peer, REQUEST, body), peer.address)]

    def retry(self, now=None):
        """Resend unanswered requests; give up after RETRIES."""
        now = self.clock() if now is None else now
        packets = []
        for ident, request in list(self.requests.items()):
            if now - request.sent < RETRY_SECONDS:
                continue
            peer = self.peers.get(request.key)
            if peer is None or request.tries >= RETRIES:
                del self.requests[ident]
                if peer is not None:
                    self.event(peer, "reply", b"", id=ident, status="timeout")
                continue
            request.sent, request.tries = now, request.tries + 1
            packets.append((self.send(peer, REQUEST, request.body), peer.address))
        return packets

    def send(self, peer, message, payload=b""):
        sequence = peer.outgoing
        size = 1 + len(payload) + 16
        header = HEADER.pack(MAGIC, VERSION, SEALED, size, peer.session, sequence)
        packet = header + seal(peer.keys[1], sequence, header, bytes([message]) + payload)
        peer.outgoing += 1
        return packet

    def expire(self, now=None):
        now = self.clock() if now is None else now
        for key in [key for key, value in self.pending.items()
                    if now - value.created > PENDING_SECONDS]:
            del self.pending[key]
        for peer in self.peers.values():
            if not peer.stalled and now - peer.last_seen > STALL_SECONDS:
                peer.stalled = True
                self.event(peer, "stalled")


class AgentListener(threading.Thread):
    """Background UDP listener. Notifications run on this thread."""

    def __init__(self, secret, notify, host="0.0.0.0", port=PORT):
        super().__init__(name="tes3x-agent", daemon=True)
        self.protocol = AgentProtocol(secret, notify)
        self.socket = udp_socket()  # a console gone away must not end the listener
        self.socket.bind((host, port))
        self.socket.settimeout(0.1)
        self.address = self.socket.getsockname()
        self.stopping = threading.Event()
        self.lock = threading.RLock()  # notify may send a request from this thread

    def transmit(self, packets):
        for packet, destination in packets:
            try:
                self.socket.sendto(packet, destination)
            except OSError:
                pass

    def run(self):
        while not self.stopping.is_set():
            try:
                data, address = self.socket.recvfrom(MAX_PACKET + 1)
            except socket.timeout:
                with self.lock:
                    self.protocol.expire()
                    packets = self.protocol.retry()
                self.transmit(packets)
                continue
            except ConnectionResetError:
                continue
            except OSError:
                break
            with self.lock:
                packets = self.protocol.receive(data, address) + self.protocol.retry()
            self.transmit(packets)

    def request(self, host, op, args=b"", port=None):
        """Send a request to the console at `host`; None when none is connected."""
        with self.lock:
            peer = self.protocol.peer_at(host, port)
            if peer is None:
                return None
            ident, packets = self.protocol.request(peer, op, args)
        self.transmit(packets)
        return ident

    def close(self):
        self.stopping.set()
        self.socket.close()
        if self.is_alive():
            self.join(1)


class Fetch:
    """Read one console file in windowed chunks; pass every reply event to `handle`."""

    def __init__(self, send, path, window=8):
        self.send, self.path, self.window = send, path, window
        self.size, self.error, self.done = None, None, False
        self.chunks, self.outstanding = {}, {}
        self.next_offset = 0

    def start(self):
        self.next_offset = READ_CHUNK
        self.issue(0)

    def issue(self, offset):
        ident = self.send(*read_request(self.path, offset))
        if ident is None:
            self.error = "no console is connected"
        else:
            self.outstanding[ident] = offset

    @property
    def received(self):
        return sum(len(chunk) for chunk in self.chunks.values())

    def data(self):
        return b"".join(self.chunks[offset] for offset in sorted(self.chunks))

    def handle(self, event):
        """True when the event answered one of this fetch's requests."""
        if event.get("kind") != "reply" or event.get("id") not in self.outstanding:
            return False
        offset = self.outstanding.pop(event["id"])
        if self.error or self.done:
            return True
        payload = event.get("payload", b"")
        if event.get("status") != "ok" or len(payload) < 8:
            self.error = f"{self.path}: {event.get('status')}"
            return True
        size, at = struct.unpack_from("<II", payload)
        if self.size is None:
            self.size = size  # later growth, as in a live log, is not followed
        want = max(0, min(READ_CHUNK, self.size - offset))
        if at != offset or len(payload) - 8 < want:
            self.error = f"{self.path}: changed while reading"
            return True
        self.chunks[offset] = payload[8:8 + want]
        while not self.error and len(self.outstanding) < self.window \
                and self.next_offset < self.size:
            self.next_offset += READ_CHUNK
            self.issue(self.next_offset - READ_CHUNK)
        self.done = not self.error and not self.outstanding and self.next_offset >= self.size
        return True


class Put:
    """Write one console file in chunks: the first alone, since it truncates the file, then the
    rest windowed. Pass every reply event to `handle`."""

    def __init__(self, send, path, data, window=8):
        self.send, self.path, self.data, self.window = send, path, bytes(data), window
        self.error, self.done = None, False
        self.outstanding = {}
        self.next_offset = 0
        self.written = 0

    def start(self):
        self.issue()

    def issue(self):
        offset = self.next_offset
        self.next_offset += WRITE_CHUNK
        ident = self.send(*write_request(self.path, offset,
                                         self.data[offset:offset + WRITE_CHUNK]))
        if ident is None:
            self.error = "no console is connected"
        else:
            self.outstanding[ident] = offset

    def handle(self, event):
        """True when the event answered one of this put's requests."""
        if event.get("kind") != "reply" or event.get("id") not in self.outstanding:
            return False
        offset = self.outstanding.pop(event["id"])
        if self.error or self.done:
            return True
        if event.get("status") != "ok":
            self.error = f"{self.path} at {offset}: {event.get('status')}"
            return True
        self.written += len(self.data[offset:offset + WRITE_CHUNK])
        while not self.error and len(self.outstanding) < self.window \
                and self.next_offset < len(self.data):
            self.issue()
        self.done = not self.error and not self.outstanding
        return True


def wait_for(events, test, timeout):
    """The first queued event that passes `test`, or None."""
    deadline = time.monotonic() + timeout
    while (left := deadline - time.monotonic()) > 0:
        try:
            event = events.get(timeout=left)
        except queue.Empty:
            return None
        if test(event):
            return event
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--key", default="tes3x.agent.key",
                    help="raw X25519 secret (default: tes3x.agent.key)")
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--duration", type=float, help="stop after this many seconds")
    ap.add_argument("--from", dest="source", metavar="HOST",
                    help="act only on the console at this address")
    ap.add_argument("--wait", type=float, default=60,
                    help="seconds to wait for a console before acting (default 60)")
    ap.add_argument("--after", metavar="REGEX",
                    help="act only once a game log line matches this")
    ap.add_argument("--console", action="append", default=[], metavar="LINE",
                    help="run an in-game console line, or reboot (repeatable)")
    ap.add_argument("--fetch", action="append", default=[], metavar="PATH",
                    help="copy a console file, such as E:/tes3xprof.bin (repeatable)")
    ap.add_argument("--out", default=".", help="folder for fetched files")
    ap.add_argument("--mkdir", action="append", default=[], metavar="PATH",
                    help="manager: make a folder (repeatable)")
    ap.add_argument("--put", action="append", default=[], metavar="PATH=LOCAL",
                    help="manager: write a local file to the console (repeatable)")
    ap.add_argument("--rename", action="append", default=[], metavar="FROM=TO",
                    help="manager: rename a console file or folder (repeatable)")
    ap.add_argument("--list", action="append", default=[], metavar="PATH",
                    help="manager: list a console folder (repeatable)")
    ap.add_argument("--delete", action="append", default=[], metavar="PATH",
                    help="manager: delete a console file or empty folder (repeatable)")
    ap.add_argument("--launch", metavar="XBE", help="manager: start an XBE on the console")
    ap.add_argument("--exit", action="store_true",
                    help="after the rest, shut the console down (needs the console patch)")
    ap.add_argument("--reboot", action="store_true", help="return the console to its dashboard")
    ap.add_argument("--until-end", action="store_true",
                    help="after any actions, wait for the game to say goodbye or stall; "
                         "exit 0 on goodbye, 3 on a stall or timeout")
    ap.add_argument("--quiet", action="store_true", help="do not print log lines and heartbeats")
    args = ap.parse_args(argv)
    secret = load_or_create_key(args.key)
    started = time.monotonic()
    events = queue.Queue()

    def report(event):
        events.put(event)
        payload = event.get("payload", b"")
        if event["kind"] == "reply" or args.quiet and event["kind"] in {"heartbeat", "log"}:
            return
        if event["kind"] == "heartbeat" and len(payload) >= 16:
            now, frame, free, dropped = struct.unpack_from("<IIII", payload)
            detail = f" time_us={now} frame_us={frame} free_kb={free} dropped={dropped}"
        elif event["kind"] == "log":
            detail = " " + payload.decode("cp1252", "replace")
        else:
            detail = f" bytes={len(payload)}"
        print(f"{event['kind']} {event['address'][0]}:{event['address'][1]}"
              f" session={event['session']:08x} gap={event.get('gap', 0)}{detail}", flush=True)

    listener = AgentListener(secret, report, args.bind, args.port)
    listener.start()
    print(f"agent fingerprint {key_fingerprint(secret)}; listening on "
          f"{listener.address[0]}:{listener.address[1]}", flush=True)
    code = 0
    try:
        acting = (args.console or args.fetch or args.exit or args.reboot or args.until_end
                  or args.mkdir or args.put or args.rename or args.list or args.delete
                  or args.launch)
        if not acting:
            while args.duration is None or time.monotonic() - started < args.duration:
                time.sleep(0.25)
            return 0
        first = wait_for(events, lambda e: e["kind"] in {"connected", "heartbeat"} and (
            args.source is None or e["address"][0] == args.source), args.wait)
        if first is None:
            print("agent: no console connected", file=sys.stderr)
            return 2
        host = first["address"][0]
        if args.after:
            pattern = re.compile(args.after)
            if not wait_for(events, lambda e: e["address"][0] == host and e["kind"] == "log" and
                            pattern.search(e["payload"].decode("cp1252", "replace")), args.wait):
                print(f"agent: no log line matched {args.after!r}", file=sys.stderr)
                return 2
        code = act(listener, events, host, args)
        if args.until_end and not code:
            left = args.duration - (time.monotonic() - started) if args.duration else 1e9
            end = wait_for(events, lambda e: e["address"][0] == host and e["kind"] in
                           {"goodbye", "stalled"}, left)
            code = 0 if end and end["kind"] == "goodbye" else 3
            print(f"agent: {end['kind'] if end else 'timed out'}", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        listener.close()
    return code


def act(listener, events, host, args):
    """Run the console lines, folders, puts, renames, lists, fetches, deletes, launch, exit and
    reboot in that order; nonzero on failure."""
    def send(op, data):
        return listener.request(host, op, data)

    def answer(ident, label):
        if ident is None:
            print(f"agent: {label}: no console is connected", file=sys.stderr)
            return False
        reply = wait_for(events, lambda e: e["kind"] == "reply" and e.get("id") == ident,
                         RETRY_SECONDS * (RETRIES + 2))
        status = reply["status"] if reply else "no reply"
        print(f"agent: {label}: {status}", flush=True)
        return status == "ok"

    def wait_reply(ident):
        return wait_for(events, lambda e: e["kind"] == "reply" and e.get("id") == ident,
                        RETRY_SECONDS * (RETRIES + 2))

    def run(transfer, label):
        transfer.start()
        while not transfer.done and not transfer.error:
            event = wait_for(events, lambda e: e["kind"] == "reply",
                             RETRY_SECONDS * (RETRIES + 2))
            if event is None:
                transfer.error = f"{label}: no reply"
            else:
                transfer.handle(event)
        if transfer.error:
            print(f"agent: {transfer.error}", file=sys.stderr)
        return not transfer.error

    for line in args.console:
        if not answer(send(*console_request(line)), line):
            return 1
    for path in args.mkdir:
        if not answer(send(*path_request(OP_MKDIR, path)), f"mkdir {path}"):
            return 1
    for spec in args.put:
        path, _, local = spec.partition("=")
        data = Path(local).read_bytes()
        started = time.monotonic()
        if not run(Put(send, path, data), path):
            return 1
        seconds = time.monotonic() - started
        print(f"agent: put {local} -> {path} ({len(data)} bytes, {seconds:.1f} s)", flush=True)
    for spec in args.rename:
        source, _, target = spec.partition("=")
        if not answer(send(*rename_request(source, target)), f"rename {source}"):
            return 1
    for path in args.list:
        start, total = 0, None
        while total is None or start < total:
            reply = wait_reply(send(*list_request(path, start)))
            if reply is None or reply["status"] != "ok":
                print(f"agent: list {path}: {reply['status'] if reply else 'no reply'}",
                      file=sys.stderr)
                return 1
            total, entries = parse_list(reply["payload"])
            for name, is_dir, size in entries:
                print(f"  {name}{'/' if is_dir else ''}" + ("" if is_dir else f"  {size}"))
            if not entries:
                break
            start += len(entries)
        print(f"agent: list {path}: {total} entries", flush=True)
    for path in args.fetch:
        fetch = Fetch(send, path)
        fetch.start()
        while not fetch.done and not fetch.error:
            event = wait_for(events, lambda e: e["kind"] == "reply",
                             RETRY_SECONDS * (RETRIES + 2))
            if event is None:
                fetch.error = f"{path}: no reply"
            else:
                fetch.handle(event)
        if fetch.error:
            print(f"agent: fetch {fetch.error}", file=sys.stderr)
            return 1
        out = Path(args.out) / PureWindowsPath(path).name
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(fetch.data())
        print(f"agent: fetched {path} -> {out} ({fetch.size} bytes)", flush=True)
    for path in args.delete:
        if not answer(send(*path_request(OP_DELETE, path)), f"delete {path}"):
            return 1
    if args.launch and not answer(send(*path_request(OP_LAUNCH, args.launch)),
                                  f"launch {args.launch}"):
        return 1
    if args.exit and not answer(send(*console_request("exit")), "exit"):
        return 1
    if args.reboot and not answer(send(OP_REBOOT, b""), "reboot"):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
