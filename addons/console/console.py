"""Drive the Xbox's XBMC4Gamers dashboard through the TES3X agent.

    python addons/console/console.py install          # agent + one AlarmClock line in the skin
    python addons/console/console.py ping
    python addons/console/console.py run "F:/Games/Morrowind/default.xbe"
    python addons/console/console.py builtin "XBMC.ActivateWindow(Home)"
    python addons/console/console.py stat "E:/tes3xlog.txt"
    python addons/console/console.py drives            # free/total MB per drive
    python addons/console/console.py reload            # load an updated agent body
    python addons/console/console.py stop              # end the agent
    python addons/console/console.py restart           # restart XBMC4Gamers
    python addons/console/console.py reboot | shutdown
    python addons/console/console.py wait [--timeout S]   # until the agent answers
    python addons/console/console.py uninstall

agent.py is a stable launcher; install replaces agent_body.py and asks the launcher to reload it,
so only the first install needs a dashboard restart. Requests carry the random token kept in the
selected target's tes3x.local.toml entry and uploaded beside the agent. The skin's Startup window
closes once per dashboard start, after the network is up; install adds a marked onunload line that
starts the launcher 30 s later and keeps the original under build/console-backup. [console]
The dashboard root can be set per target, with [console] as a fallback, and common layouts are
detected before files are made.
"""

import argparse
import ftplib
import io
from pathlib import Path
import re
import secrets
import sys
import time
import tomllib

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "tools"))
from tes3x_deploy import AGENT_PORT as PORT, agent_request as raw_request  # noqa: E402
from tes3x_deploy import ensure_dirs, ftp_basename  # noqa: E402
import tes3x_ftp  # noqa: E402
import tes3x_targets  # noqa: E402

AGENT = HERE / "agent.py"
BODY = HERE / "agent_body.py"
DASHBOARD = "F:/XBMC4Gamers"
MARKER = b"tes3xagent"
LAUNCH = (b"<onunload>AlarmClock(tes3xagent,RunScript(special://scripts/tes3xagent/agent.py),"
          b"00:30,silent)</onunload>")


def paths(dashboard):
    """The agent files, skin Startup window and default.py, where an earlier version hooked in.
    The dashboard's Q: drive is its own folder, but the FTP server refuses writes through Q:."""
    folder = f"{dashboard}/system/scripts/tes3xagent"
    return (f"{folder}/agent.py", f"{folder}/agent_body.py", f"{folder}/token.txt",
            f"{dashboard}/skins/Profile/xml/Startup.xml",
            f"{dashboard}/system/scripts/XBMC4Gamers/default.py")


def dashboard_setting(config, target=None):
    path = Path(config) if config else Path.cwd() / "tes3x.local.toml"
    try:
        local = tomllib.loads(path.read_text(encoding="utf-8"))
        selected = tes3x_targets.resolve(local, target, "xbox") or {}
        return selected.get("dashboard") or local.get("console", {}).get("dashboard")
    except (OSError, tomllib.TOMLDecodeError, tes3x_targets.TargetError):
        return None


def detect_dashboard(ftp):
    """Find an XBMC4Gamers root without creating files beneath a guessed path."""
    for dashboard in (DASHBOARD, "E:/XBMC4Gamers", "C:"):
        if remote_file(ftp, paths(dashboard)[3]) is not None:
            return dashboard
    return None


def config_path(args):
    return Path(args.config) if args.config else Path.cwd() / "tes3x.local.toml"


def save_token(args, token):
    """Add agent_token to the selected target without rewriting the rest of the local TOML."""
    path = config_path(args)
    text = path.read_text(encoding="utf-8")
    local = tomllib.loads(text)
    target = tes3x_targets.resolve(local, args.target, "xbox", required=True)
    section = "deploy" if target.get("legacy") else "targets.%s" % target["name"]
    alternatives = [re.escape(section)]
    if not target.get("legacy"):
        alternatives.append(re.escape('targets."%s"' % target["name"]))
    match = re.search(r"(?m)^\[(?:%s)\][ \t]*(?:#.*)?$" % "|".join(alternatives), text)
    if not match:
        raise SystemExit("cannot find the selected Xbox target table in %s" % path)
    eol = "\r\n" if "\r\n" in text else "\n"
    end_match = re.search(r"(?m)^\[", text[match.end():])
    end = match.end() + end_match.start() if end_match else len(text)
    existing = re.search(r"(?m)^agent_token[ \t]*=[^\r\n]*", text[match.end():end])
    if existing:
        start = match.end() + existing.start()
        finish = match.end() + existing.end()
        text = text[:start] + 'agent_token = "%s"' % token + text[finish:]
    else:
        text = text[:match.end()] + eol + 'agent_token = "%s"' % token + text[match.end():]
    path.write_bytes(text.encode("utf-8"))
    args.agent_token = token


def valid_token(value):
    return bool(value and re.match(r"^[0-9a-f]{64}$", value))


def request(args, line, timeout=10):
    if not valid_token(getattr(args, "agent_token", None)):
        raise SystemExit("the selected Xbox target has no dashboard-agent token; install the "
                         "agent first")
    return raw_request(args.host, line, timeout, args.agent_token)


def get(ftp, path):
    buf = io.BytesIO()
    ftp.retrbinary(f"RETR {ftp_basename(ftp, path)}", buf.write)
    return buf.getvalue()


def put(ftp, path, data):
    # This server only accepts bare names after a cwd.
    ensure_dirs(ftp, path, set())
    ftp.storbinary(f"STOR {path.rsplit('/', 1)[1]}", io.BytesIO(data))


# While the agent runs, the dashboard holds its file open and the FTP server answers a write or
# delete of it with 450.
IN_USE = ("is in use by the old agent. Use Remove agent, restart the dashboard, then install "
          "again.")


def stop_agent(args):
    """Ask either the authenticated agent or the pre-token agent to end."""
    try:
        reply = request(args, "stop", timeout=3)
    except (OSError, SystemExit):
        try:
            reply = raw_request(args.host, "stop", timeout=3)
        except OSError:
            return False
    if reply.startswith("ok"):
        print("stopped the running agent")
        time.sleep(1)
        return True
    return False


def remote_file(ftp, path):
    try:
        return get(ftp, path)
    except ftplib.error_perm:
        return None


def install(args):
    ftp = tes3x_ftp.connect(args)
    args.dashboard = args.dashboard or detect_dashboard(ftp)
    if not args.dashboard:
        ftp.quit()
        raise SystemExit("cannot locate XBMC4Gamers; pass --dashboard or set dashboard on the "
                         "selected Xbox target")
    agent, body, token_path, startup_path, _old = paths(args.dashboard)
    dashboard_name = args.dashboard.replace(":", "").strip("/").replace("/", "_") or "root"
    backup = (Path.cwd() / "build" / "console-backup" / (args.target or "xbox")
              / dashboard_name / "Startup.xml")
    remote_agent = remote_file(ftp, agent)
    remote_token = remote_file(ftp, token_path)
    ftp.quit()

    token = getattr(args, "agent_token", None)
    if not valid_token(token) and remote_token:
        candidate = remote_token.decode("ascii", errors="ignore").strip()
        token = candidate if valid_token(candidate) else None
    if not valid_token(token):
        token = secrets.token_hex(32)
    if token != getattr(args, "agent_token", None):
        save_token(args, token)

    launcher_current = remote_agent == AGENT.read_bytes()
    reloaded = False
    if launcher_current:
        try:
            reloaded = request(args, "reload", timeout=3).startswith("ok")
        except OSError:
            pass
    else:
        stop_agent(args)

    ftp = tes3x_ftp.connect(args)
    try:
        if not launcher_current:
            put(ftp, agent, AGENT.read_bytes())
        put(ftp, token_path, (token + "\n").encode("ascii"))
        put(ftp, body, BODY.read_bytes())
    except ftplib.error_temp:
        ftp.quit()
        raise SystemExit(f"{agent} {IN_USE}")
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
    if launcher_current and reloaded:
        print(f"updated {body}; the running agent reloaded it")
    elif launcher_current:
        print(f"updated {body}; restart the dashboard to start the agent")
    else:
        print(f"installed {agent}; restart the dashboard once to start it")


def uninstall(args):
    if not args.dashboard:
        probe = tes3x_ftp.connect(args)
        args.dashboard = detect_dashboard(probe)
        probe.quit()
    if not args.dashboard:
        raise SystemExit("cannot locate XBMC4Gamers; pass --dashboard or set dashboard on the "
                         "selected Xbox target")
    agent, body, token_path, *hooks = paths(args.dashboard)
    stop_agent(args)
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
    for path in (body, token_path, agent):
        try:
            ftp.delete(ftp_basename(ftp, path))
            print(f"removed {path}")
        except ftplib.error_temp:
            print(f"{path} is still in use; restart the dashboard, then remove the agent again")
        except ftplib.error_perm as e:
            print(f"{path}: {e}")
    folder = agent.rsplit("/", 1)[0]
    try:
        ftp.rmd(ftp_basename(ftp, folder))
        print(f"removed empty {folder}")
    except ftplib.error_perm:
        pass
    ftp.quit()


def wait(args, timeout):
    end = time.time() + timeout
    while True:
        try:
            return request(args, "ping", timeout=3)
        except OSError:
            if time.time() > end:
                raise SystemExit(f"agent not answering on {args.host}:{PORT} after {timeout:.0f}s")
            time.sleep(2)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["install", "uninstall", "ping", "run", "builtin", "stat",
                                        "drives", "reload", "stop", "restart", "reboot", "shutdown",
                                        "wait"])
    ap.add_argument("arg", nargs="?")
    ap.add_argument("--timeout", type=float, default=120, help="for wait (default 120 s)")
    ap.add_argument("--dashboard", help="dashboard folder (default: target setting, [console] "
                                        "fallback, then detection)")
    tes3x_ftp.add_arguments(ap)
    args = tes3x_ftp.resolve(ap.parse_args())
    dashboard = args.dashboard or dashboard_setting(args.config, args.target)
    args.dashboard = dashboard.rstrip("/") if dashboard else None

    if args.command == "install":
        return install(args)
    if args.command == "uninstall":
        return uninstall(args)
    if args.command == "wait":
        print(wait(args, args.timeout))
        return
    if args.command in ("run", "builtin", "stat") and not args.arg:
        ap.error(f"{args.command} needs an argument")
    line = args.command if not args.arg else f"{args.command} {args.arg}"
    try:
        reply = request(args, line)
    except OSError as e:
        raise SystemExit(f"the dashboard agent is not answering on {args.host}:{PORT} ({e}). "
                         "Is the Xbox on and in the dashboard, and the agent installed?")
    print(reply)
    if not reply.startswith("ok"):
        sys.exit(1)


if __name__ == "__main__":
    main()
