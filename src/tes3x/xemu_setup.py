#!/usr/bin/env python3
"""Find xemu's files in one folder, and download xemu and a blank HDD image into it.

The MCPX boot ROM and the BIOS are Microsoft's and are not downloadable; copy dumps from your
own console into the folder.

    tes3x xemu-setup download FOLDER
    tes3x xemu-setup find FOLDER
"""

import argparse
import io
import json
import os
from pathlib import Path
import platform
import re
import sys
import urllib.request
import zipfile

RELEASES = "https://api.github.com/repos/xemu-project/xemu/releases/latest"
CLEAN_HDD_URL = ("https://github.com/xemu-project/xemu-hdd-image/releases/latest/download/"
                 "xbox_hdd.qcow2.zip")
# Keys of [xemu] that may name a file.
PATH_KEYS = ("exe", "bootrom", "bios", "bios_128mb", "cerbios", "eeprom", "hdd",
             "extract_xiso", "gdb", "template")
BIOS_SIZES = (256 * 1024, 1024 * 1024)


def only(paths):
    return paths[0] if len(paths) == 1 else None


def find_files(folder):
    """{key: path} for each file the folder holds exactly one candidate for."""
    folder = Path(folder)
    if not folder.is_dir():
        return {}
    files = [path for path in folder.iterdir() if path.is_file()]
    name = lambda path: path.name.lower()
    size = lambda path: path.stat().st_size
    bins = [path for path in files if name(path).endswith(".bin")]
    bioses = [path for path in bins if size(path) in BIOS_SIZES]
    big = [path for path in bioses if "cerbios" in name(path) or "128" in name(path)]
    found = {
        "exe": only([path for path in files if name(path) in ("xemu.exe", "xemu")
                     or name(path).endswith(".appimage")]),
        "hdd": (folder / "xbox_hdd.qcow2" if (folder / "xbox_hdd.qcow2").is_file()
                else only([path for path in files if name(path).endswith(".qcow2")])),
        "bootrom": only([path for path in bins if size(path) == 512]),
        "eeprom": only([path for path in bins if size(path) == 256]),
        "bios": only([path for path in bioses if path not in big and "debug" not in name(path)]),
        "bios_128mb": only(big),
    }
    return {key: path for key, path in found.items() if path}


def resolve(values, base):
    """[xemu] values with paths made absolute against `base`, and files the folder supplies
    for keys left unset."""
    config = {}
    for key, value in values.items():
        if isinstance(value, str) and key in (*PATH_KEYS, "folder"):
            value = Path(value).expanduser()
            value = value if value.is_absolute() else Path(base) / value
        config[key] = value
    # The 128 MB BIOS was called cerbios before it had a general name.
    if "bios_128mb" not in config and "cerbios" in config:
        config["bios_128mb"] = config["cerbios"]
    if config.get("folder"):
        for key, path in find_files(config["folder"]).items():
            config.setdefault(key, path)
    return config


def release_asset(assets):
    """The xemu build for this machine, from a release's asset names."""
    arm = platform.machine().lower() in ("arm64", "aarch64")
    if sys.platform == "win32":
        pattern = rf"xemu-[\d.]+-windows-{'arm64' if arm else 'x86_64'}\.zip"
    elif sys.platform.startswith("linux"):
        pattern = rf"xemu-[\d.]+-{'aarch64' if arm else 'x86_64'}\.AppImage"
    else:
        raise ValueError("no xemu download for this system; get it from https://xemu.app")
    return next((asset for asset in assets if re.fullmatch(pattern, asset["name"])), None)


def get(url):
    request = urllib.request.Request(url, headers={"User-Agent": "tes3x"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def fetch_clean_hdd(path, url=CLEAN_HDD_URL):
    """Download xemu's blank formatted HDD image to path; return its size."""
    archive = zipfile.ZipFile(io.BytesIO(get(url)))
    name = next((name for name in archive.namelist() if name.endswith(".qcow2")), None)
    data = archive.read(name) if name else b""
    if data[:4] != b"QFI\xfb":
        raise ValueError(f"{url} did not contain a qcow2 image")
    write(Path(path), data)
    return len(data)


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".part")
    partial.write_bytes(data)
    os.replace(partial, path)


def download_xemu(folder):
    """Put the latest xemu release in folder, and a blank HDD image unless it has one; return
    the release's version."""
    folder = Path(folder)
    release = json.loads(get(RELEASES))
    asset = release_asset(release.get("assets", []))
    if asset is None:
        raise ValueError(f"xemu {release.get('tag_name')} has no build for this system")
    data = get(asset["browser_download_url"])
    if asset["name"].endswith(".zip"):
        archive = zipfile.ZipFile(io.BytesIO(data))
        for member in archive.infolist():
            target = (folder / member.filename).resolve()
            if member.is_dir() or folder.resolve() not in target.parents:
                continue
            write(target, archive.read(member))
    else:
        target = folder / "xemu.AppImage"
        write(target, data)
        target.chmod(0o755)
    if "hdd" not in find_files(folder):
        fetch_clean_hdd(folder / "xbox_hdd.qcow2")
    return release.get("tag_name", "")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=("download", "find"))
    ap.add_argument("folder")
    args = ap.parse_args(argv)
    if args.action == "download":
        print(f"xemu {download_xemu(args.folder)} in {args.folder}")
    found = find_files(args.folder)
    for key in ("exe", "bootrom", "bios", "bios_128mb", "eeprom", "hdd"):
        print(f"{key:11} {found.get(key, '-')}")


if __name__ == "__main__":
    main()
