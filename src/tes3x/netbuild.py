"""A server's build, handed to the Dash: the session tells an authenticated Dash
the manifest's SHA-256 and a ticket, and this serves the manifest, the files and the XBE deltas
over plain HTTP. Nothing here needs to be secret or authentic on the wire: the Dash trusts a
file only by the hash in a manifest whose own hash came through the session."""

import hashlib
import http.server
import json
import secrets
import ssl
import struct
import threading
import time
import urllib.parse
from pathlib import Path

import tes3x.manifest as tes3x_manifest

# BUILD: the manifest's SHA-256, its size (0: no build served), the HTTP port, a ticket
BUILD_BODY = struct.Struct("<32sIH2x16s")
DASH_INFO = struct.Struct('<4sHH')


def dash_info(name, description, players, maximum):
    def field(value, limit):
        return value.replace('\0', '').encode('latin-1', 'replace')[:limit] + b'\0'
    return (DASH_INFO.pack(b'INFO', min(65535, max(0, players)), min(65535, max(0, maximum)))
            + field(name, 63) + field(description, 191))


TICKET_SECONDS = 6 * 3600
TICKETS = 4096
MAX_CONNECTIONS = 32
MAX_PER_CLIENT = 8
IDLE_TIMEOUT = 30
ORIGINS = ("build", "retail", "xbe")
SERVED_BY_DEFAULT = ("build",)


def retail_entries(root):
    entries = {}
    for name, path in tes3x_manifest.tree_files(root).items():
        top = Path(name.split('/')[0])
        suffix = top.suffix.lower()
        release_part = len(suffix) == 4 and suffix[1] == 'r' and suffix[2:].isdigit()
        if (top.name.lower() in {'morrowind.ini', '_resources'}
                or suffix in {'.iso', '.nfo', '.rar', '.sfv'} or release_part):
            continue
        entries[name] = {'size': path.stat().st_size,
                         'sha256': tes3x_manifest.sha256_file(path),
                         'origin': 'xbe' if name.lower().endswith('.xbe') else 'retail'}
    return entries


class Build:
    """The staged game folder a server hands out, reread when its manifest changes."""

    def __init__(self, root, deltas=None, origins=SERVED_BY_DEFAULT, retail_base=None):
        self.root = Path(root)
        self.deltas = Path(deltas) if deltas else self.root.parent / "deltas"
        self.origins = set(origins)
        self.retail_base = Path(retail_base) if retail_base else None
        self.stamp = None
        self.load()

    def load(self):
        path = self.root / tes3x_manifest.NAME
        stamp = path.stat().st_mtime_ns
        if stamp == self.stamp:
            return
        data = path.read_bytes()
        manifest = tes3x_manifest.parse(data)
        self.retail_files = {}
        if manifest.get("install_layout") == "overlay" and self.retail_base:
            entries = retail_entries(self.retail_base)
            required = {'morrowind.xbe', 'data files/morrowind.esm', 'data files/morrowind.bsa'}
            if not required <= {name.lower() for name in entries}:
                raise ValueError('the server retail base lacks Morrowind.xbe, '
                                 'Morrowind.esm or Morrowind.bsa')
            manifest["retail_base"] = tes3x_manifest.create(
                self.retail_base, profile='retail', source={'kind': 'retail'},
                install_layout='retail-base', files=entries)
            manifest['manager'] = max(manifest.get('manager', 1), 2)
            self.retail_files = {name.lower(): (name, entry) for name, entry in entries.items()}
            data = (json.dumps(manifest, indent=1) + '\n').encode('utf-8')
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

    def retail_file(self, relative):
        entry = self.retail_files.get(relative.replace('\\', '/').lower())
        if not entry or entry[1]['origin'] not in self.origins:
            return None
        path = self.retail_base / entry[0]
        return path if path.is_file() and path.stat().st_size == entry[1]['size'] else None


class BuildServer:
    """The HTTP side: GET /TICKET/manifest, /TICKET/file/PATH and /TICKET/delta/SHA256."""

    def __init__(self, build, bind, port, log=print, max_connections=MAX_CONNECTIONS, tls=None,
                 per_client=MAX_PER_CLIENT):
        self.build, self.log = build, log
        self.tickets = {}
        self.lock = threading.Lock()
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self):
                try:
                    owner.get(self)
                except (ConnectionError, TimeoutError) as error:
                    owner.log(f"build download disconnected: {self.client_address[0]}: {error}")
                    self.close_connection = True

            def log_message(self, *args):
                pass

        class LimitedHTTPServer(http.server.ThreadingHTTPServer):
            def __init__(self, address, handler):
                self.slots = threading.BoundedSemaphore(max_connections)
                self.clients = {}
                super().__init__(address, handler)

            def get_request(self):
                request, address = super().get_request()
                request.settimeout(IDLE_TIMEOUT)
                return request, address

            def process_request(self, request, address):
                with owner.lock:
                    if self.clients.get(address[0], 0) >= per_client:
                        request.close()
                        return
                    self.clients[address[0]] = self.clients.get(address[0], 0) + 1
                if not self.slots.acquire(blocking=False):
                    self.release_client(address)
                    self.shutdown_request(request)
                    return
                try:
                    super().process_request(request, address)
                except Exception:
                    self.slots.release()
                    self.release_client(address)
                    raise

            def release_client(self, address):
                with owner.lock:
                    left = self.clients.get(address[0], 1) - 1
                    if left:
                        self.clients[address[0]] = left
                    else:
                        self.clients.pop(address[0], None)

            def process_request_thread(self, request, address):
                try:
                    if tls is not None:
                        try:
                            request = tls.wrap_socket(request, server_side=True)
                        except (OSError, ssl.SSLError):
                            request.close()
                            return
                    super().process_request_thread(request, address)
                finally:
                    self.slots.release()
                    self.release_client(address)

        self.http = LimitedHTTPServer((bind, port), Handler)
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
        """The BUILD body for a Dash that has proved its key."""
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

    def build_for(self, token):
        return self.build if self.valid(token) else None

    def get(self, request):
        parts = urllib.parse.unquote(urllib.parse.urlsplit(request.path).path).split("/", 3)
        build = self.build_for(parts[1]) if len(parts) >= 3 and not parts[0] else None
        if build is None:
            return self.reply(request, 403)
        what = parts[2]
        if what == "manifest" and len(parts) == 3:
            return self.reply(request, 200, build.data, token=parts[1], what="manifest")
        path = None
        if what == "file" and len(parts) == 4:
            path = build.file(parts[3])
        elif what == "retail" and len(parts) == 4:
            path = build.retail_file(parts[3])
        elif what == "delta" and len(parts) == 4:
            path = build.delta(parts[3])
        if path is None:
            return self.reply(request, 404)
        self.reply(request, 200, path=path, token=parts[1], what=parts[3])

    def on_transfer(self, token, what, sent):
        pass

    def pace(self, address, sent):
        """Called after each chunk sent to address; a subclass may sleep to cap bandwidth."""

    def reply(self, request, status, data=b"", path=None, token=None, what=""):
        size = path.stat().st_size if path else len(data)
        start = 0
        byte_range = request.headers.get("Range") if path and status == 200 else None
        if byte_range:
            prefix, _, offset = byte_range.partition("=")
            digits = offset[:-1] if offset.endswith("-") else ""
            if (prefix != "bytes" or len(digits) > 20
                    or not digits.isascii() or not digits.isdecimal()):
                return self.reply(request, 416)
            start = int(digits)
            if start >= size:
                request.send_response(416)
                request.send_header("Content-Range", f"bytes */{size}")
                request.send_header("Content-Length", "0")
                request.end_headers()
                return
            status = 206
        request.send_response(status)
        request.send_header("Content-Type", "application/octet-stream")
        request.send_header("Content-Length", str(size - start))
        if path:
            request.send_header("Accept-Ranges", "bytes")
        if status == 206:
            request.send_header("Content-Range", f"bytes {start}-{size - 1}/{size}")
        request.end_headers()
        if path is None:
            request.wfile.write(data)
            if token:
                self.on_transfer(token, what, len(data))
            return
        with open(path, "rb") as stream:
            stream.seek(start)
            left = size - start
            while left and (chunk := stream.read(min(left, 1 << 16))):
                request.wfile.write(chunk)
                left -= len(chunk)
                if token:
                    self.on_transfer(token, what, len(chunk))
                self.pace(request.client_address[0], len(chunk))

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
