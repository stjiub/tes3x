#!/usr/bin/env python3
"""Build an nxdk program (the manager, its probes) into its own output folder.

nxdk's Makefile writes objects beside the sources, so the sources are copied to OUT first, and
the payload's sources (hooks/) beside it, for a program that shares its portable files, and the
release key (keys/) for one that checks signed updates. Libraries a Makefile names as ../NAME
(VENDOR) are downloaded once at a pinned SHA-256 and unpacked beside it too. Needs
an nxdk checkout with its libraries and tools built, and on Windows MSYS2 with make, clang and lld.
"""

import argparse
import hashlib
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tarfile
import tomllib
import urllib.request

from tes3x_paths import data_dir, local_config

MSYS2_DIRS = (Path("C:/msys64"),)
# name: (url, SHA-256 of the archive, its top folder)
VENDOR = {
    "mbedtls": ("https://github.com/Mbed-TLS/mbedtls/releases/download/mbedtls-3.6.7/"
                "mbedtls-3.6.7.tar.bz2",
                "a7e8bcbec0e6f761b4af24f25677626b35f762f68eef79c08677a363212d11f6",
                "mbedtls-3.6.7"),
}


class NxdkError(Exception):
    pass


def local_paths(config=None):
    path = local_config(config)
    try:
        with open(path, "rb") as stream:
            return tomllib.load(stream).get("paths", {})
    except FileNotFoundError:
        return {}


def find_nxdk(paths):
    nxdk = os.environ.get("NXDK_DIR") or paths.get("nxdk")
    if not nxdk or not (Path(nxdk) / "Makefile").is_file():
        raise NxdkError("nxdk not found; set NXDK_DIR or paths.nxdk in the local config")
    if not (Path(nxdk) / "lib" / "libnxdk.lib").is_file():
        raise NxdkError(f"{nxdk} has no built libraries; run make in it once")
    return Path(nxdk)


def find_bash(paths):
    if os.name != "nt":
        return shutil.which("bash") or "bash"
    roots = [Path(paths["msys2"])] if paths.get("msys2") else list(MSYS2_DIRS)
    for root in roots:
        bash = root / "usr" / "bin" / "bash.exe"
        if bash.is_file():
            return str(bash)
    raise NxdkError("MSYS2 not found; set paths.msys2 in the local config")


def posix(path):
    """A Windows path as MSYS2 spells it: D:/x -> /d/x."""
    p = Path(path).resolve().as_posix()
    return f"/{p[0].lower()}{p[2:]}" if os.name == "nt" and p[1:2] == ":" else p


def vendor(name):
    """A pinned library's unpacked folder, downloaded once into the per-user cache."""
    url, expected, top = VENDOR[name]
    cache = data_dir() / "vendor"
    folder = cache / top
    if folder.is_dir():
        return folder
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / url.rsplit("/", 1)[1]
    if not archive.is_file():
        print(f"downloading {url}", flush=True)
        with urllib.request.urlopen(url, timeout=120) as response:
            data = response.read()
        if hashlib.sha256(data).hexdigest() != expected:
            raise NxdkError(f"{archive.name}: SHA-256 {hashlib.sha256(data).hexdigest()}, "
                            f"expected {expected}")
        archive.write_bytes(data)
    partial = cache / (top + ".part")
    shutil.rmtree(partial, ignore_errors=True)
    with tarfile.open(archive) as tar:
        tar.extractall(partial, filter="data")
    os.replace(partial / top, folder)
    shutil.rmtree(partial, ignore_errors=True)
    return folder


def build(source, out, defines=(), config=None):
    paths = local_paths(config)
    nxdk, bash = find_nxdk(paths), find_bash(paths)
    out = Path(out)
    cflags = " ".join(f"-D{d}" for d in defines)
    # make does not rebuild objects when only the flags changed
    stamp = out / "build-flags.txt"
    keys = Path(source).resolve().parent / "keys"
    flags = cflags + "\n" + (Path(source) / "Makefile").read_text(encoding="utf-8")
    if (keys / "release.pub").is_file():
        flags += (keys / "release.pub").read_text(encoding="ascii")
    if stamp.is_file() and stamp.read_text(encoding="utf-8") != flags:
        for obj in [*out.rglob("*.obj"), *(out.parent / "hooks").glob("*.obj")]:
            obj.unlink()
    shutil.copytree(source, out, dirs_exist_ok=True)
    hooks = Path(source).resolve().parent / "hooks"
    if hooks.is_dir():
        shutil.copytree(hooks, out.parent / "hooks", dirs_exist_ok=True,
                        ignore=lambda _dir, names: [n for n in names
                                                    if not n.endswith((".c", ".h"))])
    if keys.is_dir():
        shutil.copytree(keys, out.parent / "keys", dirs_exist_ok=True)
    makefile = (Path(source) / "Makefile").read_text(encoding="utf-8")
    for name in VENDOR:
        if f"../{name}" in makefile and not (out.parent / name).is_dir():
            try:
                shutil.copytree(vendor(name), out.parent / name)
            except OSError as exc:
                raise NxdkError(f"{name}: {exc}") from exc
    script = (f"export NXDK_DIR={posix(nxdk)}; eval $($NXDK_DIR/bin/activate -s); "
              f"cd {posix(out)} && make NXDK_DIR=$NXDK_DIR CFLAGS={shlex.quote(cflags)}")
    env = dict(os.environ, MSYSTEM="MINGW64", CHERE_INVOKING="1")
    result = subprocess.run([bash, "-lc", script], env=env)
    xbe = out / "bin" / "default.xbe"
    if result.returncode or not xbe.is_file():
        raise NxdkError(f"build failed in {out}")
    stamp.write_text(flags, encoding="utf-8")
    return xbe


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("source", help="folder holding the program's Makefile and sources")
    ap.add_argument("out", help="build folder; bin/default.xbe is the result")
    ap.add_argument("-D", dest="defines", action="append", default=[], metavar="NAME=VALUE",
                    help="a C define (repeatable)")
    ap.add_argument("--config", help="local config (default: see docs/configuration.md)")
    a = ap.parse_args()
    try:
        print(build(a.source, a.out, a.defines, a.config))
    except NxdkError as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
