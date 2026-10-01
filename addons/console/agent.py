# TES3X agent for XBMC4Gamers. `console.py install` adds an AlarmClock to the skin's Startup window
# that starts it well after boot: started from default.py, while the dashboard was still bringing
# the network up, it froze the whole dashboard on half the boots.
# One request per connection: a line in, a line out. Commands are listed in HELP.
import os
import re

try:
    import xbmc
except ImportError:  # host-side test
    xbmc = None

PORT = 7353
VERSION = 5
DRIVES = "CEFGXYZ"
UNITS = {"K": 1.0 / 1024, "M": 1.0, "G": 1024.0, "T": 1024.0 * 1024}
# Builtins that stop the dashboard.
EXITS = ("runxbe", "reboot", "restart", "reset", "shutdown", "powerdown", "restartapp", "dashboard",
         "hibernate", "suspend", "quit", "mastermode", "loadprofile")
HELP = "ping | run XBE | builtin CMD | stat PATH | drives | reboot | shutdown | help"
# Brings the agent back if a dashboard-ending builtin fails; dies with the dashboard otherwise.
RESTART = "AlarmClock(tes3xagent,RunScript(special://scripts/tes3xagent/agent.py),00:10,silent)"


def log(text):
    print("tes3xagent: %s" % text)


def builtin(cmd):
    if xbmc is None:
        log("builtin %s" % cmd)
    else:
        xbmc.executebuiltin(cmd)


def free_mem():
    if xbmc is None:
        return -1
    try:
        return int(xbmc.getFreeMem())
    except Exception:
        return -1


def space_mb(drive, kind):
    """MB from the dashboard's System.FreeSpace(D) or System.TotalSpace(D) label, which reads like
    "1234 MB Free"; None when it has no number."""
    if xbmc is None:
        return None
    text = xbmc.getInfoLabel("System.%sSpace(%s)" % (kind, drive)) or ""
    match = re.search(r"([\d.,]+)\s*([KMGT])B", text, re.I)
    if not match:
        return None
    return int(float(match.group(1).replace(",", "")) * UNITS[match.group(2).upper()])


def drives():
    """'C=free/total ...' in MB, '?' where the dashboard does not say."""
    parts = []
    for drive in DRIVES:
        free, total = space_mb(drive, "Free"), space_mb(drive, "Total")
        parts.append("%s=%s/%s" % (drive, "?" if free is None else free,
                                   "?" if total is None else total))
    return " ".join(parts)


def handle(line):
    """Return the reply and an action to run after the reply is sent."""
    cmd, _, arg = line.strip().partition(" ")
    cmd = cmd.lower()
    arg = arg.strip()
    if cmd == "ping":
        return "ok tes3xagent %d mem=%d" % (VERSION, free_mem()), None
    if cmd == "run":
        if not os.path.isfile(arg):
            return "err no such file: %s" % arg, None
        # RunXBE fails on forward slashes.
        return "ok", "XBMC.RunXBE(%s)" % arg.replace("/", "\\")
    if cmd == "builtin":
        if not arg:
            return "err builtin needs a command", None
        return "ok", arg
    if cmd == "stat":
        try:
            st = os.stat(arg)
        except OSError as e:
            return "err %s" % e, None
        return "ok size=%d mtime=%d dir=%d" % (st.st_size, st.st_mtime, os.path.isdir(arg)), None
    if cmd == "drives":
        return "ok " + drives(), None
    if cmd == "reboot":
        return "ok", "XBMC.Reboot"
    if cmd == "shutdown":
        return "ok", "XBMC.ShutDown"
    if cmd == "help":
        return "ok " + HELP, None
    return "err unknown command: %s (%s)" % (cmd, HELP), None


def ends_dashboard(action):
    name = action.split("(", 1)[0].strip().lower()
    if name.startswith("xbmc."):
        name = name[5:]
    return name in EXITS


def serve_one(conn):
    conn.settimeout(5)
    data = b""
    while b"\n" not in data and len(data) < 1024:
        chunk = conn.recv(1024)
        if not chunk:
            break
        data += chunk
    reply, action = handle(data.decode("latin-1"))
    conn.sendall((reply + "\n").encode("latin-1"))
    return action


def main():
    import socket
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        srv.bind(("", PORT))
    except socket.error as e:
        log("port %d unavailable (%s); another copy is running?" % (PORT, e))
        srv.close()
        return
    srv.listen(2)
    log("listening on %d" % PORT)
    # Block in accept: the dashboard's shutdown finalizes Python without stopping script threads,
    # so a thread that wakes during it can hang the console. A blocked one never wakes.
    while True:
        try:
            conn, peer = srv.accept()
        except socket.error as e:
            log("accept failed (%s); stopping" % e)
            srv.close()
            return
        action = None
        try:
            action = serve_one(conn)
        except Exception as e:
            log("request from %s failed: %s" % (peer[0], e))
        finally:
            conn.close()
        if not action:
            continue
        log("%s from %s" % (action, peer[0]))
        if ends_dashboard(action):
            # Finish this script before the dashboard starts shutting down.
            srv.close()
            builtin(RESTART)
            builtin(action)
            return
        builtin(action)


main()
