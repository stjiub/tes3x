"""Reliable delivery, rate buckets, and the per-client connection record."""

import hashlib
import ipaddress
import os
import struct
import time
from .proto import (BULK_ACK_BODY, BULK_BAD_HASH, BULK_CHUNK, BULK_DONE, BULK_MAX, BULK_OFFER,
                    BULK_PROBE, BULK_RECEIVING, BULK_REFUSED, BULK_RESEND, BULK_WINDOW_IN,
                    CLIENT_RATE, CLOCK_BODY, describe_state, DEVICE_NAMES, HANDSHAKES_PENDING,
                    MONTH_DAYS, pack_events, plain_name, T3MP_VERSION, unpack_events, UPLOAD_FILES,
                    zstr)


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
        self.stream = open(part, "r+b" if have else "wb")  # noqa: SIM115
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


class Clock:
    """The session's game time, run by the server: it advances at TimeScale game seconds per real
    second and rolls days, months and years over as the game does."""

    def __init__(self, hour, day, month, year, days_passed, scale, now):
        self.hour, self.day, self.month, self.year = hour, int(day), int(month), int(year)
        self.days_passed, self.scale, self.last = int(days_passed), scale, now

    def advance(self, now):
        self.skip((now - self.last) * self.scale / 3600)
        self.last = now

    def skip(self, hours):
        """Move the time on by hours, rolling days, months and years over."""
        self.hour += hours
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


class SourceBuckets:
    """Bounded source limits; evict only buckets whose entire burst has recovered."""

    def __init__(self, burst, rate, limit=HANDSHAKES_PENDING):
        self.burst, self.rate, self.limit = burst, rate, limit
        self.buckets = {}

    @staticmethod
    def source(host):
        address = ipaddress.ip_address(host.split('%', 1)[0])
        if isinstance(address, ipaddress.IPv6Address):
            if address.ipv4_mapped:
                return str(address.ipv4_mapped)
            return str(ipaddress.ip_network((address, 64), strict=False))
        return str(address)

    def get(self, host, now):
        source = self.source(host)
        if source in self.buckets:
            return self.buckets[source]
        if len(self.buckets) >= self.limit:
            recovered = next((key for key, bucket in self.buckets.items()
                              if bucket.tokens + (now - bucket.last) * bucket.rate
                              >= bucket.burst), None)
            if recovered is None:
                return None
            del self.buckets[recovered]
        bucket = self.buckets[source] = Bucket(self.burst, self.rate, now)
        return bucket


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
        self.record_in = None  # [parts, next part, bytes] of a record being received
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


def summary(client, prefix=""):
    rel = client.rel
    return (f"joins {client.joins}, heartbeats {client.beats}, states {client.states}, "
            f"actor states {client.actor_states}, "
            f"gaps {client.gaps}, events in {client.events} (stale {rel.stale}), out "
            f"{rel.out_next - 1} (sent {rel.sent}, resent {rel.resent}, unacked {len(rel.out)}), "
            f"dropped forged {client.forged}, replayed {client.replayed}, "
            f"over rate {client.limited}"
            + (f"; {prefix}{describe_state(client.state)}" if client.state else ""))
