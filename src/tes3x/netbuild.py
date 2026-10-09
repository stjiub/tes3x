"""A server's build, handed to the console manager: the session tells an authenticated manager
the manifest's SHA-256 and a ticket, and this serves the manifest, the files and the XBE deltas
over plain HTTP. Nothing here needs to be secret or authentic on the wire: the manager trusts a
file only by the hash in a manifest whose own hash came through the session."""

import hashlib
import http.server
import secrets
import struct
import threading
import time
import urllib.parse
from pathlib import Path

import tes3x.manifest as tes3x_manifest

# BUILD: the manifest's SHA-256, its size (0: no build served), the HTTP port, a ticket
BUILD_BODY = struct.Struct("<32sIH2x16s")
TICKET_SECONDS = 6 * 3600
TICKETS = 4096
ORIGINS = ("build", "retail", "xbe")
SERVED_BY_DEFAULT = ("build",)


class Build:
    """The staged game folder a server hands out, reread when its manifest changes."""

    def __init__(self, root, deltas=None, origins=SERVED_BY_DEFAULT):
        self.root = Path(root)
        self.deltas = Path(deltas) if deltas else self.root.parent / "deltas"
        self.origins = set(origins)
        self.stamp = None
        self.load()

    def load(self):
        path = self.root / tes3x_manifest.NAME
        stamp = path.stat().st_mtime_ns
        if stamp == self.stamp:
            return
        data = path.read_bytes()
        manifest = tes3x_manifest.parse(data)
        self.data, self.stamp = data, stamp
        self.sha256 = hashlib.sha256(data).digest()
        self.build_id = bytes.fromhex(manifest.get("build") or tes3x_manifest.build_id(manifest))
        self.files = {p.lower(): (p, e) for p, e in manifest["files"].items()}
        self.delta_files = {x["delta"]["sha256"]: x["delta"]["size"]
                            for x in manifest.get("xbe", ()) if x.get("delta")}
        self.profile = manifest.get("profile")

    def describe(self):
        served = sum(1 for _, e in self.files.values() if e.get("origin") in self.origins)
        return (f"build '{self.profile}' from {self.root}: {len(self.files)} files, {served} "
                f"served ({', '.join(sorted(self.origins))}), {len(self.delta_files)} XBE delta(s)")

    def file(self, relative):
        """The local path of a file the policy serves, or None."""
        entry = self.files.get(relative.replace("\\", "/").lower())
        if not entry or entry[1].get("origin") not in self.origins:
            return None
        path = self.root / entry[0]
        return path if path.is_file() and path.stat().st_size == entry[1]["size"] else None

    def delta(self, digest):
        if digest not in self.delta_files:
            return None
        path = self.deltas / f"{digest}.zst"
        return path if path.is_file() and path.stat().st_size == self.delta_files[digest] else None


class BuildServer:
    """The HTTP side: GET /TICKET/manifest, /TICKET/file/PATH and /TICKET/delta/SHA256."""

    def __init__(self, build, bind, port, log=print):
        self.build, self.log = build, log
        self.tickets = {}
        self.lock = threading.Lock()
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self):
                owner.get(self)

            def log_message(self, *args):
                pass

        self.http = http.server.ThreadingHTTPServer((bind, port), Handler)
        self.http.daemon_threads = True
        self.port = self.http.server_address[1]
        threading.Thread(target=self.http.serve_forever, daemon=True).start()

    def build_id(self):
        """The served build's id, or None while its manifest is unreadable."""
        try:
            self.build.load()
        except (OSError, ValueError) as error:
            self.log(f"build unreadable: {error}")
            return None
        return self.build.build_id

    def ticket(self):
        """The BUILD body for a manager that has proved its key."""
        try:
            self.build.load()
        except (OSError, ValueError) as error:
            self.log(f"build unreadable: {error}")
            return BUILD_BODY.pack(bytes(32), 0, self.port, bytes(16))
        token = secrets.token_bytes(16)
        now = time.monotonic()
        with self.lock:
            for old in [t for t, end in self.tickets.items() if end < now]:
                del self.tickets[old]
            if len(self.tickets) >= TICKETS:
                self.tickets.pop(next(iter(self.tickets)))
            self.tickets[token.hex()] = now + TICKET_SECONDS
        return BUILD_BODY.pack(self.build.sha256, len(self.build.data), self.port, token)

    def valid(self, token):
        with self.lock:
            return self.tickets.get(token, 0) >= time.monotonic()

    def get(self, request):
        parts = urllib.parse.unquote(urllib.parse.urlsplit(request.path).path).split("/", 3)
        if len(parts) < 3 or parts[0] or not self.valid(parts[1]):
            return self.reply(request, 403)
        what = parts[2]
        if what == "manifest" and len(parts) == 3:
            return self.reply(request, 200, self.build.data)
        path = None
        if what == "file" and len(parts) == 4:
            path = self.build.file(parts[3])
        elif what == "delta" and len(parts) == 4:
            path = self.build.delta(parts[3])
        if path is None:
            return self.reply(request, 404)
        self.reply(request, 200, path=path)

    def reply(self, request, status, data=b"", path=None):
        size = path.stat().st_size if path else len(data)
        request.send_response(status)
        request.send_header("Content-Type", "application/octet-stream")
        request.send_header("Content-Length", str(size))
        request.end_headers()
        if path is None:
            request.wfile.write(data)
            return
        with open(path, "rb") as stream:
            while chunk := stream.read(1 << 16):
                request.wfile.write(chunk)

    def close(self):
        self.http.shutdown()
        self.http.server_close()


def fetch(host, port, ticket, what, timeout=30):
    """GET one of the paths BuildServer serves; the body, or None on a refusal."""
    import http.client
    connection = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        connection.request("GET", f"/{ticket.hex()}/{urllib.parse.quote(what)}")
        response = connection.getresponse()
        body = response.read()
        return body if response.status == 200 else None
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit("used by tes3x_net.py serve --build")
