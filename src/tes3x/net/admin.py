"""Remote administration over the sealed admin port."""

import os
import time
from .proto import (PASSWORD_RATE, REMOTE_CHALLENGE, REMOTE_COMMAND, REMOTE_HEAD, REMOTE_HELLO,
                    remote_key, REMOTE_MAGIC, REMOTE_NONCE, REMOTE_NONCE_SECONDS, REMOTE_NONCES,
                    REMOTE_REFUSED, REMOTE_REPLY, REMOTE_VERSION, seal, unseal)
from .transport import SourceBuckets


def quiet_admin(line):
    """Whether an admin command goes unlogged: `list`, which the GUI polls."""
    return line.strip().lower() == b"list"


class RemoteAdmin:
    """The server's remote admin listener; run(line) answers an authenticated command."""

    def __init__(self, secret, sock, clock=time.monotonic):
        self.secret, self.sock, self.clock = secret, sock, clock
        self.nonces = {}  # nonce -> (address, issued)
        self.failures = SourceBuckets(*PASSWORD_RATE)

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
        bucket = self.failures.get(addr[0], now)
        nonce = data[REMOTE_HEAD.size:REMOTE_HEAD.size + REMOTE_NONCE]
        issued = self.nonces.pop(nonce, None)
        if bucket is None or not bucket.available(now):
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
