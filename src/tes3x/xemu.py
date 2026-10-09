"""Build and run a profile in xemu, optionally driving it with a script and recovering its log.

    tes3x xemu NAME profiles/my-build.toml -- --preset minimal --enable diagnostics
    tes3x xemu NAME --deploy build/some/deploy
    tes3x xemu NAME2 --iso build/xemu/NAME/game.iso    (NAME ran with --keep-iso)

Everything after `--` goes to tes3x pipeline. Diagnostics, the hang watchdog and Show FPS are
switched on in the ini unless --no-diag. Each run gets its own folder under build/xemu/ holding
xemu's output and the recovered log. The ISO and the build's deploy tree, most of a run's size, are
deleted when the run ends unless --keep-build (or --keep-iso, to pass the ISO to a later run's
--iso); the build record, link map and patched XBE stay. The disk is a copy-on-write overlay on
the clean HDD image, deleted once the log is read unless --keep-disk; --disk FILE instead keeps
one overlay across runs, so saves persist. Each completed run also writes a .tes3x-run.json
recording its command, platform and inputs without machine paths.

The selected xemu target in tes3x.local.toml names the emulator and its files; see docs/xemu.md.
--gdb-capture SECONDS pauses the guest once and saves CPU and stack state to gdb.txt.
--gdb-script FILE attaches GDB at boot and runs a Python script for the whole session;
$tes3x_handler holds the diagnostics crash handler's address from the build's link map, and
--gdb-set NAME=VALUE sets further convenience variables. Output goes to gdb.txt.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path

from tes3x.fatx import PARTITIONS, FatxReader  # noqa: E402
from tes3x.put import make_dirs, put_dir, put_file  # noqa: E402
from tes3x.qcow2 import CowView, Qcow2, create_overlay, is_qcow2, open_image  # noqa: E402
import tes3x.savepool as tes3x_savepool  # noqa: E402
import tes3x.targets as tes3x_targets  # noqa: E402
from tes3x.xemu_setup import resolve  # noqa: E402
from tes3x.readlog import read_file, read_log  # noqa: E402
from tes3x.pipeline import CONSOLE_INI, set_ini_key, stage_retail_base  # noqa: E402
from tes3x.deploy import ini_pairs  # noqa: E402
from tes3x.paths import local_config  # noqa: E402

TEST_INI = ["Xbox:Diagnostics=1", "Xbox:HangWatchdog=1", "Xbox:HangTimeoutSeconds=30",
            "General:Show FPS=1"]
# U: is E:\UDATA\<title id>. The engine loads a save by path, so the folder need not be the hash
# of a display name the save menu would use.
SAVE_DIR = "TES3X"
SAVE_PATH = "U:\\TES3X\\"
# A missing movie is logged to Warnings.txt and skipped.
SKIP_MOVIES = ["Movies:New Game=none.bik", "Movies:Morrowind Logo=none.bik"]
NO_REBOOT = ["Debug:No Reboot On New Game=1", "Debug:No Reboot On Load Game=1"]
REPORT = ("crash.", "hang.", "diag.patches", "diag.enabled", "diag.boot_hang")
PIPELINE_MARKER = ".tes3x-pipeline.json"
RUN_MARKER = ".tes3x-run.json"
FILES = {
    "exe": "xemu executable",
    "bootrom": "MCPX boot ROM",
    "bios": "BIOS",
    "hdd": "clean HDD image",
    "extract_xiso": "extract-xiso",
}
TEMPLATE = """[general]
show_welcome = false
skip_boot_anim = true

[general.updates]
check = false

[input.bindings]
port1_driver = 'usb-xbox-gamepad'
port1 = 'keyboard'

[sys.files]
bootrom_path = '{bootrom}'
flashrom_path = '{bios}'
eeprom_path = '{eeprom}'
hdd_path = '{hdd}'
dvd_path = '{dvd}'
"""
# Captures the xemu window itself, so windows covering it are never grabbed.
SHOT_SCRIPT = r"""param([string]$Out)
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;using System.Runtime.InteropServices;
public class W {
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out R r);
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr dc, uint flags);
  [StructLayout(LayoutKind.Sequential)] public struct R { public int L,T,Rt,B; }
}
"@
$p = Get-Process xemu -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
if (-not $p) { exit 1 }
$r = New-Object W+R; [W]::GetWindowRect($p.MainWindowHandle, [ref]$r) | Out-Null
$bmp = New-Object System.Drawing.Bitmap ($r.Rt - $r.L), ($r.B - $r.T)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$dc = $g.GetHdc()
[W]::PrintWindow($p.MainWindowHandle, $dc, 2) | Out-Null
$g.ReleaseHdc($dc)
$bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
"""


def load_config(path=None, target_name=None):
    """The selected xemu target, with legacy [xemu] defaults and paths resolved locally."""
    path = local_config(path).resolve()
    try:
        with open(path, "rb") as stream:
            local = tomllib.load(stream)
    except FileNotFoundError:
        local = {}
    values = dict(local.get("xemu", {}))
    try:
        target = tes3x_targets.resolve(local, target_name, "xemu")
    except tes3x_targets.TargetError:
        target = None
    if target:
        values.update({key: value for key, value in target.items()
                       if key in tes3x_targets.XEMU_KEYS})
    return resolve(values, path.parent)


CONFIG_PATH = local_config().resolve()
CONFIG = load_config(CONFIG_PATH)
GDB = Path(CONFIG.get("gdb") or shutil.which("gdb") or "gdb")


def vanilla_root():
    """Clean retail root named by [paths] vanilla_root in the local config, if any."""
    path = CONFIG_PATH
    try:
        with open(path, "rb") as stream:
            root = tomllib.load(stream).get("paths", {}).get("vanilla_root")
    except (OSError, tomllib.TOMLDecodeError):
        return None
    if not root:
        return None
    root = Path(root).expanduser()
    return root if root.is_absolute() else path.parent / root


def vanilla_launcher():
    root = vanilla_root()
    return root / "Default.xbe" if root else None


def profile_layout(path):
    try:
        with open(path, "rb") as stream:
            return tomllib.load(stream).get("profile", {}).get("install_layout", "full")
    except (OSError, tomllib.TOMLDecodeError):
        return "full"


def pool_files(pool, deploy, into):
    """Write a save pool's folder files under `into`; returns their paths."""
    launcher = deploy / "Default.xbe" if deploy and (deploy / "Default.xbe").is_file() \
        else vanilla_launcher()
    if not launcher or not launcher.is_file():
        sys.exit("the build uses a save pool, whose title image comes from Default.xbe; set "
                 "[paths] vanilla_root in tes3x.local.toml")
    image = tes3x_savepool.title_image(launcher)
    written = []
    for name, data in tes3x_savepool.files(pool["name"], image).items():
        path = Path(into) / name
        path.write_bytes(data)
        written.append(path)
    return written


def has_file(image, directory, name):
    off, size = PARTITIONS["E"]
    with open_image(str(image)) as img:
        fs = FatxReader(img, off).bind(size)
        cluster = 1
        for part in directory.split("/"):
            hit = [e for e in fs.listdir(cluster) if e[0].lower() == part.lower()]
            if not hit:
                return False
            cluster = hit[0][2]
        return any(e[0].lower() == name.lower() for e in fs.listdir(cluster))


def put_tree(disk, source, dest):
    """A host file or folder at E:/dest."""
    parent, _, name = dest.replace("\\", "/").strip("/").rpartition("/")
    if source.is_file():
        make_dirs(disk, parent)
        put_file(disk, source, parent, name)
        return
    off, size = PARTITIONS["E"]
    cluster = 1
    for part in [*parent.split("/"), name] if parent else [name]:
        hit = [e for e in FatxReader(disk, off).bind(size).listdir(cluster)
               if e[0].lower() == part.lower()]
        if not hit:
            put_dir(disk, source, parent, name)
            return
        cluster = hit[0][2]
    for path in sorted(source.iterdir()):
        put_tree(disk, path, f"{dest.strip('/')}/{path.name}")


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fixture(path):
    path = Path(path)
    return {"name": path.name, "size": path.stat().st_size, "sha256": sha256_file(path)}


def safe_passthru(values):
    """Keep pipeline switches while removing local paths from the run record."""
    path_options = {"--config", "--vanilla", "--llvm", "--build-root", "--out"}
    result, replace_next = [], False
    for value in values:
        if replace_next:
            result.append("<local-path>")
            replace_next = False
        else:
            option = next((item for item in path_options if value.startswith(item + "=")), None)
            result.append(option + "=<local-path>" if option else value)
            replace_next = value in path_options
    return result


def safe_value(value):
    return "<local-path>" if re.match(r"^(?:[A-Za-z]:[\\/]|[\\/]{1,2})", value) else value


def run_command(args, passthru, pipeline):
    command = ["python", "tools/tes3x_xemu.py", "<run>"]
    if args.profile:
        command.append("profile:" + pipeline.get("profile", Path(args.profile).stem))
    elif args.deploy:
        command += ["--deploy", "<deploy-tree>"]
    else:
        command += ["--iso", "<iso>"]
    for enabled, flag in ((args.direct_engine, "--direct-engine"),
                          (args.skip_intro, "--skip-intro"),
                          (args.no_reboot, "--no-reboot"),
                          (args.no_diag, "--no-diag"),
                          (args.keep_disk, "--keep-disk"),
                          (args.keep_build, "--keep-build"),
                          (args.keep_iso, "--keep-iso"),
                          (args.gdb, "--gdb")):
        if enabled:
            command.append(flag)
    if args.disk:
        command += ["--disk", "<disk>"]
    if args.exec:
        command += ["--exec", "fixture:" + Path(args.exec).name]
    for save in args.save:
        command += ["--save", "fixture:" + Path(save).name]
    if args.timeout is not None:
        command += ["--timeout", str(args.timeout)]
    for seconds in args.shot:
        command += ["--shot", str(seconds)]
    if args.gdb_capture is not None:
        command += ["--gdb-capture", str(args.gdb_capture)]
    if args.gdb_script:
        command += ["--gdb-script", "fixture:" + Path(args.gdb_script).name]
    for item in args.gdb_set:
        name, separator, value = item.partition("=")
        command += ["--gdb-set", name + separator + safe_value(value)]
    if args.gdb_port is not None:
        command += ["--gdb-port", str(args.gdb_port)]
    command += ["--ram", str(args.ram)]
    if args.bios:
        command += ["--bios", "bios:" + Path(args.bios).name]
    if passthru:
        command += ["--", *safe_passthru(passthru)]
    return command


def run(cmd, **kw):
    print("==", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], check=True, **kw)


def xemu_events(since):
    """Windows Error Reporting entries for xemu since the run began: an emulator abort leaves no
    guest log."""
    if os.name != "nt":
        return ""
    ps = ("Get-WinEvent -FilterHashtable @{LogName='Application'; Id=1000; StartTime=[datetime]'%s'} "
          "-ErrorAction SilentlyContinue | Where-Object { $_.Message -match 'xemu' } | "
          "ForEach-Object { ($_.Message -split \"`n\" | Select-Object -First 4) -join ' ' }"
          % time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(since)))
    out = subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps],
                         capture_output=True, text=True)
    return out.stdout.strip()


def screenshot(png):
    if os.name != "nt":
        return
    with tempfile.NamedTemporaryFile("w", suffix=".ps1", delete=False) as script:
        script.write(SHOT_SCRIPT)
    try:
        subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                        script.name, "-Out", str(png)], capture_output=True)
    finally:
        os.unlink(script.name)


def free_port():
    """A TCP port nothing is listening on, since other runs may hold the usual 1234."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def gdb_capture(path, port):
    """Pause the guest through xemu's GDB stub and save the current CPU/stack state."""
    command = [
        str(GDB), "--batch",
        "-ex", "set pagination off",
        "-ex", "set confirm off",
        "-ex", "set architecture i386",
        "-ex", f"target remote 127.0.0.1:{port}",
        "-ex", "set disassembly-flavor intel",
        "-ex", "info registers",
        "-ex", "x/24i $eip-16",
        "-ex", "x/192wx $esp",
        "-ex", "x/128wx $ebx-128",
        "-ex", "thread apply all bt",
        "-ex", "detach",
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=15)
        text = result.stdout
        if result.stderr:
            text += ("\n" if text else "") + result.stderr
    except subprocess.TimeoutExpired as error:
        text = (error.stdout or "") + "\nGDB capture timed out\n" + (error.stderr or "")
    path.write_text(text, errors="replace")


def crash_handler(iso, deploy=None):
    """The diagnostics crash handler's VA, from the link map beside the pipeline output."""
    maps = [iso.parent / "pipeline" / "hooks" / "tes3xhook.map"]
    if deploy:
        maps.insert(0, deploy.parent / "hooks" / "tes3xhook.map")
    for link_map in filter(Path.is_file, maps):
        for line in link_map.read_text(errors="replace").splitlines():
            fields = line.split()
            if len(fields) >= 3 and fields[1] == "_tes3x_exception_handler":
                return int(fields[-2], 16)
    return None


def gdb_session(script, port, handler, sets, log):
    """Attach at boot and let the script drive until xemu closes the connection."""
    command = [str(GDB), "--batch", "-ex", f"set $tes3x_port = {port}"]
    if handler is not None:
        command += ["-ex", f"set $tes3x_handler = {handler:#x}"]
    for kv in sets:
        name, value = kv.split("=", 1)
        command += ["-ex", f'set ${name} = "{value}"']
    command += ["-x", str(Path(script).resolve())]
    return subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)


def check_iso(iso):
    """Refuse a disc with no files: an interrupted extract-xiso leaves a valid header and an empty
    root, which boots to "please insert an Xbox disc"."""
    listing = subprocess.run([str(CONFIG["extract_xiso"]), "-l", str(iso)],
                             capture_output=True, text=True).stdout
    last = listing.strip().splitlines()[-1] if listing.strip() else ""
    if not last.split()[:1] or not last.split()[0].isdigit() or int(last.split()[0]) < 2:
        sys.exit(f"{iso} holds no files ({last or 'no listing'}); rebuild it")
    print(f"iso: {last}")


def link_or_copy(source, target):
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)


def clear_limit64(xbe):
    """Clear the XBE init flag that caps a title at 64 MB on a 128 MB console."""
    data = bytearray(xbe.read_bytes())
    flags = int.from_bytes(data[0x124:0x128], "little")
    data[0x124:0x128] = (flags & ~0x4).to_bytes(4, "little")
    xbe.write_bytes(data)
    print(f"init flags {flags:#x} -> {flags & ~0x4:#x} in {xbe.name}")


def clean_disk(runs):
    """The clean HDD image every overlay reads from. A qcow2 image is copied once under a name
    taken from its contents, so xemu using or replacing the configured file cannot change the
    base of an existing overlay."""
    hdd = Path(CONFIG["hdd"])
    if not is_qcow2(hdd):
        return hdd
    with Qcow2(str(hdd)) as image:
        if image.backing:
            sys.exit(f"{hdd} has a backing file; set the target's hdd to a standalone image")
    clean = runs / f"{hdd.stem}-{sha256_file(hdd)[:12]}.qcow2"
    if not clean.is_file():
        runs.mkdir(parents=True, exist_ok=True)
        partial = clean.with_suffix(".part")
        shutil.copyfile(hdd, partial)
        os.replace(partial, clean)
    return clean


def set_eeprom_mac(path, mac):
    """Write the factory section's Ethernet address and its checksum."""
    data = bytearray(Path(path).read_bytes())
    data[0x40:0x46] = mac
    high = low = 0
    for (word,) in struct.iter_unpack("<I", data[0x34:0x60]):
        total = (high << 32 | low) + word
        high, low = total >> 32 & 0xFFFFFFFF, total & 0xFFFFFFFF
    struct.pack_into("<I", data, 0x30, ~(high + low) & 0xFFFFFFFF)
    Path(path).write_bytes(data)


# The dashboard's HDTV modes, as the user section of the EEPROM stores them.
VIDEO_FLAGS = {"480i": 0, "480p": 0x80000, "720p": 0xA0000}


def set_eeprom_video(path, flags):
    """Write the user section's video flags and its checksum."""
    data = bytearray(Path(path).read_bytes())
    struct.pack_into("<I", data, 0x94, flags)
    high = low = 0
    for (word,) in struct.iter_unpack("<I", data[0x64:0xC0]):
        total = (high << 32 | low) + word
        high, low = total >> 32 & 0xFFFFFFFF, total & 0xFFFFFFFF
    struct.pack_into("<I", data, 0x60, ~(high + low) & 0xFFFFFFFF)
    Path(path).write_bytes(data)


def xemu_config(bootrom, bios, eeprom, hdd, dvd, ram, net_tunnel=None, net_nat=False,
                avpack=None):
    template = CONFIG.get("template")
    text = Path(template).read_text() if template else TEMPLATE
    for key, path in (("bootrom", bootrom), ("bios", bios), ("eeprom", eeprom), ("hdd", hdd),
                      ("dvd", dvd)):
        text = text.replace("{%s}" % key, Path(path).as_posix())
    sys_keys = ["mem_limit = '%d'" % ram] if ram != 64 else []
    if avpack:
        sys_keys.append("avpack = '%s'" % avpack)
    if sys_keys:
        text += "\n[sys]\n" + "\n".join(sys_keys) + "\n"
    if avpack:
        # "auto" follows the dashboard's widescreen flag; show the frame the guest draws.
        text += "\n[display.ui]\naspect_ratio = 'native'\n"
    if net_tunnel:
        text += ("\n[net]\nenable = true\nbackend = 'udp'\n\n[net.udp]\n"
                 "bind_addr = '127.0.0.1:%d'\nremote_addr = '127.0.0.1:%d'\n" % net_tunnel)
    elif net_nat:
        text += "\n[net]\nenable = true\nbackend = 'nat'\n"
    return text


def main():
    global CONFIG_PATH, CONFIG, GDB
    argv = sys.argv[1:]
    passthru = []
    if "--" in argv:
        i = argv.index("--")
        argv, passthru = argv[:i], argv[i + 1:]
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name", help="run folder under build/xemu/")
    ap.add_argument("profile", nargs="?", help="profile to build with tes3x pipeline")
    ap.add_argument("--config", help="local config (default: see docs/configuration.md)")
    ap.add_argument("--target", help="xemu target (default: the first configured xemu target)")
    ap.add_argument("--deploy", help="use an existing deploy tree instead of building")
    ap.add_argument("--iso", help="reuse an existing ISO")
    ap.add_argument("--direct-engine", action="store_true",
                    help="boot morrowind.xbe directly as Default.xbe; an overlay build then keeps "
                         "its retail data in the disc's Base folder")
    ap.add_argument("--exec", metavar="FILE",
                    help="write FILE to tes3xexec.txt on the run's E: drive, for the console patch "
                         "to run in-engine")
    ap.add_argument("--add", action="append", default=[], metavar="DISC=FILE",
                    type=lambda v: tuple(v.split("=", 1)) if "=" in v else ap.error(
                        f"--add takes DISC=FILE, not {v}"),
                    help="put FILE on the disc at DISC, e.g. Manager/default.xbe (repeatable)")
    ap.add_argument("--put", action="append", default=[], metavar="DEST=SOURCE",
                    type=lambda v: tuple(v.split("=", 1)) if "=" in v else ap.error(
                        f"--put takes DEST=SOURCE, not {v}"),
                    help="put a file or folder on the run's E: drive at DEST, e.g. "
                         "Games/Test=build/test-build (repeatable)")
    ap.add_argument("--save", action="append", default=[], metavar="FILE.ess",
                    help="put a save in the run's U:/TES3X folder (repeatable); a command file "
                         "loads it at boot with `@start load` and its path")
    ap.add_argument("--skip-intro", action="store_true",
                    help="profile builds only: skip the logo and New Game movies")
    ap.add_argument("--no-reboot", action="store_true",
                    help="profile builds only: New Game continues in the main menu's process "
                         "instead of relaunching the title")
    ap.add_argument("--no-diag", action="store_true", help="leave the ini's diagnostics keys alone")
    ap.add_argument("--timeout", type=float, help="kill xemu after this many seconds")
    ap.add_argument("--shot", type=float, action="append", default=[], metavar="SECONDS",
                    help="screenshot the xemu window this many seconds after launch (repeatable)")
    ap.add_argument("--gdb-capture", type=float, metavar="SECONDS",
                    help="save CPU registers, instructions and stack through xemu's GDB stub")
    ap.add_argument("--gdb-script", help="GDB Python script to run attached for the whole session")
    ap.add_argument("--gdb-set", action="append", default=[], metavar="NAME=VALUE",
                    help="string convenience variable for --gdb-script (repeatable)")
    ap.add_argument("--gdb", action="store_true",
                    help="open the GDB stub with nothing attached")
    ap.add_argument("--gdb-port", type=int,
                    help="stub port (default: a free one, written to the run's gdb.port)")
    ap.add_argument("--keep-disk", action="store_true")
    ap.add_argument("--keep-build", action="store_true",
                    help="keep the ISO and the build's deploy tree after the run")
    ap.add_argument("--keep-iso", action="store_true",
                    help="keep the ISO after the run, for a later run's --iso")
    ap.add_argument("--seed", action="append", default=[], metavar="DEST=SOURCE",
                    type=lambda v: tuple(v.split("=", 1)) if "=" in v else ap.error(
                        f"--seed takes DEST=SOURCE, not {v}"),
                    help="like --put, but only what --disk's E: drive does not hold yet; a kept "
                         "disk gets it in a new overlay on top (repeatable)")
    ap.add_argument("--disk", metavar="FILE",
                    help="use and keep this overlay across runs, making it over the clean disk "
                         "the first time")
    ap.add_argument("--bios", help="BIOS to boot instead of the target's bios; `128mb` for "
                                   "its bios_128mb")
    ap.add_argument("--net-tunnel", type=int, metavar="PORT",
                    help="attach the NIC to xemu's udp backend: guest frames go to "
                         "127.0.0.1:PORT and frames sent to PORT+1 reach the guest "
                         "(tes3x net --tunnel PORT); the guest's MAC becomes "
                         "02:00:00:00 and PORT, so each tunnel is a separate client")
    ap.add_argument("--net-nat", action="store_true",
                    help="attach the NIC to xemu's nat backend: DHCP gives 10.0.2.15, UDP "
                         "reaches any address through this PC, and 10.0.2.2 is its loopback; "
                         "the guest's MAC comes from the run name")
    ap.add_argument("--ram", type=int, choices=(64, 128),
                    help="guest RAM in MB; 128 also clears Limit64MB in the XBE it packs")
    ap.add_argument("--video", choices=tuple(VIDEO_FLAGS),
                    help="dashboard HDTV setting to boot with, on an HDTV AV pack "
                         "(default: the configured EEPROM and xemu's AV pack)")
    a = ap.parse_args(argv)
    config_path = local_config(a.config).resolve()
    CONFIG_PATH = config_path
    try:
        with open(config_path, "rb") as stream:
            local = tomllib.load(stream)
    except FileNotFoundError:
        local = {}
    try:
        target = tes3x_targets.resolve(local, a.target, "xemu", required=bool(a.target))
    except tes3x_targets.TargetError as exc:
        ap.error(str(exc))
    if target:
        a.target = target["name"]
    CONFIG = load_config(config_path, a.target)
    GDB = Path(CONFIG.get("gdb") or shutil.which("gdb") or "gdb")
    a.ram = a.ram or (target.get("ram", 64) if target else 64)
    if target and a.ram == 128 and not a.bios:
        a.bios = "128mb"
    missing = [f"{key} ({label})" for key, label in FILES.items() if key not in CONFIG]
    if missing:
        ap.error("set these on the xemu target, or put them in its folder: "
                 + ", ".join(missing))
    for key, label in FILES.items():
        if not Path(CONFIG[key]).is_file():
            ap.error(f"{label} not found: {CONFIG[key]}")
    if a.bios in ("128mb", "cerbios"):
        if "bios_128mb" not in CONFIG:
            ap.error("--bios 128mb needs bios_128mb on the selected xemu target")
        a.bios = str(CONFIG["bios_128mb"])
    if sum(bool(x) for x in (a.profile, a.deploy, a.iso)) != 1:
        ap.error("give exactly one of PROFILE, --deploy or --iso")
    if a.add and a.iso:
        ap.error("--add changes the disc; it needs a build, not --iso")
    if (a.skip_intro or a.no_reboot) and not a.profile:
        ap.error("--skip-intro and --no-reboot require a profile build")
    if a.direct_engine and not (a.profile or a.deploy):
        ap.error("--direct-engine requires a profile build or --deploy")
    if (a.gdb_capture is not None or a.gdb_script) and not shutil.which(str(GDB)):
        ap.error(f"gdb not found: {GDB}; set gdb on the selected xemu target")
    if a.gdb_capture is not None and a.gdb_script:
        ap.error("--gdb-capture and --gdb-script both need the stub; pick one")
    if a.disk and (a.exec or a.save or a.put or a.keep_disk):
        ap.error("--exec, --save, --put and --keep-disk apply to a fresh disk, not --disk")
    if a.net_nat and a.net_tunnel:
        ap.error("--net-nat and --net-tunnel are two backends for one NIC; pick one")

    runs = Path.cwd() / "build" / "xemu"
    out = runs / a.name
    if out.exists():
        sys.exit(f"{out} exists; pick a new name")
    clean = clean_disk(runs)
    out.mkdir(parents=True)

    iso = Path(a.iso).resolve() if a.iso else out / "game.iso"
    deploy = None
    if not a.iso:
        deploy = Path(a.deploy).resolve() if a.deploy else out / "pipeline" / "deploy"
        if a.profile:
            ini = [] if a.no_diag else [x for kv in TEST_INI for x in ("--ini-set", kv)]
            if a.skip_intro:
                ini += [x for kv in SKIP_MOVIES for x in ("--ini-set", kv)]
            if a.no_reboot:
                ini += [x for kv in NO_REBOOT for x in ("--ini-set", kv)]
            if profile_layout(a.profile) == "overlay":
                ini += ["--ini-set", r"Xbox:OverlayBase=\Device\CdRom0\Base"]
            target_args = (["--target", a.target] if a.target else [])
            config_args = (["--config", config_path] if config_path.is_file() else [])
            run([sys.executable, "-m", "tes3x", "pipeline", a.profile,
                 "--out", out / "pipeline", *config_args, *target_args, *ini, *passthru])
        marker = deploy.parent / PIPELINE_MARKER
        pipeline = json.loads(marker.read_text(encoding="utf-8")) if marker.is_file() else {}
        overlay = pipeline.get("install_layout") == "overlay"
        packed = deploy
        if overlay:
            retail = vanilla_root()
            if not retail or not (retail / "Data Files" / "Morrowind.bsa").is_file():
                sys.exit("the overlay install layout needs [paths] vanilla_root to compose its "
                         "retail base for xemu")
            packed = out / "stage"
            if a.direct_engine:
                # Exercise the overlay itself: only the patched engine can see this Base folder.
                shutil.copytree(deploy, packed, copy_function=link_or_copy)
                files, size = stage_retail_base(retail, packed / "Base", link_or_copy)
                overlay_base = r"\Device\CdRom0\Base"
                label = "overlay base"
            else:
                # Normal Play reconstitutes a full disc and leaves the patched engine as the
                # dashboard entry, so the overlay hook has nothing to supply.
                files, size = stage_retail_base(retail, packed, link_or_copy)
                shutil.copytree(deploy, packed, copy_function=link_or_copy, dirs_exist_ok=True)
                overlay_base = ""
                label = "retail base"
            ini_path = packed / "Morrowind.ini"
            ini_text = ini_path.read_text(encoding="latin-1")
            ini_path.write_text(set_ini_key(ini_text, "Xbox", "OverlayBase", overlay_base),
                                encoding="latin-1")
            print(f"{label}: {files} files, {size / 1048576:.1f} MB")
        elif a.ram == 128 and not a.profile:
            # Keep someone else's deploy tree intact while changing its XBE init flags.
            packed = out / "stage"
            shutil.copytree(deploy, packed, copy_function=link_or_copy)
        console_ini = deploy.parent / CONSOLE_INI
        if console_ini.is_file():
            # The disc is this run's alone, so the console's keys go into its Morrowind.ini; a
            # reused play disk then needs no console.ini.
            if packed == deploy:
                packed = out / "stage"
                shutil.copytree(deploy, packed, copy_function=link_or_copy)
            ini_path = packed / "Morrowind.ini"
            ini_text = ini_path.read_text(encoding="latin-1")
            for key, value in ini_pairs(console_ini.read_text(encoding="latin-1")):
                if not (overlay and key.casefold() == "overlaybase"):
                    ini_text = set_ini_key(ini_text, "Xbox", key, value)
            ini_path.unlink()  # a hard link must not be written through
            ini_path.write_text(ini_text, encoding="latin-1")
        if a.ram == 128 and (deploy / "morrowind.xbe").is_file():
            if packed == deploy and a.profile:
                clear_limit64(packed / "morrowind.xbe")
            else:
                if packed == deploy:
                    packed = out / "stage"
                    shutil.copytree(deploy, packed, copy_function=link_or_copy)
                (packed / "morrowind.xbe").unlink()
                shutil.copy2(deploy / "morrowind.xbe", packed / "morrowind.xbe")
                clear_limit64(packed / "morrowind.xbe")
        if a.add:
            if packed == deploy and not a.profile:
                packed = out / "stage"
                shutil.copytree(deploy, packed, copy_function=link_or_copy)
            for disc, source in a.add:
                (packed / disc).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, packed / disc)
        if a.direct_engine:
            shutil.copy2(packed / "morrowind.xbe", packed / "Default.xbe")
        run([CONFIG["extract_xiso"], "-c", str(packed), str(iso)], stdout=subprocess.DEVNULL)
        if packed != deploy:
            shutil.rmtree(packed)
    check_iso(iso)

    marker_paths = ([deploy.parent / PIPELINE_MARKER] if deploy else [])
    marker_paths.append(iso.parent / "pipeline" / PIPELINE_MARKER)
    pipeline_marker = next((path for path in marker_paths if path.is_file()), None)
    pipeline = json.loads(pipeline_marker.read_text(encoding="utf-8")) \
        if pipeline_marker else locals().get("pipeline", {})
    if not pipeline and (iso.parent / RUN_MARKER).is_file():
        # An ISO kept from an earlier run carries that run's build record.
        pipeline = json.loads((iso.parent / RUN_MARKER).read_text(encoding="utf-8")).get(
            "pipeline") or {}
    pool = pipeline.get("save_pool")
    udata = tes3x_savepool.folder(int(pool["id"], 16) if pool
                                  else tes3x_savepool.SHARED_ID)
    staging = Path(tempfile.mkdtemp(prefix="pool-", dir=out))
    pool_paths = pool_files(pool, deploy, staging) if pool else []

    # Copy-on-write over the clean disk: the run writes only what the guest changes.
    hdd = Path(a.disk).resolve() if a.disk else out / "hdd.qcow2"
    clusters = None
    puts = list(a.put)
    seeds = []
    for dest, source in a.seed:
        parent, _, name = dest.replace("\\", "/").strip("/").rpartition("/")
        if not (hdd.is_file() and has_file(hdd, parent, name)):
            seeds.append((dest, source))
    if not hdd.is_file():
        # a seed inside another seed's folder is already written with it
        inner = {d.strip("/").lower() for d, _ in seeds}
        puts += [(d, s) for d, s in seeds
                 if not any(d.strip("/").lower().startswith(o + "/") for o in inner)]
        seeds = []
    if a.exec or a.save or puts or (pool_paths and not hdd.is_file()):
        with CowView(str(clean)) as disk:
            if a.exec:
                put_file(disk, a.exec, "", "tes3xexec.txt")
            for dest, source in puts:
                put_tree(disk, Path(source), dest)
            if pool_paths:
                make_dirs(disk, udata)
            for path in pool_paths:
                put_file(disk, path, udata, path.name)
            if a.save:
                make_dirs(disk, f"{udata}/{SAVE_DIR}")
            for save in a.save:
                put_file(disk, save, f"{udata}/{SAVE_DIR}", Path(save).name)
                print("save: @start load " + SAVE_PATH + Path(save).name)
            clusters = disk.changed()
    elif seeds:
        n = 1
        while hdd.with_name(f"{hdd.stem}-{n}{hdd.suffix}").exists():
            n += 1
        below = hdd.rename(hdd.with_name(f"{hdd.stem}-{n}{hdd.suffix}"))
        with CowView(str(below)) as disk:
            for dest, source in seeds:
                put_tree(disk, Path(source), dest)
            create_overlay(str(hdd), str(below), disk.changed())
        print("seeded " + ", ".join(dest for dest, _ in seeds) + f" onto {hdd.name}")
    elif pool_paths and not has_file(hdd, udata, "TitleImage.xbx"):
        # A kept disk from before this pool: its files go in an overlay stacked on top.
        n = 1
        while hdd.with_name(f"{hdd.stem}-{n}{hdd.suffix}").exists():
            n += 1
        below = hdd.rename(hdd.with_name(f"{hdd.stem}-{n}{hdd.suffix}"))
        with CowView(str(below)) as disk:
            make_dirs(disk, udata)
            for path in pool_paths:
                if not has_file(below, udata, path.name):
                    put_file(disk, path, udata, path.name)
            create_overlay(str(hdd), str(below), disk.changed())
        print(f"save pool '{pool['name']}': added E:/{udata} to {hdd.name}")
    if not hdd.is_file():
        hdd.parent.mkdir(parents=True, exist_ok=True)
        create_overlay(str(hdd), str(clean), clusters)
    shutil.rmtree(staging, ignore_errors=True)
    # Without one, xemu writes a new EEPROM at the path it is given.
    if CONFIG.get("eeprom"):
        shutil.copyfile(CONFIG["eeprom"], out / "eeprom.bin")
        # The session server knows clients by MAC, so xemus sharing a server need their own.
        if a.net_tunnel:
            set_eeprom_mac(out / "eeprom.bin",
                           bytes([2, 0, 0, 0]) + a.net_tunnel.to_bytes(2, "big"))
        elif a.net_nat:
            set_eeprom_mac(out / "eeprom.bin",
                           bytes([2, 0, 1]) + hashlib.sha256(a.name.encode()).digest()[:3])
    if a.video:
        if not CONFIG.get("eeprom"):
            sys.exit("--video needs an eeprom in the xemu target")
        set_eeprom_video(out / "eeprom.bin", VIDEO_FLAGS[a.video])
    bios = Path(a.bios).resolve() if a.bios else CONFIG["bios"]
    toml = out / "xemu.toml"
    tunnel = (a.net_tunnel + 1, a.net_tunnel) if a.net_tunnel else None
    toml.write_text(xemu_config(CONFIG["bootrom"], bios, out / "eeprom.bin", hdd, iso, a.ram,
                                tunnel, a.net_nat, "hdtv" if a.video else None))

    t0 = time.time()
    with open(out / "xemu.out", "w") as so, open(out / "xemu.err", "w") as se:
        command = [str(CONFIG["exe"]), "-config_path", str(toml)]
        if a.gdb_capture is not None or a.gdb_script or a.gdb:
            a.gdb_port = a.gdb_port or free_port()
            (out / "gdb.port").write_text(str(a.gdb_port))
            command.extend(("-gdb", f"tcp:127.0.0.1:{a.gdb_port}"))
        proc = subprocess.Popen(command, stdout=so, stderr=se)
        print(f"xemu: started, pid {proc.pid}", flush=True)
        debugger = None
        if a.gdb_script:
            time.sleep(3)  # let xemu open the stub's port
            handler = crash_handler(iso, deploy)
            print(f"gdb: crash handler {handler:#x}" if handler else "gdb: no link map; no crash stop")
            gdb_log = open(out / "gdb.txt", "w")
            debugger = gdb_session(a.gdb_script, a.gdb_port, handler, a.gdb_set, gdb_log)
        shots = sorted(a.shot)
        captured = False
        while proc.poll() is None:
            el = time.time() - t0
            if shots and el >= shots[0]:
                screenshot(out / f"shot-{int(shots.pop(0)):04d}s.png")
            if a.gdb_capture is not None and not captured and el >= a.gdb_capture:
                gdb_capture(out / "gdb.txt", a.gdb_port)
                captured = True
            if a.timeout and el >= a.timeout:
                proc.kill()
                break
            time.sleep(1)
        proc.wait()
        if debugger:
            try:
                debugger.wait(timeout=10)
            except subprocess.TimeoutExpired:
                debugger.kill()
            gdb_log.close()
    print(f"xemu ran {time.time() - t0:.0f}s, exit {proc.returncode}")

    log = read_log(str(hdd))
    if log:
        (out / "tes3xlog.txt").write_bytes(log)
        lines = log.decode("ascii", "replace").splitlines()
        for line in lines:
            if any(k in line for k in REPORT):
                print("  " + line)
        print(f"  ({len(lines)} log lines, last: {lines[-1] if lines else ''})")
    else:
        print("  no hook log on the disk")
    for name, label in (("tes3xprof.bin", "profiler dump"), ("tes3xheap.bin", "heap census"),
                        ("tes3xmem.bin", "memory census"), ("tes3xmgr.txt", "manager log")):
        data = read_file(str(hdd), name)
        if data:
            (out / name).write_bytes(data)
            print(f"  {label}: {len(data)} bytes -> {out / name}")
    events = xemu_events(t0)
    if events:
        print("  xemu crashed:", events)
    err = [l for l in (out / "xemu.err").read_text(errors="replace").splitlines()
           if l.strip() and not l.startswith(("xemu_", "GL_", "CPU:", "OS_", "nvapi", "WARNING: Image",
                                                "         Automatically", "         Specify"))]
    if err:
        print("  xemu stderr:", *err[-10:], sep="\n    ")
    version_match = re.search(r"xemu_version: (\S+)",
                              (out / "xemu.err").read_text(errors="replace"))
    platform = {
        "kind": "xemu",
        "version": version_match.group(1) if version_match else "unknown",
        "bios": Path(bios).name,
        "guest_ram_mb": a.ram,
    }
    fixtures = {}
    if a.exec:
        fixtures["script"] = fixture(a.exec)
    if a.save:
        fixtures["saves"] = [fixture(path) for path in a.save]
    if a.gdb_script:
        fixtures["gdb_script"] = fixture(a.gdb_script)
    run_record = {
        "schema": 1,
        "environment": "xemu",
        "command": run_command(a, passthru, pipeline),
        "platform": platform,
        "fixtures": fixtures,
        "pipeline": pipeline,
    }
    if deploy and (deploy / "morrowind.xbe").is_file():
        run_record["morrowind_xbe_sha256"] = sha256_file(deploy / "morrowind.xbe")
    (out / RUN_MARKER).write_text(json.dumps(run_record, indent=2) + "\n", encoding="utf-8")
    if not (a.keep_disk or a.disk):
        hdd.unlink()
    if not (a.iso or a.keep_build or a.keep_iso):
        iso.unlink(missing_ok=True)
    if a.profile and not a.keep_build:
        shutil.rmtree(out / "pipeline" / "deploy", ignore_errors=True)
    print(f"run directory: {out}")


if __name__ == "__main__":
    main()
