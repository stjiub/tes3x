"""Server key, join password, ban list and admitted-player files."""

import os
import socket
import sys
from .passwords import (ADMIN_PREFIX, admin_secret_from, first_line, JOIN_PREFIX, JoinPassword,
                        warn_if_readable, write_private)
from .proto import (ADMIN_PASSWORD_MIN, admin_secret, BAN_KINDS, crypto, fingerprint, PASSWORD_MAX,
                    x25519_public)


def load_server_key(args):
    """The server's static secret: from --key, or server.key in the world folder, made on first
    use; without either, a new one each run."""
    crypto()
    path = args.key or (os.path.join(args.world, "server.key") if args.world else None)
    if path and os.path.exists(path):
        warn_if_readable(path, "the server key")
        with open(path, encoding="ascii") as stream:
            secret = bytes.fromhex(stream.read().strip())
    else:
        secret = os.urandom(32)
        if path:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            write_private(path, secret.hex() + "\n")
    print(f"server key {fingerprint(x25519_public(secret))}"
          + ("" if path else " (not kept: give --key or --world)"), flush=True)
    return secret


def load_password(args):
    """The password in --password-file (plain, or a hash line), or None for an open server."""
    if not args.password_file:
        return None
    warn_if_readable(args.password_file, "the password file")
    line = first_line(args.password_file)
    if line.startswith(JOIN_PREFIX):
        try:
            return JoinPassword(line)
        except ValueError:
            sys.exit(f"{args.password_file}: not a hash made by tes3x net hash-password")
    # The console reads NetPassword from its ini, which trims spaces and has no Unicode.
    if not 0 < len(line) <= PASSWORD_MAX or not line.isascii() or not line.isprintable():
        sys.exit(f"{args.password_file}: the password must be 1 to {PASSWORD_MAX} printable "
                 "ASCII characters")
    return JoinPassword(line)


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


def load_admin_secret(path):
    """The key remote admin commands are made with: from an admin_hash line, or a password."""
    warn_if_readable(path, "the admin password file")
    line = first_line(path)
    if line.startswith(ADMIN_PREFIX):
        try:
            return admin_secret_from(line)
        except ValueError:
            sys.exit(f"{path}: not a hash made by tes3x net hash-password --admin")
    return admin_secret(load_admin_password(path))


def load_admin_password(path):
    """The first line of an admin password file, as bytes."""
    with open(path, encoding="utf-8") as stream:
        password = stream.readline().strip()
    if len(password) < ADMIN_PASSWORD_MIN or not password.isprintable():
        sys.exit(f"{path}: the admin password must be at least {ADMIN_PASSWORD_MIN} printable "
                 "characters")
    return password.encode("utf-8")
