"""Authenticated UDP listener for the in-game TES3X agent."""

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import socket
import struct
import threading
import time

from tes3x_net import Noise, fingerprint, seal, unseal, x25519_public


MAGIC = b"T3AG"
VERSION = 1
PORT = 26501
PROLOGUE = b"TES3X in-game agent v1"
HEADER = struct.Struct("<4sBBHII")  # magic, version, kind, body bytes, session, sequence
HANDSHAKE1, HANDSHAKE2, HANDSHAKE3, SEALED = range(1, 5)
WELCOME, HEARTBEAT, LOG, GOODBYE, REPLY = range(5)
MAX_PACKET = 1400
PENDING_SECONDS = 5.0
STALL_SECONDS = 3.0


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


class AgentProtocol:
    """Noise responder and replay gate; socket ownership stays with AgentListener."""

    def __init__(self, secret, notify=lambda _event: None, clock=time.monotonic):
        if len(secret) != 32:
            raise ValueError("agent secret must be 32 bytes")
        self.secret, self.notify, self.clock = secret, notify, clock
        self.pending, self.peers = {}, {}

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
        names = {HEARTBEAT: "heartbeat", LOG: "log", GOODBYE: "goodbye", REPLY: "reply"}
        if message not in names:
            return []
        self.event(peer, names[message], payload, gap=gap)
        if message == GOODBYE:
            del self.peers[key]
        return []

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
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind((host, port))
        self.socket.settimeout(0.25)
        self.address = self.socket.getsockname()
        self.stopping = threading.Event()

    def run(self):
        while not self.stopping.is_set():
            try:
                data, address = self.socket.recvfrom(MAX_PACKET + 1)
            except socket.timeout:
                self.protocol.expire()
                continue
            except OSError:
                break
            for packet, destination in self.protocol.receive(data, address):
                try:
                    self.socket.sendto(packet, destination)
                except OSError:
                    break

    def close(self):
        self.stopping.set()
        self.socket.close()
        if self.is_alive():
            self.join(1)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--key", default="tes3x.agent.key",
                    help="raw X25519 secret (default: tes3x.agent.key)")
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--duration", type=float, help="stop after this many seconds")
    args = ap.parse_args(argv)
    secret = load_or_create_key(args.key)
    started = time.monotonic()

    def report(event):
        payload = event.get("payload", b"")
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
    try:
        while args.duration is None or time.monotonic() - started < args.duration:
            time.sleep(0.25)
    except KeyboardInterrupt:
        pass
    finally:
        listener.close()


if __name__ == "__main__":
    main()
