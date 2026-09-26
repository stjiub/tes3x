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
import sys
import time
import tes3x_ftp
from tes3x_paths import require_paths

PLUGIN_EXT = (".esm", ".esp")
MTIME_SLACK = 3
CACHE_DRIVES = ("X:", "Y:", "Z:")
MANIFEST = "tes3xdeploy.json"
# The dashboard keeps its metadata and artwork here. A build may add to it but never owns it.
DASHBOARD_DIR = "_resources/"
# Edited in place at the same size; without a manifest entry these always go.
IN_PLACE_EXT = (".xbe", ".ini", ".txt", ".xml")


def sha1(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def read_manifest(ftp, base):
    buf = io.BytesIO()
    try:
        ftp.retrbinary(f"RETR {ftp_basename(ftp, posixpath.join(base, MANIFEST))}", buf.write)
        return {k.lower(): v for k, v in json.loads(buf.getvalue()).items()}
    except ftplib.all_errors + (ValueError,):
        return {}


def write_manifest(ftp, base, entries):
    data = json.dumps(dict(sorted(entries.items())), indent=0).encode()
    ftp.storbinary(f"STOR {ftp_basename(ftp, posixpath.join(base, MANIFEST))}", io.BytesIO(data))


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


def orphans(remote, local_ci):
    """Console files the build does not contain, other than the dashboard's own."""
    return [r for r in remote
            if r.lower() not in local_ci and not r.lower().startswith(DASHBOARD_DIR)]


def verify_uploads(ftp, base, local, paths, mode):
    """Verify uploaded files by remote size, or by retrieving and hashing their contents."""
    if mode == "none" or not paths:
        return
    remote = remote_tree(ftp, base)
    remote_ci = {path.lower(): size for path, size in remote.items()}
    problems = [f"{path}: remote size {remote_ci.get(path.lower(), 'missing')}, "
                f"expected {local[path][0]}"
                for path in paths if remote_ci.get(path.lower()) != local[path][0]]
    if problems:
        raise RuntimeError("upload size verification failed: " + "; ".join(problems))
    if mode == "hash":
        for path in paths:
            digest = hashlib.sha1()
            name = ftp_basename(ftp, posixpath.join(base, path))
            ftp.retrbinary(f"RETR {name}", digest.update, blocksize=64 * 1024)
            expected = sha1(local[path][2])
            if digest.hexdigest() != expected:
                raise RuntimeError(f"upload hash verification failed: {path}")


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
    ap.add_argument("--clear-cache", action="store_true", help="empty X:/Y:/Z: cache partitions")
    ap.add_argument("--verify", choices=("none", "size", "hash"), default="none",
                    help="verify uploaded files after transfer; hash retrieves every upload")
    ap.add_argument("--plugin-delay", type=float, default=2.5,
                    help="seconds between plugin uploads when MFMT is unsupported")
    args = ap.parse_args()

    if not os.path.isdir(args.tree):
        sys.exit(f"not a directory: {args.tree}")

    local = local_tree(args.tree)
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
    ftp = tes3x_ftp.connect(args)
    print(f"connected to {args.host}:{args.port} as {args.user}")

    feats = ""
    try:
        feats = ftp.sendcmd("FEAT")
    except ftplib.all_errors:
        pass
    has_mfmt = "MFMT" in feats.upper()
    print(f"  MFMT (set mtime): {'yes' if has_mfmt else 'no - will pace plugin uploads'}")

    remote = remote_tree(ftp, base)
    remote.pop(MANIFEST, None)
    manifest = read_manifest(ftp, base)
    print(f"  console has {len(remote)} files under {base}, "
          f"manifest {'with %d entries' % len(manifest) if manifest else 'missing'}")

    hashes = {r: sha1(v[2]) for r, v in local.items()}

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
        known = manifest.get(r.lower())
        if known and known[0] == have:
            return known[1] != hashes[r]
        return r.lower().endswith(IN_PLACE_EXT)

    # A named selection goes regardless: an XBE edited in place is the normal case.
    upload = [r for r in local if args.only or stale(r)]
    # Load order is upload order without MFMT, so one changed plugin resends them all.
    if not has_mfmt and any(r.lower().endswith(PLUGIN_EXT) for r in upload):
        upload += [r for r in local if r.lower().endswith(PLUGIN_EXT) and r not in upload]
    # A selected send says nothing about what else belongs on the console.
    delete = [] if args.only else orphans(remote, local_ci)
    up_bytes = sum(local[r][0] for r in upload)
    print(f"\n  upload {len(upload)} files ({human(up_bytes)})")
    print(f"  delete {len(delete)} orphaned files")

    if args.dry_run:
        for r in sorted(delete)[:20]:
            print(f"    - {r}")
        if len(delete) > 20:
            print(f"    - ... {len(delete)-20} more")
        for r in sorted(upload)[:20]:
            print(f"    + {r}  {human(local[r][0])}")
        if len(upload) > 20:
            print(f"    + ... {len(upload)-20} more")
        ftp.quit()
        return

    for r in sorted(delete):
        try:
            dst = posixpath.join(base, r)
            ftp.delete(ftp_basename(ftp, dst))
        except ftplib.all_errors as e:
            print(f"    delete failed {r}: {e}")

    made = set()
    plugins = sorted((r for r in upload if r.lower().endswith(PLUGIN_EXT)),
                     key=lambda r: local[r][1])
    assets = [r for r in upload if r not in set(plugins)]
    sent = 0
    t0 = time.time()

    for r in assets + plugins:
        dst = posixpath.join(base, r)
        ensure_dirs(ftp, dst, made)
        with open(local[r][2], "rb") as f:
            name = ftp_basename(ftp, dst)
            ftp.storbinary(f"STOR {name}", f, blocksize=64 * 1024)
        sent += local[r][0]
        if has_mfmt:
            stamp = time.strftime("%Y%m%d%H%M%S", time.gmtime(local[r][1]))
            try:
                ftp.sendcmd(f"MFMT {stamp} {name}")
            except ftplib.all_errors:
                has_mfmt = False
        elif r in set(plugins):
            time.sleep(args.plugin_delay)
        el = time.time() - t0
        print(f"\r  {human(sent)}/{human(up_bytes)}  {human(sent/max(el,1))}/s   ", end="", flush=True)

    print(f"\n  uploaded in {time.time()-t0:.0f}s")

    verify_uploads(ftp, base, local, assets + plugins, args.verify)
    if args.verify != "none":
        print(f"  verified {len(assets) + len(plugins)} uploaded files by {args.verify}")

    entries = {} if not args.only else dict(manifest)
    for r in local:
        entries[r.lower()] = [local[r][0], hashes[r]]
    write_manifest(ftp, base, entries)

    if args.clear_cache:
        for drive in CACHE_DRIVES:
            try:
                names = ftp.nlst(f"/{drive}")
                for n in names:
                    if posixpath.basename(n) in (".", ".."):
                        continue
                    try:
                        ftp.delete(n)
                    except ftplib.all_errors:
                        pass
                print(f"  cleared {drive}")
            except ftplib.all_errors:
                print(f"  {drive} not accessible")

    ftp.quit()
    print("done")


if __name__ == "__main__":
    main()
