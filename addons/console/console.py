"""Drive the Xbox's XBMC4Gamers dashboard through the TES3X agent.

    python addons/console/console.py install          # agent + one AlarmClock line in the skin
    python addons/console/console.py ping
    python addons/console/console.py run "F:/Games/Morrowind/default.xbe"
    python addons/console/console.py builtin "XBMC.ActivateWindow(Home)"
    python addons/console/console.py stat "E:/tes3xlog.txt"
    python addons/console/console.py reboot | shutdown
    python addons/console/console.py wait [--timeout S]   # until the agent answers
    python addons/console/console.py uninstall

The agent is agent.py beside this file. The skin's Startup window closes once per dashboard start,
after the network is up; install adds a marked onunload line there that starts the agent 30 s
later, and keeps the original under build/console-backup. Until the alarm fires, uninstall can
still remove a hook that hangs the dashboard. The first install needs one reboot, and an
XBMC4Gamers update that replaces the skin needs another install. The FTP login comes from
[deploy] in tes3x.local.toml; [console] dashboard names the dashboard folder if it is not
F:/XBMC4Gamers.
"""

import argparse
import ftplib
import io
from pathlib import Path
import socket
import sys
import time
import tomllib

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "tools"))
from tes3x_deploy import ensure_dirs, ftp_basename  # noqa: E402
import tes3x_ftp  # noqa: E402

AGENT = HERE / "agent.py"
PORT = 7353
DASHBOARD = "F:/XBMC4Gamers"
MARKER = b"tes3xagent"
LAUNCH = (b"<onunload>AlarmClock(tes3xagent,RunScript(special://scripts/tes3xagent/agent.py),"
          b"00:30,silent)</onunload>")


def paths(dashboard):
    """The agent, the skin's Startup window, and default.py, where an earlier version hooked in.
    The dashboard's Q: drive is its own folder, but the FTP server refuses writes through Q:."""
    return (f"{dashboard}/system/scripts/tes3xagent/agent.py",
            f"{dashboard}/skins/Profile/xml/Startup.xml",
            f"{dashboard}/system/scripts/XBMC4Gamers/default.py")


def dashboard_setting(config):
    path = Path(config) if config else Path.cwd() / "tes3x.local.toml"
    try:
        return tomllib.loads(path.read_text(encoding="utf-8")).get("console", {}).get("dashboard")
    except (OSError, tomllib.TOMLDecodeError):
        return None


def request(host, line, timeout=10):
    with socket.create_connection((host, PORT), timeout=timeout) as s:
        s.sendall((line + "\n").encode("latin-1"))
        data = b""
        while not data.endswith(b"\n"):
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
    return data.decode("latin-1").strip()


def get(ftp, path):
    buf = io.BytesIO()
    ftp.retrbinary(f"RETR {ftp_basename(ftp, path)}", buf.write)
    return buf.getvalue()


def put(ftp, path, data):
    # This server only accepts bare names after a cwd.
    ensure_dirs(ftp, path, set())
    ftp.storbinary(f"STOR {path.rsplit('/', 1)[1]}", io.BytesIO(data))


def install(args):
    agent, startup_path, _old = paths(args.dashboard)
    backup = Path.cwd() / "build" / "console-backup" / "XBMC4Gamers" / "Startup.xml"
    ftp = tes3x_ftp.connect(args)
    put(ftp, agent, AGENT.read_bytes())
    startup = get(ftp, startup_path)
    if MARKER in startup:
        print(f"{startup_path} already starts the agent")
    else:
        if not backup.exists():
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup.write_bytes(startup)
        eol = b"\r\n" if b"\r\n" in startup else b"\n"
        at = startup.index(b"<onunload")
        put(ftp, startup_path, startup[:at] + LAUNCH + eol + b"\t" + startup[at:])
        print(f"{startup_path}: added the agent line (original in {backup})")
    ftp.quit()
    print(f"installed {agent}; it starts with the dashboard, so restart the dashboard once")


def uninstall(args):
    agent, *hooks = paths(args.dashboard)
    ftp = tes3x_ftp.connect(args)
    for path in hooks:
        try:
            startup = get(ftp, path)
        except ftplib.error_perm:
            continue
        if MARKER in startup:
            kept = b"".join(l for l in startup.splitlines(keepends=True) if MARKER not in l)
            put(ftp, path, kept)
            print(f"{path}: removed the agent line")
    try:
        ftp.delete(ftp_basename(ftp, agent))
        print(f"removed {agent}")
    except ftplib.error_perm as e:
        print(f"{agent}: {e}")
    ftp.quit()


def wait(host, timeout):
    end = time.time() + timeout
    while True:
        try:
            return request(host, "ping", timeout=3)
        except OSError:
            if time.time() > end:
                raise SystemExit(f"agent not answering on {host}:{PORT} after {timeout:.0f}s")
            time.sleep(2)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["install", "uninstall", "ping", "run", "builtin", "stat",
                                        "reboot", "shutdown", "wait"])
    ap.add_argument("arg", nargs="?")
    ap.add_argument("--timeout", type=float, default=120, help="for wait (default 120 s)")
    ap.add_argument("--dashboard", help=f"dashboard folder (default: [console] dashboard, then "
                                        f"{DASHBOARD})")
    tes3x_ftp.add_arguments(ap)
    args = tes3x_ftp.resolve(ap.parse_args())
    args.dashboard = (args.dashboard or dashboard_setting(args.config) or DASHBOARD).rstrip("/")

    if args.command == "install":
        return install(args)
    if args.command == "uninstall":
        return uninstall(args)
    if args.command == "wait":
        print(wait(args.host, args.timeout))
        return
    if args.command in ("run", "builtin", "stat") and not args.arg:
        ap.error(f"{args.command} needs an argument")
    line = args.command if not args.arg else f"{args.command} {args.arg}"
    try:
        reply = request(args.host, line)
    except OSError as e:
        raise SystemExit(f"the dashboard agent is not answering on {args.host}:{PORT} ({e}). "
                         "Is the Xbox on and in the dashboard, and the agent installed?")
    print(reply)
    if not reply.startswith("ok"):
        sys.exit(1)


if __name__ == "__main__":
    main()
