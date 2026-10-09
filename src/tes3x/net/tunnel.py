"""Ethernet peer for xemu's UDP tunnel."""

import random
import socket
import struct
import time
from .proto import (BROADCAST, DHCP_MAGIC, GUEST_IP, PEER_IP, PEER_MAC, PORT)

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
