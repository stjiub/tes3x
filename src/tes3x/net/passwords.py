"""Join and admin passwords as the server stores them, and private files."""

import getpass
import hashlib
import hmac
import os
import stat
import sys

from .proto import ADMIN_PASSWORD_MIN, PASSWORD_MAX, admin_secret

JOIN_PREFIX = "scrypt$"
ADMIN_PREFIX = "secret$"
SCRYPT = {"n": 1 << 14, "r": 8, "p": 1, "dklen": 32}


def join_hash(password, salt=None):
    """The file line that stands for a join password: scrypt$SALT$HASH."""
    salt = salt or os.urandom(16)
    return f"{JOIN_PREFIX}{salt.hex()}${hashlib.scrypt(password, salt=salt, **SCRYPT).hex()}"


class JoinPassword:
    """The password a new console must give: plain text, or a join_hash line."""

    def __init__(self, line):
        self.plain = self.salt = self.digest = None
        if line.startswith(JOIN_PREFIX):
            salt, _, digest = line[len(JOIN_PREFIX):].partition("$")
            self.salt, self.digest = bytes.fromhex(salt), bytes.fromhex(digest)
        else:
            self.plain = line.encode("ascii")

    def check(self, given):
        if self.plain is not None:
            return hmac.compare_digest(given, self.plain)
        if not 0 < len(given) <= PASSWORD_MAX:
            return False
        return hmac.compare_digest(hashlib.scrypt(given, salt=self.salt, **SCRYPT), self.digest)


def admin_hash(password):
    """The file line that stands for an admin password: secret$KEY, the key both ends derive
    their commands' keys from. Whoever holds it can send admin commands, but cannot learn a
    password that was used elsewhere."""
    return ADMIN_PREFIX + admin_secret(password).hex()


def admin_secret_from(line):
    """The admin key for a file line: a admin_hash, or a password to stretch."""
    if line.startswith(ADMIN_PREFIX):
        return bytes.fromhex(line[len(ADMIN_PREFIX):])
    return admin_secret(line.encode("utf-8"))


def first_line(path):
    with open(path, encoding="utf-8") as stream:
        return stream.readline().strip()


def write_private(path, text):
    """Write a file only its owner can read (mode 0600 where the system has modes)."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(text)


def warn_if_readable(path, what):
    """Say so when other accounts can read a secret file; modes mean nothing on Windows."""
    if os.name == "nt" or not path:
        return
    try:
        mode = os.stat(path).st_mode
    except OSError:
        return
    if mode & (stat.S_IRGRP | stat.S_IROTH):
        print(f"warning: {what} {path} is readable by other accounts; "
              f"run chmod 600 {path}", file=sys.stderr, flush=True)


def hash_password_command(args):
    """tes3x net hash-password: print the file line for a password asked for, not echoed."""
    password = getpass.getpass("password: ")
    if getpass.getpass("again: ") != password:
        sys.exit("the two entries differ")
    if args.admin:
        if len(password) < ADMIN_PASSWORD_MIN or not password.isprintable():
            sys.exit(f"the admin password must be at least {ADMIN_PASSWORD_MIN} printable "
                     "characters")
        line = admin_hash(password.encode("utf-8"))
    else:
        if not 0 < len(password) <= PASSWORD_MAX or not password.isascii() or \
                not password.isprintable():
            sys.exit(f"the password must be 1 to {PASSWORD_MAX} printable ASCII characters")
        line = join_hash(password.encode("ascii"))
    if args.out:
        write_private(args.out, line + "\n")
        print(f"wrote {args.out}")
    else:
        print(line)
    return 0
