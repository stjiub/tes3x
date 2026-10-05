#!/usr/bin/env python3
"""Build an nxdk program (the manager, its probes) into its own output folder.

nxdk's Makefile writes objects beside the sources, so the sources are copied to OUT first. Needs
an nxdk checkout with its libraries and tools built, and on Windows MSYS2 with make, clang and lld.
"""

import argparse
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tomllib

MSYS2_DIRS = (Path("C:/msys64"),)


class NxdkError(Exception):
    pass


def local_paths(config=None):
    path = Path(config) if config else Path.cwd() / "tes3x.local.toml"
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


def build(source, out, defines=(), config=None):
    paths = local_paths(config)
    nxdk, bash = find_nxdk(paths), find_bash(paths)
    out = Path(out)
    shutil.copytree(source, out, dirs_exist_ok=True)
    cflags = " ".join(f"-D{d}" for d in defines)
    script = (f"export NXDK_DIR={posix(nxdk)}; eval $($NXDK_DIR/bin/activate -s); "
              f"cd {posix(out)} && make NXDK_DIR=$NXDK_DIR CFLAGS={shlex.quote(cflags)}")
    env = dict(os.environ, MSYSTEM="MINGW64", CHERE_INVOKING="1")
    result = subprocess.run([bash, "-lc", script], env=env)
    xbe = out / "bin" / "default.xbe"
    if result.returncode or not xbe.is_file():
        raise NxdkError(f"build failed in {out}")
    return xbe


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("source", help="folder holding the program's Makefile and sources")
    ap.add_argument("out", help="build folder; bin/default.xbe is the result")
    ap.add_argument("-D", dest="defines", action="append", default=[], metavar="NAME=VALUE",
                    help="a C define (repeatable)")
    ap.add_argument("--config", help="local config (default: ./tes3x.local.toml)")
    a = ap.parse_args()
    try:
        print(build(a.source, a.out, a.defines, a.config))
    except NxdkError as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
