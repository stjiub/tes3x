"""Publish completed builds from a standalone content library for TES3X Dash."""

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import threading
import time
import urllib.parse
from pathlib import Path, PurePosixPath

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from tes3x import manifest, netbuild

PORT = 26510
MAX_ENTRIES = 64
MAX_CATALOG = 64 * 1024
MAX_ACTIVITY = 128
MAX_BINDINGS = 4096
MAX_SOURCES = 16
ACTIVITY_IDLE = 90
STAGES = {"planning", "retail", "downloading", "verifying", "rebuilding",
          "copying", "retrying", "complete", "failed", "cancelled"}
ENTRY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,41}\Z")


def hex64(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)


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


def public_key(key):
    return key.public_key().public_bytes(serialization.Encoding.Raw,
                                         serialization.PublicFormat.Raw)


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


class Library:
    """Copy each new revision into private storage before advertising it."""

    def __init__(self, state, build_root, folders, all_builds, origins, retail_base=None):
        self.state = Path(state)
        self.state.mkdir(parents=True, exist_ok=True)
        self.key = identity(self.state)
        self.build_root = Path(build_root)
        self.folders = [Path(folder) for folder in folders]
        self.all_builds = all_builds
        self.origins = origins
        self.retail_base = retail_base
        self.entries = {}
        self.history = self.load_index()
        self.revisions = {}
        self.lock = threading.RLock()
        self.refresh()

    def load_index(self):
        path = self.state / "publications.json"
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

    def save_index(self):
        path = self.state / "publications.json"
        staged = path.with_suffix(".json.new")
        staged.write_text(json.dumps({"format": 1, "entries": self.history},
                                     separators=(",", ":")), encoding="utf-8")
        staged.replace(path)

    def set_metadata(self, entry_id, name=None, description=None):
        if name is not None and (not name.strip() or len(name) > 47):
            raise ValueError("content name must have 1-47 characters")
        if description is not None and len(description) > 191:
            raise ValueError("content description must have at most 191 characters")
        with self.lock:
            rows = self.history.get(entry_id)
            if not rows:
                raise ValueError(f"unknown content entry {entry_id!r}")
            for row in rows:
                if name is not None:
                    row["name"] = name.strip()
                if description is not None:
                    row["description"] = description
            self.save_index()

    def set_listed(self, entry_id, revision, listed):
        with self.lock:
            row = next((row for row in self.history.get(entry_id, [])
                        if row["revision"] == revision), None)
            if row is None:
                raise ValueError(f"unknown published revision {entry_id}/{revision}")
            if listed and not row["listed"] and sum(item["listed"] for rows in self.history.values()
                              for item in rows) >= MAX_ENTRIES:
                raise ValueError("content catalog has 64 listed revisions; withdraw one")
            row["listed"] = listed
            self.save_index()

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
                revision.parent.mkdir(parents=True, exist_ok=True)
                staged.rename(revision)
            finally:
                if staged.exists():
                    shutil.rmtree(staged)
        build = self.revisions.get(manifest_digest)
        if build is None:
            build = netbuild.Build(revision / "deploy", deltas=revision / "deltas",
                                   origins=self.origins, retail_base=self.retail_base)
        name = str(parsed.get("profile") or entry_id)[:47]
        with self.lock:
            self.revisions[manifest_digest] = build
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
                self.save_index()
            self.entries[entry_id] = previous

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

    def catalog(self):
        with self.lock:
            rows = [{**{key: value for key, value in row.items() if key != "listed"},
                     "latest": int(row["revision"] == self.entries[entry_id]["revision"])}
                    for entry_id in self.entries for row in reversed(self.history[entry_id])
                    if row["listed"]]
            body = json.dumps({"format": 1, "entries": rows},
                              separators=(",", ":")).encode()
        if len(body) > MAX_CATALOG:
            raise ValueError("content catalog exceeds the Dash limit")
        return signed(self.key, body)

    def select(self, entry_id, build_id, revision_id):
        with self.lock:
            if entry_id not in self.entries:
                return None
            row = next((row for row in self.history[entry_id]
                        if row["build"] == build_id and row["revision"] == revision_id
                        and row["listed"]), None)
            if row is None:
                return None
            build = self.revisions.get(row["revision"])
            if build is None:
                revision = self.state / "revisions" / row["revision"]
                build = netbuild.Build(revision / "deploy", deltas=revision / "deltas",
                                       origins=self.origins, retail_base=self.retail_base)
                self.revisions[row["revision"]] = build
            return row, build


class ContentServer(netbuild.BuildServer):
    """A public signed catalog and revision tickets over the existing transfer protocol."""

    def __init__(self, library, bind, port, log=print):
        self.library = library
        self.activity = {}
        self.completed = {}
        self.bindings = {}
        super().__init__(None, bind, port, log=log)

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
                                        "client": ((hashlib.sha256(found[5]).hexdigest()[:12]
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
            try:
                return self.reply(request, 200, self.library.catalog())
            except (OSError, ValueError) as error:
                self.log(f"catalog unavailable: {error}")
                return self.reply(request, 503)
        if path.startswith("/v1/ticket/"):
            entry_id = path[len("/v1/ticket/"):]
            if len(request.path) > 640 or not ENTRY.fullmatch(entry_id):
                return self.reply(request, 400)
            try:
                params = urllib.parse.parse_qs(urllib.parse.urlsplit(request.path).query,
                                               max_num_fields=6)
                client, requested_build, revision, attempt, latest, signature = (
                    params[name][0] for name in ("client", "build", "revision", "attempt",
                                                "latest", "sig"))
            except (KeyError, IndexError, ValueError):
                return self.reply(request, 400)
            if (not re.fullmatch(r"[0-9a-f]{64}", client)
                    or not re.fullmatch(r"[0-9a-f]{64}", requested_build)
                    or not re.fullmatch(r"[0-9a-f]{64}", revision)
                    or not re.fullmatch(r"[0-9a-f]{16}", attempt)
                    or latest not in ("0", "1")
                    or not re.fullmatch(r"[0-9a-f]{128}", signature)):
                return self.reply(request, 400)
            client_key = bytes.fromhex(client)
            message = f"ticket:{entry_id}:{requested_build}:{revision}:{attempt}:{latest}".encode()
            try:
                ed25519.Ed25519PublicKey.from_public_bytes(client_key).verify(
                    bytes.fromhex(signature), message)
            except (InvalidSignature, ValueError):
                return self.reply(request, 403)
            with self.library.lock:
                entry = self.library.entries.get(entry_id)
            if entry is None:
                return self.reply(request, 404)
            try:
                selected = self.library.select(entry_id, requested_build, revision)
            except (OSError, ValueError, KeyError) as error:
                self.log(f"content revision unavailable: {error}")
                return self.reply(request, 503)
            if selected is None or (latest == "1" and entry["revision"] != revision):
                body = json.dumps({"format": 1, "build": entry["build"],
                                   "revision": entry["revision"]},
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
                               client=(hashlib.sha256(client_key).hexdigest()[:12] + " @ "
                                       + request.client_address[0]),
                               tickets=row["tickets"] + 1)
                    renewed = {key: value for key, value in row.items()
                               if key not in ("seen", "emitted", "_binding", "_token")}
            body = json.dumps({"format": 1, "build": requested_build,
                               "revision": revision,
                               "sha256": build.sha256.hex(), "size": len(build.data),
                               "token": token.hex(), "attempt": attempt},
                              separators=(",", ":")).encode()
            if renewed is not None:
                self.log("activity " + json.dumps(renewed, separators=(",", ":")))
            self.log(f"content ticket {entry_id}: {row_selected['build'][:12]}")
            return self.reply(request, 200, signed(self.library.key, body))
        return super().get(request)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="host a signed catalog and completed builds")
    serve.add_argument("--state", type=Path, default=Path("content-state"))
    serve.add_argument("--build-root", type=Path, default=Path("build"))
    serve.add_argument("--all-builds", action="store_true",
                       help="publish every completed build under BUILD-ROOT automatically")
    serve.add_argument("--build", type=Path, action="append", default=[], metavar="DEPLOY",
                       help="publish this completed deploy folder (repeatable)")
    serve.add_argument("--bind", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=PORT)
    serve.add_argument("--retail-base", type=Path)
    serve.add_argument("--serve-origin", action="append", choices=netbuild.ORIGINS)
    listing = sub.add_parser("list", help="list stored content publications")
    listing.add_argument("--state", type=Path, default=Path("content-state"))
    key_command = sub.add_parser("key", help="print the existing content service's public key")
    key_command.add_argument("--state", type=Path, default=Path("content-state"))
    edit = sub.add_parser("set", help="edit a published entry's display metadata")
    edit.add_argument("entry")
    edit.add_argument("--state", type=Path, default=Path("content-state"))
    edit.add_argument("--name")
    edit.add_argument("--description")
    for command in ("withdraw", "restore"):
        change = sub.add_parser(command, help=f"{command} one published revision")
        change.add_argument("entry")
        change.add_argument("revision")
        change.add_argument("--state", type=Path, default=Path("content-state"))
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
        print(f"fingerprint {hashlib.sha256(public).hexdigest()[:16]}")
        return
    if args.command != "serve":
        try:
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
            else:
                library.set_listed(args.entry, args.revision, args.command == "restore")
        except (OSError, ValueError) as error:
            parser.error(str(error))
        return
    if not args.all_builds and not args.build:
        parser.error("choose --all-builds or at least one --build")
    library = Library(args.state, args.build_root, args.build, args.all_builds,
                      args.serve_origin or netbuild.SERVED_BY_DEFAULT, args.retail_base)
    server = ContentServer(library, args.bind, args.port)
    print(f"content server on {args.bind}:{server.port}; {len(library.entries)} build(s); "
          f"key {hashlib.sha256(public_key(library.key)).hexdigest()[:16]}", flush=True)
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


if __name__ == "__main__":
    main()
