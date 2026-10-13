"""Publish completed builds from a standalone content library for TES3X Dash."""

import argparse
import contextlib
import datetime
import hashlib
import json
import os
import re
import secrets
import shutil
import ssl
import threading
import time
import urllib.parse
from pathlib import Path, PurePosixPath

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from cryptography.x509.oid import NameOID

from tes3x import manifest, netbuild
from tes3x.net.server import SourceBuckets

PORT = 26510
MAX_ENTRIES = 64
MAX_CATALOG = 64 * 1024
MAX_ACTIVITY = 128
MAX_BINDINGS = 4096
MAX_SOURCES = 16
MAX_CLIENTS = 1024
MAX_PENDING = 32
MAX_DELEGATES = 64
MAX_DELEGATED = 64
DELEGATION_SECONDS = 24 * 3600
ACTIVITY_IDLE = 90
ACCESS_MODES = ("private", "public")
# (burst, per second) for each source address
CATALOG_RATE = (10, 1.0)
TICKET_RATE = (6, 0.2)
REPORT_RATE = (30, 2.0)
STAGES = {"planning", "retail", "downloading", "verifying", "rebuilding",
          "copying", "retrying", "complete", "failed", "cancelled"}
ENTRY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,41}\Z")


def hex64(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)


def fingerprint(public):
    """The 32-digit fingerprint the Dash shows for a key."""
    return hashlib.blake2b(public, digest_size=16).hexdigest()


def write_json(path, data):
    staged = path.with_name(path.name + ".new")
    staged.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    staged.replace(path)


def identity(state):
    key_path = state / "content.key"
    if key_path.exists():
        return serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    key = ed25519.Ed25519PrivateKey.generate()
    data = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())
    with key_path.open("xb") as out:
        out.write(data)
    if os.name != "nt":
        key_path.chmod(0o600)
    return key


def tls_identity(state):
    """The content port's TLS context and its certificate's SHA-256. The Dash does not check the
    certificate as such: signed catalogs and tickets name this digest, binding it to the content
    key, and the Dash refuses a connection presenting any other certificate."""
    path = Path(state) / "content-tls.pem"
    if not path.exists():
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "TES3X content")])
        utc = datetime.timezone.utc
        certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                       .public_key(key.public_key())
                       .serial_number(x509.random_serial_number())
                       .not_valid_before(datetime.datetime(2020, 1, 1, tzinfo=utc))
                       .not_valid_after(datetime.datetime(2099, 12, 31, tzinfo=utc))
                       .sign(key, hashes.SHA256()))
        data = (key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                  serialization.NoEncryption())
                + certificate.public_bytes(serialization.Encoding.PEM))
        with path.open("xb") as out:
            out.write(data)
        if os.name != "nt":
            path.chmod(0o600)
    certificate = x509.load_pem_x509_certificate(path.read_bytes())
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.options |= ssl.OP_CIPHER_SERVER_PREFERENCE
    # ChaCha20 first: the Xbox's CPU has no AES instructions
    context.set_ciphers("ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-ECDSA-AES128-GCM-SHA256")
    context.load_cert_chain(path)
    digest = hashlib.sha256(certificate.public_bytes(serialization.Encoding.DER)).hexdigest()
    return context, digest


def public_key(key):
    return key.public_key().public_bytes(serialization.Encoding.Raw,
                                         serialization.PublicFormat.Raw)


def read_access(state):
    path = Path(state) / "access.json"
    if not path.exists():
        return {"format": 1, "clients": {}, "delegates": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(data, dict) or data.get("format") != 1
            or not isinstance(data.get("clients"), dict)
            or not isinstance(data.get("delegates"), dict)):
        raise ValueError("invalid content access file")
    for key, row in data["clients"].items():
        if not hex64(key) or not isinstance(row, dict) or not isinstance(row.get("label"), str):
            raise ValueError("invalid approved Dash in the content access file")
    for key, row in data["delegates"].items():
        if (not hex64(key) or not isinstance(row, dict)
                or not isinstance(row.get("label"), str)
                or not isinstance(row.get("revisions"), list)
                or not all(isinstance(pair, list) and len(pair) == 2
                           and isinstance(pair[0], str) and ENTRY.fullmatch(pair[0])
                           and hex64(pair[1]) for pair in row["revisions"])):
            raise ValueError("invalid world delegation in the content access file")
    return data


def read_pending(state):
    path = Path(state) / "pending.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {key: row for key, row in data.items()
            if hex64(key) and isinstance(row, dict)} if isinstance(data, dict) else {}


def resolve_client(state, which):
    """A full Dash key from its hex form or a fingerprint prefix of at least 8 digits, looked up
    among pending requests and approved Dashes."""
    which = which.strip().lower()
    known = set(read_pending(state)) | set(read_access(state)["clients"])
    if hex64(which):
        return which
    if not re.fullmatch(r"[0-9a-f]{8,32}", which):
        raise ValueError("give a Dash's key or at least 8 digits of its fingerprint")
    found = [key for key in known if fingerprint(bytes.fromhex(key)).startswith(which)]
    if len(found) != 1:
        raise ValueError(f"{len(found)} Dashes match fingerprint {which}")
    return found[0]


def approve(state, which, label=""):
    key = resolve_client(state, which)
    data = read_access(state)
    if key not in data["clients"] and len(data["clients"]) >= MAX_CLIENTS:
        raise ValueError(f"the library already admits {MAX_CLIENTS} Dashes")
    pending = read_pending(state).get(key, {})
    data["clients"][key] = {"label": label[:47] or str(pending.get("address", ""))[:47],
                            "added": time.strftime("%Y-%m-%d")}
    write_json(Path(state) / "access.json", data)
    return key


def revoke(state, which):
    key = resolve_client(state, which)
    data = read_access(state)
    if data["clients"].pop(key, None) is None:
        raise ValueError("that Dash is not approved")
    write_json(Path(state) / "access.json", data)
    return key


def delegate(state, key, entry, revision, label=""):
    """Let a multiplayer world's delegation key authorize tickets for one exact revision."""
    if not hex64(key) or not ENTRY.fullmatch(entry) or not hex64(revision):
        raise ValueError("a delegation needs a 64-digit key, an entry ID and a revision")
    data = read_access(state)
    row = data["delegates"].get(key)
    if row is None:
        if len(data["delegates"]) >= MAX_DELEGATES:
            raise ValueError(f"the library already trusts {MAX_DELEGATES} worlds")
        row = data["delegates"][key] = {"label": "", "revisions": []}
    if label:
        row["label"] = label[:47]
    if [entry, revision] not in row["revisions"]:
        if len(row["revisions"]) >= MAX_DELEGATED:
            raise ValueError(f"a world may hold at most {MAX_DELEGATED} revisions")
        row["revisions"].append([entry, revision])
    write_json(Path(state) / "access.json", data)


def undelegate(state, key, entry=None, revision=None):
    data = read_access(state)
    row = data["delegates"].get(key)
    if row is None:
        raise ValueError("that world key is not trusted")
    if entry is None:
        del data["delegates"][key]
    else:
        row["revisions"] = [pair for pair in row["revisions"]
                            if pair != [entry, revision] and (revision or pair[0] != entry)]
    write_json(Path(state) / "access.json", data)


def delegation_message(client, service, entry, build, revision, expires):
    return f"delegate:{client}:{service}:{entry}:{build}:{revision}:{expires}".encode()


AUTHORIZATION_SECONDS = 3600


def delegation_identity(path):
    """A world's Ed25519 delegation key, kept beside its other state."""
    path = Path(path)
    if path.exists():
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
        if not isinstance(key, ed25519.Ed25519PrivateKey):
            raise ValueError(f"{path} is not an Ed25519 key")
        return key
    key = ed25519.Ed25519PrivateKey.generate()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as out:
        out.write(key.private_bytes(serialization.Encoding.PEM,
                                    serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption()))
    if os.name != "nt":
        path.chmod(0o600)
    return key


class WorldSource:
    """A multiplayer world's required revision and the library that serves it. The BUILD reply to
    a Dash carries it as a CSRC block, with an authorization for that Dash's content identity:

        "CSRC", u16 length of the rest, content key (32), port (u16), host and entry (each
        NUL-terminated; an empty host is the world's own), build (32), revision (32), world
        delegation key (32), expiry (u64 Unix seconds), signature (64; zero without a Dash key)
    """

    def __init__(self, host, port, service_key, entry, build, revision, signer):
        if (len(host) > 63 or not re.fullmatch(r"[A-Za-z0-9.-]*", host)
                or not 0 < port < 65536 or not hex64(service_key) or not ENTRY.fullmatch(entry)
                or not hex64(build) or not hex64(revision)):
            raise ValueError("invalid content source")
        self.host, self.port, self.service_key = host, port, service_key
        self.entry, self.build, self.revision, self.signer = entry, build, revision, signer

    @property
    def delegate(self):
        return public_key(self.signer).hex()

    def block(self, dash_key):
        expires = int(time.time()) + AUTHORIZATION_SECONDS if any(dash_key) else 0
        signature = self.signer.sign(delegation_message(
            dash_key.hex(), self.service_key, self.entry, self.build, self.revision,
            expires)) if expires else bytes(64)
        body = (bytes.fromhex(self.service_key) + self.port.to_bytes(2, "little")
                + self.host.encode() + b"\0" + self.entry.encode() + b"\0"
                + bytes.fromhex(self.build) + bytes.fromhex(self.revision)
                + public_key(self.signer) + expires.to_bytes(8, "little") + signature)
        return b"CSRC" + len(body).to_bytes(2, "little") + body

    @classmethod
    def configure(cls, signer, source=None, key=None, entry=None, build=None, revision=None,
                  state=None, label=""):
        """From explicit values, filling what is missing from a local library's state; a local
        library also starts trusting this world for the revision."""
        host, _, port = (source or ":").rpartition(":")
        port = int(port or PORT)
        if state is not None:
            state = Path(state)
            with (state / "content.key").open("rb") as stream:
                local = serialization.load_pem_private_key(stream.read(), password=None)
            key = key or public_key(local).hex()
            rows = read_publications(state).get(entry, [])
            row = next((row for row in rows if row["revision"] == revision), None) if revision \
                else next((row for row in reversed(rows) if row["listed"]), None)
            if row is None:
                raise ValueError(f"the library has no revision {revision or '(latest)'} of "
                                 f"{entry!r}")
            build, revision = build or row["build"], row["revision"]
        if not (key and entry and build and revision):
            raise ValueError("a content source needs its key, entry, build and revision")
        world = cls(host, port, key, entry, build, revision, signer)
        if state is not None:
            delegate(state, world.delegate, entry, revision, label)
        return world


class Access:
    """The library's admission policy, reread when access.json changes so the GUI and CLI can
    approve Dashes while the server runs. Requests from unknown Dashes are kept in pending.json."""

    def __init__(self, state, mode):
        if mode not in ACCESS_MODES:
            raise ValueError(f"unknown access mode {mode!r}")
        self.state, self.mode = Path(state), mode
        self.stamp = None
        self.data = {"format": 1, "clients": {}, "delegates": {}}
        self.pending = read_pending(state)
        self.lock = threading.Lock()

    def current(self):
        path = self.state / "access.json"
        try:
            stamp = path.stat().st_mtime_ns
        except OSError:
            stamp = None
        with self.lock:
            if stamp != self.stamp:
                self.data = read_access(self.state)
                self.stamp = stamp
            return self.data

    def admitted(self, key):
        return self.mode == "public" or key.hex() in self.current()["clients"]

    def note(self, key, address):
        """Record a refused Dash so the admin can approve it; True when it is new."""
        clients = self.current()["clients"]
        with self.lock:
            fresh = key.hex() not in self.pending
            self.pending[key.hex()] = {"address": address, "seen": time.strftime("%Y-%m-%d %H:%M")}
            for old in [k for k in self.pending if k in clients]:
                del self.pending[old]
            while len(self.pending) > MAX_PENDING:
                self.pending.pop(next(iter(self.pending)))
            write_json(self.state / "pending.json", self.pending)
        return fresh

    def delegated(self, client, service, entry, build, revision, world, expires, signature):
        row = self.current()["delegates"].get(world)
        now = time.time()
        if (row is None or [entry, revision] not in row["revisions"]
                or not now < expires <= now + DELEGATION_SECONDS + 300):
            return False
        try:
            ed25519.Ed25519PublicKey.from_public_bytes(bytes.fromhex(world)).verify(
                bytes.fromhex(signature),
                delegation_message(client, service, entry, build, revision, expires))
        except (InvalidSignature, ValueError):
            return False
        return True


def signed(key, body):
    """The Dash verifies public key (32), Ed25519 signature (64), then JSON bytes."""
    return public_key(key) + key.sign(body) + body


def safe_relative(name):
    path = PurePosixPath(name)
    return (name and "\\" not in name and ":" not in name and not path.is_absolute()
            and all(part not in ("", ".", "..") and len(part) <= 42
                    for part in name.split("/")))


def revision_sources(value):
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_SOURCES:
        raise ValueError("invalid revision sources")
    for row in value:
        if (not isinstance(row, dict) or row.get("kind") not in ("profile", "pack")
                or not isinstance(row.get("name"), str)
                or not 1 <= len(row["name"]) <= 191
                or not isinstance(row.get("version"), str)
                or not 1 <= len(row["version"]) <= 64):
            raise ValueError("invalid revision source")
    return [{key: row[key] for key in ("kind", "name", "version")} for row in value]


def read_publications(state):
    """publications.json: entry ID -> its revisions, oldest first."""
    path = Path(state) / "publications.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(data, dict) or data.get("format") != 1
            or not isinstance(data.get("entries"), dict)):
        raise ValueError("invalid content publication index")
    history = data["entries"]
    for entry_id, rows in history.items():
        if not isinstance(entry_id, str) or not ENTRY.fullmatch(entry_id) \
                or not isinstance(rows, list):
            raise ValueError("invalid content publication entry")
        for row in rows:
            if (not isinstance(row, dict) or row.get("id") != entry_id
                    or not hex64(row.get("build"))
                    or not hex64(row.get("revision"))
                    or not hex64(row.get("sha256"))
                    or not isinstance(row.get("listed"), bool)
                    or not isinstance(row.get("name"), str)
                    or not 1 <= len(row["name"]) <= 47
                    or not isinstance(row.get("description"), str)
                    or len(row["description"]) > 191
                    or not isinstance(row.get("layout"), str)
                    or len(row["layout"]) > 15
                    or not isinstance(row.get("bytes"), int)
                    or row["bytes"] < 0
                    or not isinstance(row.get("size"), int)
                    or not 0 < row["size"] <= 16 << 20):
                raise ValueError("invalid content publication revision")
            row["sources"] = revision_sources(row.get("sources"))
    return history


class Library:
    """Copy each new revision into private storage before advertising it."""

    def __init__(self, state, build_root, folders, all_builds, origins, retail_base=None):
        self.state = Path(state)
        self.state.mkdir(parents=True, exist_ok=True)
        self.key = identity(self.state)
        self.tls_context, self.tls = tls_identity(self.state)
        self.build_root = Path(build_root)
        self.folders = [Path(folder) for folder in folders]
        self.all_builds = all_builds
        self.origins = origins
        self.retail_base = retail_base
        self.entries = {}
        self.index_stamp = None
        self.history = self.load_index()
        self.revisions = {}
        self.lock = threading.RLock()
        self.refresh()

    def index_digest(self):
        # by content: two quick writes can share a modification time
        try:
            return hashlib.sha256((self.state / "publications.json").read_bytes()).digest()
        except OSError:
            return None

    def load_index(self):
        self.index_stamp = self.index_digest()
        return read_publications(self.state) if self.index_stamp else {}

    def save_index(self):
        write_json(self.state / "publications.json", {"format": 1, "entries": self.history})
        self.index_stamp = self.index_digest()

    def sync_index(self):
        """Reread the index if another process (the GUI, the CLI) changed it."""
        if self.index_digest() != self.index_stamp:
            self.history = self.load_index()
            for entry_id in list(self.entries):
                current = next((row for row in self.history.get(entry_id, ())
                                if row["revision"] == self.entries[entry_id]["revision"]), None)
                if current is None:
                    del self.entries[entry_id]
                else:
                    self.entries[entry_id] = current

    @contextlib.contextmanager
    def editing(self):
        """Hold the index for a read-modify-write shared with other processes."""
        lock = self.state / "publications.lock"
        deadline = time.monotonic() + 10
        while True:
            try:
                os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
                break
            except FileExistsError:
                try:
                    if time.time() - lock.stat().st_mtime > 30:
                        lock.unlink(missing_ok=True)
                        continue
                except OSError:
                    continue
                if time.monotonic() > deadline:
                    raise ValueError("the content publication index is busy") from None
                time.sleep(0.05)
        try:
            with self.lock:
                self.sync_index()
                yield
                self.save_index()
        finally:
            lock.unlink(missing_ok=True)

    def prune(self, keep=()):
        """Delete withdrawn snapshots no world delegation names, with hosting stopped: tickets
        issued earlier in a run may still read them. Returns the revisions removed."""
        delegated = {revision for row in read_access(self.state)["delegates"].values()
                     for _, revision in row["revisions"]}
        removed = []
        with self.editing():
            live = {row["revision"] for rows in self.history.values() for row in rows
                    if row["listed"]} | delegated | set(keep)
            for entry_id, rows in list(self.history.items()):
                kept = [row for row in rows if row["listed"] or row["revision"] in live]
                for row in rows:
                    if row not in kept:
                        removed.append(row["revision"])
                if kept:
                    self.history[entry_id] = kept
                else:
                    del self.history[entry_id]
            for revision in removed:
                shutil.rmtree(self.state / "revisions" / revision, ignore_errors=True)
            listed = set()
            for kept in (self.state / "revisions").glob("*/deploy"):
                listed |= {entry["sha256"]
                           for entry in (manifest.load(kept) or {}).get("store", [])}
            for archive in (self.state / "store").glob("*.bsa"):
                if archive.stem not in listed:
                    archive.unlink()
                    archive.with_suffix(".json").unlink(missing_ok=True)
        return removed

    def set_metadata(self, entry_id, name=None, description=None):
        if name is not None and (not name.strip() or len(name) > 47):
            raise ValueError("content name must have 1-47 characters")
        if description is not None and len(description) > 191:
            raise ValueError("content description must have at most 191 characters")
        with self.editing():
            rows = self.history.get(entry_id)
            if not rows:
                raise ValueError(f"unknown content entry {entry_id!r}")
            for row in rows:
                if name is not None:
                    row["name"] = name.strip()
                if description is not None:
                    row["description"] = description

    def set_listed(self, entry_id, revision, listed):
        with self.editing():
            row = next((row for row in self.history.get(entry_id, [])
                        if row["revision"] == revision), None)
            if row is None:
                raise ValueError(f"unknown published revision {entry_id}/{revision}")
            if listed and not row["listed"] and sum(item["listed"] for rows in self.history.values()
                              for item in rows) >= MAX_ENTRIES:
                raise ValueError("content catalog has 64 listed revisions; withdraw one")
            row["listed"] = listed

    def candidates(self):
        folders = list(self.folders)
        if self.all_builds:
            folders.extend(sorted(self.build_root.glob("*/deploy")))
        return dict((folder.parent.name if folder.name == "deploy" else folder.name, folder)
                    for folder in folders if folder.is_dir())

    def publish(self, entry_id, source):
        if not ENTRY.fullmatch(entry_id):
            raise ValueError(f"invalid content entry ID {entry_id!r}")
        data = (source / manifest.NAME).read_bytes()
        parsed = manifest.parse(data)
        build_id = parsed.get("build") or manifest.build_id(parsed)
        if (not re.fullmatch(r"[0-9a-f]{64}", build_id)
                or build_id != manifest.build_id(parsed)):
            raise ValueError(f"{source} has an invalid build ID")
        sources = revision_sources(parsed.get("sources"))
        manifest_digest = hashlib.sha256(data).hexdigest()
        revision = self.state / "revisions" / manifest_digest
        if not revision.is_dir():
            staged = revision.with_name(build_id + "." + secrets.token_hex(6) + ".new")
            staged.mkdir(parents=True)
            try:
                target = staged / "deploy"
                target.mkdir()
                for relative, info in parsed["files"].items():
                    if not safe_relative(relative):
                        raise ValueError(f"unsafe build path {relative!r}")
                    original = source / relative
                    if (not original.resolve().is_relative_to(source.resolve())
                            or original.is_symlink() or not original.is_file()):
                        raise ValueError(f"missing regular build file {original}")
                    destination = target / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(original, destination)
                    if (destination.stat().st_size != info["size"]
                            or manifest.sha256_file(destination) != info["sha256"]):
                        raise ValueError(f"build file changed while publishing: {original}")
                (target / manifest.NAME).write_bytes(data)
                deltas = staged / "deltas"
                deltas.mkdir()
                for recipe in parsed.get("xbe", []):
                    delta = recipe.get("delta")
                    if not delta:
                        continue
                    delta_digest = delta["sha256"]
                    if not re.fullmatch(r"[0-9a-f]{64}", delta_digest):
                        raise ValueError("invalid XBE delta digest")
                    original = source.parent / "deltas" / f"{delta_digest}.zst"
                    if not original.is_file() or original.is_symlink():
                        raise ValueError(f"missing XBE delta {original}")
                    destination = deltas / original.name
                    shutil.copyfile(original, destination)
                    if (destination.stat().st_size != delta["size"]
                            or manifest.sha256_file(destination) != delta_digest):
                        raise ValueError(f"XBE delta changed while publishing: {original}")
                self.copy_store(parsed, source)
                revision.parent.mkdir(parents=True, exist_ok=True)
                staged.rename(revision)
            finally:
                if staged.exists():
                    shutil.rmtree(staged)
        build = self.revisions.get(manifest_digest)
        if build is None:
            build = self.build(revision)
        name = str(parsed.get("profile") or entry_id)[:47]
        with self.lock:
            self.revisions[manifest_digest] = build
        with self.editing():
            rows = self.history.setdefault(entry_id, [])
            previous = next((row for row in rows if row["revision"] == manifest_digest), None)
            if previous is None:
                if sum(row["listed"] for eid, group in self.history.items()
                       if eid == entry_id or eid in self.entries for row in group) \
                        >= MAX_ENTRIES:
                    raise ValueError("content catalog has 64 listed revisions; withdraw one")
                previous = {"id": entry_id, "name": rows[-1]["name"] if rows else name,
                            "description": rows[-1]["description"] if rows else "",
                            "build": build_id, "revision": manifest_digest,
                            "layout": parsed.get("install_layout", "full"),
                            "sources": sources,
                            "bytes": sum(item["size"] for item in parsed["files"].values()),
                            "sha256": build.sha256.hex(), "size": len(build.data),
                            "listed": True}
                rows.append(previous)
            self.entries[entry_id] = previous

    def build(self, revision):
        return netbuild.Build(revision / "deploy", deltas=revision / "deltas",
                              origins=self.origins, retail_base=self.retail_base,
                              store=self.state / "store")

    def copy_store(self, parsed, source):
        """Store archives into the library's one shared store, kept while any revision lists
        them; the pipeline's store is beside the source's profile folder."""
        from tes3x import store
        entries = store.check_entries(parsed.get("store", []))
        shared = self.state / "store"
        for entry in entries:
            if store.read_entry(shared, entry["sha256"]):
                continue
            original = store.pc_store(source) / f"{entry['sha256']}.bsa"
            if not original.is_file() or original.is_symlink():
                raise ValueError(f"missing store archive {original}")
            shared.mkdir(parents=True, exist_ok=True)
            staged = shared / f"{entry['sha256']}.bsa.new"
            shutil.copyfile(original, staged)
            if (staged.stat().st_size != entry["size"]
                    or manifest.sha256_file(staged) != entry["sha256"]):
                staged.unlink()
                raise ValueError(f"store archive changed while publishing: {original}")
            staged.replace(shared / f"{entry['sha256']}.bsa")
            (shared / f"{entry['sha256']}.json").write_text(json.dumps(entry),
                                                           encoding="utf-8")

    def refresh(self):
        candidates = self.candidates()
        if len(candidates) > MAX_ENTRIES:
            raise ValueError(f"content library has more than {MAX_ENTRIES} builds")
        for entry_id, source in candidates.items():
            parsed = manifest.load(source)
            if parsed is None:
                continue
            digest = hashlib.sha256((source / manifest.NAME).read_bytes()).hexdigest()
            with self.lock:
                current = self.entries.get(entry_id, {}).get("revision")
            if current != digest:
                self.publish(entry_id, source)
        with self.lock:
            for entry_id in list(self.entries):
                if entry_id not in candidates:
                    del self.entries[entry_id]

    def catalog(self, access="public", client=None):
        """The signed catalog. Unless access is "public" or "approved" it lists nothing and says
        why; every form names the TLS certificate digest."""
        with self.lock:
            self.sync_index()
            rows = [{**{key: value for key, value in row.items() if key != "listed"},
                     "latest": int(row["revision"] == self.entries[entry_id]["revision"])}
                    for entry_id in self.entries for row in reversed(self.history[entry_id])
                    if row["listed"]] if access in ("public", "approved") else []
            data = {"format": 1, "access": access, "tls": self.tls, "entries": rows}
            if client is not None:
                data["client"] = fingerprint(client)
            body = json.dumps(data, separators=(",", ":")).encode()
        if len(body) > MAX_CATALOG:
            raise ValueError("content catalog exceeds the Dash limit")
        return signed(self.key, body)

    def select(self, entry_id, build_id, revision_id, delegated=False):
        """The publication row and its build. A delegated request may name a withdrawn
        revision or one whose source folder is no longer shared, while its snapshot remains."""
        with self.lock:
            self.sync_index()
            if entry_id not in self.entries and not delegated:
                return None
            row = next((row for row in self.history.get(entry_id, ())
                        if row["build"] == build_id and row["revision"] == revision_id
                        and (row["listed"] or delegated)), None)
            if row is None:
                return None
            build = self.revisions.get(row["revision"])
            if build is None:
                revision = self.state / "revisions" / row["revision"]
                if not revision.is_dir():
                    return None
                build = self.build(revision)
                self.revisions[row["revision"]] = build
            return row, build


class ContentServer(netbuild.BuildServer):
    """A signed catalog and revision tickets over TLS, admitted by the access policy."""

    def __init__(self, library, bind, port, log=print, access=None, client_rate=0):
        self.library = library
        self.access = access or Access(library.state, "public")
        self.activity = {}
        self.completed = {}
        self.bindings = {}
        self.client_rate = client_rate
        self.pacing = {}
        self.limits = {name: SourceBuckets(*rate) for name, rate in
                       (("catalog", CATALOG_RATE), ("ticket", TICKET_RATE),
                        ("report", REPORT_RATE))}
        super().__init__(None, bind, port, log=log, tls=library.tls_context)

    def limited(self, kind, address):
        now = time.monotonic()
        with self.lock:
            bucket = self.limits[kind].get(address, now)
            return bucket is None or not bucket.take(now)

    def pace(self, address, sent):
        if not self.client_rate:
            return
        now = time.monotonic()
        with self.lock:
            # the time this address's sending may run up to, shared by its connections
            due = max(self.pacing.get(address, now), now) + sent / self.client_rate
            self.pacing[address] = due
            if len(self.pacing) > 4096:
                self.pacing = {key: value for key, value in self.pacing.items() if value > now}
        if due - now > 0.05:
            time.sleep(due - now)

    def activity_start(self, token):
        with self.lock:
            found = self.tickets.get(token)
            if not found or found[0] < time.monotonic() or token in self.completed:
                return None
            attempt = found[6]
            if attempt not in self.activity:
                now = time.monotonic()
                self.activity[attempt] = {"attempt": attempt,
                                        "_binding": (found[5], found[2], found[8], found[7]),
                                        "_token": token,
                                        "client": ((fingerprint(found[5])[:12]
                                                    + " @ " + found[3]) if found[5] else found[3]),
                                        "entry": found[2],
                                        "build": found[4], "stage": "planning",
                                        "done": 0, "total": 0, "wire": 0,
                                        "file": "", "tickets": 1, "seen": now}
                while len(self.activity) > MAX_ACTIVITY:
                    self.activity.pop(next(iter(self.activity)))
            return self.activity[attempt]

    def activity_update(self, token, **fields):
        now = time.monotonic()
        with self.lock:
            ticket = self.tickets.get(token)
            row = self.activity.get(ticket[6]) if ticket else None
            if (row is None or row["_token"] != token
                    or row["stage"] in ("complete", "failed", "cancelled")):
                return
            if fields.get("stage") in ("complete", "failed", "cancelled") \
                    and not fields.get("total"):
                fields["done"], fields["total"] = row["done"], row["total"]
            row.update(fields)
            row["seen"] = now
            if row["stage"] in ("complete", "failed", "cancelled"):
                self.completed[token] = self.tickets.get(token, (now,))[0]
                while len(self.completed) > netbuild.TICKETS:
                    self.completed.pop(next(iter(self.completed)))
            visible = {key: value for key, value in row.items()
                       if key not in ("seen", "emitted", "_binding", "_token")}
        self.log("activity " + json.dumps(visible, separators=(",", ":")))

    def on_transfer(self, token, what, sent):
        now = time.monotonic()
        self.activity_start(token)
        with self.lock:
            ticket = self.tickets.get(token)
            row = self.activity.get(ticket[6]) if ticket else None
            if (row is None or row["_token"] != token
                    or row["stage"] in ("complete", "failed", "cancelled")):
                return
            row["wire"] += sent
            row["file"] = what[:80]
            row["seen"] = now
            if now - row.get("emitted", 0) < 1:
                return
            row["emitted"] = now
            visible = {key: value for key, value in row.items()
                       if key not in ("seen", "emitted", "_binding", "_token")}
        self.log("activity " + json.dumps(visible, separators=(",", ":")))

    def activity_snapshot(self):
        now = time.monotonic()
        expired = []
        with self.lock:
            for row in self.activity.values():
                if row["stage"] not in ("complete", "failed", "cancelled", "unknown") \
                        and now - row["seen"] > ACTIVITY_IDLE:
                    row["stage"] = "unknown"
                    expired.append({key: value for key, value in row.items()
                                    if key not in ("seen", "emitted", "_binding", "_token")})
            snapshot = [{key: value for key, value in row.items()
                         if key not in ("seen", "emitted", "_binding", "_token")}
                        for row in self.activity.values()]
        for row in expired:
            self.log("activity " + json.dumps(row, separators=(",", ":")))
        return snapshot

    def build_for(self, token):
        with self.lock:
            found = self.tickets.get(token)
        return found[1] if found and found[0] >= time.monotonic() else None

    def get(self, request):
        path = urllib.parse.urlsplit(request.path).path
        if path.startswith("/v1/report/"):
            if len(request.path) > 256:
                return self.reply(request, 400)
            if self.limited("report", request.client_address[0]):
                return self.reply(request, 429)
            token = path[len("/v1/report/"):]
            if not re.fullmatch(r"[0-9a-f]{32}", token) or self.build_for(token) is None:
                return self.reply(request, 403)
            with self.lock:
                ticket = self.tickets.get(token)
            if ticket is None or ticket[3] != request.client_address[0] or ticket[5] is None:
                return self.reply(request, 403)
            try:
                params = urllib.parse.parse_qs(urllib.parse.urlsplit(request.path).query,
                                               max_num_fields=4)
                stage = params["stage"][0]
                done = int(params.get("done", ["0"])[0])
                total = int(params.get("total", ["0"])[0])
                signature = params["sig"][0]
            except (KeyError, ValueError, IndexError):
                return self.reply(request, 400)
            if (stage not in STAGES or not 0 <= done <= 1 << 45
                    or not 0 <= total <= 1 << 45 or done > total and total
                    or not re.fullmatch(r"[0-9a-f]{128}", signature)):
                return self.reply(request, 400)
            message = f"{token}:{stage}:{done}:{total}".encode()
            try:
                ed25519.Ed25519PublicKey.from_public_bytes(ticket[5]).verify(
                    bytes.fromhex(signature), message)
            except InvalidSignature:
                return self.reply(request, 403)
            with self.lock:
                already_complete = token in self.completed
            if already_complete:
                return self.reply(request, 200)
            self.activity_start(token)
            self.activity_update(token, stage=stage, done=done, total=total)
            return self.reply(request, 200)
        if path == "/v1/catalog":
            if len(request.path) > 400:
                return self.reply(request, 400)
            if self.limited("catalog", request.client_address[0]):
                return self.reply(request, 429)
            try:
                params = urllib.parse.parse_qs(urllib.parse.urlsplit(request.path).query,
                                               max_num_fields=2)
                client = params.get("client", [""])[0]
                signature = params.get("sig", [""])[0]
            except ValueError:
                return self.reply(request, 400)
            client_key = None
            if (re.fullmatch(r"[0-9a-f]{64}", client)
                    and re.fullmatch(r"[0-9a-f]{128}", signature)):
                try:
                    ed25519.Ed25519PublicKey.from_public_bytes(bytes.fromhex(client)).verify(
                        bytes.fromhex(signature), f"catalog:{self.library.tls}".encode())
                    client_key = bytes.fromhex(client)
                except (InvalidSignature, ValueError):
                    return self.reply(request, 403)
            try:
                if self.access.mode == "public":
                    state = "public"
                elif client_key is None:
                    state = "private"
                elif self.access.admitted(client_key):
                    state = "approved"
                else:
                    state = "pending"
                    if self.access.note(client_key, request.client_address[0]):
                        self.log("access pending " + json.dumps(
                            {"client": fingerprint(client_key),
                             "address": request.client_address[0]}, separators=(",", ":")))
                return self.reply(request, 200, self.library.catalog(state, client_key))
            except (OSError, ValueError) as error:
                self.log(f"catalog unavailable: {error}")
                return self.reply(request, 503)
        if path.startswith("/v1/ticket/"):
            entry_id = path[len("/v1/ticket/"):]
            if len(request.path) > 1024 or not ENTRY.fullmatch(entry_id):
                return self.reply(request, 400)
            if self.limited("ticket", request.client_address[0]):
                return self.reply(request, 429)
            try:
                params = urllib.parse.parse_qs(urllib.parse.urlsplit(request.path).query,
                                               max_num_fields=9)
                client, requested_build, revision, attempt, latest, signature = (
                    params[name][0] for name in ("client", "build", "revision", "attempt",
                                                "latest", "sig"))
                world = params.get("world", [""])[0]
                expires = params.get("expires", ["0"])[0]
                world_signature = params.get("wsig", [""])[0]
            except (KeyError, IndexError, ValueError):
                return self.reply(request, 400)
            if (not re.fullmatch(r"[0-9a-f]{64}", client)
                    or not re.fullmatch(r"[0-9a-f]{64}", requested_build)
                    or not re.fullmatch(r"[0-9a-f]{64}", revision)
                    or not re.fullmatch(r"[0-9a-f]{16}", attempt)
                    or latest not in ("0", "1")
                    or not re.fullmatch(r"[0-9a-f]{128}", signature)
                    or world and (not re.fullmatch(r"[0-9a-f]{64}", world)
                                  or not re.fullmatch(r"[0-9]{1,12}", expires)
                                  or not re.fullmatch(r"[0-9a-f]{128}", world_signature))):
                return self.reply(request, 400)
            client_key = bytes.fromhex(client)
            # the certificate digest binds the request to this connection's server
            message = (f"ticket:{entry_id}:{requested_build}:{revision}:{attempt}:{latest}:"
                       f"{self.library.tls}").encode()
            try:
                ed25519.Ed25519PublicKey.from_public_bytes(client_key).verify(
                    bytes.fromhex(signature), message)
            except (InvalidSignature, ValueError):
                return self.reply(request, 403)
            delegated = bool(world) and self.access.delegated(
                client, public_key(self.library.key).hex(), entry_id, requested_build,
                revision, world, int(expires), world_signature)
            if world and not delegated:
                self.log(f"refused a world authorization for {entry_id}/{revision[:12]}")
                return self.reply(request, 403)
            if not delegated and not self.access.admitted(client_key):
                return self.reply(request, 403)
            with self.library.lock:
                entry = self.library.entries.get(entry_id)
            if entry is None and not delegated:
                return self.reply(request, 404)
            try:
                selected = self.library.select(entry_id, requested_build, revision, delegated)
            except (OSError, ValueError, KeyError) as error:
                self.log(f"content revision unavailable: {error}")
                return self.reply(request, 503)
            if delegated and selected is None:
                return self.reply(request, 404)
            if selected is None or (latest == "1" and not delegated
                                    and entry["revision"] != revision):
                body = json.dumps({"format": 1, "tls": self.library.tls,
                                   "build": entry["build"], "revision": entry["revision"]},
                                  separators=(",", ":")).encode()
                return self.reply(request, 200, signed(self.library.key, body))
            row_selected, build = selected
            token = secrets.token_bytes(16)
            renewed = None
            with self.lock:
                now = time.monotonic()
                binding = (client_key, entry_id, requested_build, revision)
                row = self.activity.get(attempt)
                if ((attempt in self.bindings and self.bindings[attempt] != binding)
                        or (row is not None and row["_binding"] != binding)):
                    return self.reply(request, 403)
                self.bindings[attempt] = binding
                while len(self.bindings) > MAX_BINDINGS:
                    self.bindings.pop(next(iter(self.bindings)))
                self.tickets = {key: value for key, value in self.tickets.items()
                                if value[0] >= now}
                if len(self.tickets) >= netbuild.TICKETS:
                    self.tickets.pop(next(iter(self.tickets)))
                self.tickets[token.hex()] = (now + netbuild.TICKET_SECONDS, build, entry_id,
                                             request.client_address[0], requested_build[:12],
                                             client_key, attempt, revision, requested_build)
                if row is not None:
                    row.update(stage="retrying", done=0, total=0, file="", seen=now,
                               _token=token.hex(),
                               client=(fingerprint(client_key)[:12] + " @ "
                                       + request.client_address[0]),
                               tickets=row["tickets"] + 1)
                    renewed = {key: value for key, value in row.items()
                               if key not in ("seen", "emitted", "_binding", "_token")}
            body = json.dumps({"format": 1, "tls": self.library.tls, "build": requested_build,
                               "revision": revision,
                               "sha256": build.sha256.hex(), "size": len(build.data),
                               "token": token.hex(), "attempt": attempt},
                              separators=(",", ":")).encode()
            if renewed is not None:
                self.log("activity " + json.dumps(renewed, separators=(",", ":")))
            self.log(f"content ticket {entry_id}: {row_selected['build'][:12]}")
            return self.reply(request, 200, signed(self.library.key, body))
        return super().get(request)


def serving_lock(state):
    """An exclusive lock held while a server hosts this state; None if another holds it."""
    Path(state).mkdir(parents=True, exist_ok=True)
    stream = open(Path(state) / "serving.lock", "a+b")
    try:
        if os.name == "nt":
            import msvcrt
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        stream.close()
        return None
    return stream


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def command(name, help):
        child = sub.add_parser(name, help=help)
        child.add_argument("--state", type=Path, default=Path("content-state"))
        return child

    serve = command("serve", "host a signed catalog and completed builds")
    serve.add_argument("--build-root", type=Path, default=Path("build"))
    serve.add_argument("--all-builds", action="store_true",
                       help="publish every completed build under BUILD-ROOT automatically")
    serve.add_argument("--build", type=Path, action="append", default=[], metavar="DEPLOY",
                       help="publish this completed deploy folder (repeatable)")
    serve.add_argument("--bind", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=PORT)
    serve.add_argument("--retail-base", type=Path)
    serve.add_argument("--serve-origin", action="append", choices=netbuild.ORIGINS)
    serve.add_argument("--access", choices=ACCESS_MODES, default="private",
                       help="private: only approved Dashes and world authorizations "
                            "(default); public: any Dash")
    serve.add_argument("--client-rate", type=float, default=0, metavar="MBPS",
                       help="cap each address's download rate in megabits per second")
    command("list", "list stored content publications")
    command("key", "print the existing content service's public key")
    edit = command("set", "edit a published entry's display metadata")
    edit.add_argument("entry")
    edit.add_argument("--name")
    edit.add_argument("--description")
    for name in ("withdraw", "restore"):
        change = command(name, f"{name} one published revision")
        change.add_argument("entry")
        change.add_argument("revision")
    command("prune", "delete withdrawn snapshots no world uses (hosting stopped)")
    command("access", "list approved and waiting Dashes and trusted worlds")
    allow = command("approve", "admit a Dash by its key or fingerprint")
    allow.add_argument("client")
    allow.add_argument("--label", default="")
    deny = command("revoke", "remove an approved Dash")
    deny.add_argument("client")
    trust = command("delegate", "let a world's delegation key authorize one revision")
    trust.add_argument("world", help="the world's delegation public key (64 hex digits)")
    trust.add_argument("entry")
    trust.add_argument("revision")
    trust.add_argument("--label", default="")
    distrust = command("undelegate", "stop trusting a world, or one of its revisions")
    distrust.add_argument("world")
    distrust.add_argument("entry", nargs="?")
    distrust.add_argument("revision", nargs="?")
    args = parser.parse_args(argv)
    if args.command == "key":
        try:
            path = args.state / "content.key"
            with path.open("rb") as stream:
                key = serialization.load_pem_private_key(stream.read(), password=None)
            if not isinstance(key, ed25519.Ed25519PrivateKey):
                raise ValueError("content identity is not an Ed25519 key")
        except (OSError, ValueError) as error:
            parser.error(str(error))
        public = public_key(key)
        print(f"public key {public.hex()}")
        print(f"fingerprint {fingerprint(public)}")
        return
    if args.command in ("access", "approve", "revoke", "delegate", "undelegate"):
        try:
            if args.command == "approve":
                print(f"approved {fingerprint(bytes.fromhex(approve(args.state, args.client, args.label)))}")
            elif args.command == "revoke":
                print(f"revoked {fingerprint(bytes.fromhex(revoke(args.state, args.client)))}")
            elif args.command == "delegate":
                delegate(args.state, args.world, args.entry, args.revision, args.label)
            elif args.command == "undelegate":
                undelegate(args.state, args.world, args.entry, args.revision)
            else:
                data = read_access(args.state)
                for key, row in data["clients"].items():
                    print(f"approved {fingerprint(bytes.fromhex(key))} {row['label']}")
                for key, row in read_pending(args.state).items():
                    if key not in data["clients"]:
                        print(f"waiting {fingerprint(bytes.fromhex(key))} "
                              f"{row.get('address', '')} {row.get('seen', '')}")
                for key, row in data["delegates"].items():
                    for entry, revision in row["revisions"]:
                        print(f"world {key} {entry} {revision} {row['label']}")
        except (OSError, ValueError) as error:
            parser.error(str(error))
        return
    if args.command != "serve":
        try:
            if args.command == "prune":
                held = serving_lock(args.state)
                if held is None:
                    parser.error("stop the content server before pruning")
            library = Library(args.state, args.state, [], False, netbuild.SERVED_BY_DEFAULT)
            if args.command == "list":
                for entry_id, rows in library.history.items():
                    for row in reversed(rows):
                        print(f"{entry_id} {row['revision']} {row['build'][:12]} "
                              f"{'listed' if row['listed'] else 'withdrawn'} {row['name']}")
            elif args.command == "set":
                if args.name is None and args.description is None:
                    parser.error("set requires --name or --description")
                library.set_metadata(args.entry, args.name, args.description)
            elif args.command == "prune":
                removed = library.prune()
                held.close()
                print(f"removed {len(removed)} withdrawn revision(s)")
                for revision in removed:
                    print(f"  {revision}")
            else:
                library.set_listed(args.entry, args.revision, args.command == "restore")
        except (OSError, ValueError) as error:
            parser.error(str(error))
        return
    if not args.all_builds and not args.build:
        parser.error("choose --all-builds or at least one --build")
    held = serving_lock(args.state)
    if held is None:
        parser.error(f"another content server is using {args.state}")
    library = Library(args.state, args.build_root, args.build, args.all_builds,
                      args.serve_origin or netbuild.SERVED_BY_DEFAULT, args.retail_base)
    server = ContentServer(library, args.bind, args.port, access=Access(args.state, args.access),
                           client_rate=args.client_rate * 125000)
    print(f"content server on {args.bind}:{server.port}; {len(library.entries)} build(s); "
          f"{args.access} access; key {fingerprint(public_key(library.key))}", flush=True)
    stop = threading.Event()

    def watch():
        while not stop.wait(10):
            try:
                library.refresh()
            except (OSError, ValueError, KeyError) as error:
                print(f"content library: {error}", flush=True)
            server.activity_snapshot()

    threading.Thread(target=watch, daemon=True).start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.close()
        held.close()


if __name__ == "__main__":
    main()
