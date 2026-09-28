"""One xemu test run: build a profile, pack an ISO, boot it on a fresh disk, read the log.

    python tools/tes3x_xemu.py NAME profiles/my-build.toml -- --preset minimal --enable diagnostics
    python tools/tes3x_xemu.py NAME --deploy build/some/deploy
    python tools/tes3x_xemu.py NAME2 --iso build/xemu/NAME/game.iso

Everything after `--` goes to tes3x_pipeline.py. Diagnostics, the hang watchdog and Show FPS are
switched on in the ini unless --no-diag. Each run gets its own folder under build/xemu/ holding
the ISO, xemu's output and the recovered log. The disk is a copy-on-write overlay on the clean
HDD image, deleted once the log is read unless --keep-disk; --disk FILE instead keeps one overlay
across runs, so saves persist. Each completed run also writes a .tes3x-run.json with no machine
paths, for validation results.

[xemu] in tes3x.local.toml names the emulator and its files; see docs/testing.md.
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
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
from tes3x_put import make_dirs, put_file  # noqa: E402
from tes3x_qcow2 import CowView, Qcow2, create_overlay, is_qcow2  # noqa: E402
from tes3x_readlog import read_file, read_log  # noqa: E402

TEST_INI = ["Xbox:Diagnostics=1", "Xbox:HangWatchdog=1", "Xbox:HangTimeoutSeconds=30",
            "General:Show FPS=1"]
# U: is E:\UDATA\<title id>. The engine loads a save by path, so the folder need not be the hash
# of a display name the save menu would use.
SAVE_DIR = "UDATA/42530005/TES3X"
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
    "eeprom": "EEPROM image",
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


def load_config(path=None):
    """[xemu] from the local config, with relative paths resolved against that file."""
    path = Path(path or Path.cwd() / "tes3x.local.toml").resolve()
    try:
        with open(path, "rb") as stream:
            values = tomllib.load(stream).get("xemu", {})
    except FileNotFoundError:
        values = {}
    config = {}
    for key, value in values.items():
        if isinstance(value, str) and key in (*FILES, "bios_128mb", "cerbios", "gdb", "template"):
            value = Path(value).expanduser()
            value = value if value.is_absolute() else path.parent / value
        config[key] = value
    # The 128 MB BIOS was called cerbios before it had a general name.
    if "bios_128mb" not in config and "cerbios" in config:
        config["bios_128mb"] = config["cerbios"]
    return config


CONFIG = load_config(os.environ.get("TES3X_CONFIG"))
GDB = Path(CONFIG.get("gdb") or shutil.which("gdb") or "gdb")


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
            sys.exit(f"{hdd} has a backing file; set [xemu] hdd to a standalone image")
    clean = runs / f"{hdd.stem}-{sha256_file(hdd)[:12]}.qcow2"
    if not clean.is_file():
        runs.mkdir(parents=True, exist_ok=True)
        partial = clean.with_suffix(".part")
        shutil.copyfile(hdd, partial)
        os.replace(partial, clean)
    return clean


def xemu_config(bootrom, bios, eeprom, hdd, dvd, ram):
    template = CONFIG.get("template")
    text = Path(template).read_text() if template else TEMPLATE
    for key, path in (("bootrom", bootrom), ("bios", bios), ("eeprom", eeprom), ("hdd", hdd),
                      ("dvd", dvd)):
        text = text.replace("{%s}" % key, Path(path).as_posix())
    if ram != 64:
        text += "\n[sys]\nmem_limit = '%d'\n" % ram
    return text


def main():
    argv = sys.argv[1:]
    passthru = []
    if "--" in argv:
        i = argv.index("--")
        argv, passthru = argv[:i], argv[i + 1:]
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name", help="run folder under build/xemu/")
    ap.add_argument("profile", nargs="?", help="profile to build with tes3x_pipeline.py")
    ap.add_argument("--deploy", help="use an existing deploy tree instead of building")
    ap.add_argument("--iso", help="reuse an existing ISO")
    ap.add_argument("--direct-engine", action="store_true",
                    help="profile builds only: boot morrowind.xbe directly as Default.xbe")
    ap.add_argument("--exec", metavar="FILE",
                    help="write FILE to tes3xexec.txt on the run's E: drive, for the console patch "
                         "to run in-engine")
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
    ap.add_argument("--disk", metavar="FILE",
                    help="use and keep this overlay across runs, making it over the clean disk "
                         "the first time")
    ap.add_argument("--bios", help="BIOS to boot instead of [xemu] bios; `128mb` for "
                                   "[xemu] bios_128mb")
    ap.add_argument("--ram", type=int, choices=(64, 128), default=64,
                    help="guest RAM in MB; 128 also clears Limit64MB when building from a profile")
    a = ap.parse_args(argv)
    missing = [f"{key} ({label})" for key, label in FILES.items() if key not in CONFIG]
    if missing:
        ap.error("set these under [xemu] in tes3x.local.toml: " + ", ".join(missing))
    for key, label in FILES.items():
        if not Path(CONFIG[key]).is_file():
            ap.error(f"{label} not found: {CONFIG[key]}")
    if a.bios in ("128mb", "cerbios"):
        if "bios_128mb" not in CONFIG:
            ap.error("--bios 128mb needs [xemu] bios_128mb in tes3x.local.toml")
        a.bios = str(CONFIG["bios_128mb"])
    if sum(bool(x) for x in (a.profile, a.deploy, a.iso)) != 1:
        ap.error("give exactly one of PROFILE, --deploy or --iso")
    if (a.skip_intro or a.no_reboot) and not a.profile:
        ap.error("--skip-intro and --no-reboot require a profile build")
    if a.direct_engine and not a.profile:
        ap.error("--direct-engine requires a profile build")
    if (a.gdb_capture is not None or a.gdb_script) and not shutil.which(str(GDB)):
        ap.error(f"gdb not found: {GDB}; set [xemu] gdb")
    if a.gdb_capture is not None and a.gdb_script:
        ap.error("--gdb-capture and --gdb-script both need the stub; pick one")
    if a.disk and (a.exec or a.save or a.keep_disk):
        ap.error("--exec, --save and --keep-disk apply to a fresh disk, not --disk")

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
            run([sys.executable, TOOLS / "tes3x_pipeline.py", a.profile,
                 "--out", out / "pipeline", *ini, *passthru])
        if a.ram == 128 and a.profile:
            clear_limit64(deploy / "morrowind.xbe")
        if a.direct_engine:
            shutil.copy2(deploy / "morrowind.xbe", deploy / "Default.xbe")
        run([CONFIG["extract_xiso"], "-c", str(deploy), str(iso)], stdout=subprocess.DEVNULL)
    check_iso(iso)

    marker_paths = ([deploy.parent / PIPELINE_MARKER] if deploy else [])
    marker_paths.append(iso.parent / "pipeline" / PIPELINE_MARKER)
    pipeline_marker = next((path for path in marker_paths if path.is_file()), None)
    pipeline = json.loads(pipeline_marker.read_text(encoding="utf-8")) \
        if pipeline_marker else {}

    # Copy-on-write over the clean disk: the run writes only what the guest changes.
    hdd = Path(a.disk).resolve() if a.disk else out / "hdd.qcow2"
    clusters = None
    if a.exec or a.save:
        with CowView(str(clean)) as disk:
            if a.exec:
                put_file(disk, a.exec, "", "tes3xexec.txt")
            if a.save:
                make_dirs(disk, SAVE_DIR)
            for save in a.save:
                put_file(disk, save, SAVE_DIR, Path(save).name)
                print("save: @start load " + SAVE_PATH + Path(save).name)
            clusters = disk.changed()
    if not hdd.is_file():
        hdd.parent.mkdir(parents=True, exist_ok=True)
        create_overlay(str(hdd), str(clean), clusters)
    shutil.copyfile(CONFIG["eeprom"], out / "eeprom.bin")
    bios = Path(a.bios).resolve() if a.bios else CONFIG["bios"]
    toml = out / "xemu.toml"
    toml.write_text(xemu_config(CONFIG["bootrom"], bios, out / "eeprom.bin", hdd, iso, a.ram))

    t0 = time.time()
    with open(out / "xemu.out", "w") as so, open(out / "xemu.err", "w") as se:
        command = [str(CONFIG["exe"]), "-config_path", str(toml)]
        if a.gdb_capture is not None or a.gdb_script or a.gdb:
            a.gdb_port = a.gdb_port or free_port()
            (out / "gdb.port").write_text(str(a.gdb_port))
            command.extend(("-gdb", f"tcp:127.0.0.1:{a.gdb_port}"))
        proc = subprocess.Popen(command, stdout=so, stderr=se)
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
                        ("tes3xmem.bin", "memory census")):
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
    print(f"run directory: {out}")


if __name__ == "__main__":
    main()
