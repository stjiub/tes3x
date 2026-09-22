#!/usr/bin/env python3
"""Copy files or directories off the console over FTP."""

import argparse
import fnmatch
import ftplib
import os
import posixpath
import sys
import time

from tes3x_deploy import ftp_basename, human, remote_tree


def connect(args):
    ftp = ftplib.FTP(encoding="latin-1")
    ftp.connect(args.host, args.port, timeout=30)
    ftp.login(args.user, args.password)
    return ftp


def normalize(path):
    """`E:\\Foo` and `E:/Foo/` both become `E:/Foo`; a bare drive keeps its slash."""
    path = path.replace("\\", "/")
    return path if path.endswith(":/") else path.rstrip("/")


def is_dir(ftp, path):
    try:
        ftp.cwd(path)
        return True
    except ftplib.all_errors:
        return False


def list_dir(ftp, path):
    """Immediate children of a directory as (name, size, is_dir); [] if unreadable."""
    if not is_dir(ftp, path):
        return []
    lines = []
    ftp.retrlines("LIST", lines.append)
    out = []
    for line in lines:
        parts = line.split(maxsplit=8)
        if len(parts) < 9 or parts[8] in (".", ".."):
            continue
        try:
            size = int(parts[4])
        except ValueError:
            size = -1
        out.append((parts[8], size, line[0] == "d"))
    return out


def safe_join(out_dir, rel):
    """Remote names are untrusted; keep every write inside the output directory."""
    parts = [p for p in rel.replace("\\", "/").split("/") if p not in ("", ".", "..")]
    parts = [p.replace(":", "") for p in parts]
    if not parts:
        raise ValueError("empty destination for %r" % rel)
    dest = os.path.normpath(os.path.join(out_dir, *parts))
    if os.path.commonpath([os.path.abspath(dest), os.path.abspath(out_dir)]) \
            != os.path.abspath(out_dir):
        raise ValueError("refusing to write outside %s: %r" % (out_dir, rel))
    return dest


def plan(ftp, remote, tree):
    """Pairs of (remote path, local path relative to --out) for one argument."""
    remote = normalize(remote)
    parent, name = posixpath.split(remote)

    if is_dir(ftp, remote):
        jobs = []
        for rel in sorted(remote_tree(ftp, remote)):
            full = posixpath.join(remote, rel)
            jobs.append((full, full if tree else posixpath.relpath(full, parent)))
        return jobs

    # A wildcard is matched here: this server does not expand one in RETR.
    if any(c in name for c in "*?["):
        hits = [(n, sz) for n, sz, d in list_dir(ftp, parent)
                if not d and fnmatch.fnmatch(n.lower(), name.lower())]
        return [(posixpath.join(parent, n), posixpath.join(parent, n) if tree else n)
                for n, _ in sorted(hits)]

    return [(remote, remote if tree else name)]


def fetch(ftp, src, dest):
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    name = ftp_basename(ftp, src)
    with open(dest, "wb") as f:
        ftp.retrbinary("RETR %s" % name, f.write, blocksize=64 * 1024)
    return os.path.getsize(dest)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("remote", nargs="+",
                    help='file, directory or wildcard, e.g. "E:/tes3xprof.bin" or "E:/tes3x*"')
    ap.add_argument("--host", required=True)
    ap.add_argument("--port", type=int, default=21)
    ap.add_argument("--user", default="xbox")
    ap.add_argument("--password", default="xbox")
    ap.add_argument("--out", default=".", help="local destination directory")
    ap.add_argument("--tree", action="store_true",
                    help="keep the full remote path under --out, not just the basename")
    ap.add_argument("--list", action="store_true", help="list each path instead of copying")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    # Remote names are latin-1; the console encoding usually is not. Never let one
    # unprintable name kill a listing.
    sys.stdout.reconfigure(errors="replace")

    ftp = connect(args)
    print("connected to %s:%d as %s" % (args.host, args.port, args.user))

    if args.list:
        for path in args.remote:
            path = normalize(path)
            entries = list_dir(ftp, path)
            if not entries:
                print("\n  %s: empty, not a directory, or unreadable" % path)
                continue
            print("\n  %s: %d entries" % (path, len(entries)))
            for name, size, isdir in sorted(entries, key=lambda e: (not e[2], e[0].lower())):
                print("    %-44s %10s" % (name, "<dir>" if isdir else human(size)))
        ftp.quit()
        return

    jobs = []
    for path in args.remote:
        found = plan(ftp, path, args.tree)
        if not found:
            print("  %s: nothing matched" % path)
        jobs.extend(found)

    if not jobs:
        ftp.quit()
        sys.exit("nothing to fetch")

    if args.dry_run:
        for src, rel in jobs:
            print("    %s -> %s" % (src, safe_join(args.out, rel)))
        print("\n  %d file(s)" % len(jobs))
        ftp.quit()
        return

    got = 0
    t0 = time.time()
    for src, rel in jobs:
        dest = safe_join(args.out, rel)
        try:
            size = fetch(ftp, src, dest)
        except ftplib.all_errors as e:
            print("    FAILED %s: %s" % (src, e))
            continue
        got += size
        print("    %s -> %s  %s" % (src, dest, human(size)))
    el = time.time() - t0
    print("\n  %d file(s), %s in %.0fs (%s/s)" % (len(jobs), human(got), el,
                                                  human(got / max(el, 1))))
    ftp.quit()


if __name__ == "__main__":
    main()
