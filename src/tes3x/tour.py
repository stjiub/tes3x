#!/usr/bin/env python3
"""Walk the game through a plugin's cells and log free memory at each, to find where it runs out.

  tes3x tour make Data/TR_Mainland.esm --bounds=0,-20,30,10 --step 2 -o tes3xexec.txt
  tes3x tour make Data/TR_Mainland.esm --interiors --prefix "Firewatch" -o tes3xexec.txt
  tes3x tour report tes3xlog.txt

`make` writes a command file for the console patch: an optional `@start` line, then for each cell
a `coe X, Y` or `coc "NAME"`, a wait, and `mark LABEL`, which logs `mem.LABEL <free KB>`.
Exteriors go row by row, alternating direction, so each move is to a neighbour. `report` reads
the last session of a log and prints free memory at each stop and the change from the one before;
if the tour did not finish, it names the last command and any crash or hang.
"""

import argparse
import re
import struct
import sys

from tes3x.records import records, subrecords  # noqa: E402

CELL_INTERIOR = 0x01
SESSION_START = re.compile(r"^0 ms entry\.free_kb ")
MARK = re.compile(r"^(\d+) ms mem\.(.*) (\d+)$")
WS_MARK = re.compile(r"^(\d+) ms ws\.(.*) (\d+)$")
WS_META = {"base", "committed_pages", "no_region", "not_active", "reset", "union_pages"}


def cells(paths):
    """{key: label} for every cell the plugins define; later plugins override earlier ones."""
    out = {}
    for path in paths:
        for tag, _flags, data in records(path):
            if tag != b"CELL":
                continue
            name, grid, flags = None, None, 0
            # The first NAME and DATA are the cell's; later ones belong to its references.
            for sub, value in subrecords(data):
                if sub == b"NAME" and name is None:
                    name = value.rstrip(b"\0").decode("latin-1")
                elif sub == b"DATA" and grid is None and len(value) >= 12:
                    flags, x, y = struct.unpack_from("<Iii", value)
                    grid = (x, y)
            if flags & CELL_INTERIOR:
                out[("int", name.lower())] = name
            elif grid is not None:
                out[("ext", grid)] = grid
    return out


def tour(found, interiors, exteriors, bounds, prefixes, every, step):
    stops = []
    if exteriors:
        grid = sorted(v for k, v in found.items() if k[0] == "ext")
        if bounds:
            x0, y0, x1, y1 = bounds
            grid = [(x, y) for x, y in grid if x0 <= x <= x1 and y0 <= y <= y1]
        grid = [(x, y) for x, y in grid if x % step == 0 and y % step == 0]
        rows = {}
        for x, y in grid:
            rows.setdefault(y, []).append(x)
        for i, y in enumerate(sorted(rows, reverse=True)):
            xs = sorted(rows[y], reverse=bool(i % 2))
            stops += [("coe %d, %d" % (x, y), "%d,%d" % (x, y)) for x in xs]
    if interiors:
        names = sorted(v for k, v in found.items() if k[0] == "int")
        if prefixes:
            names = [n for n in names if any(n.lower().startswith(p.lower()) for p in prefixes)]
        stops += [('coc "%s"' % n, n) for n in names]
    return stops[::every]


def make(args):
    found = cells(args.plugins)
    interiors = args.interiors or not args.exteriors
    exteriors = args.exteriors or not args.interiors
    bounds = tuple(int(v) for v in args.bounds.split(",")) if args.bounds else None
    if bounds and len(bounds) != 4:
        sys.exit("--bounds is X0,Y0,X1,Y1")
    stops = tour(found, interiors, exteriors, bounds, args.prefix, args.every, args.step)
    if args.limit:
        stops = stops[:args.limit]
    if not stops:
        sys.exit("no cells to visit")
    lines = []
    if args.start:
        lines.append("@start " + args.start)
    lines.append("wait %d" % args.wait)
    if args.working_set:
        lines.append("tes3xws reset")
    lines.append("mark start")
    for command, label in stops:
        lines += [command, "wait %d" % args.wait, "mark " + label]
    if args.exit:
        lines.append("exit")
    text = "\n".join(lines) + "\n"
    with open(args.output, "w", encoding="latin-1", newline="\n") as f:
        f.write(text)
    print("%d cells, %d bytes -> %s" % (len(stops), len(text), args.output))


def last_session(text):
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if SESSION_START.match(line)]
    return lines[starts[-1]:] if starts else lines


def report(args):
    with open(args.log, encoding="latin-1") as f:
        lines = last_session(f.read())
    marks, working_set, last_command, trouble, finished = [], [], None, [], False
    ws_pending = None
    for line in lines:
        m = MARK.match(line.strip())
        if m:
            marks.append((int(m.group(1)), m.group(2), int(m.group(3))))
            continue
        m = WS_MARK.match(line.strip())
        if m:
            key, value = m.group(2), int(m.group(3))
            if key == "union_pages" and ws_pending:
                ws_pending[3] = value
            elif key == "committed_pages" and ws_pending:
                ws_pending[4] = value
            elif key not in WS_META:
                ws_pending = [int(m.group(1)), key, value, None, None]
                working_set.append(ws_pending)
        elif line.startswith("exec> "):
            last_command = line[6:].strip()
        elif re.search(r" ms (crash|hang)\.", line):
            trouble.append(line.strip())
        elif re.search(r" ms exec\.(exit|done) ", line):
            finished = True
    if not marks:
        sys.exit("no mem.* lines in the last session")
    if working_set:
        print("%10s  %9s  %8s  %9s  %9s  %9s  %s" %
              ("ms", "free KB", "change", "touched", "union", "committed", "cell"))
    else:
        print("%10s  %9s  %8s  %s" % ("ms", "free KB", "change", "cell"))
    previous = None
    for index, (ms, label, kb) in enumerate(marks):
        change = "" if previous is None else "%+d" % (kb - previous)
        ws = working_set[index] if index < len(working_set) else None
        if ws:
            _ws_ms, ws_label, touched, union, committed = ws
            if ws_label != label:
                touched = union = committed = None
            print("%10d  %9d  %8s  %9s  %9s  %9s  %s" %
                  (ms, kb, change, touched if touched is not None else "-",
                   union if union is not None else "-",
                   committed if committed is not None else "-", label))
        else:
            print("%10d  %9d  %8s  %s" % (ms, kb, change, label))
        previous = kb
    low = min(marks, key=lambda m: m[2])
    print("\nlowest: %d KB at %s; %d stops logged" % (low[2], low[1], len(marks) - 1))
    if not finished:
        print("did not finish; last command: %s" % last_command)
    for line in trouble[:10]:
        print("  " + line)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    mk = sub.add_parser("make", help="write a tour command file from plugins")
    mk.add_argument("plugins", nargs="+", help=".esm/.esp files whose cells to visit")
    mk.add_argument("-o", "--output", default="tes3xexec.txt")
    mk.add_argument("--interiors", action="store_true", help="interiors only (default: both)")
    mk.add_argument("--exteriors", action="store_true", help="exteriors only (default: both)")
    mk.add_argument("--bounds", metavar="X0,Y0,X1,Y1", help="exterior grid box, inclusive")
    mk.add_argument("--prefix", action="append", default=[], help="interior name prefix (repeatable)")
    mk.add_argument("--step", type=int, default=1,
                    help="exteriors only on an N-cell grid, still visited in walking order")
    mk.add_argument("--every", type=int, default=1,
                    help="every Nth stop of the whole tour; moves become jumps")
    mk.add_argument("--limit", type=int, help="stop after N cells")
    mk.add_argument("--wait", type=int, default=90, help="frames to wait after each move")
    mk.add_argument("--working-set", action="store_true",
                    help="reset the heap-region working-set sampler before the tour")
    mk.add_argument("--start", default="new",
                    help="`new`, `load U:\\DIR\\NAME.ess`, or empty for no @start line")
    mk.add_argument("--no-exit", dest="exit", action="store_false",
                    help="leave the game running at the end instead of turning it off")
    rp = sub.add_parser("report", help="summarize a tour from a log")
    rp.add_argument("log")
    args = ap.parse_args()
    make(args) if args.cmd == "make" else report(args)


if __name__ == "__main__":
    main()
