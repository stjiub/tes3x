# TES3X agent body for XBMC4Gamers. agent.py reloads this file after an update.
import os
import re

try:
    import xbmc
except ImportError:  # host-side test
    xbmc = None

PORT = 7353
VERSION = 6
DRIVES = "CEFGXYZ"
UNITS = {"K": 1.0 / 1024, "M": 1.0, "G": 1024.0, "T": 1024.0 * 1024}
# Builtins that stop the dashboard.
EXITS = ("runxbe", "reboot", "restart", "reset", "shutdown", "powerdown", "restartapp", "dashboard",
         "hibernate", "suspend", "quit", "mastermode", "loadprofile")
HELP = ("ping | run XBE | builtin CMD | stat PATH | drives | reload | stop | restart | reboot | "
        "shutdown | help")
# Ends the agent so its file can be replaced or deleted; the dashboard keeps running.
STOP = "stop"
RELOAD = "reload"
# Brings the agent back if a dashboard-ending builtin fails; dies with the dashboard otherwise.
RESTART = "AlarmClock(tes3xagent,RunScript(special://scripts/tes3xagent/agent.py),00:10,silent)"
TOKEN_FILE = os.path.join(os.path.dirname(__file__), "token.txt")
TOKEN = None


def log(text):
    print("tes3xagent: %s" % text)


def builtin(cmd):
    if xbmc is None:
        log("builtin %s" % cmd)
    else:
        xbmc.executebuiltin(cmd)


def same_token(left, right):
    """Compare equal-length token bytes without stopping at the first different byte."""
    if len(left) != len(right):
        return False
    different = 0
    for a, b in zip(left, right):
        different |= ord(a) ^ ord(b)
    return different == 0


def private_peer(address):
    try:
        parts = [int(part) for part in address.split(".")]
    except ValueError:
        return False
    return (len(parts) == 4 and all(0 <= part <= 255 for part in parts) and
            (parts[0] == 10 or parts[0] == 172 and 16 <= parts[1] <= 31 or
             parts[0] == 192 and parts[1] == 168))


def allowed_builtin(cmd):
    name = cmd.split("(", 1)[0].strip().lower()
    if name.startswith("xbmc."):
        name = name[5:]
    return name in ("notification", "activatewindow", "takescreenshot")


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
        if not allowed_builtin(arg):
            return "err builtin is not allowed", None
        return "ok", arg
    if cmd == "stat":
        try:
            st = os.stat(arg)
        except OSError as e:
            return "err %s" % e, None
        return "ok size=%d mtime=%d dir=%d" % (st.st_size, st.st_mtime, os.path.isdir(arg)), None
    if cmd == "drives":
        return "ok " + drives(), None
    if cmd == "reload":
        return "ok", RELOAD
    if cmd == "stop":
        return "ok", STOP
    if cmd == "reboot":
        return "ok", "XBMC.Reboot"
    if cmd == "restart":
        return "ok", "XBMC.RestartApp"
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
    line = data.decode("latin-1").strip()
    token, separator, command = line.partition(" ")
    if not separator or not same_token(token, TOKEN):
        reply, action = "err unauthorized", None
    else:
        reply, action = handle(command)
    conn.sendall((reply + "\n").encode("latin-1"))
    return action


def run():
    import socket
    global TOKEN
    TOKEN = open(TOKEN_FILE, "rb").read().strip()
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
            if private_peer(peer[0]):
                action = serve_one(conn)
            else:
                conn.sendall(b"err private network only\n")
        except Exception as e:
            log("request from %s failed: %s" % (peer[0], e))
        finally:
            conn.close()
        if not action:
            continue
        log("%s from %s" % (action, peer[0]))
        if action == STOP:
            srv.close()
            return STOP
        if action == RELOAD:
            srv.close()
            return RELOAD
        if ends_dashboard(action):
            # Finish this script before the dashboard starts shutting down.
            srv.close()
            builtin(RESTART)
            builtin(action)
            return action
        builtin(action)
