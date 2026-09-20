#!/usr/bin/env python3
"""Pull and summarize a TES3X hardware diagnostics log."""

import argparse
import ftplib
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tes3x_inject import Xbe  # noqa: E402

LINE = re.compile(r"^(\d+) ms ([^ ]+)(?: (.*))?$")
EXCEPTIONS = {
    0x80000003: "breakpoint",
    0xC0000005: "access violation",
    0xC000001D: "illegal instruction",
    0xC000008C: "array bounds exceeded",
    0xC000008D: "floating-point denormal",
    0xC000008E: "floating-point divide by zero",
    0xC0000094: "integer divide by zero",
    0xC00000FD: "stack overflow",
}
PATCH_BITS = {
    0: "drive-letters",
    1: "save-staging",
    2: "boot-media",
    3: "multi-bsa",
    4: "script-ext",
    5: "mcp-1",
    6: "diagnostics",
    7: "console",
    8: "rotating-autosaves",
}


def parse_value(text):
    if text is None:
        return None
    try:
        return int(text, 0)
    except ValueError:
        return text


def parse_log(text):
    records = []
    for raw in text.splitlines():
        raw = raw.rstrip("\r")
        if not raw:
            continue
        match = LINE.match(raw)
        if not match:
            records.append({"raw": raw})
            continue
        records.append({"ms": int(match.group(1)), "tag": match.group(2),
                        "value": parse_value(match.group(3)), "raw": raw})
    return records


def sessions(records):
    out = []
    current = []
    for record in records:
        if record.get("tag") == "entry.free_kb" and current:
            out.append(current)
            current = []
        current.append(record)
    if current:
        out.append(current)
    return out


def latest_values(records):
    out = {}
    for record in records:
        if "tag" in record:
            out[record["tag"]] = record.get("value")
    return out


def location(xbe, va):
    if not xbe or not isinstance(va, int):
        return None
    for section in xbe.sections:
        if section.va <= va < section.va + section.vsize:
            delta = va - section.va
            suffix = "%s+0x%X" % (section.name, delta)
            if delta < section.rsize:
                suffix += " (file+0x%X)" % (section.raw + delta)
            return suffix
    return "outside XBE image"


def fmt_value(value):
    if isinstance(value, int):
        return "0x%08X" % (value & 0xFFFFFFFF)
    return str(value)


def report(records, xbe=None, show_all=False, stream=sys.stdout):
    runs = sessions(records)
    if not runs:
        print("empty diagnostics log", file=stream)
        return
    selected = runs if show_all else runs[-1:]
    print("sessions: %d%s" % (len(runs), " (showing latest)" if not show_all else ""),
          file=stream)

    for index, run in enumerate(selected, len(runs) - len(selected) + 1):
        values = latest_values(run)
        duration = max((r.get("ms", 0) for r in run), default=0)
        print("\nsession %d: %d records, %.1f s" % (index, len(run), duration / 1000),
              file=stream)
        if "diag.session" in values:
            print("  id: %s" % fmt_value(values["diag.session"]), file=stream)
        if "diag.build" in values:
            print("  payload build: %s" % fmt_value(values["diag.build"]), file=stream)
        if isinstance(values.get("diag.patches"), int):
            mask = values["diag.patches"]
            names = [name for bit, name in PATCH_BITS.items() if mask & (1 << bit)]
            print("  patches: %s (0x%08X)" % (", ".join(names) or "none", mask), file=stream)
        if "diag.enabled" in values:
            print("  diagnostics level: %s" % values["diag.enabled"], file=stream)
        elif "entry.free_kb" in values:
            print("  diagnostics did not reach enabled state", file=stream)
        if "entry.free_kb" in values:
            print("  entry free memory: %s KB" % values["entry.free_kb"], file=stream)

        code = values.get("crash.code")
        if isinstance(code, int):
            print("  CRASH: 0x%08X (%s)" %
                  (code & 0xFFFFFFFF, EXCEPTIONS.get(code & 0xFFFFFFFF, "unknown exception")),
                  file=stream)
            for tag in ("crash.eip", "crash.address", "crash.esp", "crash.ebp"):
                if tag not in values:
                    continue
                value = values[tag]
                where = location(xbe, value)
                print("    %-13s %s%s" %
                      (tag.split(".", 1)[1] + ":", fmt_value(value),
                       "  " + where if where else ""), file=stream)
            if code == 0xC0000005 and isinstance(values.get("crash.info0"), int):
                operation = {0: "read", 1: "write", 8: "execute"}.get(
                    values["crash.info0"], "operation %s" % values["crash.info0"])
                print("    access: %s at %s" %
                      (operation, fmt_value(values.get("crash.info1"))), file=stream)
            if "crash.last_code" in values:
                print("    last note: %s / %s" %
                      (values["crash.last_code"], fmt_value(values.get("crash.last_value"))),
                      file=stream)
            candidates = []
            for tag, value in sorted(values.items()):
                if not tag.startswith("crash.stack"):
                    continue
                where = location(xbe, value)
                if where and where != "outside XBE image":
                    candidates.append((tag, value, where))
            if candidates:
                print("    stack addresses in XBE:", file=stream)
                for tag, value, where in candidates:
                    print("      %s %s  %s" % (tag, fmt_value(value), where), file=stream)

        if "hang.detected" in values:
            print("  HANG: heartbeat %s stopped for %s s" %
                  (values["hang.detected"], values.get("hang.seconds", "?")), file=stream)
            print("    last note: %s / %s" %
                  (values.get("diag.last_code", 0), fmt_value(values.get("diag.last_value"))),
                  file=stream)
            if "hang.resumed" in values:
                print("    main loop later resumed", file=stream)

        if code is None and "hang.detected" not in values:
            print("  no captured crash or watchdog timeout", file=stream)

        print("  tail:", file=stream)
        for record in run[-12:]:
            print("    " + record["raw"], file=stream)


def pull(args):
    ftp = ftplib.FTP()
    ftp.connect(args.host, args.port, timeout=30)
    ftp.login(args.user, args.password)
    data = bytearray()
    ftp.retrbinary("RETR " + args.remote, data.extend, blocksize=64 * 1024)
    ftp.quit()
    with open(args.out, "wb") as f:
        f.write(data)
    print("pulled %d bytes from %s:%s to %s" %
          (len(data), args.host, args.remote, args.out))
    return data.decode("ascii", "replace")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)

    get = sub.add_parser("pull", help="download the log over Xbox FTP and summarize it")
    get.add_argument("--host", default="192.0.2.10")
    get.add_argument("--port", type=int, default=21)
    get.add_argument("--user", default="xbox")
    get.add_argument("--password", default="xbox")
    get.add_argument("--remote", default="E:/tes3xlog.txt")
    get.add_argument("--out", default="tes3xlog.txt")
    get.add_argument("--xbe", help="patched XBE used for offline address resolution")
    get.add_argument("--all-sessions", action="store_true")

    show = sub.add_parser("report", help="summarize an already-pulled log")
    show.add_argument("log")
    show.add_argument("--xbe", help="patched XBE used for offline address resolution")
    show.add_argument("--all-sessions", action="store_true")

    args = ap.parse_args()
    if args.command == "pull":
        text = pull(args)
    elif args.log == "-":
        text = sys.stdin.read()
    else:
        with open(args.log, encoding="ascii", errors="replace") as f:
            text = f.read()
    xbe = Xbe(open(args.xbe, "rb").read()) if args.xbe else None
    report(parse_log(text), xbe, args.all_sessions)


if __name__ == "__main__":
    main()
