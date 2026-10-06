#!/usr/bin/env python3
"""Sync an authoritative deploy tree to Xbox FTP and preserve plugin load order."""

import argparse
import fnmatch
import ftplib
import hashlib
import io
import json
import os
import posixpath
import re
import socket
import sys
import time
from pathlib import Path
import tes3x_agent
import tes3x_ftp
import tes3x_manifest
from tes3x_pack import set_ini_key
from tes3x_paths import require_paths
import tes3x_savepool

PLUGIN_EXT = (".esm", ".esp")
MTIME_SLACK = 3
CACHE_DRIVES = ("X:", "Y:", "Z:")
MANIFEST = tes3x_manifest.NAME
# Written by deploys before the build manifest: {path: [size, sha1], ":build": {...}}.
LEGACY_MANIFEST = "tes3xdeploy.json"
# The dashboard keeps its metadata and artwork here. A build may add to it but never owns it.
DASHBOARD_DIR = "_resources/"
# Edited in place at the same size; without a manifest entry these always go.
IN_PLACE_EXT = (".xbe", ".ini", ".txt", ".xml")
BUILD_KEY = ":build"
PIPELINE_MARKER = ".tes3x-pipeline.json"
# Exit status when the target folder or save pool belongs to something else.
CONFLICT = 3
# Exit status when the console's drive cannot take the upload.
NO_SPACE = 4
CLUSTER = 16 * 1024  # FATX's unit of allocation on the console's partitions
SPACE_WARN = 256 * 1024 * 1024  # left free below this after a deploy, say so
# The XBMC4Gamers dashboard agent (addons/console), when installed: one line in, one line out.
AGENT_PORT = 7353
RETRYABLE_FTP = (ftplib.error_temp, ftplib.error_reply, ftplib.error_proto, EOFError, OSError)
REMOTE_ERRORS = ftplib.all_errors + (tes3x_agent.AgentError,)
# Through the manager, files arrive under this prefix and are renamed in once all are complete.
STAGING_PREFIX = "~t3x"
# [Xbox] settings of the console rather than a build; the payload reads them before Morrowind.ini.
CONSOLE_INI = "E:/TES3X/console.ini"


def sha1(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def read_manifest(ftp, base):
    return load_manifest(lambda path: read_remote(ftp, path), base)


def load_manifest(read, base):
    """The folder's build manifest, a legacy deploy record converted to one, or None.

    A converted record keeps its SHA-1s under "sha1"; they still vouch for unchanged files."""
    for name in (MANIFEST, LEGACY_MANIFEST):
        try:
            data = read(posixpath.join(base, name))
            if name == MANIFEST:
                return tes3x_manifest.parse(data)
            return legacy_manifest(json.loads(data))
        except REMOTE_ERRORS + (ValueError,):
            continue
    return None


def legacy_manifest(entries):
    build = entries.get(BUILD_KEY)
    # Folders deployed before builds were stamped name no profile.
    build = build if isinstance(build, dict) else {}
    pool = build.get("save_pool")
    return {"profile": build.get("profile"), "deployed": build.get("deployed"),
            "save_pool": {"id": pool} if pool else None, "legacy": True,
            "files": {path: {"size": value[0], "sha1": value[1]}
                      for path, value in entries.items()
                      if path != BUILD_KEY and isinstance(value, list) and len(value) == 2}}


def manifest_bytes(manifest):
    return (json.dumps(manifest, indent=1) + "\n").encode()


def deployed_manifest(built, record, local, hashes, previous=None):
    """The manifest a deploy leaves: the build's own, or one made for a hand-built tree, with
    the files as they now stand on the console.

    `previous` is the console's manifest when only some files were sent; its entries keep
    vouching for the rest, old-format SHA-1 ones included until a full deploy."""
    if built is None:
        built = tes3x_manifest.create(
            None, files={}, profile=record.get("profile"), source={"kind": "tree"},
            install_layout=record.get("install_layout", "full"),
            save_pool=record.get("save_pool"))
    sent = {r.lower() for r in local}
    built_ci = {p.lower(): e for p, e in built["files"].items()}
    files = {path: entry for path, entry in (previous or {}).get("files", {}).items()
             if path.lower() not in sent}
    for r, (size, _, path) in local.items():
        entry = built_ci.get(r.lower())
        kind = entry["origin"] if entry else tes3x_manifest.origin(r, Path(path))
        files[r] = {"size": size, "sha256": hashes[r], "origin": kind}
    manifest = {key: value for key, value in built.items() if key != "files"}
    manifest["deployed"] = time.strftime("%Y-%m-%d %H:%M")
    manifest["files"] = dict(sorted(files.items(), key=lambda item: item[0].lower()))
    if manifest.get("source") == {"kind": "tree"}:  # no pipeline id: the files sent are the build
        manifest["build"] = tes3x_manifest.build_id(manifest)
    return manifest


def build_summary(manifest):
    """What a folder list shows about a build: profile, save pool ID, deploy time, and the
    version of a tool such as the manager."""
    pool = manifest.get("save_pool") or {}
    source = manifest.get("source") or {}
    return {"profile": manifest.get("profile"), "save_pool": pool.get("id"),
            "deployed": manifest.get("deployed"),
            "version": source.get("version") if isinstance(source, dict) else None}


def build_record(tree):
    """What the pipeline recorded about the tree beside it, or {} for a tree made by hand."""
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(tree)), PIPELINE_MARKER),
                  encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def owner_conflicts(base, remote, manifest, profile):
    """Why deploying this profile to `base` would overwrite something it does not own."""
    if not remote:
        return []
    if manifest is None:
        size = sum(max(n, 0) for n in remote.values())
        return [f"{base} holds {len(remote)} files ({human(size)}) that TES3X did not deploy"]
    # Folders deployed before builds were stamped name no profile; they stay unchallenged.
    owner = manifest.get("profile")
    if owner is not None and owner != profile:
        when = manifest.get("deployed") or "an unknown time"
        return [f"{base} holds profile '{owner}', deployed {when}"]
    return []


def read_remote(ftp, path):
    buf = io.BytesIO()
    ftp.retrbinary(f"RETR {ftp_basename(ftp, path)}", buf.write)
    return buf.getvalue()


def pool_plan(ftp, pool):
    return pool_check(lambda base: remote_tree(ftp, base), lambda path: read_remote(ftp, path),
                      pool)


def pool_check(tree, read, pool):
    """(base, conflicts, files to write) for a save pool's E:/UDATA folder."""
    base = "E:/" + tes3x_savepool.folder(int(pool["id"], 16))
    present = {name.lower(): size for name, size in tree(base).items()}
    marker = tes3x_savepool.MARKER.lower()
    conflicts = []
    if marker in present:
        owner = read(f"{base}/{tes3x_savepool.MARKER}").decode("utf-8", "replace")
        if owner.strip() != pool["name"]:
            conflicts.append(f"{base} is save pool '{owner.strip()}', not '{pool['name']}'")
    elif present:
        conflicts.append(f"{base} holds {len(present)} files of another title")
    missing = [name for name in ("TitleMeta.xbx", "TitleImage.xbx", tes3x_savepool.MARKER)
               if name.lower() not in present]
    return base, conflicts, missing


def write_pool(ftp, base, pool, image, names):
    contents = tes3x_savepool.files(pool["name"], image)
    for name in names:
        path = f"{base}/{name}"
        ensure_dirs(ftp, path, set())
        ftp.storbinary(f"STOR {name}", io.BytesIO(contents[name]))
        print(f"  save pool: wrote {path}")


def local_tree(root):
    out = {}
    for dp, _, fns in os.walk(root):
        for fn in fns:
            full = os.path.join(dp, fn)
            rel = os.path.relpath(full, root).replace("\\", "/")
            st = os.stat(full)
            out[rel] = (st.st_size, st.st_mtime, full)
    return out


def remote_tree(ftp, base):
    # `LIST <path>` on this console's FTP server ignores the argument and lists the
    # drive root instead of erroring, which sends a path that doesn't exist yet into
    # unbounded recursion. cwd + bare LIST is the form it actually honors.
    out = {}

    def walk(path):
        try:
            ftp.cwd(path)
        except (ftplib.error_perm, ftplib.error_temp):
            return
        entries = []
        ftp.retrlines("LIST", entries.append)
        for line in entries:
            parts = line.split(maxsplit=8)
            if len(parts) < 9:
                continue
            name = parts[8]
            if name in (".", ".."):
                continue
            child = posixpath.join(path, name)
            if line[0] == "d":
                walk(child)
                ftp.cwd(path)
            else:
                rel = posixpath.relpath(child, base)
                try:
                    size = int(parts[4])
                except ValueError:
                    size = -1
                out[rel] = size

    walk(base)
    return out


def installed_builds(ftp, games_root):
    """{folder: its manifest's build record, or None} for each folder under games_root."""
    root = games_root.replace("\\", "/").rstrip("/")
    try:
        ftp.cwd(root)
    except (ftplib.error_perm, ftplib.error_temp):
        return {}
    entries = []
    ftp.retrlines("LIST", entries.append)
    folders = [parts[8] for parts in (line.split(maxsplit=8) for line in entries)
               if len(parts) == 9 and parts[0].startswith("d") and parts[8] not in (".", "..")]
    builds = {}
    for name in folders:
        manifest = read_manifest(ftp, posixpath.join(root, name))
        builds[name] = build_summary(manifest) if manifest else None
    return builds


def ensure_dirs(ftp, path, made):
    parent = posixpath.dirname(path)
    if parent in made:
        ftp.cwd(parent)
        return

    parts = parent.split("/")
    ftp.cwd(parts[0])
    current = parts[0]
    made.add(current)
    for part in parts[1:]:
        current = posixpath.join(current, part)
        try:
            ftp.cwd(part)
        except (ftplib.error_perm, ftplib.error_temp):
            ftp.mkd(part)
            ftp.cwd(part)
        made.add(current)


def ftp_basename(ftp, path):
    """Enter a file's directory and return the relative name this server accepts."""
    parent, name = posixpath.split(path)
    ftp.cwd(parent)
    return name


def human(n):
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return f"{n:.1f} {u}"
        n /= 1024


def upload_file(ftp, args, path, source, made, progress=None, retries=2):
    """Upload one file, reconnecting after a transient failure; return the usable FTP session."""
    for attempt in range(retries + 1):
        if ftp is None:
            try:
                ftp = tes3x_ftp.connect(args)
            except RETRYABLE_FTP as exc:
                if attempt >= retries:
                    raise RuntimeError(
                        f"upload failed for {path} after {attempt + 1} attempts: {exc}") from exc
                print(f"\n    reconnect failed: {exc}; retrying "
                      f"({attempt + 2}/{retries + 1})", flush=True)
                time.sleep(min(1 + attempt, 3))
                continue
        try:
            ensure_dirs(ftp, path, made)
            with open(source, "rb") as stream:
                name = ftp_basename(ftp, path)
                callback = None
                if progress:
                    progress(0, True)
                    callback = lambda block: progress(len(block), False)
                ftp.storbinary(f"STOR {name}", stream, blocksize=64 * 1024,
                               callback=callback)
            return ftp
        except ftplib.error_perm as exc:
            raise RuntimeError(f"upload failed for {path}: {exc}") from exc
        except RETRYABLE_FTP as exc:
            if attempt >= retries:
                raise RuntimeError(
                    f"upload failed for {path} after {attempt + 1} attempts: {exc}") from exc
            print(f"\n    transfer failed: {exc}; reconnecting and retrying "
                  f"({attempt + 2}/{retries + 1})", flush=True)
            try:
                ftp.close()
            except ftplib.all_errors:
                pass
            ftp = None
            time.sleep(min(1 + attempt, 3))
            made.clear()


def agent_request(host, line, timeout=10, token=None):
    with socket.create_connection((host, AGENT_PORT), timeout=timeout) as s:
        request = line if not token else token + " " + line
        s.sendall((request + "\n").encode("latin-1"))
        data = b""
        while not data.endswith(b"\n"):
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
    return data.decode("latin-1").strip()


def parse_drives(reply):
    """{drive: (free MB, total MB)} from the agent's 'ok C=free/total ...', None where unknown."""
    drives = {}
    for drive, free, total in re.findall(r"([A-Z])=(\d+|\?)/(\d+|\?)", reply or ""):
        drives[drive] = tuple(None if v == "?" else int(v) for v in (free, total))
    return drives


def drive_free(host, drive, timeout=3, token=None):
    """Bytes free on the console's drive from the dashboard agent; None without one."""
    try:
        reply = agent_request(host, "drives", timeout, token)
    except OSError:
        return None
    if not reply.startswith("ok"):
        return None
    free = parse_drives(reply).get(drive.upper(), (None, None))[0]
    return None if free is None else free * 1024 * 1024


def on_disk(size):
    return -(-size // CLUSTER) * CLUSTER


def orphans(remote, local_ci):
    """Console files the build does not contain, other than the dashboard's own."""
    return [r for r in remote
            if r.lower() not in local_ci and not r.lower().startswith(DASHBOARD_DIR)]


def verify_uploads(ftp, base, local, paths, mode):
    check_uploads(lambda path: remote_tree(ftp, path), lambda path: read_remote(ftp, path),
                  base, local, paths, mode)


def check_uploads(tree, read, base, local, paths, mode):
    """Verify uploaded files by remote size, or by retrieving and hashing their contents."""
    if mode == "none" or not paths:
        return
    remote_ci = {path.lower(): size for path, size in tree(base).items()}
    problems = [f"{path}: remote size {remote_ci.get(path.lower(), 'missing')}, "
                f"expected {local[path][0]}"
                for path in paths if remote_ci.get(path.lower()) != local[path][0]]
    if problems:
        raise RuntimeError("upload size verification failed: " + "; ".join(problems))
    if mode == "hash":
        for path in paths:
            if hashlib.sha1(read(posixpath.join(base, path))).hexdigest() != \
                    sha1(local[path][2]):
                raise RuntimeError(f"upload hash verification failed: {path}")


class FtpTarget:
    """The console through its dashboard's FTP server."""

    staged = False

    def __init__(self, args):
        self.args, self.made = args, set()
        self.ftp = tes3x_ftp.connect(args)
        feats = ""
        try:
            feats = self.ftp.sendcmd("FEAT")
        except ftplib.all_errors:
            pass
        self.timed = "MFMT" in feats.upper()
        self.label = (f"connected to {args.host}:{args.port} as {args.user}\n  MFMT (set mtime): "
                      + ("yes" if self.timed else "no - will pace plugin uploads"))

    def tree(self, base):
        return remote_tree(self.ftp, base)

    def read(self, path):
        return read_remote(self.ftp, path)

    def write(self, path, data):
        ensure_dirs(self.ftp, path, set())
        self.ftp.storbinary(f"STOR {posixpath.basename(path)}", io.BytesIO(data))

    def upload(self, path, source, progress):
        self.ftp = upload_file(self.ftp, self.args, path, source, self.made, progress,
                               self.args.retries)

    def delete(self, path):
        self.ftp.delete(ftp_basename(self.ftp, path))

    def set_time(self, path, mtime):
        """False once the server has refused; later plugins are then paced instead."""
        if self.timed:
            stamp = time.strftime("%Y%m%d%H%M%S", time.gmtime(mtime))
            try:
                self.ftp.sendcmd(f"MFMT {stamp} {ftp_basename(self.ftp, path)}")
            except ftplib.all_errors:
                self.timed = False
        return self.timed

    def free(self, drive):
        return drive_free(self.args.host, drive, token=getattr(self.args, "agent_token", None))

    def clear(self, drive):
        for name in self.ftp.nlst(f"/{drive}"):
            if posixpath.basename(name) not in (".", ".."):
                try:
                    self.ftp.delete(name)
                except ftplib.all_errors:
                    pass

    def close(self):
        try:
            self.ftp.quit()
        except ftplib.all_errors:
            pass


class AgentTarget:
    """The console through the TES3X manager's agent. Uploads are staged: each file goes to a
    temporary name and all are renamed in at the end, so an interrupted deploy leaves the build
    as it was."""

    staged = True
    timed = True

    def __init__(self, args):
        self.listener, self.client = tes3x_agent.connect(args.agent_key, args.host,
                                                         args.agent_port, args.agent_wait)
        self.label = f"paired with the manager at {self.client.host}"
        self.dirs = set()

    def tree(self, base):
        out = {}

        def walk(path, prefix):
            try:
                entries = self.client.list(path)
            except tes3x_agent.AgentError:
                return
            for name, is_dir, size in entries:
                if is_dir:
                    walk(f"{path}/{name}", f"{prefix}{name}/")
                else:
                    out[prefix + name] = size

        walk(base.rstrip("/"), "")
        return out

    def read(self, path):
        return self.client.fetch(path)

    def ensure_dirs(self, path):
        parts = posixpath.dirname(path).split("/")
        for count in range(2, len(parts) + 1):
            folder = "/".join(parts[:count])
            if folder not in self.dirs:
                self.client.mkdir(folder)
                self.dirs.add(folder)

    def write(self, path, data):
        self.ensure_dirs(path)
        self.client.put(path, data)

    def upload(self, path, source, progress):
        self.ensure_dirs(path)
        data = Path(source).read_bytes()
        done = 0

        def advance(written):
            nonlocal done
            progress(written - done, False)
            done = written

        progress(0, True)
        self.client.put(path, data, progress=advance)

    def delete(self, path):
        self.client.delete(path)

    def rename(self, source, target):
        self.client.rename(source, target)

    def set_time(self, path, mtime):
        self.client.set_time(path, mtime)
        return True

    def free(self, drive):
        try:
            return self.client.space(f"{drive}:/")[0]
        except tes3x_agent.AgentError:
            return None

    def clear(self, drive):
        for name, is_dir, _ in self.client.list(f"{drive[0]}:/"):
            if not is_dir:
                try:
                    self.client.delete(f"{drive[0]}:/{name}")
                except tes3x_agent.AgentError:
                    pass

    def close(self):
        self.listener.close()


def ini_pairs(text):
    """[(key, value)] of a file's [Xbox] section."""
    pairs, section = [], None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("["):
            section = line.lower()
        elif section == "[xbox]" and "=" in line and not line.startswith(";"):
            key, _, value = line.partition("=")
            pairs.append((key.strip(), value.strip()))
    return pairs


def merge_console_ini(remote, wanted):
    """The console's console.ini with the build's keys set; its other keys are kept."""
    text = remote.replace("\r\n", "\n").strip("\n")
    for key, value in ini_pairs(wanted):
        text = set_ini_key(text, "Xbox", key, value)
    return text.strip("\n").replace("\n", "\r\n") + "\r\n"


def write_console_ini(target, source, dry_run):
    wanted = Path(source).read_text(encoding="latin-1")
    try:
        remote = target.read(CONSOLE_INI).decode("latin-1")
    except REMOTE_ERRORS:
        remote = ""
    merged = merge_console_ini(remote, wanted)
    keys = ", ".join(key for key, _ in ini_pairs(wanted))
    if ini_pairs(merged) == ini_pairs(remote):
        print(f"  {CONSOLE_INI}: {keys} already set")
    elif dry_run:
        print(f"  {CONSOLE_INI}: would set {keys}")
    else:
        put_file(target, CONSOLE_INI, merged.encode("latin-1"))
        print(f"  {CONSOLE_INI}: set {keys}")


def staging_name(path, index):
    return posixpath.join(posixpath.dirname(path), f"{STAGING_PREFIX}{index}.new")


def put_file(target, path, data):
    """Write a small file whole; a staged target renames it over the old one."""
    if not target.staged:
        target.write(path, data)
        return
    staged = staging_name(path, "")
    target.write(staged, data)
    try:
        target.delete(path)
    except REMOTE_ERRORS:
        pass
    target.rename(staged, path)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("tree", help="staged deploy tree (tes3x_pack --out)")
    tes3x_ftp.add_arguments(ap)
    ap.add_argument("--remote", required=True, help='e.g. "E:/Games/Morrowind"')
    ap.add_argument("--only", action="append", default=[], metavar="PATH",
                    help="send just these tree-relative paths, wildcards allowed "
                         "(repeatable). Nothing is deleted: the rest of the console's "
                         "tree is left as it stands")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--require-current", action="store_true",
                    help="with --dry-run, exit as a conflict unless the remote tree already "
                         "matches exactly")
    ap.add_argument("--replace", action="store_true",
                    help="deploy even where the folder belongs to another profile or to no TES3X "
                         "build, or the save pool's folder is not this pool's")
    ap.add_argument("--clear-cache", action="store_true", help="empty X:/Y:/Z: cache partitions")
    ap.add_argument("--verify", choices=("none", "size", "hash"), default="none",
                    help="verify uploaded files after transfer; hash retrieves every upload")
    ap.add_argument("--ignore-space", action="store_true",
                    help="deploy even when the dashboard agent reports too little free space")
    ap.add_argument("--plugin-delay", type=float, default=2.5,
                    help="seconds between plugin uploads when MFMT is unsupported")
    ap.add_argument("--retries", type=int, default=2,
                    help="times to retry a file after a transient FTP failure (default: 2)")
    ap.add_argument("--agent", action="store_true",
                    help="deploy through the TES3X manager's agent instead of FTP; the manager "
                         "must be running with NetAgent naming this PC")
    ap.add_argument("--agent-key", help="this PC's agent key (default: tes3x.agent.key beside "
                                        "the local config)")
    ap.add_argument("--agent-port", type=int, default=tes3x_agent.PORT,
                    help=f"UDP port NetAgent names (default: {tes3x_agent.PORT})")
    ap.add_argument("--agent-wait", type=float, default=60,
                    help="seconds to wait for the manager to pair (default: 60)")
    ap.add_argument("--console-ini", metavar="FILE",
                    help=f"merge this file's [Xbox] keys into the console's {CONSOLE_INI}")
    args = ap.parse_args()
    if args.require_current and not args.dry_run:
        ap.error("--require-current needs --dry-run")
    if args.retries < 0:
        ap.error("--retries cannot be negative")

    if not os.path.isdir(args.tree):
        sys.exit(f"not a directory: {args.tree}")

    local = local_tree(args.tree)
    built = tes3x_manifest.load(args.tree)
    for r in [r for r in local if r.lower() == MANIFEST]:
        del local[r]
    if args.only:
        matched = {r for r in local
                   for pat in args.only if r == pat or fnmatch.fnmatch(r, pat)}
        unused = [p for p in args.only
                  if not any(r == p or fnmatch.fnmatch(r, p) for r in local)]
        if unused:
            sys.exit("not in the staged tree: " + ", ".join(unused))
        local = {r: v for r, v in local.items() if r in matched}
    # Check the actual destination before opening FTP, including dry-run.
    require_paths(local, args.remote)
    total = sum(v[0] for v in local.values())
    print(f"staged tree: {len(local)} files, {human(total)}"
          + (" (selected)" if args.only else ""))

    base = args.remote.replace("\\", "/").rstrip("/")
    tes3x_ftp.resolve(args)
    if args.agent:
        args.agent_key = args.agent_key or str(
            Path(args.config or Path.cwd() / "tes3x.local.toml").with_name("tes3x.agent.key"))
        secret = tes3x_agent.load_or_create_key(args.agent_key)
        print(f"waiting for the manager at {args.host} to pair "
              f"(agent key {tes3x_agent.key_fingerprint(secret)})", flush=True)
    try:
        target = AgentTarget(args) if args.agent else FtpTarget(args)
    except tes3x_agent.AgentError as exc:
        sys.exit(str(exc))
    print(target.label)
    try:
        sync(args, target, base, local, built)
        if args.console_ini:
            write_console_ini(target, args.console_ini, args.dry_run)
    except tes3x_agent.AgentError as exc:
        sys.exit(f"agent: {exc}")
    finally:
        target.close()
    print("done")


def sync(args, target, base, local, built):
    remote = target.tree(base)
    remote.pop(MANIFEST, None)
    legacy = remote.pop(LEGACY_MANIFEST, None) is not None
    manifest = load_manifest(target.read, base)
    known_files = {p.lower(): e for p, e in manifest["files"].items()} if manifest else {}
    print(f"  console has {len(remote)} files under {base}, "
          + (f"manifest with {len(known_files)} entries" if manifest else "manifest missing")
          + (" (old format)" if manifest and manifest.get("legacy") else ""))

    record = build_record(args.tree)
    pool = record.get("save_pool")
    conflicts = owner_conflicts(base, remote, manifest, record.get("profile"))
    pool_missing = []
    if pool:
        pool_base, pool_conflicts, pool_missing = pool_check(target.tree, target.read, pool)
        conflicts += pool_conflicts
        print(f"  save pool '{pool['name']}': {pool_base}"
              + (f", {len(pool_missing)} file(s) to write" if pool_missing else ""))
    for line in conflicts:
        print(f"  conflict: {line}")
    if conflicts and not args.replace and not args.dry_run:
        print("nothing changed; deploy with --replace to go ahead")
        sys.exit(CONFLICT)

    hashes = {r: tes3x_manifest.sha256_file(v[2]) for r, v in local.items()}

    # FATX is case-insensitive, so a tree carrying both music/Battle and music/battle
    # matches one remote directory. Comparing case-sensitively made every sync delete
    # one spelling and upload the other, for ever.
    remote_ci = {r.lower(): sz for r, sz in remote.items()}
    local_ci = {r.lower() for r in local}

    def stale(r):
        sz = local[r][0]
        have = remote_ci.get(r.lower())
        if have != sz:
            return True
        # A manifest entry only vouches for the file if the size still agrees with it.
        known = known_files.get(r.lower())
        if known and known["size"] == have:
            if "sha256" in known:
                return known["sha256"] != hashes[r]
            return known.get("sha1") != sha1(local[r][2])
        return r.lower().endswith(IN_PLACE_EXT)

    # A named selection goes regardless: an XBE edited in place is the normal case.
    upload = [r for r in local if args.only or stale(r)]
    # Load order is upload order without MFMT, so one changed plugin resends them all.
    if not target.timed and any(r.lower().endswith(PLUGIN_EXT) for r in upload):
        upload += [r for r in local if r.lower().endswith(PLUGIN_EXT) and r not in upload]
    # A selected send says nothing about what else belongs on the console.
    delete = [] if args.only else orphans(remote, local_ci)
    up_bytes = sum(local[r][0] for r in upload)
    print(f"\n  upload {len(upload)} files ({human(up_bytes)})")
    print(f"  delete {len(delete)} orphaned files")
    # Staging keeps a build runnable through an interrupted deploy; an empty folder has none,
    # and each rename costs about 40 ms in a FATX directory of 500 files.
    staging = target.staged and bool(remote)
    # Staged files sit beside the ones they replace until all have arrived.
    replaced = 0 if staging else sum(on_disk(remote_ci.get(r.lower()) or 0) for r in upload)
    grow = (sum(on_disk(local[r][0]) for r in upload) - replaced
            - sum(on_disk(remote[r]) for r in delete))
    drive = base[0].upper()
    free = target.free(drive)
    if free is None:
        print(f"  space: needs {human(max(grow, 0))} more on {drive}:; free space unknown "
              "(the dashboard agent is not installed or not answering)")
    else:
        print(f"  space: needs {human(max(grow, 0))} more on {drive}:, {human(free)} free")
        if grow > free and not args.dry_run and not args.ignore_space:
            print(f"nothing changed: {drive}: is {human(grow - free)} short; free some space, "
                  "or deploy with --ignore-space")
            sys.exit(NO_SPACE)
        if grow > free:
            print(f"  warning: {drive}: is {human(grow - free)} short")
        elif free - grow < SPACE_WARN:
            print(f"  warning: {human(free - grow)} will be left free on {drive}:")

    if args.dry_run:
        for name in pool_missing:
            print(f"    + {pool_base}/{name}")
        for r in sorted(delete)[:20]:
            print(f"    - {r}")
        if len(delete) > 20:
            print(f"    - ... {len(delete)-20} more")
        for r in sorted(upload)[:20]:
            print(f"    + {r}  {human(local[r][0])}")
        if len(upload) > 20:
            print(f"    + ... {len(upload)-20} more")
        if args.require_current and (conflicts or delete or upload):
            print("nothing changed: the required remote tree is missing or out of date")
            sys.exit(CONFLICT)
        return

    for r in sorted(delete):
        try:
            target.delete(posixpath.join(base, r))
        except REMOTE_ERRORS as e:
            print(f"    delete failed {r}: {e}")

    plugins = sorted((r for r in upload if r.lower().endswith(PLUGIN_EXT)),
                     key=lambda r: local[r][1])
    assets = [r for r in upload if r not in set(plugins)]
    sent = 0
    t0 = time.time()

    transfers = assets + plugins
    staged = {}
    for index, r in enumerate(transfers, 1):
        dst = posixpath.join(base, r)
        size = local[r][0]
        print(f"  [{index}/{len(transfers)}] {r} ({human(size)})", flush=True)
        file_sent = 0
        last_progress = 0.0

        def progress(amount, reset):
            nonlocal file_sent, last_progress
            if reset:
                file_sent, last_progress = 0, 0.0
                return
            file_sent += amount
            now = time.monotonic()
            if file_sent == size or now - last_progress >= 0.5:
                elapsed = max(time.time() - t0, 1)
                print(f"\r    {human(file_sent)}/{human(size)} · total "
                      f"{human(sent + file_sent)}/{human(up_bytes)} · "
                      f"{human((sent + file_sent) / elapsed)}/s   ", end="", flush=True)
                last_progress = now

        if staging:
            staged[r] = staging_name(dst, index)
        try:
            target.upload(staged.get(r, dst), local[r][2], progress)
        except RuntimeError as exc:
            sys.exit(str(exc))
        print()
        sent += local[r][0]
        if not staging and not target.set_time(dst, local[r][1]) and r in set(plugins):
            time.sleep(args.plugin_delay)

    print(f"  uploaded in {time.time()-t0:.0f}s")
    if staged:
        t0 = time.time()
        for r, path in staged.items():
            dst = posixpath.join(base, r)
            if r.lower() in remote_ci:
                target.delete(dst)
            target.rename(path, dst)
            target.set_time(dst, local[r][1])
        print(f"  renamed {len(staged)} staged files into place in {time.time()-t0:.0f}s")

    check_uploads(target.tree, target.read, base, local, assets + plugins, args.verify)
    if args.verify != "none":
        print(f"  verified {len(assets) + len(plugins)} uploaded files by {args.verify}")

    put_file(target, posixpath.join(base, MANIFEST), manifest_bytes(deployed_manifest(
        built, record, local, hashes, manifest if args.only else None)))
    if legacy:
        try:
            target.delete(posixpath.join(base, LEGACY_MANIFEST))
        except REMOTE_ERRORS as e:
            print(f"    delete failed {LEGACY_MANIFEST}: {e}")

    if pool_missing:
        image = tes3x_savepool.title_image(os.path.join(args.tree, "Default.xbe"))
        contents = tes3x_savepool.files(pool["name"], image)
        for name in pool_missing:
            target.write(f"{pool_base}/{name}", contents[name])
            print(f"  save pool: wrote {pool_base}/{name}")

    if args.clear_cache:
        for drive in CACHE_DRIVES:
            try:
                target.clear(drive)
                print(f"  cleared {drive}")
            except REMOTE_ERRORS:
                print(f"  {drive} not accessible")


if __name__ == "__main__":
    main()
