#!/usr/bin/env python3
"""Receive the payload's network test datagrams.

    python tools/tes3x_net.py listen                  # console on the LAN, UDP port 26500
    python tools/tes3x_net.py listen --tunnel 9369    # xemu's udp backend, raw Ethernet frames

A tunnel port is xemu's net.udp.remote_addr: xemu sends each guest frame to it as one datagram.
"""

import argparse
import socket
import struct
import sys
import time

PORT = 26500
MAGIC = b"TES3XNET"


def decode(payload):
    """(seq, count, mac) of a test datagram, or None."""
    if len(payload) < 22 or payload[:8] != MAGIC:
        return None
    seq, count = struct.unpack_from("<II", payload, 8)
    return seq, count, payload[16:22].hex(":")


def udp_from_frame(frame, port=PORT):
    """UDP payload of an IPv4 frame to port, or None."""
    if len(frame) < 42 or frame[12:14] != b"\x08\x00":
        return None
    ihl = (frame[14] & 0x0F) * 4
    if frame[23] != 17:
        return None
    udp = 14 + ihl
    if struct.unpack_from(">H", frame, udp + 2)[0] != port:
        return None
    length = struct.unpack_from(">H", frame, udp + 4)[0]
    return frame[udp + 8:udp + length]


def listen(args):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    port = args.tunnel or args.port
    sock.bind((args.bind, port))
    sock.settimeout(0.5)
    mode = "xemu tunnel" if args.tunnel else "udp"
    print(f"listening on {args.bind}:{port} ({mode})", flush=True)
    seen, frames = set(), 0
    expected = None
    deadline = time.time() + args.timeout if args.timeout else None
    while deadline is None or time.time() < deadline:
        try:
            data, peer = sock.recvfrom(2048)
        except socket.timeout:
            continue
        if args.tunnel:
            frames += 1
            data = udp_from_frame(data)
            if data is None:
                continue
        hit = decode(data)
        if not hit:
            continue
        seq, count, mac = hit
        seen.add(seq)
        expected = count
        print(f"seq {seq}/{count} from {mac} via {peer[0]}", flush=True)
        if len(seen) >= count:
            break
    if args.tunnel:
        print(f"frames: {frames}")
    missing = sorted(set(range(expected)) - seen) if expected else []
    print(f"received {len(seen)}" + (f" of {expected}" if expected else "")
          + (f"; missing {missing}" if missing else ""))
    return 0 if expected and not missing else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("listen", help="print test datagrams until a full run arrives")
    p.add_argument("--port", type=int, default=PORT)
    p.add_argument("--tunnel", type=int, metavar="PORT",
                   help="read raw frames from xemu's udp backend on this port")
    p.add_argument("--bind", default="0.0.0.0")
    p.add_argument("--timeout", type=float, help="give up after this many seconds")
    args = ap.parse_args(argv)
    return listen(args)


if __name__ == "__main__":
    sys.exit(main())
