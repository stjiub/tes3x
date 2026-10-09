#!/usr/bin/env python3
"""Build a portable TES3X folder: TES3X.exe, an embedded Python holding TES3X, 7-Zip and LLVM."""

import argparse
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile

from tes3x.paths import checkout, data_dir

ROOT = checkout()
PYTHON = "3.12.9"
EMBED_URL = "https://www.python.org/ftp/python/{0}/python-{0}-embed-amd64.zip"
SEVEN_ZIP = "26.03"
SEVEN_ZIP_URL = "https://github.com/ip7z/7zip/releases/download/26.03/"
LLVM = "22.1.8"
LLVM_ARCHIVE = f"clang+llvm-{LLVM}-x86_64-pc-windows-msvc"
DOWNLOADS = {
    "7zr.exe": (SEVEN_ZIP_URL + "7zr.exe",
                "ad4c82fadcbdf93c03b4fc440f300509c7d60c5c2f4d183e35d9d70d6957037d"),
    "7z2603-x64.exe": (SEVEN_ZIP_URL + "7z2603-x64.exe",
                       "0859c524b8a63551848f0c246abddcb1d0b7b656b0fbfe879f8d85e61a9e6edd"),
    LLVM_ARCHIVE + ".tar.xz": (
        f"https://github.com/llvm/llvm-project/releases/download/llvmorg-{LLVM}/"
        f"{LLVM_ARCHIVE}.tar.xz",
        "d96c2cc1736f4eb7fa43cb9bbdf56d93551a9ae0a9aadb9c99c3c3b2b712a234"),
}
# The binary release carries no license; ship each component's from the same tag.
LLVM_LICENSES = {
    "llvm": "8d85c1057d742e597985c7d4e6320b015a9139385cff4cbae06ffc0ebe89afee",
    "clang": "ebcd9bbf783a73d05c53ba4d586b8d5813dcdf3bbec50265860ccc885e606f47",
    "lld": "f7891568956e34643eb6a0db1462db30820d40d7266e2a78063f2fe233ece5a0",
}
for _part, _sha in LLVM_LICENSES.items():
    DOWNLOADS[f"LICENSE-{_part}-{LLVM}.TXT"] = (
        f"https://raw.githubusercontent.com/llvm/llvm-project/llvmorg-{LLVM}/{_part}/LICENSE.TXT",
        _sha)
NOTICES = f"""# Third-party software

This folder holds programs TES3X runs, unmodified, each with its license.

- `7zip/`: 7-Zip {SEVEN_ZIP} by Igor Pavlov, <https://www.7-zip.org>, which unpacks `.7z` and `.rar`
  mods. GNU LGPL 2.1 with the unRAR restriction, BSD 3-clause in parts; see
  `7zip/License.txt`. Source: {SEVEN_ZIP_URL}7z2603-src.7z
- `llvm/`: clang and lld-link from LLVM {LLVM}, <https://llvm.org>, which compile the engine
  patches for your XBE. Apache License 2.0 with LLVM Exceptions; see `llvm/LICENSE-*.TXT`.

Also included, outside this folder:

- `python/`: Python {PYTHON}, PSF License (`python/LICENSE.txt`), and in
  `python/Lib/site-packages` TES3X itself (GPL 3.0 or later, `LICENSE.txt`) and its packages,
  each with its license in its `.dist-info` folder: PySide6 and Qt (GNU LGPL 3), tomlkit (MIT),
  cryptography (Apache 2.0 or BSD), Pillow (MIT-CMU), zstandard (BSD).
- `python/Lib/site-packages/tes3x/_vendor/mlox/`: mlox 1.0.3, MIT.
- `manager/default.xbe`: the console manager, built with nxdk (MIT) and lwIP (BSD), includes
  Monocypher 4.0.2 (CC0 or BSD 2-clause), the zstd 1.5.7 decoder (BSD,
  `manager/LICENSE-zstd.txt`) and Mbed TLS 3.6.7 (Apache 2.0, `manager/LICENSE-mbedtls.txt`).
"""
CC_DIRS = (Path("C:/msys64/mingw64/bin"), Path("C:/msys64/clang64/bin"))


class PackageError(Exception):
    pass


def git(*args):
    done = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True)
    if done.returncode:
        raise PackageError(f"git {' '.join(args)}: {done.stderr.strip()}")
    return done.stdout.strip()


def base_version():
    text = (ROOT / "src" / "tes3x" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'^__version__ = "([^"]+)"', text, re.M).group(1)


def version():
    """X.Y.Z at a clean vX.Y.Z tag; otherwise X.Y.Z-dev.N+gSHA, N commits past the tag."""
    dirty = bool(git("status", "--porcelain", "--untracked-files=no"))
    sha = git("rev-parse", "--short", "HEAD")
    try:
        described = git("describe", "--tags", "--match", "v[0-9]*", "--long")
        tag, count, _ = described.rsplit("-", 2)
        base, count = tag[1:], int(count)
    except PackageError:
        base, count = base_version(), int(git("rev-list", "--count", "HEAD"))
    if count == 0 and not dirty:
        return base
    return f"{base}-dev.{count}+g{sha}" + (".dirty" if dirty else "")


def wheel(out):
    """A wheel of this checkout, built afresh: the package and the data it reads (setup.py)."""
    folder = Path(out) / "wheel"
    shutil.rmtree(folder, ignore_errors=True)
    subprocess.run([sys.executable, "-m", "pip", "wheel", "--quiet", "--no-deps",
                    "--disable-pip-version-check", "-w", str(folder), str(ROOT)], check=True)
    return next(folder.glob("tes3x-*.whl"))


def cache_dir():
    return data_dir() / "package"


def download(name):
    """A pinned download, fetched once into the cache and checked against its SHA-256."""
    url, expected = DOWNLOADS[name]
    path = cache_dir() / name
    if path.is_file():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(name + ".part")
    print(f"downloading {url}")
    digest = hashlib.sha256()
    with urllib.request.urlopen(url, timeout=120) as response, open(partial, "wb") as out:
        while chunk := response.read(1 << 20):
            digest.update(chunk)
            out.write(chunk)
    if digest.hexdigest() != expected:
        partial.unlink()
        raise PackageError(f"{name}: SHA-256 {digest.hexdigest()}, expected {expected}")
    os.replace(partial, path)
    return path


def seven_zip_files():
    """7z.exe, 7z.dll and the license, extracted once from the installer with 7zr."""
    folder = cache_dir() / f"7zip-{SEVEN_ZIP}"
    if not (folder / "7z.exe").is_file():
        shutil.rmtree(folder, ignore_errors=True)
        subprocess.run([str(download("7zr.exe")), "x", "-y", "-bso0", "-bsp0", f"-o{folder}",
                        str(download("7z2603-x64.exe")), "7z.exe", "7z.dll", "License.txt",
                        "readme.txt"], check=True)
    return folder


def llvm_files():
    """clang, lld-link, clang's own headers and the license from the LLVM release."""
    folder = cache_dir() / f"llvm-{LLVM}"
    if (folder / "bin" / "clang.exe").is_file():
        return folder
    archive = download(LLVM_ARCHIVE + ".tar.xz")
    shutil.rmtree(folder, ignore_errors=True)
    print(f"unpacking LLVM {LLVM}")
    prefix = LLVM_ARCHIVE + "/"
    wanted = ("bin/clang.exe", "bin/lld-link.exe", "bin/lld.exe")
    links = {}
    with tarfile.open(archive, "r|xz") as tar:
        for member in tar:
            name = member.name.removeprefix(prefix)
            if not (name in wanted or name.startswith("lib/clang/") and "/include/" in name):
                continue
            target = folder / name
            if member.issym() or member.islnk():
                links[target] = folder / member.linkname.removeprefix(prefix)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(member) as source, open(target, "wb") as out:
                    shutil.copyfileobj(source, out)
    for target, source in links.items():
        if not source.is_absolute() or not source.is_file():
            source = target.parent / source.name
        shutil.copyfile(source, target)
    (folder / "bin" / "lld.exe").unlink(missing_ok=True)
    if not (folder / "bin" / "lld-link.exe").is_file():
        raise PackageError(f"{archive.name} has no bin/lld-link.exe")
    for part in LLVM_LICENSES:
        shutil.copyfile(download(f"LICENSE-{part}-{LLVM}.TXT"), folder / f"LICENSE-{part}.TXT")
    return folder


def externals(target):
    shutil.copytree(seven_zip_files(), target / "7zip")
    shutil.copytree(llvm_files(), target / "llvm")
    (target / "README.md").write_text(NOTICES, encoding="utf-8")


def embedded_python(target, package):
    cache = cache_dir() / f"python-{PYTHON}-embed-amd64.zip"
    if not cache.is_file():
        cache.parent.mkdir(parents=True, exist_ok=True)
        print(f"downloading Python {PYTHON}")
        with urllib.request.urlopen(EMBED_URL.format(PYTHON), timeout=120) as response:
            data = response.read()
        cache.with_suffix(".part").write_bytes(data)
        os.replace(cache.with_suffix(".part"), cache)
    with zipfile.ZipFile(cache) as archive:
        archive.extractall(target)
    # The embedded build takes sys.path only from this file, which leaves out site-packages.
    pth = next(target.glob("python3*._pth"))
    lines = pth.read_text(encoding="utf-8").splitlines()
    pth.write_text("\n".join(lines[:2] + ["Lib\\site-packages"] + lines[2:]) + "\n",
                   encoding="utf-8")
    short = "".join(PYTHON.split(".")[:2])
    print(f"installing {package.name} and its packages")
    subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
                    "--no-warn-conflicts",
                    "--target", str(target / "Lib" / "site-packages"), "--platform", "win_amd64",
                    "--python-version", short, "--implementation", "cp", "--only-binary=:all:",
                    f"{package}[gui]"], check=True)


def find_cc(cc):
    if cc:
        return cc
    for name in ("gcc", "clang"):
        found = shutil.which(name)
        if found:
            return found
        for folder in CC_DIRS:
            if (folder / f"{name}.exe").is_file():
                return str(folder / f"{name}.exe")
    raise PackageError("no C compiler for the launchers; pass --cc or install MSYS2 mingw64 gcc")


def build_launchers(cc, stage):
    """TES3X.exe starts the GUI with no console; tes3x-cli.exe runs commands in the console it is
    started from, which a GUI program cannot print to."""
    env = dict(os.environ, PATH=str(Path(cc).parent) + os.pathsep + os.environ.get("PATH", ""))
    for source, name, subsystem in (("tes3x.c", "TES3X.exe", "-mwindows"),
                                    ("tes3x-cli.c", "tes3x-cli.exe", "-mconsole")):
        subprocess.run([cc, "-O2", "-s", "-municode", subsystem, "-static", "-o", str(stage / name),
                        str(ROOT / "launcher" / source), "-lshlwapi"], check=True, env=env)


README = """TES3X {version}

TES3X.exe       starts the GUI.
tes3x-cli.exe   runs TES3X's commands in a terminal, as `tes3x` does elsewhere:
                  tes3x-cli.exe --help
                  tes3x-cli.exe pipeline profiles\\my-build.toml --check

Settings, profiles and downloads live in %LOCALAPPDATA%\\TES3X, not here, so a newer release can
replace this folder. Nothing is added to PATH or the registry.

Documentation: https://github.com/stjiub/tes3x/blob/main/docs/index.md
License: LICENSE.txt (GPL 3.0 or later). Bundled programs: externals/README.md.
"""


def manager_xbe(given, out, launcher=False):
    """The console manager the GUI installs, or its launcher: the given XBE, or an nxdk build of
    manager/ (manager/launcher/)."""
    if given:
        return Path(given)
    import tes3x.nxdk as tes3x_nxdk
    name = "launcher" if launcher else "manager"
    source = ROOT / "manager" / "launcher" if launcher else ROOT / "manager"
    try:
        return tes3x_nxdk.build(source, Path(out) / name)
    except tes3x_nxdk.NxdkError as exc:
        raise PackageError(f"console {name}: {exc}; pass --{'launcher' if launcher else 'manager'} "
                           "XBE") from exc


def package(out, cc=None, make_zip=False, manager=None, launcher=None):
    label = version()
    stage = Path(out) / f"TES3X-{label}"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    (stage / "VERSION").write_text(label + "\n", encoding="utf-8")
    shutil.copyfile(ROOT / "LICENSE", stage / "LICENSE.txt")
    (stage / "manager").mkdir()
    shutil.copyfile(manager_xbe(manager, out), stage / "manager" / "default.xbe")
    shutil.copyfile(manager_xbe(launcher, out, True), stage / "manager" / "launcher.xbe")
    import tes3x.nxdk as tes3x_nxdk
    shutil.copyfile(tes3x_nxdk.vendor("mbedtls") / "LICENSE",
                    stage / "manager" / "LICENSE-mbedtls.txt")
    shutil.copyfile(ROOT / "manager" / "zstd" / "LICENSE", stage / "manager" / "LICENSE-zstd.txt")
    print(f"TES3X {label}")
    embedded_python(stage / "python", wheel(out))
    externals(stage / "externals")
    build_launchers(find_cc(cc), stage)
    (stage / "README.txt").write_text(README.format(version=label), encoding="utf-8",
                                      newline="\r\n")
    if make_zip:
        archive = shutil.make_archive(str(stage), "zip", stage.parent, stage.name)
        print(f"wrote {archive}")
    print(f"wrote {stage}")
    return stage


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(checkout("build", "package")))
    ap.add_argument("--cc", help="C compiler for the launchers (default: gcc or clang)")
    ap.add_argument("--zip", action="store_true", help="also write TES3X-<version>.zip")
    ap.add_argument("--manager", metavar="XBE",
                    help="the console manager to ship (default: build manager/ with nxdk)")
    ap.add_argument("--launcher", metavar="XBE",
                    help="the manager's launcher to ship (default: build manager/launcher/)")
    ap.add_argument("--print-version", action="store_true")
    args = ap.parse_args()
    try:
        if args.print_version:
            print(version())
        else:
            package(args.out, args.cc, args.zip, args.manager, args.launcher)
    except (PackageError, OSError, subprocess.CalledProcessError) as exc:
        sys.exit(f"tes3x_package: {exc}")
