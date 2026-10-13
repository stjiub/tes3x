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

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from tes3x import manifest, netbuild

PORT = 26510
MAX_ENTRIES = 64
MAX_CATALOG = 64 * 1024
ENTRY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,41}\Z")


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
        self.revisions = {}
        self.lock = threading.RLock()
        self.refresh()

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
        digest = hashlib.sha256(data).hexdigest()
        revision = self.state / "revisions" / digest
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
                    digest = delta["sha256"]
                    if not re.fullmatch(r"[0-9a-f]{64}", digest):
                        raise ValueError("invalid XBE delta digest")
                    original = source.parent / "deltas" / f"{digest}.zst"
                    if not original.is_file() or original.is_symlink():
                        raise ValueError(f"missing XBE delta {original}")
                    destination = deltas / original.name
                    shutil.copyfile(original, destination)
                    if (destination.stat().st_size != delta["size"]
                            or manifest.sha256_file(destination) != digest):
                        raise ValueError(f"XBE delta changed while publishing: {original}")
                revision.parent.mkdir(parents=True, exist_ok=True)
                staged.rename(revision)
            finally:
                if staged.exists():
                    shutil.rmtree(staged)
        build = self.revisions.get(digest)
        if build is None:
            build = netbuild.Build(revision / "deploy", deltas=revision / "deltas",
                                   origins=self.origins, retail_base=self.retail_base)
        name = str(parsed.get("profile") or entry_id)[:47]
        with self.lock:
            self.revisions[digest] = build
            self.entries[entry_id] = {"id": entry_id, "name": name, "description": "",
                                      "build": build_id, "revision": digest,
                                      "layout": parsed.get("install_layout", "full"),
                                      "bytes": sum(item["size"] for item in parsed["files"].values()),
                                      "sha256": build.sha256.hex(), "size": len(build.data)}

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
            body = json.dumps({"format": 1, "entries": list(self.entries.values())},
                              separators=(",", ":")).encode()
        if len(body) > MAX_CATALOG:
            raise ValueError("content catalog exceeds the Dash limit")
        return signed(self.key, body)


class ContentServer(netbuild.BuildServer):
    """A public signed catalog and revision tickets over the existing transfer protocol."""

    def __init__(self, library, bind, port, log=print):
        self.library = library
        super().__init__(None, bind, port, log=log)

    def build_for(self, token):
        with self.lock:
            found = self.tickets.get(token)
        return found[1] if found and found[0] >= time.monotonic() else None

    def get(self, request):
        path = urllib.parse.urlsplit(request.path).path
        if path == "/v1/catalog":
            try:
                return self.reply(request, 200, self.library.catalog())
            except (OSError, ValueError) as error:
                self.log(f"catalog unavailable: {error}")
                return self.reply(request, 503)
        if path.startswith("/v1/ticket/"):
            entry_id = path[len("/v1/ticket/"):]
            with self.library.lock:
                entry = self.library.entries.get(entry_id)
                build = self.library.revisions.get(entry["revision"]) if entry else None
            if build is None:
                return self.reply(request, 404)
            token = secrets.token_bytes(16)
            with self.lock:
                now = time.monotonic()
                self.tickets = {key: value for key, value in self.tickets.items()
                                if value[0] >= now}
                if len(self.tickets) >= netbuild.TICKETS:
                    self.tickets.pop(next(iter(self.tickets)))
                self.tickets[token.hex()] = (now + netbuild.TICKET_SECONDS, build)
            body = json.dumps({"format": 1, "build": entry["build"],
                               "sha256": build.sha256.hex(), "size": len(build.data),
                               "token": token.hex()}, separators=(",", ":")).encode()
            self.log(f"content ticket {entry_id}: {entry['build'][:12]}")
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
    args = parser.parse_args(argv)
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
